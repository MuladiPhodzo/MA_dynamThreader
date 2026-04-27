from __future__ import annotations

import pandas as pd

from advisor.Strategy_model.patterns.pattern_base import SwingPattern
from advisor.Strategy_model.patterns.pattern_registry import PatternRegistry


@PatternRegistry.register("quasimodo", "bearish")
class QuasimodoBearishPattern(SwingPattern):
    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        swings = self._extract_swings(out)
        seq = self._last_n_swings(swings, 5)
        detected = None
        if seq and [s.kind for s in seq] == ["H", "L", "H", "L", "H"] and self._min_separation_ok(seq, int(self.params.__getitem__("min_distance"))):
            h1, l1, h2, l2, h3 = seq
            if h2.meta.close > h1.meta.high and l2.meta.close < l1.meta.low and h3.meta.close < h2.meta.close:
                detected = {
                    "index": h3.index,
                    "label": "Quasimodo_Bearish",
                    "direction": -1,
                    "score": 0.9,
                    "confirmed": True,
                }
        return self._write_summary(out, detected, "Quasimodo_Bearish")


@PatternRegistry.register("quasimodo", "bullish")
class QuasimodoBullishPattern(SwingPattern):
    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        swings = self._extract_swings(out)
        seq = self._last_n_swings(swings, 5)
        detected = None
        if seq and [s.kind for s in seq] == ["L", "H", "L", "H", "L"] and self._min_separation_ok(seq, int(self.params.__getitem__("min_distance"))):
            l1, h1, l2, h2, l3 = seq
            if l2.meta.close < l1.meta.low and h2.meta.close > h1.meta.high and l3.meta.close > l2.meta.close:
                detected = {
                    "index": l3.index,
                    "label": "Quasimodo_Bullish",
                    "direction": 1,
                    "score": 0.9,
                    "confirmed": True,
                }
        return self._write_summary(out, detected, "Quasimodo_Bullish")
