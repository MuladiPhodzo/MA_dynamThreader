import asyncio
import logging
import sys
import threading
from datetime import datetime, timedelta, timezone

from advisor.core.health_bus import HealthBus
from advisor.core.state import BotLifecycle, StateManager, Strategy
from advisor.indicators.signal_store import SignalStore
from advisor.scheduler.process_sceduler import ProcessScheduler
from advisor.scheduler.requirements import ProcessRequirement
from advisor.scheduler.resource_registry import ResourceRegistry
from advisor.utils import dataHandler
from advisor.Client.symbols.symbol_watch import SymbolWatch

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("MA_DynamAdvisor.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)

logger = logging.getLogger("Strategy_Manager")

STRATEGY_REQS = [ProcessRequirement("market_data", max_age=timedelta(minutes=5))]
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
    ):
        self.client = client
        self.cache = cache_handler
        self.registry = registry
        self.scheduler = scheduler
        self.signal_store = store
        self.state = state
        self.symbol_watch = symbol_watch

        self.health_bus = health_bus
        self.heartbeats = heartbeats
        self.stop_event = shutdown_event
        self.interval = interval

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
        while not self.stop_event.is_set():
            await self.scheduler.schedule(
                process_name=self.name,
                required_resources=STRATEGY_REQS,
                task=self._run_cycle,
                shutdown_event=self.stop_event,
                heartbeats=self.heartbeats,
                timeout=100,
            )
            await asyncio.sleep(self.interval)

    async def _run_cycle(self):
        produced = 0
        for symbol in self.symbol_watch.active_symbols:
            for s in symbol.strategies:
                payload = await asyncio.to_thread(self._build_signal, symbol.symbol, s)
                if payload is None:
                    continue
                self.signal_store.add_signal(payload)
                self.symbol_watch.mark_signal(symbol)
                produced += 1

        self.registry.set_ready("signals")
        self.heartbeats[self.name] = datetime.now(timezone.utc).isoformat()
        self.health_bus.update(
            self.name,
            "RUNNING",
            {
                "signals": produced,
                "telemetry": self.symbol_watch.snapshot(),
            },
        )

    def _build_signal(self, symbol, strategy: Strategy):
        try:
            data = strategy(False)
        except Exception as e:
            logger.exception("Signal build failed for %s: %s", symbol, e)
            self.symbol_watch.mark_error(symbol, f"signal build failed: {e}")
            return None
        if not data or hasattr(data["sig"], "(W)"):
            return None

        frame = data["frame"]
        if frame is None or getattr(frame, "empty", True):
            return None

        side = data["sig"]
        price = float(frame["close"])
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
