import asyncio
import logging
import sys
from typing import Dict
from advisor.Client.mt5Client import MetaTrader5Client
from advisor.Client.symbols.symbol_watch import SymbolWatch
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
    """

    def __init__(self, client: MetaTrader5Client, cache_handler: CacheManager, symbol_watch: SymbolWatch):
        self.client = client
        self.cache = cache_handler
        self.symbol_watch = symbol_watch

    def fetch_symbol(self, symbol: str) -> Dict | None:
        try:
            data = self.client.get_multi_tf_data(symbol)
            if data is None:
                logger.warning(f"No data returned for {symbol}")
                self.symbol_watch.mark_error(symbol, "no data returned")
                return None
            return data
        except Exception:
            logger.exception(f"Failed fetching data for {symbol}")
            self.symbol_watch.mark_error(symbol, "fetch failed")
            return None

    async def ingest_symbol(self, symbol: str) -> dict | None:
        # Offload blocking MT5 call
        data = await asyncio.to_thread(self.fetch_symbol, symbol)
        if data is None:
            return None
        return data

    async def run_once(self) -> None:
        symbols = self.symbol_watch.active_symbol_names()
        tasks = [self.ingest_symbol(symbol) for symbol in symbols]

        results = await asyncio.gather(*tasks)

        for symbol, data in zip(symbols, results):
            if data is None:
                continue
            self.cache.set_atomic(symbol, data)
            self.symbol_watch.mark_data_fetch(symbol)
