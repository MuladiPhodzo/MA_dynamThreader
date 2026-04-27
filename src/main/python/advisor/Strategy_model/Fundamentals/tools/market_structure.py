from __future__ import annotations

import numpy as np
import pandas as pd

from advisor.Strategy_model.Fundamentals.technical_base import Technical
from advisor.Strategy_model.Fundamentals.technical_registry import TechnicalRegistry
from utils import math_utils


@TechnicalRegistry.register("Market_Structure", "structure")
class MarketStructure(Technical):
    """
    Detects market structure and related price-action features.

    The implementation avoids lookahead bias by confirming pivots only after
    enough future candles have already printed. It also forward-fills structure
    state so downstream consumers do not have to deal with sparse labels.
    """
    def _true_range(self, df: pd.DataFrame) -> pd.Series:
        prev_close = df["close"].shift(1)
        high_low = df["high"] - df["low"]
        high_close = (df["high"] - prev_close).abs()
        low_close = (df["low"] - prev_close).abs()
        return pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)

    def _atr(self, df: pd.DataFrame) -> pd.Series:
        window = max(1, int(self.params.get("zone_window", self.params.get("swing_window", 5))))
        return self._true_range(df).rolling(window, min_periods=1).mean()

    def _detect_confirmed_swings(self, df: pd.DataFrame):
        """
        Confirm pivots with a `swing_window` bar delay to avoid repainting.

        A pivot at `pivot_idx` is only marked on `confirm_idx = pivot_idx + swing_window`
        after the right-hand side of the window has fully printed.
        """

        highs = pd.to_numeric(df["high"], errors="coerce").to_numpy(dtype=float)
        lows = pd.to_numeric(df["low"], errors="coerce").to_numpy(dtype=float)
        n = len(df)
        w = max(1, int(self.params.get("swing_window", 5)))

        swing_highs = np.full(n, np.nan)
        swing_lows = np.full(n, np.nan)

        if n < (2 * w + 1):
            return swing_highs, swing_lows

        for confirm_idx in range(2 * w, n):
            pivot_idx = confirm_idx - w
            window_start = confirm_idx - (2 * w)
            window_high = highs[window_start:confirm_idx + 1]
            window_low = lows[window_start:confirm_idx + 1]

            pivot_high = highs[pivot_idx]
            pivot_low = lows[pivot_idx]

            if np.isfinite(pivot_high) and window_high.size and np.isclose(
                pivot_high, np.nanmax(window_high), rtol=1e-9, atol=1e-12
            ):
                swing_highs[confirm_idx] = pivot_high

            if np.isfinite(pivot_low) and window_low.size and np.isclose(
                pivot_low, np.nanmin(window_low), rtol=1e-9, atol=1e-12
            ):
                swing_lows[confirm_idx] = pivot_low

        return swing_highs, swing_lows

    def _classify_structure(self, swing_highs, swing_lows, atr: pd.Series):
        structure_event = [None] * len(swing_highs)
        structure_strength = np.full(len(swing_highs), np.nan)

        last_high = None
        last_low = None

        for i in range(len(swing_highs)):
            if not np.isnan(swing_highs[i]):
                current_high = float(swing_highs[i])
                if last_high is None:
                    structure_event[i] = "HH?"
                elif current_high > last_high:
                    structure_event[i] = "HH"
                    structure_strength[i] = abs(current_high - last_high) / max(float(atr.iloc[i]), 1e-6)
                else:
                    structure_event[i] = "LH"
                    structure_strength[i] = abs(current_high - last_high) / max(float(atr.iloc[i]), 1e-6)
                last_high = current_high

            elif not np.isnan(swing_lows[i]):
                current_low = float(swing_lows[i])
                if last_low is None:
                    structure_event[i] = "LL?"
                elif current_low > last_low:
                    structure_event[i] = "HL"
                    structure_strength[i] = abs(current_low - last_low) / max(float(atr.iloc[i]), 1e-6)
                else:
                    structure_event[i] = "LL"
                    structure_strength[i] = abs(current_low - last_low) / max(float(atr.iloc[i]), 1e-6)
                last_low = current_low

        return structure_event, structure_strength

    def _derive_trend(self, structure: pd.Series) -> pd.Series:
        mapped = structure.map(
            {
                "HH": "Bullish",
                "HL": "Bullish",
                "LH": "Bearish",
                "LL": "Bearish",
            }
        )
        trend_values = []
        current = "Neutral"
        for value in mapped.tolist():
            if pd.notna(value):
                current = value
            trend_values.append(current)
        return pd.Series(trend_values, index=structure.index, dtype=object)

    def _detect_bos(self, df: pd.DataFrame, swing_highs, swing_lows):
        bos = [None] * len(df)

        last_high = None
        last_low = None
        close = pd.to_numeric(df["close"], errors="coerce").to_numpy(dtype=float)

        for i in range(len(df)):
            if not np.isnan(swing_highs[i]):
                last_high = float(swing_highs[i])
            if not np.isnan(swing_lows[i]):
                last_low = float(swing_lows[i])

            prev_close = close[i - 1] if i > 0 else np.nan
            if last_high is not None and np.isfinite(close[i]) and close[i] > last_high and (
                i == 0 or not np.isfinite(prev_close) or prev_close <= last_high
            ):
                bos[i] = "Bullish_BOS"
            elif last_low is not None and np.isfinite(close[i]) and close[i] < last_low and (
                i == 0 or not np.isfinite(prev_close) or prev_close >= last_low
            ):
                bos[i] = "Bearish_BOS"

        return bos

    def _build_zones(self, df: pd.DataFrame, swing_highs, swing_lows, atr: pd.Series):
        n = len(df)
        pip_size = math_utils.get_pip_size(df)
        zone_low = np.full(n, np.nan)
        zone_high = np.full(n, np.nan)

        support = np.full(n, np.nan)
        resistance = np.full(n, np.nan)
        support_low = np.full(n, np.nan)
        support_high = np.full(n, np.nan)
        resistance_low = np.full(n, np.nan)
        resistance_high = np.full(n, np.nan)

        last_support = None
        last_resistance = None

        for i in range(n):
            strength_threshold = float(self.params.get("strength_threshold", 0.5))
            buffer = max(float(atr.iloc[i]) * (strength_threshold / 2.0), pip_size)

            if not np.isnan(swing_lows[i]):
                center = float(swing_lows[i])
                last_support = (center, center - buffer, center + buffer)
            if not np.isnan(swing_highs[i]):
                center = float(swing_highs[i])
                last_resistance = (center, center - buffer, center + buffer)

            if last_support is not None:
                support[i], support_low[i], support_high[i] = last_support
                zone_low[i] = support_low[i]
                zone_high[i] = support_high[i]
            if last_resistance is not None:
                resistance[i], resistance_low[i], resistance_high[i] = last_resistance

        return {
            "Support": support,
            "Support_Zone_Low": support_low,
            "Support_Zone_High": support_high,
            "Demand_Zone": support,
            "Demand_Zone_Low": support_low,
            "Demand_Zone_High": support_high,
            "Resistance": resistance,
            "Resistance_Zone_Low": resistance_low,
            "Resistance_Zone_High": resistance_high,
            "Supply_Zone": resistance,
            "Supply_Zone_Low": resistance_low,
            "Supply_Zone_High": resistance_high,
        }

    def _classify_candles(self, df: pd.DataFrame) -> pd.DataFrame:
        body = (df["close"] - df["open"]).abs()
        candle_range = (df["high"] - df["low"]).replace(0, 1e-6)

        df["Bullish"] = df["close"] > df["open"]
        df["Bearish"] = df["close"] < df["open"]
        df["Candle_Strength"] = (body / candle_range).clip(0.0, 1.0)
        df["Strong_Candle"] = df["Candle_Strength"] >= 0.7

        prev_open = df["open"].shift(1)
        prev_close = df["close"].shift(1)
        bullish_engulfing = (
            df["Bullish"]
            & (prev_close < prev_open)
            & (df["close"] > prev_open)
            & (df["open"] < prev_close)
        )
        bearish_engulfing = (
            df["Bearish"]
            & (prev_close > prev_open)
            & (df["close"] < prev_open)
            & (df["open"] > prev_close)
        )

        df["Bullish_Engulfing"] = bullish_engulfing.fillna(False)
        df["Bearish_Engulfing"] = bearish_engulfing.fillna(False)
        df["Engulfing"] = (bullish_engulfing | bearish_engulfing).fillna(False)
        return df

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Adds:
            - Swing_High / Swing_Low
            - Structure / Trend_Structure
            - BOS
            - Support / Resistance and zone bounds
            - Candle and engulfing features
        """

        df = df.copy()

        atr = self._atr(df)
        swing_highs, swing_lows = self._detect_confirmed_swings(df)
        structure_event, structure_strength = self._classify_structure(swing_highs, swing_lows, atr)
        zones = self._build_zones(df, swing_highs, swing_lows, atr)

        df["Swing_High"] = swing_highs
        df["Swing_Low"] = swing_lows
        df["Structure_Event"] = structure_event
        df["Structure_Strength"] = structure_strength
        df["Strong_Structure"] = df["Structure_Strength"].fillna(0.0) >= float(self.params.get("strength_threshold", 0.5))

        structure_series = pd.Series(structure_event, index=df.index, dtype=object)
        df["Structure"] = structure_series.ffill().fillna("Neutral")
        df["Trend_Structure"] = self._derive_trend(df["Structure"])

        df["BOS"] = self._detect_bos(df, swing_highs, swing_lows)

        for column, values in zones.items():
            df[column] = values
            df[column] = df[column].ffill()

        df = self._classify_candles(df)
        return df
