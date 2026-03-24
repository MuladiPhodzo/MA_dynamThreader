import asyncio
import threading
from datetime import datetime, timedelta, timezone

from advisor.core.health_bus import HealthBus
from advisor.core.event_bus import EventBus
from advisor.core import events
from advisor.core.state import BotLifecycle, StateManager, Strategy, SymbolState
from advisor.indicators.signal_store import SignalStore
from advisor.scheduler.process_sceduler import ProcessScheduler
from advisor.scheduler.requirements import ProcessRequirement
from advisor.scheduler.resource_registry import ResourceRegistry
from advisor.utils import dataHandler
from advisor.Client.symbols.symbol_watch import SymbolWatch
from advisor.utils.logging_setup import get_logger

logger = get_logger("Strategy_Manager")

STRATEGY_REQS = [
    ProcessRequirement("market_data", max_age=timedelta(minutes=5)),
    ProcessRequirement("symbol_ingestion", max_age=timedelta(minutes=5)),
]
class strategyManager:
    name = "strategy"

    def __init__(
        self,
        client,
        cache_handler: dataHandler.CacheManager,
        shutdown_event: threading.Event,
        heartbeats: dict,
        health_bus: HealthBus,
        registry: ResourceRegistry,
        scheduler: ProcessScheduler,
        store: SignalStore,
        state: StateManager,
        symbol_watch: SymbolWatch,
        interval=5,
        event_bus: EventBus | None = None,
        state_store=None,
    ):
        self.client = client
        self.cache = cache_handler
        self.registry = registry
        self.scheduler = scheduler
        self.signal_store = store
        self.state = state
        self.symbol_watch = symbol_watch
        self._warmup_logs: dict[str, datetime] = {}

        self.health_bus = health_bus
        self.heartbeats = heartbeats
        self.stop_event = shutdown_event
        self.interval = interval
        self.event_bus = event_bus
        self.state_store = state_store
        self._last_idle_beat: datetime | None = None

        self.registry.register("signals")

    def start(self):
        try:
            asyncio.run(self._safe_execute())
        except Exception as e:
            self.state.set_state(BotLifecycle.DEGRADED)
            logger.critical("%s crashed: %s", self.name, e, exc_info=True)
            self.health_bus.update(self.name, "CRASHED", {"error": str(e)})
            raise

    async def _safe_execute(self):
        if self.event_bus is None:
            raise RuntimeError("Event bus not configured for strategy process")

        sub = self.event_bus.subscribe(events.MARKET_DATA_READY)
        try:
            while not self.stop_event.is_set():
                evt = await sub.next(stop_event=self.stop_event, timeout=1.0)
                if evt is None:
                    self._idle_heartbeat()
                    continue
                await self.scheduler.schedule(
                    process_name=self.name,
                    required_resources=STRATEGY_REQS,
                    task=self._run_cycle,
                    shutdown_event=self.stop_event,
                    heartbeats=self.heartbeats,
                    timeout=400,
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
                "warmup_pending": self._count_warmup_pending(),
            },
        )

    async def _run_cycle(self):
        produced = 0
        for symbol in self.symbol_watch.active_symbols:
            if not self._symbol_ready(symbol):
                self._log_warmup(symbol.symbol)
                continue
            for s in symbol.strategies:
                payload = await asyncio.to_thread(self._build_signal, symbol.symbol, s)
                if payload is None:
                    continue
                self.signal_store.add_signal(payload)
                self.symbol_watch.mark_signal(symbol)
                produced += 1

        self.registry.set_ready("signals")
        self.heartbeats[self.name] = datetime.now(timezone.utc).isoformat()
        if self.state_store is not None and produced:
            self.state_store.save_signal_store(self.signal_store)
        if self.event_bus is not None:
            self.event_bus.emit(
                events.SIGNALS_READY,
                {
                    "signals": produced,
                    "telemetry": self.symbol_watch.snapshot(),
                    "warmup_pending": self._count_warmup_pending(),
                },
            )
        self.health_bus.update(
            self.name,
            "RUNNING",
            {
                "signals": produced,
                "telemetry": self.symbol_watch.snapshot(),
                "warmup_pending": self._count_warmup_pending(),
            },
        )

    def _symbol_ready(self, symbol: SymbolState) -> bool:
        name = symbol.symbol
        cached = self.cache.get(name)
        if cached:
            return True
        telem = self.symbol_watch.get_telemetry(name)
        if telem and telem.data_fetch_count > 0:
            return True
        return False

    def _log_warmup(self, symbol: str) -> None:
        now = datetime.now(timezone.utc)
        last = self._warmup_logs.get(symbol)
        if last and now - last < timedelta(minutes=1):
            return
        logger.warning("%s: waiting for warm-up data before generating signals.", symbol)
        self._warmup_logs[symbol] = now

    def _count_warmup_pending(self) -> int:
        pending = 0
        for symbol in self.symbol_watch.active_symbols:
            if not self._symbol_ready(symbol):
                pending += 1
        return pending

    def _build_signal(self, symbol, strategy: Strategy):
        try:
            data = strategy.strategy(False)
        except Exception as e:
            logger.exception("Signal build failed for %s: %s", symbol, e)
            self.symbol_watch.mark_error(symbol, f"signal build failed: {e}")
            return None
        if not isinstance(data, dict):
            return None

        raw_sig = str(data.get("sig") or "")
        if not raw_sig or "(W)" in raw_sig:
            return None

        frame = data.get("frame")
        if frame is None or getattr(frame, "empty", False):
            return None

        side = None
        lowered = raw_sig.lower()
        if "bullish" in lowered:
            side = "buy"
        elif "bearish" in lowered:
            side = "sell"
        if side is None:
            return None

        try:
            close = None
            if hasattr(frame, "get"):
                close = frame.get("close")
            if close is None and hasattr(frame, "__getitem__"):
                close = frame["close"]
            if hasattr(close, "iloc"):
                close = close.iloc[-1]
            if hasattr(close, "item"):
                close = close.item()
            price = float(close)
        except Exception as e:
            logger.exception("Failed to parse close price for %s: %s", symbol, e)
            return None

        sl = max(price * 0.001, 1e-6)
        tp = max(price * 0.002, 1e-6)
        return {
            "id": f"{symbol}:{datetime.now(timezone.utc).isoformat()}",
            "symbol": symbol,
            "side": side,
            "sl": sl,
            "tp": tp,
            "timestamp": datetime.now(timezone.utc),
            "data": {"price": price},
        }
