import pandas as pd
from advisor.Strategy_model.indicators.registry import IndicatorRegistry
from advisor.Strategy_model.indicators.indicator_base import Indicator

@IndicatorRegistry.register("ATR", "volatility")
class ATRIndicator(Indicator):
    def compute(self, df: pd.DataFrame):
        period = self.params.get("period", 14)
        mean_window = self.params.get("mean_window", 14)
        df = df.copy()

        tr = pd.concat([
            (df["high"] - df["low"]).abs(),
            (df["high"] - df["close"].shift(1)).abs(),
            (df["low"] - df["close"].shift(1)).abs(),
        ], axis=1).max(axis=1)

        df["ATR"] = tr.rolling(period, min_periods=1).mean().shift(1)
        df["ATR_Mean"] = df["ATR"].rolling(mean_window, min_periods=1).mean()

        return df
