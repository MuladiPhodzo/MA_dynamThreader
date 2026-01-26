import unittest
from unittest.mock import patch, MagicMock, mock_open
import pandas as pd

from advisor.mt5_pipeline.Client.mt5Client import MetaTrader5Client,  DataPlotter


class TestMetaTrader5Client(unittest.TestCase):

    def setUp(self):
        self.client = MetaTrader5Client()

    @patch("advisor.Client.mt5Client.mt5.initialize", return_value=True)
    @patch("advisor.Client.mt5Client.mt5.account_info", return_value="account_info")
    @patch("advisor.Client.mt5Client.mt5.terminal_info", return_value="terminal_info")
    @patch("advisor.Client.mt5Client.mt5.symbols_get", return_value=[MagicMock(name="EURUSD", visible=True)])
    def test_initialize_with_user_data(self, mock_symbols, mock_terminal, mock_account, mock_init):
        user_data = {"account_id": 123, "password": "test", "server": "demo"}
        res = self.client.initialize(user_data)

        self.assertTrue(res[0])
        self.assertEqual(self.client.account_info, "account_info")
        self.assertEqual(self.client.terminal_info, "terminal_info")
        self.assertIn("EURUSD", res[1])

    @patch("advisor.Client.mt5Client.mt5.symbols_get", return_value=[MagicMock(name="EURUSD", visible=True)])
    def test_get_symbols(self, mock_symbols):
        symbols = self.client.get_Symbols()
        self.assertIn("EURUSD", symbols)

    @patch("advisor.Client.mt5Client.mt5.copy_rates_from_pos")
    def test_get_live_data(self, mock_copy):
        mock_copy.return_value = [{"time": 1, "open": 1.1, "close": 1.2}]
        df = self.client.get_live_data("EURUSD", 1, bars=1)
        self.assertIsInstance(df, pd.DataFrame)
        self.assertIn("close", df.columns)

    @patch("advisor.Client.mt5Client.mt5.copy_rates_from_pos", return_value=None)
    def test_get_live_data_fail(self, mock_copy):
        df = self.client.get_live_data("EURUSD", 1, bars=1)
        self.assertIsNone(df)

    @patch("advisor.Client.mt5Client.mt5.shutdown", return_value=True)
    def test_close(self, mock_shutdown):
        res = self.client.close()
        self.assertFalse(res)


class TestDataHandler(unittest.TestCase):

    @patch("advisor.Client.mt5Client.os.path.isfile", return_value=False)
    @patch("advisor.Client.mt5Client.pd.DataFrame.to_csv")
    def test_toCSV_new_file(self, mock_csv, mock_exists):
        handler = dataHandler()
        df = pd.DataFrame({"a": [1, 2]})
        handler.toCSV(df, "test.csv")
        mock_csv.assert_called_once()

    @patch("advisor.Client.mt5Client.os.path.isfile", return_value=True)
    @patch("advisor.Client.mt5Client.pd.DataFrame.to_csv")
    def test_toCSV_append(self, mock_csv, mock_exists):
        handler = dataHandler()
        df = pd.DataFrame({"a": [1, 2]})
        handler.toCSV(df, "test.csv")
        mock_csv.assert_called_once_with(
            "test.csv", index=False, mode="a", header=False)

    @patch("advisor.Client.mt5Client.os.path.isfile", return_value=False)
    @patch("builtins.open", new_callable=mock_open)
    def test_toJSON_new_file(self, mock_file, mock_exists):
        handler = dataHandler()
        df = pd.DataFrame({"a": [1, 2]})
        handler.toJSON(df, "test.json")
        mock_file.assert_called_once_with("test.json", "a")

    def test_toCSV_empty_data(self):
        handler = dataHandler()
        df = pd.DataFrame()
        handler.toCSV(df, "test.csv")  # should just logger.info "No data to save."


class TestDataPlotter(unittest.TestCase):

    @patch("advisor.Client.mt5Client.plt.show")
    def test_plot_ticks(self, mock_show):
        ticks = [{"time": 1, "ask": 1.1, "bid": 1.0}]
        DataPlotter.plot_ticks(ticks, "Ticks")
        mock_show.assert_called_once()

    @patch("advisor.Client.mt5Client.plt.show")
    def test_plot_rates(self, mock_show):
        rates = [{"time": 1, "close": 1.2}]
        DataPlotter.plot_rates(rates, "Rates")
        mock_show.assert_called_once()

    @patch("advisor.Client.mt5Client.plt.show")
    def test_plot_charts(self, mock_show):
        df = pd.DataFrame({
            "close": [1, 2, 3, 4],
            "Fast_MA": [1, 2, 3, 4],
            "Slow_MA": [1, 2, 3, 4],
            "Crossover": [0, 2, -2, 0],
        }, index=pd.date_range("2023-01-01", periods=4))
        DataPlotter.plot_charts(
            df, entries=None, fast_period=5, slow_period=10)
        mock_show.assert_called_once()

    def test_plot_charts_missing_column(self):
        df = pd.DataFrame({"close": [1, 2, 3]})
        with self.assertRaises(ValueError):
            DataPlotter.plot_charts(df, None, 5, 10)


if __name__ == "__main__":
    unittest.main()
