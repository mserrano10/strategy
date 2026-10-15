"""Point-in-time membership, bankruptcy paths, size-floor parsing, and the event study."""

from __future__ import annotations

import unittest
from datetime import date

import pandas as pd

from backtest_lab.cli import parse_min_cap
from backtest_lab.data.adjust import apply_identity, deal_price
from backtest_lab.data.listings import _common_name
from backtest_lab.data.sp500_pit import PointInTimeSP500, is_bankruptcy, load_point_in_time
from backtest_lab.data.symbols import yahoo_symbol
from backtest_lab.engine import prior_year_returns, without_unverified_extremes
from backtest_lab.eventstudy import build_event_study, coverage_brackets


def _pit():
    current = pd.DataFrame(
        {
            "ticker": ["NEW", "STAY", "META"],
            "name": ["New Co", "Stay Co", "Meta Platforms"],
            "sector": ["Tech", "Tech", "Tech"],
            "date_added": pd.to_datetime(["2016-06-01", "1990-01-01", "2012-05-18"]),
        }
    )
    changes = pd.DataFrame(
        {
            "date": pd.to_datetime(["2016-06-01", "2012-06-09", "2008-09-16", "2014-03-01"]),
            "added": ["NEW", "META", None, "BACK"],
            "added_name": ["New Co", "Meta Platforms", "", "Back Co"],
            "removed": ["OLD", "FB", "LEH", "BACK"],
            "removed_name": ["Old Co", "Facebook", "Lehman Brothers", "Back Co"],
            "reason": [
                "Market capitalization changes.",
                "Facebook changed its ticker symbol from FB to META.",
                "Lehman Brothers filed for bankruptcy.",
                "Market capitalization changes.",
            ],
        }
    )
    # BACK is removed in 2014 after being added... the fixture adds then the
    # frame is sorted by date inside PointInTime, so the 2014 removal wins
    # only if the add is earlier. Swap to add in 2009 and remove in 2014.
    changes.loc[3, "date"] = pd.Timestamp("2014-03-01")
    changes.loc[changes["added"].eq("BACK"), "date"] = pd.Timestamp("2009-01-01")
    # The row is both add and remove. Split it.
    changes = pd.DataFrame(
        {
            "date": pd.to_datetime(["2008-09-16", "2009-01-01", "2012-06-09", "2014-03-01", "2016-06-01"]),
            "added": [None, "BACK", "META", None, "NEW"],
            "added_name": ["", "Back Co", "Meta Platforms", "", "New Co"],
            "removed": ["LEH", None, "FB", "BACK", "OLD"],
            "removed_name": ["Lehman Brothers", "", "Facebook", "Back Co", "Old Co"],
            "reason": [
                "Lehman Brothers filed for bankruptcy.",
                "Market capitalization changes.",
                "Facebook changed its ticker symbol from FB to META.",
                "Market capitalization changes.",
                "Market capitalization changes.",
            ],
        }
    )
    return PointInTimeSP500(current, changes)


class MembershipTests(unittest.TestCase):
    def test_walks_changes_backward_and_honors_date_added(self):
        pit = _pit()
        y2007 = set(pit.members_on("2007-12-31")["ticker"])
        self.assertIn("STAY", y2007)
        self.assertIn("LEH", y2007)
        self.assertNotIn("NEW", y2007)
        self.assertNotIn("META", y2007)
        y2013 = set(pit.members_on("2013-12-31")["ticker"])
        self.assertIn("META", y2013)
        self.assertNotIn("FB", y2013)
        self.assertIn("BACK", y2013)
        self.assertNotIn("LEH", y2013)
        y2015 = set(pit.members_on("2015-12-31")["ticker"])
        self.assertNotIn("BACK", y2015)
        self.assertNotIn("NEW", y2015)
        self.assertIn("OLD", y2015)
        y2016 = set(pit.members_on("2016-12-31")["ticker"])
        self.assertIn("NEW", y2016)
        self.assertNotIn("OLD", y2016)
        # Added in 2009 and removed in 2014: absent both before the add and after the removal.
        self.assertNotIn("BACK", y2007)
        self.assertNotIn("BACK", y2016)

    def test_bankruptcy_and_rename_flags(self):
        pit = _pit()
        self.assertTrue(pit.removal("LEH")["bankruptcy"])
        self.assertFalse(pit.removal("OLD")["bankruptcy"])
        self.assertTrue(is_bankruptcy("filed for bankruptcy"))
        self.assertEqual(pit.renames()[0]["old"], "FB")
        self.assertEqual(yahoo_symbol("BRK.B"), "BRK-B")
        self.assertIsNone(yahoo_symbol("BAC-PL"))

    def test_bankruptcy_wipes_the_path_and_a_rename_uses_the_successor(self):
        pit = _pit()
        index = pd.to_datetime(["2007-12-31", "2008-09-15", "2008-12-31", "2011-12-30", "2012-06-08", "2012-12-31"])
        meta = pd.Series([10.0, 12.0, 14.0, 20.0, 22.0, 30.0], index=index)
        panel = pd.DataFrame({"META": meta, "LEH": [80.0, 40.0, 5.0, 5.0, 5.0, 5.0]}, index=index)
        # The post-2008 LEH prints are a recycled ticker. FB itself is missing.
        adjusted, notes = apply_identity(panel, pit)
        self.assertIn("FB", adjusted.columns)
        self.assertAlmostEqual(float(adjusted.loc[pd.Timestamp("2011-12-30"), "FB"]), 20.0)
        # The rename is not an exit: December still earns the successor's return.
        self.assertAlmostEqual(float(adjusted.loc[pd.Timestamp("2012-12-31"), "FB"]), 30.0)
        leh_end = float(adjusted.loc[pd.Timestamp("2008-12-31"), "LEH"])
        self.assertLess(leh_end, 0.01)
        ret = prior_year_returns(adjusted, 2008)
        self.assertLess(float(ret["LEH"]), -0.99)
        self.assertIsInstance(notes, list)

    def test_a_later_spell_keeps_prices_after_an_old_removal(self):
        current = pd.DataFrame(
            {
                "ticker": ["MCK"],
                "name": ["McKesson"],
                "sector": ["Health Care"],
                "date_added": pd.to_datetime(["1999-01-13"]),
            }
        )
        changes = pd.DataFrame(
            {
                "date": pd.to_datetime(["1994-09-30"]),
                "added": [None],
                "added_name": [""],
                "removed": ["MCK"],
                "removed_name": ["McKesson"],
                "reason": ["McKesson sold PCS Health Services to Eli Lilly."],
            }
        )
        pit = PointInTimeSP500(current, changes)
        index = pd.to_datetime(["1998-12-31", "1999-12-31", "2015-12-31"])
        panel = pd.DataFrame({"MCK": [10.0, 12.0, 20.0]}, index=index)
        adjusted, _notes = apply_identity(panel, pit)
        self.assertAlmostEqual(float(adjusted.loc[pd.Timestamp("2015-12-31"), "MCK"]), 20.0)
        self.assertIn("MCK", set(pit.members_on("2015-12-31")["ticker"]))

    def test_prior_year_returns_skip_a_missing_print(self):
        index = pd.to_datetime(["2007-12-31", "2008-12-31"])
        panel = pd.DataFrame({"AAA": [10.0, 12.0], "BBB": [10.0, pd.NA]}, index=index)
        ret = prior_year_returns(panel, 2008)
        self.assertAlmostEqual(float(ret["AAA"]), 0.2)
        self.assertNotIn("BBB", ret.index)


class IdentityAndAliasTests(unittest.TestCase):
    def test_a_continuous_series_is_kept_across_an_index_gap(self):
        current = pd.DataFrame(
            {
                "ticker": ["AMD"],
                "name": ["Advanced Micro Devices"],
                "sector": ["Tech"],
                "date_added": pd.to_datetime(["2017-03-20"]),
            }
        )
        changes = pd.DataFrame(
            {
                "date": pd.to_datetime(["2013-09-20", "2017-03-20"]),
                "added": ["VRTX", "AMD"],
                "added_name": ["Vertex", "Advanced Micro Devices"],
                "removed": ["AMD", "URBN"],
                "removed_name": ["Advanced Micro Devices", "Urban"],
                "reason": ["Market capitalization changes.", "Market capitalization changes."],
            }
        )
        pit = PointInTimeSP500(current, changes)
        self.assertNotIn("AMD", set(pit.members_on("2016-12-31")["ticker"]))
        self.assertIn("AMD", set(pit.members_on("2017-12-31")["ticker"]))
        index = pd.to_datetime(["2012-12-31", "2016-12-30", "2017-12-29"])
        panel = pd.DataFrame({"AMD": [10.0, 11.34, 10.28]}, index=index)
        adjusted, _notes = apply_identity(panel, pit)
        ret = prior_year_returns(adjusted, 2017)
        self.assertAlmostEqual(float(ret["AMD"]), 10.28 / 11.34 - 1.0, places=6)

    def test_a_recycled_ticker_does_not_fill_the_old_spell(self):
        current = pd.DataFrame(
            {
                "ticker": ["AMD"],
                "name": ["Advanced Micro Devices"],
                "sector": ["Tech"],
                "date_added": pd.to_datetime(["2017-03-20"]),
            }
        )
        changes = pd.DataFrame(
            {
                "date": pd.to_datetime(["2013-09-20", "2017-03-20"]),
                "added": ["VRTX", "AMD"],
                "added_name": ["Vertex", "Advanced Micro Devices"],
                "removed": ["AMD", "URBN"],
                "removed_name": ["Advanced Micro Devices", "Urban"],
                "reason": ["Market capitalization changes.", "Market capitalization changes."],
            }
        )
        pit = PointInTimeSP500(current, changes)
        index = pd.to_datetime(["2016-12-30", "2018-12-31"])
        panel = pd.DataFrame({"AMD": [50.0, 80.0]}, index=index)
        adjusted, _notes = apply_identity(panel, pit)
        self.assertTrue(pd.isna(adjusted.loc[pd.Timestamp("2016-12-30"), "AMD"]))
        self.assertAlmostEqual(float(adjusted.loc[pd.Timestamp("2018-12-31"), "AMD"]), 80.0)

    def test_a_stated_deal_price_fills_a_missing_exit(self):
        self.assertAlmostEqual(deal_price("Acquired for $50.25 per share."), 50.25)
        self.assertIsNone(deal_price("Acquired."))
        pit = _pit()
        # OLD leaves on 2016-06-01. Give it a print before that and nothing after.
        index = pd.to_datetime(["2015-12-31", "2016-12-30"])
        panel = pd.DataFrame({"OLD": [40.0, pd.NA]}, index=index)
        pit.removals()["OLD"]["reason"] = "Acquired for $50 per share."
        adjusted, _notes = apply_identity(panel, pit)
        self.assertAlmostEqual(float(adjusted.loc[pd.Timestamp("2016-12-30"), "OLD"]), 50.0)

    def test_snapshot_file_links_a_rename_and_not_an_acquisition(self):
        current = pd.DataFrame(
            {
                "ticker": ["META", "ACGL", "ISRG"],
                "name": ["Meta", "Arch", "Intuitive"],
                "sector": ["Tech", "Financials", "Health Care"],
                "date_added": pd.to_datetime(["2013-12-23", "2022-11-01", "2008-06-02"]),
            }
        )
        changes = pd.DataFrame(
            {
                "date": pd.to_datetime(["2022-11-01"]),
                "added": ["ACGL"],
                "added_name": ["Arch Capital"],
                "removed": ["TWTR"],
                "removed_name": ["Twitter"],
                "reason": ["Elon Musk acquired Twitter."],
            }
        )
        constituents = pd.DataFrame(
            {
                "date": pd.to_datetime(
                    ["2008-06-01", "2008-06-02", "2015-12-31", "2022-06-08", "2022-06-09", "2022-10-31", "2022-11-01"]
                ),
                "tickers": [
                    "BSC,STAY",
                    "ISRG,STAY",
                    "FB,ISRG,STAY",
                    "FB,ISRG,STAY",
                    "META,ISRG,STAY",
                    "META,TWTR,ISRG,STAY",
                    "META,ACGL,ISRG,STAY",
                ],
            }
        )
        pit = PointInTimeSP500(current, changes, constituents=constituents)
        linked = {row["old"]: row["new"] for row in pit.renames()}
        self.assertEqual(linked.get("FB"), "META")
        self.assertNotIn("TWTR", linked)
        self.assertNotIn("BSC", linked)
        self.assertIn("FB", set(pit.members_on("2015-12-31")["ticker"]))
        self.assertNotIn("META", set(pit.members_on("2015-12-31")["ticker"]))

    def test_vendored_constituents_cover_a_normal_year_and_keep_celgene(self):
        pit = load_point_in_time()
        y2018 = set(pit.members_on("2018-12-31")["ticker"])
        self.assertGreater(len(y2018), 490)
        self.assertLess(len(y2018), 520)
        self.assertIn("CELG", y2018)
        self.assertIn("AMD", y2018)
        self.assertNotIn("AMD", set(pit.members_on("2016-12-31")["ticker"]))
        linked = {row["old"]: row["new"] for row in pit.renames()}
        self.assertEqual(linked.get("FB"), "META")
        self.assertEqual(linked.get("CTL"), "LUMN")
        self.assertNotIn("TWTR", linked)
        self.assertNotIn("BSC", linked)


class EventStudyTests(unittest.TestCase):
    def test_counts_a_crash_and_ignores_a_partial_next_year(self):
        pit = _pit()
        index = pd.to_datetime(
            ["1999-12-31", "2000-12-29", "2001-12-31", "2002-06-28"]
        )
        # STAY is the only name in the early set besides LEH (LEH removed 2008,
        # so LEH is a member in 2000). Give STAY the huge 2000 and a crash in 2001.
        panel = pd.DataFrame(
            {
                "STAY": [10.0, 40.0, 10.0, 12.0],
                "LEH": [10.0, 12.0, 13.0, 14.0],
            },
            index=index,
        )
        # 2002 is not a complete year, so 2001's next year is partial.
        payload = build_event_study(panel, pit, 2000, date(2002, 6, 28), [])
        by_year = {row["year"]: row for row in payload["years"]}
        self.assertEqual(by_year[2000]["winner"]["ticker"], "STAY")
        self.assertAlmostEqual(by_year[2000]["winner"]["next_return"], 10 / 40 - 1)
        self.assertFalse(by_year[2000]["partial_next_year"])
        self.assertTrue(by_year[2001]["partial_next_year"])
        summary = payload["summary"]
        self.assertEqual(summary["winner_observed"], 1)
        self.assertEqual(summary["winner_down_30"], 1)
        self.assertEqual(summary["winner_down_50"], 1)
        excluded = summary["distribution"]["winner"]["excluded"]
        self.assertEqual(excluded["n"], 1)
        self.assertLess(excluded["median"], -0.5)

    def test_an_unverified_1000_percent_year_cannot_win(self):
        kept, dropped = without_unverified_extremes(pd.Series({"BIG": 12.0, "OK": 2.0}))
        self.assertEqual(dropped, ["BIG"])
        self.assertAlmostEqual(float(kept["OK"]), 2.0)
        pit = _pit()
        index = pd.to_datetime(["1999-12-31", "2000-12-29", "2001-12-31"])
        panel = pd.DataFrame(
            {"STAY": [10.0, 130.0, 20.0], "LEH": [10.0, 30.0, 15.0]},
            index=index,
        )
        payload = build_event_study(panel, pit, 2000, date(2001, 12, 31), [])
        self.assertEqual(payload["years"][0]["winner"]["ticker"], "LEH")
        self.assertIn("STAY", payload["years"][0]["extreme_unverified"])

    def test_coverage_bracket_compounds_the_hole(self):
        coverage = [{"year": 2016, "members": 10, "unpriceable": 2, "priced": 8}]
        cohorts = [
            {
                "year": 2016,
                "period_return": 1.0,
                "rebalance_date": "2015-12-31",
                "period_end": "2016-12-30",
            }
        ]
        spy = pd.Series([100.0, 110.0], index=pd.to_datetime(["2015-12-31", "2016-12-30"]))
        result = coverage_brackets(coverage, cohorts, spy, 10_000)
        self.assertAlmostEqual(result["excluded"]["total_return"], 1.0)
        self.assertAlmostEqual(result["worst"]["total_return"], 0.6)
        self.assertAlmostEqual(result["neutral"]["total_return"], 0.82)
        self.assertAlmostEqual(result["years"][0]["spy_return"], 0.10)


class ListingTests(unittest.TestCase):
    def test_common_stock_filter_and_cap_parser(self):
        self.assertTrue(_common_name("Apple Inc. - Common Stock"))
        self.assertTrue(_common_name("Agilent Technologies, Inc. Common Stock"))
        self.assertFalse(_common_name("StablecoinX Inc. - Warrants"))
        self.assertFalse(_common_name("AllianceBernstein Holding L.P. Units"))
        self.assertFalse(_common_name("Some Preferred Stock"))
        self.assertEqual(parse_min_cap("10b"), 10_000_000_000)
        self.assertEqual(parse_min_cap("2B"), 2_000_000_000)
        self.assertEqual(parse_min_cap("$500m"), 500_000_000)


if __name__ == "__main__":
    unittest.main()
