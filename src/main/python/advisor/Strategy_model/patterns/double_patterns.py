from __future__ import annotations

import math

import pandas as pd

from advisor.Strategy_model.patterns.pattern_base import SwingPattern
from advisor.Strategy_model.patterns.pattern_registry import PatternRegistry

@PatternRegistry.register("Double_Pattern", "bearish")
class DoubleTopPattern(SwingPattern):
    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        swings = self._extract_swings(out)
        seq = self._last_n_swings(swings, 3)
        detected = None
        if seq:
            a, b, c = seq
            if [s.kind for s in seq] == ["H", "L", "H"] and self._min_separation_ok(seq, int(self.params.__getitem__("min_distance"))):
                p1, p2, p3 = a.meta.close, b.meta.close, c.meta.close
                tol = self._effective_tolerance(out, max(p1, p3))
                depth = (min(p1, p3) - p2) / max(max(p1, p3), 1e-9)
                if abs(p1 - p3) / max(max(p1, p3), 1e-9) <= tol and depth >= float(self.params.__getitem__("retracement")):
                    detected = {
                        "index": c.index,
                        "label": "DoubleTop",
                        "direction": -1,
                        "score": 0.85,
                        "confirmed": True,
                    }
        return self._write_summary(out, detected, "DoubleTop")

    def _effective_tolerance(self, df: pd.DataFrame, reference_price: float) -> float:
        base = float(self.params.__getitem__("tolerance"))
        atr = pd.to_numeric(df.get("ATR", pd.Series(dtype=float)), errors="coerce").iloc[-1] if "ATR" in df.columns else math.nan
        if pd.notna(atr) and reference_price:
            base = max(base, float(atr) / max(reference_price, 1e-9) * 0.5)
        return base


@PatternRegistry.register("Double_Pattern", "bullish")
class DoubleBottomPattern(SwingPattern):
    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        swings = self._extract_swings(out)
        seq = self._last_n_swings(swings, 3)
        detected = None
        if seq:
            a, b, c = seq
            if [s.kind for s in seq] == ["L", "H", "L"] and self._min_separation_ok(seq, int(self.params.__getitem__("min_distance"))):
                p1, p2, p3 = a.meta.close, b.meta.close, c.meta.close
                tol = self._effective_tolerance(out, min(p1, p3))
                bounce = (p2 - max(p1, p3)) / max(max(p1, p3), 1e-9)
                if abs(p1 - p3) / max(max(abs(p1), abs(p3)), 1e-9) <= tol and bounce >= float(self.params.__getitem__("retracement")):
                    detected = {
                        "index": c.index,
                        "label": "DoubleBottom",
                        "direction": 1,
                        "score": 0.85,
                        "confirmed": True,
                    }
        return self._write_summary(out, detected, "DoubleBottom")

    def _effective_tolerance(self, df: pd.DataFrame, reference_price: float) -> float:
        base = float(self.params.__getitem__("tolerance"))
        if "ATR" in df.columns:
            atr = pd.to_numeric(df["ATR"], errors="coerce").iloc[-1]
            if pd.notna(atr) and reference_price:
                base = max(base, float(atr) / max(abs(reference_price), 1e-9) * 0.5)
        return base
