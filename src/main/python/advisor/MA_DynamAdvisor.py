# -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
# MA_DynamAdvisor Bot Main Module
# -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
import asyncio
import logging
import os
import signal
import sys
from threading import Event

from advisor.Client.mt5Client import MetaTrader5Client
from advisor.scheduler.resource_registry import ResourceRegistry
from process.process_engine import Supervisor
from scheduler.process_sceduler import ProcessScheduler
from advisor.core.state import StateManager, BotState
from advisor.core.health_bus import HealthBus
from advisor.indicators.signal_store import SignalStore
from Trade.trateState import TradeStateManager
from advisor.process.heartbeats import HeartbeatRegistry

from advisor.backtest.engine import backtestProcess as backtest_process
from advisor.mt5_pipeline.runner import pipelineProcess as pipeline_process
from advisor.indicators.strategy import strategyManager as strategy_process
from Trade.trade_engine import ExecutionProcess
from advisor.utils.config_handler import UserConfig
from advisor.GUI.userInput import UserGUI as setUpWizard
from utils.dataHandler import CacheManager


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[
        logging.FileHandler("advisor_engine.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger("MAIN")

# ============================================================
# APPLICATION BOOTSTRAP
# ============================================================

class Main:

    def __init__(self):
        self.shutdown_event = Event()

        self._init_core_components()
        self._init_broker()
        self._init_core_instance_()

        self._register_signal_handlers()

    # ------------------------------------------------------------
    # INITIALIZATION
    # ------------------------------------------------------------

    def _init_core_components(self):

        logger.info("Initializing core components...")

        self.state_manager = StateManager()
        self.scheduler = ProcessScheduler()
        self.heartbeats = HeartbeatRegistry()
        self.signal_store = SignalStore()
        self.trade_state = TradeStateManager()
        self.cache_handler = CacheManager()
        self.orch = Supervisor(self.state_manager, self.heartbeats)
        self.botState = self.state_manager.load_bot_state()

        logger.info("Core components initialized.")

    def _init_broker(self, user_data):

        logger.info("Connecting to broker...")

        self.client = MetaTrader5Client()

        if not self.client.initialize(user_data):
            logger.critical("Broker initialization failed.")
            sys.exit(1)

        logger.info("Broker connection established.")

    def _init_core_instance_(self):

        logger.info("Initializing risk engine...")

        self._executor = ExecutionProcess(
            client=self.client,
            signal_store=self.signal_store,
            state=self.trade_state,
            state_manager=self.state_manager,
            health_bus=self.orch.health_bus,
            heartbeats=self.orch.heartbeats,
            registry=self.orch.registry,
            max_daily_loss_pct=0.05,
            max_total_dd_pct=0.15,
            max_trades_per_hour=10,
            max_symbol_exposure=2,
            max_consecutive_losses=5,
        )
        self.pipeline = pipeline_process(
            self.client,
            self.cache_handler,
            self.orch.shutdown,
            self.orch.heartbeats,
            self.orch.health_bus,
            self.orch.registry,
            self.scheduler,
            self.state_manager,
        )
        self.backtest = backtest_process(
            self.client,
            self.cache_handler,
            self.orch.registry,
            self.orch.health_bus,
            self.orch.heartbeats,
            self.orch.shutdown,
            self.botState,
            self.state_manager,
            self.scheduler
        )
        self.strategy = strategy_process(
            self.client,
            self.orch.shutdown,
            self.orch.heartbeats,
            self.orch.health_bus,
            self.orch.registry,
            self.scheduler,
            self.signal_store,
            self.state_manager,
        )

        self.register_processes()

    def register_processes(self):
        self.orch.register_process(name="pipeline", target=self.pipeline.start, *(), depends=[])
        self.orch.register_process(name="backtest", target=self.backtest.start, *(), depends=["pipeline"])
        self.orch.register_process(name="strategy", target=self.strategy.start, *(), depends=["pipeline", "backtest"])
        self.orch.register_process(name="execution", target=self._executor.start, *(), depends=["strategy"])

        logger.info("engines ready.")

    # ------------------------------------------------------------
    # STARTUP
    # ------------------------------------------------------------

    def start(self):

        try:
            logger.info("Reconciling open broker positions...")
            self._restore_open_positions()

            logger.info("Application starting...")
            self.state_manager.set_state(BotState.state.RUNNING)

            self.orch.start()

        except Exception as e:
            logger.critical(f"Fatal startup error: {e}", exc_info=True)
            self.state_manager.set_state(BotState.state.DEGRADED)
            sys.exit(1)

    # ------------------------------------------------------------
    # CRASH RECOVERY
    # ------------------------------------------------------------

    def _restore_open_positions(self):

        open_positions = self.client.get_open_positions()

        for pos in open_positions:
            self.trade_state.register_open(pos)

        logger.info(f"Restored {len(open_positions)} open positions.")

    # ------------------------------------------------------------
    # SHUTDOWN
    # ------------------------------------------------------------

    def shutdown(self, *args):

        logger.warning("Shutdown signal received.")

        self.shutdown_event.set()

        try:
            self.client.shutdown()
        except Exception:
            pass

        self.state_manager.set_state(BotState.state.STOPPED)

        logger.info("System shutdown complete.")
        sys.exit(0)

    # ------------------------------------------------------------
    # SIGNAL HANDLERS
    # ------------------------------------------------------------

    def _register_signal_handlers(self):

        signal.signal(signal.SIGINT, self.shutdown)
        signal.signal(signal.SIGTERM, self.shutdown)


# -------------------------
# Single Instance Guard
# -------------------------
def ensure_single_instance(lock_file):
    """Ensure only one instance of the bot is running."""
    if os.path.exists(lock_file):
        logger.warning("⚠️ Another instance of MA_DynamAdvisor is already running.")
        return False
    with open(lock_file, "w") as f:
        f.write(str(os.getpid()))
    return True

def load_configs(self):
    from advisor.bootstrap.sys_bootstrap import SystemBootstrap
    from advisor.Client.mt5Client import MetaTrader5Client

    bootstrap = SystemBootstrap(MetaTrader5Client)

    try:
        system_objects = bootstrap.initialize()
    except Exception as e:
        print(f"Bootstrap failed: {e}")
        sys.exit(1)

    client = system_objects["client"]
    config = system_objects["config"]
    state = system_objects["state"]


if __name__ == "__main__":
    LOCK_FILE = os.path.splitext(os.path.basename(sys.argv[0]))[0] + ".lock"
    bot = None

    try:
        # --- Register OS signals to call logging.shutdown or our stop
        signal.signal(signal.SIGINT, lambda *_: logging.shutdown())
        if sys.platform != "win32":
            signal.signal(signal.SIGTERM, lambda *_: logging.shutdown())
        else:
            logger.info("⚠️ SIGTERM not supported on Windows — using SIGINT only.")

        if not ensure_single_instance(LOCK_FILE):
            logger.info("⚠️ Please close the running instance before starting a new one.")
            sys.exit(1)

        bot = Main()
        bot.start()

    except KeyboardInterrupt:
        logger.info("🟥 Bot stopped manually.")
    except Exception as e:
        logger.exception(f"❌ Processes stopped with: {e}")
    finally:
        if os.path.exists(LOCK_FILE):
            try:
                os.remove(LOCK_FILE)
                logger.info("✅ Lock file removed. Bot exited cleanly.")
            except Exception as e:
                logger.warning(f"⚠️ Could not remove lock file: {e}")
