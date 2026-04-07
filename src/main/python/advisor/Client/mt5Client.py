import threading

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import datetime
from tkinter import messagebox

import pandas as pd
from pandas.plotting import register_matplotlib_converters
import MetaTrader5 as mt5
from advisor.utils.logging_setup import get_logger
from advisor.core.state import BotLifecycle, StateManager
register_matplotlib_converters()

logger = get_logger(__name__)

class MetaTrader5Client:
    def __init__(self, state: StateManager):
        self.symbols = []
        self.trade_cfg = None
        self.account_info = None
        self.terminal_info = None
        self.THRESHOLD = 0.0100
        self.state = state

        self._tf_last_fetch = {}      # {(symbol, tf): datetime}
        self._symbol_lock = threading.RLock()
        self._select_lock = threading.Lock()
        self._selected_symbols: set[str] = set()

        self.account_info = None

        self.TF_dict = {
            '5M': {"tf_val": mt5.TIMEFRAME_M5, "prox_limit": 50, "interval_minutes": 5},
            '15M': {"tf_val": mt5.TIMEFRAME_M15, "prox_limit": 75, "interval_minutes": 15},
            '30M': {"tf_val": mt5.TIMEFRAME_M30, "prox_limit": 100, "interval_minutes": 30},
            '1H': {"tf_val": mt5.TIMEFRAME_H1, "prox_limit": 125, "interval_minutes": 60},
            '2H': {"tf_val": mt5.TIMEFRAME_H2, "prox_limit": 150, "interval_minutes": 120},
            '4H': {"tf_val": mt5.TIMEFRAME_H4, "prox_limit": 200, "interval_minutes": 240},
            '6H': {"tf_val": mt5.TIMEFRAME_H6, "prox_limit": 250, "interval_minutes": 360},
            '8H': {"tf_val": mt5.TIMEFRAME_H8, "prox_limit": 300, "interval_minutes": 480},
            '1D': {"tf_val": mt5.TIMEFRAME_D1, "prox_limit": 500, "interval_minutes": 1440},
        }

        self.data_executor = ThreadPoolExecutor(max_workers=5)

    def _determine_bar_count(self, timeframe):
        if self.state.get_state() == BotLifecycle.RUNNING_BACKTEST:
            if timeframe in ("1M", "5M", "15M") :
                return 3000
            if timeframe in ("30M", "1H", "2H"):
                return 2500
            if timeframe in ("4H", "6H", "8H"):
                return 2000
            if timeframe in ("1D",):
                return 1500
            return 1000
        else:
            return 500

    def initialize(self, user_data):
        logger.info("🔑 Logging in to MetaTrader 5...")
        try:

            res = self.connect_account(user_data)

            if not res:
                messagebox.showerror(
                    "Connection failed", f"Failed to log in with error code ={mt5.last_error()}")
                logger.info(f"failed to log in with error code ={mt5.last_error()}")
                return self.close()
            else:
                self.account_info = mt5.account_info()._asdict()
                self.terminal_info = mt5.terminal_info()._asdict()
                self.creds = user_data

            logger.info(f"✅ Successfully connected to MT5 account {user_data['account_id']} on server '{user_data['server']}'")
            return True
        except ConnectionError as e:
            logger.critical(f"Connection to Metatrader terminal refused with: {e}")

    def connect_account(self, user_data):
        if user_data is not None:
            if not mt5.initialize(
                login=int(user_data['account_id']),
                password=user_data['password'],
                server=user_data['server']
            ):
                return False
            else:
                return True
        raise ConnectionError

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
        all_symbols = mt5.symbols_get() or []
        forex_modes = {
            getattr(mt5, "SYMBOL_CALC_MODE_FOREX", None),
            getattr(mt5, "SYMBOL_CALC_MODE_FOREX_NO_LEVERAGE", None),
        }
        disabled_mode = getattr(mt5, "SYMBOL_TRADE_MODE_DISABLED", None)
        symbols = []
        for symbol in all_symbols:
            name = getattr(symbol, "name", "") or ""
            if len(name) < 6:
                # Filter out non-tradable headers like "AUD"
                continue
            calc_mode = getattr(symbol, "trade_calc_mode", None)
            trade_mode = getattr(symbol, "trade_mode", None)
            path = (getattr(symbol, "path", "") or "").lower()
            is_forex = calc_mode in forex_modes or "forex" in path or "fx" in path
            if not is_forex:
                continue
            if disabled_mode is not None and trade_mode == disabled_mode:
                continue
            point = getattr(symbol, "point", 0) or 0
            if point <= 0:
                continue
            if not self._ensure_symbol_selected(symbol.name):
                continue
            symbols.append(symbol.name)
        return symbols

    def get_acc_attr(self, name):
        return self.account_info.get(name)

    def get_history(self, utc_from):
        return mt5.history_deals_get(utc_from, datetime.now(datetime.timezone.utc))

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
            logger.info(f"Failed to get data for {symbol} (error={mt5.last_error()})")
            return None

        data = pd.concat([pd.DataFrame(rates)])
        # convert timestamps to datetime
        data["time"] = pd.to_datetime(data["time"], unit="s")

        return data

    def _should_fetch_tf(self, symbol: str, tf_name: str) -> bool:
        """
        Check if timeframe interval has elapsed.
        """
        from datetime import datetime, timedelta, timezone
        interval = self.TF_dict[tf_name]["interval_minutes"]

        key = (symbol, tf_name)
        now = datetime.now(timezone.utc)

        with self._symbol_lock:
            last_fetch = self._tf_last_fetch.get(key)

            if last_fetch is None:
                self._tf_last_fetch[key] = now
                return True

            if now - last_fetch >= timedelta(minutes=interval):
                self._tf_last_fetch[key] = now
                return True

        return False

    def get_multi_tf_data(self, symbol, backtest=False) -> dict[str, pd.DataFrame] | None:
        """
        Interval-aware parallel multi-timeframe fetcher.
        Fetches ONLY timeframes whose interval has elapsed.

        Returns:
            dict[str, pd.DataFrame] or None
        """
        if not self._ensure_symbol_selected(symbol):
            return None

        logger.info("Checking timeframes for %s...", symbol)
        self.backtest = backtest
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
            logger.debug("No TF intervals elapsed for %s", symbol)
            return {}

        for future in as_completed(futures):
            tf_name = futures[future]
            try:
                df = future.result()
                results[tf_name] = pd.DataFrame(df)
            except Exception as e:
                logger.exception("%s %s fetch failed: %s", symbol, tf_name, e)

        if not results:
            return None

        if results:
            logger.info("Updated TFs for %s: %s", symbol, list(results.keys()))
            return results

    def _ensure_symbol_selected(self, symbol: str) -> bool:
        with self._select_lock:
            if symbol in self._selected_symbols:
                return True

            info = mt5.symbol_info(symbol)
            if info is None:
                logger.warning("Symbol %s not found in MT5", symbol)
                return False

            if not getattr(info, "visible", False):
                if not mt5.symbol_select(symbol, True):
                    logger.warning("Failed to select %s in Market Watch (error=%s)", symbol, mt5.last_error())
                    return False

            self._selected_symbols.add(symbol)
            return True

    def close(self):
        """ Close the MT5 connection.
        """
        mt5.shutdown()
        logger.info("🔌 Disconnected from MetaTrader 5.")
        return False
