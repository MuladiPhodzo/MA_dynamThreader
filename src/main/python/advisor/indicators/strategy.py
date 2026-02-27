import asyncio
import logging
import sys
import threading
from datetime import datetime, timedelta, timezone

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

logger = logging.getLogger("Strategy_Manager")

STRATEGY_REQS = [ProcessRequirement("market_data", max_age=timedelta(minutes=5))]


class strategyManager:
    name = "Strategy"

    def __init__(
        self,
        client,
        shutdown_event: threading.Event,
        heartbeats: dict,
        health_bus: HealthBus,
        registry: ResourceRegistry,
        scheduler: ProcessScheduler,
        store: SignalStore,
        state: StateManager,
        interval=5,
    ):
        self.client = client
        self.registry = registry
        self.scheduler = scheduler
        self.signal_store = store
        self.state = state

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
                timeout=60,
            )
            await asyncio.sleep(self.interval)

    async def _run_cycle(self):
        produced = 0
        for symbol in list(getattr(self.client, "symbols", [])):
            payload = await asyncio.to_thread(self._build_signal, symbol)
            if payload is None:
                continue
            self.signal_store.add_signal(payload)
            produced += 1

        self.registry.set_ready("signals")
        self.heartbeats[self.name] = datetime.now(timezone.utc).isoformat()
        self.health_bus.update(self.name, "RUNNING", {"signals": produced})

    def _build_signal(self, symbol: str):
        data = self.client.get_multi_tf_data(symbol)
        if not data:
            return None
        frame = data.get("15M") or next(iter(data.values()))
        if frame is None or getattr(frame, "empty", True):
            return None

        close = frame["close"]
        if len(close) < 3:
            return None

        side = "buy" if close.iloc[-1] > close.iloc[-2] else "sell"
        price = float(close.iloc[-1])
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
