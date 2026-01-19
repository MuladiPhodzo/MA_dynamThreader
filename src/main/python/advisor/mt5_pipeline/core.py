import threading
from advisor.Client.mt5Client import MetaTrader5Client
from advisor.utils.cache import CacheManager


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

        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def fetch_symbol_data(self, symbol: str):
        try:
            data = self.mt5_client.get_multi_tf_data(symbol)
            if data is None or data.empty:
                print(f"No data found for symbol {symbol}")
                return None
            return data
        except Exception as e:
            print(f"Error fetching data for symbol {symbol}: {e}")
            return None

    def run_scheduled_cycle(self):
        import time
        """
        runs the pipeline on a scheduled basis
        """
        # main pipeline logic
        # e.g., fetching data, processing, caching, etc.
        try:
            while not self._stop_event.is_set():
                self._stop_event.wait(self.poll_interval)
                # fetch data for all symbols
                for s in self.symbols:
                    data = self.fetch_symbol_data(s)
                    if data is not None:
                        self.cache_handler.set(s, data)

                time.sleep(self.poll_interval)
        except Exception as e:
            print(f"Error in scheduled cycle: {e}")
        finally:
            self._stop_event.clear()
