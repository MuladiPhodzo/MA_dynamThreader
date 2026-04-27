import pandas as pd


class FeatureBuilder:
    """
    Converts trading dataframe into ML-ready dataset.
    """

    def build(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()

        features = pd.DataFrame(index=df.index)

        # Trend
        features["ma_diff"] = df["Fast_MA"] - df["Slow_MA"]

        # Momentum
        features["ao"] = df.get("AO")
        features["macd"] = df.get("MACD")
        features["macd_hist"] = df.get("MACD_Hist")

        # Volatility
        features["atr"] = df.get("ATR")
        features["atr_ratio"] = df["ATR"] / df["ATR_Mean"]

        # Price action
        features["return"] = df["close"].pct_change()
        features["candle_body"] = (df["close"] - df["open"]).abs()

        # Distance metrics
        features["distance_ma"] = (df["close"] - df["Slow_MA"]).abs()

        # Target (supervised learning)
        features["target"] = df["PnL"].shift(-1)

        return features.dropna()
