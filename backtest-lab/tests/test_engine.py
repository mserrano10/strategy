"""Annual engine, rank selection, and the survivorship flag. No network."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

from backtest_lab.engine import trade_to_weights
from backtest_lab.report import write_report
from backtest_lab.strategies.rank import AnnualRank


class FakeSource:
    id = "yfinance"
    label = "Yahoo Finance free daily prices"
    free_source = True
    survivorship_bias_free = False

    def banner(self):
        return (
            "SURVIVORSHIP-BIASED — free Yahoo Finance source. "
            "A survivorship-bias-free universe requires Sharadar (SEP / SF1 / TICKERS)."
        )

    def caveats(self):
        return [self.banner()]


def _panel():
    index = pd.to_datetime(["2013-12-31", "2014-12-31", "2015-06-30", "2015-12-31", "2016-12-30"])
    adjusted = pd.DataFrame(
        {
            "A": [50.0, 100.0, 105.0, 110.0, 121.0],
            "B": [80.0, 100.0, 120.0, 150.0, 150.0],
        },
        index=index,
    )
    members = pd.DataFrame(
        {
            "ticker": ["A", "B"],
            "name": ["Alpha", "Bravo"],
            "sector": ["Tech", "Energy"],
            "source": ["test", "test"],
        }
    )
    basis = {
        "A": pd.Series({pd.Timestamp("2013-01-01"): 10.0}),
        "B": pd.Series({pd.Timestamp("2013-01-01"): 1000.0}),
    }
    return adjusted, members, basis


class TradeTests(unittest.TestCase):
    def test_full_investment_from_cash_charges_the_entry(self):
        prices = pd.Series({"A": 100.0})
        shares, cost, post = trade_to_weights(10_000, prices, {}, {"A": 1.0}, 0.001)
        self.assertAlmostEqual(post, 10_000 / 1.001, places=6)
        self.assertAlmostEqual(cost, 10_000 - post, places=6)
        self.assertAlmostEqual(shares["A"] * 100.0, post, places=6)

    def test_switch_costs_both_sides(self):
        prices = pd.Series({"A": 10.0, "B": 20.0})
        equity = 9_990.0
        old = {"A": equity / 10.0}
        rate = 0.001
        shares, cost, post = trade_to_weights(equity, prices, old, {"B": 1.0}, rate)
        expected = equity * (1 - rate) / (1 + rate)
        self.assertAlmostEqual(post, expected, places=4)
        self.assertAlmostEqual(cost, equity - post, places=4)
        self.assertNotIn("A", shares)
        self.assertAlmostEqual(shares["B"] * 20.0, post, places=4)


class RankTests(unittest.TestCase):
    def test_best_returner_switches_after_the_year_it_won(self):
        adjusted, members, basis = _panel()
        report = AnnualRank("best-returner", "Best Returner", 1, "return", "test").build(
            adjusted, adjusted, basis, members, 2015, date(2016, 12, 30), 10_000, 0.0, FakeSource()
        )
        self.assertFalse(report["data_source"]["survivorship_bias_free"])
        self.assertTrue(report["data_source"]["free_source"])
        self.assertIn("SURVIVORSHIP-BIASED", report["data_source"]["banner"])
        self.assertIn("Sharadar", report["data_source"]["banner"])
        tickers = [cohort["holdings"][0]["ticker"] for cohort in report["cohorts"]]
        self.assertEqual(tickers, ["A", "B"])
        self.assertAlmostEqual(report["cohorts"][0]["period_return"], 0.10, places=6)
        self.assertAlmostEqual(report["cohorts"][1]["period_return"], 0.0, places=6)
        by_date = {row["date"]: row["equity"] for row in report["equity"]}
        self.assertEqual(by_date["2014-12-31"], 10_000)
        self.assertEqual(by_date["2015-06-30"], 10_500)
        self.assertEqual(by_date["2016-12-30"], 11_000)
        self.assertAlmostEqual(report["metrics"]["total_return"], 0.10, places=6)

    def test_top_two_equal_weight_and_rebalance(self):
        adjusted, members, basis = _panel()
        report = AnnualRank("top5-returners", "Top 5 Returners", 5, "return", "test").build(
            adjusted, adjusted, basis, members, 2015, date(2016, 12, 30), 10_000, 0.0, FakeSource()
        )
        self.assertEqual(len(report["cohorts"][0]["holdings"]), 2)
        self.assertAlmostEqual(report["cohorts"][0]["holdings"][0]["weight"], 0.5)
        by_date = {row["date"]: row["equity"] for row in report["equity"]}
        self.assertEqual(by_date["2015-06-30"], 11_250)
        self.assertEqual(by_date["2015-12-31"], 13_000)
        self.assertEqual(by_date["2016-12-30"], 13_650)
        growth = 1.0
        for row in report["yearly_returns"]:
            growth *= 1 + row["return"]
        self.assertAlmostEqual(growth, 13_650 / 10_000, places=6)

    def test_biggest_uses_approximated_cap_not_price(self):
        adjusted, members, basis = _panel()
        report = AnnualRank("biggest", "Biggest", 1, "cap", "test").build(
            adjusted, adjusted, basis, members, 2015, date(2016, 12, 30), 10_000, 0.0, FakeSource()
        )
        self.assertEqual([cohort["holdings"][0]["ticker"] for cohort in report["cohorts"]], ["B", "B"])
        # 2014 close 100 × 1000 shares = 100,000. A is only 1,000.
        self.assertAlmostEqual(report["cohorts"][0]["holdings"][0]["market_cap"], 100_000)
        self.assertIn("approximation", report["market_cap_note"])
        self.assertEqual(report["equity"][-1]["equity"], 15_000)

    def test_entry_cost_reduces_dollars_and_headline_return(self):
        adjusted, members, basis = _panel()
        report = AnnualRank("best-returner", "Best Returner", 1, "return", "test").build(
            adjusted, adjusted, basis, members, 2015, date(2016, 12, 30), 10_000, 10.0, FakeSource()
        )
        # A then a full switch into B. B is flat in 2016, so the end value is
        # the post-switch equity. Cohort return stays the pre-cost holding result.
        self.assertAlmostEqual(report["cohorts"][0]["period_return"], 0.10, places=6)
        first = 10_000 / 1.001
        pre_switch = first * 1.10
        after_switch = pre_switch * 0.999 / 1.001
        self.assertAlmostEqual(report["metrics"]["final_equity"], after_switch, places=2)
        self.assertAlmostEqual(report["metrics"]["total_return"], after_switch / 10_000 - 1, places=6)
        self.assertLess(report["metrics"]["final_equity"], 11_000)

    def test_json_roundtrip_keeps_the_flag(self):
        adjusted, members, basis = _panel()
        report = AnnualRank("best-returner", "Best Returner", 1, "return", "test").build(
            adjusted, adjusted, basis, members, 2015, date(2016, 12, 30), 10_000, 0.0, FakeSource()
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = write_report(report, Path(tmp))
            payload = json.loads(path.read_text())
            self.assertIn("SURVIVORSHIP-BIASED", payload["data_source"]["banner"])
            self.assertTrue((Path(tmp) / "best-returner.csv").exists())
            self.assertTrue((Path(tmp) / "best-returner.png").exists())
            manifest = json.loads((Path(tmp) / "manifest.json").read_text())
            self.assertIn("SURVIVORSHIP-BIASED", manifest["banner"])


if __name__ == "__main__":
    unittest.main()
