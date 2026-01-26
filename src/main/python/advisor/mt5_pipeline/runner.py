from datetime import datetime, timedelta
import time
import logging
import sys
import threading

from advisor.utils.cache import CacheManager
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

logger = logging.getLogger(__name__)
class pipelineProcess:
    def __init__(
        self,
        user_creds: dict,
        cache_handler: CacheManager,
        interval=5,
    ):
        self.init = False
        self.cache = cache_handler
        self.client = MetaTrader5Client()
        self.user_creds = user_creds
        self.poll_interval = interval
        self.pipeline = None
        self.last_run: datetime = None

        self.stop_event = threading.Event()
        self.stop_event.set()

    def init_client(self):
        try:
            if not self.client.logIn(user_data=self.user_creds):
                raise ConnectionRefusedError
            else:
                return True
        except ConnectionError as e:
            logger.exception(f"error connecting metatrader client: {e}")

    def init_pipeline(self):
        try:
            self.init = self.init_client()
            if not self.init:
                raise SystemError
            else:
                return core.mt5Pipeline(self.cache, self.client, self.stop_event)
        except SystemError as e:
            logger.exception(f"error initialising pipeline module: {e}")

    def schedule_pipeline(self):
        pl = self.init_pipeline()
        while not self.stop_event.is_set():
            try:
                now = datetime.utcnow()
                if self.last_run is None:
                    pl.run_Injestion_Cycle()
                elif now - self.last_run >= timedelta(minutes=self.poll_interval):
                    pl.run_Injestion_Cycle()
                self.last_run = now
                time.sleep(60 * self.poll_interval)
            except ChildProcessError as e:
                logger.critical(f"pipeline process fail: {e}", exc_info=True)
                self.stop_event.clear()
            finally:
                pl.stop()
                self.client.close()
