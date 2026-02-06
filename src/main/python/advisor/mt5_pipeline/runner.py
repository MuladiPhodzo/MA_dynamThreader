from datetime import datetime, timedelta
import time
import logging
import sys

from advisor.utils.dataHandler import CacheManager
from advisor.mt5_pipeline.Client.mt5Client import MetaTrader5Client
import advisor.mt5_pipeline.core as core

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
    def __init__(
        self,
        client: MetaTrader5Client,
        cache_handler: CacheManager,  # shared cache state(Authorative)
        shutdown_event,
        interval=5,
    ):
        self.init = False
        self.cache = cache_handler
        self.client = client
        self.user_creds = self.client.creds
        self.poll_interval = interval
        self.pipeline = None
        self.last_run: datetime = None
        self.done: bool = False

        self.stop_event = shutdown_event
        self.stop_event.set()

        self.init_client()

    def schedule_pipeline(self, heartbeats: dict):
        name = ""
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
                heartbeats[name] = datetime.utcnow().isoformat()
                self.last_run = now
                self.done = True
                time.sleep(60 * self.poll_interval)
            except ChildProcessError as e:
                logger.critical(f"pipeline process fail: {e}", exc_info=True)
                self.stop_event.clear()
            finally:
                pl.stop()
                self.client.close()
