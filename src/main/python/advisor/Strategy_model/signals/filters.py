import pandas as pd
from advisor.Strategy_model.signals.proximity import ProximitySignal


class SignalFilter:
    """
    Post-scoring validation layer.

    Ensures:
        - Minimum confidence/score
        - Trend alignment (structure + MA)
        - Momentum agreement (MACD + AO)
        - Optional smart-money confluence (FVG / OB / Liquidity)

    Output:
        df["Filtered"] = True / False
    """

    def __init__(
        self,
        rules: dict,
    ):
        self.min_confidence = rules.get("min_confidence")
        self.min_score = rules.get("min_score")
        self.require_trend_alignment = rules.get("require_trend_alignment", rules.get("require_mtf_alignment", True))
        self.require_momentum_agreement = rules.get("require_momentum_agreement", rules.get("require_momentum", True))
        self.require_smc_confluence = rules.get("require_smc_confluence", True)
        filters = rules.get("entry_filters") or {}
        if filters.get("enabled", True):
            self.proximty = ProximitySignal(filters) or None

    # ---------------------------------------------------
    # CORE FILTER
    # ---------------------------------------------------
    def apply(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        confidence = df["Confidence"] if "Confidence" in df.columns else df["Score"].abs() * 100

        # -------------------------
        # 1. BASE QUALITY FILTER
        # -------------------------
        valid = (
            (df["Score"].abs() >= self.min_score)
            & (confidence >= self.min_confidence)
        )

        # -------------------------
        # 2. TREND ALIGNMENT
        # -------------------------
        if self.require_trend_alignment:
            bias_bullish = self._series_eq(df, "Bias", "Bullish")
            bias_bearish = self._series_eq(df, "Bias", "Bearish")
            structure_bullish = self._series_isin(df, "Structure", ["HH", "HL", "HH", "HL"])
            structure_bearish = self._series_isin(df, "Structure", ["LH", "LL", "LH", "LL"])
            trend_ok = (
                # Bullish alignment
                (df["Score"] > 0)
                & (
                    bias_bullish
                    | structure_bullish
                )
            ) | (
                # Bearish alignment
                (df["Score"] < 0)
                & (
                    bias_bearish
                    | structure_bearish
                )
            )

            valid &= trend_ok.fillna(False)

        # -------------------------
        # 3. MOMENTUM CONFIRMATION
        # -------------------------
        if self.require_momentum_agreement:

            momentum_ok = (
                (df["Score"] > 0)
                & self._momentum_bullish(df, "MACD")
                & self._momentum_bullish(df, "AO")
            ) | (
                (df["Score"] < 0)
                & self._momentum_bearish(df, "MACD")
                & self._momentum_bearish(df, "AO")
            )

            valid &= momentum_ok.fillna(False)

        # -------------------------
        # 4. SMART MONEY CONFLUENCE
        # -------------------------
        if self.require_smc_confluence:
            stop_hunt_bull = self._series_eq(df, "Stop_Hunt", "Bullish_Sweep")
            stop_hunt_bear = self._series_eq(df, "Stop_Hunt", "Bearish_Sweep")
            ob_bull = self._series_eq(df, "OrderBlock", "Bullish_OB")
            ob_bear = self._series_eq(df, "OrderBlock", "Bearish_OB")
            fvg_bull = self._series_eq(df, "FVG", "Bullish_FVG")
            fvg_bear = self._series_eq(df, "FVG", "Bearish_FVG")
            fvg_filled = self._series_bool(df, "FVG_Filled", default=True)

            smc_ok = (
                # Bullish confluence
                (df["Score"] > 0)
                & (
                    stop_hunt_bull
                    | ob_bull
                    | (fvg_bull & ~fvg_filled)
                )
            ) | (
                # Bearish confluence
                (df["Score"] < 0)
                & (
                    stop_hunt_bear
                    | ob_bear
                    | (fvg_bear & ~fvg_filled)
                )
            )

            valid &= smc_ok.fillna(False)

        # -------------------------
        # FINAL OUTPUT
        # -------------------------
        df["Filtered"] = valid

        return df

    def _momentum_bullish(self, df: pd.DataFrame, prefix: str) -> pd.Series:
        return self._momentum_direction(df, prefix, bullish=True)

    def _momentum_bearish(self, df: pd.DataFrame, prefix: str) -> pd.Series:
        return self._momentum_direction(df, prefix, bullish=False)

    def _momentum_direction(self, df: pd.DataFrame, prefix: str, bullish: bool) -> pd.Series:
        candidates: list[pd.Series] = []
        hist_col = f"{prefix}_Hist"
        trend_col = f"{prefix}_Trend"
        signal_col = f"{prefix}_Signal"
        value_col = prefix

        if hist_col in df.columns:
            candidates.append(df[hist_col] > 0 if bullish else df[hist_col] < 0)

        if trend_col in df.columns:
            label = "Bullish" if bullish else "Bearish"
            candidates.append(df[trend_col] == label)

        if signal_col in df.columns:
            if pd.api.types.is_numeric_dtype(df[signal_col]):
                candidates.append(df[signal_col] > 0 if bullish else df[signal_col] < 0)
            else:
                label = "Bullish" if bullish else "Bearish"
                candidates.append(df[signal_col] == label)

        if value_col in df.columns:
            candidates.append(df[value_col] > 0 if bullish else df[value_col] < 0)

        if not candidates:
            return pd.Series(False, index=df.index)

        result = candidates[0].fillna(False)
        for candidate in candidates[1:]:
            result = result | candidate.fillna(False)
        return result

    @staticmethod
    def _series_eq(df: pd.DataFrame, column: str, value) -> pd.Series:
        if column not in df.columns:
            return pd.Series(False, index=df.index)
        return df[column].eq(value).fillna(False)

    @staticmethod
    def _series_isin(df: pd.DataFrame, column: str, values) -> pd.Series:
        if column not in df.columns:
            return pd.Series(False, index=df.index)
        return df[column].isin(values).fillna(False)

    @staticmethod
    def _series_bool(df: pd.DataFrame, column: str, default: bool = False) -> pd.Series:
        if column not in df.columns:
            return pd.Series(default, index=df.index)
        series = df[column].fillna(default)
        if pd.api.types.is_bool_dtype(series):
            return series
        return series.astype(bool)
