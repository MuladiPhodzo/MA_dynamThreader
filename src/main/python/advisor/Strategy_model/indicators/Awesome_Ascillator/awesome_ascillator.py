import pandas as pd
from advisor.Strategy_model.indicators.indicator_base import Indicator
from advisor.Strategy_model.indicators.registry import IndicatorRegistry

@IndicatorRegistry.register("AO", "momentum")
class AwesomeOscillator(Indicator):
    """
    Awesome Oscillator (AO)

    AO = SMA(5) of median price - SMA(34) of median price

    Adds:
        - AO
        - AO_Signal (optional trend direction)
    """
    def compute(self, df: pd.DataFrame):
        fast = self.params.get("fast", 5)
        slow = self.params.get("slow", 34)
        use_signal = self.params.get("use_signal", True)

        df = df.copy()

        if not {"high", "low"}.issubset(df.columns):
            raise ValueError("AO requires 'high' and 'low' columns")

        # Median price
        median_price = (df["high"] + df["low"]) / 2

        # AO calculation
        sma_fast = median_price.rolling(fast, min_periods=1).mean()
        sma_slow = median_price.rolling(slow, min_periods=1).mean()

        df["AO"] = sma_fast - sma_slow

        # Optional signal (momentum direction)
        if use_signal:
            df["AO_Signal"] = df["AO"].diff()
            df["AO_Trend"] = df["AO_Signal"].apply(
                lambda x: "Bullish" if x > 0 else "Bearish"
            )

        return df
