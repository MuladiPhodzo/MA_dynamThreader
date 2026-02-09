from datetime import datetime, timedelta
import time
import logging
import sys
import threading
from typing import Optional

from advisor.utils.dataHandler import CacheManager
from advisor.mt5_pipeline.Client.mt5Client import MetaTrader5Client
import advisor.mt5_pipeline.core as core
from advisor.scheduler.resource_registry import ResourceRegistry
from advisor.core.health_bus import HealthBus
from scheduler.process_sceduler import ProcessScheduler

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
        shutdown_event: threading.Event,
        heartbeats: dict,  # shared heartbeat state(Authorative)
        health_bus: HealthBus,  # shared health bus state(Authorative)
        registry: ResourceRegistry,
        interval=5,
    ):
        self.registry = registry
        self.cache = cache_handler
        self.client = client
        self.poll_interval = interval
        self.pipeline = None
        self.last_run: datetime = None
        self.done: bool = False
        self.health_bus = health_bus
        self.heartbeats = heartbeats
        self.stop_event = shutdown_event
        self.stop_event.clear()
        self.registry.register("market_data")
        self.scheduler = ProcessScheduler(registry)

    def schedule_pipeline(self):
        pl = core.MarketDataPipeline(self.client, self.cache)
        while not self.stop_event.is_set():
            try:
                now = datetime.utcnow()
                if self.last_run is None:
                    self.done = False
                    pl.run_once(self.client.symbols)
                elif now - self.last_run >= timedelta(minutes=self.poll_interval):
                    self.done = False
                    pl.run_once(self.client.symbols)
                self.heartbeats[self.name] = datetime.utcnow().isoformat()
                self.health_bus.update(self.name, "RUNNING")
                self.last_run = now
                self.done = True
                self.registry.set_ready("market_data")
                time.sleep(60 * self.poll_interval)
            except Exception as e:
                logger.critical(f"pipeline process fail: {e}", exc_info=True)
                self.health_bus.update(self.name, "CRASHED", {"ERROR": str(e)})
                raise
            finally:
                pl.stop()
                self.stop_event.clear()
                self.client.close()

    def run(self):
        try:
            return self._safe_execute()
        except Exception as e:
            self.health_bus.update(self.name, "CRASHED", {"ERROR": str(e)})
            raise

    # -------------------------------
    # Safety Wrapper
    # -------------------------------
    def _safe_execute(self) -> Optional[dict]:
        try:
            return self.scheduler.schedule(
                self.name,
                [],
                self.schedule_pipeline,
                self.stop_event,
                self.heartbeats
            )
        except Exception as e:
            logger.critical(f"{self.name} process fail: {e}", exc_info=True)
