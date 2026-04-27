import pandas as pd

from advisor.Strategy_model.signals.filters import SignalFilter
from advisor.Strategy_model.signals.score_engine import ScoringEngine


def test_scoring_engine_returns_bounded_scores_and_feature_matrix():
    engine = ScoringEngine()
    df = pd.DataFrame(
        {
            "Fast_MA": [1.00, 1.02, 1.04, 1.06],
            "Slow_MA": [1.00, 1.01, 1.02, 1.03],
            "MACD_Hist": [-0.2, -0.05, 0.05, 0.2],
            "AO": [-0.3, -0.1, 0.1, 0.3],
            "ATR": [0.8, 0.9, 1.1, 1.3],
            "ATR_Mean": [1.0, 1.0, 1.0, 1.0],
            "Structure": ["LL", "LH", "HL", "HH"],
            "FVG": [None, "Bullish_FVG", None, "Bullish_FVG"],
            "FVG_Filled": [False, False, False, False],
            "OrderBlock": [None, None, "Bullish_OB", "Bullish_OB"],
            "Pattern_Label": [None, None, "DoubleBottom", "InverseHeadAndShoulders"],
            "Pattern_Score": [0.0, 0.0, 0.7, 0.9],
        }
    )

    features = engine.get_feature_matrix(df)
    score = engine.compute(df, features=features)
    confidence = engine.confidence(score)

    assert list(features.columns) == [
        "ma_trend",
        "macd",
        "ao",
        "atr",
        "structure",
        "fvg",
        "order_block",
        "pattern",
    ]
    assert score.between(-1.0, 1.0).all()
    assert confidence.between(0.0, 100.0).all()
    assert score.iloc[-1] > score.iloc[0]
    assert features["pattern"].iloc[-1] > 0


def test_signal_filter_accepts_continuous_scores_and_numeric_momentum():
    df = pd.DataFrame(
        {
            "Score": [0.65, -0.72],
            "Confidence": [65.0, 72.0],
            "Bias": ["Bullish", "Bearish"],
            "Structure": ["HH", "LL"],
            "MACD_Hist": [0.4, -0.5],
            "AO": [0.3, -0.2],
        }
    )

    filtered = SignalFilter().apply(df)

    assert filtered["Filtered"].tolist() == [True, True]
