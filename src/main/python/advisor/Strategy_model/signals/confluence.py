from __future__ import annotations

import numpy as np
import pandas as pd


class SMCFeatureEngine:
    """
    Derive measurable smart-money style features from OHLC data.

    The engine keeps the shape of the existing dataframes intact while adding
    reusable columns for liquidity, imbalance, order blocks, displacement, and
    engulfing timing.
    """

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()

        # 1. Liquidity
        df["Equal_Highs"] = df["high"].diff().abs() < 1e-5
        df["Equal_Lows"] = df["low"].diff().abs() < 1e-5
        df["Liquidity_Zone"] = df["Equal_Highs"] | df["Equal_Lows"]

        # 2. Liquidity sweep / stop hunt proxy
        prev_high = df["high"].shift(1)
        prev_low = df["low"].shift(1)
        df["Liquidity_Sweep_High"] = (df["high"] > prev_high) & (df["close"] < prev_high)
        df["Liquidity_Sweep_Low"] = (df["low"] < prev_low) & (df["close"] > prev_low)

        # 3. Imbalance
        df["FVG_Bullish"] = df["low"].shift(1) > df["high"].shift(2)
        df["FVG_Bearish"] = df["high"].shift(1) < df["low"].shift(2)

        # 4. Order block proxy
        df["Bullish_OB"] = (
            (df["close"].shift(1) < df["open"].shift(1))
            & (df["close"] > df["high"].shift(1))
        )
        df["Bearish_OB"] = (
            (df["close"].shift(1) > df["open"].shift(1))
            & (df["close"] < df["low"].shift(1))
        )

        # 5. Displacement / momentum burst
        body = (df["close"] - df["open"]).abs()
        candle_range = (df["high"] - df["low"]).replace(0, 1e-6)
        df["Displacement"] = (body / candle_range).clip(0.0, 1.0)
        df["Strong_Displacement"] = df["Displacement"] > 0.7

        # 6. Engulfing timing
        prev_open = df["open"].shift(1)
        prev_close = df["close"].shift(1)
        bullish_engulfing = (
            (df["close"] > df["open"])
            & (prev_close < prev_open)
            & (df["open"] <= prev_close)
            & (df["close"] >= prev_open)
        )
        bearish_engulfing = (
            (df["close"] < df["open"])
            & (prev_close > prev_open)
            & (df["open"] >= prev_close)
            & (df["close"] <= prev_open)
        )
        df["Bullish_Engulfing"] = bullish_engulfing
        df["Bearish_Engulfing"] = bearish_engulfing
        df["Engulfing"] = bullish_engulfing | bearish_engulfing

        # Keep compatibility with the existing filter and downstream code.
        df["Stop_Hunt"] = np.select(
            [df["Liquidity_Sweep_Low"], df["Liquidity_Sweep_High"]],
            ["Bullish_Sweep", "Bearish_Sweep"],
            default=None,
        )
        df["FVG"] = np.select(
            [df["FVG_Bullish"], df["FVG_Bearish"]],
            ["Bullish_FVG", "Bearish_FVG"],
            default=None,
        )
        df["OrderBlock"] = np.select(
            [df["Bullish_OB"], df["Bearish_OB"]],
            ["Bullish_OB", "Bearish_OB"],
            default=None,
        )
        if "FVG_Filled" not in df.columns:
            df["FVG_Filled"] = False

        return df


class ConfluenceEngine:
    """
    Factorized scoring engine for SMC-style context.
    """

    def __init__(self, config: dict | None = None):
        self.config = dict(config or {})
        self.enabled = bool(self.config.get("enabled", True))
        self.feature_weights = self.config.get("weights")
        self.role_weights = self.config.get("role_weights")
        self.gates = dict(self.config.get("gates", {}))
        self.roles = tuple(self.config.get("roles", ("HTF", "TREND", "BIAS", "ENTRY")))

    def score(self, features: dict[str, dict]) -> dict[str, object]:
        htf = features.get("HTF", {})
        trend = features.get("TREND", {})
        bias = features.get("BIAS", {})
        entry = features.get("ENTRY", {})

        if not self.enabled:
            return {
                "score": 0.0,
                "raw_score": 0.0,
                "passed": False,
                "alignment_score": 0.0,
                "reasons": [],
                "gate_reasons": ["confluence scoring disabled"],
            }

        if self.feature_weights:
            raw_score = self._score_with_weights(features, self.feature_weights)
        else:
            raw_score = self._dynamic_score(features)

        alignment_score = self.alignment_score(features)
        passed, gate_reasons = self._apply_gates(features, alignment_score)

        reasons = self._build_reasons(htf, trend, bias, entry, alignment_score)
        bounded = float(np.tanh(raw_score)) if passed else 0.0
        return {
            "score": round(bounded, 4),
            "raw_score": round(raw_score, 4),
            "passed": passed,
            "alignment_score": round(alignment_score, 4),
            "reasons": reasons,
            "gate_reasons": gate_reasons,
        }

    def alignment_score(self, features: dict[str, dict]) -> float:
        htf = self._direction(features.get("HTF", {}).get("trend", 0.0))
        trend = self._direction(features.get("TREND", {}).get("trend", 0.0))
        bias = self._direction(features.get("BIAS", {}).get("momentum", 0.0))

        comparisons = [(htf, trend), (trend, bias), (htf, bias)]
        matches = 0
        total = 0
        for left, right in comparisons:
            if left == 0 or right == 0:
                continue
            total += 1
            matches += int(left == right)

        if total == 0:
            return 0.0
        return matches / total

    def _score_with_weights(self, features: dict[str, dict], weights: dict) -> float:
        raw_score = 0.0

        if all(isinstance(value, dict) for value in weights.values()):
            for role, role_weights in weights.items():
                role_features = self._numeric_features(features.get(role, {}))
                if not role_features:
                    continue
                for feature_name, weight in role_weights.items():
                    raw_score += float(weight) * float(role_features.get(feature_name, 0.0))
            return raw_score

        flattened = self._flatten_features(features)
        for feature_name, weight in weights.items():
            raw_score += float(weight) * float(flattened.get(feature_name, 0.0))
        return raw_score

    def _dynamic_score(self, features: dict[str, dict]) -> float:
        active_roles = []
        for role in self.roles:
            role_features = self._numeric_features(features.get(role, {}))
            if sum(abs(value) for value in role_features.values()) > 0.0:
                active_roles.append(role)
        if not active_roles:
            return 0.0

        role_weights = self._resolve_role_weights(active_roles)
        raw_score = 0.0

        for role in active_roles:
            role_features = self._numeric_features(features.get(role, {}))
            if not role_features:
                continue

            role_mass = sum(abs(value) for value in role_features.values())
            if role_mass == 0.0:
                continue

            base_weight = role_weights.get(role, 0.0)
            for value in role_features.values():
                feature_weight = abs(value) / role_mass
                raw_score += base_weight * feature_weight * value

        return raw_score

    def _resolve_role_weights(self, active_roles: list[str]) -> dict[str, float]:
        if isinstance(self.role_weights, dict) and self.role_weights:
            weights = {
                role: max(0.0, float(self.role_weights.get(role, 0.0)))
                for role in active_roles
                if role in self.role_weights
            }
            total = sum(weights.values())
            if total > 0.0:
                return {role: weight / total for role, weight in weights.items()}

        equal = 1.0 / len(active_roles)
        return {role: equal for role in active_roles}

    def _apply_gates(self, features: dict[str, dict], alignment_score: float) -> tuple[bool, list[str]]:
        gates = self.gates
        reasons: list[str] = []

        min_alignment = float(gates.get("min_alignment", 0.67))
        if gates.get("require_alignment", True) and alignment_score < min_alignment:
            reasons.append(f"alignment below threshold ({alignment_score:.2f} < {min_alignment:.2f})")

        trend_strength = abs(float(features.get("TREND", {}).get("strength", 0.0)))
        min_trend_strength = gates.get("min_trend_strength")
        if min_trend_strength is not None and trend_strength < float(min_trend_strength):
            reasons.append(
                f"trend strength below threshold ({trend_strength:.2f} < {float(min_trend_strength):.2f})"
            )

        if gates.get("require_bias_engulfing", True) and not bool(features.get("BIAS", {}).get("engulfing", False)):
            reasons.append("bias engulfing confirmation missing")

        if gates.get("require_entry_trigger", True) and not bool(features.get("ENTRY", {}).get("trigger", False)):
            reasons.append("entry trigger missing")

        if gates.get("require_momentum_confirmation", True):
            trend_dir = self._direction(features.get("TREND", {}).get("trend", 0.0))
            bias_dir = self._direction(features.get("BIAS", {}).get("momentum", 0.0))
            if trend_dir != 0 and bias_dir != 0 and trend_dir != bias_dir:
                reasons.append("trend and bias momentum are not aligned")

        if gates.get("require_smc_context", True):
            htf = features.get("HTF", {})
            bias = features.get("BIAS", {})
            entry = features.get("ENTRY", {})
            context_ok = any(
                [
                    bool(htf.get("liquidity", 0.0)),
                    bool(htf.get("structure", 0.0)),
                    bool(htf.get("imbalance", 0.0)),
                    bool(bias.get("liquidity_sweep", 0.0)),
                    bool(entry.get("ob", 0.0)),
                    bool(entry.get("fvg", 0.0)),
                    bool(entry.get("pattern", 0.0)),
                    bool(entry.get("proximity", 0.0)),
                ]
            )
            if not context_ok:
                reasons.append("no actionable SMC context found")

        return not reasons, reasons

    @staticmethod
    def _build_reasons(htf: dict, trend: dict, bias: dict, entry: dict, alignment_score: float) -> list[str]:
        reasons: list[str] = []
        if htf.get("trend", 0.0):
            reasons.append("HTF context present")
        if htf.get("structure", 0.0):
            reasons.append("HTF structure confirmed")
        if htf.get("liquidity", 0.0):
            reasons.append("HTF liquidity map active")
        if trend.get("displacement", 0.0):
            reasons.append("Trend displacement confirmed")
        if trend.get("strength", 0.0):
            reasons.append("Trend strength measurable")
        if bias.get("momentum", 0.0):
            reasons.append("Bias momentum confirmed")
        if bias.get("liquidity_sweep", 0.0):
            reasons.append("Liquidity sweep present")
        if entry.get("ob", 0.0):
            reasons.append("Order block present")
        if entry.get("fvg", 0.0):
            reasons.append("FVG present")
        if entry.get("pattern", 0.0):
            reasons.append("Pattern confirmation present")
        if entry.get("proximity", 0.0) > 0:
            reasons.append("Price is near an actionable zone")
        if alignment_score:
            reasons.append(f"Alignment score {alignment_score:.2f}")
        return reasons

    @staticmethod
    def _flatten_features(features: dict[str, dict]) -> dict[str, float]:
        flattened: dict[str, float] = {}
        for role_features in features.values():
            flattened.update(ConfluenceEngine._numeric_features(role_features))
        return flattened

    @staticmethod
    def _numeric_features(feature_block: dict) -> dict[str, float]:
        numeric: dict[str, float] = {}
        for key, value in (feature_block or {}).items():
            if isinstance(value, (bool, np.bool_)):
                numeric[key] = 1.0 if value else 0.0
            elif isinstance(value, (int, float, np.integer, np.floating)) and not pd.isna(value):
                numeric[key] = float(value)
        return numeric

    @staticmethod
    def _direction(value) -> int:
        try:
            numeric = float(value)
        except Exception:
            return 0
        if numeric > 0:
            return 1
        if numeric < 0:
            return -1
        return 0

    @staticmethod
    def confidence(score: float) -> float:
        return round(min(100.0, abs(float(score)) * 100.0), 2)
