from advisor.core.portfolio.portfolio_manager import PortfolioManager


def test_portfolio_manager_ranks_and_allocates_best_signals():
    manager = PortfolioManager(
        capital=10000,
        risk_per_trade=0.01,
        max_positions=2,
        max_symbol_exposure=0.2,
    )

    manager.add_signal(
        "EURUSD",
        {"direction": "Buy", "confidence": 82.5, "metadata": {"score": 0.9}},
    )
    manager.add_signal(
        "GBPUSD",
        {"direction": "Sell", "confidence": 75.0, "metadata": {"score": -0.7}},
    )
    manager.add_signal(
        "USDJPY",
        {"direction": "Buy", "confidence": 55.0, "metadata": {"score": 0.4}},
    )

    trades = manager.build_portfolio()

    assert [trade["symbol"] for trade in trades] == ["EURUSD", "GBPUSD"]
    assert trades[0]["position_size"] == 82.5
    assert trades[1]["position_size"] == 75.0
    assert all(trade["risk_amount"] == 100.0 for trade in trades)


def test_portfolio_manager_uses_atr_style_position_sizing_when_available():
    manager = PortfolioManager(capital=10000, risk_per_trade=0.01, max_positions=1)
    manager.add_signal(
        "EURUSD",
        {
            "direction": "Buy",
            "confidence": 60.0,
            "metadata": {"score": 0.8, "pip_value": 2.0, "sl_distance": 25.0},
        },
    )

    trades = manager.build_portfolio()

    assert trades[0]["position_size"] == 2.0
