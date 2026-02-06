import logging
from pathlib import Path
import json
import sys
from datetime import datetime
from dateutil.relativedelta import relativedelta
import threading

from advisor.mt5_pipeline.Client.mt5Client import MetaTrader5Client
from advisor.indicators.MovingAverage import MovingAverage as MA
from advisor.utils.dataHandler import CacheManager
from . import metrics

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

BACKTEST_STATE_FILE = Path("config.json")
BACKTEST_INTERVAL_DAYS = 90  # ≈ 3 months
class backtestProcess:
    def __init__(
        self,
        client: MetaTrader5Client,
        cache_handler: CacheManager
    ):
        self.client = client
        self.cache = cache_handler
        self.stop_event = threading.Event()
        self.next_cycle = datetime.date()

    def load_last_backtest_time(self) -> datetime | None:
        if not BACKTEST_STATE_FILE.exists():
            return None

        with open(BACKTEST_STATE_FILE, "r") as f:
            data = json.load(f)
            return datetime.fromisoformat(data["last_backtest"])

    def save_last_backtest_time(self, ts: datetime):
        with open(BACKTEST_STATE_FILE, "w") as f:
            json.dump({"last_backtest": ts.isoformat()}, f)

    # -------------------------
    # Backtesting Logic
    # -------------------------
    def backtest_all_symbols(self, symbols: list[str]) -> dict:
        """
        Backtest all symbols and return performance registry.
        """

        results = {}

        for symbol in symbols:
            try:
                data = self.client.get_multi_tf_data(symbol)
                indicator = MA.MovingAverageCrossover(symbol, data)
                data = indicator.run_MA_Strategy(data, backtest=True)
                results[symbol] = data
                
            except Exception as e:
                logger.warning(f"Backtest failed for {symbol}: {e}")
        return results

    def select_best_symbols(self, results: dict, min_score=0.78) -> list:
        """
        Select best-performing symbols based on backtest metrics.
        """
        symbols = metrics.metrics.rank_symbols(results.get("summaries"), results.get("data"))
        return [
            symbol
            for symbol in symbols
            if symbol.get("summaries")("score") >= min_score
        ]

    def run_backtest_cycle(self):
        """
        Run quarterly backtest and update active symbols.
        """
        now = datetime.now()
        last_run = self.load_last_backtest_time()
        if last_run and now < last_run + relativedelta(months=3):
            return  # Too early
        try:
            logger.info(f"Starting scheduled backtest cycle. date now: {now}, next cycle: {self.next_cycle}")
            self.client.backtest = True
            results = self.backtest_all_symbols(self.client.symbols)
            best_symbols = self.select_best_symbols(results=results)
            # Activate only best symbols
            self.client.symbols.clear()
            self.client.symbols = best_symbols
            # cache best performing symbol data
            for sym in self.client.symbols:
                self.cache.set(sym, results[sym]["data"])

            logger.info(f"Backtest cycle completed. Next cycle scheduled for {self.next_cycle}.")

        except ChildProcessError as e:
            logger.critical(f"backtest process failure: {e}")
            self.stop()

    def stop(self):
        self.stop_event.clear()
