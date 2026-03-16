import json
import logging
import sys
from pathlib import Path
from threading import Event
import signal

from advisor.Trade.trade_engine import ExecutionProcess
from advisor.Trade.trateState import TradeStateManager
from advisor.backtest.engine import backtestProcess
from advisor.bootstrap.sys_bootstrap import BootstrapError, SystemBootstrap
from advisor.core.state import BotLifecycle, StateManager, SymbolState, BotState
from advisor.indicators.signal_store import SignalStore
from advisor.indicators.strategy import strategyManager
from advisor.mt5_pipeline.runner import pipelineProcess
from advisor.process.heartbeats import HeartbeatRegistry
from advisor.process.process_engine import Supervisor
from advisor.scheduler.process_sceduler import ProcessScheduler
from advisor.utils.dataHandler import CacheManager
from advisor.Client.mt5Client import MetaTrader5Client
from advisor.Client.symbols.symbol_watch import SymbolWatch
from advisor.api.server import DashboardContext, DashboardServer
from advisor.GUI.userInput import setUpWizard

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[
        logging.FileHandler("advisor_engine.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)

logger = logging.getLogger("Runner")


class Main:
    def __init__(self):
        self.shutdown_event = Event()
        self.process_events = {
            "pipeline": Event(),
            "backtest": Event(),
            "strategy": Event(),
            "execution": Event(),
        }
        self.bootstrap = SystemBootstrap()
        self.state_manager = StateManager()
        self.scheduler = ProcessScheduler(None)
        self.heartbeats = HeartbeatRegistry()
        self.signal_store = SignalStore()
        self.client: MetaTrader5Client | None = None
        self.trade_state: TradeStateManager | None = None
        self.cache_handler = CacheManager()
        self.orch = Supervisor(self.shutdown_event, self.state_manager, self.heartbeats)
        self.scheduler.registry = self.orch.registry
        self.scheduler.gate.registry = self.orch.registry

        self.bot_state = self.state_manager.bot
        self.symbol_watch = SymbolWatch(self.bot_state)
        if not self.symbol_watch.all_symbols:
            logger.warning("No symbols loaded in bot state; configure symbols in configs.json or bot_state.json.")
        elif not self.symbol_watch.active_symbols:
            logger.warning("No active symbols enabled; enable symbols via configs.json, bot_state.json, or /symbols/toggle.")
        self.objects = None
        self.dashboard = None
        self._load_configs()
        self._connect_client()
        self._ensure_symbols()
        self._defer_activation_until_backtest()
        self._init_core_instances()

    def _load_configs(self):
        try:
            boot = self.bootstrap.initialize()
        except BootstrapError as e:
            if self._configs_missing():
                self._run_setup_wizard()
                boot = self.bootstrap.initialize()
            else:
                logger.critical("Bootstrap failed: %s", e)
                raise

        self.config = boot.get("config")
        self.client = boot.get("client")
        self.objects = {
            "creds": self.config.creds,
            "trade_configs": self.config.trade,
            "account_data": self.config.account,
        }

    def _configs_missing(self) -> bool:
        config_path = self._config_path()
        return not config_path.exists() or config_path.stat().st_size == 0

    def _config_path(self) -> Path:
        base = Path(__file__).resolve()
        root = base.parents[4]
        return root / "configs.json"

    def _run_setup_wizard(self) -> None:
        logger.warning("No configs.json found. Launching setup wizard.")
        StateManager.save_bot_state(BotState())

        wizard = setUpWizard(MetaTrader5Client())
        user_data = getattr(wizard, "user_data", None) or {}
        creds = user_data.get("creds")
        trade_cfg = user_data.get("trade_cfg")
        if not creds or not trade_cfg:
            raise BootstrapError("Setup wizard did not produce valid configuration.")

        payload = {
            "creds": {
                "server": creds.get("server"),
                "account_id": int(creds.get("account_id")),
                "password": creds.get("password"),
            },
            "trade_configs": {
                "volume": str(trade_cfg.get("volume", "0.01")),
                "pip_distance": int(trade_cfg.get("pip_distance", 200)),
                "rr_ratio": str(trade_cfg.get("rr_ratio", "1:2")),
                "trailing_sl": str(trade_cfg.get("trailing_sl", False)),
            },
            "account_data": {
                "Equity": 0,
                "deposit": 0,
                "max_open_trades": 10,
                "max_daily_loss": 10,
                "max_concurrent_trades": 5,
            },
            "symbols": {},
        }

        config_path = self._config_path()
        config_path.write_text(json.dumps(payload, indent=4), encoding="utf-8")
        logger.info("Created new configs.json via setup wizard.")

    def _connect_client(self):
        if self.client is None:
            self.client = MetaTrader5Client()

        if getattr(self.client, "account_info", None):
            return

        success = self.client.initialize(self.objects["creds"])
        if not success:
            raise ConnectionError("failed to connect to MT5 server")

    def _ensure_symbols(self):
        config_symbols = {}
        try:
            config_symbols = self.config.symbols or {}
        except Exception:
            config_symbols = {}

        self._config_symbols_empty = not bool(config_symbols)

        if config_symbols:
            return

        if self.client is None:
            return

        mt5_symbols = getattr(self.client, "symbols", None) or self.client.get_Symbols()
        if not mt5_symbols:
            logger.warning("No symbols available from MT5 to seed bot state.")
            return

        existing = {sym.symbol: sym for sym in (self.state_manager.bot.symbols or [])}
        updated = []
        for sym in mt5_symbols:
            state = existing.get(sym)
            if state is None:
                state = SymbolState(symbol=sym, enabled=False, meta={"desired_enabled": True})
            updated.append(state)

        current_symbols = [sym.symbol for sym in (self.state_manager.bot.symbols or [])]
        if current_symbols != mt5_symbols:
            self.state_manager.bot.symbols = updated
            StateManager.save_bot_state(self.state_manager.bot)
            self.symbol_watch.refresh()
            logger.warning(
                "No symbols in configs.json; synced %d symbols from MT5 with enabled=False.",
                len(mt5_symbols),
            )

    def _defer_activation_until_backtest(self):
        if self.state_manager.last_backtest_run:
            return

        changed = False
        for sym in self.state_manager.bot.symbols:
            if not isinstance(sym.meta, dict):
                sym.meta = {}
                changed = True
            if "desired_enabled" not in sym.meta:
                if getattr(self, "_config_symbols_empty", False):
                    sym.meta["desired_enabled"] = True
                else:
                    sym.meta["desired_enabled"] = bool(sym.enabled)
                changed = True
            if sym.enabled:
                sym.enabled = False
                changed = True

        if changed:
            StateManager.save_bot_state(self.state_manager.bot)
            self.symbol_watch.refresh()
            logger.info("Deferring symbol activation until backtest completes.")

    def _init_core_instances(self):
        if self.client is None:
            raise RuntimeError("MT5 client not initialized")

        self.trade_state = TradeStateManager(self.client)
        self.pipeline = pipelineProcess(
            self.client,
            self.cache_handler,
            self.process_events["pipeline"],
            self.orch.heartbeats,
            self.orch.health_bus,
            self.orch.registry,
            self.scheduler,
            self.state_manager,
            self.symbol_watch,
        )
        self.backtest = backtestProcess(
            self.client,
            self.cache_handler,
            self.orch.registry,
            self.orch.health_bus,
            self.orch.heartbeats,
            self.process_events["backtest"],
            self.bot_state,
            self.state_manager,
            self.scheduler,
            self.symbol_watch,
        )
        self.strategy = strategyManager(
            self.client,
            self.cache_handler,
            self.process_events["strategy"],
            self.orch.heartbeats,
            self.orch.health_bus,
            self.orch.registry,
            self.scheduler,
            self.signal_store,
            self.state_manager,
            self.symbol_watch,
        )
        self.execution = ExecutionProcess(
            client=self.client,
            signal_store=self.signal_store,
            state=self.trade_state,
            registry=self.orch.registry,
            health_bus=self.orch.health_bus,
            heartbeats=self.orch.heartbeats,
            shutdown_event=self.process_events["execution"],
            scheduler=self.scheduler,
            state_manager=self.state_manager,
            symbol_watch=self.symbol_watch,
        )

        self.dashboard = DashboardServer(
            DashboardContext(
                supervisor=self.orch,
                state_manager=self.state_manager,
                symbol_watch=self.symbol_watch,
                health_bus=self.orch.health_bus,
            )
        )

        self.orch.register_process(name="pipeline", target=self.pipeline.start, depends=[])
        self.orch.register_process(name="backtest", target=self.backtest.start, depends=["pipeline"])
        self.orch.register_process(name="strategy", target=self.strategy.start, depends=["pipeline", "backtest"])
        self.orch.register_process(name="execution", target=self.execution.start, depends=["strategy"])
        logger.info("Engines registered.")

    def _restore_open_positions(self):
        getter = getattr(self.client, "get_open_positions", None)
        if not callable(getter):
            return
        for pos in getter() or []:
            self.trade_state.register_open(pos)

    def start(self):
        try:
            self._restore_open_positions()
            self.state_manager.set_state(BotLifecycle.RUNNING)
            if self.dashboard:
                self.dashboard.start()
            logger.info("Starting all engines")
            self.orch.start()
        except Exception as e:
            logger.critical("Fatal startup error: %s", e, exc_info=True)
            self.state_manager.set_state(BotLifecycle.DEGRADED)
            self.shutdown()
            raise RuntimeError(f"critical system fault: {e}")

    def shutdown(self, *args):
        signum = args[0] if args and isinstance(args[0], int) else None
        if signum is not None:
            try:
                sig_name = signal.Signals(signum).name
            except ValueError:
                sig_name = str(signum)
            logger.warning("Shutdown signal received (%s).", sig_name)
        else:
            logger.warning("Shutdown requested.")
        self.shutdown_event.set()
        for ev in self.process_events.values():
            ev.set()
        self.orch.stop_all()
        if self.dashboard:
            try:
                self.dashboard.stop()
            except Exception:
                logger.exception("Failed to stop dashboard server")

        close = getattr(self.client, "close", None)
        if callable(close):
            close()

        self.state_manager.set_state(BotLifecycle.STOPPED)
        logger.info("System shutdown complete.")
        raise RuntimeError("system shut down manually")
