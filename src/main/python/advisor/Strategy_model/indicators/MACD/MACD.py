import pandas as pd
from advisor.Strategy_model.indicators.indicator_base import Indicator
from advisor.Strategy_model.indicators.registry import IndicatorRegistry

@IndicatorRegistry.register("MACD", "momentum")
class MACD(Indicator):
    """
    MACD Indicator

    Adds:
        - MACD
        - MACD_Signal
        - MACD_Hist
        - MACD_Trend (optional)
    """
    def compute(self, df: pd.DataFrame):
        df = df.copy()
        fast = self.params.get("fast_period", self.params.get("fast", 12))
        slow = self.params.get("slow_period", self.params.get("slow", 26))
        signal = self.params.get("signal_period", self.params.get("signal", 9))
        include_trend = self.params.get("include_trend", True)

        # EMAs
        ema_fast = df["close"].ewm(span=fast, adjust=False).mean()
        ema_slow = df["close"].ewm(span=slow, adjust=False).mean()

        # MACD components
        df["MACD"] = ema_fast - ema_slow
        df["MACD_Signal"] = df["MACD"].ewm(span=signal, adjust=False).mean()
        df["MACD_Hist"] = df["MACD"] - df["MACD_Signal"]

        if include_trend:
            df["MACD_Trend"] = df["MACD_Hist"].apply(
                lambda x: "Bullish" if x > 0 else "Bearish"
            )

        return df
