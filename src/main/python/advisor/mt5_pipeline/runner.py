import asyncio
from datetime import datetime, timedelta
import logging
import sys

from advisor.utils.dataHandler import CacheManager
from advisor.Client.mt5Client import MetaTrader5Client
import advisor.mt5_pipeline.core as core
from advisor.scheduler.resource_registry import ResourceRegistry
from advisor.core.health_bus import HealthBus
from scheduler.process_sceduler import ProcessScheduler
from advisor.core.state import StateManager, BotState


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

logger = logging.getLogger("MT5-Pipeline")
class pipelineProcess:

    name = "PipelineProcess"

    def __init__(
        self,
        client: MetaTrader5Client,
        cache_handler: CacheManager,  # shared cache state(Authorative)
        shutdown_event,
        heartbeats: dict,  # shared heartbeat state(Authorative)
        health_bus: HealthBus,  # shared health bus state(Authorative)
        registry: ResourceRegistry,
        scheduler: ProcessScheduler,
        stateManager: StateManager,
        interval=5,
    ):
        self.cache = cache_handler
        self.client = client
        self.poll_interval = interval
        self.last_run: datetime = None
        self.done: bool = False
        self.state = stateManager

        self.registry = registry
        self.health_bus = health_bus
        self.heartbeats = heartbeats
        self.stop_event = shutdown_event
        self.stop_event.clear()
        self.registry.register("market_data")
        self.scheduler = scheduler  # ProcessScheduler(registry)
        self.pl = core.MarketDataPipeline(self.client, self.cache)

    async def _pipeline_cycle(self):

        now = datetime.now(datetime.timezone.utc)

        if (
            self.last_run is None
            or now - self.last_run >= timedelta(minutes=self.poll_interval)
        ):
            logger.info("Running market data ingestion")

            await self.pl.run_once()

            self.last_run = now

            self.registry.set_ready("market_data")

            self.health_bus.update(
                self.name,
                "RUNNING",
                {"symbols": len(self.cache)}
            )

    # -------------------------------
    # Safety Wrapper
    # -------------------------------
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

            # non-blocking wait before next check
            await asyncio.sleep(1)

    def start(self):
        try:
            asyncio.run(self._run_loop())
        except Exception as e:
            self.state.set_state(BotState.state.DEGRADED)
            logger.critical(f"{self.name} crashed: {e}", exc_info=True)
            self.health_bus.update(
                self.name,
                "CRASHED",
                {"error": str(e)}
            )
            raise
