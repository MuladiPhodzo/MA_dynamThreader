from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Iterable

import pandas as pd

from advisor.Strategy_model.indicators.registry import IndicatorRegistry, load_builtin_indicators
from advisor.Strategy_model.patterns.pattern_registry import PatternRegistry
from advisor.Strategy_model.signals.score_engine import ScoringEngine
from advisor.Strategy_model.signals.filters import SignalFilter
from advisor.Strategy_model.signals.decider import SignalDecision
from advisor.Strategy_model.signals.confluence import ConfluenceEngine, SMCFeatureEngine
from advisor.Strategy_model.Fundamentals.technical_registry import TechnicalRegistry
from advisor.utils.logging_setup import get_logger
from utils import dataHandler
from utils.cache_handler import CacheManager

logger = get_logger("Strategy")


@dataclass
class TFState:
    """Runtime state for one logical timeframe role: HTF, TREND, BIAS, ENTRY."""

    RAW_CANDLE_KEYS = {
        "open",
        "high",
        "low",
        "close",
        "time",
        "timestamp",
        "datetime",
        "volume",
        "tick_volume",
        "spread",
        "real_volume",
    }

    frame: pd.DataFrame = field(default_factory=pd.DataFrame)
    features: dict[str, Any] = field(default_factory=dict)
    last_candle: dict[str, Any] | None = None
    buffer_size: int = 250

    def append_candle(self, candle: dict[str, Any]) -> pd.DataFrame:
        normalized = self._normalize_candle(candle)
        if not normalized:
            return self.frame

        row = pd.DataFrame([normalized])
        row.index = pd.Index([self._extract_index(normalized, len(self.frame))])

        self.frame = pd.concat([self.frame, row], axis=0)
        self.frame = self._deduplicate_frame(self.frame)
        self.frame = self._trim(self.frame)
        self.last_candle = normalized
        return self.frame

    def seed(self, frame: pd.DataFrame | None) -> None:
        if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
            self.frame = pd.DataFrame()
            self.features = {}
            self.last_candle = None
            return

        self.frame = self._trim(frame.copy())
        self.last_candle = self._row_to_candle(self.frame.iloc[-1])

    def _trim(self, frame: pd.DataFrame) -> pd.DataFrame:
        if self.buffer_size and len(frame) > self.buffer_size:
            return frame.tail(self.buffer_size).copy()
        return frame.copy()

    @classmethod
    def _deduplicate_frame(cls, frame: pd.DataFrame) -> pd.DataFrame:
        if frame.empty:
            return frame
        return frame[~frame.index.duplicated(keep="last")].sort_index()

    @classmethod
    def _normalize_candle(cls, candle: dict[str, Any] | None) -> dict[str, Any]:
        normalized: dict[str, Any] = {}
        for key, value in (candle or {}).items():
            lowered = str(key).lower()
            if lowered in cls.RAW_CANDLE_KEYS:
                normalized[lowered] = value
        return normalized

    @staticmethod
    def _extract_index(candle: dict[str, Any], fallback: int) -> Any:
        for key in ("timestamp", "datetime", "time"):
            value = candle.get(key)
            if value is None:
                continue
            if isinstance(value, pd.Timestamp):
                return value
            if isinstance(value, (int, float)):
                unit = "ms" if value > 1e12 else "s"
                parsed = pd.to_datetime(value, unit=unit, errors="coerce")
                if pd.notna(parsed):
                    return parsed
                continue
            parsed = pd.to_datetime(value, errors="coerce")
            if pd.notna(parsed):
                return parsed
        return fallback

    @classmethod
    def _row_to_candle(cls, row: pd.Series | None) -> dict[str, Any]:
        if row is None:
            return {}

        candle: dict[str, Any] = {}
        for key in cls.RAW_CANDLE_KEYS:
            if key not in row.index:
                continue
            value = row.get(key)
            if pd.notna(value):
                candle[key] = value.item() if hasattr(value, "item") else value
        return candle


class IndicatorEngine:
    """Incremental indicator adapter for live/event-driven updates."""

    def update(self, strategy: "StrategyModel", state: TFState, role: str, candle: dict[str, Any]) -> pd.DataFrame:
        state.append_candle(candle)
        if state.frame.empty:
            return state.frame

        frame = strategy._apply_indicators_by_role(role, state.frame)
        state.frame = frame.tail(state.buffer_size).copy()
        return state.frame


class SMCEventEngine:
    """Incremental SMC feature adapter."""

    REQUIRED_OHLC = {"open", "high", "low", "close"}

    def __init__(self, engine: SMCFeatureEngine | None = None) -> None:
        self.engine = engine or SMCFeatureEngine()

    def update(self, state: TFState) -> pd.DataFrame:
        if state.frame is None or state.frame.empty:
            return state.frame
        if not self.REQUIRED_OHLC.issubset(state.frame.columns):
            return state.frame

        computed = self.engine.compute(state.frame)
        state.frame = StrategyModel._merge_computed_frame(state.frame, computed).tail(state.buffer_size).copy()
        return state.frame


class FeatureEngine:
    """Build and persist per-role feature dictionaries."""

    def build(self, strategy: "StrategyModel", states: dict[str, TFState]) -> dict[str, dict[str, Any]]:
        source_data = {role: state.frame for role, state in states.items()}
        features = strategy._build_features(source_data=source_data)
        for role, state in states.items():
            state.features = features.get(role, {})
        return features


class RealTimeConfluence:
    def __init__(self, engine: ConfluenceEngine) -> None:
        self.engine = engine
        self.last_result: dict[str, Any] = {}

    def compute(self, states: dict[str, TFState]) -> float:
        features = {role: state.features for role, state in states.items()}
        self.last_result = self.engine.score(features)
        return float(self.last_result.get("score", 0.0))


class StrategyModel:
    """Master strategy orchestrator backbone for batch and live trading modes."""

    TF_SEQUENCE = ("HTF", "TREND", "BIAS", "ENTRY")

    TF_TYPE_FILTER = {
        "HTF": ["trend", "structure", "liquidity", "imbalance", "pattern"],
        "TREND": ["trend", "momentum", "structure", "imbalance", "pattern"],
        "BIAS": ["trend", "momentum", "liquidity", "imbalance", "orderflow", "pattern"],
        "ENTRY": ["trend", "momentum", "volatility", "structure", "liquidity", "imbalance", "orderflow", "pattern"],
    }

    DEFAULT_CONFIG = {
        "name": "EMA_Proxim8te",
        "timeframes": {
            "HTF": "1D",
            "TREND": "4H",
            "BIAS": "1H",
            "ENTRY": "15M",
        },
        "tools": {
            "indicators": {
                "enabled": True,
                "params": {
                    "ma": {"fast_period": 50, "slow_period": 200},
                    "macd": {"fast": 12, "slow": 26, "signal": 9, "include_trend": True},
                    "ao": {"fast": 5, "slow": 34},
                    "atr": {"period": 14},
                },
            },
            "technical": {
                "enabled": True,
                "tools": ["fvg", "market structure", "liquidity"],
                "params": {
                    "swing_window": 5,
                    "min_gap_pips": 5,
                    "lookback": 5,
                    "tolerance": 2,
                    "zone_window": 5,
                    "strength_threshold": 0.5,
                    "impulse_threshold": 1.5,
                },
            },
            "patterns": {
                "enabled": True,
                "patterns": ["quasimodo", "head_and_shoulders", "double_pattern"],
                "params": {"tolerance": 0.003, "min_distance": 2, "retracement": 0.25},
            },
        },
        "confluence": {
            "enabled": True,
            "gates": {
                "require_alignment": True,
                "min_alignment": 0.67,
                "min_trend_strength": 0.5,
                "require_bias_engulfing": True,
                "require_entry_trigger": True,
                "require_momentum_confirmation": True,
                "require_smc_context": True,
            },
        },
        "live": {"enabled": False, "buffer_size": 250},
        "rules": {
            "min_score": 0.65,
            "min_confidence": 50,
            "entry_filters": {
                "enabled": True,
                "proximity": 200,
                "levels": 10,
            },
            "require_mtf_alignment": True,
            "require_momentum": True,
            "require_smc_confluence": True,
        },
        "risk": {
            "high_frequency_trade": {"enabled": True, "max_trades": 5},
            "sl_atr_mult": 1.5,
            "tp_atr_mult": 3.0,
        },
    }

    def __init__(self, symbol: str, cacheManager: CacheManager, config: dict | None = None) -> None:
        self.symbol = symbol
        self.config = copy.deepcopy(config) if config else copy.deepcopy(self.DEFAULT_CONFIG)
        self.strategy_name = self.config.get("name", self.DEFAULT_CONFIG["name"])

        self.data_handler = dataHandler.DataHandler(symbol, self.strategy_name, cacheManager)
        self.data = self.data_handler.data
        self.TF_MAP = self._normalize_timeframes(self.config.get("timeframes", self.DEFAULT_CONFIG["timeframes"]))

        self._normalize_tool_config()
        self.technical_tools = None
        self.indicator_registry = None
        self.pattern_registry = None
        self._initialize_tools()

        self.smc_engine = SMCFeatureEngine()
        self.confluence_config = self._build_confluence_config()
        self.confluence_engine = ConfluenceEngine(self.confluence_config)
        self.scoring_engine = ScoringEngine()

        self.live_config = copy.deepcopy(self.config.get("live", {}))
        self.live_mode = bool(self.live_config.get("enabled", False))
        self.live_buffer_size = self._safe_positive_int(self.live_config.get("buffer_size"), 250)
        self.tf_states = {role: TFState(buffer_size=self.live_buffer_size) for role in self.TF_SEQUENCE}
        self.prev_candles = {role: None for role in self.TF_SEQUENCE}

        self.indicator_engine = IndicatorEngine()
        self.smc_event_engine = SMCEventEngine(self.smc_engine)
        self.feature_engine = FeatureEngine()
        self.realtime_confluence = RealTimeConfluence(self.confluence_engine)

        rules = self._effective_rules(copy.deepcopy(self.config.get("rules", {})))
        self.filter = SignalFilter(rules=rules)
        self.decider = SignalDecision()
        self.last_feature_matrix: pd.DataFrame | None = None

        if self.live_mode:
            self._seed_live_states(reset=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def __call__(self, backtest: bool = False) -> dict | None:
        return self.run(backtest=backtest)

    def run(self, backtest: bool = False) -> dict | None:
        if self.live_mode:
            return self._run_live_snapshot()
        return self._run_batch(backtest=backtest)

    def enable_live_mode(self) -> None:
        self.live_mode = True
        self.live_config["enabled"] = True
        self._seed_live_states(reset=False)

    def disable_live_mode(self) -> None:
        self.live_mode = False
        self.live_config["enabled"] = False

    def on_new_candle(self, tf: str, candle: dict[str, Any]) -> dict | None:
        """Event-driven entry point for live mode."""
        if not self.live_mode:
            logger.warning("Use enable_live_mode() before on_new_candle()")
            return None

        role = self._resolve_tf_role(tf)
        if role is None:
            logger.warning("Unknown timeframe/role received: %s", tf)
            return None

        normalized = TFState._normalize_candle(candle)
        if not normalized:
            return None

        if self._same_candle(self.prev_candles.get(role), normalized):
            return None

        state = self.tf_states[role]
        self.indicator_engine.update(self, state, role, normalized)
        self.smc_event_engine.update(state)
        self.prev_candles[role] = dict(normalized)

        if role != "ENTRY":
            self.feature_engine.build(self, self.tf_states)
            return None

        features = self.feature_engine.build(self, self.tf_states)
        return self._build_signal_from_entry_frame(state.frame, features, mode="live")

    # ------------------------------------------------------------------
    # Batch pipeline
    # ------------------------------------------------------------------

    def _run_batch(self, backtest: bool = False) -> dict | None:
        try:
            logger.info("running strategy %s for %s", self.strategy_name, self.symbol)
            self._process_timeframes(backtest=backtest)
            self._apply_smc_features()

            entry_df = self._resolve_frame("ENTRY")
            if entry_df is None or entry_df.empty:
                return None

            features = self._build_features()
            entry_df = self._score(entry_df, features)
            entry_df = self._filter(entry_df)
            self._store_role_frame("ENTRY", entry_df)

            return self._decide(entry_df, features)
        except Exception:
            logger.exception("Strategy pipeline failed")
            return None

    def _process_timeframes(self, backtest: bool = False) -> None:
        logger.info("Processing timeframes sequentially")
        for role in self.TF_SEQUENCE:
            df = self._resolve_frame(role)
            if df is None or not isinstance(df, pd.DataFrame) or df.empty:
                continue

            window = None if backtest else self.live_buffer_size
            df_input = df.copy() if window is None else df.tail(window).copy()
            processed = self._apply_indicators_by_role(role, df_input)
            merged = self._merge_computed_frame(df, processed)
            self._store_role_frame(role, merged)

    def _apply_smc_features(self) -> None:
        for tf, df in list(self.data.items()):
            if df is None or not isinstance(df, pd.DataFrame) or df.empty:
                continue
            computed_df = self.smc_engine.compute(df)
            self.data[tf] = self._merge_computed_frame(df, computed_df)

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------

    def _apply_indicators_by_role(self, role: str, df: pd.DataFrame) -> pd.DataFrame:
        allowed_types = self.TF_TYPE_FILTER.get(role, [])
        df_out = df.copy()

        for name, tool in self._iter_tools():
            tool_type = getattr(tool, "_type", None)
            if tool_type not in allowed_types:
                continue
            try:
                result = tool.compute(df_out)
                df_out = self._merge_tool_output(df_out, result, name)
            except Exception:
                logger.exception("%s -> %s failed", role, name)

        return df_out

    # Backward-compatible method name used by your current code.
    def _apply_indicators_by_tf(self, tf_name: str, df: pd.DataFrame) -> pd.DataFrame:
        return self._apply_indicators_by_role(tf_name, df)

    def _iter_tools(self) -> Iterable[tuple[str, Any]]:
        if self.indicator_registry is not None:
            yield from self.indicator_registry.instances.items()
        if self.technical_tools is not None:
            yield from self.technical_tools.instances.items()
        if self.pattern_registry is not None and getattr(self.pattern_registry, "instances", None):
            yield "patterns", self.pattern_registry

    @staticmethod
    def _merge_tool_output(df_out: pd.DataFrame, result: Any, tool_name: str) -> pd.DataFrame:
        if isinstance(result, pd.DataFrame):
            if result.empty:
                return df_out

            merged = df_out.copy()
            for column in result.columns:
                merged.loc[result.index, column] = result[column]
            return merged

        merged = df_out.copy()
        merged[tool_name] = result
        return merged

    @staticmethod
    def _merge_computed_frame(original: pd.DataFrame, computed: pd.DataFrame | None) -> pd.DataFrame:
        if computed is None or computed.empty:
            return original

        merged = original.copy()
        for column in computed.columns:
            merged.loc[computed.index, column] = computed[column]
        return merged

    # ------------------------------------------------------------------
    # Scoring / filtering / decision
    # ------------------------------------------------------------------

    def _score(self, df: pd.DataFrame, features: dict[str, dict[str, Any]] | None = None) -> pd.DataFrame:
        df = df.copy()
        if df.empty:
            return df

        features = features or self._build_features()
        legacy_matrix = self.scoring_engine.get_feature_matrix(df)
        self.last_feature_matrix = legacy_matrix
        legacy_score = self.scoring_engine.compute(df, features=legacy_matrix)

        confluence = self.confluence_engine.score(features)
        passed = bool(confluence.get("passed", False))
        final_score = float(confluence.get("score", 0.0)) if passed else 0.0
        final_confidence = self.confluence_engine.confidence(final_score)

        df["Legacy_Score"] = legacy_score
        df["Score"] = legacy_score
        df["Confidence"] = self.scoring_engine.confidence(df["Score"])

        last_idx = df.index[-1]
        df.loc[last_idx, "Score"] = final_score
        df.loc[last_idx, "Confidence"] = final_confidence
        df.loc[last_idx, "Confluence_Passed"] = passed
        df.loc[last_idx, "Confluence_Alignment"] = float(confluence.get("alignment_score", 0.0))
        df.loc[last_idx, "Confluence_Raw"] = confluence.get("raw_score", 0.0)
        df.loc[last_idx, "Confluence_Reasons"] = " | ".join(confluence.get("reasons", []))
        df.loc[last_idx, "Confluence_Gates"] = " | ".join(confluence.get("gate_reasons", []))
        return df

    def _filter(self, df: pd.DataFrame) -> pd.DataFrame:
        return self.filter.apply(df)

    def _decide(self, entry_df: pd.DataFrame, features: dict[str, dict[str, Any]] | None = None) -> dict | None:
        features = features or self._build_features()
        if not self._timing_filter(features):
            return None

        return self._build_signal_from_entry_frame(entry_df, features, mode="batch")

    def _build_signal_from_entry_frame(
        self,
        entry_df: pd.DataFrame,
        features: dict[str, dict[str, Any]],
        mode: str,
    ) -> dict | None:
        if entry_df is None or entry_df.empty:
            return None

        latest = entry_df.iloc[-1]
        if not latest.get("Filtered", False):
            return None
        if not bool(latest.get("Confluence_Passed", mode == "live")):
            return None

        score = float(latest.get("Score", 0.0))
        if score == 0.0:
            return None

        direction = "Buy" if score > 0 else "Sell"
        confidence = self._compute_confidence(score)
        metadata = self._build_signal_metadata(latest, features, score, mode)

        return self.decider.build_signal(
            direction=direction,
            confidence=confidence,
            metadata=metadata,
            frame=entry_df.tail(1),
        )

    def _build_signal_metadata(
        self,
        latest: pd.Series,
        features: dict[str, dict[str, Any]],
        score: float,
        mode: str,
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "mode": mode,
            "score": score,
            "alignment": float(latest.get("Confluence_Alignment", 0.0)),
            "timing": {
                "engulfing": bool(features.get("BIAS", {}).get("engulfing", False)),
                "trigger": bool(features.get("ENTRY", {}).get("trigger", False)),
            },
            "confluence": latest.get("Confluence_Reasons"),
            "confluence_gates": latest.get("Confluence_Gates"),
        }

        htf_row = self._latest_row("HTF")
        trend_row = self._latest_row("TREND")
        bias_row = self._latest_row("BIAS")

        if htf_row is not None:
            metadata["structure"] = self._get_structure(htf_row)
        if trend_row is not None:
            metadata["trend"] = self._get_trend(trend_row)
        if bias_row is not None:
            metadata["bias"] = bias_row.get("Bias", "Neutral")

        if mode == "live":
            metadata["features"] = {role: state.features for role, state in self.tf_states.items()}

        return metadata

    # ------------------------------------------------------------------
    # Feature extraction
    # ------------------------------------------------------------------

    def _build_features(self, source_data: dict[str, pd.DataFrame] | None = None) -> dict[str, dict[str, Any]]:
        source_data = source_data or self.data
        return {
            "HTF": self._htf_features(source_data),
            "TREND": self._trend_features(source_data),
            "BIAS": self._bias_features(source_data),
            "ENTRY": self._entry_features(source_data),
        }

    def _latest_row(self, timeframe: str, source_data: dict[str, pd.DataFrame] | None = None) -> pd.Series | None:
        df = self._resolve_frame(timeframe, source_data)
        if df is None or not isinstance(df, pd.DataFrame) or df.empty:
            return None
        return df.iloc[-1]

    def _htf_features(self, source_data: dict[str, pd.DataFrame] | None = None) -> dict[str, float]:
        row = self._latest_row("HTF", source_data)
        if row is None:
            return {"trend": 0.0, "structure": 0.0, "liquidity": 0.0, "imbalance": 0.0}

        return {
            "trend": self._direction_from_row(row, "Trend_Structure", "Structure"),
            "structure": self._structure_direction(row),
            "liquidity": 1.0 if bool(row.get("Liquidity_Zone", False) or row.get("Equal_Highs", False) or row.get("Equal_Lows", False)) else 0.0,
            "imbalance": self._imbalance_direction(row),
        }

    def _trend_features(self, source_data: dict[str, pd.DataFrame] | None = None) -> dict[str, float]:
        row = self._latest_row("TREND", source_data)
        if row is None:
            return {"trend": 0.0, "strength": 0.0, "displacement": 0.0}

        fast = row.get("Fast_MA")
        slow = row.get("Slow_MA")
        if pd.notna(fast) and pd.notna(slow):
            fast_f = self._safe_float(fast)
            slow_f = self._safe_float(slow)
            trend = 1.0 if fast_f > slow_f else -1.0 if fast_f < slow_f else 0.0
            strength = min(abs(fast_f - slow_f) / (abs(slow_f) + 1e-6), 1.0)
        else:
            trend = self._structure_direction(row) or self._direction_from_row(row, "Trend_Structure", "Structure")
            strength = abs(self._safe_float(row.get("Structure_Strength", 0.0)))
            if strength == 0.0 and trend != 0.0:
                strength = 1.0

        displacement = 1.0 if bool(row.get("Strong_Displacement", False) or row.get("Displacement", 0.0) > 0.7) else 0.0
        return {"trend": trend, "strength": strength, "displacement": displacement}

    def _bias_features(self, source_data: dict[str, pd.DataFrame] | None = None) -> dict[str, float | bool]:
        row = self._latest_row("BIAS", source_data)
        if row is None:
            return {"momentum": 0.0, "engulfing": False, "liquidity_sweep": 0.0}

        bullish_engulfing = bool(row.get("Bullish_Engulfing", False))
        bearish_engulfing = bool(row.get("Bearish_Engulfing", False))
        momentum = 1.0 if bullish_engulfing else -1.0 if bearish_engulfing else 0.0

        liquidity_sweep = 0.0
        if bool(row.get("Liquidity_Sweep_Low", False)) or row.get("Stop_Hunt") == "Bullish_Sweep":
            liquidity_sweep = 1.0
        elif bool(row.get("Liquidity_Sweep_High", False)) or row.get("Stop_Hunt") == "Bearish_Sweep":
            liquidity_sweep = -1.0

        return {
            "momentum": momentum,
            "engulfing": bool(row.get("Engulfing", bullish_engulfing or bearish_engulfing)),
            "liquidity_sweep": liquidity_sweep,
        }

    def _entry_features(self, source_data: dict[str, pd.DataFrame] | None = None) -> dict[str, float | bool]:
        row = self._latest_row("ENTRY", source_data)
        if row is None:
            return {"trigger": False, "ob": 0.0, "fvg": 0.0, "pattern": 0.0, "proximity": 0.0}

        pattern_direction = self._safe_float(row.get("Pattern_Direction", 0.0))
        pattern_score = self._safe_float(row.get("Pattern_Score", 0.0))

        return {
            "trigger": bool(row.get("Engulfing", False)),
            "ob": self._direction_from_row(row, "OrderBlock", fallback_positive="Bullish_OB", fallback_negative="Bearish_OB"),
            "fvg": self._direction_from_row(row, "FVG", fallback_positive="FVG_Bullish", fallback_negative="FVG_Bearish"),
            "pattern": pattern_direction * (pattern_score if pattern_score else 1.0),
            "proximity": self._real_proximity(row),
        }

    def _timing_filter(self, features: dict[str, dict[str, Any]]) -> bool:
        gates = self.confluence_config.get("gates", {})
        bias = features.get("BIAS", {})
        entry = features.get("ENTRY", {})

        if gates.get("require_bias_engulfing", True) and not bias.get("engulfing", False):
            return False
        if gates.get("require_entry_trigger", True) and not entry.get("trigger", False):
            return False
        return True

    # ------------------------------------------------------------------
    # Live state helpers
    # ------------------------------------------------------------------

    def _run_live_snapshot(self) -> dict | None:
        if not self.live_mode:
            return None

        signal = None
        for role in self.TF_SEQUENCE:
            frame = self._resolve_frame(role)
            if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
                continue
            candle = TFState._row_to_candle(frame.iloc[-1])
            if candle:
                result = self.on_new_candle(role, candle)
                if result is not None:
                    signal = result
        return signal

    def _seed_live_states(self, reset: bool = False) -> None:
        source_data = self.data_handler.data if hasattr(self.data_handler, "data") else self.data

        for role in self.TF_SEQUENCE:
            state = self.tf_states.get(role)
            if state is None:
                continue
            if not reset and not state.frame.empty:
                continue

            frame = self._resolve_frame(role, source_data)
            if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
                if reset:
                    state.seed(None)
                    self.prev_candles[role] = None
                continue

            seed = frame.tail(self.live_buffer_size).copy()
            seed = self._apply_indicators_by_role(role, seed)
            seed = self.smc_engine.compute(seed)
            state.seed(seed)
            self.prev_candles[role] = dict(state.last_candle) if state.last_candle else None

        self.feature_engine.build(self, self.tf_states)

    # ------------------------------------------------------------------
    # Config / initialization
    # ------------------------------------------------------------------

    def _initialize_tools(self) -> None:
        tools_config = self.config.get("tools", {})
        try:
            technicals = tools_config.get("technicals") or tools_config.get("technical") or {}
            if technicals.get("enabled"):
                self.technical_tools = TechnicalRegistry(technicals)
                self.technical_tools.build()

            indicators = tools_config.get("indicators") or {}
            if indicators.get("enabled"):
                load_builtin_indicators()
                self.indicator_registry = IndicatorRegistry(indicators)
                self.indicator_registry.build()

            patterns = tools_config.get("patterns") or {}
            if patterns.get("enabled"):
                self.pattern_registry = PatternRegistry(patterns)
                self.pattern_registry.build()
                self.pattern_registry._type = "pattern"
                self.pattern_registry._name = "patterns"
        except Exception:
            logger.exception("strategy %s failed to initialise tools", self.strategy_name)

    def _normalize_tool_config(self) -> None:
        tools = self.config.setdefault("tools", {})
        if "technical" not in tools and "technicals" in tools:
            tools["technical"] = tools["technicals"]

        technical = tools.get("technical")
        if isinstance(technical, dict):
            params = technical.setdefault("params", {})
            swing_window = params.setdefault("swing_window", 5)
            min_gap = params.get("min_gap_pips", params.get("min_gaps_pips", 5))
            params.setdefault("min_gap_pips", min_gap)
            params.setdefault("min_gaps_pips", min_gap)
            params.setdefault("lookback", swing_window)
            params.setdefault("tolerance", 2)
            params.setdefault("zone_window", swing_window)
            params.setdefault("strength_threshold", 0.5)
            params.setdefault("impulse_threshold", 1.5)

        patterns = tools.get("patterns")
        if isinstance(patterns, dict):
            params = patterns.setdefault("params", {})
            params.setdefault("tolerance", 0.003)
            params.setdefault("min_distance", 2)
            params.setdefault("retracement", 0.25)

    def _build_confluence_config(self) -> dict[str, Any]:
        config = self._effective_confluence_config(copy.deepcopy(self.config.get("confluence", {})))
        legacy_weights = self.config.get("confluence_weights")
        if legacy_weights and "weights" not in config:
            config["weights"] = legacy_weights
        return config

    def _effective_rules(self, rules: dict[str, Any]) -> dict[str, Any]:
        indicators = self._indicator_names()
        has_entry_alignment = "ma" in indicators or self._has_technical("market structure")
        has_momentum_pair = {"macd", "ao"}.issubset(indicators)

        if not has_entry_alignment:
            rules["require_mtf_alignment"] = False
            rules["require_trend_alignment"] = False
        if not has_momentum_pair:
            rules["require_momentum"] = False
            rules["require_momentum_agreement"] = False
        return rules

    def _effective_confluence_config(self, config: dict[str, Any]) -> dict[str, Any]:
        gates = config.setdefault("gates", {})
        has_trend_source = "ma" in self._indicator_names() or self._has_technical("market structure")
        if not has_trend_source:
            gates["require_alignment"] = False
            gates["min_trend_strength"] = None
        return config

    @staticmethod
    def _normalize_timeframes(timeframes: dict[str, str]) -> dict[str, str]:
        normalized = dict(timeframes or {})
        if normalized.get("ENTRY") == "15":
            normalized["ENTRY"] = "15M"
        return normalized

    def _indicator_names(self) -> set[str]:
        indicators = self.config.get("tools", {}).get("indicators", {})
        if not isinstance(indicators, dict) or not indicators.get("enabled"):
            return set()
        return {str(name).lower() for name in (indicators.get("params") or {}).keys()}

    def _technical_names(self) -> set[str]:
        technical = self.config.get("tools", {}).get("technical", {})
        if not isinstance(technical, dict) or not technical.get("enabled"):
            return set()
        return {TechnicalRegistry._normalize_name(name) for name in (technical.get("tools") or [])}

    def _has_technical(self, name: str) -> bool:
        return TechnicalRegistry._normalize_name(name) in self._technical_names()

    # ------------------------------------------------------------------
    # Frame resolution / storage
    # ------------------------------------------------------------------

    def _resolve_frame(
        self,
        timeframe: str,
        source_data: dict[str, pd.DataFrame] | None = None,
    ) -> pd.DataFrame | None:
        data = source_data if source_data is not None else self.data
        if not isinstance(data, dict):
            return None

        if timeframe in data:
            return data.get(timeframe)

        mapped = self.TF_MAP.get(timeframe)
        if mapped and mapped in data:
            return data.get(mapped)

        for role, tf_name in self.TF_MAP.items():
            if str(tf_name).upper() == str(timeframe).upper():
                return data.get(tf_name) or data.get(role)

        return None

    def _store_role_frame(self, role: str, frame: pd.DataFrame) -> None:
        mapped = self.TF_MAP.get(role, role)
        self.data[mapped] = frame

    def _resolve_tf_role(self, tf: str) -> str | None:
        candidate = str(tf).upper()
        if candidate in self.tf_states:
            return candidate

        for role, tf_name in self.TF_MAP.items():
            if candidate == str(tf_name).upper():
                return role
        return None

    # ------------------------------------------------------------------
    # Direction / proximity helpers
    # ------------------------------------------------------------------

    def _direction_from_row(
        self,
        row: pd.Series,
        primary: str,
        secondary: str | None = None,
        fallback_positive: str | None = None,
        fallback_negative: str | None = None,
    ) -> float:
        bullish_values = {"Bullish", "Bullish_FVG", "Bullish_OB", "Bullish_Sweep", "HH", "HL"}
        bearish_values = {"Bearish", "Bearish_FVG", "Bearish_OB", "Bearish_Sweep", "LH", "LL"}

        primary_value = row.get(primary)
        if primary_value in bullish_values:
            return 1.0
        if primary_value in bearish_values:
            return -1.0

        if secondary is not None:
            secondary_value = row.get(secondary)
            if secondary_value in bullish_values:
                return 1.0
            if secondary_value in bearish_values:
                return -1.0

        if fallback_positive and bool(row.get(fallback_positive, False)):
            return 1.0
        if fallback_negative and bool(row.get(fallback_negative, False)):
            return -1.0
        return 0.0

    def _structure_direction(self, row: pd.Series) -> float:
        structure = row.get("Structure") or row.get("Trend_Structure")
        if structure in {"HH", "HL", "Bullish"}:
            return 1.0
        if structure in {"LH", "LL", "Bearish"}:
            return -1.0
        return 0.0

    def _imbalance_direction(self, row: pd.Series) -> float:
        if bool(row.get("FVG_Bullish", False)) or row.get("FVG") == "Bullish_FVG":
            return 1.0
        if bool(row.get("FVG_Bearish", False)) or row.get("FVG") == "Bearish_FVG":
            return -1.0
        return 0.0

    def _real_proximity(self, row: pd.Series) -> float:
        close = self._safe_float(row.get("close", 0.0))
        atr = self._safe_float(row.get("ATR", 0.0))
        scale = max(atr, abs(close) * 0.001, 1e-6)

        zones: list[tuple[float, float]] = []
        for low_key, high_key in (("OB_Low", "OB_High"), ("FVG_Low", "FVG_High")):
            low = row.get(low_key)
            high = row.get(high_key)
            if pd.notna(low) and pd.notna(high):
                low_f = float(min(low, high))
                high_f = float(max(low, high))
                zones.append((low_f, high_f))

        if not zones:
            return 0.0

        min_distance = min(
            0.0 if low <= close <= high else min(abs(close - low), abs(close - high))
            for low, high in zones
        )
        proximity = max(0.0, 1.0 - (min_distance / scale))
        return round(min(proximity, 1.0), 4)

    def _get_structure(self, row: pd.Series) -> str:
        direction = self._structure_direction(row)
        if direction > 0:
            return "Bullish"
        if direction < 0:
            return "Bearish"
        return "Neutral"

    def _get_trend(self, row: pd.Series) -> str:
        fast = row.get("Fast_MA")
        slow = row.get("Slow_MA")
        if pd.notna(fast) and pd.notna(slow):
            if fast > slow:
                return "Bullish"
            if fast < slow:
                return "Bearish"

        structure = self._get_structure(row)
        if structure != "Neutral":
            return structure

        bias = row.get("Bias")
        if bias in {"Bullish", "Bearish"}:
            return bias
        return "Neutral"

    def _compute_confidence(self, score: float) -> float:
        return self.confluence_engine.confidence(score)

    # ------------------------------------------------------------------
    # Generic helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _same_candle(previous: dict[str, Any] | None, current: dict[str, Any] | None) -> bool:
        if not previous or not current:
            return False
        for key in TFState.RAW_CANDLE_KEYS:
            prev_value = previous.get(key)
            curr_value = current.get(key)
            if pd.isna(prev_value) and pd.isna(curr_value):
                continue
            if prev_value != curr_value:
                return False
        return True

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            if value is None or pd.isna(value):
                return default
            return float(value)
        except Exception:
            return default

    @staticmethod
    def _safe_positive_int(value: Any, default: int) -> int:
        try:
            parsed = int(value)
            return parsed if parsed > 0 else default
        except Exception:
            return default

    @staticmethod
    def _clone_data(data: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
        return {tf: df.copy() for tf, df in (data or {}).items() if isinstance(df, pd.DataFrame)}

    @staticmethod
    def _get_pip_size(df: pd.DataFrame) -> float:
        try:
            price = float(df["close"].dropna().iloc[-1])
        except Exception:
            return 0.0001

        price_str = str(price)
        if "." in price_str:
            decimals = len(price_str.split(".")[1])
            if decimals in (2, 3):
                return 0.01
        return 0.0001


# Backward compatibility for older backtest imports.
Strategy = StrategyModel
