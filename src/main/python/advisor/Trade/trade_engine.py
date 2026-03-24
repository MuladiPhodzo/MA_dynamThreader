import asyncio
from datetime import datetime, timedelta, timezone

from advisor.Trade.RiskManager import RiskManager
from advisor.Trade.tradeHandler import mt5TradeHandler
from advisor.Trade.trateState import TradeStateManager
from advisor.core.health_bus import HealthBus
from advisor.core.event_bus import EventBus
from advisor.core import events
from advisor.core.state import BotLifecycle, StateManager
from advisor.indicators.signal_store import SignalStore
from advisor.scheduler.process_sceduler import ProcessScheduler
from advisor.scheduler.requirements import ProcessRequirement
from advisor.scheduler.resource_registry import ResourceRegistry
from advisor.Client.symbols.symbol_watch import SymbolWatch
from advisor.utils.logging_setup import get_logger

logger = get_logger("Trade_Executor")

EXECUTION_REQS = [
    ProcessRequirement("signals", max_age=timedelta(minutes=2)),
    ProcessRequirement("symbol_ingestion", max_age=timedelta(minutes=5)),
]


class ExecutionProcess:
    name = "execution"

    def __init__(
        self,
        client,
        signal_store: SignalStore,
        registry: ResourceRegistry,
        health_bus: HealthBus,
        heartbeats: dict,
        shutdown_event,
        scheduler: ProcessScheduler,
        state_manager: StateManager,
        symbol_watch: SymbolWatch,
        state: TradeStateManager | None = None,
        interval=2,
        event_bus: EventBus | None = None,
        state_store=None,
    ):
        self.client = client
        self.signal_store = signal_store
        self.registry = registry
        self.health_bus = health_bus
        self.heartbeats = heartbeats
        self.stop_event = shutdown_event
        self.scheduler = scheduler
        self.interval = interval
        self.symbol_watch = symbol_watch
        self.trade_state = state or TradeStateManager(self.client)
        self.state_manager = state_manager
        self.processed_signals = set()
        self.event_bus = event_bus
        self.state_store = state_store
        self._last_idle_beat: datetime | None = None
        if self.state_store is not None:
            self.processed_signals = self.state_store.load_processed_signals()

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
        if self.event_bus is None:
            raise RuntimeError("Event bus not configured for execution process")

        sub = self.event_bus.subscribe(events.SIGNALS_READY)
        try:
            while not self.stop_event.is_set():
                evt = await sub.next(stop_event=self.stop_event, timeout=1.0)
                if evt is None:
                    self._idle_heartbeat()
                    continue
                await self.scheduler.schedule(
                    process_name=self.name,
                    required_resources=EXECUTION_REQS,
                    task=self._execution_cycle,
                    shutdown_event=self.stop_event,
                    heartbeats=self.heartbeats,
                    timeout=30,
                )
        finally:
            sub.close()

    def _idle_heartbeat(self) -> None:
        now = datetime.now(timezone.utc)
        if self._last_idle_beat and now - self._last_idle_beat < timedelta(seconds=30):
            return
        self._last_idle_beat = now
        self.heartbeats[self.name] = now.isoformat()
        self.health_bus.update(
            self.name,
            "IDLE",
            {
                "telemetry": self.symbol_watch.snapshot(),
            },
        )

    async def _execution_cycle(self):
        executed = 0
        for symbol in self.symbol_watch.active_symbol_names():
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
                self.symbol_watch.mark_trade(signal.symbol)
                executed += 1
            except Exception as e:
                logger.error("%s trade failed: %s", symbol, e, exc_info=True)
                self.symbol_watch.mark_error(symbol, f"trade failed: {e}")

        self.heartbeats[self.name] = datetime.now(timezone.utc).isoformat()
        if self.state_store is not None:
            self.state_store.save_processed_signals(self.processed_signals)
        self.health_bus.update(
            self.name,
            "RUNNING",
            {
                "executed": executed,
                "telemetry": self.symbol_watch.snapshot(),
            },
        )
