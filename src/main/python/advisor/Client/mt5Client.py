from dateutil.relativedelta import relativedelta
import os
import datetime
import csv
import json

import matplotlib.pyplot as plt
from tkinter import messagebox
import pandas as pd
from pandas.plotting import register_matplotlib_converters
import MetaTrader5 as mt5

register_matplotlib_converters()


class MetaTrader5Client:
    def __init__(self, timeframes=None):
        self.symbols = []
        self.THRESHOLD = 0.0100
        self.account_info = None
        self.terminal_info = None
        self.TF = {}
        if timeframes:
            self._configTF(timeframes)
        else:
            self._configTF(['30M', '2H'])

    def _configTF(self, timeframes):
        try:
            TF_dict = {
                '1M': mt5.TIMEFRAME_M1,
                '15M': mt5.TIMEFRAME_M15,
                '30M': mt5.TIMEFRAME_M30,
                '1H': mt5.TIMEFRAME_H1,
                '2H': mt5.TIMEFRAME_H2,
                '4H': mt5.TIMEFRAME_H4,
                '1D': mt5.TIMEFRAME_D1,
                '1W': mt5.TIMEFRAME_W1
            }
            self.TF['LTF'] = TF_dict[timeframes[0]]
            self.TF['HTF'] = TF_dict[timeframes[1]]
        except KeyError as e:
            print(
                f"❌ Invalid timeframe provided: {e}. Using default timeframes 30M and 2H.")
            self.TF['LTF'] = TF_dict['30M']
            self.TF['HTF'] = TF_dict['2H']

    def logIn(self, user_data):
        print("🔑 Logging in to MetaTrader 5...")

        res = self.initialize(user_data)

        if not res[0]:
            messagebox.showerror(
                "Connection failed", f"Failed to log in with error code ={mt5.last_error()}")
            print(f"failed to log in with error code ={mt5.last_error()}")
            mt5.shutdown()
            return False
        messagebox.showinfo("Login successful",
                            "Connecting to MetaTrader 5....")
        print(
            f"✅ Successfully connected to MT5 account {user_data['account_id']} on server '{user_data['server']}'")
        return res

    def initialize(self, user_data):
        try:
            if user_data is not None:
                print("Initializing MetaTrader 5 with user data...")
                if not mt5.initialize(login=int(user_data['account_id']), password=user_data['password'], server=user_data['server']):
                    print("initialize() failed, error code =", mt5.last_error())
                    messagebox.showerror(
                        "Login Error", "Failed to connect to MetaTrader 5.")
                    mt5.shutdown()
                    return [False, []]

            else:
                if not mt5.initialize():
                    print("initialize() failed, error code =", mt5.last_error())
                    mt5.shutdown()
                    return [False, []]

        finally:
            print("🚀 Bot is ready to start trading!")
            self.account_info = mt5.account_info()
            self.terminal_info = mt5.terminal_info()

            print('searching for available symbols...')
            symbols = self.get_Symbols()

            return [True, symbols]

    def check_symbols_availability(self):
        """
        Checks the availability of the symbols in the MetaTrader 5 Market Watch.

        This method iterates through the list of symbols stored in the instance
        and checks if each symbol is available in the MetaTrader 5 Market Watch.
        If any symbol is not available, it prints a message indicating the symbol
        is not available and suggests checking if it is enabled in Market Watch.

        Returns:
            bool: True if all symbols are available, False otherwise.
        """
        available_symbols = [s.name for s in mt5.symbols_get()]
        for pair in self.symbols:
            if pair not in available_symbols:
                print(
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

    def get_live_data(self, symbol, timeframe, bars=1000):
        """
        Fetch live market data for a given symbol and timeframe.
        Parameters:
        symbol (str): The financial instrument symbol to retrieve data for.
        timeframe (int): The timeframe for the data (e.g., MT5 timeframes like mt5.TIMEFRAME_M1).
        bars (int, optional): The number of bars to retrieve. Default is 100.
        Returns:
        pd.DataFrame: A DataFrame containing the market data if successful, None otherwise.
        Prints:
        - The client's timeframe and its type.
        - A message if data retrieval fails.
        - The retrieved market data.
        """
        rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, bars)
        if rates is None:
            print(f"Failed to get data for {symbol}")
            return None

        return pd.DataFrame(rates)

    def get_rates_range(self, symbol):
        multi_tf_data = {}

        end_date = datetime.datetime.now()
        start_date = end_date - relativedelta(months=6)

        for tf_name, tf_value in self.TF.items():
            rates = mt5.copy_rates_range(
                symbol, tf_value, start_date, end_date)
            if rates is not None and len(rates) > 0:
                df = pd.DataFrame(rates)
                # convert timestamps to datetime
                df['time'] = pd.to_datetime(df['time'], unit='s')
                multi_tf_data[tf_name] = df
            else:
                print(
                    f"Failed to retrieve {symbol} rates for {tf_name}, error: {mt5.last_error()}")

        return multi_tf_data

    def get_multi_tf_data(self, symbol):
        """Fetch data for multiple timeframes and return as a dictionary with HTF and LTF keys."""

        multi_tf_data = {}

        print(f'🔍 Fetching data for {symbol}...')

        # Loop over the provided timeframes
        for tf_name, tf_value in self.TF.items():

            # Fetch live data for each timeframe
            data = self.get_live_data(symbol, tf_value, 1000)

            if data is not None:
                # Store the fetched data in the dictionary under its corresponding timeframe name
                multi_tf_data[tf_name] = pd.DataFrame(data)
            else:
                print(f"Failed to fetch data for {symbol} on {tf_name}.")

        # Store the data and return
        self.data = multi_tf_data
        return multi_tf_data

    def toCSVFile(self, file_path):
        """
        Save ratesData to a CSV file.
        - Creates the file if it doesn't exist.
        - Appends to the file if it already exists.
        """

        if self.Ratesdata is not None:
            file_exists = os.path.isfile(file_path)

            if not file_exists:
                self.Ratesdata.to_csv(
                    file_path, index=False, mode='w', header=True)
                print(
                    f"New file created and entry levels saved to {file_path}.")
            else:
                self.Ratesdata.to_csv(
                    file_path, index=False, mode='a', header=False)
                print(f"Entry levels appended to existing file {file_path}.")
        else:
            print("No rates to save.")

    def close(self):
        """ Close the MT5 connection.
        """
        mt5.shutdown()
        print("🔌 Disconnected from MetaTrader 5.")
        return False


class dataHandler:
    def save_trade(self, trade_data, file_type="json"):
        """
        Saves trade information to a file (JSON or CSV).

        Args:
            trade_data (dict): Trade details to save.
            file_type (str): 'json' or 'csv'.
        """
        # Ensure trades directory exists
        os.makedirs("trades", exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

        if file_type.lower() == "json":
            file_path = "trades/trades_log.json"
            self._toJSON(file_path, trade_data, timestamp)

        elif file_type.lower() == "csv":
            file_path = "trades/trades_log.csv"
            self._toCSV(file_path, trade_data, timestamp)

        else:
            raise ValueError("Unsupported file type. Use 'json' or 'csv'.")

    def _toJSON(self, file_path, trade_data, timestamp):
        """Helper method for JSON saving"""
        entry = {"timestamp": timestamp, **trade_data}

        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except json.JSONDecodeError:
                data = []
        else:
            data = []

        data.append(entry)

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)

        print(f"✅ Trade saved to {file_path}")

    def _toCSV(self, file_path, trade_data, timestamp):
        """Helper method for CSV saving"""
        entry = {"timestamp": timestamp, **trade_data}
        file_exists = os.path.exists(file_path)

        with open(file_path, "a", newline='', encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=entry.keys())
            if not file_exists:
                writer.writeheader()
            writer.writerow(entry)
        print(f"✅ Trade saved to {file_path}")


class DataPlotter:

    @staticmethod
    def plot_ticks(ticks, title):
        if ticks is None or len(ticks) == 0:
            print("No data to plot.")
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
            print("No data to plot.")
            return
        rates_frame = pd.DataFrame(rates)
        rates_frame['time'] = pd.to_datetime(rates_frame['time'], unit='s')
        plt.plot(rates_frame['time'], rates_frame['close'], label='close')
        plt.title(title)
        plt.legend()
        plt.show()

    @staticmethod
    def plot_charts(rates, entries, fast_period, slow_period):
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
        buy_signals = rates.loc[rates['Crossover'] == 2]
        sell_signals = rates.loc[rates['Crossover'] == -2]

        plt.plot(buy_signals.index, buy_signals['Fast_MA'], '^',
                 color='green', markersize=12, label="Buy Signal")
        plt.plot(sell_signals.index, sell_signals['Fast_MA'],
                 'v', color='red', markersize=12, label="Sell Signal")

        # Plot SL/TP levels
        for i in buy_signals.index:
            plt.hlines(rates.loc[i, 'StopLoss'], i, i + 5, colors='red',
                       linestyles='dashed', label="SL" if i == buy_signals.index[0] else "")
            plt.hlines(rates.loc[i, 'TakeProfit'], i, i + 5, colors='green',
                       linestyles='dashed', label="TP" if i == buy_signals.index[0] else "")

        for i in sell_signals.index:
            plt.hlines(rates.loc[i, 'StopLoss'], i, i + 5,
                       colors='red', linestyles='dashed')
            plt.hlines(rates.loc[i, 'TakeProfit'], i, i + 5,
                       colors='green', linestyles='dashed')

        plt.title('Moving Average Crossover Signals with SL/TP')
        plt.legend(loc='upper left')
        plt.show()
