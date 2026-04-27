import numpy as np
import pandas as pd


class ScoreEngine:
    """
    Multi-indicator weighted scoring engine.

    Output:
        Score in [-1, 1]
            +1 -> strong buy
            -1 -> strong sell
    """

    def __init__(self, weights: dict | None = None):
        self.weights = dict(weights) if weights else None
        self.last_feature_matrix: pd.DataFrame | None = None

    def compute(
        self,
        df: pd.DataFrame,
        features: pd.DataFrame | None = None,
    ) -> pd.Series:
        """
        Compute the weighted score for each row in a frame.
        """
        feature_matrix = features if features is not None else self.get_feature_matrix(df)
        if feature_matrix.empty:
            return pd.Series(0.0, index=df.index, name="Score", dtype=float)

        if self.weights:
            score = pd.Series(0.0, index=feature_matrix.index, dtype=float)
            for key, weight in self.weights.items():
                if key not in feature_matrix:
                    continue
                score = score.add(feature_matrix[key].fillna(0.0) * float(weight), fill_value=0.0)
        else:
            active = feature_matrix.fillna(0.0).astype(float)
            magnitude = active.abs()
            weight_sum = magnitude.sum(axis=1)
            dynamic_weights = magnitude.div(weight_sum.where(weight_sum != 0.0), axis=0).fillna(0.0)
            score = (active * dynamic_weights).sum(axis=1)

        return pd.Series(np.tanh(score), index=feature_matrix.index, name="Score")

    def get_feature_matrix(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Expose the normalized feature matrix for downstream ML or diagnostics.
        """
        features = {
            "ma_trend": self._ma_trend(df),
            "macd": self._macd_momentum(df),
            "ao": self._ao_momentum(df),
            "atr": self._volatility(df),
            "structure": self._market_structure(df),
            "fvg": self._fvg_signal(df),
            "order_block": self._order_block_signal(df),
            "pattern": self._pattern_signal(df),
        }
        matrix = pd.DataFrame(features, index=df.index).fillna(0.0).clip(-1.0, 1.0)
        self.last_feature_matrix = matrix
        return matrix

    def confidence(self, score: pd.Series | float) -> pd.Series | float:
        """
        Convert score magnitude into a percentage confidence.
        """
        if np.isscalar(score):
            return round(min(100.0, abs(float(score)) * 100.0), 2)

        series = pd.Series(score, copy=False)
        confidence = series.abs().clip(0.0, 1.0) * 100.0
        return confidence.round(2)

    def score_row(self, row) -> float:
        frame = pd.DataFrame([row])
        return float(self.compute(frame).iloc[0])

    def generate_signal(self, score: float) -> tuple[str, float]:
        confidence = float(self.confidence(score))
        if score >= 0.8:
            return "STRONG_BUY", confidence
        if score >= 0.3:
            return "BUY", confidence
        if score <= -0.8:
            return "STRONG_SELL", confidence
        if score <= -0.3:
            return "SELL", confidence
        return "NEUTRAL", confidence

    def _ma_trend(self, df: pd.DataFrame) -> pd.Series:
        if {"Fast_MA", "Slow_MA"}.issubset(df.columns):
            return self._normalize(df["Fast_MA"] - df["Slow_MA"])
        if "Bias" in df.columns:
            return self._map_direction(df["Bias"], {"Bullish": 1.0, "Bearish": -1.0})
        return self._zero(df)

    def _macd_momentum(self, df: pd.DataFrame) -> pd.Series:
        if "MACD_Hist" in df.columns:
            return self._normalize(df["MACD_Hist"])
        if "MACD" in df.columns:
            return self._normalize(df["MACD"])
        if "MACD_Trend" in df.columns:
            return self._map_direction(df["MACD_Trend"], {"Bullish": 1.0, "Bearish": -1.0})
        if "MACD_Signal" in df.columns and not self._is_numeric(df["MACD_Signal"]):
            return self._map_direction(df["MACD_Signal"], {"Bullish": 1.0, "Bearish": -1.0})
        return self._zero(df)

    def _ao_momentum(self, df: pd.DataFrame) -> pd.Series:
        if "AO" in df.columns:
            return self._normalize(df["AO"])
        if "AO_Signal" in df.columns and self._is_numeric(df["AO_Signal"]):
            return self._normalize(df["AO_Signal"])
        if "AO_Trend" in df.columns:
            return self._map_direction(df["AO_Trend"], {"Bullish": 1.0, "Bearish": -1.0})
        if "AO_Signal" in df.columns:
            return self._map_direction(df["AO_Signal"], {"Bullish": 1.0, "Bearish": -1.0})
        return self._zero(df)

    def _volatility(self, df: pd.DataFrame) -> pd.Series:
        if not {"ATR", "ATR_Mean"}.issubset(df.columns):
            return self._zero(df)
        expansion = self._normalize(df["ATR"] - df["ATR_Mean"])
        trend = self._ma_trend(df)
        direction = np.sign(trend).replace(0, 1.0)
        return expansion * direction

    def _market_structure(self, df: pd.DataFrame) -> pd.Series:
        if "Structure" in df.columns:
            mapping = {
                "HH": 1.0,
                "HL": 0.5,
                "LH": -0.5,
                "LL": -1.0,
            }
            return self._map_direction(df["Structure"], mapping)
        if "Trend_Structure" in df.columns:
            return self._map_direction(
                df["Trend_Structure"],
                {"Bullish": 1.0, "Bearish": -1.0, "Neutral": 0.0},
            )
        return self._zero(df)

    def _fvg_signal(self, df: pd.DataFrame) -> pd.Series:
        if "FVG" not in df.columns:
            return self._zero(df)
        series = df["FVG"]
        if self._is_numeric(series):
            return pd.Series(series, index=df.index, dtype=float).clip(-1.0, 1.0).fillna(0.0)
        mapped = self._map_direction(
            series,
            {"Bullish_FVG": 1.0, "Bearish_FVG": -1.0},
        )
        if "FVG_Filled" in df.columns:
            mapped = mapped.where(~df["FVG_Filled"].fillna(True), 0.0)
        return mapped

    def _order_block_signal(self, df: pd.DataFrame) -> pd.Series:
        if "OrderBlock" not in df.columns:
            return self._zero(df)
        series = df["OrderBlock"]
        if self._is_numeric(series):
            return pd.Series(series, index=df.index, dtype=float).clip(-1.0, 1.0).fillna(0.0)
        return self._map_direction(
            series,
            {"Bullish_OB": 1.0, "Bearish_OB": -1.0},
        )

    def _pattern_signal(self, df: pd.DataFrame) -> pd.Series:
        if "Pattern_Score" in df.columns and self._is_numeric(df["Pattern_Score"]):
            return pd.Series(df["Pattern_Score"], index=df.index, dtype=float).clip(-1.0, 1.0).fillna(0.0)

        for col in ("Pattern_Label", "Pattern"):
            if col in df.columns:
                return self._map_direction(
                    df[col],
                    {
                        "DoubleTop": -1.0,
                        "HeadAndShoulders": -1.0,
                        "Quasimodo_Bearish": -1.0,
                        "DoubleBottom": 1.0,
                        "InverseHeadAndShoulders": 1.0,
                        "Quasimodo_Bullish": 1.0,
                    },
                )
        return self._zero(df)

    def _normalize(self, series: pd.Series) -> pd.Series:
        numeric = pd.to_numeric(series, errors="coerce")
        if numeric.isna().all():
            return self._zero(series)
        std = float(numeric.std())
        if std == 0.0 or np.isnan(std):
            return self._zero(series)
        normalized = (numeric - numeric.mean()) / (std + 1e-9)
        return normalized.clip(-3.0, 3.0) / 3.0

    def _map_direction(self, series: pd.Series, mapping: dict[str, float]) -> pd.Series:
        return series.map(mapping).fillna(0.0).astype(float)

    def _zero(self, df_or_series) -> pd.Series:
        return pd.Series(0.0, index=df_or_series.index, dtype=float)

    @staticmethod
    def _is_numeric(series: pd.Series) -> bool:
        return pd.api.types.is_numeric_dtype(series)


class ScoringEngine(ScoreEngine):
    """
    Backward-compatible name for the strategy stack.
    """
