import io
import unittest
import zipfile
from datetime import date, datetime

from scraper import (
    ET_ZONE,
    annual_ms_baseline,
    buffett_estimate,
    classify_scheduled_date,
    drop_open_session,
    fred_quarter,
    multpl_anchor,
    parse_cmv_buffett,
    parse_cmv_public_next,
    parse_fred_csv,
    parse_next_z1_release,
    parse_z1_ncb,
    wilshire_scale_ok,
    z1_quarter,
)


class MacroInputTests(unittest.TestCase):
    def test_fred_quarter_uses_the_quarter_end(self):
        self.assertEqual(fred_quarter("2026-04-01"), (date(2026, 6, 30), "2026Q2"))
        self.assertEqual(z1_quarter("2026:Q2"), (date(2026, 6, 30), "2026Q2"))

    def test_z1_package_reads_the_ncb_series(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr(
                "csv/S11_1_b.csv",
                "date,FL102090005.Q,FL103164106.Q\n"
                "2026:Q1,37735884,214.82799\n"
                "2026:Q2,39097641,242.306115\n",
            )
        value, as_of, quarter = parse_z1_ncb(buffer.getvalue())
        self.assertAlmostEqual(value, 242.306115)
        self.assertEqual(as_of, date(2026, 6, 30))
        self.assertEqual(quarter, "2026Q2")

    def test_fred_csv_skips_missing_observations(self):
        rows = parse_fred_csv("DATE,GDP\n2026-04-01,32563.030\n2026-07-01,.\n2026-07-01,33056.203\n")
        self.assertEqual(rows[-1], ("2026-07-01", 33056.203))

    def test_buffett_estimate_uses_the_wilshire_move_and_latest_gdp(self):
        # 30 Jun 2026 Wilshire close and the 24 Nov 2026 close, over the
        # Q3 2026 second-estimate GDP in billions.
        estimate = buffett_estimate(78.10, 75293.0859375, 71818.9296875, 33056.203)
        self.assertEqual(round(estimate, 1), 225.4)
        self.assertTrue(wilshire_scale_ok(78.10, 75293.0859375))
        self.assertFalse(wilshire_scale_ok(78.10, 20000))

    def test_official_page_parser(self):
        html = (
            "As of June 30, 2026 we calculate the Buffett Indicator as 244%."
            "Total US Stock Market Value = $78.10T"
            "Annualized GDP = $32.06T"
        )
        parsed = parse_cmv_buffett(html)
        self.assertEqual(parsed["as_of"], "2026-06-30")
        self.assertEqual(parsed["value"], 244.0)
        self.assertEqual(parsed["market_cap_trillions"], 78.10)

    def test_public_schedule_that_has_passed_is_overdue(self):
        html = '<td>On Nov 15, with Oct 01 data</td>'
        scheduled = parse_cmv_public_next(html, date(2026, 11, 25))
        self.assertEqual(scheduled, date(2026, 11, 15))
        next_date, kind = classify_scheduled_date(scheduled, date(2026, 11, 25), grace_days=3)
        self.assertEqual((next_date, kind), ("2026-11-15", "overdue"))

    def test_z1_release_sentence(self):
        html = "The next Z.1 release will be Thursday, December 10, 2026, at 12:00 noon."
        self.assertEqual(parse_next_z1_release(html), "2026-12-10")

    def test_open_session_is_not_treated_as_a_close(self):
        pairs = [("2026-11-24", 7201.0), ("2026-11-25", 6982.0)]
        morning = datetime(2026, 11, 25, 9, 49, tzinfo=ET_ZONE)
        after_close = datetime(2026, 11, 25, 16, 20, tzinfo=ET_ZONE)
        self.assertEqual(drop_open_session(pairs, morning), [("2026-11-24", 7201.0)])
        self.assertEqual(drop_open_session(pairs, after_close), pairs)

    def test_scaled_multpl_keeps_the_original_anchor(self):
        item = {
            "value": 36.26,
            "as_of": "2026-11-24",
            "method": "spx_scaled",
            "anchor_value": 36.44,
            "anchor_as_of": "2026-11-23",
            "source": "multpl.com reading of 2026-11-23 scaled by the S&P 500 move",
        }
        self.assertEqual(multpl_anchor(item), (36.44, "2026-11-23"))

    def test_ms_baseline_uses_post_1950_january_observations_only(self):
        points = [{"date": f"{year}-01-01", "index": 1.0} for year in range(1950, 1990)]
        points.append({"date": "1989-11-23", "index": 9.0})
        points[0]["index"] = 0.5
        baseline = annual_ms_baseline(points)
        self.assertEqual(baseline["n"], 40)
        self.assertEqual(baseline["start"], "1950-01-01")
        self.assertAlmostEqual(baseline["mean"], 39.5 / 40)


if __name__ == "__main__":
    unittest.main()
