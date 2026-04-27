from datetime import datetime, timezone

from advisor.core.state import BotState, StateManager
from Strategy_model.signals.signal_store import SignalStore


def test_signal_store_round_trip():
    store = SignalStore()
    store.add_signal(
        {
            "id": "EURUSD:1",
            "symbol": "EURUSD",
            "side": "buy",
            "sl": 0.001,
            "tp": 0.002,
            "timestamp": datetime.now(timezone.utc),
        }
    )

    signal = store.get_latest("EURUSD")
    assert signal is not None
    assert signal.id == "EURUSD:1"
    assert signal.is_valid()


def test_state_manager_backtest_schedule():
    state = BotState()
    assert StateManager.is_backtest_due(state) is True
    StateManager.schedule_next_backtest(state)
    assert state.last_backtest_run is not None
    assert state.next_backtest_run is not None
