import unittest
import pandas as pd
import MetaTrader5 as mt5

from advisor.Client.mt5Client import MetaTrader5Client as Client
from advisor.MovingAverage.MovingAverage import MovingAverageCrossover


class TestMovingAverages(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        """Initialize MT5 client and fetch live data once for all tests"""
        cls.symbol = "USDJPY"
        cls.timeframe = mt5.TIMEFRAME_H1
        cls.user_data = {
            "account_id": 308826480,
            "password": "N3gus5@1111",
            "server": "XMGlobal-MT5 6"
        }

        cls.client = Client()
        initialized = cls.client.initialize(cls.user_data)

        if not initialized:
            raise RuntimeError("Failed to initialize MetaTrader5 client")

        cls.data = cls.client.get_live_data(cls.symbol, cls.timeframe)

        if cls.data is None or cls.data.empty:
            raise RuntimeError(
                "Failed to fetch live data for testing MovingAverages")

    def setUp(self):
        """Create a fresh MovingAverageCrossover instance for each test"""
        self.strategy = MovingAverageCrossover(self.symbol, self.data)

    def test_calculate_averages(self):
        averages_data = self.strategy.calculate_moving_averages(self.data)

        self.assertIsNotNone(averages_data, "Averages data should not be None")
        self.assertIsInstance(averages_data, pd.DataFrame)
        self.assertFalse(averages_data.empty,
                         "Averages data should not be empty")

        # Required columns
        expected_columns = {"Fast_MA", "Slow_MA",
                            "Crossover", "Bias", "Signal"}
        self.assertTrue(expected_columns.issubset(averages_data.columns))

    # def test_backtest(self):
    #     _ = self.strategy.calculate_moving_averages(self.data)
    #     backtested_data = self.strategy.backtest_strategy()

    #     self.assertIsNotNone(backtested_data, "Backtest data should not be None")
    #     self.assertIsInstance(backtested_data, pd.DataFrame)
    #     self.assertFalse(backtested_data.empty, "Backtest data should not be empty")

    #     # Required columns
    #     expected_columns = {
    #         "Position",
    #         "Market_Returns",
    #         "Strategy_Returns",
    #         "Cumulative_Market_Returns",
    #         "Cumulative_Strategy_Returns",
    #     }
    #     self.assertTrue(expected_columns.issubset(backtested_data.columns))

    # def test_strategy_runs_end_to_end(self):
    #     """Full run test: calculate averages + backtest"""
    #     averages_data = self.strategy.calculate_moving_averages(self.data)
    #     backtested_data = self.strategy.backtest_strategy()

    #     self.assertIsNotNone(averages_data)
    #     self.assertIsNotNone(backtested_data)
    #     self.assertEqual(len(averages_data), len(backtested_data))

    def test_invalid_symbol(self):
        """Ensure strategy handles invalid symbol gracefully"""
        bad_client = Client("INVALID")
        data = bad_client.get_live_data("INVALID", self.timeframe)

        self.assertTrue(data is None or data.empty,
                        "Invalid symbol should return None or empty DataFrame")


if __name__ == "__main__":
    unittest.main()
