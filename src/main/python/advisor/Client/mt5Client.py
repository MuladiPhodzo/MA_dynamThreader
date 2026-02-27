import threading
from dateutil.relativedelta import relativedelta

import time
import sys
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
import datetime
from tkinter import messagebox

import pandas as pd
from pandas.plotting import register_matplotlib_converters
import MetaTrader5 as mt5
register_matplotlib_converters()

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

class MetaTrader5Client:
    def __init__(self):
        self.symbols = []
        self.creds = None
        self.account_info = None
        self.terminal_info = None
        self.THRESHOLD = 0.0100
        self.backtest = True

        self._tf_last_fetch = {}      # {(symbol, tf): datetime}
        self._symbol_lock = threading.Lock()

        self.TF_dict = {
            '15M': {"tf_val": mt5.TIMEFRAME_M15, "interval_minutes": 15},
            '30M': {"tf_val": mt5.TIMEFRAME_M30, "interval_minutes": 30},
            '1H': {"tf_val": mt5.TIMEFRAME_H1, "interval_minutes": 60},
            '2H': {"tf_val": mt5.TIMEFRAME_H2, "interval_minutes": 120},
            '4H': {"tf_val": mt5.TIMEFRAME_H4, "interval_minutes": 240},
            '6H': {"tf_val": mt5.TIMEFRAME_H6, "interval_minutes": 360},
            '8H': {"tf_val": mt5.TIMEFRAME_H8, "interval_minutes": 480},
            '1D': {"tf_val": mt5.TIMEFRAME_D1, "interval_minutes": 1440},
        }

        self.data_executor = ThreadPoolExecutor(max_workers=5)

    def _determine_bar_count(self, timeframe):
        if self.backtest:
            if timeframe in ("1M", "5M", "15M") :
                return 3000
            if timeframe in ("30M", "1H", "2H"):
                return 2500
            if timeframe in ("4H", "6H", "8H"):
                return 1500
            if timeframe in ("1D",):
                return 500
            return 1000
        else:
            return 100

    def initialize(self, user_data):
        logger.info("🔑 Logging in to MetaTrader 5...")

        res = self.connect_account(user_data)

        if not res:
            messagebox.showerror(
                "Connection failed", f"Failed to log in with error code ={mt5.last_error()}")
            logger.info(f"failed to log in with error code ={mt5.last_error()}")
            self.close()
            return False
        else:
            logger.info(
                f"✅ Successfully connected to MT5 account {user_data['account_id']} on server '{user_data['server']}'")
            self.account_info = mt5.account_info()._asdict()
            self.terminal_info = mt5.terminal_info()._asdict()
            self.creds = user_data
            logger.info('fetching all available symbols...')
            self.symbols = self.get_Symbols()
            return True

    def get_acc_attr(self, name):
        self.account_info = mt5.account_info()._asdict()
        return self.account_info.get(name)

    def connect_account(self, user_data):
        try:
            if user_data is not None:
                if not mt5.initialize(login=int(user_data['account_id']),
                                      password=user_data['password'],
                                      server=user_data['server']):
                    return False
                else:
                    return True
        except Exception as e:
            logger.info(f"❌ Exception during MT5 initialization: {e}")
            return False

    def check_symbols_availability(self):
        """
        Checks the availability of the symbols in the MetaTrader 5 Market Watch.

        This method iterates through the list of symbols stored in the instance
        and checks if each symbol is available in the MetaTrader 5 Market Watch.
        If any symbol is not available, it logger.infos a message indicating the symbol
        is not available and suggests checking if it is enabled in Market Watch.

        Returns:
            bool: True if all symbols are available, False otherwise.
        """
        available_symbols = [s.name for s in mt5.symbols_get()]
        for pair in self.symbols:
            if pair not in available_symbols:
                logger.info(
                    f"Pair {pair} is not available. Check if it's enabled in Market Watch.")
                return False
        return True

    def get_Symbols(self):
        all_symbols = mt5.symbols_get()
        symbols = []
        for symbol in all_symbols:
            symbols.append(symbol.name)
        return symbols

    def get_live_data(self, symbol, timeframe , bars=1000) -> pd.DataFrame:
        """
        Fetch live market data for a given symbol and timeframe.
        Parameters:
        symbol (str): The financial instrument symbol to retrieve data for.
        timeframe (int): The timeframe for the data (e.g., MT5 timeframes like mt5.TIMEFRAME_M1).
        bars (int, optional): The number of bars to retrieve. Default is 100.
        Returns:
        pd.DataFrame: A DataFrame containing the market data if successful, None otherwise.
        logger.infos:
        - The client's timeframe and its type.
        - A message if data retrieval fails.
        - The retrieved market data.
        """
        data = pd.DataFrame()
        rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, bars)
        if rates is None:
            logger.info(f"Failed to get data for {symbol}")
            return None

        data = pd.concat([pd.DataFrame(rates)])
        # convert timestamps to datetime
        data["time"] = pd.to_datetime(data["time"], unit="s")

        return data

    def get_rates_range(self, symbol, tf_name, tf_value) -> pd.DataFrame:
        data = pd.DataFrame()
        end_date = datetime.datetime.now()
        start_date = end_date - relativedelta(months=6)

        rates = mt5.copy_rates_range(
            symbol, tf_value, start_date, end_date)
        if rates is not None:
            data = pd.DataFrame(rates)
            # convert timestamps to datetime
            data['time'] = pd.to_datetime(data['time'], unit='s')
        else:
            logger.info(
                f"Error in {symbol} {tf_name}: {mt5.last_error()}")

        return data

    def _should_fetch_tf(self, symbol: str, tf_name: str) -> bool:

        from datetime import datetime, timedelta
        """
        Check if timeframe interval has elapsed.
        """
        interval = self.TF_dict[tf_name]["interval_minutes"]

        key = (symbol, tf_name)
        now = datetime.datetime.now(datetime.timezone.utc)

        with self._symbol_lock:
            last_fetch = self._tf_last_fetch.get(key)

            if last_fetch is None:
                self._tf_last_fetch[key] = now
                return True

            if now - last_fetch >= timedelta(minutes=interval):
                self._tf_last_fetch[key] = now
                return True

        return False

    def get_equity(self) -> float:
        info = mt5.account_info()
        if info is None:
            return 0.0
        return float(info.equity)

    def get_multi_tf_data(self, symbol) -> dict[str, pd.DataFrame] | None:
        """
        Interval-aware parallel multi-timeframe fetcher.
        Fetches ONLY timeframes whose interval has elapsed.

        Returns:
            dict[str, pd.DataFrame] or None
        """
        with self._symbol_lock:
            logger.info(f"⏳ Checking timeframes for {symbol}...")

            futures = {}
            results: dict[str, pd.DataFrame] = {}

            for tf_name, tf_meta in self.TF_dict.items():
                if not self._should_fetch_tf(symbol, tf_name):
                    continue

                futures[self.data_executor.submit(
                    self.get_live_data,
                    symbol,
                    tf_meta["tf_val"],
                    self._determine_bar_count(tf_name)
                )] = tf_name

                # Gentle MT5 pacing
                time.sleep(0.15)

            if not futures:
                logger.debug(f"⏭ No TF intervals elapsed for {symbol}")
                return None

            for future in as_completed(futures):
                tf_name = futures[future]
                try:
                    df = future.result()
                    results[tf_name] = pd.DataFrame(df)
                except Exception as e:
                    logger.exception(f"❌ {symbol} {tf_name} fetch failed: {e}")

            if results:
                logger.info(f"✅ Updated TFs for {symbol}: {list(results.keys())}")

            return results

    def close(self):
        """ Close the MT5 connection.
        """
        mt5.shutdown()
        logger.info("🔌 Disconnected from MetaTrader 5.")
        return False
