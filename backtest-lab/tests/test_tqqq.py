"""Two-bullet rule and TQQQ buy-and-hold. Hand-built prices, no network."""

from __future__ import annotations

import unittest

import pandas as pd

from backtest_lab.strategies.tqqq import simulate_buy_hold, simulate_two_bullet


def _flat(index, value):
    return pd.Series(value, index=index, dtype=float)


class TwoBulletTests(unittest.TestCase):
    def _path(self):
        index = pd.date_range("2020-01-01", "2020-06-22", freq="D")
        close = _flat(index, 100.0)
        close.loc["2020-01-15"] = 60.0  # 40% drawdown, not a trigger
        close.loc["2020-02-01":"2020-04-19"] = 50.0
        close.loc["2020-04-20":"2020-06-19"] = 20.0
        close.loc["2020-06-20"] = 10.0
        close.loc["2020-06-21"] = 90.0
        close.loc["2020-06-22"] = 100.0
        high = close.copy()
        opened = close.copy()
        # 9× the floor of 10 must not fill. 10× does.
        high.loc["2020-06-21"] = 90.0
        high.loc["2020-06-22"] = 100.0
        return index, close, high, opened

    def test_tranches_floor_and_ten_times_exit(self):
        index, close, high, opened = self._path()
        result = simulate_two_bullet(
            spy_adj=_flat(index, 100.0),
            bil_adj=_flat(index, 100.0),
            tqqq_close=close,
            tqqq_high=high,
            tqqq_open=opened,
            start=pd.Timestamp("2020-01-01"),
            capital=10_000,
            cost_bps=0,
            seed_ath=100.0,
        )
        equity = result["equity"]
        self.assertAlmostEqual(equity.loc[pd.Timestamp("2020-01-15")], 10_000, places=4)
        self.assertAlmostEqual(equity.loc[pd.Timestamp("2020-02-01")], 10_000, places=4)
        self.assertAlmostEqual(equity.loc[pd.Timestamp("2020-04-20")], 9_700, places=4)
        self.assertAlmostEqual(equity.loc[pd.Timestamp("2020-06-20")], 9_350, places=4)
        self.assertAlmostEqual(equity.loc[pd.Timestamp("2020-06-21")], 12_150, places=4)
        self.assertAlmostEqual(equity.loc[pd.Timestamp("2020-06-22")], 12_500, places=4)

        buys = [event for event in result["events"] if event["type"] == "buy"]
        self.assertEqual(len(buys), 6)
        self.assertEqual(sum(1 for event in buys if event["bullet"] == "bullet-1"), 3)
        self.assertEqual(sum(1 for event in buys if event["bullet"] == "bullet-2"), 3)
        self.assertEqual(buys[0]["date"], "2020-02-01")
        self.assertAlmostEqual(buys[0]["shares"], 5.0, places=6)
        self.assertEqual(buys[3]["date"], "2020-04-20")
        self.assertTrue(any(event["type"] == "cycle-end" and event["date"] == "2020-06-22" for event in result["events"]))
        self.assertFalse(any(event["type"] == "trigger" and event["date"] == "2020-01-15" for event in result["events"]))
        # Still invested the day before the limit; the 9× print did not sell.
        self.assertFalse(any(event["type"] == "sell" and event["date"] == "2020-06-21" for event in result["events"]))

    def test_seeded_ath_triggers_on_the_first_session(self):
        index = pd.date_range("2020-01-01", periods=5, freq="D")
        close = _flat(index, 100.0)
        result = simulate_two_bullet(
            spy_adj=_flat(index, 100.0),
            bil_adj=_flat(index, 50.0),
            tqqq_close=close,
            tqqq_high=close.copy(),
            tqqq_open=close.copy(),
            start=index[0],
            capital=10_000,
            cost_bps=0,
            seed_ath=200.0,
        )
        triggers = [event for event in result["events"] if event["type"] == "trigger"]
        self.assertEqual(triggers[0]["date"], "2020-01-01")
        self.assertEqual(triggers[0]["bullet"], "bullet-1")
        self.assertEqual(len(triggers), 1)

    def test_gap_through_the_limit_fills_at_the_open(self):
        index = pd.date_range("2020-01-01", periods=3, freq="D")
        close = pd.Series([100.0, 40.0, 80.0], index=index)
        high = pd.Series([100.0, 40.0, 200.0], index=index)
        opened = pd.Series([100.0, 40.0, 180.0], index=index)
        # Floor will be 40 after day 2. Day 3 limit is 400, and the high is only 200, so no fill.
        # Build a path that floors at 10 and gaps through 100.
        index = pd.date_range("2020-01-01", "2020-01-04", freq="D")
        close = pd.Series([50.0, 50.0, 10.0, 150.0], index=index)
        high = close.copy()
        opened = close.copy()
        high.iloc[-1] = 180.0
        opened.iloc[-1] = 160.0
        result = simulate_two_bullet(
            spy_adj=_flat(index, 100.0),
            bil_adj=_flat(index, 100.0),
            tqqq_close=close,
            tqqq_high=high,
            tqqq_open=opened,
            start=index[0],
            capital=10_000,
            cost_bps=0,
            seed_ath=100.0,
        )
        sells = [event for event in result["events"] if event["type"] == "sell"]
        self.assertTrue(sells)
        self.assertAlmostEqual(sells[0]["price"], 160.0, places=4)

    def test_buy_and_hold_uses_adjusted_close(self):
        index = pd.to_datetime(["2014-12-31", "2015-06-30", "2015-12-31"])
        adj = pd.Series([100.0, 150.0, 250.0], index=index)
        equity = simulate_buy_hold(adj, 10_000, 0)
        self.assertAlmostEqual(float(equity.iloc[-1]), 25_000, places=4)
        costly = simulate_buy_hold(adj, 10_000, 10)
        self.assertAlmostEqual(float(costly.iloc[-1]), 10_000 / 1.001 * 2.5, places=4)


if __name__ == "__main__":
    unittest.main()
