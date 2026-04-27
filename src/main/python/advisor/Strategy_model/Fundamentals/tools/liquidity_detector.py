import numpy as np
import pandas as pd

from advisor.Strategy_model.Fundamentals.technical_base import Technical
from advisor.Strategy_model.Fundamentals.technical_registry import TechnicalRegistry
from utils import math_utils

@TechnicalRegistry.register("Liquidity", "liquidity")
class LiquidityDetector(Technical):
    """
    Detects:
        - Equal Highs (buy-side liquidity)
        - Equal Lows (sell-side liquidity)
        - Stop Hunts (liquidity sweeps)
    """
    # ---------------------------------------------------
    # EQUAL HIGHS / LOWS
    # ---------------------------------------------------
    def _equal_highs_lows(self, df: pd.DataFrame):
        lookback = max(1, int(self.params.get("lookback", self.params.get("swing_window", 5))))
        tolerance_pips = float(self.params.get("tolerance_pips", self.params.get("tolerance", 2)))
        tolerance = max(tolerance_pips * math_utils.get_pip_size(df), math_utils.get_pip_size(df))

        highs = df["high"].values
        lows = df["low"].values

        eq_high = np.zeros(len(df), dtype=bool)
        eq_low = np.zeros(len(df), dtype=bool)

        for i in range(lookback, len(df)):
            window_highs = highs[i - lookback:i]
            window_lows = lows[i - lookback:i]

            # Equal High detection
            if np.any(np.abs(window_highs - highs[i]) <= tolerance):
                eq_high[i] = True

            # Equal Low detection
            if np.any(np.abs(window_lows - lows[i]) <= tolerance):
                eq_low[i] = True

        return eq_high, eq_low

    # ---------------------------------------------------
    # STOP HUNT (LIQUIDITY SWEEP)
    # ---------------------------------------------------
    def _detect_stop_hunts(self, df: pd.DataFrame, eq_high, eq_low):
        stop_hunt = [None] * len(df)

        high = df["high"].values
        low = df["low"].values
        close = df["close"].values

        for i in range(1, len(df)):

            # Sweep above equal highs then close below → bearish trap
            if eq_high[i - 1] and high[i] > high[i - 1] and close[i] < high[i - 1]:
                stop_hunt[i] = "Bearish_Sweep"

            # Sweep below equal lows then close above → bullish trap
            elif eq_low[i - 1] and low[i] < low[i - 1] and close[i] > low[i - 1]:
                stop_hunt[i] = "Bullish_Sweep"

        return stop_hunt

    # ---------------------------------------------------
    # PUBLIC PIPELINE
    # ---------------------------------------------------
    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()

        eq_high, eq_low = self._equal_highs_lows(df)

        df["Equal_High"] = eq_high
        df["Equal_Low"] = eq_low

        df["Liquidity"] = np.select(
            [eq_high, eq_low],
            ["Buy_Side_Liquidity", "Sell_Side_Liquidity"],
            default=None
        )

        df["Stop_Hunt"] = self._detect_stop_hunts(df, eq_high, eq_low)

        return df
