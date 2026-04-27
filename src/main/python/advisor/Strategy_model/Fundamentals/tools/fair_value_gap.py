import numpy as np
import pandas as pd
from advisor.Strategy_model.Fundamentals.technical_base import Technical
from advisor.Strategy_model.Fundamentals.technical_registry import TechnicalRegistry
from utils import math_utils

@TechnicalRegistry.register("FVG", "imbalance")
class FairValueGap(Technical):
    """
    Detects Fair Value Gaps (FVG):
        - Bullish FVG (gap below price → buy retracement zone)
        - Bearish FVG (gap above price → sell retracement zone)

    Also tracks:
        - FVG fill status
        - Entry zones
    """
    # ---------------------------------------------------
    # FVG DETECTION (3-candle pattern)
    # ---------------------------------------------------
    def _detect_fvg(self, df: pd.DataFrame):
        min_gap_pips = self.params.get("min_gap_pips", self.params.get("min_gaps_pips", 5))
        min_gap = float(min_gap_pips) * math_utils.get_pip_size(df)
        high = df["high"].values
        low = df["low"].values

        fvg_type = [None] * len(df)
        fvg_high = np.full(len(df), np.nan)
        fvg_low = np.full(len(df), np.nan)

        for i in range(2, len(df)):
            # Candle 1 (i-2), Candle 2 (i-1), Candle 3 (i)

            # Bullish FVG → gap between high[i-2] and low[i]
            if low[i] > high[i - 2]:
                gap = low[i] - high[i - 2]
                if gap >= min_gap:
                    fvg_type[i] = "Bullish_FVG"
                    fvg_high[i] = low[i]
                    fvg_low[i] = high[i - 2]

            # Bearish FVG → gap between low[i-2] and high[i]
            elif high[i] < low[i - 2]:
                gap = low[i - 2] - high[i]
                if gap >= min_gap:
                    fvg_type[i] = "Bearish_FVG"
                    fvg_high[i] = low[i - 2]
                    fvg_low[i] = high[i]

        return fvg_type, fvg_high, fvg_low

    # ---------------------------------------------------
    # FVG FILL DETECTION
    # ---------------------------------------------------
    def _detect_fills(self, df, fvg_high, fvg_low):
        close = df["close"].values
        filled = [False] * len(df)

        for i in range(len(df)):
            if not np.isnan(fvg_high[i]) and not np.isnan(fvg_low[i]):
                zone_high = fvg_high[i]
                zone_low = fvg_low[i]

                # check future candles for fill
                for j in range(i + 1, len(df)):
                    if zone_low <= close[j] <= zone_high:
                        filled[i] = True
                        break

        return filled

    # ---------------------------------------------------
    # PUBLIC PIPELINE
    # ---------------------------------------------------
    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()

        fvg_type, fvg_high, fvg_low = self._detect_fvg(df)

        df["FVG"] = fvg_type
        df["FVG_High"] = fvg_high
        df["FVG_Low"] = fvg_low

        df["FVG_Filled"] = self._detect_fills(df, fvg_high, fvg_low)

        return df
