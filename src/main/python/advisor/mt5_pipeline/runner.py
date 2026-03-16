import asyncio
import logging
import sys
from datetime import datetime, timedelta, timezone

import advisor.mt5_pipeline.core as core
from advisor.Client.symbols.symbol_watch import SymbolWatch
from advisor.core.health_bus import HealthBus
from advisor.core.state import BotLifecycle, StateManager
from advisor.scheduler.process_sceduler import ProcessScheduler
from advisor.scheduler.resource_registry import ResourceRegistry
from advisor.utils.dataHandler import CacheManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("MA_DynamAdvisor.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)

logger = logging.getLogger("MT5-Pipeline")


class pipelineProcess:
    name = "pipeline"

    def __init__(
        self,
        client,
        cache_handler: CacheManager,
        shutdown_event,
        heartbeats: dict,
        health_bus: HealthBus,
        registry: ResourceRegistry,
        scheduler: ProcessScheduler,
        state_manager: StateManager,
        symbol_watch: SymbolWatch,
        interval=5,
    ):
        self.cache = cache_handler
        self.client = client
        self.poll_interval = interval
        self.last_run: datetime | None = None
        self.state = state_manager
        self.symbol_watch = symbol_watch

        self.registry = registry
        self.health_bus = health_bus
        self.heartbeats = heartbeats
        self.stop_event = shutdown_event
        self.registry.register("market_data")
        self.scheduler = scheduler
        self.pipeline = core.MarketDataPipeline(self.client, self.cache, self.symbol_watch)

    async def _pipeline_cycle(self):
        now = datetime.now(timezone.utc)
        if self.last_run and now - self.last_run < timedelta(minutes=self.poll_interval):
            return

        logger.info("Running market data ingestion")
        await self.pipeline.run_once()
        self.last_run = now
        self.registry.set_ready("market_data")
        self.health_bus.update(
            self.name,
            "RUNNING",
            {
                "symbols": len(self.symbol_watch.active_symbols),
                "telemetry": self.symbol_watch.snapshot(),
            },
        )

    async def _run_loop(self):
        while not self.stop_event.is_set():
            await self.scheduler.schedule(
                process_name=self.name,
                required_resources=[],
                task=self._pipeline_cycle,
                shutdown_event=self.stop_event,
                heartbeats=self.heartbeats,
                timeout=60,
            )
            await asyncio.sleep(self.poll_interval)

    def start(self):
        try:
            asyncio.run(self._run_loop())
        except Exception as e:
            self.state.set_state(BotLifecycle.DEGRADED)
            logger.critical("%s crashed: %s", self.name, e, exc_info=True)
            self.health_bus.update(self.name, "CRASHED", {"error": str(e)})
            raise
