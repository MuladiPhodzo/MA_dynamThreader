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
from advisor.utils import dataHandler
from advisor.Client.symbols.symbol_watch import SymbolWatch
from advisor.utils.logging_setup import get_logger

logger = get_logger("Strategy_Manager")

STRATEGY_REQS = [
    ProcessRequirement("market_data", max_age=timedelta(minutes=5)),
    ProcessRequirement("symbols", max_age=timedelta(minutes=5)),
]

class StrategyManager:
    name = "strategy"

    def __init__(
        self,
        scheduler: ProcessScheduler,
        event_bus: EventBus,
        shutdown_event: threading.Event,
        heartbeats: dict,
        health_bus: HealthBus,
        cache_handler: dataHandler.CacheManager,
        symbol_watch: SymbolWatch,
        store: SignalStore,
        state_manager: StateManager,
    ):
        self.scheduler = scheduler
        self.event_bus = event_bus
        self.stop_event = shutdown_event
        self.heartbeats = heartbeats
        self.health_bus = health_bus
        self.cache = cache_handler
        self.symbol_watch = symbol_watch
        self.signal_store = store

        self.state = state_manager

        self._running: set[str] = set()  # prevent duplicate runs per symbol
        self._subscribed_symbols: set[str] = set()

    # -------------------------------------------------
    # Registration (NO LOOP)
    # -------------------------------------------------

    def register(self):
        """
        Subscribe per-symbol to market data events.
        """
        self.event_bus.subscribe(events.MARKET_DATA_READY, self._on_symbols)
        for symbol in self.symbol_watch.all_symbol_names():
            self._subscribe_symbol(symbol)

    def _subscribe_symbol(self, symbol: str) -> None:
        if symbol in self._subscribed_symbols:
            return
        self._subscribed_symbols.add(symbol)
        self.event_bus.subscribe(
            f"{events.MARKET_DATA_READY}:{symbol}",
            lambda evt, s=symbol: asyncio.create_task(self._on_market_data(s, evt)),
        )

        self.event_bus.subscribe(
            f"{events.BACKTEST_COMPLETED}:{symbol}",
            lambda evt, s=symbol: asyncio.create_task(self._on_market_data(s, evt)),
        )

    def _on_symbols(self, event) -> None:
        symbols: list[str] = []
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

    async def _on_market_data(self, symbol: str, event):
        if self.stop_event.is_set():
            return

        if symbol in self._running:
            return  # prevent overlap

        self._running.add(symbol)

        try:
            self.state.set_state(BotLifecycle.RUNNING_BACKTEST)
            await self.scheduler.schedule(
                process_name=f"{self.name}:{symbol}",
                required_resources=[],  # event-driven → no gating
                task=lambda: self._run_symbol(symbol),
                shutdown_event=self.stop_event,
                heartbeats=self.heartbeats,
                timeout=120,
            )
        except Exception as e:
            self.state.set_state(BotLifecycle.DEGRADED)
            self.health_bus.update(f"{self.name}:{symbol}", "ERROR", {"error": str(e)})
            logger.exception("Failed to schedule strategy for %s: %s", symbol, e)
            self.symbol_watch.mark_error(symbol, f"scheduling failed: {e}")
        finally:
            self._running.discard(symbol)

    # -------------------------------------------------
    # Per-Symbol Execution (CORE LOGIC)
    # -------------------------------------------------

    async def _run_symbol(self, symbol: str):
        state = self.symbol_watch.get(symbol)

        if state is None:
            return

        if not getattr(state, "enabled", False):
            return

        if not self._symbol_ready(state):
            self._log_warmup(symbol)
            return

        produced = 0

        for strat in state.strategies:
            try:
                payload = await asyncio.to_thread(
                    self._build_signal, symbol, strat
                )

                if payload is None:
                    continue

                self.signal_store.add_signal(payload)
                self.symbol_watch.mark_signal(symbol)
                produced += 1
                # 🔥 Emit per-symbol signal
                await self.event_bus.publish(
                    f"{events.SIGNAL_GENERATED}:{symbol}",
                    payload,
                )

            except Exception as e:
                logger.exception("Strategy failed for %s: %s", symbol, e)
                self.symbol_watch.mark_error(symbol, str(e))

        # heartbeat
        self.heartbeats[f"{self.name}:{symbol}"] = datetime.now(timezone.utc).isoformat()

        # health update
        self.health_bus.update(
            f"{self.name}:{symbol}",
            "RUNNING",
            {
                "signals": produced,
                "symbol": symbol,
            },
        )

    # -------------------------------------------------
    # Helpers (UNCHANGED LOGIC)
    # -------------------------------------------------

    def _symbol_ready(self, symbol: SymbolState) -> bool:
        cached = self.cache.get(symbol.symbol)
        if cached:
            return True

        telem = self.symbol_watch.get_telemetry(symbol.symbol)
        return telem and telem.data_fetch_count > 0

    def _log_warmup(self, symbol: str) -> None:
        logger.debug("%s: waiting for warm-up data", symbol)

    def _build_signal(self, symbol, strategy: Strategy):
        try:
            data = strategy.strategy(False)
        except Exception as e:
            logger.exception("Signal build failed for %s: %s", symbol, e)
            return None

        if not isinstance(data, dict):
            return None

        raw_sig = str(data.get("sig") or "")
        if not raw_sig or "(W)" in raw_sig:
            logger.info(f"skipping weak {symbol} signal")
            return None

        frame = data.get("frame")
        if frame is None or getattr(frame, "empty", False):
            return None

        side = self._parse_side(raw_sig)
        if side is None:
            return None

        price = self._extract_price(symbol, frame)
        if price is None:
            return None

        return {
            "id": f"{symbol}:{datetime.now(timezone.utc).isoformat()}",
            "symbol": symbol,
            "side": side,
            "sl": max(price * 0.001, 1e-6),
            "tp": max(price * 0.002, 1e-6),
            "timestamp": datetime.now(timezone.utc),
            "data": {"price": price},
        }

    def _parse_side(self, raw_sig: str) -> str | None:
        raw_sig = raw_sig.lower()
        if "bullish" in raw_sig:
            return "buy"
        if "bearish" in raw_sig:
            return "sell"
        return None

    def _extract_price(self, symbol: str, frame) -> float | None:
        try:
            close = frame["close"]
            if hasattr(close, "iloc"):
                close = close.iloc[-1]
            return float(close)
        except Exception:
            logger.exception("Failed to extract price for %s", symbol)
            return None
