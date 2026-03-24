from advisor.Client.mt5Client import MetaTrader5Client


def test_determine_bar_count_backtest_true():
    client = MetaTrader5Client()
    client.backtest = True
    assert client._determine_bar_count("15M") == 3000
    assert client._determine_bar_count("1D") == 500


def test_get_acc_attr_reads_account_info():
    client = MetaTrader5Client()
    client.account_info = {"equity": 1234.5}
    assert client.get_acc_attr("equity") == 1234.5
