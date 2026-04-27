import math

import numpy as np
import pandas as pd

@staticmethod
def _apply_precision(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalize all price-related columns to match pip precision.
    Ensures numerical consistency across OHLC + indicators.
    """
    try:
        precision = abs(int(np.log10(math)))
    except Exception:
        precision = 5  # safe fallback

    cols = ["open", "high", "low", "close", "Fast_MA", "Slow_MA"]

    for col in cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").round(precision)
    return df

@staticmethod
def comp(data_set):
    count = 0
    if data_set["Slow_MA"] > data_set["Fast_MA"]:
        count -= 1
    else:
        count += 1
    return count

@staticmethod
def _clean_value(v):
    """Convert numpy types or pandas types to native Python."""
    if hasattr(v, "item"):
        return v.item()
    return float(v) if isinstance(v, (np.floating, np.float64)) else v

@staticmethod
def _refresh_pip_size(symbol: str, df: pd.DataFrame) -> None | float:
    try:
        if df is None or not isinstance(df, pd.DataFrame) or "close" not in df.columns:
            return
        if df["close"].dropna().empty:
            return
        return get_pip_size(df)
    except Exception:
        raise ValueError(f"Failed to determine pip size for {symbol}")

@staticmethod
def get_pip_size(df: pd.DataFrame) -> float:
    """Auto-detect pip size from number of decimals in price."""
    price = float(df["close"].dropna().iloc[-1])
    price_str = str(price)
    if "." in price_str:
        decimals = len(price_str.split(".")[1])
        # 2 or 3 decimals → JPY pairs (0.01, 0.001)
        # 4 or 5 decimals → normal pairs (0.0001, 0.00001)
        if decimals in [2, 3]:
            return 0.01
        elif decimals in [4, 5]:
            return 0.0001
    return 0.0001  # fallback