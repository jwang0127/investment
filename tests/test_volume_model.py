import unittest

import pandas as pd

from investment_dashboard.volume_model import backtest_volume, classify_volume, market_features


class VolumeModelTests(unittest.TestCase):
    def setUp(self):
        dates = pd.date_range("2020-01-01", periods=90, freq="D")
        self.df = pd.DataFrame({"date": dates, "close": range(100, 190), "amount": [100 + i * 2 for i in range(90)]})

    def test_classification_returns_known_states(self):
        states = classify_volume(self.df["amount"])
        self.assertTrue(set(states.dropna()).issubset({"FLOOD", "EXPAND", "STABLE", "SHRINK", "DROUGHT"}))

    def test_features_and_backtest(self):
        features = market_features(self.df)
        result = backtest_volume(features)
        self.assertGreater(result.observations, 0)
        self.assertIsNotNone(result.hit_rate)


if __name__ == "__main__":
    unittest.main()
