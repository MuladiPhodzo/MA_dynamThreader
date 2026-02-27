from unittest.mock import patch

from advisor.Client.mt5Client import MetaTrader5Client


def test_determine_bar_count_backtest_true():
    client = MetaTrader5Client()
    client.backtest = True
    assert client._determine_bar_count("15M") == 3000
    assert client._determine_bar_count("1D") == 500


@patch("advisor.Client.mt5Client.mt5.account_info")
def test_get_equity(mock_account_info):
    mock_account_info.return_value = type("A", (), {"equity": 1234.5})()
    client = MetaTrader5Client()
    assert client.get_equity() == 1234.5
