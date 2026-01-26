import logging
import sys

from advisor.mt5_pipeline.Client.mt5Client import MetaTrader5Client
from advisor.utils.cache import CacheManager

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
class mt5Pipeline:
    def __init__(
        self,
        cache_handler: CacheManager,
        client: MetaTrader5Client,
        poll_interval: int = 60 * 5,
        bars: int = 100
    ):
        super().__init__(daemon=True)
        self.cache_handler = cache_handler
        self.poll_interval = poll_interval
        self.bars = bars

        self.mt5_client = client
        self.symbols = client.symbols

    def stop(self):
        self._stop_event.set()

    def fetch_symbol_data(self, symbol: str):
        try:
            data = self.mt5_client.get_multi_tf_data(symbol)
            if data is None:
                print(f"No data for {symbol}")
                return None
            return data
        except Exception as e:
            print(f"Error fetching data for symbol {symbol}: {e}")
            return None

    def run_Injestion_Cycle(self):
        """
        runs the pipeline on a scheduled basis
        """
        # main pipeline logic
        # e.g., fetching data, processing, caching, etc.
        try:
            # fetch data for all symbols
            for s in self.symbols:
                data = self.fetch_symbol_data(s)
                if data is not None:
                    self.cache_handler.set(s, data)
        except Exception as e:
            print(f"Error in injestion cycle: {e}")
