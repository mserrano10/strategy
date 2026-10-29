"""QQQ condor math and the 3% VAR eject. Hand-built marks, no network."""

from __future__ import annotations

import unittest

import pandas as pd

from backtest_lab.strategies.condor import (
    Snap,
    Week,
    classify_week,
    contracts_for,
    price_week,
    prepare,
    resolve_stop,
    run_book,
    sigma_put_strike,
    strike_for_delta,
    var_stop_debit,
)


def _snap(day, open_, high, low, close, **marks):
    snap = Snap(
        day=pd.Timestamp(day),
        t_trade=4 / 365,
        settle=False,
        prior_sig=0.20,
        day_sig=0.20,
        prev_close=open_,
        open=open_,
        high=high,
        low=low,
        close=close,
    )
    for key, value in marks.items():
        setattr(snap, key, value)
    return snap


def _manual(days, credit=0.20):
    week = Week(
        entry=days[0].day,
        expiry=days[-1].day,
        sessions=days,
        spot=100.0,
        prior_sig=0.20,
        credit=credit,
        put_credit=credit / 2,
        call_credit=credit / 2,
        put_short=90.0,
        put_long=80.0,
        call_short=110.0,
        call_long=120.0,
        n_legs=4,
        tradable=True,
        manual=True,
        friday_close=days[-1].close,
    )
    days[-1].settle = True
    return week


class CondorMathTests(unittest.TestCase):
    def test_sheet_strikes_on_4_january_2010(self):
        spot = 40.21268372
        sigma = 21.64999962 / 100.0
        year_frac = 4 / 365
        put = strike_for_delta(spot, year_frac, 0.04, sigma, 0.03, "put")
        call = strike_for_delta(spot, year_frac, 0.04, sigma, 0.03, "call")
        self.assertAlmostEqual(put, 38.56, delta=0.01)
        self.assertAlmostEqual(call, 41.99, delta=0.01)
        put_025 = strike_for_delta(spot, year_frac, 0.04, sigma, 0.025, "put")
        call_02 = strike_for_delta(spot, year_frac, 0.04, sigma, 0.02, "call")
        self.assertAlmostEqual(put_025, 38.49, delta=0.01)
        self.assertAlmostEqual(call_02, 42.16, delta=0.01)
        floor = sigma_put_strike(spot, year_frac, sigma)
        # Calculator: K = S * exp(0.5*m^2 - 2.90*m), m = VXN * 1.03 * sqrt(T).
        self.assertAlmostEqual(floor, 37.5907, delta=0.001)
        self.assertLess(floor, put)

    def test_stop_fills_at_the_level_unless_the_open_has_gapped_through(self):
        fill, reason = resolve_stop(0.30, 0.90, 0.50, 10.0, allow_gap=False)
        self.assertEqual(reason, "stop")
        self.assertAlmostEqual(fill, 0.50)
        fill, reason = resolve_stop(0.80, 0.95, 0.50, 10.0, allow_gap=True)
        self.assertEqual(reason, "gap")
        self.assertAlmostEqual(fill, 0.80)
        fill, reason = resolve_stop(0.80, 0.95, 0.50, 10.0, allow_gap=False)
        self.assertEqual(reason, "stop")
        self.assertAlmostEqual(fill, 0.50)

    def test_three_percent_loss_is_net_of_the_credit(self):
        nav = 100_000.0
        credit = 0.20
        contracts = contracts_for(nav, credit, "var")
        self.assertEqual(contracts, 102)
        stop = var_stop_debit(credit, nav, contracts, 20.0)
        self.assertAlmostEqual(stop, credit + 0.03 * nav / (100 * contracts))
        entry = _snap(
            "2020-01-06",
            100,
            101,
            99,
            100,
            open_debit=0.30,
            adverse_debit=0.90,
            open_put=0.28,
            open_call=0.02,
            adverse_put=0.85,
            adverse_call=0.05,
            adverse_spot=99,
            close_debit=0.22,
        )
        week = _manual([entry], credit=credit)
        result = run_book([week], [entry.day], nav, "stop-3pct", False, "var")
        trade = result["trades"][0]
        self.assertEqual(trade["reason"], "stop")
        self.assertAlmostEqual(trade["premium_loss_pct"], 0.03, places=6)
        self.assertAlmostEqual(trade["debit"], stop, places=6)
        self.assertGreater(float(result["equity"].min()), 0)

        gap = _snap(
            "2020-01-07",
            90,
            91,
            89,
            90,
            open_debit=0.80,
            adverse_debit=0.90,
            open_put=0.75,
            open_call=0.05,
            adverse_put=0.85,
            adverse_call=0.05,
            adverse_spot=89,
        )
        calm = _snap(
            "2020-01-06",
            100,
            101,
            99,
            100,
            open_debit=0.25,
            adverse_debit=0.25,
            open_put=0.20,
            open_call=0.05,
            adverse_put=0.20,
            adverse_call=0.05,
            adverse_spot=99,
            close_debit=0.22,
        )
        week = _manual([calm, gap], credit=credit)
        result = run_book([week], [calm.day, gap.day], nav, "stop-3pct", False, "var")
        trade = result["trades"][0]
        self.assertEqual(trade["reason"], "gap")
        self.assertAlmostEqual(trade["premium_loss_pct"], 6120 / nav, places=6)
        self.assertGreater(trade["premium_loss_pct"], 0.03)
        self.assertGreater(float(result["equity"].min()), 0)

    def test_var_size_is_larger_than_desk_size(self):
        self.assertGreater(contracts_for(10_000, 0.15, "var"), contracts_for(10_000, 0.15, "desk"))

    def test_filter_priority(self):
        short = [pd.Timestamp(day).date() for day in pd.bdate_range("2018-03-19", "2018-03-22")]
        self.assertEqual(classify_week(short, 100, 100), "short")
        full = [pd.Timestamp(day).date() for day in pd.bdate_range("2018-03-19", "2018-03-23")]
        self.assertEqual(classify_week(full, 100, 100), "fed")
        self.assertEqual(classify_week(full, 103, 100), "gap")
        quiet = [pd.Timestamp(day).date() for day in pd.bdate_range("2018-04-02", "2018-04-06")]
        self.assertIsNone(classify_week(quiet, 100, 100))

    def test_friday_close_beyond_the_short_strike_is_a_bust(self):
        index = pd.bdate_range("2009-12-31", "2010-01-08")
        frame = pd.DataFrame(
            {
                "open": 500.0,
                "high": 505.0,
                "low": 495.0,
                "close": [500, 500, 500, 500, 500, 500, 400],
                "vxn": 35.0,
                "vix": 30.0,
            },
            index=index,
        )
        weeks, sessions, _note = prepare(frame, 2010, index[-1].date())
        self.assertEqual(len(weeks), 1)
        price_week(weeks[0])
        self.assertTrue(weeks[0].tradable, weeks[0].skip)
        block = weeks[0].strikes["prior"]["0.03"]
        self.assertLess(weeks[0].friday_close, block["put"])
        result = run_book(weeks, sessions, 10_000, "hold", False, "desk")
        self.assertGreater(float(result["equity"].min()), 0)
        self.assertEqual(len(result["trades"]), 1)
        self.assertGreater(
            contracts_for(10_000, weeks[0].credit, "var"),
            contracts_for(10_000, weeks[0].credit, "desk"),
        )

    def test_quiet_week_expires_instead_of_stopping_on_the_entry_spread(self):
        index = pd.bdate_range("2009-12-31", "2010-01-08")
        frame = pd.DataFrame(
            {
                "open": 500.0,
                "high": 503.0,
                "low": 497.0,
                "close": 500.0,
                "vxn": 20.0,
                "vix": 18.0,
            },
            index=index,
        )
        weeks, sessions, _note = prepare(frame, 2010, index[-1].date())
        self.assertEqual(len(weeks), 1)
        week = weeks[0]
        price_week(week)
        self.assertTrue(week.tradable, week.skip)
        traded = week.strikes["traded_put"]
        self.assertLess(traded["used"], traded["delta_rounded"])
        self.assertEqual(traded["used"], traded["sigma_rounded"])
        entry = week.sessions[0]
        self.assertLess(entry.open_debit, week.credit + 0.05)
        result = run_book(weeks, sessions, 200_000, "stop-3pct", False, "var")
        self.assertEqual(len(result["trades"]), 1)
        self.assertEqual(result["trades"][0]["reason"], "expiry")
        self.assertGreater(result["trades"][0]["pnl_dollars"], 0)


if __name__ == "__main__":
    unittest.main()
