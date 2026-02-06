import logging
import sys
from typing import Dict
from advisor.mt5_pipeline.Client.mt5Client import MetaTrader5Client
from advisor.utils.dataHandler import CacheManager

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

class MarketDataPipeline:
    """
    Stateless market data ingestion logic.
    No scheduling, no sleeping, no threading.
    """

    def __init__(self, client: MetaTrader5Client, cache_handler: CacheManager):
        self.client = client
        self.cache = cache_handler

    def fetch_symbol(self, symbol: str) -> Dict | None:
        try:
            data = self.client.get_multi_tf_data(symbol)
            if data is None:
                logger.warning(f"No data returned for {symbol}")
                return None
            return data
        except Exception:
            logger.exception(f"Failed fetching data for {symbol}")
            return None

    def ingest_symbol(self, symbol: str) -> dict | None:
        data = self.fetch_symbol(symbol)
        if data is None:
            return None
        return data

    def run_once(self, symbols: list[str]) -> None:
        for symbol in symbols:
            data = self.ingest_symbol(symbol)
            if data is None:
                continue
            # atomic per-symbol write
            self.cache.set_atomic(symbol, data)

