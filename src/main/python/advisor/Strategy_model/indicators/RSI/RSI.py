from advisor.Strategy_model.indicators.indicator_base import Indicator
from advisor.Strategy_model.indicators.registry import IndicatorRegistry


@IndicatorRegistry.register("RSI", "volatility")
class RSI(Indicator):
    def compute(self, df):
        period = self.params.get("period", 14)
        delta = df["close"].diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)

        avg_gain = gain.rolling(period).mean()
        avg_loss = loss.rolling(period).mean()

        rs = avg_gain / avg_loss
        df["RSI"] = 100 - (100 / (1 + rs))
        return df
