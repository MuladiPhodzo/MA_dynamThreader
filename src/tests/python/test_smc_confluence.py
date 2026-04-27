import pandas as pd

from advisor.Strategy_model.Fundamentals.tools.market_structure import MarketStructure  # noqa: F401
from advisor.Strategy_model.indicators.MA.MovingAverage import MA  # noqa: F401
from advisor.Strategy_model.Fundamentals.technical_registry import TechnicalRegistry
from advisor.Strategy_model.indicators.registry import IndicatorRegistry
from advisor.Strategy_model.patterns.pattern_registry import PatternRegistry
from advisor.Strategy_model.signals.confluence import ConfluenceEngine, SMCFeatureEngine
from advisor.Strategy_model.signals.filters import SignalFilter
from advisor.Strategy_model.strategy import StrategyModel


def test_smc_feature_engine_derives_liquidity_engulfing_and_ob_signals():
    engine = SMCFeatureEngine()
    df = pd.DataFrame(
        {
            "open": [1.00, 0.99, 1.02, 0.99],
            "high": [1.01, 1.01, 1.03, 1.06],
            "low": [0.97, 0.97, 0.99, 0.98],
            "close": [0.98, 1.00, 1.00, 1.05],
        }
    )

    out = engine.compute(df)

    assert bool(out.loc[1, "Liquidity_Zone"]) is True
    assert bool(out.loc[2, "Liquidity_Sweep_High"]) is True
    assert bool(out.loc[3, "Bullish_OB"]) is True
    assert bool(out.loc[3, "Bullish_Engulfing"]) is True
    assert bool(out.loc[3, "Engulfing"]) is True
    assert bool(out.loc[3, "Strong_Displacement"]) is True


def test_market_structure_confirms_swings_without_lookahead_and_keeps_state_dense():
    tool = MarketStructure(swing_window=2, zone_window=3, strength_threshold=1.5)
    df = pd.DataFrame(
        {
            "open": [1.0, 2.0, 2.6, 1.8, 1.5, 1.2, 1.3],
            "high": [1.5, 2.5, 5.0, 2.9, 1.9, 1.8, 2.1],
            "low": [0.8, 1.5, 1.9, 0.5, 1.0, 0.9, 1.1],
            "close": [1.2, 2.1, 2.0, 2.8, 1.4, 1.3, 1.9],
        }
    )

    out = tool.compute(df)

    assert pd.isna(out.loc[2, "Swing_High"])
    assert out.loc[4, "Swing_High"] == 5.0
    assert out.loc[4, "Structure_Event"] == "HH?"
    assert out.loc[5, "Structure"] != "Neutral"
    assert out.loc[6, "Trend_Structure"] == out.loc[5, "Trend_Structure"]
    assert out.loc[5, "Support_Zone_High"] >= out.loc[5, "Support_Zone_Low"]
    assert out.loc[5, "Supply_Zone_High"] >= out.loc[5, "Supply_Zone_Low"]
    assert bool(out.loc[3, "Engulfing"]) is True


def test_confluence_engine_scores_bullish_context_and_produces_reasons():
    engine = ConfluenceEngine()
    features = {
        "HTF": {"trend": 1.0, "structure": 1.0, "liquidity": 1.0, "imbalance": 1.0},
        "TREND": {"trend": 1.0, "strength": 0.9, "displacement": 1.0},
        "BIAS": {"momentum": 1.0, "engulfing": True, "liquidity_sweep": 1.0},
        "ENTRY": {"trigger": True, "ob": 1.0, "fvg": 1.0, "proximity": 0.8},
    }

    result = engine.score(features)

    assert result["score"] > 0
    assert result["raw_score"] > 0
    assert result["reasons"]
    assert engine.confidence(result["score"]) > 0


def test_strategy_timing_filter_requires_engulfing_and_trigger():
    model = StrategyModel.__new__(StrategyModel)

    assert model._timing_filter(
        {
            "BIAS": {"engulfing": True},
            "ENTRY": {"trigger": True},
        }
    )
    assert not model._timing_filter(
        {
            "BIAS": {"engulfing": False},
            "ENTRY": {"trigger": True},
        }
    )


def test_signal_filter_can_require_smc_confluence_with_new_columns():
    df = pd.DataFrame(
        {
            "Score": [0.85],
            "Confidence": [85.0],
            "Bias": ["Bullish"],
            "Structure": ["HH"],
            "MACD_Hist": [0.4],
            "AO": [0.3],
            "Stop_Hunt": ["Bullish_Sweep"],
            "OrderBlock": ["Bullish_OB"],
            "FVG": ["Bullish_FVG"],
            "FVG_Filled": [False],
        }
    )

    filtered = SignalFilter(require_smc_confluence=True).apply(df)

    assert filtered["Filtered"].tolist() == [True]


def test_registries_attach_tool_types_and_params():
    indicator_registry = IndicatorRegistry({"ma": {"fast": 5, "slow": 20}})
    indicator_registry.build()

    technical_registry = TechnicalRegistry({"market_structure": {"swing_window": 3}})
    technical_registry.build()

    assert indicator_registry.instances["ma"]._type == "trend"
    assert indicator_registry.instances["ma"].params == {"fast": 5, "slow": 20}
    assert technical_registry.instances["market_structure"]._type == "structure"
    assert technical_registry.instances["market_structure"].params == {"swing_window": 3}


def test_pattern_registry_labels_double_top_from_confirmed_swings():
    registry = PatternRegistry(
        {
            "double_top": {
                "tolerance": 0.01,
                "min_distance": 1,
                "retracement": 0.2,
            }
        }
    )
    registry.build()

    df = pd.DataFrame(
        {
            "high": [1.0, 2.0, 1.1, 2.01, 0.9],
            "low": [0.5, 1.2, 0.4, 1.15, 0.3],
            "Swing_High": [None, 2.0, None, 2.01, None],
            "Swing_Low": [None, None, 1.0, None, None],
        }
    )

    out = registry.compute(df)

    assert bool(out.loc[3, "Pattern_DoubleTop"]) is True
    assert out.loc[3, "Pattern_Label"] == "DoubleTop"
    assert out.loc[3, "Pattern_Direction"] == -1
    assert bool(out.loc[3, "Pattern_Confirmed"]) is True
