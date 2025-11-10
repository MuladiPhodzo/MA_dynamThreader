import pandas as pd
import matplotlib.pyplot as plt
import os
import numpy as np


class MovingAverageCrossover:

    def __init__(self,
                 symbol,
                 data: pd.DataFrame,
                 fast_period=50,
                 slow_period=150):
        """
        Initialize the strategy with data and parameters.

        :param data: DataFrame containing historical data (must include 'close').
        :param fast_period: Period for the fast-moving average.
        :param slow_period: Period for the slow-moving average.
        """
        self.entries = None
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.signals = None
        self.results = None
        self.symbol = symbol

    def calculate_moving_averages(self, data):
        """Calculate the fast and slow moving averages."""
        if 'close' not in data.columns:
            raise ValueError("'close' column is missing in the data.")

        data['Fast_MA'] = data['close'].rolling(
            window=self.fast_period).mean().shift()
        data['Slow_MA'] = data['close'].rolling(
            window=self.slow_period).mean().shift()
        data['Signal'] = np.where(data['Fast_MA'] > data['Slow_MA'], 1,
                                  np.where(data['Fast_MA'] < data['Slow_MA'], -1, 0))

        data['Crossover'] = data['Signal'].diff()
        data['Bias'] = np.where(
            data['Fast_MA'] > data['Slow_MA'], "Bullish", "Bearish")

        self.data = data.dropna()
        print(f'MA Data Available for {self.symbol}')
        return self.data

    def identify_proximity_entries(self, data: pd.DataFrame, pip_distance: int = 100):
        """
        Identify entries when price is within X pips of the Slow MA,
        and classify them as Buy or Sell depending on MA alignment.
        Also auto-assign SL and TP levels.

        Args:
            data (pd.DataFrame):
                DataFrame containing at least
                'close', 'Fast_MA', and 'Slow_MA'.
            pip_distance (int): Number of pips to define proximity (default=100 pips).

        Returns:
            pd.DataFrame: Updated DataFrame with new columns: 'EntrySignal', 'SL', 'TP'.
        """
        required_cols = ['close', 'Fast_MA', 'Slow_MA']
        for col in required_cols:
            if col not in data.columns:
                raise ValueError(f"Data must contain '{col}' column.")

        # Determine pip size automatically from last price
        pip_size = self.get_pip_size(data['close'].iloc[-1])
        threshold = pip_distance * pip_size

        # Proximity condition: price near Slow MA
        data['Proximity'] = abs(data['close'] - data['Slow_MA']) <= threshold

        # Initialize new columns
        data['Entry'] = None
        data['SL'] = np.nan
        data['TP'] = np.nan

        signals = []
        for i, row in data.iterrows():
            if row['Proximity']:
                entry_price = row['close']

                if row['Fast_MA'] > row['Slow_MA']:
                    data.at[i, 'Entry'] = "Buy"
                    data.at[i, 'SL'] = entry_price - (pip_distance * pip_size)
                    data.at[i, 'TP'] = entry_price + \
                        (2 * pip_distance * pip_size)

                    risk = entry_price - data.at[i, 'SL']
                    reward = data.at[i, 'TP'] - entry_price
                    rrr = reward / risk if risk != 0 else None
                    signals.append(
                        (i, "Buy", entry_price, data.at[i, 'SL'], data.at[i, 'TP'], rrr))

                elif row['Fast_MA'] < row['Slow_MA']:
                    data.at[i, 'Entry'] = "Sell"
                    data.at[i, 'SL'] = entry_price + (pip_distance * pip_size)
                    data.at[i, 'TP'] = entry_price - \
                        (2 * pip_distance * pip_size)

                    risk = data.at[i, 'SL'] - entry_price
                    reward = entry_price - data.at[i, 'TP']
                    rrr = reward / risk if risk != 0 else None

                    signals.append(
                        (i, "Sell", entry_price, data.at[i, 'SL'], data.at[i, 'TP'], rrr))
                else:
                    data.at[i, 'Entry'] = None

        print(
            f"✅ Proximity entries identified for {self.symbol} within {pip_distance} pips of Slow MA, with SL/TP set.")
        return data

    def backtest_entries(self, data: pd.DataFrame, pip_distance: int = 100, tp_factor: int = 2):
        """
        Backtest moving average entry signals using historical data.

        Args:
            data (pd.DataFrame): Price and MA data
            pip_distance (int): Stop distance in pips (default 100)
            tp_factor (int): Multiplier for TP vs SL (default 2:1 RR)

        Returns:
            pd.DataFrame with trade results
        """
        if not {'close', 'Fast_MA', 'Slow_MA'}.issubset(data.columns):
            raise ValueError(
                "Data must contain 'close', 'Fast_MA', and 'Slow_MA' columns.")

        last_price = data['close'].iloc[-1]
        pip_size = self.get_pip_size(last_price)
        threshold = pip_distance * pip_size

        trades = []

        for i in range(len(data)):
            price = data['close'].iloc[i]

            # Proximity filter
            if abs(price - data['Fast_MA'].iloc[i]) <= threshold:
                if data['Fast_MA'].iloc[i] > data['Slow_MA'].iloc[i]:  # Buy setup
                    entry_type = "Buy"
                    entry_price = price
                    sl = entry_price - pip_distance * pip_size
                    tp = entry_price + (pip_distance * tp_factor * pip_size)
                elif data['Fast_MA'].iloc[i] < data['Slow_MA'].iloc[i]:  # Sell setup
                    entry_type = "Sell"
                    entry_price = price
                    sl = entry_price + pip_distance * pip_size
                    tp = entry_price - (pip_distance * tp_factor * pip_size)
                else:
                    continue

                # Walk forward to see outcome
                outcome = "Open"
                exit_price = None
                for j in range(i + 1, len(data)):
                    future_price = data['close'].iloc[j]

                    if entry_type == "Buy":
                        if future_price <= sl:
                            outcome = "Loss"
                            exit_price = sl
                            break
                        elif future_price >= tp:
                            outcome = "Win"
                            exit_price = tp
                            break
                    elif entry_type == "Sell":
                        if future_price >= sl:
                            outcome = "Loss"
                            exit_price = sl
                            actual_Loss = exit_price - entry_price
                            break
                        elif future_price <= tp:
                            outcome = "Win"
                            exit_price = tp
                            break

                trades.append({
                    "Index": data.index[i],
                    "Type": entry_type,
                    "EntryPrice": entry_price,
                    "SL": sl,
                    "TP": tp,
                    "ExitPrice": exit_price,
                    "Outcome": outcome,
                    "Risk": abs(entry_price - sl),
                    "Reward": abs(tp - entry_price),
                    "RRR": abs(tp - entry_price) / abs(entry_price - sl) if sl != entry_price else None,
                    # ✅ Profit / Loss in pips


                    "PnL_Pips": (
                        (exit_price - entry_price) / pip_size if entry_type == "Buy" else
                        (entry_price - exit_price) / pip_size
                    ) if exit_price else 0,
                    # ✅ Profit / Loss in % (relative to entry)
                    "PnL_%": (
                        ((exit_price - entry_price) / entry_price) * 100 if entry_type == "Buy" else
                        ((entry_price - exit_price) / entry_price) * 100
                    ) if exit_price else 0,
                    "actual_loss": actual_Loss if 'actual_Loss' in locals() else None
                })

        self.results = pd.DataFrame(trades)
        print(
            f"Backtest completed for {self.symbol}. Total trades: {len(self.results)}")

    def get_pip_size(self, price):
        """Auto-detect pip size from number of decimals in price."""
        price_str = str(price)
        if "." in price_str:
            decimals = len(price_str.split(".")[1])
            # 2 or 3 decimals → JPY pairs (0.01, 0.001)
            # 4 or 5 decimals → normal pairs (0.0001, 0.00001)
            if decimals in [2, 3]:
                return 0.01
            elif decimals in [4, 5]:
                return 0.0001
        return 0.0001  # fallback

    def save_signals_to_csv(self, data, file_name="src/main/python/advisor/Logs/Rates"):
        """
        Save identified entry levels to a CSV file.
        - Creates the file if it doesn't exist.
        - Appends to the file if it already exists.
        """

        if data is not None:
            file_exists = os.path.isfile(file_name)
            if not file_exists:
                data.to_csv(file_name, index=False, mode='w')
                print(
                    f"New file created and entry levels saved to {file_name}.")
            else:
                data.to_csv(file_name, index=False, mode='a', header=False)
                print(f"Entry levels appended to existing file {file_name}.")
        else:
            print("No signals to save. Please run 'identify_entry_levels()' first.")

    def backtest_strategy(self):
        """Backtest the strategy by calculating strategy returns."""

        self.data['Position'] = self.data['Entry'].shift(
            1)  # Avoid lookahead bias
        self.data['Market_Returns'] = self.data['close'].pct_change()
        self.data['Strategy_Returns'] = self.data['Market_Returns'] * \
            self.data['Position']
        self.data['Cumulative_Market_Returns'] = (
            1 + self.data['Market_Returns']).cumprod()
        self.data['Cumulative_Strategy_Returns'] = (
            1 + self.data['Strategy_Returns']).cumprod()
        self.results = self.data.dropna().copy()
        print("Backtest completed.")
        return self.results

    def plot_performance(self):
        """Visualize the strategy performance against market performance."""
        if self.results is None:
            raise ValueError(
                "No results available. Run backtest_strategy() first.")

        plt.figure(figsize=(12, 6))
        plt.plot(self.results.index,
                 self.results['Cumulative_Market_Returns'], label='Market Returns', color='blue')
        plt.plot(self.results.index,
                 self.results['Cumulative_Strategy_Returns'], label='Strategy Returns', color='green')
        plt.title('Moving Average Crossover Strategy Performance')
        plt.legend()
        plt.show()

    def plot_charts(self, ltf_data):
        if 'Entry' not in ltf_data.columns:
            raise ValueError(
                "Error: 'Entry' column is missing in `ltf_data`. Check data processing.")
        if 'close' not in ltf_data.columns or ltf_data['close'].empty:
            raise ValueError("No Close data available")

        # Ensure both datasets use datetime index for consistency
        if not isinstance(self.data.index, pd.DatetimeIndex):
            self.data.index = pd.to_datetime(self.data.index)

        ax = plt.subplots(figsize=(18, 6))

        # Plot market data (uses self.data.index)
        ax.plot(ltf_data.index, ltf_data['close'],
                label="Close", color='black')
        ax.plot(ltf_data.index, ltf_data['Fast_MA'],
                label=f"Fast MA ({self.fast_period})", color='blue')
        ax.plot(ltf_data.index, ltf_data['Slow_MA'],
                label=f"Slow MA ({self.slow_period})", color='red')

        ax.fill_between(ltf_data.index, ltf_data['Fast_MA'], ltf_data['Slow_MA'], where=(
            ltf_data['Fast_MA'] > ltf_data['Slow_MA']), color='green', alpha=0.3, label='Bullish Zone')
        ax.fill_between(ltf_data.index, ltf_data['Fast_MA'], ltf_data['Slow_MA'], where=(
            ltf_data['Fast_MA'] < ltf_data['Slow_MA']), color='red', alpha=0.3, label='Bearish Zone')
        ax.fill_between(ltf_data.index, ltf_data['Fast_MA'], ltf_data['close'], where=(
            ltf_data['Fast_MA'] - ltf_data['close'] <= 0.005), color='orange', alpha=0.3, label='Range')

        # Plot Buy signals
        # buy_signals = ltf_data[ltf_data['Entry'] == 'Buy']
        # Plot entries and SL/TP
        for i, row in ltf_data.iterrows():
            if row['Entry'] == "Buy":
                plt.scatter(row['time'], row['close'], marker='^',
                            color='green', label='Buy' if i == 0 else "")
                plt.hlines(y=row['SL'], xmin=row['time'] - pd.Timedelta(minutes=1), xmax=row['time'] + pd.Timedelta(minutes=1),
                           color='red', linestyles='--', label='SL' if i == 0 else "")
                plt.hlines(y=row['TP'], xmin=row['time'] - pd.Timedelta(minutes=1), xmax=row['time'] + pd.Timedelta(minutes=1),
                           color='green', linestyles='--', label='TP' if i == 0 else "")
            elif row['Entry'] == "Sell":
                plt.scatter(row['time'], row['close'], marker='v',
                            color='red', label='Sell' if i == 0 else "")
                plt.hlines(y=row['SL'], xmin=row['time'] - pd.Timedelta(minutes=1), xmax=row['time'] + pd.Timedelta(minutes=1),
                           color='red', linestyles='--', label='SL' if i == 0 else "")
                plt.hlines(y=row['TP'], xmin=row['time'] - pd.Timedelta(minutes=1), xmax=row['time'] + pd.Timedelta(minutes=1),
                           color='green', linestyles='--', label='TP' if i == 0 else "")

        plt.title(f"{self.symbol} - Moving Average Proximity Entries")
        plt.xlabel("Time")
        plt.ylabel("Price")
        plt.legend()
        plt.grid(True)
        plt.tight_layout()
        plt.show()

    def run_moving_average_strategy(self, symbol, data):
        """
        Run the Moving Average strategy using historical data.
        Detect proximity entries and plot them with SL/TP levels.

        Args:
            symbol (str): Symbol to run strategy on.
            data (dict): Dictionary with 'HTF' and 'LTF' DataFrames.
        """
        if data is None or 'LTF' not in data or 'HTF' not in data:
            print(f"Failed to retrieve data for {symbol}.")
            return None
        dataCopy = data.copy()
        ltf_data = dataCopy['LTF']
        htf_data = dataCopy['HTF']

        # Step 1: Calculate MAs if not already calculated
        if 'Fast_MA' not in ltf_data.columns or 'Slow_MA' not in ltf_data.columns:
            self.calculate_moving_averages(ltf_data)
        if 'Fast_MA' not in htf_data.columns or 'Slow_MA' not in htf_data.columns:
            self.calculate_moving_averages(htf_data)

        # Step 2: Identify historical proximity entries
        ltf_data = self.identify_proximity_entries(ltf_data, pip_distance=100)

        # Optional: save signals to CSV
        self.save_signals_to_csv(
            ltf_data, file_name=f"src/main/python/Advisor/Logs/{symbol}_entry_levels.csv")

        self.plot_charts()
