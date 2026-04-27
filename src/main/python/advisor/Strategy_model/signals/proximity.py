import numpy as np
import pandas as pd

from utils import math_utils


class ProximitySignal:

    def __init__(self, filters: dict):
        self.entry_filters: dict = filters.get("entry_filters") if "entry_filters" in filters else filters
        self.entry_filters = self.entry_filters or {}
        self.pip_distance = self.entry_filters.get("proximity", None)
        self.levls = self.entry_filters.get("levels")
        self.cooldown_bars = 3

    def generate(self, tf, df: pd.DataFrame):
        if "M" in tf:
            df = df.copy()
            self.pip_size = math_utils.get_pip_size(df)
            threshold = self.pip_distance * math_utils.get_pip_size(df)
            if self.pip_distance is not None:
                df.loc[:, 'Proximity'] = (df['close'] - df['Slow_MA']).abs() <= threshold

            prox = df["Proximity"].astype(bool)
            bullish = df["Bias"].str.contains("Bullish")
            bearish = df["Bias"].str.contains("Bearish")

            strong_bull = (df["Fast_Slope"] > 0) & (df["Slow_Slope"] > 0)
            strong_bear = (df["Fast_Slope"] < 0) & (df["Slow_Slope"] < 0)

            bull_candle = df["close"] > df["open"]
            bear_candle = df["close"] < df["open"]

            mask_buy = prox & bullish & strong_bull & bull_candle
            mask_sell = prox & bearish & strong_bear & bear_candle

            # cooldown
            candidate = (mask_buy | mask_sell).to_numpy()
            allow = np.zeros_like(candidate, dtype=bool)
            last = -999999

            for pos in np.flatnonzero(candidate):
                if pos - last > self.cooldown_bars:
                    allow[pos] = True
                    last = pos

            mask_buy &= allow
            mask_sell &= allow

            df.loc[mask_buy, "Entry"] = "Buy"
            df.loc[mask_sell, "Entry"] = "Sell"

            entry_price = df["close"]

            df.loc[mask_buy, "SL"] = entry_price - (self.pip_distance * self.pip_size)
            df.loc[mask_buy, "TP"] = entry_price + (3 * self.pip_distance * self.pip_size)

            df.loc[mask_sell, "SL"] = entry_price + (self.pip_distance * self.pip_size)
            df.loc[mask_sell, "TP"] = entry_price - (3 * self.pip_distance * self.pip_size)

            return df
