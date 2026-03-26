from advisor.utils.logging_setup import get_logger
import numpy as np
import pandas as pd
from collections.abc import Iterable
# import tabulate

from concurrent.futures import ThreadPoolExecutor, as_completed
import time

from advisor.utils.dataHandler import CacheManager, DataHandler
from advisor.Client.mt5Client import MetaTrader5Client
from advisor.core.state import SymbolState
logger = get_logger("_EMA_")

class MovingAverageCrossover:

    def __init__(
        self,
        symbol: SymbolState,
        client: MetaTrader5Client,
        cache: CacheManager,
        fast_period=50,
        slow_period=200,
        pip_distance=250
    ):
        """
        Initialize the strategy with data handlers and parameters.

        :param data: DataFrame containing historical data (must include 'close').
        :param fast_period: Period for the fast-moving average.
        :param slow_period: Period for the slow-moving average.
        """
        self.client = client
        self.symbol = symbol
        self.symbol_name = symbol.symbol if hasattr(symbol, "symbol") else str(symbol)
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.pip_distance = pip_distance
        self.cache = cache
        self.backtest: bool = False

        self.executor = ThreadPoolExecutor(max_workers=20)
        self.all_timestamps = set()
        self.data_handler = DataHandler(self.symbol_name, logger.name, self.cache)

        self.results: dict = {}
        try:
            self.pip_size = self.get_pip_size(self.data_handler.data.get('30M'))
        except Exception:
            self.pip_size = 0.0001

    # ------------------------------------------------------
    # Helper Methods
    # ------------------------------------------------------
    @staticmethod
    def comp(data_set):
        count = 0
        if data_set["Slow_MA"] > data_set["Fast_MA"]:
            count -= 1
        else:
            count += 1
        return count

    def _clean_value(self, v):
        """Convert numpy types or pandas types to native Python."""
        if hasattr(v, "item"):
            return v.item()
        return float(v) if isinstance(v, (np.floating, np.float64)) else v

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

    def _build_all_timestamps(self, df):
        """Build a unified set of timestamps from all TF data."""
        if (isinstance(df, pd.DataFrame)) and ("time" in df.columns):
            return sorted(df.index)

    def _build_snapshot(self, ts: dict):
        """Build multi-timeframe snapshot for a given timestamp."""
        def get_tf_snap(tf, df: pd.DataFrame) -> dict:
            row = df.loc[ts]
            if not all(col in row for col in ["Slow_MA", "Fast_MA", "Proximity"]):
                return

            mtf_row_data = {
                "timestamp": ts,
                "Slow_MA": row["Slow_MA"],
                "Fast_MA": row["Fast_MA"],
                "Proximity": row["Proximity"],
            }
            return mtf_row_data

        mtf_row_data = {}
        row_futures = {}
        for tf, df in self.data_handler.data.items():
            if ts in df.index:
                row_futures[self.executor.submit(
                    get_tf_snap,
                    tf,
                    df
                )] = tf
                time.sleep(0.2)

        for f in as_completed(row_futures):
            tf = row_futures[f]
            try:
                data = f.result()
                mtf_row_data[tf] = data
            except Exception as e:
                logger.exception(f"Exception running sequence: {e}")
                return
        return mtf_row_data

    def _write_main_trend_to_ltf(self, ts, main_trend):
        """Write Main_Trend back into required LTF rows that match timestamp."""
        for tf in ["15M", "30M"]:
            if tf in self.data_handler.data and ts in self.data_handler.data[tf].index:
                self.data_handler.data[tf].loc[ts, "Bias"] = main_trend

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
                    return "Loss", sl, pos

                if price_high >= tp:
                    return "Profit", tp, pos

            # SELL: reversed SL/TP checks
            else:

                if price_high >= sl:
                    return "Loss", sl, pos

                if price_low <= tp:
                    return "Profit", tp, pos

        # If never hit SL/TP — optional behavior
        return "NoHit", entry_price, None

    # ------------------------------------------------------
    # Main Methods
    # ------------------------------------------------------

    # ------------------------------------------------------
    # 1) Moving Average Values (per timeframe)
    # ------------------------------------------------------
    def calculate_moving_averages_data(self, tf, df):
        """
        Calculate Fast/Slow MA and derived columns per timeframe.
        - Validates 'close' column
        - Ensures datetime index
        - Keeps frames even if some rows are NaN (avoid aggressive dropna)
        """
        # Skip non-dataframes & placeholder keys
        if df is None or not isinstance(df, pd.DataFrame):
            return

        if 'close' not in df.columns:
            logger.warning(f"{self.symbol_name} {tf} missing 'close' column — skipping TF.")
            return

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
        meta = self.client.TF_dict[tf]
        threshold = meta["prox_limit"] * self.pip_size
        try:
            # Vectorized calculation and safe fill
            df.loc[:, 'Fast_MA'] = df['close'].rolling(window=self.fast_period, min_periods=1).mean().shift(1)
            df.loc[:, 'Slow_MA'] = df['close'].rolling(window=self.slow_period, min_periods=1).mean().shift(1)
            df.loc[:, 'Bias'] = np.where(df['Fast_MA'] > df['Slow_MA'], "Bullish", "Bearish")
            df.loc[:, 'Proximity'] = (df['close'] - df['Slow_MA']).abs() <= threshold
            df.dropna()
        except Exception as e:
            logger.exception(f"exception in proximity calculation for {self.symbol_name} {tf}: {e}")

        if self.verify_fields(tf, df):
            self.data_handler.update_timestamps(self._build_all_timestamps(df))
            return df
        return

    # ------------------------------------------------------
    # 2) Check fields (per timeframe)
    # ------------------------------------------------------
    def verify_fields(self, tf, df: pd.DataFrame, fields: Iterable = {"close"}):
        """
        Ensure each timeframe has a {'Slow_MA', 'Fast_MA' 'Proximity'} column.
        Uses a TF-aware threshold via pip size; fills missing values safely.
        """
        try:

            if df is None or not isinstance(df, pd.DataFrame):
                logger.warning(f"{self.symbol_name} {tf} is None or not a DataFrame, instance == {type(df)}")
                raise ValueError("df missing critical computational fields")

            # Ensure required MA columns exist
            if not fields.issubset(df.columns):
                logger.warning(f"{self.symbol_name}-{tf} missing MA columns; creating with NaN.")
                for col in ['Fast_MA', 'Slow_MA', 'Proximity']:
                    if col not in df.columns:
                        df[col] = np.nan
                return False

            logger.info(f"🎯 Proximity check completed for all timeframes ({self.symbol_name})")
            # NOTE: do not auto-run sequence_data here if caller wants control
            return True
        except Exception as e:
            logger.exception(f"exception in proximity check: {e}")
            return False

    # ------------------------------------------------------
    # 3) Check Trend Alignment from higher timeframes
    # ------------------------------------------------------
    def identify_Trend_Alignment(self, data: dict = None) -> str | None:
        """
        Compute an overall Main_Trend label based on latest row of each TF.
        Defensive: skips empty / invalid TFs.
        """
        try:
            count = 0
            prox_meter = []
            for tf, df in list(self.data_handler.data.items()):
                if not self.backtest:
                    # only fetch the latest row per df for trend alignment
                    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
                        continue

                    if pd.isna(df.get("Fast_MA")) or pd.isna(df.get("Slow_MA")):
                        continue

                    latest = df.iloc[-1]
                    prox_meter.append(bool(latest["Proximity"]))
                    count += self.comp(latest)
                else:
                    # iterate all every row for trend alignment
                    if data is not None:
                        df = pd.DataFrame(df)
                        for t, row in data.items():
                            if t != tf:
                                continue
                            # Collect proximity boolean
                            prox_meter.append(bool(row["Proximity"]))
                            count += self.comp(row)

            prox_true = prox_meter.count(True)
            majority = len(self.data_handler.data) // 2 + 1

            if count >= majority:
                return self._classify_bullish_trend(count, len(self.data_handler.data), prox_true)
            elif count <= -majority:
                return self._classify_bearish_trend(count, len(self.data_handler.data), prox_true)

            return "Neutral"

        except Exception as e:
            logger.exception(f"exception in Trend alignment: {e}")
            return "Error"

    # ------------------------------------------------------
    # 5) sequence proximity entries for lower timeframes
    # ------------------------------------------------------
    def sequence_Trend_Data(self):
        """
        Timestamp-aligned multi-timeframe trend evaluation.

        For every unique timestamp across all timeframes:
            - Build snapshot of (Slow_MA, Fast_MA, Proximity)
            - Compute MTF Main_Trend
            - Insert Main_Trend into 15M & 30M rows that match that timestamp
        """
        try:
            if not self.data_handler.all_timestamps:
                logger.warning(f"{self.symbol_name}: No timestamps found for MTF alignment.")
                return

            common_ts = sorted(self.data_handler.all_timestamps)
            if not common_ts:
                logger.warning(f"{self.symbol_name}: No timestamps found for MTF alignment.")
                return

            ts_futures = {}
            for ts in common_ts:
                stamp = str(ts)
                ts_futures[self.executor.submit(
                    self._build_snapshot,
                    ts
                )] = stamp

            for f in as_completed(ts_futures):
                stamp = ts_futures[f]
                mtf_snapshot = f.result()
                if len(mtf_snapshot) == 0:
                    continue

                main_trend = self.identify_Trend_Alignment(mtf_snapshot)
                self._write_main_trend_to_ltf(ts, main_trend)

            logger.info(f"✔ Timestamp-aligned MTF Main_Trend computed for {self.symbol_name}")

        except Exception as e:
            logger.exception(f"Exception in timestamp-based sequence_data: {e}")

    # ------------------------------------------------------
    # 6) Proximity Entry Signals (per timeframe)
    # ------------------------------------------------------
    def identify_proximity_entries(self, df: pd.DataFrame, tf) -> pd.DataFrame:
        """
        Identifies BUY & SELL proximity entries timeframes.
        """
        # Skip the Main Trend key
        if df is None:
            logger.warning(f'{self.symbol_name} {tf}: data is none == {df}')
            return

        if "M" not in tf:
            return

        copyDF = df.copy()

        copyDF["Entry"] = pd.Series([None] * len(copyDF), index=copyDF.index, dtype="object")
        copyDF["SL"] = np.nan
        copyDF["TP"] = np.nan

        for i, row in copyDF.iterrows():

            entry_price = row['close']
            bias = copyDF.loc[i, "Bias"]

            if not row['Proximity']:
                continue

            if "(W)" in bias:
                continue
            # BUY
            if "Bullish" in bias:
                if "(S)" in bias:
                    # stronger MA confluence == higher risk higher reward entry
                    copyDF.loc[i, 'Entry'] = "Buy"
                    copyDF.loc[i, 'SL'] = entry_price - (self.pip_distance * (self.pip_size * 3))
                    copyDF.loc[i, 'TP'] = entry_price + (3 * self.pip_distance * (self.pip_size * 3))
                else:
                    # normal confluence == normal entry
                    copyDF.loc[i, 'Entry'] = "Buy"
                    copyDF.loc[i, 'SL'] = entry_price - (self.pip_distance * self.pip_size)
                    copyDF.loc[i, 'TP'] = entry_price + (3 * self.pip_distance * self.pip_size)
            # SELL
            elif "Bearish" in bias:
                if "(S)" in bias:
                    # stronger MA confluence == higher risk higher reward entry
                    copyDF.loc[i, 'Entry'] = "Sell"
                    copyDF.loc[i, 'SL'] = entry_price + (self.pip_distance * (self.pip_size * 3))
                    copyDF.loc[i, 'TP'] = entry_price - (3 * self.pip_distance * (self.pip_size * 3))
                else:
                    # normal confluence == normal entry
                    copyDF.loc[i, 'Entry'] = "Sell"
                    copyDF.loc[i, 'SL'] = entry_price + (self.pip_distance * self.pip_size)
                    copyDF.loc[i, 'TP'] = entry_price - (3 * self.pip_distance * self.pip_size)
            else:
                continue
        df = copyDF

        self.backtest_entries(tf, df)

        logger.info(f"🎯 Proximity entries generated for timeframe ({tf})")
        return df

    # ------------------------------------------------------
    # 7) Backtesting per timeframe
    # ------------------------------------------------------
    def backtest_entries(self, tf, df: pd.DataFrame) -> dict | None:
        """
        Backtest entries for each timeframe and update the
        original DataFrame with trade results.

        Adds the following columns to each TF DataFrame:
            - ExitPrice
            - ExitIndex
            - Outcome
            - PnL_Pips
        """

        all_trades = {}

        # Skip invalid items
        if df is None:
            logger.warning(f'{self.symbol_name} {tf} is None: {df}')
            return

        if tf not in ["15M", "30M"] or not isinstance(df, pd.DataFrame):
            return

        # Skip TFs without Entry column (H1, H4, etc.)
        if "Entry" not in df.columns:
            # logger.info(f"[{tf}] Skipped — no Entry column.")
            return

        # Defensive copy
        df = df.copy()

        # Add result columns if missing
        for col in ["ExitPrice", "ExitIndex", "Outcome", "PnL_Pips"]:
            if col not in df.columns:
                df[col] = None

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
                pip_size=self.pip_size
            )

            # PnL (Buy vs Sell)
            if exit_price is not None:
                pnl_pips = (
                    (exit_price - entry_price) / self.pip_size
                    if entry_type == "Buy"
                    else (entry_price - exit_price) / self.pip_size
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
            self.data_handler.data[tf] = df
            self.data_handler.save_data_toCSVFile(df, f"src/main/python/Advisor/Logs/{self.symbol_name}_data/{tf}_backtest.csv")
            # Also save summary results
            all_trades[tf] = trades
            logger.info(f"[{self.symbol_name}][{tf}] Backtest complete — {len(all_trades[tf])} trades updated.")

        return all_trades

    # ------------------------------------------------------
    # 8) generate eummary per timeframe
    # ------------------------------------------------------
    def generate_backtest_summary(self, tf, df: pd.DataFrame) -> dict:
        """
        Generate a clean summary report for all timeframes.
        Converts numpy types to built-in Python values.
        Includes:
            - daily & weekly trade averages
            - days covered by each timeframe
        """
        if not hasattr(self, "results") or not self.results:
            return

        summary: dict = {}
        all_pips = []

        if df.empty or tf not in ["15M", "30M"]:
            return

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
            if o == "Profit":
                temp_win += 1
                temp_loss = 0
            elif o == "Loss":
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

    def plot_backtest_strategy(self, tf, df: pd.DataFrame):
        """Backtest the strategy by calculating strategy returns."""
        logger.info("Computing backtest results")
        df = df.copy()
        df['Position'] = df['Entry'].shift(1) if df["Outcome"] == "TP-Hit" else np.nan  # Avoid lookahead bias
        df['Market_Returns'] = df['close'].pct_change()
        df['Strategy_Returns'] = df['Market_Returns'] * \
            df['Position'] if not pd.isna(df["Position"]) else np.nan
        df['Cumulative_Market_Returns'] = (1 + df['Market_Returns']).cumprod()
        df['Cumulative_Strategy_Returns'] = (1 + df['Strategy_Returns']).cumprod() if pd.isna(df["Strategy_Returns"]) else np.nan
        self.results[tf] = df.dropna()
        return self.results

    def backtest_entries_data(self):
        # --- STEP 3: entry detection ---
        entry_futures = {
            self.executor.submit(self.identify_proximity_entries, df, tf): tf
            for tf, df in self.data_handler.data.items()
            if "M" in tf
        }

        summaries = {}
        for f in as_completed(entry_futures):
            tf = entry_futures[f]
            try:
                df = f.result()
                self.data_handler.data[tf] = df
                if tf in ["15M", "30M"] and df is not None:
                    summaries[tf] = self.generate_backtest_summary(tf, df)[tf]
            except Exception as e:
                logger.exception("Backtest entry generation failed for %s: %s", tf, e)

        return summaries

    def run(self) -> dict | None:
        """
        Execute MA strategy on available data.
        Returns: a dictionary of a signl and row data or None if running backtest
        """
        # --- STEP 1: MA calculation (parallel, no early return) ---
        futures = {
            self.executor.submit(self.calculate_moving_averages_data, tf, df): tf
            for tf, df in self.data_handler.data.items()
            if isinstance(df, pd.DataFrame)
        }

        for f in as_completed(futures):
            tf = futures[f]
            self.data_handler.data[tf] = f.result()

        results = {}
        if self.backtest:
            # --- STEP 2: sequence synthesis ---
            self.sequence_Trend_Data()
            self.results = self.backtest_entries_data()
            key = self.symbol_name + "_backtest_summary"
            self.cache.set(key, results)
            return None

        frame = None
        preferred_tfs = ["15M", "30M", "1H", "2H", "4H", "6H", "8H", "1D"]
        for tf in preferred_tfs:
            df = self.data_handler.data.get(tf)
            if isinstance(df, pd.DataFrame) and not df.empty:
                frame = df.iloc[-1]
                break

        if frame is None:
            for tf, df in self.data_handler.data.items():
                if isinstance(df, pd.DataFrame) and not df.empty:
                    frame = df.iloc[-1]
                    break

        if frame is None:
            logger.warning(f"{self.symbol_name}: no timeframe data available for live signal.")
            return None

        signal = self.identify_Trend_Alignment()
        return {"sig": signal, "frame": frame}

    # ------------------------------------------------------
    # Callable Strategy Interface
    # ------------------------------------------------------
    def __call__(self, backtest: bool | None = None):
        """
        Allows the strategy instance to be executed like a function.

        Example:
            strategy()
            strategy(backtest=True)
        """

        if backtest is not None:
            self.backtest = backtest

        try:
            return self.run()
        finally:
            self.executor.shutdown(wait=True)
