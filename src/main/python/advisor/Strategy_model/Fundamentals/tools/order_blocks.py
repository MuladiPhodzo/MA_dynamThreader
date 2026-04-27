import numpy as np
import pandas as pd
from advisor.Strategy_model.Fundamentals.technical_base import Technical
from advisor.Strategy_model.Fundamentals.technical_registry import TechnicalRegistry

@TechnicalRegistry.register("OBD", "orderflow")
class OrderBlockDetector(Technical):
    """
    Detects:
        - Bullish Order Blocks
        - Bearish Order Blocks
    """
    # ---------------------------------------------------
    # IMPULSE DETECTION
    # ---------------------------------------------------
    def _detect_impulse(self, df: pd.DataFrame):
        body = (df["close"] - df["open"]).abs()
        avg_body = body.rolling(20, min_periods=1).mean()

        impulse = body > (avg_body * float(self.params.get("impulse_threshold", 1.5)))
        return impulse

    # ---------------------------------------------------
    # ORDER BLOCK DETECTION
    # ---------------------------------------------------
    def _detect_order_blocks(self, df: pd.DataFrame, impulse):
        ob_type = [None] * len(df)
        ob_high = np.full(len(df), np.nan)
        ob_low = np.full(len(df), np.nan)

        open_ = df["open"].values
        close = df["close"].values
        high = df["high"].values
        low = df["low"].values

        for i in range(1, len(df)):

            # Bullish OB → last bearish candle before strong bullish move
            if impulse[i] and close[i] > open_[i]:
                if close[i - 1] < open_[i - 1]:
                    ob_type[i] = "Bullish_OB"
                    ob_high[i] = high[i - 1]
                    ob_low[i] = low[i - 1]

            # Bearish OB → last bullish candle before strong drop
            elif impulse[i] and close[i] < open_[i]:
                if close[i - 1] > open_[i - 1]:
                    ob_type[i] = "Bearish_OB"
                    ob_high[i] = high[i - 1]
                    ob_low[i] = low[i - 1]

        return ob_type, ob_high, ob_low

    # ---------------------------------------------------
    # PUBLIC PIPELINE
    # ---------------------------------------------------
    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()

        impulse = self._detect_impulse(df)

        ob_type, ob_high, ob_low = self._detect_order_blocks(df, impulse)

        df["OrderBlock"] = ob_type
        df["OB_High"] = ob_high
        df["OB_Low"] = ob_low

        return df
