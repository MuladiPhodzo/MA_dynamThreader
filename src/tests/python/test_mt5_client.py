import pytest

from advisor.Client.mt5Client import MetaTrader5Client
from advisor.core.state import BotLifecycle


class DummyStateManager:
    def __init__(self, state: BotLifecycle):
        self._state = state

    def get_state(self) -> BotLifecycle:
        return self._state


def test_determine_bar_count_backtest_true():
    client = MetaTrader5Client(DummyStateManager(BotLifecycle.RUNNING_BACKTEST))
    assert client._determine_bar_count("15M") == 6000
    assert client._determine_bar_count("1D") == 3000


def test_get_acc_attr_reads_account_info():
    client = MetaTrader5Client(DummyStateManager(BotLifecycle.RUNNING))
    client.account_info = {"equity": 1234.5}
    assert client.get_acc_attr("equity") == 1234.5


def test_get_acc_attr_handles_missing_account_info():
    client = MetaTrader5Client(DummyStateManager(BotLifecycle.RUNNING))
    client.account_info = None
    assert client.get_acc_attr("equity") is None


def test_connect_account_rejects_invalid_credentials():
    client = MetaTrader5Client(DummyStateManager(BotLifecycle.RUNNING))
    with pytest.raises(ConnectionError):
        client.connect_account({"account_id": "bad"})


def test_get_live_data_returns_none_for_empty_rates(monkeypatch):
    client = MetaTrader5Client(DummyStateManager(BotLifecycle.RUNNING))
    monkeypatch.setattr("advisor.Client.mt5Client.mt5.copy_rates_from_pos", lambda *args: [])
    monkeypatch.setattr("advisor.Client.mt5Client.mt5.last_error", lambda: (0, "ok"))

    assert client.get_live_data("EURUSD", 1, 100) is None


def test_get_live_data_converts_epoch_time_to_utc(monkeypatch):
    client = MetaTrader5Client(DummyStateManager(BotLifecycle.RUNNING))
    rates = [
        {
            "time": 1_700_000_000,
            "open": 1.1,
            "high": 1.2,
            "low": 1.0,
            "close": 1.15,
            "tick_volume": 10,
            "spread": 1,
            "real_volume": 0,
        }
    ]
    monkeypatch.setattr("advisor.Client.mt5Client.mt5.copy_rates_from_pos", lambda *args: rates)

    data = client.get_live_data("EURUSD", 1, 100)

    assert len(data) == 1
    assert str(data.loc[0, "time"].tz) == "UTC"


def test_get_account_deals_returns_dict_rows(monkeypatch):
    client = MetaTrader5Client(DummyStateManager(BotLifecycle.RUNNING))

    class Deal:
        def _asdict(self):
            return {"ticket": 1, "profit": 12.5}

    monkeypatch.setattr("advisor.Client.mt5Client.mt5.history_deals_get", lambda *args: [Deal()])

    assert client.get_account_deals() == [{"ticket": 1, "profit": 12.5}]


def test_get_account_deals_handles_mt5_failure(monkeypatch):
    client = MetaTrader5Client(DummyStateManager(BotLifecycle.RUNNING))
    monkeypatch.setattr("advisor.Client.mt5Client.mt5.history_deals_get", lambda *args: None)
    monkeypatch.setattr("advisor.Client.mt5Client.mt5.last_error", lambda: (1, "failed"))

    assert client.get_account_deals() == []
