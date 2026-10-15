"""Point-in-time membership, bankruptcy paths, size-floor parsing, and the event study."""

from __future__ import annotations

import unittest
from datetime import date

import pandas as pd

from backtest_lab.cli import parse_min_cap
from backtest_lab.data.adjust import apply_identity
from backtest_lab.data.listings import _common_name
from backtest_lab.data.sp500_pit import PointInTimeSP500, is_bankruptcy
from backtest_lab.data.symbols import yahoo_symbol
from backtest_lab.engine import prior_year_returns
from backtest_lab.eventstudy import build_event_study


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
