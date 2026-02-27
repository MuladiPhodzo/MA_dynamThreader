import asyncio
import logging
import sys
from datetime import datetime, timedelta, timezone

from advisor.Trade.RiskManager import RiskManager
from advisor.Trade.tradeHandler import mt5TradeHandler
from advisor.Trade.trateState import TradeStateManager
from advisor.core.health_bus import HealthBus
from advisor.core.state import BotLifecycle, StateManager
from advisor.indicators.signal_store import SignalStore
from advisor.scheduler.process_sceduler import ProcessScheduler
from advisor.scheduler.requirements import ProcessRequirement
from advisor.scheduler.resource_registry import ResourceRegistry

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
    ProcessRequirement("signals", max_age=timedelta(minutes=2)),
]


class ExecutionProcess:
    name = "Execution"

    def __init__(
        self,
        client,
        signal_store: SignalStore,
        state: TradeStateManager,
        registry: ResourceRegistry,
        health_bus: HealthBus,
        heartbeats: dict,
        shutdown_event,
        scheduler: ProcessScheduler,
        state_manager: StateManager,
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
        self.state_manager = state_manager
        self.processed_signals = set()

        self.risk_manager = RiskManager(
            client=self.client,
            trade_state=self.trade_state,
            state_manager=self.state_manager,
            health_bus=self.health_bus,
        )
        self.executor = mt5TradeHandler(self.client, logger)

    def start(self):
        try:
            asyncio.run(self._safe_execute())
        except Exception as e:
            self.state_manager.set_state(BotLifecycle.DEGRADED)
            logger.critical("%s crashed: %s", self.name, e, exc_info=True)
            self.health_bus.update(self.name, "CRASHED", {"error": str(e)})
            raise

    async def _safe_execute(self):
        while not self.stop_event.is_set():
            await self.scheduler.schedule(
                process_name=self.name,
                required_resources=EXECUTION_REQS,
                task=self._execution_cycle,
                shutdown_event=self.stop_event,
                heartbeats=self.heartbeats,
                timeout=30,
            )
            await asyncio.sleep(self.interval)

    async def _execution_cycle(self):
        executed = 0
        for symbol in list(getattr(self.client, "symbols", [])):
            signal = self.signal_store.get_latest(symbol)
            if not signal:
                continue

            signal_id = signal.id
            if signal_id in self.processed_signals or not signal.is_valid():
                continue

            allowed, lot = self.risk_manager.validate(signal)
            if not allowed:
                continue

            try:
                trade = self.executor.place_market_order(
                    symbol=signal.symbol,
                    side=signal.side,
                    lot=lot,
                    sl_points=signal.sl,
                    tp_points=signal.tp,
                )
                self.trade_state.register_open(trade)
                self.risk_manager.register_trade_open()
                self.processed_signals.add(signal_id)
                executed += 1
            except Exception as e:
                logger.error("%s trade failed: %s", symbol, e, exc_info=True)

        self.heartbeats[self.name] = datetime.now(timezone.utc).isoformat()
        self.health_bus.update(self.name, "RUNNING", {"executed": executed})
