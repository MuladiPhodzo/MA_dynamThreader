from __future__ import annotations

import pandas as pd

from advisor.Strategy_model.patterns.pattern_base import SwingPattern
from advisor.Strategy_model.patterns.pattern_registry import PatternRegistry


@PatternRegistry.register("head_and_shoulders", "bearish")
class HeadAndShouldersPattern(SwingPattern):
    def __init__(self, tolerance: float = 0.003, min_distance: int = 2):
        super().__init__("head_and_shoulders", tolerance=tolerance, min_distance=min_distance)

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        swings = self._extract_swings(out)
        seq = self._last_n_swings(swings, 5)
        detected = None
        if seq and [s.kind for s in seq] == ["H", "L", "H", "L", "H"] and self._min_separation_ok(seq, int(self.params.__getitem__("min_distance"))):
            left_shoulder, _, head, _, right_shoulder = seq
            head_price = head.meta.close
            if head_price > left_shoulder.meta.close and head_price > right_shoulder.meta.close:
                shoulder_delta = abs(left_shoulder.meta.close - right_shoulder.meta.close) / max(head_price, 1e-9)
                if shoulder_delta <= float(self.params.__getitem__("tolerance")):
                    detected = {
                        "index": right_shoulder.index,
                        "label": "HeadAndShoulders",
                        "direction": -1,
                        "score": 0.95,
                        "confirmed": True,
                    }
        return self._write_summary(out, detected, "HeadAndShoulders")


@PatternRegistry.register("head_and_shoulders", "bullish")
class InverseHeadAndShouldersPattern(SwingPattern):
    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        swings = self._extract_swings(out)
        seq = self._last_n_swings(swings, 5)
        detected = None
        if seq and [s.kind for s in seq] == ["L", "H", "L", "H", "L"] and self._min_separation_ok(seq, int(self.params.__getitem__("min_distance"))):
            left_shoulder, _, head, _, right_shoulder = seq
            head_price = head.meta.close
            if head_price < left_shoulder.meta.close and head_price < right_shoulder.meta.close:
                shoulder_delta = abs(left_shoulder.meta.close - right_shoulder.meta.close) / max(abs(head_price), 1e-9)
                if shoulder_delta <= float(self.params.__getitem__("tolerance")):
                    detected = {
                        "index": right_shoulder.index,
                        "label": "InverseHeadAndShoulders",
                        "direction": 1,
                        "score": 0.95,
                        "confirmed": True,
                    }
        return self._write_summary(out, detected, "InverseHeadAndShoulders")
