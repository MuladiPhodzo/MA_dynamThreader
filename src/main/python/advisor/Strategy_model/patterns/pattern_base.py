from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import pandas as pd

@dataclass(frozen=True)
class CandleMeta:
    high: float
    open: float
    close: float
    low: float

@dataclass(frozen=True)
class SwingPoint:
    kind: str
    index: int
    meta: CandleMeta


class Pattern:
    def __init__(self, name: str, **params):
        self.name = name
        self.params = params or {}

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        raise NotImplementedError


class SwingPattern(Pattern):
    def _swing_columns(self, df: pd.DataFrame) -> tuple[str, str]:
        high_col = "Swing_High" if "Swing_High" in df.columns else "swing_high"
        low_col = "Swing_Low" if "Swing_Low" in df.columns else "swing_low"
        return high_col, low_col

    def _extract_swings(self, df: pd.DataFrame) -> list[SwingPoint]:
        high_col, low_col = self._swing_columns(df)
        swings: list[SwingPoint] = []
        for i in range(len(df)):
            if high_col in df.columns and pd.notna(df.iloc[i].get(high_col)):
                meta = CandleMeta(
                    high=float(df.iloc[i]["high"]),
                    open=float(df.iloc[i]["open"]),
                    close=float(df.iloc[i]["close"]),
                    low=float(df.iloc[i]["low"])
                )
                swings.append(SwingPoint("H", i, meta=meta))
            elif low_col in df.columns and pd.notna(df.iloc[i].get(low_col)):
                meta = CandleMeta(
                    high=float(df.iloc[i]["high"]),
                    open=float(df.iloc[i]["open"]),
                    close=float(df.iloc[i]["close"]),
                    low=float(df.iloc[i]["low"])
                )
                swings.append(SwingPoint("L", i, meta=meta))

        return swings

    @staticmethod
    def _last_n_swings(swings: list[SwingPoint], n: int) -> list[SwingPoint] | None:
        if len(swings) < n:
            return None
        return swings[-n:]

    @staticmethod
    def _min_separation_ok(swings: Iterable[SwingPoint], min_distance: int) -> bool:
        indices = [s.index for s in swings]
        if len(indices) < 2:
            return True
        return all((b - a) >= min_distance for a, b in zip(indices, indices[1:]))

    def _write_summary(
        self,
        df: pd.DataFrame,
        detected: dict | None,
        pattern_name: str,
    ) -> pd.DataFrame:
        out = df.copy()
        defaults = {
            "Pattern_Label": None,
            "Pattern_Direction": 0,
            "Pattern_Score": 0.0,
            "Pattern_Confirmed": False,
            f"Pattern_{pattern_name}": False,
        }
        for column, default in defaults.items():
            if column not in out.columns:
                out[column] = default
        if not detected:
            return out

        idx = detected.get("index")
        if isinstance(idx, int) and 0 <= idx < len(out):
            idx = out.index[idx]
        if idx is None or idx not in out.index:
            return out

        out.at[idx, f"Pattern_{pattern_name}"] = True
        out.at[idx, "Pattern_Label"] = detected.get("label")
        out.at[idx, "Pattern_Direction"] = detected.get("direction", 0)
        out.at[idx, "Pattern_Score"] = detected.get("score", 0.0)
        out.at[idx, "Pattern_Confirmed"] = bool(detected.get("confirmed", False))
        return out
