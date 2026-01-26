import unittest
import pandas as pd
import MetaTrader5 as mt5

from advisor.mt5_pipeline.Client.mt5Client import MetaTrader5Client as Client


class TestAdvisor(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        """Run once before all tests"""
        cls.user_data = {
            "account_id": 308826480,
            "password": "N3gus5@1111",
            "server": "XMGlobal-MT5 6"
        }
        cls.client = Client()
        initialized = cls.client.initialize(cls.user_data)
        if not initialized:
            raise RuntimeError("Failed to initialize MetaTrader5 client")

    @classmethod
    def tearDownClass(cls):
        """Run once after all tests"""
        cls.client.close()

    def test_symbol_availability(self):
        availability = self.client.check_symbols_availability()
        self.assertTrue(availability, "Symbols should be available")

    def test_get_live_data(self):
        timeframe = mt5.TIMEFRAME_H1
        data = self.client.get_live_data("USDJPY", timeframe)
        self.assertIsNotNone(data, "Live data should not be None")
        self.assertIsInstance(data, pd.DataFrame)
        self.assertFalse(data.empty, "Live data should not be empty")

    def test_get_multi_tf_data(self):
        self.client.TF = {
            "HTF": mt5.TIMEFRAME_H4,
            "LTF": mt5.TIMEFRAME_H1,
        }
        data = self.client.get_multi_tf_data("USDJPY")
        self.assertIsNotNone(data, "Multi timeframe data should not be None")
        self.assertIsInstance(data, dict)
        self.assertIn("HTF", data)
        self.assertIn("LTF", data)

    def test_invalid_symbol(self):
        """Check handling of invalid symbol requests"""
        timeframe = mt5.TIMEFRAME_H1
        data = self.client.get_live_data("INVALID", timeframe)
        self.assertTrue(data is None or data.empty,
                        "Invalid symbol should return None or empty DataFrame")

    def test_account_info(self):
        """Optional: test account details retrieval"""
        info = self.client.account_info
        self.assertIsNotNone(info, "Account info should not be None")

        # Check expected attributes exist
        self.assertTrue(hasattr(info, "balance"),
                        "AccountInfo should have balance")
        self.assertTrue(hasattr(info, "equity"),
                        "AccountInfo should have equity")
        self.assertTrue(hasattr(info, "margin_free"),
                        "AccountInfo should have margin_free")

        # Optional: assert types
        self.assertIsInstance(info.balance, float)
        self.assertIsInstance(info.equity, float)
        self.assertIsInstance(info.margin_free, float)


if __name__ == "__main__":
    unittest.main()
