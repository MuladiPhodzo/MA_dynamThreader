from typing import Iterable

import numpy as np
import pandas as pd

from advisor.utils.logging_setup import get_logger

logger = get_logger(__name__)

@staticmethod
def _normalize_datetime_index(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize DataFrame index to DatetimeIndex."""
    if isinstance(df.index, pd.DatetimeIndex):
        logger.info("Datetime index verified.")
        return df

    try:
        if 'time' in df.columns:
            if pd.api.types.is_numeric_dtype(df['time']):
                df['time'] = pd.to_datetime(df['time'], unit='s', errors='coerce')
            else:
                df['time'] = pd.to_datetime(df['time'], errors='coerce')
            df.set_index('time', inplace=True)
        else:
            df.index = pd.to_datetime(df.index, errors='coerce')

        df = df[~df.index.isna()]
        df.sort_index(inplace=True)
    except Exception:
        logger.warning("Failed to normalize datetime index")
        raise ValueError("Failed to normalize datetime index")
    return df

@staticmethod
def _ensure_schema(df: pd.DataFrame) -> pd.DataFrame:
    required = [
        "Entry", "SL", "TP",
        "ExitPrice", "ExitIndex",
        "Outcome", "PnL_Pips"
    ]

    for col in required:
        if col not in df.columns:
            if col in ["Entry", "Outcome"]:
                df[col] = None
            else:
                df[col] = np.nan
    return df

@staticmethod
def verify_fields(tf: str, df: pd.DataFrame, fields: Iterable = ["open", "high", "low", "close", "Fast_MA", "Slow_MA", "Proximity"]) -> bool:
    """Verify required fields for a given timeframe."""
    for field in fields:
        if field not in df.columns:
            logger.warning(f"{tf} missing required field: {field}")
            return False
    return True
