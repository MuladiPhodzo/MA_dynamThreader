import os
import sys
import logging
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

import advisor.Client.mt5Client as mt5Client

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
class MovingAverageCrossover:

    def __init__(self,
                 symbol,
                 data: dict,
                 fast_period=50,
                 slow_period=150):
        """
        Initialize the strategy with data and parameters.

        :param data: DataFrame containing historical data (must include 'close').
        :param fast_period: Period for the fast-moving average.
        :param slow_period: Period for the slow-moving average.
        """
        self.symbol = symbol
        self.data = data
        self.fast_period = fast_period
        self.slow_period = slow_period

        self.signals = {}
        self.results = {}
        self.data_handler = mt5Client.dataHandler()  # Reuse existing CSV saving method

    # ------------------------------------------------------
    # 1) Moving Averages per timeframe
    # ------------------------------------------------------
    def calculate_moving_averages(self):
        """
        Calculates Fast_MA, Slow_MA, Signal, Crossover, Bias
        for EACH timeframe DataFrame inside a dictionary.
        """

        for tf, df in self.data.items():
            # Skip the Main Trend key
            if df is None:
                logger.warning(f'{self.symbol} {tf} is None: {df}')
                continue

            if tf == "Main_Trend":
                continue

            if 'close' not in df.columns:
                raise ValueError(f"{tf} data is missing a 'close' column.")

            df['Fast_MA'] = df['close'].rolling(
                window=self.fast_period).mean().shift(1)
            df['Slow_MA'] = df['close'].rolling(
                window=self.slow_period).mean().shift(1)

            df['Signal'] = np.where(
                df['Fast_MA'] > df['Slow_MA'], 1,
                np.where(df['Fast_MA'] < df['Slow_MA'], -1, 0)
            )

            df['Crossover'] = df['Signal'].diff()

            df['Bias'] = np.where(
                df['Fast_MA'] > df['Slow_MA'], "Bullish", "Bearish"
            )

            # Clean NaN rows
            self.data[tf] = df.dropna()
        self.identify_Main_Trend_Alignment()

        logger.info(f"📊 MA indicators calculated for all timeframes ({self.symbol})")
        return self.data

    # ------------------------------------------------------
    # 2) Proximity Entry Signals (per timeframe)
    # ------------------------------------------------------
    def identify_proximity_entries(self, pip_distance=250):
        """
        Identifies BUY & SELL proximity entries for 15min & 30min timeframes.
        """
        for tf, df in self.data.items():
            # Skip the Main Trend key
            if df is None:
                logger.warning(f'{self.symbol} {tf}: data is none == {df}')
                continue

            if tf == "Main_Trend" or tf == "D1":
                continue
            copyDF = df.copy()
            pip_size = self.get_pip_size(df['close'].iloc[-1])
            threshold = pip_distance * pip_size

            copyDF.loc[:, 'Proximity'] = abs(copyDF['close'] - copyDF['Slow_MA']) <= threshold
            if "M" in tf:
                copyDF['Entry'] = None
                copyDF['SL'] = np.nan
                copyDF['TP'] = np.nan
                for i, row in copyDF.iterrows():
                    entry_price = row['close']
                    if not row['Proximity']:
                        continue
                    # BUY
                    if self.data["Main_Trend"] == "Bullish":
                        if row['Fast_MA'] > row['Slow_MA']:
                            df.loc[i, 'Entry'] = "Buy"
                            df.loc[i, 'SL'] = entry_price - (pip_distance * pip_size)
                            df.loc[i, 'TP'] = entry_price + (2 * pip_distance * pip_size)
                    # SELL
                    elif self.data["Main_Trend"] == "Bearish":
                        if row['Fast_MA'] < row['Slow_MA']:
                            df.loc[i, 'Entry'] = "Sell"
                            df.loc[i, 'SL'] = entry_price + (pip_distance * pip_size)
                            df.loc[i, 'TP'] = entry_price - (2 * pip_distance * pip_size)
                    else:
                        continue
                df = copyDF

            self.data[tf] = df
        self.sequence_data()

        logger.info(f"🎯 Proximity entries generated for all timeframes ({self.symbol})")
        return self.data

    def _evaluate_trade_outcome(self, df, entry_pos, entry_type, entry_price, sl, tp, pip_size):
        """
        Scans future candles from entry_pos+1 onward.
        Returns (Outcome, ExitPrice, ExitIndex)
        """

        future = df.iloc[entry_pos + 1:]

        for pos, (idx, row) in enumerate(future.iterrows(), start=entry_pos + 1):

            price_high = row["high"]
            price_low = row["low"]

            # BUY: price must hit TP or SL
            if entry_type == "Buy":

                if price_low <= sl:
                    return "SL-Hit", sl, pos

                if price_high >= tp:
                    return "TP-Hit", tp, pos

            # SELL: reversed SL/TP checks
            else:

                if price_high >= sl:
                    return "SL-Hit", sl, pos

                if price_low <= tp:
                    return "TP-Hit", tp, pos

        # If never hit SL/TP — optional behavior
        return "NoHit", entry_price, None

    # ------------------------------------------------------
    # 3) Backtesting per timeframe
    # ------------------------------------------------------
    def backtest_entries(self, pip_distance=100, tp_factor=2):
        """
        Backtest entries for each timeframe and update the
        original DataFrame with trade results.

        Adds the following columns to each TF DataFrame:
            - ExitPrice
            - ExitIndex
            - Outcome
            - PnL_Pips
        """

        all_results = {}

        for tf, df in self.data.items():
            # Skip invalid items
            if df is None:
                logger.warning(f'{self.symbol} {tf} is None: {df}')
                continue

            if tf == "Main_Trend" or not isinstance(df, pd.DataFrame):
                continue

            # Skip TFs without Entry column (H1, H4, etc.)
            if "Entry" not in df.columns:
                # logger.info(f"[{tf}] Skipped — no Entry column.")
                continue

            # Defensive copy
            df = df.copy()

            # Add result columns if missing
            for col in ["ExitPrice", "ExitIndex", "Outcome", "PnL_Pips"]:
                if col not in df.columns:
                    df[col] = None

            pip_size = self.get_pip_size(df["close"].iloc[-1])
            trades = []

            # Iterate with positional index
            for pos, (idx, row) in enumerate(df.iterrows()):

                entry_type = row["Entry"]
                if entry_type not in ("Buy", "Sell"):
                    continue

                entry_price = row["close"]
                sl = row["SL"]
                tp = row["TP"]

                if pd.isna(sl) or pd.isna(tp):
                    continue

                # Evaluate the trade forward
                outcome, exit_price, exit_index = self._evaluate_trade_outcome(
                    df=df,
                    entry_pos=pos,
                    entry_type=entry_type,
                    entry_price=entry_price,
                    sl=sl,
                    tp=tp,
                    pip_size=pip_size
                )

                # PnL (Buy vs Sell)
                if exit_price is not None:
                    pnl_pips = (
                        (exit_price - entry_price) / pip_size
                        if entry_type == "Buy"
                        else (entry_price - exit_price) / pip_size
                    )
                else:
                    pnl_pips = 0

                # --- Write results directly into TF dataframe ---
                df.loc[idx, "ExitPrice"] = exit_price
                df.loc[idx, "ExitIndex"] = exit_index
                df.loc[idx, "Outcome"] = outcome
                df.loc[idx, "PnL_Pips"] = pnl_pips

                # Optional summary output
                trades.append({
                    "TF": tf,
                    "Index": idx,
                    "Type": entry_type,
                    "EntryPrice": entry_price,
                    "SL": sl,
                    "TP": tp,
                    "ExitPrice": exit_price,
                    "Outcome": outcome,
                    "ExitIndex": exit_index,
                    "PnL_Pips": pnl_pips,
                })

            # Save updated DataFrame back to storage
            self.data[tf] = df

            # Also save summary results
            all_results[tf] = pd.DataFrame(trades)
            logger.info(f"[{self.symbol}][{tf}] Backtest complete — {len(all_results[tf])} trades updated.")

        self.results = all_results
        logger.info(f"📊 [{self.symbol}] Backtests updated in DF for all timeframes ({self.symbol})")
        return self.results

    def _clean_value(self, v):
        """Convert numpy types or pandas types to native Python."""
        if hasattr(v, "item"):
            return v.item()
        return float(v) if isinstance(v, (np.floating, np.float64)) else v

    def generate_backtest_summary(self):
        """
        Generate a clean summary report for all timeframes.
        Converts numpy types to built-in Python values.
        Includes:
            - daily & weekly trade averages
            - days covered by each timeframe
        """
        if not hasattr(self, "results") or not self.results:
            logger.error("Backtest summary requested but no results found.")
            return {}

        summary = {}
        all_pips = []

        for tf, df in self.results.items():
            if df.empty:
                continue

            # Ensure numeric PnL
            df["PnL_Pips"] = df["PnL_Pips"].astype(float)

            # --- BASIC METRICS ---
            wins = df[df["PnL_Pips"] > 0]
            losses = df[df["PnL_Pips"] < 0]

            total_trades = len(df)
            win_count = len(wins)
            loss_count = len(losses)
            win_rate = (win_count / total_trades * 100) if total_trades else 0

            avg_win = wins["PnL_Pips"].mean() if not wins.empty else 0
            avg_loss = losses["PnL_Pips"].mean() if not losses.empty else 0
            self.data_handler.toCSVFile(df, f"src/main/python/Advisor/Logs/{self.symbol}_data/{tf}_backtest.csv")
            rr_ratio = abs(avg_win / avg_loss) if avg_loss else float("inf")

            expectancy = (
                (win_rate / 100) * avg_win
                - ((1 - win_rate / 100) * abs(avg_loss))
            ) if total_trades else 0

            # --- WIN/LOSS STREAKS ---
            outcomes = df["Outcome"].tolist()
            max_win_streak = max_loss_streak = 0
            temp_win = temp_loss = 0

            for o in outcomes:
                if o == "TP-Hit":
                    temp_win += 1
                    temp_loss = 0
                elif o == "SL-Hit":
                    temp_loss += 1
                    temp_win = 0

                max_win_streak = max(max_win_streak, temp_win)
                max_loss_streak = max(max_loss_streak, temp_loss)

            # ---------------------------------------
            # 📅 DATE RANGE & TRADE FREQUENCY METRICS
            # ---------------------------------------
            if isinstance(df.index, pd.DatetimeIndex):
                min_date = df.index.min()
                max_date = df.index.max()
                days_covered = (max_date - min_date).days + 1
            else:
                min_date = max_date = None
                days_covered = None

            if days_covered and days_covered > 0:
                avg_trades_daily = total_trades / days_covered
                avg_trades_weekly = avg_trades_daily * 7
            else:
                avg_trades_daily = None
                avg_trades_weekly = None

            # --- NET PIPS + EQUITY CURVE ---
            net_pips = df["PnL_Pips"].sum()
            df["Equity"] = df["PnL_Pips"].cumsum()

            # Store
            summary[tf] = {
                "total_trades": total_trades,
                "wins": win_count,
                "losses": loss_count,
                "win_rate": self._clean_value(win_rate),
                "avg_win_pips": self._clean_value(avg_win),
                "avg_loss_pips": self._clean_value(avg_loss),
                "rr_ratio": self._clean_value(rr_ratio),
                "expectancy": self._clean_value(expectancy),
                "max_win_streak": max_win_streak,
                "max_loss_streak": max_loss_streak,
                "biggest_win": self._clean_value(df["PnL_Pips"].max()),
                "biggest_loss": self._clean_value(df["PnL_Pips"].min()),
                "net_pips": self._clean_value(net_pips),
                "days_covered": days_covered,
                "avg_trades_daily": self._clean_value(avg_trades_daily),
                "avg_trades_weekly": self._clean_value(avg_trades_weekly),
            }

            all_pips.extend(df["PnL_Pips"].tolist())

        # -------------------------------
        # 📌 GLOBAL COMBINED SUMMARY
        # -------------------------------
        if all_pips:
            all_pips = pd.Series(all_pips)

            summary["combined"] = {
                "total_trades": len(all_pips),
                "win_rate": self._clean_value(100 * (all_pips > 0).sum() / len(all_pips)),
                "net_pips": self._clean_value(all_pips.sum()),
                "avg_win": self._clean_value(all_pips[all_pips > 0].mean()),
                "avg_loss": self._clean_value(all_pips[all_pips < 0].mean()),
                "expectancy": self._clean_value(
                    (all_pips > 0).mean() * all_pips[all_pips > 0].mean()
                    - (all_pips < 0).mean() * abs(all_pips[all_pips < 0].mean())
                ),
                "max_pips": self._clean_value(all_pips.max()),
                "min_pips": self._clean_value(all_pips.min()),
            }

        logger.info("📊 Clean backtest summary generated.")
        return summary

    def identify_Main_Trend_Alignment(self):
        try:
            count = 0  # count should equal 8 for successful trend alignnment
            for tf, df in self.data.items():
                current = df.iloc[-1]
                if current["Slow_MA"] > current["Fast_MA"]:
                    count -= 1
                else:
                    count += 1
            if abs(count) == len(self.data):
                if count > 0:
                    self.data["Main_Trend"] = "Bullish"
                else:
                    self.data["Main_Trend"] = "Bearish"
            else:
                self.data["Main_Trend"] = "No Trend Alignment"
            count = 0
        except Exception as e:
            logger.exception(f"exception in Trend alignment: {e}")
            self.data['Main_Trend'] = ""

    def sequence_trend_alignmemt(self, mtf_row_data: dict) -> str:
        try:
            count = 0
            ls_len = len(mtf_row_data)

            prox_meter = [bool]
            for tf, df in mtf_row_data.items():
                prox_meter.append(df["Proximity"])
                if df["Slow_MA"] > df["Fast_MA"]:
                    count -= 1
                else:
                    count += 1

            if count > 0:
                if count in range(ls_len - 2, ls_len) and prox_meter.count(True) >= 6:
                    return "(S)Bullish"
                elif count == 5 and prox_meter.count(True) == 5:
                    return "Bullish"
                elif count in range(ls_len / 2) and prox_meter.count(True) <= 4:
                    return "(W)Bullish"
            else:
                if (abs(count) in range(ls_len - 2, ls_len)) and (prox_meter.count(True) >= 6):
                    return "(S)Bearish"
                elif (abs(count) == 5) and (prox_meter.count(True) == 5):
                    return "Bearish"
                elif (abs(count) in range(ls_len / 2)) and (prox_meter.count(True) <= 4):
                    return "(W)Bearish"
        except Exception as e:
            logger.exception(f"exception in trend alignment: {e}")

    def sequence_data(self):
        try:
            mtf_row_data = {}
            index = 0
            Main_Trend = ""
            for tf, df in self.data.items():
                for pos, (idx, row) in enumerate(df.iterrows()):
                    current = row.iloc[index, "Slow_MA"][index, "Fast_MA"][index, "Proximity"]
                    mtf_row_data[tf] = current

                    index = idx
                Main_Trend = self.sequence_trend_alignmemt(mtf_row_data)
                if tf in ['15M', '30M']:
                    df.loc[index, 'Main_Trend'] = Main_Trend
        except Exception as e:
            logger.exception(f"exception in sequence data: {e}")

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
                logger.info(
                    f"New file created and entry levels saved to {file_name}.")
            else:
                data.to_csv(file_name, index=False, mode='a', header=False)
                logger.info(f"Entry levels appended to existing file {file_name}.")
        else:
            logger.info("No signals to save. Please run 'identify_entry_levels()' first.")

    def backtest_strategy(self):
        """Backtest the strategy by calculating strategy returns."""
        for tf, df in self.data.items():
            if "M" in tf:
                df['Position'] = df['Entry'].shift(1)  # Avoid lookahead bias
                df['Market_Returns'] = df['close'].pct_change()
                df['Strategy_Returns'] = df['Market_Returns'] * \
                    df['Position']
                df['Cumulative_Market_Returns'] = (1 + df['Market_Returns']).cumprod()
                df['Cumulative_Strategy_Returns'] = (1 + df['Strategy_Returns']).cumprod()
                self.results = df.dropna().copy()
        logger.info("Backtest completed.")
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
        self.identify_proximity_entries()
        self.sequence_data()
        self.backtest_entries()
        summary = self.generate_backtest_summary()

        logger.info(f"{self.symbol} strategy performance:\n{summary}")
