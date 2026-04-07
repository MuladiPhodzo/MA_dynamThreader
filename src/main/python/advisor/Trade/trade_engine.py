import asyncio
from datetime import datetime, timedelta, timezone

from advisor.Trade.RiskManager import RiskManager
from advisor.Trade.tradeHandler import mt5TradeHandler
from advisor.Trade.trateState import TradeStateManager
from advisor.core.health_bus import HealthBus
from advisor.core.event_bus import EventBus
from advisor.core import events
from advisor.core.state import BotLifecycle, StateManager
from Strategy_model.indicators.signal_store import SignalStore
from advisor.scheduler.process_sceduler import ProcessScheduler
from advisor.scheduler.requirements import ProcessRequirement
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
        health_bus: HealthBus,
        heartbeats: dict,
        shutdown_event,
        scheduler: ProcessScheduler,
        state_manager: StateManager,
        symbol_watch: SymbolWatch,
        event_bus: EventBus,
        trade_state: TradeStateManager | None = None,
    ):
        self.client = client
        self.signal_store = signal_store
        self.health_bus = health_bus
        self.heartbeats = heartbeats
        self.stop_event = shutdown_event
        self.scheduler = scheduler
        self.symbol_watch = symbol_watch
        self.event_bus = event_bus
        self.state_manager = state_manager

        self.trade_state = trade_state or TradeStateManager(self.client)

        self.risk_manager = RiskManager(
            client=self.client,
            trade_state=self.trade_state,
            state_manager=self.state_manager,
            health_bus=self.health_bus,
        )

        self.executor = mt5TradeHandler(self.client, logger)

        self._running: set[str] = set()  # per-symbol lock
        self._processed: set[str] = set()  # dedup signals
        self.processed_signals = self._processed
        self._subscribed_symbols: set[str] = set()

    # -------------------------------------------------
    # Registration (NO LOOP)
    # -------------------------------------------------

    def register(self):
        self.event_bus.subscribe(events.SYMBOLS, self._on_symbols)
        for symbol in self.symbol_watch.all_symbol_names():
            self._subscribe_symbol(symbol)

    def _subscribe_symbol(self, symbol: str) -> None:
        if symbol in self._subscribed_symbols:
            return
        self._subscribed_symbols.add(symbol)
        self.event_bus.subscribe(
            f"{events.SIGNAL_GENERATED}:{symbol}",
            lambda evt, s=symbol: asyncio.create_task(self._on_signal(s, evt)),
        )

    def _on_symbols(self, event) -> None:
        symbols = []
        if event and getattr(event, "payload", None):
            payload_symbols = event.payload.get("symbols")
            if isinstance(payload_symbols, list):
                symbols = payload_symbols
        if not symbols:
            symbols = self.symbol_watch.all_symbol_names()
        for symbol in symbols:
            self._subscribe_symbol(symbol)

    # -------------------------------------------------
    # Event Handler
    # -------------------------------------------------

    async def _on_signal(self, symbol: str, event):
        if self.stop_event.is_set():
            return

        if symbol in self._running:
            return  # prevent overlap

        self._running.add(symbol)

        try:
            await self.scheduler.schedule(
                process_name=f"{self.name}:{symbol}",
                required_resources=[],  # event-driven
                task=lambda: self._execute_symbol(symbol, event.payload),
                shutdown_event=self.stop_event,
                heartbeats=self.heartbeats,
                timeout=30,
            )
        except Exception as e:
            logger.exception("Execution failed for %s: %s", symbol, e)
            self.state_manager.set_state(BotLifecycle.DEGRADED)
            self.symbol_watch.mark_error(symbol, str(e))
            self.health_bus.update(f"{self.name}:{symbol}", "ERROR", {"error": str(e)})
        finally:
            self._running.discard(symbol)

    # -------------------------------------------------
    # Core Execution (PER SYMBOL)
    # -------------------------------------------------

    async def _execute_symbol(self, symbol: str, payload: dict):
        try:
            signal_id = payload.get("id")

            if not signal_id or signal_id in self._processed:
                return

            state = self.symbol_watch.get(symbol)
            if state is not None and not getattr(state, "enabled", False):
                return

            # reconstruct signal (or pass full object if preferred)
            signal = self.signal_store.get_latest(symbol)
            if not signal or not signal.is_valid():
                return

            allowed, lot = self.risk_manager.validate(signal)
            if not allowed:
                return

            trade = self.executor.place_market_order(
                symbol=signal.symbol,
                side=signal.side,
                lot=lot,
                sl_points=signal.sl,
                tp_points=signal.tp,
            )

            self.trade_state.register_open(trade)
            self.risk_manager.register_trade_open()

            self._processed.add(signal_id)

            self.symbol_watch.mark_trade(symbol)

            # heartbeat
            self.heartbeats[f"{self.name}:{symbol}"] = datetime.now(timezone.utc).isoformat()

            # health
            self.health_bus.update(
                f"{self.name}:{symbol}",
                "RUNNING",
                {
                    "symbol": symbol,
                    "executed": 1,
                },
            )

            # 🔥 Emit trade event (optional but powerful)
            await self.event_bus.publish(
                f"{events.ORDER_EXECUTED}:{symbol}",
                {
                    "symbol": symbol,
                    "trade": str(trade),
                },
            )

        except Exception as e:
            logger.exception("Execution failed for %s: %s", symbol, e)
            self.symbol_watch.mark_error(symbol, str(e))
