import logging
import sys
from threading import Event
import signal

from advisor.Trade.trade_engine import ExecutionProcess
from advisor.Trade.trateState import TradeStateManager
from advisor.backtest.engine import backtestProcess
from advisor.bootstrap.sys_bootstrap import BootstrapError, SystemBootstrap
from advisor.core.state import BotLifecycle, StateManager
from advisor.indicators.signal_store import SignalStore
from advisor.indicators.strategy import strategyManager
from advisor.mt5_pipeline.runner import pipelineProcess
from advisor.process.heartbeats import HeartbeatRegistry
from advisor.process.process_engine import Supervisor
from advisor.scheduler.process_sceduler import ProcessScheduler
from advisor.utils.dataHandler import CacheManager
from advisor.GUI.userInput import setUpWizard
from advisor.Client.mt5Client import MetaTrader5Client
from advisor.Client.symbols.symbol_watch import SymbolWatch

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
        self.bootstrap = SystemBootstrap()
        self.state_manager = StateManager()
        self.scheduler = ProcessScheduler(None)
        self.heartbeats = HeartbeatRegistry()
        self.signal_store = SignalStore()
        self.trade_state = TradeStateManager()
        self.cache_handler = CacheManager()
        self.orch = Supervisor(self.shutdown_event, self.state_manager, self.heartbeats)
        self.scheduler.registry = self.orch.registry
        self.scheduler.gate.registry = self.orch.registry

        self.bot_state = self.state_manager.bot
        self.symbol_watch = SymbolWatch(self.bot_state)
        self.client = MetaTrader5Client()
        self.objects = None
        self._load_configs()
        self._connect_client()
        self._init_core_instances()
        self._register_signal_handlers()

    def _load_configs(self):
        try:
            self.objects = self.bootstrap.initialize()
            if hasattr(self.objects, "creds"):
                self.objects = setUpWizard()
        except BootstrapError as e:
            logger.critical("Bootstrap failed: %s", e)
            raise

    def _connect_client(self):
        success = self.client.initialize(self.objects['creds'])
        if success:
            pass
        else:
            raise ConnectionError("failed to connect to MT5 server")

    def _init_core_instances(self):
        self.pipeline = pipelineProcess(
            self.client,
            self.cache_handler,
            self.orch.shutdown,
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
            self.orch.shutdown,
            self.bot_state,
            self.state_manager,
            self.scheduler,
            self.symbol_watch,
        )
        self.strategy = strategyManager(
            self.client,
            self.cache_handler,
            self.orch.shutdown,
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
            shutdown_event=self.orch.shutdown,
            scheduler=self.scheduler,
            state_manager=self.state_manager,
            symbol_watch=self.symbol_watch,
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
            self.orch.start()
            logger.info("All Engines Running")
        except Exception as e:
            logger.critical("Fatal startup error: %s", e, exc_info=True)
            self.state_manager.set_state(BotLifecycle.DEGRADED)
            self.shutdown()
            raise RuntimeError(f"critical system fault: {e}")

    def shutdown(self, *args):
        logger.warning("Shutdown signal received.")
        self.shutdown_event.set()
        self.orch.stop_all()

        close = getattr(self.client, "close", None)
        if callable(close):
            close()

        self.state_manager.set_state(BotLifecycle.STOPPED)
        logger.info("System shutdown complete.")

    def _register_signal_handlers(self):
        signal.signal(signal.SIGINT, self.shutdown)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, self.shutdown)
