from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from advisor.Strategy_model.indicators.indicator_base import Indicator
from advisor.Strategy_model.indicators.registry import IndicatorRegistry
from advisor.utils.logging_setup import get_logger
from utils import date_utils, math_utils

logger = get_logger("_EMA_")


@IndicatorRegistry.register("MA", "trend")
class MA(Indicator):
    """
    Moving Average trend indicator.

    Responsibilities:
    - Normalize datetime index.
    - Calculate candle type.
    - Calculate fast/slow moving averages.
    - Calculate MA slopes.
    - Derive directional bias.
    - Preserve row count as much as possible.

    Output columns:
    - c_type
    - Fast_MA
    - Fast_Slope
    - Slow_MA
    - Slow_Slope
    - Bias
    """

    tf_meta = {
        "5M": 75,
        "15M": 100,
        "30M": 125,
        "1H": 150,
        "4H": 200,
        "8H": 250,
        "1D": 300,
    }

    REQUIRED_COLUMNS = {"open", "close"}
    NUMERIC_OUTPUT_COLUMNS = (
        "Fast_MA",
        "Fast_Slope",
        "Slow_MA",
        "Slow_Slope",
    )

    DEFAULT_FAST_PERIOD = 15
    DEFAULT_SLOW_PERIOD = 50

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate Fast/Slow MA and derived columns.

        Notes:
        - Does not aggressively drop rows.
        - Keeps MA outputs aligned to the same dataframe length.
        - Uses shifted MA values to avoid look-ahead bias.
        """
        logger.info("MA calc start")

        if df is None or df.empty:
            logger.warning("MA compute received empty dataframe")
            return pd.DataFrame() if df is None else df.copy()

        result = df.copy()

        result = self._normalize_index(result)
        result = self._coerce_required_numeric_columns(result)
        self._validate_input(result)

        self._refresh_pip_size(result)

        result = self._calculate_ma_indicators(result)
        result = self._apply_precision(result)

        # Keep only rows where close is missing. This protects downstream logic,
        # but avoids dropping early MA rows just because shifted/slope values are NaN.
        result = result.dropna(subset=["close"])

        self._log_completion(result)
        return result

    def _calculate_ma_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate candle type, moving averages, slopes, and directional bias."""
        result = df.copy()

        fast_period = self._get_positive_int_param("fast_period", self.DEFAULT_FAST_PERIOD)
        slow_period = self._get_positive_int_param("slow_period", self.DEFAULT_SLOW_PERIOD)

        if fast_period >= slow_period:
            logger.warning(
                "MA config unusual: fast_period=%s >= slow_period=%s",
                fast_period,
                slow_period,
            )

        close = result["close"]

        result["c_type"] = np.where(result["close"] > result["open"], "Bull", "Bear")

        # shift(1) prevents current candle close from influencing current MA signal.
        result["Fast_MA"] = (
            close.rolling(window=fast_period, min_periods=1)
            .mean()
            .shift(1)
        )
        result["Slow_MA"] = (
            close.rolling(window=slow_period, min_periods=1)
            .mean()
            .shift(1)
        )

        result["Fast_Slope"] = result["Fast_MA"].diff()
        result["Slow_Slope"] = result["Slow_MA"].diff()

        result["Bias"] = np.select(
            [
                result["Fast_MA"] > result["Slow_MA"],
                result["Fast_MA"] < result["Slow_MA"],
            ],
            ["Bullish", "Bearish"],
            default="Neutral",
        )

        return result

    def _normalize_index(self, df: pd.DataFrame) -> pd.DataFrame:
        """Normalize datetime index without failing the full indicator pipeline."""
        try:
            return date_utils._normalize_datetime_index(df)
        except ValueError as exc:
            logger.warning("Error normalizing datetime index: %s", exc)
            return df
        except Exception:
            logger.exception("Unexpected error normalizing datetime index")
            return df

    def _coerce_required_numeric_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """Ensure required price columns are numeric."""
        result = df.copy()
        for column in self.REQUIRED_COLUMNS:
            if column in result.columns:
                result[column] = pd.to_numeric(result[column], errors="coerce")
        return result

    def _validate_input(self, df: pd.DataFrame) -> None:
        """Fail fast when required columns are missing."""
        missing = self.REQUIRED_COLUMNS.difference(df.columns)
        if missing:
            raise ValueError(f"MA indicator missing required columns: {sorted(missing)}")

    def _refresh_pip_size(self, df: pd.DataFrame) -> None:
        """Refresh pip size using existing project utility without breaking MA calc."""
        try:
            math_utils._refresh_pip_size(getattr(self, "name", "MA"), df)
        except Exception:
            logger.exception("Failed to refresh pip size for MA indicator")

    def _apply_precision(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Apply existing project precision utility safely.

        Falls back to local rounding if math_utils._apply_precision fails.
        """
        try:
            return math_utils._apply_precision(df)
        except Exception:
            logger.exception("math_utils._apply_precision failed; applying local fallback rounding")
            result = df.copy()
            for column in self.NUMERIC_OUTPUT_COLUMNS:
                if column in result.columns:
                    result[column] = result[column].round(6)
            return result

    def _get_positive_int_param(self, key: str, default: int) -> int:
        """Read a positive integer indicator param safely."""
        raw_value: Any = getattr(self, "params", {}).get(key, default)
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            logger.warning("Invalid MA param %s=%r; using default=%s", key, raw_value, default)
            return default

        if value <= 0:
            logger.warning("MA param %s must be positive; using default=%s", key, default)
            return default

        return value

    def _log_completion(self, df: pd.DataFrame) -> None:
        try:
            if isinstance(df.index, pd.DatetimeIndex) and not df.empty:
                logger.info(
                    "MA calc done rows=%d range=%s..%s",
                    len(df),
                    df.index.min(),
                    df.index.max(),
                )
            else:
                logger.info("MA calc done rows=%d", len(df))
        except Exception:
            logger.info("MA calc done")
