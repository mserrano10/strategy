"""Market-cap basis, session dating, the Sharadar stub, and the unlinked page."""

from __future__ import annotations

import os
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from backtest_lab.calendarutil import required_through
from backtest_lab.cli import parse_end
from backtest_lab.data.caps import market_cap, split_adjusted_share_basis
from backtest_lab.data.sharadar import API_KEY_ENV, SharadarSource
from backtest_lab.data.yfinance_source import YFinanceSource, _cache_covers


class CapTests(unittest.TestCase):
    def test_later_splits_are_applied_to_the_share_count(self):
        shares = pd.Series(
            [100.0, 250.0],
            index=pd.to_datetime(["2015-01-01", "2016-06-01"]),
        )
        splits = pd.Series([2.0], index=pd.to_datetime(["2016-01-01"]))
        basis = split_adjusted_share_basis(shares, splits)
        # 2015 shares are pre-split, so they double onto the close's basis.
        self.assertAlmostEqual(
            market_cap(basis, 10.0, pd.Timestamp("2015-06-01")),
            2_000.0,
        )
        # The June 2016 count is already after the split. Do not double it.
        self.assertAlmostEqual(
            market_cap(basis, 10.0, pd.Timestamp("2016-07-01")),
            2_500.0,
        )
        # Before any record, use the earliest share count, still split-adjusted.
        self.assertAlmostEqual(
            market_cap(basis, 10.0, pd.Timestamp("2014-12-31")),
            2_000.0,
        )


class CalendarTests(unittest.TestCase):
    def test_required_session_skips_weekends_and_the_unfinished_day(self):
        # Thursday 15 Oct 2026, 11:00 UTC is before the US cash close.
        morning = datetime(2026, 10, 15, 11, 0, tzinfo=timezone.utc)
        self.assertEqual(required_through(date(2026, 10, 15), morning), date(2026, 10, 14))
        evening = datetime(2026, 10, 15, 21, 30, tzinfo=timezone.utc)
        self.assertEqual(required_through(date(2026, 10, 15), evening), date(2026, 10, 15))
        saturday = datetime(2026, 10, 17, 15, 0, tzinfo=timezone.utc)
        self.assertEqual(required_through(date(2026, 10, 17), saturday), date(2026, 10, 16))
        # A historical Saturday end stops on Friday.
        self.assertEqual(
            required_through(date(2016, 12, 31), datetime(2026, 10, 15, 11, tzinfo=timezone.utc)),
            date(2016, 12, 30),
        )

    def test_parse_end(self):
        self.assertEqual(parse_end("today", today=date(2026, 10, 15)), date(2026, 10, 15))
        self.assertEqual(parse_end("2016"), date(2016, 12, 31))
        self.assertEqual(parse_end("2016-12-30"), date(2016, 12, 30))


class SharadarTests(unittest.TestCase):
    def test_missing_key_names_the_env_var(self):
        previous = os.environ.pop(API_KEY_ENV, None)
        try:
            source = SharadarSource()
            with self.assertRaises(RuntimeError) as caught:
                source.history(["AAPL"], date(2020, 1, 1), date(2020, 12, 31))
            self.assertIn(API_KEY_ENV, str(caught.exception))
        finally:
            if previous is not None:
                os.environ[API_KEY_ENV] = previous

    def test_stub_does_not_fetch_when_the_key_is_present(self):
        source = SharadarSource(api_key="test-key")
        self.assertEqual(source.api_key, "test-key")
        with self.assertRaises(NotImplementedError) as caught:
            source.cap_basis(["AAPL"])
        message = str(caught.exception)
        self.assertIn("SHARADAR/SF1", message)
        self.assertIn("marketcap", message)
        with self.assertRaises(NotImplementedError) as prices:
            source.history(["AAPL"], date(2020, 1, 1), date(2020, 12, 31))
        self.assertIn("SHARADAR/SEP", str(prices.exception))
        with self.assertRaises(NotImplementedError) as universe:
            source.universe()
        self.assertIn("SHARADAR/TICKERS", str(universe.exception))


class CacheTests(unittest.TestCase):
    def test_cache_covers_a_start_that_falls_on_a_holiday(self):
        index = pd.bdate_range("2013-01-02", "2026-10-14")
        frame = pd.DataFrame({"Close": 1.0}, index=index)
        now = datetime(2026, 10, 15, 11, 0, tzinfo=timezone.utc)
        self.assertTrue(_cache_covers(frame, date(2026, 10, 15), date(2013, 1, 1), date(2026, 10, 15), now))
        short = frame.iloc[:-40]
        self.assertFalse(_cache_covers(short, date(2026, 10, 1), date(2013, 1, 1), date(2026, 10, 15), now))


class FlagTests(unittest.TestCase):
    def test_free_source_is_marked_biased(self):
        source = YFinanceSource()
        self.assertTrue(source.free_source)
        self.assertFalse(source.survivorship_bias_free)
        banner = source.banner()
        self.assertIn("SURVIVORSHIP-BIASED", banner)
        self.assertIn("Sharadar", banner)


class SiteTests(unittest.TestCase):
    def test_portal_index_does_not_link_the_lab(self):
        root = Path(__file__).resolve().parents[2]
        text = (root / "index.html").read_text()
        self.assertNotIn("backtest-lab", text)
        page = (root / "backtest-lab" / "index.html").read_text()
        self.assertIn("results/manifest.json", page)
        self.assertIn("SURVIVORSHIP", page.upper())


if __name__ == "__main__":
    unittest.main()
