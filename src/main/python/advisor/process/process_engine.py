import os
import json
import sys
import time
import signal
import logging
from multiprocessing import Process, Event, Manager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict

from advisor.core import dependency_graph, restart_store, health_bus, state
from advisor.scheduler.resource_registry import ResourceRegistry
from utils.dataHandler import CacheManager

# -------------------------
# Logging Configuration
# -------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("MA_DynamAdvisor.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)


logger = logging.getLogger("Ocherstrator")

class ManagedProcess:
    """
    Metadata wrapper around a child process
    """

    def __init__(self, name, target, args):
        self.name = name
        self.target = target
        self.args = args

        self.process: Process | None = None
        self.last_heartbeat: datetime | None = None
        self.restart_count = 0

class Supervisor:
    HEARTBEAT_TIMEOUT = timedelta(seconds=30)
    """
    Crash-safe process supervisor.
    """

    STATE_FILE = Path("runtime/supervisor_state.json")

    MAX_RESTARTS = 5
    RESTART_BACKOFF = 5  # seconds

    def __init__(self, botState: state.BotState):
        self.shutdown = Event()
        self.manager = Manager()

        self.last_backtest = None
        
        self.registry = ResourceRegistry(self.manager)

        # shared heartbeat dict: {process_name: timestamp}
        self.heartbeats = self.manager.dict()
        self.stateManager = state.StateManager(self.manager)
        self.health_bus = health_bus.HealthBus(self.manager)
        self.restart_store = restart_store.RestartStore()
        self.restart_counts: dict[str, int] = {}
        self.dep_graph = dependency_graph.DependencyGraph()

        self.processes: Dict[str, ManagedProcess] = {}
        self._load_state()

        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

    # -------------------------
    # State persistence
    # -------------------------

    def _load_state(self):
        self.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

        if not self.STATE_FILE.exists():
            return

        try:
            with open(self.STATE_FILE, "r") as f:
                state = json.load(f)

            self.active_symbols = state.get("active_symbols", [])
            self.restart_counts = state.get("restart_counts", {})
            last_bt = state.get("last_backtest")

            if last_bt:
                self.last_backtest = datetime.fromisoformat(last_bt)

        except Exception:
            logger.exception("Failed to load supervisor state")

    def _persist_state(self):
        tmp = self.STATE_FILE.with_suffix(".tmp")

        state = {
            "active_symbols": self.active_symbols,
            "restart_counts": self.restart_counts,
            "last_backtest": (
                self.last_backtest.isoformat() if self.last_backtest else None
            ),
        }

        with open(tmp, "w") as f:
            json.dump(state, f, indent=2)
            f.flush()
            os.fsync(f.fileno())

        os.replace(tmp, self.STATE_FILE)

    # -------------------------
    # Signal handling
    # -------------------------

    def _install_signal_handlers(self):
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

    def _handle_shutdown(self, signum, frame):
        logger.warning(f"Supervisor shutdown signal received ({signum})")
        self.shutdown.set()
        self.stop_all()

    # -------------------------
    # Process control
    # -------------------------

    def register_process(self, name: str, target, *args, dependencies=[]):
        proc = ManagedProcess(
            name=name,
            target=target,
            args=args,
        )
        self.processes[name] = proc
        self.restart_counts.setdefault(name, 0)
        self.dep_graph.add(name, dependencies)

    def _start_process(self, proc: ManagedProcess):
        logger.info(f"▶ Starting {proc.name}")
        proc.process = Process(
            target=proc.target,
            name=proc.name,
            args=(*proc.args, self.shutdown, self.heartbeats),
            daemon=True
        )
        proc.process.start()
        proc.last_heartbeat = datetime.utcnow()

    def start_all(self):
        """starts all registered processes and the monitering loop
        """        
        for name, proc in self.processes.items():
            self._start_process(proc)
        self.monitor()

    def _restart(self, proc: ManagedProcess):
        if proc.restart_count >= self.MAX_RESTARTS:
            logger.critical(f"{proc.name} exceeded restart limit")
            self.shutdown.set()
            return

        logger.warning(f"♻ Restarting {proc.name}")
        proc.restart_count += 1

        try:
            if proc.process.is_alive():
                proc.process.terminate()
        except Exception:
            pass
        self.stateManager.set_state(state.BotState.state.RECOVERING)
        self._start_process(proc)
        self.stateManager.set_state(state.BotState.state.RUNNING)

        # -------------------------
    # Boot Order
    # -------------------------

    def start(self):
        self.stateManager.set_state(state.BotState.state.STARTING)

        order = self.dep_graph.resolve_order()

        logger.info(f"Startup order: {order}")

        for name in order:
            self._start_process(self.processes[name])

        self.stateManager.set_state(state.BotState.state.RUNNING)
        self.monitor()

    def stop_all(self):
        logger.warning("Stopping all processes")
        self.stateManager.set_state(state.BotState.state.STOPPING)
        for proc in self.processes.values():
            if proc.process.is_alive():
                proc.process.terminate()
                proc.process.join(timeout=10)
        self.stateManager.set_state(state.BotState.state.STOPPED)

    # -------------------------
    # Monitoring loop
    # -------------------------

    def monitor(self):
        logger.info("Supervisor monitor loop started")

        while not self.shutdown.is_set():
            for name, proc in list(self.processes.items()):
                if not proc.process.is_alive():
                    logger.error(f"Process crashed: {name}")
                    self.restart_counts[name] += 1
                    self._persist_state()

                    self._restart(proc)
                hb = self.heartbeats.get(name)
                if hb:
                    last = datetime.fromisoformat(hb)
                    if datetime.utcnow() - last > self.HEARTBEAT_TIMEOUT:
                        logger.error(f"Heartbeat timeout: {name}")
                        self._restart(proc)
            self._maybe_run_backtest()
            time.sleep(1)

    # -------------------------
    # Scheduled backtest logic
    # -------------------------

    def _maybe_run_backtest(self):
        if not self.last_backtest:
            self.last_backtest = datetime.utcnow()
            self._persist_state()
            return

        if datetime.utcnow() - self.last_backtest >= timedelta(days=90):
            logger.info("Triggering scheduled backtest")
            self.last_backtest = datetime.utcnow()
            self._persist_state()
            # Backtest process should be signaled here
            backtest = self.processes.get("backtest_engine")
            backtest.run() if backtest else None


if __name__ == "main":

    from advisor.Client.mt5Client import MetaTrader5Client
    from advisor.backtest.engine import backtestProcess as backtest_process
    from advisor.mt5_pipeline.runner import pipelineProcess as pipeline_process
    from advisor.indicators.strategy import strategyManager as strategy_process
    from advisor.Trade.TradesAlgo import MT5TradingAlgorithm as execution_process
    from advisor.core.state import BotState, StateManager
    from 
    try:
        """
        load configs
            cfg = {}
            cfg["bot_cfg"] = loader.json_load("bot")
            cfg["user_cfg"] = loader.json_load("user")
            if cfg["user_cfg"] or not cfg["user_cfg"]:
                cfg = setUpWizard()
        """

        #establish mt5 connection and symbol fetch
        client = MetaTrader5Client()
        if not client.login(cfg["user_cfg"])
            raise ConnectionError
        """
            -------------
                        |
                        |
                        v

        """
        cache_handler = CacheManager()
        registry = ResourceRegistry()
        orch = Supervisor()
        
        pl = pipeline_process(client, cache_handler, registry, health_bus, orch.heartbeats, orch.shutdown, orch.bot_state, orch.stateManager)
        orch.register_process("pipeline", pl.schedule_pipeline() )  # pyright: ignore[reportUndefinedVariable]
        orch.register_process("backtest", backtest_process, (client, cache_handler, registry, health_bus, orch.heartbeats, orch.shutdown, orch.bot_state, orch.stateManager), depends=["pipeline"])  # pyright: ignore[reportUndefinedVariable]
        orch.register_process("strategy", strategy_process, (client, cache_handler, registry, health_bus, orch.heartbeats, orch.shutdown, orch.bot_state, orch.stateManager), depends=["pipeline", "backtest"])  # pyright: ignore[reportUndefinedVariable]
        orch.register_process("execution", execution_process, (client, cache_handler, registry, health_bus, orch.heartbeats, orch.shutdown, orch.bot_state, orch.stateManager), depends=["strategy"])

        orch.start_all()
    except Exception as e:
        logger.critical(f"Supervisor failed to start: {e}", exc_info=True)