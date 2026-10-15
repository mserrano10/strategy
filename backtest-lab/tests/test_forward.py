"""Frozen Oct 15, 2026 forward test. No network."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from backtest_lab.forward import (
    BOOK_IDS,
    LABEL,
    build_picks,
    close_is_due,
    mark_to_market,
    marked_equity,
    session_complete,
)

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "backtest-lab" / "results"

# Published backtest totals. The forward test must not rewrite these files.
BACKTEST_TOTALS = {
    "best-returner": 66.8791814392,
    "best-returner-pit": 408.0305085154,
    "top5-returners": 245.5486783204,
    "top5-returners-pit": 35.5494703854,
    "best-returner-10b": 6.0043002814,
    "best-returner-2b": -0.4229250064,
    "top5-returners-10b": 17.0050908050,
    "top5-returners-2b": 0.6868764908,
    "biggest": 10.0179093654,
    "top5-biggest": 11.2637766219,
    "tqqq-two-bullet": 5.0987868127,
    "tqqq-buy-hold": 37.4690435095,
}


class ForwardPickTests(unittest.TestCase):
    def setUp(self):
        self.picks = build_picks(RESULTS)

    def test_label_and_inception(self):
        self.assertEqual(self.picks["label"], LABEL)
        self.assertEqual(self.picks["label"], "Forward test, live since Oct 15, 2026")
        self.assertEqual(self.picks["inception"], "2026-10-15")
        self.assertIsNone(self.picks["entry_session"])
        self.assertEqual([book["id"] for book in self.picks["books"]], list(BOOK_IDS))

    def test_cohorts_match_published_2026_holdings(self):
        by_id = {book["id"]: book for book in self.picks["books"]}
        for book_id in BOOK_IDS:
            report = json.loads((RESULTS / f"{book_id}.json").read_text())
            cohort = next(row for row in report["cohorts"] if row["year"] == 2026)
            frozen = [(row["ticker"], row["weight"]) for row in by_id[book_id]["holdings"]]
            published = [(row["ticker"], row["weight"]) for row in cohort["holdings"]]
            self.assertEqual(frozen, published)
            self.assertIn("Oct 15, 2026", by_id[book_id]["derivation"])
            if book_id == "tqqq-two-bullet":
                self.assertEqual(by_id[book_id]["signal_date"], cohort["period_end"])
            else:
                self.assertEqual(by_id[book_id]["signal_date"], cohort["rebalance_date"])
                self.assertIn(cohort["rebalance_date"], by_id[book_id]["derivation"])

    def test_named_winners(self):
        by_id = {book["id"]: book for book in self.picks["books"]}
        self.assertEqual(by_id["best-returner"]["holdings"][0]["ticker"], "ECHO")
        self.assertEqual(by_id["best-returner-pit"]["holdings"][0]["ticker"], "WDC")
        self.assertEqual(by_id["best-returner-10b"]["holdings"][0]["ticker"], "LITE")
        self.assertEqual(by_id["best-returner-2b"]["holdings"][0]["ticker"], "CELC")
        self.assertEqual(
            [row["ticker"] for row in by_id["top5-returners"]["holdings"]],
            ["ECHO", "LITE", "BE", "WDC", "MU"],
        )
        self.assertEqual(
            [row["ticker"] for row in by_id["top5-returners-pit"]["holdings"]],
            ["WDC", "MU", "STX", "HOOD", "NEM"],
        )
        self.assertEqual(
            [row["ticker"] for row in by_id["top5-returners-10b"]["holdings"]],
            ["LITE", "HL", "BE", "AU", "IREN"],
        )
        self.assertEqual(
            [row["ticker"] for row in by_id["top5-returners-2b"]["holdings"]],
            ["CELC", "ALM", "TMC", "PL", "GRAL"],
        )
        self.assertEqual(by_id["biggest"]["holdings"][0]["ticker"], "NVDA")
        self.assertEqual(
            [row["ticker"] for row in by_id["top5-biggest"]["holdings"]],
            ["NVDA", "AAPL", "GOOGL", "GOOG", "MSFT"],
        )

    def test_two_bullet_is_the_post_exit_mix(self):
        book = next(row for row in self.picks["books"] if row["id"] == "tqqq-two-bullet")
        weights = {(row["ticker"], row["sleeve"]): row["weight"] for row in book["holdings"]}
        self.assertEqual(weights[("TQQQ", "bullet-1")], 0.0)
        self.assertEqual(weights[("TQQQ", "bullet-2")], 0.0)
        self.assertAlmostEqual(weights[("SPY", "core")], 0.6705914163902598)
        self.assertAlmostEqual(weights[("BIL", "bullet-1")], 0.2571593027697674)
        self.assertAlmostEqual(weights[("BIL", "bullet-2")], 0.07224928083997285)
        self.assertIn("not a new 90/10", book["derivation"])
        self.assertIn("2026-05-26", book["derivation"])

    def test_size_floor_derivation_is_the_listing_universe(self):
        book = next(row for row in self.picks["books"] if row["id"] == "best-returner-10b")
        self.assertIn("at least $10B", book["derivation"])
        self.assertIn("+1000%", book["derivation"])
        self.assertNotIn("today's S&P 500", book["derivation"])

    def test_cap_books_document_the_shared_sleeve(self):
        biggest = next(row for row in self.picks["books"] if row["id"] == "biggest")
        top5 = next(row for row in self.picks["books"] if row["id"] == "top5-biggest")
        self.assertIn("share this sleeve", biggest["derivation"])
        self.assertIn("GOOG", top5["derivation"])
        self.assertNotIn("tqqq-buy-hold", [book["id"] for book in self.picks["books"]])


class MarkTests(unittest.TestCase):
    def test_session_complete_waits_for_21_utc(self):
        session = date(2026, 10, 15)
        before = datetime(2026, 10, 15, 20, 59, tzinfo=timezone.utc)
        after = datetime(2026, 10, 15, 21, 0, tzinfo=timezone.utc)
        self.assertFalse(session_complete(session, before))
        self.assertTrue(session_complete(session, after))
        self.assertFalse(close_is_due(session, before))
        self.assertTrue(close_is_due(session, after))
        self.assertFalse(session_complete(date(2026, 10, 17), after))

    def test_marked_equity_ignores_a_zero_weight(self):
        holdings = [
            {"ticker": "SPY", "weight": 0.67},
            {"ticker": "TQQQ", "weight": 0.0},
            {"ticker": "BIL", "weight": 0.33},
        ]
        entries = {"SPY": 100.0, "BIL": 50.0}
        prices = {"SPY": 110.0, "BIL": 50.0, "TQQQ": 1.0}
        self.assertEqual(marked_equity(holdings, prices, entries, 10_000, is_entry=True), 10_000)
        self.assertAlmostEqual(
            marked_equity(holdings, prices, entries, 10_000, is_entry=False),
            10_670.0,
        )

    def test_partial_bar_does_not_lock_entries(self):
        picks = _one_name_picks()
        panel = pd.DataFrame(
            {"ECHO": [90.0, 100.0], "SPY": [400.0, 410.0]},
            index=pd.to_datetime(["2026-10-14", "2026-10-15"]),
        )
        now = datetime(2026, 10, 15, 15, 6, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            published = mark_to_market(
                now=now,
                prices=panel,
                picks_file=folder / "picks.json",
                published_file=folder / "forward-test.json",
                write=True,
            )
            stored = json.loads((folder / "picks.json").read_text())
        self.assertEqual(published["status"], "awaiting-close")
        self.assertIsNone(stored["entry_session"])
        self.assertIsNone(stored["books"][0]["holdings"][0]["entry_price"])
        self.assertEqual(published["books"][0]["equity"], [])
        self.assertEqual(published["label"], LABEL)

    def test_completed_close_locks_once_and_marks_the_next_session(self):
        picks = _one_name_picks()
        first = pd.DataFrame(
            {"ECHO": [100.0], "SPY": [400.0]},
            index=pd.to_datetime(["2026-10-15"]),
        )
        later = pd.DataFrame(
            {"ECHO": [90.0, 110.0], "SPY": [390.0, 440.0]},
            index=pd.to_datetime(["2026-10-15", "2026-10-16"]),
        )
        now = datetime(2026, 10, 16, 21, 30, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            picks_file = folder / "picks.json"
            published_file = folder / "forward-test.json"
            picks_file.write_text(json.dumps(picks))
            mark_to_market(now=now, prices=first, picks_file=picks_file, published_file=published_file)
            locked = json.loads(picks_file.read_text())
            self.assertEqual(locked["entry_session"], "2026-10-15")
            self.assertEqual(locked["books"][0]["holdings"][0]["entry_price"], 100.0)
            self.assertEqual(locked["spy_entry_price"], 400.0)
            published = mark_to_market(
                now=now,
                prices=later,
                picks_file=picks_file,
                published_file=published_file,
            )
            still = json.loads(picks_file.read_text())
        self.assertEqual(still["books"][0]["holdings"][0]["entry_price"], 100.0)
        self.assertEqual(still["spy_entry_price"], 400.0)
        book = published["books"][0]
        self.assertEqual([row["equity"] for row in book["equity"]], [10_000.0, 11_000.0])
        self.assertEqual([row["equity"] for row in published["spy"]["equity"]], [10_000.0, 11_000.0])
        self.assertAlmostEqual(book["metrics"]["total_return"], 0.10)
        self.assertEqual(published["spy"]["name"], "S&P 500 (SPY)")

    def test_equal_weight_book(self):
        holdings = [
            {"ticker": "AAA", "weight": 0.5},
            {"ticker": "BBB", "weight": 0.5},
        ]
        entries = {"AAA": 10.0, "BBB": 20.0}
        prices = {"AAA": 12.0, "BBB": 20.0}
        self.assertAlmostEqual(marked_equity(holdings, prices, entries, 10_000, is_entry=False), 11_000.0)


class PageAndBacktestTests(unittest.TestCase):
    def test_page_has_the_forward_label_and_the_portal_does_not_link_it(self):
        page = (ROOT / "backtest-lab" / "index.html").read_text()
        portal = (ROOT / "index.html").read_text()
        self.assertIn("Forward test, live since Oct 15, 2026", page)
        self.assertIn("results/forward-test.json", page)
        self.assertNotIn("backtest-lab", portal)

    def test_workflow_refreshes_weekday_after_the_close(self):
        workflow = (ROOT / ".github" / "workflows" / "forward-test.yml").read_text()
        self.assertIn("30 21 * * 1-5", workflow)
        self.assertIn("backtest-lab/forward/picks.json", workflow)
        self.assertIn("backtest-lab/results/forward-test.json", workflow)
        self.assertIn("python -m backtest_lab forward-mark", workflow)
        self.assertNotIn("backtest_lab run", workflow)

    def test_published_backtest_totals_are_unchanged(self):
        for book_id, total in BACKTEST_TOTALS.items():
            payload = json.loads((RESULTS / f"{book_id}.json").read_text())
            self.assertAlmostEqual(payload["metrics"]["total_return"], total, places=8)


def _one_name_picks() -> dict:
    return {
        "label": LABEL,
        "inception": "2026-10-15",
        "initial_capital": 10000.0,
        "entry_session": None,
        "spy_entry_price": None,
        "entry_rule": "test",
        "ambiguities": [],
        "books": [
            {
                "id": "best-returner",
                "name": "Best Returner",
                "cohort_year": 2026,
                "signal_date": "2025-12-31",
                "derivation": "ECHO.",
                "holdings": [
                    {
                        "ticker": "ECHO",
                        "name": "EchoStar",
                        "weight": 1.0,
                        "entry_price": None,
                        "price_field": "adjusted_close",
                    }
                ],
            }
        ],
    }


if __name__ == "__main__":
    unittest.main()
