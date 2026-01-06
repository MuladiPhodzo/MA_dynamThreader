from dateutil.relativedelta import relativedelta

import time
import sys
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
import datetime

import matplotlib.pyplot as plt
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
TF_dict = {
    '15M': mt5.TIMEFRAME_M15,
    '30M': mt5.TIMEFRAME_M30,
    '1H': mt5.TIMEFRAME_H1,
    '2H': mt5.TIMEFRAME_H2,
    '4H': mt5.TIMEFRAME_H4,
    '6H': mt5.TIMEFRAME_H6,
    '8H': mt5.TIMEFRAME_H8,
    '1D': mt5.TIMEFRAME_D1,
}
class MetaTrader5Client:
    def __init__(self, timeframes=None):
        self.symbols = []
        self.account_info = None
        self.terminal_info = None
        self.THRESHOLD = 0.0100
        self.backtest = True

        self.data_executor = ThreadPoolExecutor(max_workers=5)
        if timeframes:
            self._configTF(timeframes)
        else:
            self._configTF(['30M', '2H'])

    def _determine_bar_count(self, timeframe):
        if timeframe in ("1M", "5M", "15M"):
            return 3000
        if timeframe in ("30M", "1H", "2H"):
            return 2500
        if timeframe in ("4H", "6H", "8H"):
            return 1500
        if timeframe in ("1D",):
            return 500
        return 1000

    def _configTF(self, timeframes):
        try:
            self.TF['LTF'] = TF_dict[timeframes[0]]
            self.TF['HTF'] = TF_dict[timeframes[1]]
            self.TF['Main'] = TF_dict['4H']
        except KeyError as e:
            logger.info(
                f"❌ Invalid timeframe provided: {e}. Using default timeframes 30M and 2H.")
            self.TF['LTF'] = TF_dict['30M']
            self.TF['HTF'] = TF_dict['1H']
            self.TF['Main'] = TF_dict['4H']

    def logIn(self, user_data):
        logger.info("🔑 Logging in to MetaTrader 5...")

        res = self.initialize(user_data)

        if not res:
            messagebox.showerror(
                "Connection failed", f"Failed to log in with error code ={mt5.last_error()}")
            logger.info(f"failed to log in with error code ={mt5.last_error()}")
            self.close()
            return False
        else:
            logger.info(
                f"✅ Successfully connected to MT5 account {user_data['account_id']} on server '{user_data['server']}'")
            self.account_info = mt5.account_info()
            self.terminal_info = mt5.terminal_info()

            logger.info('fetching all available symbols...')
            self.symbols = self.get_all_symbols()
            return True

    def initialize(self, user_data):
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
            if symbol.visible:
                symbols.append(symbol.name)
        return symbols

    def get_all_symbols(self) -> list:
        return mt5.symbols.get()

    def get_live_data(self, symbol, timeframe , bars=1000):
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
        data["time"] = pd.to_datetime(data["time"], unit="s")

        return data

    def get_rates_range(self, symbol, tf_name, tf_value):
        multi_tf_data = {}

        end_date = datetime.datetime.now()
        start_date = end_date - relativedelta(months=6)

        rates = mt5.copy_rates_range(
            symbol, tf_value, start_date, end_date)
        if rates is not None and len(rates) > 0:
            df = pd.DataFrame(rates)
            # convert timestamps to datetime
            df['time'] = pd.to_datetime(df['time'], unit='s')
            multi_tf_data[tf_name] = df
        else:
            logger.info(
                f"Failed to retrieve {symbol} rates for {tf_name}, error: {mt5.last_error()}")

        return multi_tf_data

    def get_multi_tf_data(self, symbol):
        """
        parallel multi-timeframe data fetcher.
        Returns: {"15M": df, "30M": df, ...}
        """
        try:
            logger.info(f"⏳ Fetching multi-timeframe data for {symbol}...")
            futures = {}
            # Submit fetch tasks to executor
            for tf_name, tf_value in TF_dict.items():
                # fetch all historical data
                futures[self.data_executor.submit(
                    self.get_live_data,
                    symbol,
                    tf_value,
                    self._determine_bar_count(tf_name)
                )] = tf_name
                time.sleep(0.2)

            # Collect results
            results = {}
            for future in as_completed(futures):
                tf_name = futures[future]
                try:
                    df = future.result()
                    results[tf_name] = df
                except Exception as e:
                    logger.error(f"❌ Error fetching {symbol} {tf_name}: {e}")
                    return False
            logger.info(f"✅ Completed fetching multi-timeframe data for {symbol}.")
            return results
        except Exception as e:
            logger.exception(f'error fetching multi timeframe threads : {e}')
            return False

    def close(self):
        """ Close the MT5 connection.
        """
        mt5.shutdown()
        logger.info("🔌 Disconnected from MetaTrader 5.")
        return False


class DataPlotter:

    @staticmethod
    def plot_ticks(ticks, title):
        if ticks is None or len(ticks) == 0:
            logger.info("No data to plot.")
            return
        ticks_frame = pd.DataFrame(ticks)
        ticks_frame['time'] = pd.to_datetime(ticks_frame['time'], unit='s')
        plt.plot(ticks_frame['time'], ticks_frame['ask'], 'r-', label='ask')
        plt.plot(ticks_frame['time'], ticks_frame['bid'], 'b-', label='bid')
        plt.legend(loc='upper left')
        plt.title(title)
        plt.show()

    @staticmethod
    def plot_rates(rates, title):
        if rates is None or len(rates) == 0:
            logger.info("No data to plot.")
            return
        rates_frame = pd.DataFrame(rates)
        rates_frame['time'] = pd.to_datetime(rates_frame['time'], unit='s')
        plt.plot(rates_frame['time'], rates_frame['close'], label='close')
        plt.title(title)
        plt.legend()
        plt.show()

    @staticmethod
    def plot_charts(rates, fast_period, slow_period):
        if rates is None:
            raise ValueError(
                "Error: `self.results` is None. Run `backtest_strategy()` before plotting.")

        if 'Crossover' not in rates.columns:
            raise ValueError(
                "Error: 'Crossover' column is missing in `self.results`. Check data processing.")

        if 'close' not in rates.columns or rates['close'].empty:
            raise ValueError("No Close data available")

        plt.figure(figsize=(12, 6))

        # Plot the close price
        plt.plot(rates.index, rates['close'], label="Close", color='black')

        # Plot the fast and slow moving averages
        plt.plot(rates.index, rates['Fast_MA'],
                 label=f"Fast MA ({fast_period})", color='blue')
        plt.plot(rates.index, rates['Slow_MA'],
                 label=f"Slow MA ({slow_period})", color='red')

        # Plot buy/sell signals
        buy_signals = rates.loc[rates['Entry'] == "Buy"]
        sell_signals = rates.loc[rates['Entry'] == "Sell"]

        plt.plot(buy_signals.index, buy_signals['Fast_MA'], '^',
                 color='green', markersize=12, label="Buy Signal")
        plt.plot(sell_signals.index, sell_signals['Fast_MA'],
                 'v', color='red', markersize=12, label="Sell Signal")

        # Plot SL/TP levels
        for i in buy_signals.index:
            plt.hlines(rates.loc[i, 'SL'], i, i + 5, colors='red',
                       linestyles='dashed', label="SL" if i == buy_signals.index[0] else "")
            plt.hlines(rates.loc[i, 'TP'], i, i + 5, colors='green',
                       linestyles='dashed', label="TP" if i == buy_signals.index[0] else "")

        for i in sell_signals.index:
            plt.hlines(rates.loc[i, 'SL'], i, i + 5,
                       colors='red', linestyles='dashed')
            plt.hlines(rates.loc[i, 'TP'], i, i + 5,
                       colors='green', linestyles='dashed')

        plt.title('Moving Average Crossover Signals with SL/TP')
        plt.legend(loc='upper left')
        plt.show()
