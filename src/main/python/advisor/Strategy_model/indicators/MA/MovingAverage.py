from datetime import datetime, timedelta

from advisor.utils.logging_setup import get_logger
import numpy as np
import pandas as pd
from collections.abc import Iterable
# import tabulate

from concurrent.futures import ThreadPoolExecutor, as_completed

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
        pip_distance=250,
        start_workers: bool = True,
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

        self.executor: ThreadPoolExecutor | None = None
        self.all_timestamps = set()
        self.data_handler = DataHandler(
            self.symbol_name,
            logger.name,
            self.cache,
            start_workers=start_workers,
        )

        self.results: dict = {}
        self.pip_size = 0.0001
        try:
            df_30m = self.data_handler.data.get("30M")
            if isinstance(df_30m, pd.DataFrame) and not df_30m.empty:
                self.pip_size = self.get_pip_size(df_30m)
        except Exception:
            pass

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

    def _refresh_pip_size(self, df: pd.DataFrame) -> None:
        try:
            if df is None or not isinstance(df, pd.DataFrame) or "close" not in df.columns:
                return
            if df["close"].dropna().empty:
                return
            self.pip_size = self.get_pip_size(df)
        except Exception:
            logger.exception("Failed updating pip size for %s", self.symbol_name)

    # =========================================================
    # SCHEMA ENFORCEMENT (CRITICAL)
    # =========================================================
    def _ensure_schema(self, df: pd.DataFrame) -> pd.DataFrame:
        required = [
            "Entry", "SL", "TP",
            "ExitPrice", "ExitIndex",
            "Outcome", "PnL_Pips"
        ]

        for col in required:
            if col not in df.columns:
                if col in ["Entry", "Outcome"]:
                    df[col] = None
                else:
                    df[col] = np.nan
        return df

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

    def update_timestamps(self, timestamps):
        """Accept list/set of timestamps directly."""
        if timestamps:
            self.all_timestamps.update(timestamps)

    def _normalize_timestamp(self, ts):
        """Coerce mixed timestamp types into pandas Timestamp."""
        if isinstance(ts, pd.Timestamp):
            return ts
        if isinstance(ts, datetime):
            return pd.Timestamp(ts)
        if isinstance(ts, np.datetime64):
            return pd.Timestamp(ts)
        if isinstance(ts, (int, float, np.integer, np.floating)):
            try:
                unit = "ms" if ts > 1e12 else "s"
                return pd.to_datetime(ts, unit=unit, errors="coerce")
            except Exception:
                return pd.NaT
        return pd.to_datetime(ts, errors="coerce")

    def _build_snapshot(self, ts: dict):
        """Build multi-timeframe snapshot for a given timestamp."""
        def get_tf_snap(tf, df: pd.DataFrame) -> dict:
            if ts not in df.index:
                return None

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
        for tf, df in list(self.data_handler.data.items()):
            if df is None or not isinstance(df, pd.DataFrame):
                continue
            df = df.copy()  # ✅ prevent race condition
            if ts in df.index:
                row_futures[self.executor.submit(
                    get_tf_snap,
                    tf,
                    df
                )] = tf

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

    def _evaluate_trade_outcome_fast(self, high, low, entry_pos, entry_type, entry_price, sl, tp):
        """
        Fast, vectorized outcome scan using numpy arrays.
        Preserves original SL-first priority when both hit in the same candle.
        Returns (Outcome, ExitPrice, ExitIndex)
        """
        future_high = high[entry_pos + 1:]
        future_low = low[entry_pos + 1:]

        if entry_type == "Buy":
            sl_hits = np.flatnonzero(future_low <= sl)
            tp_hits = np.flatnonzero(future_high >= tp)
            sl_idx = sl_hits[0] if sl_hits.size else None
            tp_idx = tp_hits[0] if tp_hits.size else None

            if sl_idx is None and tp_idx is None:
                return "NoHit", entry_price, None
            if sl_idx is None:
                return "Profit", tp, entry_pos + 1 + tp_idx
            if tp_idx is None:
                return "Loss", sl, entry_pos + 1 + sl_idx
            if tp_idx < sl_idx:
                return "Profit", tp, entry_pos + 1 + tp_idx
            return "Loss", sl, entry_pos + 1 + sl_idx

        # SELL: SL/TP reversed
        sl_hits = np.flatnonzero(future_high >= sl)
        tp_hits = np.flatnonzero(future_low <= tp)
        sl_idx = sl_hits[0] if sl_hits.size else None
        tp_idx = tp_hits[0] if tp_hits.size else None

        if sl_idx is None and tp_idx is None:
            return "NoHit", entry_price, None
        if sl_idx is None:
            return "Profit", tp, entry_pos + 1 + tp_idx
        if tp_idx is None:
            return "Loss", sl, entry_pos + 1 + sl_idx
        if tp_idx < sl_idx:
            return "Profit", tp, entry_pos + 1 + tp_idx
        return "Loss", sl, entry_pos + 1 + sl_idx

    def _apply_precision(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Normalize all price-related columns to match pip precision.
        Ensures numerical consistency across OHLC + indicators.
        """
        try:
            precision = abs(int(np.log10(self.pip_size)))
        except Exception:
            precision = 5  # safe fallback

        cols = ["open", "high", "low", "close", "Fast_MA", "Slow_MA"]

        for col in cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").round(precision)

        return df
    # ------------------------------------------------------
    # Main Methods
    # ------------------------------------------------------

    # ------------------------------------------------------
    # 1) Moving Average Values (per timeframe)
    # ------------------------------------------------------
    def _normalize_datetime_index(self, df: pd.DataFrame, tf: str) -> pd.DataFrame:
        """Normalize DataFrame index to DatetimeIndex."""
        if isinstance(df.index, pd.DatetimeIndex):
            logger.info(f"{self.symbol_name} {tf}: Datetime index verified.")
            return df

        try:
            if 'time' in df.columns:
                if pd.api.types.is_numeric_dtype(df['time']):
                    df['time'] = pd.to_datetime(df['time'], unit='s', errors='coerce')
                else:
                    df['time'] = pd.to_datetime(df['time'], errors='coerce')
                df.set_index('time', inplace=True)
            else:
                df.index = pd.to_datetime(df.index, errors='coerce')

            df = df[~df.index.isna()]
            df.sort_index(inplace=True)
        except Exception:
            logger.warning(f"{self.symbol_name} {tf}: Failed to normalize datetime index")
        return df

    def _calculate_ma_indicators(self, df: pd.DataFrame, tf: str, threshold: float) -> pd.DataFrame:
        """Calculate Fast MA, Slow MA, Bias, and Proximity indicators."""
        try:
            logger.info(f"Calculating MAs for {self.symbol_name} {tf} with threshold {threshold}")
            df.loc[:, 'Fast_MA'] = df['close'].rolling(window=self.fast_period, min_periods=1).mean().shift(1)
            df.loc[:, 'Slow_MA'] = df['close'].rolling(window=self.slow_period, min_periods=1).mean().shift(1)
            df.loc[:, 'Bias'] = np.where(df['Fast_MA'] > df['Slow_MA'], "Bullish", "Bearish")
            df.loc[:, 'Proximity'] = (df['close'] - df['Slow_MA']).abs() <= threshold
        except Exception as e:
            logger.exception(f"exception in proximity calculation for {self.symbol_name} {tf}: {e}")
        return df

    def calculate_moving_averages_data(self, tf, df):
        """
        Calculate Fast/Slow MA and derived columns per timeframe.
        - Validates 'close' column
        - Ensures datetime index
        - Keeps frames even if some rows are NaN (avoid aggressive dropna)
        """
        logger.info("[%s][%s] MA calc start", self.symbol_name, tf)
        # Skip non-dataframes & placeholder keys
        if df is None or not isinstance(df, pd.DataFrame):
            logger.warning(f"{self.symbol_name} {tf} is None or not a DataFrame, instance == {type(df)}")
            return

        df = df.copy()
        df = self._normalize_datetime_index(df, tf)

        self._refresh_pip_size(df)
        meta = self.client.TF_dict[tf]
        threshold = meta.get("prox_limit", 300) * self.pip_size

        df = self._calculate_ma_indicators(df, tf, threshold)
        df = self._apply_precision(df)

        if self.verify_fields(tf, df):
            self.data_handler.update_timestamps(df.index)
        else:
            logger.warning(f"{self.symbol_name} {tf} failed field verification.")

        df.dropna(subset=['close'], inplace=True)

        try:
            if isinstance(df.index, pd.DatetimeIndex) and not df.empty:
                logger.info(
                    "[%s][%s] MA calc done rows=%d range=%s..%s",
                    self.symbol_name,
                    tf,
                    len(df),
                    df.index.min(),
                    df.index.max(),
                )
            else:
                logger.info("[%s][%s] MA calc done rows=%d", self.symbol_name, tf, len(df))
        except Exception:
            logger.info("[%s][%s] MA calc done", self.symbol_name, tf)
        return df

    # ------------------------------------------------------
    # 2) Check fields (per timeframe)
    # ------------------------------------------------------
    def verify_fields(self, tf, df: pd.DataFrame, fields: Iterable = {"close", "Fast_MA", "Slow_MA", "Proximity"}):
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
            return True
        except Exception as e:
            logger.exception(f"exception in proximity check: {e}")
            return False

    # ------------------------------------------------------
    # 3) Check Trend Alignment from higher timeframes
    # ------------------------------------------------------
    def _count_trend_metrics(self, data: dict = None) -> tuple:
        """
        Helper method to count trend direction and proximity metrics.
        Returns: (count, prox_meter)
        """
        count = 0
        prox_meter = []
        for tf, df in list(self.data_handler.data.items()):
            if not self.backtest:
                # only fetch the latest row per df for trend alignment
                if df is None or not isinstance(df, pd.DataFrame) or df.empty:
                    continue

                latest = df.iloc[-1]
                if pd.isna(latest.get("Fast_MA")) or pd.isna(latest.get("Slow_MA")):
                    continue
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
                        if pd.isna(row.get("Fast_MA")) or pd.isna(row.get("Slow_MA")):
                            continue
                        prox_meter.append(bool(row["Proximity"]))
                        count += self.comp(row)

        return count, prox_meter

    def identify_Trend_Alignment(self, data: dict = None) -> str | None:
        """
        Compute an overall Main_Trend label based on latest row of each TF.
        Defensive: skips empty / invalid TFs.
        """
        try:
            logger.info(f"Identifying trend alignment for {self.symbol_name} with data: {data}")
            count, prox_meter = self._count_trend_metrics(data)
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
    def _get_valid_timeframes(self) -> list:
        """Extract and validate timeframes for MTF alignment."""
        tfs = []
        for tf, df in list(self.data_handler.data.items()):
            if df is None or not isinstance(df, pd.DataFrame) or df.empty:
                continue
            if not all(col in df.columns for col in ["Fast_MA", "Slow_MA", "Proximity"]):
                continue
            if not isinstance(df.index, pd.DatetimeIndex):
                continue
            tfs.append(tf)
        return tfs

    def _compute_trend_metrics(self, base: pd.DataFrame, tfs: list) -> tuple:
        """Compute count and proximity metrics for all timeframes."""
        count = pd.Series(0, index=base.index, dtype="int")
        prox_true = pd.Series(0, index=base.index, dtype="int")

        for tf in tfs:
            df = self.data_handler.data[tf]
            aligned = df.reindex(base.index)
            fast = aligned["Fast_MA"]
            slow = aligned["Slow_MA"]
            prox = aligned["Proximity"]

            valid = fast.notna() & slow.notna()
            sign = pd.Series(0, index=base.index, dtype="int")
            sign[valid] = (fast[valid] > slow[valid]).astype(int) * 2 - 1
            count = count.add(sign, fill_value=0).astype(int)

            prox_true = prox_true.add((valid & prox.astype(bool)).astype(int), fill_value=0).astype(int)

        return count, prox_true

    def _classify_trends_vectorized(self, count: pd.Series, prox_true: pd.Series, ls_len: int, majority: int) -> np.ndarray:
        """Vectorized trend classification based on count and proximity."""
        strong_threshold = max(3, ls_len - 1)
        strong_bull = (count >= (ls_len - 2)) & (prox_true >= strong_threshold)
        strong_bear = (count <= -(ls_len - 2)) & (prox_true >= strong_threshold)
        bull = (count >= 5) & (prox_true >= 5)
        bear = (count <= -5) & (prox_true >= 5)

        main_trend = np.select(
            [
                count >= majority,
                count <= -majority,
            ],
            [
                np.select([strong_bull, bull], ["(S)Bullish", "Bullish"], default="(W)Bullish"),
                np.select([strong_bear, bear], ["(S)Bearish", "Bearish"], default="(W)Bearish"),
            ],
            default="Neutral",
        )
        return main_trend

    def sequence_Trend_Data(self):
        """
        Timestamp-aligned multi-timeframe trend evaluation.

        For every unique timestamp across all timeframes:
            - Build snapshot of (Slow_MA, Fast_MA, Proximity)
            - Compute MTF Main_Trend
            - Insert Main_Trend into 15M & 30M rows that match that timestamp
        """
        try:
            logger.info(f"Starting timestamp-based sequence_data for {self.symbol_name}")
            if not self.data_handler.all_timestamps:
                logger.warning(f"{self.symbol_name}: No timestamps found for MTF alignment.")
                return

            normalized = []
            for ts in self.data_handler.all_timestamps:
                ts_norm = self._normalize_timestamp(ts)
                if pd.isna(ts_norm):
                    continue
                normalized.append(ts_norm)
            self.data_handler.all_timestamps = set(normalized)

            common_ts = sorted(self.data_handler.all_timestamps, key=self._normalize_timestamp)
            if not common_ts:
                logger.warning(f"{self.symbol_name}: No common timestamps found for MTF alignment.")
                return
            logger.info("[%s] MTF alignment timestamps=%d", self.symbol_name, len(common_ts))

            tfs = self._get_valid_timeframes()
            if not tfs:
                logger.warning("%s: No valid timeframes found for MTF alignment.", self.symbol_name)
                return

            ls_len = len(tfs)
            majority = ls_len // 2 + 1

            for ltf in ["15M", "30M"]:
                base = self.data_handler.data.get(ltf)
                if base is None or not isinstance(base, pd.DataFrame) or base.empty:
                    continue
                if not isinstance(base.index, pd.DatetimeIndex):
                    continue

                base = base.copy()
                base.sort_index(inplace=True)

                count, prox_true = self._compute_trend_metrics(base, tfs)
                main_trend = self._classify_trends_vectorized(count, prox_true, ls_len, majority)

                base.loc[base.index, "Bias"] = main_trend
                self.data_handler.data[ltf] = base

            logger.info("[%s] MTF alignment complete", self.symbol_name)

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
        logger.info(f"Identifying proximity entries for {self.symbol_name} {tf} with pip size {self.pip_size} and pip distance {self.pip_distance}")

        bias = copyDF["Bias"].astype(str)
        prox = copyDF["Proximity"].astype(bool)
        weak = bias.str.contains(r"\(W\)")
        strong = bias.str.contains(r"\(S\)")
        bullish = bias.str.contains("Bullish")
        bearish = bias.str.contains("Bearish")

        mask_buy = prox & bullish & ~weak
        mask_sell = prox & bearish & ~weak
        mask_buy_strong = mask_buy & strong
        mask_buy_norm = mask_buy & ~strong
        mask_sell_strong = mask_sell & strong
        mask_sell_norm = mask_sell & ~strong

        entry_price = copyDF["close"]

        copyDF.loc[mask_buy_strong, "Entry"] = "Buy"
        copyDF.loc[mask_buy_strong, "SL"] = entry_price[mask_buy_strong] - (2 * self.pip_distance * (self.pip_size * 3))
        copyDF.loc[mask_buy_strong, "TP"] = entry_price[mask_buy_strong] + (3 * self.pip_distance * (self.pip_size * 3))

        copyDF.loc[mask_buy_norm, "Entry"] = "Buy"
        copyDF.loc[mask_buy_norm, "SL"] = entry_price[mask_buy_norm] - (self.pip_distance * self.pip_size)
        copyDF.loc[mask_buy_norm, "TP"] = entry_price[mask_buy_norm] + (3 * self.pip_distance * self.pip_size)

        copyDF.loc[mask_sell_strong, "Entry"] = "Sell"
        copyDF.loc[mask_sell_strong, "SL"] = entry_price[mask_sell_strong] + (2 * self.pip_distance * (self.pip_size * 3))
        copyDF.loc[mask_sell_strong, "TP"] = entry_price[mask_sell_strong] - (3 * self.pip_distance * (self.pip_size * 3))

        copyDF.loc[mask_sell_norm, "Entry"] = "Sell"
        copyDF.loc[mask_sell_norm, "SL"] = entry_price[mask_sell_norm] + (self.pip_distance * self.pip_size)
        copyDF.loc[mask_sell_norm, "TP"] = entry_price[mask_sell_norm] - (3 * self.pip_distance * self.pip_size)

        entries = copyDF["Entry"].notna().sum()
        df = copyDF

        self.backtest_entries(tf, df)

        logger.info(f"Proximity entries generated for timeframe ({tf}) entries={entries}")
        return df

    # ------------------------------------------------------
    # 7) Backtesting per timeframe
    # ------------------------------------------------------
    def _is_within_cooldown(self, current: datetime | None, timestamp: datetime, cooldown: timedelta) -> tuple[bool, datetime]:
        if current is None:
            return False, timestamp
        if timestamp - current < cooldown:
            return True, current
        return False, timestamp

    def _record_backtest_trade(
        self,
        tf: str,
        df: pd.DataFrame,
        idx,
        pos: int,
        row: pd.Series,
        entry_type: str,
        entry_price: float,
        sl: float,
        tp: float,
    ) -> dict | None:
        if pd.isna(sl) or pd.isna(tp):
            return None

        outcome, exit_price, exit_index = self._evaluate_trade_outcome(
            df=df,
            entry_pos=pos,
            entry_type=entry_type,
            entry_price=entry_price,
            sl=sl,
            tp=tp,
            pip_size=self.pip_size
        )

        pnl_pips = 0
        if exit_price is not None:
            pnl_pips = (
                (exit_price - entry_price) / self.pip_size
                if entry_type == "Buy"
                else (entry_price - exit_price) / self.pip_size
            )

        df.loc[idx, "ExitPrice"] = exit_price
        df.loc[idx, "ExitIndex"] = exit_index
        df.loc[idx, "Outcome"] = outcome
        df.loc[idx, "PnL_Pips"] = pnl_pips

        return {
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
        }

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
            return

        df = df.copy()
        logger.info(f"Starting backtest for {self.symbol_name} {tf} with {len(df)} rows.")
        for col in ["ExitPrice", "ExitIndex", "Outcome", "PnL_Pips"]:
            if col not in df.columns:
                df[col] = None

        trades = []
        current: datetime | None = None
        cooldown = timedelta(hours=1)

        entry_series = df["Entry"]
        entry_mask = entry_series.isin(["Buy", "Sell"])
        entry_positions = np.flatnonzero(entry_mask.to_numpy())

        high = df["high"].to_numpy()
        low = df["low"].to_numpy()
        close = df["close"].to_numpy()
        sl_arr = df["SL"].to_numpy()
        tp_arr = df["TP"].to_numpy()
        entry_type_arr = entry_series.to_numpy()

        col_exit_price = df.columns.get_loc("ExitPrice")
        col_exit_index = df.columns.get_loc("ExitIndex")
        col_outcome = df.columns.get_loc("Outcome")
        col_pnl = df.columns.get_loc("PnL_Pips")

        for pos in entry_positions:
            entry_type = entry_type_arr[pos]
            if entry_type not in ("Buy", "Sell"):
                continue

            sl = sl_arr[pos]
            tp = tp_arr[pos]
            if pd.isna(sl) or pd.isna(tp):
                continue

            timestamp = pd.to_datetime(df.index[pos], errors="coerce")
            if pd.isna(timestamp):
                continue

            cooldown_violation, current = self._is_within_cooldown(current, timestamp, cooldown)
            if cooldown_violation:
                continue

            entry_price = close[pos]
            outcome, exit_price, exit_index = self._evaluate_trade_outcome_fast(
                high=high,
                low=low,
                entry_pos=pos,
                entry_type=entry_type,
                entry_price=entry_price,
                sl=sl,
                tp=tp,
            )

            pnl_pips = 0
            if exit_price is not None:
                pnl_pips = (
                    (exit_price - entry_price) / self.pip_size
                    if entry_type == "Buy"
                    else (entry_price - exit_price) / self.pip_size
                )

            df.iat[pos, col_exit_price] = exit_price
            df.iat[pos, col_exit_index] = exit_index
            df.iat[pos, col_outcome] = outcome
            df.iat[pos, col_pnl] = pnl_pips

            trades.append(
                {
                    "TF": tf,
                    "Index": df.index[pos],
                    "Type": entry_type,
                    "EntryPrice": entry_price,
                    "SL": sl,
                    "TP": tp,
                    "ExitPrice": exit_price,
                    "Outcome": outcome,
                    "ExitIndex": exit_index,
                    "PnL_Pips": pnl_pips,
                }
            )

        if trades:
            self.data_handler.data[tf] = df
            self.data_handler.save_data_toFile(
                df,
                f"Backtest/{tf}_Backtest.csv"
            )
            all_trades[tf] = trades
            logger.info(
                f"[{self.symbol_name}][{tf}] Backtest complete - {len(all_trades[tf])} trades updated."
            )
        else:
            logger.info(f"[{self.symbol_name}][{tf}] Backtest complete - 0 trades.")

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
        if df is None or df.empty or tf not in ["15M", "30M"]:
            return {}

        summary: dict = {}
        all_pips = []

        # Ensure numeric PnL
        df["PnL_Pips"] = df["PnL_Pips"].astype(float) if "PnL_Pips" in df.columns and not df["PnL_Pips"].isna().all() else pd.Series(dtype=float)

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
        outcomes = df["Outcome"].tolist() if "Outcome" in df.columns and not df["Outcome"].isna().any() else []
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
        df['Position'] = np.where(
            df["Outcome"].isin(["Profit", "Loss"]),
            df["Entry"].shift(1),
            np.nan
        )
        df["Position_Sign"] = df["Position"].map({"Buy": 1, "Sell": -1})
        df['Market_Returns'] = df['close'].pct_change()
        df['Strategy_Returns'] = df['Market_Returns'] * df["Position_Sign"]
        df['Cumulative_Market_Returns'] = (1 + df['Market_Returns'].fillna(0)).cumprod()
        df['Cumulative_Strategy_Returns'] = (1 + df['Strategy_Returns'].fillna(0)).cumprod()
        self.results[tf] = df.dropna()
        return self.results

    def backtest_entries_data(self):
        # --- STEP 3: entry detection ---
        logger.info("[%s] Backtest entry generation start", self.symbol_name)
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
                    summary = self.generate_backtest_summary(tf, df)
                    if isinstance(summary, dict) and tf in summary:
                        summaries[tf] = summary[tf]
            except Exception as e:
                logger.exception("Backtest entry generation failed for %s: %s", tf, e)

        logger.info("[%s] Backtest entry generation done summaries=%d", self.symbol_name, len(summaries))
        return summaries

    def run(self) -> dict | None:
        """
        Execute MA strategy on available data.
        Returns: a dictionary of a signl and row data or None if running backtest
        """
        # --- STEP 1: MA calculation (parallel, no early return) ---
        logger.info(f"calculating moving averages for {self.symbol_name} across all timeframes.")
        logger.info("[%s] Timeframes available=%d", self.symbol_name, len(self.data_handler.data))
        futures = {
            self.executor.submit(self.calculate_moving_averages_data, tf, df): tf
            for tf, df in self.data_handler.data.items()
            if isinstance(df, pd.DataFrame)
        }

        for f in as_completed(futures):
            tf = futures[f]
            self.data_handler.data[tf] = f.result()
        logger.info("[%s] MA calc complete across all timeframes", self.symbol_name)

        if self.backtest:
            # --- STEP 2: sequence synthesis ---
            self.sequence_Trend_Data()
            print(f"MA entries {tf}\nresult: \n{self.data_handler.data}\n\n")
            self.results = self.backtest_entries_data()
            key = self.symbol_name + "_backtest_summary"
            self.cache.set(key, self.results)
            logger.info("[%s] Backtest run complete", self.symbol_name)
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
        logger.info("MovingAverageCrossover start for %s (backtest=%s)", self.symbol_name, self.backtest)

        if not self.backtest:
            self.data_handler.start_workers()

        try:
            self.executor = ThreadPoolExecutor(max_workers=20)
            return self.run()
        finally:
            if self.executor is not None:
                self.executor.shutdown(wait=True)
                self.executor = None
