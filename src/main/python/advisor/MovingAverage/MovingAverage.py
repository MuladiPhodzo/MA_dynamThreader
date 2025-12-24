import sys
import logging
import numpy as np
import pandas as pd
import tabulate
import MetaTrader5 as mt5

import advisor.utils.dataHandler as utils
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
class MovingAverageCrossover:

    def __init__(self,
                 symbol,
                 caching: CacheManager = None,
                 data_handler: utils.dataHandler = None,
                 fast_period=50,
                 slow_period=150,
                 pip_distance=250):
        """
        Initialize the strategy with data and parameters.

        :param data: DataFrame containing historical data (must include 'close').
        :param fast_period: Period for the fast-moving average.
        :param slow_period: Period for the slow-moving average.
        """
        self.symbol = symbol
        self.cache = caching
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.pip_distance = pip_distance

        self.signals = {}
        self.results = {}
        self.data_handler = data_handler

        try:
            self.pip_size = self.get_pip_size(self.data_handler.get('30M'))
        except Exception:
            self.pip_size = 0.0001

            # decide threshold scaling: keep configurable via pip_distance
            self.threshold = pip_distance * self.pip_size

    # ------------------------------------------------------
    # Helper Methods
    # ------------------------------------------------------
    def _clean_value(self, v):
        """Convert numpy types or pandas types to native Python."""
        if hasattr(v, "item"):
            return v.item()
        return float(v) if isinstance(v, (np.floating, np.float64)) else v

    def _classify_bullish_trend(self, count: int, ls_len: int, prox_true: int) -> str:
        """Classify bullish trend strength."""
        if count >= (ls_len - 2) and prox_true >= max(3, ls_len - 1):
            return "(S)Bullish"
        if count >= 5 and prox_true >= 5:
            return "Bullish"
        return "(W)Bullish"

    def _classify_bearish_trend(self, count: int, ls_len: int, prox_true: int) -> str:
        """Classify bearish trend strength."""
        if abs(count) >= (ls_len - 2) and prox_true >= max(3, ls_len - 1):
            return "(S)Bearish"
        if abs(count) >= 5 and prox_true >= 5:
            return "Bearish"
        return "(W)Bearish"

    def sequence_trend_alignment(self, mtf_row_data: dict) -> str:
        """
        Evaluate multi-timeframe trend alignment.
        mtf_row_data structure:
        {
            "1H": {"Slow_MA": float, "Fast_MA": float, "Proximity": bool},
            "30M": {...},
            ...
        }
        """

        try:
            if not mtf_row_data:
                return "Neutral"

            ls_len = len(mtf_row_data)
            prox_meter = []
            count = 0

            for tf, row in mtf_row_data.items():
                # Collect proximity boolean
                prox_meter.append(bool(row["Proximity"]))

                # Count trend direction: +1 = bullish, -1 = bearish
                if row["Slow_MA"] > row["Fast_MA"]:
                    count -= 1
                else:
                    count += 1

            prox_true = prox_meter.count(True)
            majority = ls_len // 2 + 1

            if count >= majority:
                return self._classify_bullish_trend(count, ls_len, prox_true)
            elif count <= -majority:
                return self._classify_bearish_trend(count, ls_len, prox_true)

            return "Neutral"

        except Exception as e:
            logger.exception(f"exception in trend alignment: {e}")
            return "Error"

    def _build_all_timestamps(self):
        """Build a unified set of timestamps from all TF data."""
        all_timestamps = set()
        for tf, df in self.data.items():
            if isinstance(df, pd.DataFrame) and "Slow_MA" in df.columns:
                all_timestamps.update(df.index)
        return sorted(all_timestamps) if all_timestamps else None

    def _build_mtf_row_data(self, ts):
        """Build multi-timeframe snapshot for a given timestamp."""
        mtf_row_data = {}
        for tf, df in self.data_handler.data.items():
            if tf == "Main_Trend" or df is None or not isinstance(df, pd.DataFrame):
                continue
            if ts not in df.index:
                continue

            row = df.loc[ts]
            if not all(col in row for col in ["Slow_MA", "Fast_MA", "Proximity"]):
                continue

            mtf_row_data[tf] = {
                "timestamp": ts,
                "Slow_MA": row["Slow_MA"],
                "Fast_MA": row["Fast_MA"],
                "Proximity": row["Proximity"],
            }
        return mtf_row_data

    def _write_main_trend_to_ltf(self, ts, main_trend):
        """Write Main_Trend back into required LTF rows that match timestamp."""
        for tf in ["15M", "30M"]:
            if tf in self.data and ts in self.data[tf].index:
                self.data[tf].loc[ts, "Bias"] = main_trend

    def get_pip_size(self, df: pd.DataFrame):
        """Auto-detect pip size from number of decimals in price."""
        price = float(df["close"].dropna().iloc[-1])
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

    def sequence_Trend_Data(self, all_timestamps):
        """
        Timestamp-aligned multi-timeframe trend evaluation.

        For every unique timestamp across all timeframes:
            - Build snapshot of (Slow_MA, Fast_MA, Proximity)
            - Compute MTF Main_Trend
            - Insert Main_Trend into 15M & 30M rows that match that timestamp
        """

        try:

            if not self.data_handler.all_timestamps:
                logger.warning(f"{self.symbol}: No timestamps found for MTF alignment.")
                return

            for ts in self.data_handler.all_timestamps:
                mtf_row_data = self._build_mtf_row_data(ts)

                if len(mtf_row_data) == 0:
                    continue

                main_trend = self.sequence_trend_alignment(mtf_row_data)
                self._write_main_trend_to_ltf(ts, main_trend)

            logger.info(f"✔ Timestamp-aligned MTF Main_Trend computed for {self.symbol}")

        except Exception as e:
            logger.exception(f"Exception in timestamp-based sequence_data: {e}")
    # ------------------------------------------------------
    # Main Methods
    # ------------------------------------------------------

    # ------------------------------------------------------
    # 1) Moving Average Value (per timeframe)
    # ------------------------------------------------------
    def calculate_moving_averages_data(self, tf):
        """
        Calculate Fast/Slow MA and derived columns per timeframe.
        - Validates 'close' column
        - Ensures datetime index
        - Keeps frames even if some rows are NaN (avoid aggressive dropna)
        """
        df = self.data_handler.get(tf)
        # Skip non-dataframes & placeholder keys
        if df is None or not isinstance(df, pd.DataFrame):
            return False

        if 'close' not in df.columns:
            logger.warning(f"{self.symbol} {tf} missing 'close' column — skipping TF.")
            return False

        # Ensure datetime index if 'time' exists
        if 'time' in df.columns and not isinstance(df.index, pd.DatetimeIndex):
            try:
                df = df.copy()
                df['time'] = pd.to_datetime(df['time'], unit='s', errors='coerce')
                df.set_index('time', inplace=True)
            except Exception:
                # leave index as-is if conversion fails
                pass

        # Compute MAs — shift by 1 to avoid lookahead
        df = df.copy()
        try:
            # moving average values
            df.loc[:, 'Fast_MA'] = df['close'].rolling(window=self.fast_period, min_periods=1).mean().shift(1)
            df.loc[:, 'Slow_MA'] = df['close'].rolling(window=self.slow_period, min_periods=1).mean().shift(1)
            # Signal values
            df.loc[:, 'Signal'] = np.where(df['Fast_MA'] > df['Slow_MA'], 1, np.where(df['Fast_MA'] < df['Slow_MA'], -1, 0))
            df.loc[:, 'Crossover'] = df['Signal'].diff()
            df.loc[:, 'Bias'] = np.where(df['Fast_MA'] > df['Slow_MA'], "Bullish", "Bearish")
            # Vectorized proximity calculation and safe fill
            df.loc[:, 'Proximity'] = (df['close'] - df['Slow_MA']).abs() <= self.threshold

        except Exception as e:
            logger.exception(f"exception in proximity calculation for {self.symbol} {tf}: {e}")
        # Keep the frame but mark rows where MA values are missing
        if self.verify_fields():
            return True, df
        return False

    # ------------------------------------------------------
    # 2) Check Proximity (per timeframe)
    # ------------------------------------------------------
    def verify_fields(self, tf, df):
        """
        Ensure each timeframe has a {'Slow_MA', 'Fast_MA' 'Proximity'} column.
        Uses a TF-aware threshold via pip size; fills missing values safely.
        """
        try:

            if df is None or not isinstance(df, pd.DataFrame):
                logger.warning(f"{self.symbol} {tf} is None or not a DataFrame, instance == {type(df)}")
                raise ValueError("df missing critical computational fields")

            # Ensure required MA columns exist
            if not {'Slow_MA', 'Fast_MA', "Proximity"}.issubset(df.columns):
                logger.warning(f"{self.symbol} {tf} missing MA columns; creating with NaN.")
                for col in ['Fast_MA', 'Slow_MA', 'Proximity']:
                    if col not in df.columns:
                        df[col] = np.nan
                return False

            logger.info(f"🎯 Proximity check completed for all timeframes ({self.symbol})")
            # NOTE: do not auto-run sequence_data here if caller wants control
            return True
        except Exception as e:
            logger.exception(f"exception in proximity check: {e}")
            return False

    # ------------------------------------------------------
    # 3) Check Main Trend Alignment from higher timeframes
    # ------------------------------------------------------
    def identify_Main_Trend_Alignment(self):
        """
        Compute an overall Main_Trend label based on latest row of each TF.
        Defensive: skips empty / invalid TFs.
        """
        try:
            count = 0
            valid_tfs = 0

            for tf, df in list(self.data.items()):
                if df is None or not isinstance(df, pd.DataFrame) or df.empty:
                    continue
                latest = df.iloc[-1]
                if pd.isna(latest.get("Fast_MA")) or pd.isna(latest.get("Slow_MA")):
                    continue
                valid_tfs += 1
                if latest["Slow_MA"] > latest["Fast_MA"]:
                    count -= 1
                else:
                    count += 1

            if valid_tfs == 0:
                self.data["Bias"] = "Unknown"
                return "Unknown"

            # strong alignment: absolute count equals number of valid TFs
            if abs(count) == valid_tfs:
                self.data["Bias"] = "Bullish" if count > 0 else "Bearish"
            else:
                self.data["Bias"] = "No Trend Alignment"
            logger.info(f"{self.symbol} Main_Trend identified: {self.data['Bias']}")
            return self.data["Bias"]

        except Exception as e:
            logger.exception(f"exception in Trend alignment: {e}")
            self.data["Bias"] = "Error"
            return "Error"

    # ------------------------------------------------------
    # 3) Proximity Entry Signals (per timeframe)
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

            if "M" not in tf or tf == "Bias":
                continue
            copyDF = df.copy()
            pip_size = self.get_pip_size(df['close'].iloc[-1])

            copyDF['Entry'] = None
            copyDF['SL'] = np.nan
            copyDF['TP'] = np.nan
            for i, row in copyDF.iterrows():
                entry_price = row['close']
                if not row['Proximity']:
                    continue
                # BUY
                if "Bullish" in df.loc[i, "Bias"]:
                    if row['Fast_MA'] > row['Slow_MA']:
                        df.loc[i, 'Entry'] = "Buy"
                        df.loc[i, 'SL'] = entry_price - (pip_distance * pip_size)
                        df.loc[i, 'TP'] = entry_price + (2 * pip_distance * pip_size)
                # SELL
                elif "Bearish" in df.loc[i, "Bias"]:
                    if row['Fast_MA'] < row['Slow_MA']:
                        df.loc[i, 'Entry'] = "Sell"
                        df.loc[i, 'SL'] = entry_price + (pip_distance * pip_size)
                        df.loc[i, 'TP'] = entry_price - (2 * pip_distance * pip_size)
                else:
                    continue
                df = copyDF

            self.data[tf] = df

        logger.info(f"🎯 Proximity entries generated for all timeframes ({self.symbol})")
        return self.data

    # ------------------------------------------------------
    # 5) Backtesting per timeframe
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

            if tf not in ["15M", "30M"] or not isinstance(df, pd.DataFrame):
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
                if entry_type not in ("Buy", "Sell") or "(W)" in row["Bias"]:
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
            self.data_handler.toCSVFile(df, f"src/main/python/Advisor/Logs/{self.symbol}_data/{tf}_backtest.csv")
            # Also save summary results
            all_results[tf] = pd.DataFrame(trades)
            logger.info(f"[{self.symbol}][{tf}] Backtest complete — {len(all_results[tf])} trades updated.")

        self.results = all_results
        return self.results

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

    def plot_backtest_strategy(self):
        """Backtest the strategy by calculating strategy returns."""
        for tf, df in self.data.items():
            if "M" in tf:
                df = df.copy()
                df['Position'] = df['Entry'].shift(1) if df["Outcome"] == "TP-Hit" else np.nan  # Avoid lookahead bias
                df['Market_Returns'] = df['close'].pct_change()
                df['Strategy_Returns'] = df['Market_Returns'] * \
                    df['Position'] if not pd.isna(df["Position"]) else np.nan
                df['Cumulative_Market_Returns'] = (1 + df['Market_Returns']).cumprod()
                df['Cumulative_Strategy_Returns'] = (1 + df['Strategy_Returns']).cumprod() if pd.isna(df["Strategy_Returns"]) else np.nan
                self.results = df.dropna()
        logger.info("Backtest completed.")
        return self.results

    def run_MA_Backtest(self):
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import time
        """
        High-level runner: ensure proximity present, run entries/backtest and summary.
        """
        # ensure proximity is present; check_proximity returns boolean
        executor = ThreadPoolExecutor()
        futures = {}
        for tf, value in TF_dict.items():
            futures[executor.submit(
                self.calculate_moving_averages_data,
                value
            )] = tf
            time.sleep(0.2)

        for f in as_completed(futures):
            tf = futures[f]
            try:
                res = f.result()
                self.cache.set(self.symbol + tf, res[1])
                return res
            except Exception as e:
                logger.exception(f'error fetching multi timeframe threads : {e}')
                return False
        # self.identify_proximity_entries()
        # self.backtest_entries()
        # tbl_30m = tabulate.tabulate(self.data["30M"], headers='keys', tablefmt='pretty', showindex=False)
        # tbl_15m = tabulate.tabulate(self.data["15M"], headers='keys', tablefmt='pretty', showindex=False)
        # print(f"30M:\n{tbl_30m}")
        # print(f"15M:\n{tbl_15m}")
        # summary = self.generate_backtest_summary()
        # logger.info(f"{self.symbol} strategy performance:\n{summary}")