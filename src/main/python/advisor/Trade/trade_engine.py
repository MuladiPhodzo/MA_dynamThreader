from datetime import timedelta, datetime
import logging
import sys
import asyncio

from Trade.trateState import TradeStateManager
from advisor.Client.mt5Client import MetaTrader5Client
from advisor.scheduler.resource_registry import ResourceRegistry
from scheduler.process_sceduler import ProcessScheduler
from advisor.core.state import StateManager, BotState
from advisor.indicators.signal_store import SignalStore
from advisor.scheduler.requirements import ProcessRequirement
from .tradeHandler import mt5TradeHandler
from advisor.core.health_bus import HealthBus
from advisor.Trade.RiskManager import RiskManager

EXECUTION_REQS = [
    ProcessRequirement("market_data", max_age=timedelta(minutes=5)),
    ProcessRequirement("symbols", max_age=timedelta(days=90)),
    ProcessRequirement("backtest_data", max_age=timedelta(days=90))]

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

logger = logging.getLogger("Trade_Executor")

EXECUTION_REQS = [
    ProcessRequirement("market_data", max_age=timedelta(minutes=2)),
    ProcessRequirement("symbols", max_age=timedelta(days=90)),
    ProcessRequirement("strategy_results", max_age=timedelta(minutes=1)),
    ProcessRequirement("signals", max_age=timedelta(minutes=1))
]

class ExecutionProcess:

    name = "Execution"

    def __init__(
        self,
        client: MetaTrader5Client,
        signal_store: SignalStore,     # strategy output storage
        state: TradeStateManager,
        registry: ResourceRegistry,
        health_bus: HealthBus,
        heartbeats: dict,
        shutdown_event,
        scheduler: ProcessScheduler,
        stateManager: StateManager,
        interval=2,
    ):
        self.client = client
        self.signal_store = signal_store
        self.registry = registry
        self.health_bus = health_bus
        self.heartbeats = heartbeats
        self.stop_event = shutdown_event
        self.scheduler = scheduler
        self.interval = interval
        self.trade_state = state
        self._state = stateManager
        self.processed_signals = set()  # crash-safe if persisted

        self.risk_manager = RiskManager(
            client=self.client,
            trade_state=self.trade_state,
            state_manager=self._state,
            health_bus=self.health_bus,
            persistence=self.persist_data,
            max_daily_loss_pct=0.05,
            max_total_dd_pct=0.15,
            max_trades_per_hour=10,
            max_symbol_exposure=2,
            max_consecutive_losses=5
        )

        self._exec_ = mt5TradeHandler(self.client, logger)

    def start(self):
        try:
            asyncio.run(self._safe_execute())
        except Exception as e:
            self.state.set_state(BotState.state.DEGRADED)
            logger.critical(f"{self.name} crashed: {e}", exc_info=True)
            self.health_bus.update(
                self.name,
                "CRASHED",
                {"error": str(e)}
            )
            raise

    async def _safe_execute(self):
        while not self.stop_event.is_set():
            await self.scheduler.schedule(
                process_name=self.name,
                required_resources=EXECUTION_REQS,
                task=self._execution_cycle,
                shutdown_event=self.stop_event,
                heartbeats=self.heartbeats,
                timeout=30
            )
            asyncio.sleep(self.interval)

    async def _execution_cycle(self):

        active_symbols = self.client.symbols
        executed = 0

        for symbol in active_symbols:

            signal = self.signal_store.get_latest(symbol)

            if not signal:
                continue

            signal_id = f"{symbol}:{signal.timestamp}"

            # Idempotency protection
            if signal_id in self.processed_signals:
                continue

            if not signal.is_valid():
                continue

            try:
                if self.execute_signal(signal):
                    executed += 1

            except Exception as e:
                logger.error(f"{symbol} trade failed: {e}", exc_info=True)

        self.heartbeats[self.name] = datetime.now(datetime.timezone.utc).isoformat()

        self.health_bus.update(
            self.name,
            "RUNNING",
            {"executed": executed}
        )

    def execute_signal(self, signal):

        signal_id = signal.id

        if signal_id in self.processed_signals:
            return False

        if not signal.is_valid():
            return False

        # ------------------------
        # RISK GATE
        # ------------------------
        allowed, lot = self.risk_manager.validate(signal)

        if not allowed:
            logger.info(f"Risk blocked trade: {signal.symbol}")
            return False

        try:
            trade = self._exec_.place_market_order(
                symbol=signal.symbol,
                side=signal.side,
                lot=lot,
                sl_points=signal.sl,
                tp_points=signal.tp
            )

            self.trade_state.register_open(trade)
            self.processed_signals.add(signal_id)

            return True

        except Exception as e:
            logger.error(f"Execution failed: {e}", exc_info=True)
            return False
