"""QQQ weekly iron condor.

The tracking sheet's delta columns are Black–Scholes 0.03 / 0.025 / 0.02.
The traded put is the further of that 0.03-delta strike and the calculator's
2.90-sigma floor (VXN × 1.03). The traded call stays at 0.03 delta. T is
calendar days to expiry / 365, r = 4%, q = 0. The wing is a fixed 10 points.
There is no profit target.

Three exits are books, plus one filtered book:

- hold to the Friday intrinsic settlement
- buy back once the cost to close is 2× the credit collected
- buy back once the loss, net of that week's credit, reaches 3% of VAR

VAR is the account net worth at Monday's entry. That definition is an
assumption the page asks the reader to confirm. Every book sizes contracts
from the calculator's desk budget: 12.5% of net worth, times the 1.5 edge
cap. The 3% eject is still 3% of the whole net worth.

Prices are modelled. There is no historical option tape. Credits are compared
with recorded fills only as percentages of spot.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from statistics import NormalDist

import pandas as pd

_NORMAL = NormalDist()

from backtest_lab.metrics import compute_metrics, drawdown_series
from backtest_lab.report import _equity_rows, assemble_report
from backtest_lab.strategies.base import RunContext
from backtest_lab.strategies.condor_calendar import CPI, FOMC

WIDTH = 10.0
RATE = 0.04
DELTAS = (0.03, 0.025, 0.02)
PRIMARY = 0.03
VOL_MULT = 1.03
PUT_SIGMA_FLOOR = 2.90
COMMISSION = 0.65
HALF_FLOOR = 0.035
HALF_FRAC = 0.10
DESK_FRACTION = 0.125 * 1.5
VAR_LOSS = 0.03
REFERENCE_VERTICAL = 0.65
RUIN_FLOOR = 0.01
FRIDAY_RESIDUAL = 0.25 / 365.0
PUT_SKEW_SLOPE = 0.0
PUT_SKEW_CAP = 0.0
CALL_VOL_SHIFT = 0.0
VIX_SCALE_DEFAULT = 1.15
SHEET_START = pd.Timestamp("2010-01-04")
SHEET_END = pd.Timestamp("2026-04-20")
SHEET_WEEKS = 850
SHEET_PUT = {"0.03": 0.0400, "0.025": 0.0376, "0.02": 0.0306}
SHEET_CALL = {"0.03": 0.0270, "0.025": 0.0224, "0.02": 0.0165}
ACCOUNTS = (10_000.0, 200_000.0)

VARIANTS = (
    {
        "id": "qqq-condor-hold",
        "name": "QQQ Condor — hold",
        "variant": "hold",
        "filters": False,
        "sizing": "desk",
        "blurb": "Sell the Monday condor and hold it to the Friday intrinsic settlement. The put is at or beyond 2.90 sigma. The call is the 0.03 delta.",
    },
    {
        "id": "qqq-condor-stop-2x",
        "name": "QQQ Condor — 2× credit",
        "variant": "stop-2x",
        "filters": False,
        "sizing": "desk",
        "blurb": "Same entry. Buy the condor back once the cost to close reaches twice the credit collected.",
    },
    {
        "id": "qqq-condor-stop-3pct",
        "name": "QQQ Condor — 3% of VAR",
        "variant": "stop-3pct",
        "filters": False,
        "sizing": "desk",
        "blurb": "Desk size. Exit once the loss, net of the week's credit, reaches 3% of Monday net worth.",
    },
    {
        "id": "qqq-condor-filtered",
        "name": "QQQ Condor — 3% of VAR, filtered",
        "variant": "stop-3pct",
        "filters": True,
        "sizing": "desk",
        "blurb": "The 3% VAR eject at desk size, skipping holiday-short weeks, 2% Monday gaps, FOMC weeks, and CPI weeks.",
    },
)


def _norm_cdf(x: float) -> float:
    return _NORMAL.cdf(x)


def _norm_ppf(p: float) -> float:
    return _NORMAL.inv_cdf(p)


def strike_for_delta(spot: float, year_frac: float, rate: float, sigma: float, delta: float, kind: str) -> float:
    """Strike whose Black–Scholes delta equals `delta` (a positive number)."""
    if spot <= 0 or year_frac <= 0 or sigma <= 0:
        raise ValueError("spot, time, and vol must be positive")
    vol = sigma * math.sqrt(year_frac)
    d1 = -_norm_ppf(delta) if kind == "put" else _norm_ppf(delta)
    log_s_over_k = d1 * vol - (rate + 0.5 * sigma * sigma) * year_frac
    return spot * math.exp(-log_s_over_k)


def round_strike(strike: float) -> float:
    return float(math.floor(strike + 0.5))


def sigma_put_strike(spot: float, year_frac: float, sigma: float, z: float = PUT_SIGMA_FLOOR) -> float:
    """Put short strike z sigma below spot. Distance uses VXN × 1.03, as the calculator does."""
    move = sigma * VOL_MULT * math.sqrt(year_frac)
    if spot <= 0 or move <= 0:
        raise ValueError("spot and sigma distance must be positive")
    return spot * math.exp(0.5 * move * move - z * move)


def bs_price(spot: float, strike: float, year_frac: float, rate: float, sigma: float, kind: str) -> float:
    intrinsic = _intrinsic(spot, strike, kind)
    if year_frac <= 1e-8 or sigma <= 1e-8 or spot <= 0 or strike <= 0:
        return intrinsic
    vol = sigma * math.sqrt(year_frac)
    d1 = (math.log(spot / strike) + (rate + 0.5 * sigma * sigma) * year_frac) / vol
    d2 = d1 - vol
    discount = math.exp(-rate * year_frac)
    if kind == "call":
        return max(0.0, spot * _norm_cdf(d1) - strike * discount * _norm_cdf(d2))
    return max(0.0, strike * discount * _norm_cdf(-d2) - spot * _norm_cdf(-d1))


def _intrinsic(spot: float, strike: float, kind: str) -> float:
    if kind == "call":
        return max(0.0, spot - strike)
    return max(0.0, strike - spot)


def half_spread(mid: float) -> float:
    return max(HALF_FLOOR, HALF_FRAC * max(mid, 0.0))


def skewed_sigma(base: float, spot: float, strike: float, kind: str) -> float:
    if spot <= 0:
        return max(0.01, base + (CALL_VOL_SHIFT if kind == "call" else 0.0))
    if kind == "put":
        otm = max(0.0, 1.0 - strike / spot)
        return max(0.01, base + min(PUT_SKEW_CAP, PUT_SKEW_SLOPE * otm))
    return max(0.01, base + CALL_VOL_SHIFT)


def vol_bump(spot: float, ref: float) -> float:
    """Spot-vol add-on for a stop mark. +1.5 vol points per 1% drop, capped."""
    if ref <= 0 or spot <= 0:
        return 0.0
    move = spot / ref - 1.0
    if move < 0:
        return min(0.25, 1.5 * (-move))
    return min(0.10, 0.4 * move)


def vertical_credit(short_mid: float, long_mid: float) -> float:
    """Sell the short at the bid and buy the wing at the ask."""
    short_bid = short_mid - half_spread(short_mid)
    long_ask = long_mid + half_spread(long_mid)
    return short_bid - long_ask


def vertical_debit(short_mid: float, long_mid: float, width: float = WIDTH) -> float:
    """Buy the short at the ask and sell the wing at the bid. Clamped to the wing."""
    short_ask = short_mid + half_spread(short_mid)
    long_bid = max(0.0, long_mid - half_spread(long_mid))
    return min(width, max(0.0, short_ask - long_bid))


def intrinsic_vertical(spot: float, short: float, long: float, kind: str) -> float:
    if kind == "put":
        raw = max(0.0, short - spot) - max(0.0, long - spot)
    else:
        raw = max(0.0, spot - short) - max(0.0, spot - long)
    return min(WIDTH, max(0.0, raw))


def contracts_for(nav: float, credit: float, sizing: str) -> int:
    risk = (WIDTH - credit) * 100.0
    if risk <= 0 or nav <= 0:
        return 0
    budget = nav if sizing == "var" else nav * DESK_FRACTION
    return int(budget // risk)


def var_stop_debit(credit: float, nav: float, contracts: int, max_debit: float) -> float:
    """Cost to close at which the loss, net of credit, equals 3% of VAR."""
    if contracts <= 0:
        return max_debit
    debit = credit + (VAR_LOSS * nav) / (100.0 * contracts)
    return min(max_debit, max(0.0, debit))


def resolve_stop(
    open_debit: float,
    adverse_debit: float,
    stop_debit: float,
    max_debit: float,
    allow_gap: bool,
) -> tuple[float | None, str | None]:
    """Fill a stop. The entry open is not a gap. A later open through the stop fills at the open."""
    open_debit = min(max_debit, max(0.0, open_debit))
    adverse_debit = min(max_debit, max(0.0, adverse_debit))
    stop_debit = min(max_debit, max(0.0, stop_debit))
    if allow_gap and open_debit + 1e-9 >= stop_debit:
        return open_debit, "gap"
    if adverse_debit + 1e-9 >= stop_debit:
        return stop_debit, "stop"
    return None, None


def classify_week(days: list[date], entry_open: float, prior_close: float | None) -> str | None:
    """First matching reason: short week, then a 2% gap, then FOMC, then CPI."""
    reasons = set()
    if len(days) < 5:
        reasons.add("short")
    if prior_close and prior_close > 0 and abs(entry_open / prior_close - 1.0) >= 0.02:
        reasons.add("gap")
    window = set(days)
    if days and days[0].weekday() == 0:
        window.add(days[0] - timedelta(days=1))
    if window & FOMC:
        reasons.add("fed")
    if window & CPI:
        reasons.add("cpi")
    for key in ("short", "gap", "fed", "cpi"):
        if key in reasons:
            return key
    return None


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return float(0.5 * (ordered[mid - 1] + ordered[mid]))


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return float(sum(values) / len(values))


@dataclass
class Snap:
    day: pd.Timestamp
    t_trade: float
    settle: bool
    prior_sig: float
    day_sig: float
    prev_close: float
    open: float
    high: float
    low: float
    close: float
    open_debit: float = 0.0
    adverse_debit: float = 0.0
    close_debit: float = 0.0
    open_put: float = 0.0
    open_call: float = 0.0
    adverse_put: float = 0.0
    adverse_call: float = 0.0
    close_put: float = 0.0
    close_call: float = 0.0
    adverse_spot: float = 0.0


@dataclass
class Week:
    entry: pd.Timestamp
    expiry: pd.Timestamp
    sessions: list[Snap]
    spot: float
    prior_sig: float
    credit: float = 0.0
    put_credit: float = 0.0
    call_credit: float = 0.0
    put_short: float | None = None
    put_long: float | None = None
    call_short: float | None = None
    call_long: float | None = None
    put_short_raw: float | None = None
    call_short_raw: float | None = None
    put_basis: float | None = None
    call_basis: float | None = None
    n_legs: int = 0
    tradable: bool = False
    skip: str | None = None
    filter_reason: str | None = None
    strikes: dict = field(default_factory=dict)
    friday_close: float = 0.0
    manual: bool = False

    @property
    def max_debit(self) -> float:
        sides = (1 if self.put_short is not None else 0) + (1 if self.call_short is not None else 0)
        return WIDTH * max(sides, 1)


def _leg_mids(spot: float, short: float, long: float, year_frac: float, sigma: float, kind: str, prev_close: float, stress: bool) -> tuple[float, float]:
    base = sigma + (vol_bump(spot, prev_close) if stress else 0.0)
    short_sig = skewed_sigma(base, spot, short, kind)
    long_sig = skewed_sigma(base, spot, long, kind)
    if year_frac <= 1e-8:
        return _intrinsic(spot, short, kind), _intrinsic(spot, long, kind)
    return (
        bs_price(spot, short, year_frac, RATE, short_sig, kind),
        bs_price(spot, long, year_frac, RATE, long_sig, kind),
    )


def _vertical_at(week: Week, spot: float, year_frac: float, sigma: float, prev_close: float, stress: bool, kind: str) -> float:
    short = week.put_short if kind == "put" else week.call_short
    long = week.put_long if kind == "put" else week.call_long
    if short is None or long is None:
        return 0.0
    if year_frac <= 1e-8:
        return intrinsic_vertical(spot, short, long, kind)
    short_mid, long_mid = _leg_mids(spot, short, long, year_frac, sigma, kind, prev_close, stress)
    return vertical_debit(short_mid, long_mid)


def _anchored(debit: float, basis: float | None, credit: float) -> float:
    """Cost to close, with the entry debit shifted so the sale itself is not a loss.

    The half-spread floor is what lines the entry credit up with recorded fills.
    Charging that same spread again as the opening mark put the condor within a
    few cents of the 3% stop before the market moved. The loss is the later
    rise in the close cost: debit now, minus the entry debit, plus the credit.
    """
    if basis is None:
        return debit
    return min(WIDTH, max(0.0, debit - basis + credit))


def structure_at(week: Week, spot: float, year_frac: float, sigma: float, prev_close: float, stress: bool) -> tuple[float, float, float]:
    put = _vertical_at(week, spot, year_frac, sigma, prev_close, stress, "put")
    call = _vertical_at(week, spot, year_frac, sigma, prev_close, stress, "call")
    if week.put_short is not None:
        put = _anchored(put, week.put_basis, week.put_credit)
    if week.call_short is not None:
        call = _anchored(call, week.call_basis, week.call_credit)
    return put + call, put, call


def _price_credit(week: Week, short: float, long: float, kind: str) -> float:
    snap = week.sessions[0]
    short_mid, long_mid = _leg_mids(snap.open, short, long, snap.t_trade, week.prior_sig, kind, snap.prev_close, False)
    return vertical_credit(short_mid, long_mid)


def price_week(week: Week) -> None:
    """Fill credits and the daily marks. Manual weeks already carry their marks."""
    if week.manual or not week.sessions:
        return
    snap0 = week.sessions[0]
    if week.put_short is None or snap0.t_trade <= 0 or not math.isfinite(week.prior_sig):
        week.tradable = False
        week.skip = week.skip or "no-vol"
        return
    put_credit = _price_credit(week, week.put_short, week.put_long, "put")
    call_credit = 0.0
    if week.call_short is not None:
        call_credit = _price_credit(week, week.call_short, week.call_long, "call")
    # A 2.90-sigma put is often inside the half-spread floor. A negative model
    # credit means there was no bid; the wing is still on, for no credit.
    if put_credit < 0:
        put_credit = 0.0
    if call_credit <= 0:
        call_credit = 0.0
        week.call_short = None
        week.call_long = None
    if put_credit <= 0 and call_credit <= 0:
        week.tradable = False
        week.skip = "no-credit"
        week.put_credit = put_credit
        week.call_credit = call_credit
        return
    week.put_credit = put_credit
    week.call_credit = call_credit
    week.credit = put_credit + call_credit
    week.n_legs = (2 if week.put_short is not None else 0) + (2 if week.call_short is not None else 0)
    week.tradable = True
    week.put_basis = _vertical_at(week, snap0.open, snap0.t_trade, week.prior_sig, snap0.prev_close, False, "put")
    week.call_basis = (
        _vertical_at(week, snap0.open, snap0.t_trade, week.prior_sig, snap0.prev_close, False, "call")
        if week.call_short is not None
        else None
    )
    week.entry_open_debit = 0.0
    for snap in week.sessions:
        trade_t = snap.t_trade
        debit_o, put_o, call_o = structure_at(week, snap.open, trade_t, snap.prior_sig, snap.prev_close, True)
        debit_l, put_l, call_l = structure_at(week, snap.low, trade_t, snap.prior_sig, snap.prev_close, True)
        debit_h, put_h, call_h = structure_at(week, snap.high, trade_t, snap.prior_sig, snap.prev_close, True)
        if debit_l >= debit_h:
            snap.adverse_debit, snap.adverse_put, snap.adverse_call, snap.adverse_spot = debit_l, put_l, call_l, snap.low
        else:
            snap.adverse_debit, snap.adverse_put, snap.adverse_call, snap.adverse_spot = debit_h, put_h, call_h, snap.high
        snap.open_debit, snap.open_put, snap.open_call = debit_o, put_o, call_o
        if snap.settle:
            put_c = intrinsic_vertical(snap.close, week.put_short, week.put_long, "put")
            call_c = intrinsic_vertical(snap.close, week.call_short, week.call_long, "call") if week.call_short else 0.0
        else:
            _, put_c, call_c = structure_at(week, snap.close, trade_t, snap.day_sig, snap.prev_close, False)
        snap.close_put, snap.close_call, snap.close_debit = put_c, call_c, put_c + call_c
    week.entry_open_debit = week.sessions[0].open_debit


def tested_vertical(week: Week, snap: Snap, stop_debit: float) -> tuple[float, float, str]:
    """Debit of the breached vertical, and of the other side, at the stop."""
    if week.manual or week.put_short is None:
        span = snap.adverse_debit - snap.open_debit
        weight = 0.0 if span <= 1e-9 else (stop_debit - snap.open_debit) / span
        weight = min(1.0, max(0.0, weight))
        put = snap.open_put + weight * (snap.adverse_put - snap.open_put)
        call = snap.open_call + weight * (snap.adverse_call - snap.open_call)
    else:
        safe_spot = snap.open
        breach_spot = snap.adverse_spot
        put = snap.adverse_put
        call = snap.adverse_call
        for _ in range(28):
            mid = 0.5 * (safe_spot + breach_spot)
            debit, put, call = structure_at(week, mid, snap.t_trade, snap.prior_sig, snap.prev_close, True)
            if debit + 1e-9 >= stop_debit:
                breach_spot = mid
            else:
                safe_spot = mid
    if put >= call:
        return put, call, "put"
    return call, put, "call"


def _split_open(snap: Snap, both: bool) -> tuple[float, float, str]:
    if not both:
        if snap.open_put >= snap.open_call:
            return snap.open_put, 0.0, "put"
        return snap.open_call, 0.0, "call"
    if snap.open_put >= snap.open_call:
        return snap.open_put, snap.open_call, "put"
    return snap.open_call, snap.open_put, "call"


def run_book(weeks: list[Week], sessions: list[pd.Timestamp], capital: float, variant: str, filters: bool, sizing: str) -> dict:
    cash = float(capital)
    ruined = False
    mark_floored = False
    trades: list[dict] = []
    skipped = {"short": 0, "gap": 0, "fed": 0, "cpi": 0, "no-credit": 0, "no-vol": 0, "size": 0}
    equity_rows: list[tuple[pd.Timestamp, float]] = []
    by_day: dict[pd.Timestamp, tuple[Week, Snap]] = {}
    for week in weeks:
        for snap in week.sessions:
            by_day[snap.day] = (week, snap)
    active: dict | None = None

    for day in sessions:
        pair = by_day.get(day)
        if pair is None:
            equity_rows.append((day, max(cash, RUIN_FLOOR)))
            continue
        week, snap = pair
        if snap is week.sessions[0]:
            active = None
            if ruined:
                pass
            elif filters and week.filter_reason:
                skipped[week.filter_reason] += 1
            elif not week.tradable:
                skipped[week.skip or "no-vol"] += 1
            else:
                nav = cash
                contracts = contracts_for(nav, week.credit, sizing)
                if contracts < 1:
                    skipped["size"] += 1
                else:
                    entry_fees = COMMISSION * contracts * week.n_legs
                    cash += week.credit * 100.0 * contracts - entry_fees
                    if variant == "stop-2x":
                        stop = min(week.max_debit, 2.0 * week.credit)
                    elif variant == "stop-3pct":
                        stop = var_stop_debit(week.credit, nav, contracts, week.max_debit)
                    else:
                        stop = None
                    active = {
                        "nav": nav,
                        "contracts": contracts,
                        "stop": stop,
                        "entry_fees": entry_fees,
                    }
        if active is None:
            shown = cash if cash > RUIN_FLOOR else RUIN_FLOOR
            equity_rows.append((day, shown))
            continue

        allow_gap = snap is not week.sessions[0]
        fill, reason = (None, None)
        if active["stop"] is not None:
            fill, reason = resolve_stop(snap.open_debit, snap.adverse_debit, active["stop"], week.max_debit, allow_gap)
        if fill is not None:
            contracts = active["contracts"]
            exit_fees = COMMISSION * contracts * week.n_legs
            cash -= fill * 100.0 * contracts + exit_fees
            both = week.call_short is not None and week.put_short is not None
            if reason == "gap":
                tested, safe, side = _split_open(snap, both)
            else:
                tested, safe, side = tested_vertical(week, snap, fill)
            pnl = cash - active["nav"]
            if cash <= RUIN_FLOOR:
                cash = RUIN_FLOOR
                ruined = True
            trades.append(_trade(week, snap, reason, fill, tested, safe, side, pnl, active, contracts))
            active = None
            equity_rows.append((day, cash))
            continue

        if snap.settle:
            contracts = active["contracts"]
            cash -= snap.close_debit * 100.0 * contracts
            both = week.call_short is not None and week.put_short is not None
            tested, safe, side = _split_open(snap, both)
            if snap.close_put >= snap.close_call:
                tested, safe, side = snap.close_put, snap.close_call, "put"
            else:
                tested, safe, side = snap.close_call, snap.close_put, "call"
            pnl = cash - active["nav"]
            if cash <= RUIN_FLOOR:
                cash = RUIN_FLOOR
                ruined = True
            trades.append(_trade(week, snap, "expiry", snap.close_debit, tested, safe, side, pnl, active, contracts))
            active = None
            equity_rows.append((day, cash))
            continue

        marked = cash - snap.close_debit * 100.0 * active["contracts"]
        if marked <= RUIN_FLOOR:
            mark_floored = True
            marked = RUIN_FLOOR
        equity_rows.append((day, marked))

    equity = pd.Series(dict(equity_rows), dtype=float).sort_index()
    equity = equity[~equity.index.duplicated(keep="last")]
    return {
        "equity": equity,
        "trades": trades,
        "skipped": skipped,
        "ruined": ruined,
        "mark_floored": mark_floored,
        "final_cash": cash,
    }


def _trade(week: Week, snap: Snap, reason: str, debit: float, tested: float, safe: float, side: str, pnl: float, active: dict, contracts: int) -> dict:
    nav = float(active["nav"])
    premium_loss = (debit - week.credit) * 100.0 * contracts
    structure = "iron condor" if week.call_short is not None and week.put_short is not None else "put spread"
    return {
        "date": pd.Timestamp(week.entry).date().isoformat(),
        "exit": pd.Timestamp(snap.day).date().isoformat(),
        "structure": structure,
        "reason": reason,
        "side": side,
        "pnl_dollars": pnl,
        "pnl_pct": pnl / nav if nav else None,
        "premium_loss_pct": premium_loss / nav if nav else None,
        "debit": debit,
        "credit": week.credit,
        "tested_vertical": tested,
        "safe_vertical": safe,
        "gap": reason == "gap",
        "filter_reason": week.filter_reason,
        "entry_open_debit": week.sessions[0].open_debit if week.sessions else None,
    }


def _rate(flags: list[bool]) -> float | None:
    if not flags:
        return None
    return float(sum(1 for flag in flags if flag) / len(flags))


def _bust_flags(weeks: list[Week], delta: float, source: str, rounded: bool) -> tuple[list[bool], list[bool]]:
    puts, calls = [], []
    label = f"{delta:.3g}"
    for week in weeks:
        block = (week.strikes.get(source) or {}).get(label)
        if not block or not week.sessions:
            continue
        close = week.friday_close
        put = block["put_rounded" if rounded else "put"]
        call = block["call_rounded" if rounded else "call"]
        puts.append(close < put)
        calls.append(close > call)
    return puts, calls


def _bust_block(weeks: list[Week], source: str, rounded: bool) -> dict:
    out = {"weeks": 0, "put": {}, "call": {}}
    for delta in DELTAS:
        label = f"{delta:.3g}"
        puts, calls = _bust_flags(weeks, delta, source, rounded)
        out["weeks"] = max(out["weeks"], len(puts))
        out["put"][label] = _rate(puts)
        out["call"][label] = _rate(calls)
    return out


def bust_report(weeks: list[Week]) -> dict:
    sheet = [week for week in weeks if SHEET_START <= week.entry <= SHEET_END and week.strikes]
    full = [week for week in weeks if week.strikes]
    filtered = [week for week in sheet if week.filter_reason]
    clear = [week for week in sheet if not week.filter_reason]
    put_sheet, call_sheet = _bust_flags(sheet, PRIMARY, "prior", False)

    def concentration(flags_weeks: list[Week], side: str) -> dict:
        busted = []
        for week in flags_weeks:
            block = (week.strikes.get("prior") or {}).get("0.03")
            if not block:
                continue
            close = week.friday_close
            hit = close < block["put"] if side == "put" else close > block["call"]
            if hit:
                busted.append(week)
        on_filter = sum(1 for week in busted if week.filter_reason)
        return {
            "busts": len(busted),
            "on_filtered_weeks": on_filter,
            "share_on_filtered_weeks": (on_filter / len(busted)) if busted else None,
        }

    def side_rate(sample: list[Week], side: str) -> float | None:
        puts, calls = _bust_flags(sample, PRIMARY, "prior", False)
        return _rate(puts if side == "put" else calls)

    return {
        "definition": "A bust is the last session's close beyond the short strike. The sheet's counts use the unrounded 0.03 / 0.025 / 0.02 strikes. The traded put is the further of the rounded 0.03-delta strike and the rounded 2.90-sigma strike.",
        "sheet": {
            "start": SHEET_START.date().isoformat(),
            "end": SHEET_END.date().isoformat(),
            "weeks": SHEET_WEEKS,
            "put": SHEET_PUT,
            "call": SHEET_CALL,
            "note": "Counts taken from the tracking sheet: put busts 4.00% / 3.76% / 3.06% and call busts 2.70% / 2.24% / 1.65% at 0.03 / 0.025 / 0.02 delta.",
        },
        "model_full": {
            "start": full[0].entry.date().isoformat() if full else None,
            "end": full[-1].entry.date().isoformat() if full else None,
            "unrounded_prior_vol": _bust_block(full, "prior", False),
            "rounded_prior_vol": _bust_block(full, "prior", True),
        },
        "model_sheet_window": {
            "start": SHEET_START.date().isoformat(),
            "end": SHEET_END.date().isoformat(),
            "unrounded_prior_vol": _bust_block(sheet, "prior", False),
            "rounded_prior_vol": _bust_block(sheet, "prior", True),
            "unrounded_same_day_vol": _bust_block(sheet, "same", False),
        },
        "filtered_weeks": {
            "weeks": len(sheet),
            "filtered": len(filtered),
            "put_rate_filtered": side_rate(filtered, "put"),
            "put_rate_clear": side_rate(clear, "put"),
            "call_rate_filtered": side_rate(filtered, "call"),
            "call_rate_clear": side_rate(clear, "call"),
            "put": concentration(sheet, "put"),
            "call": concentration(sheet, "call"),
        },
        "primary_sheet_window_unrounded": {
            "put": _rate(put_sheet),
            "call": _rate(call_sheet),
            "weeks": len(put_sheet),
        },
        "traded_put": _traded_put_bust(sheet),
    }


def _traded_put_bust(weeks: list[Week]) -> dict:
    flagged = [week for week in weeks if week.put_short is not None and week.sessions]
    puts = [week.friday_close < week.put_short for week in flagged]
    used_sigma = [
        week for week in flagged
        if (week.strikes.get("traded_put") or {}).get("used") == (week.strikes.get("traded_put") or {}).get("sigma_rounded")
        and (week.strikes.get("traded_put") or {}).get("sigma_rounded") != (week.strikes.get("traded_put") or {}).get("delta_rounded")
    ]
    return {
        "weeks": len(flagged),
        "bust_rate": _rate(puts),
        "weeks_on_sigma_floor": len(used_sigma),
        "note": "Friday close through the traded put. That strike is the 2.90-sigma floor whenever it is further out than the 0.03 delta.",
    }


def _trigger_stats(trades: list[dict]) -> dict:
    stops = [trade for trade in trades if trade["reason"] == "stop" and trade.get("tested_vertical") is not None]
    gaps = [trade for trade in trades if trade["reason"] == "gap" and trade.get("tested_vertical") is not None]
    tested = [float(trade["tested_vertical"]) for trade in stops]
    safes = [float(trade["safe_vertical"]) for trade in stops]
    credits = [float(trade["credit"]) for trade in stops]
    med_tested = _median(tested)
    med_credit = _median(credits)
    med_safe = _median(safes)
    implied = None
    if med_credit is not None and med_safe is not None:
        denom = REFERENCE_VERTICAL - med_credit + med_safe
        if denom > 0.05:
            implied = VAR_LOSS * (WIDTH - med_credit) / denom
    ratio = (med_tested / REFERENCE_VERTICAL) if med_tested else None
    modern = [trade for trade in stops if trade["date"] >= "2020-01-01"]
    modern_tested = [float(trade["tested_vertical"]) for trade in modern]
    modern_credit = [float(trade["credit"]) for trade in modern]
    modern_safe = [float(trade["safe_vertical"]) for trade in modern]
    med_modern = _median(modern_tested)
    med_modern_credit = _median(modern_credit)
    med_modern_safe = _median(modern_safe)
    modern_implied = None
    if med_modern_credit is not None and med_modern_safe is not None:
        denom = REFERENCE_VERTICAL - med_modern_credit + med_modern_safe
        if denom > 0.05:
            modern_implied = VAR_LOSS * (WIDTH - med_modern_credit) / denom
    return {
        "reference_vertical": REFERENCE_VERTICAL,
        "stop_fills": len(stops),
        "gap_fills": len(gaps),
        "median_tested_vertical": med_tested,
        "mean_tested_vertical": _mean(tested),
        "p25_tested_vertical": sorted(tested)[len(tested) // 4] if tested else None,
        "p75_tested_vertical": sorted(tested)[(3 * len(tested)) // 4] if tested else None,
        "ratio_to_reference": ratio,
        "difference_vs_reference": (med_tested - REFERENCE_VERTICAL) if med_tested is not None else None,
        "median_credit": med_credit,
        "median_safe_vertical": med_safe,
        "median_structure_debit": _median([float(trade["debit"]) for trade in stops]),
        "implied_wing_fraction_for_065": implied,
        "gap_median_tested_vertical": _median([float(trade["tested_vertical"]) for trade in gaps]),
        "since_2020": {
            "stop_fills": len(modern),
            "median_tested_vertical": med_modern,
            "median_credit": med_modern_credit,
            "median_safe_vertical": med_modern_safe,
            "ratio_to_reference": (med_modern / REFERENCE_VERTICAL) if med_modern else None,
            "difference_vs_reference": (med_modern - REFERENCE_VERTICAL) if med_modern is not None else None,
            "implied_wing_fraction_for_065": modern_implied,
            "median_structure_debit": _median([float(trade["debit"]) for trade in modern]),
        },
        "note": (
            "Stop fills are the weeks closed at a 3% VAR loss during the session. "
            "The tested vertical is the breached side's debit at the spot where the condor loss equals 3% of Monday net worth. "
            "Gap fills are left out of the median. The since-2020 block is the comparison to the $0.65 figure, because that is when a 10-point wing and the credits look like the recent book."
        ),
    }


def _trade_stats(result: dict, capital: float) -> dict:
    trades = result["trades"]
    wins = [trade for trade in trades if trade["pnl_dollars"] > 0]
    losses = [trade for trade in trades if trade["pnl_dollars"] <= 0]
    gaps = [trade for trade in trades if trade["reason"] == "gap"]
    stops = [trade for trade in trades if trade["reason"] == "stop"]
    worst = sorted(trades, key=lambda trade: trade["pnl_pct"] if trade["pnl_pct"] is not None else 0.0)[:8]
    gap_losses = [trade["pnl_pct"] for trade in gaps if trade["pnl_pct"] is not None]
    beyond = [trade for trade in trades if trade.get("premium_loss_pct") is not None and trade["premium_loss_pct"] > VAR_LOSS + 1e-4]
    same_day = [trade for trade in trades if trade["date"] == trade["exit"] and trade["reason"] in ("stop", "gap")]
    multiples = [
        trade["entry_open_debit"] / trade["credit"]
        for trade in trades
        if trade.get("entry_open_debit") and trade.get("credit")
    ]
    equity = result["equity"]
    metrics = compute_metrics(equity) if len(equity) > 1 else None
    return {
        "capital": capital,
        "n": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": (len(wins) / len(trades)) if trades else None,
        "avg_win_pct": _mean([trade["pnl_pct"] for trade in wins]),
        "avg_loss_pct": _mean([trade["pnl_pct"] for trade in losses]),
        "expectancy_pct": _mean([trade["pnl_pct"] for trade in trades]),
        "avg_win_dollars": _mean([trade["pnl_dollars"] for trade in wins]),
        "avg_loss_dollars": _mean([trade["pnl_dollars"] for trade in losses]),
        "expectancy_dollars": _mean([trade["pnl_dollars"] for trade in trades]),
        "stops": len(stops),
        "gaps": len(gaps),
        "same_day_exits": len(same_day),
        "median_entry_open_over_credit": _median(multiples),
        "expiries": sum(1 for trade in trades if trade["reason"] == "expiry"),
        "put_only": sum(1 for trade in trades if trade["structure"] == "put spread"),
        "skipped": result["skipped"],
        "ruined": bool(result["ruined"]),
        "mark_floored": bool(result["mark_floored"]),
        "worst": [
            {
                "date": trade["date"],
                "exit": trade["exit"],
                "structure": trade["structure"],
                "reason": trade["reason"],
                "pnl_pct": trade["pnl_pct"],
                "pnl_dollars": trade["pnl_dollars"],
                "premium_loss_pct": trade["premium_loss_pct"],
                "gap": trade["gap"],
                "tested_vertical": trade["tested_vertical"],
            }
            for trade in worst
        ],
        "gap_stats": {
            "gap_throughs": len(gaps),
            "stop_fills": len(stops),
            "share_of_exits": (len(gaps) / len(trades)) if trades else None,
            "median_gap_loss_pct": _median([value for value in gap_losses if value is not None]),
            "worst_gap_loss_pct": min(gap_losses) if gap_losses else None,
            "premium_losses_beyond_3pct": len(beyond),
        },
        "trigger": _trigger_stats(trades) if any(trade["reason"] == "stop" for trade in trades) else None,
        "metrics": metrics,
    }


def _fmt_pct(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.{digits}f}%"


def _fmt_px(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"${value:.2f}"


def build_verdict(books: dict[str, dict[str, dict]]) -> dict:
    hold = books["qqq-condor-hold"]["10000"]
    two = books["qqq-condor-stop-2x"]["10000"]
    var = books["qqq-condor-stop-3pct"]["10000"]
    var_big = books["qqq-condor-stop-3pct"]["200000"]
    filt = books["qqq-condor-filtered"]["10000"]
    trigger = (var_big.get("trigger") or {})
    modern = trigger.get("since_2020") or {}
    med = modern.get("median_tested_vertical")
    if med is None:
        med = trigger.get("median_tested_vertical")
        ratio = trigger.get("ratio_to_reference")
        implied = trigger.get("implied_wing_fraction_for_065")
        window = "the full sample"
    else:
        ratio = modern.get("ratio_to_reference")
        implied = modern.get("implied_wing_fraction_for_065")
        window = "weeks since 2020"
    ruined = bool(var.get("ruined") or var_big.get("ruined"))
    hold_edge = hold.get("expectancy_pct") or 0.0
    fragile = ruined or (var.get("metrics") or {}).get("max_drawdown", 0) <= -0.5
    tone = "bad" if fragile or hold_edge <= 0 else "ink"
    if ruined:
        title = "The 3% VAR size is wiped out by gaps."
    elif hold_edge > 0 and not fragile:
        title = "Desk size has a modelled edge. The full-account wing does not forgive a gap."
    else:
        title = "The modelled condor does not clear its own costs."
    two_same = two.get("same_day_exits") or 0
    two_n = two.get("n") or 0
    two_ratio = two.get("median_entry_open_over_credit") or 0
    if two_n and two_same >= two_n * 0.9 and two_ratio >= 1.5:
        two_clause = (
            f"{two_same} of {two_n} $10,000 trades buy back the same day, "
            f"because the cost to close at the entry open is already about {two_ratio:.1f}× the credit just received."
        )
    else:
        two_clause = (
            f"It runs to Friday on {two.get('expiries') or 0} of {two_n} $10,000 trades "
            f"and stops on {two.get('stops') or 0}. "
            f"The entry-open close cost is {two_ratio:.2f}× the credit."
        )
    edge = (
        f"Held to Friday at desk size, the average week is {_fmt_pct(hold.get('expectancy_pct'), 2)} of the account "
        f"({_fmt_pct(hold.get('win_rate'), 1)} of weeks finish green, average win {_fmt_pct(hold.get('avg_win_pct'), 2)}, "
        f"average loss {_fmt_pct(hold.get('avg_loss_pct'), 2)}). "
        f"The 2× credit stop averages {_fmt_pct(two.get('expectancy_pct'), 2)} a week. "
        + two_clause
    )
    var_dd = (var_big.get("metrics") or {}).get("max_drawdown")
    gap_n = (var_big.get("gap_stats") or {}).get("gap_throughs") or 0
    if gap_n == 1:
        gap_clause = "One open on the $200,000 book was already through the stop and filled at the open."
    elif gap_n == 0:
        gap_clause = "No open on the $200,000 book was already through the stop."
    else:
        gap_clause = (
            f"{gap_n} opens on the $200,000 book were already through the stop and filled at the open."
        )
    kills = (
        f"The eject is 3% of Monday net worth, on the desk's contract budget (18.75% of that net worth). "
        f"A stop that fills at the level loses 3% net of the credit. "
        f"A full wing is larger than that, so a gap can go through it. "
        f"{gap_clause} "
        f"The worst of those is {_fmt_pct((var_big.get('gap_stats') or {}).get('worst_gap_loss_pct'), 1)} of the account. "
        f"The book’s maximum drawdown is {_fmt_pct(var_dd)}."
    )
    if med is None:
        close = "The 3% book did not record an intraday stop fill, so there is no modelled vertical to set next to $0.65."
    else:
        close = (
            f"On {window}, the modelled price to close the breached vertical at an intraday 3% stop has median {_fmt_px(med)} "
            f"against the $0.65 figure. That is {ratio:.2f}× the figure, {_fmt_px(abs(med - REFERENCE_VERTICAL))} "
            f"{'under' if med < REFERENCE_VERTICAL else 'over'} it. "
            f"The full sample median is {_fmt_px(trigger.get('median_tested_vertical'))}."
        )
        if implied is not None:
            close += (
                f" Matching $0.65 with this credit and this residual value on the safe side would mean a wing of about "
                f"{_fmt_pct(implied, 0)} of the account. This book risks 18.75% of Monday net worth."
            )
    sizing = (
        f"Desk size risks 18.75% of Monday net worth (the 12.5% base VAR times the 1.5 edge cap) and cannot lose more than about one wing in a week. "
        f"Filtering FOMC, CPI, short weeks, and 2% gaps puts the $10,000 3% book at "
        f"{_fmt_pct((filt.get('metrics') or {}).get('cagr'))} CAGR "
        f"and {_fmt_pct((filt.get('metrics') or {}).get('max_drawdown'))} max drawdown, "
        f"next to {_fmt_pct((var.get('metrics') or {}).get('cagr'))} and "
        f"{_fmt_pct((var.get('metrics') or {}).get('max_drawdown'))} unfiltered. "
        + close
    )
    return {"tone": tone, "title": title, "edge": edge, "kills": kills, "sizing": sizing}


def assumptions() -> list[str]:
    return [
        "VAR is the account's net worth at the Monday entry. The 3% loss is 3% of that number. This is the sizing plan's definition, and it is an assumption to confirm.",
        "The loss is net of the premium collected that week: cost to close the whole condor, minus the total credit received. The sign is that way around. A mark below the credit is a profit and does not stop. Commissions sit on top of the 3% test.",
        "The entry credit already includes the half-spread. The close mark starts at that credit and then follows the change in the model debit. The spread is not charged a second time as an opening loss.",
        "The tracking notes put that same 3% loss at roughly a 0.65 price to close the breached vertical. The model does not aim at 0.65. The calibration block reports the debit it produces.",
        "The call is the 0.03 delta. The put is the further of that 0.03-delta strike and the 2.90-sigma floor. Sigma distance is prior VXN times 1.03, times the square root of calendar days over 365, the same formula as the calculator. 0.025 and 0.02 stay reconciliation columns. Delta is Black–Scholes with r = 4%, q = 0, and the prior session's VXN.",
        "The short strike is rounded to the nearest dollar. The long strike is 10 points further out. The sheet has no long strike. Ten points is the calculator's width. On a $40 QQQ that wing is very wide and the long is nearly worthless.",
        "A put whose model bid is inside the half-spread is kept on for no credit. The week is skipped only when both sides have no credit. A call credit at or below zero drops the call.",
        "The half-spread charged on every leg is the larger of "
        f"${HALF_FLOOR:.3f} and 10% of the mid. The short is sold at the bid and bought back at the ask. "
        "Settlement at expiry is intrinsic, with no commission. A stop pays commission on the way out. "
        "The floor was set so the modelled 0.03-delta credits land near the recorded credits.",
        "Commission is $0.65 per contract per leg. Cash earns nothing. Early assignment is ignored.",
        "Strike skew versus VXN is flat. A positive put skew was checked against the recorded credits and made the model too rich. Stop marks still add vol when spot has moved since the prior close: 1.5 vol points per 1% drop (cap 25) and 0.4 per 1% rise (cap 10). That intraday vol is not observed.",
        "Close marks use that session's VXN. Entry and stop marks use the prior session's VXN plus the spot-vol add-on. Before VXN exists, vol is VIX times the median VXN/VIX ratio.",
        "The entry open is not treated as a gap through the stop. From the next session on, an open already through the stop fills at the open, capped at the wing. An intraday breach fills at the stop. The adverse mark is the worse of the low and the high. Both extremes are not added together.",
        "Friday intraday marks keep a quarter-day of residual time. The close, if the stop has not fired, is the intrinsic value.",
        "Every book, including the 3% eject, sizes contracts from the desk budget: 18.75% of Monday net worth. The eject is still 3% of the whole net worth, so the per-share stop is wider than 3% of one wing. One contract is the minimum. A book that cannot buy one contract skips the week.",
        "The filtered book skips a week with fewer than five sessions, a Monday (or first-session) gap of 2% or more in either direction, a week that contains an FOMC announcement (or the Sunday before a Monday entry), or a CPI release. The first matching reason is the one counted.",
        "Earnings windows and the calculator's call-block on a down Monday are notes, not filters, except for the explicit 2% gap on the filtered book.",
        "CPI dates other than 2024 were compiled. A one-day miss usually stays inside the same week. Gate 1 (the live IV versus VXN check) cannot be rebuilt from daily bars; recorded fill IV sits close to VXN.",
    ]


def lessons() -> list[str]:
    return [
        "The bust notes keep coming back to the open: large gaps in either direction, not a slow grind through the short strike.",
        "FOMC weeks and CPI mornings cluster on both the put side and the call side. A CPI print on the Friday before an FOMC week is named more than once.",
        "Holiday-short weeks are described as fussy on both sides. The filtered book drops any week with fewer than five sessions.",
        "A second week of a sharp bounce, debt-ceiling noise, and single-name earnings show up in the notes. Several notes say the headline was not really the cause.",
        "One call-side note in August 2026 records a stop-out on a gap two days into the week. That is the overnight case the 3% book fills at the open.",
        "The sheet's bust columns are the 0.03 / 0.025 / 0.02 delta strikes. The traded put in this backtest is the calculator's 2.90-sigma floor when that strike is further out, which it is on a four-day week.",
    ]


def _load_derived() -> dict | None:
    try:
        from backtest_lab.strategies import condor_derived as derived
    except ImportError:
        return None
    recorded = getattr(derived, "RECORDED", None)
    live = getattr(derived, "LIVE", None)
    if not recorded and not live:
        return None
    return {"recorded": recorded, "live": live}


def _account_payload(stats: dict, include_equity: bool, equity: pd.Series | None) -> dict:
    payload = {key: value for key, value in stats.items() if key != "metrics"}
    payload["metrics"] = stats.get("metrics")
    if include_equity and equity is not None and len(equity):
        payload["equity"] = _equity_rows(equity, drawdown_series(equity))
    return payload


def _banner() -> str:
    return (
        "Modelled option prices, not a tape of fills. QQQ open, high, low, and close are Yahoo's split-adjusted bars. "
        "Implied vol is ^VXN, or ^VIX scaled by the median VXN/VIX ratio when ^VXN is missing. "
        "Strikes, credits, and stops are Black–Scholes with a flat strike skew, a spot-vol add-on on stop marks, and a bid/ask haircut. "
        "This book does not select a stock universe. SPY is a reference line only."
    )


def _caveats(spec: dict, scale_note: str) -> list[str]:
    lines = [
        _banner(),
        spec["blurb"],
        "VAR for the 3% eject is Monday net worth. The loss is cost to close minus the total credit collected that week. Confirm that definition before treating the path as the live rule.",
        "No historical option chain is used. Debits and credits are Black–Scholes mids with a half-spread of "
        f"max(${HALF_FLOOR:.3f}, 10% of mid) per leg. That floor is the one that lines the 0.03-delta credit up with the recorded credits.",
        "Intraday implied vol is not observed. Stop marks take the prior VXN and add 1.5 vol points per 1% spot drop, capped at 25 points.",
        scale_note,
        "A 10-point wing on early QQQ prices is a large fraction of spot, so the long wing is almost free and the position is close to a naked short with a far stop.",
        "The entry is the week's first session, usually Monday, at the open. The same day's high and low can stop the new position. That open is not a gap fill.",
        "From the next morning on, a stop that the open has already crossed fills at the open debit, capped at the wing. The loss can be much larger than 3% of VAR.",
        "Equity is floored at one cent if a liquidation would have wiped the account, so the CAGR can still be computed. The trade row keeps the uncapped loss. Ruin is flagged.",
        "Hypothetical dollars in the trade table are the model's P&L on a $10,000 or $200,000 account. Recorded fills are stored only as a percent of spot.",
        "Commission is $0.65 per contract per leg on the entry and on a stop. Expiry pays no commission. Cash earns zero.",
    ]
    return lines


def build_reports(frame: pd.DataFrame, capital: float, end: date, start_year: int, source) -> list[dict]:
    weeks, sessions, scale_note = prepare(frame, start_year, end)
    for week in weeks:
        price_week(week)
    print(f"  condor weeks {len(weeks)}  tradable {sum(1 for week in weeks if week.tradable)}  sessions {len(sessions)}")
    tradable = sum(1 for week in weeks if week.tradable)
    scale_note += (
        f" {tradable} of {len(weeks)} weeks have a credit after the haircut. "
        "The 2.90-sigma put is often inside that haircut and is then carried for no credit."
    )
    books: dict[str, dict[str, dict]] = {}
    results: dict[str, dict[float, dict]] = {}
    for spec in VARIANTS:
        results[spec["id"]] = {}
        books[spec["id"]] = {}
        for account in ACCOUNTS:
            result = run_book(weeks, sessions, account, spec["variant"], spec["filters"], spec["sizing"])
            stats = _trade_stats(result, account)
            results[spec["id"]][account] = result
            books[spec["id"]][f"{int(account)}"] = stats
            print(
                f"  {spec['id']} ${int(account):,}  trades {stats['n']}  "
                f"win {_fmt_pct(stats['win_rate'])}  exp {_fmt_pct(stats['expectancy_pct'], 2)}  "
                f"CAGR {_fmt_pct((stats.get('metrics') or {}).get('cagr'))}  "
                f"maxDD {_fmt_pct((stats.get('metrics') or {}).get('max_drawdown'))}  "
                f"gaps {stats['gaps']}  ruined {stats['ruined']}"
            )
    shared_bust = bust_report(weeks)
    shared_verdict = build_verdict(books)
    derived = _load_derived()
    reports = []
    for spec in VARIANTS:
        stats_10 = books[spec["id"]]["10000"]
        equity = results[spec["id"]][10_000.0]["equity"]
        big = books[spec["id"]]["200000"]
        trigger = big.get("trigger") if spec["variant"] == "stop-3pct" else None
        report = assemble_report(
            strategy_id=spec["id"],
            name=spec["name"],
            description=spec["blurb"],
            kind="qqq-condor",
            group="QQQ Condor",
            signal="2.90-sigma put, 0.03-delta call, 10-point wings",
            rebalance="weekly, Monday open to Friday close",
            equity=equity,
            cohorts=[],
            events=[],
            caveats=_caveats(spec, scale_note),
            warnings=_warnings(stats_10, big),
            source=source,
            universe={
                "id": "none",
                "survivorship_bias_free": True,
                "note": "Single ETF. Option prices are modelled from QQQ and VXN.",
            },
            capital=capital,
            cost_bps=0.0,
            start_year=start_year,
            survivorship_bias_free=True,
            banner=_banner(),
        )
        report["condor"] = {
            "variant": spec["variant"],
            "sizing": spec["sizing"],
            "filters": spec["filters"],
            "verdict": shared_verdict,
            "assumptions": assumptions(),
            "rules": [spec["blurb"] for spec in VARIANTS],
            "lessons": lessons(),
            "bust": shared_bust,
            "calibration": {
                "recorded": None if not derived else derived.get("recorded"),
                "trigger": trigger,
                "trigger_10k": stats_10.get("trigger") if spec["variant"] == "stop-3pct" else None,
                "reference_vertical": REFERENCE_VERTICAL,
            },
            "live": None if not derived else derived.get("live"),
            "accounts": {
                "10000": _account_payload(stats_10, False, None),
                "200000": _account_payload(big, True, results[spec["id"]][200_000.0]["equity"]),
            },
        }
        reports.append(report)
    return reports


def _warnings(small: dict, big: dict) -> list[str]:
    notes = []
    if small.get("ruined") or big.get("ruined"):
        notes.append("A liquidation lost the whole account. Later weeks are not traded. Equity is floored at one cent from that day.")
    if small.get("mark_floored") or big.get("mark_floored"):
        notes.append("An intraday mark was floored at one cent. The position was still open; the floor is only the published curve.")
    gaps = (big.get("gap_stats") or {}).get("gap_throughs") or 0
    if gaps:
        notes.append(f"The $200k book filled {gaps} stops at the open because the open was already through the stop.")
    return notes


def prepare(frame: pd.DataFrame, start_year: int, end: date) -> tuple[list[Week], list[pd.Timestamp], str]:
    df = frame.copy()
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df = df.sort_index()
    df = df[~df.index.duplicated(keep="last")]
    needed = ["open", "high", "low", "close"]
    missing = [column for column in needed if column not in df.columns]
    if missing:
        raise ValueError(f"condor frame is missing {missing}")
    if "vxn" not in df.columns:
        df["vxn"] = float("nan")
    if "vix" not in df.columns:
        df["vix"] = float("nan")
    ratio = (df["vxn"] / df["vix"]).replace([math.inf, -math.inf], pd.NA).dropna()
    scale = float(ratio.median()) if len(ratio) else VIX_SCALE_DEFAULT
    same = df["vxn"].where(df["vxn"] > 0)
    fallback = df["vix"].where(df["vix"] > 0) * scale
    df["vol"] = (same.fillna(fallback) / 100.0).ffill().astype(float)
    df["prior_vol"] = df["vol"].shift(1)
    df = df.dropna(subset=["open", "high", "low", "close"])
    if "prev_close" not in df.columns:
        df["prev_close"] = df["close"].shift(1)
    start = pd.Timestamp(date(start_year, 1, 1))
    stop = pd.Timestamp(end)
    window = df[(df.index >= start) & (df.index <= stop)]
    groups: dict[tuple[int, int], list[pd.Timestamp]] = {}
    for stamp in df.index:
        iso = stamp.isocalendar()
        groups.setdefault((int(iso.year), int(iso.week)), []).append(stamp)
    weeks: list[Week] = []
    for key in sorted(groups):
        days = groups[key]
        entry = days[0]
        if entry < start or entry > stop:
            continue
        expiry = days[-1]
        if expiry > stop:
            days = [day for day in days if day <= stop]
            if not days:
                continue
            expiry = days[-1]
        row0 = df.loc[entry]
        prior = row0["prior_vol"]
        spot = float(row0["open"])
        year_frac = max((expiry - entry).days, 0) / 365.0
        sessions = [_snap(df, day, entry, expiry, year_frac) for day in days]
        week = Week(
            entry=entry,
            expiry=expiry,
            sessions=sessions,
            spot=spot,
            prior_sig=float(prior) if pd.notna(prior) else float("nan"),
            friday_close=float(df.loc[expiry, "close"]),
        )
        week.filter_reason = classify_week(
            [stamp.date() for stamp in days],
            spot,
            float(df.loc[entry, "prev_close"]) if pd.notna(df.loc[entry, "prev_close"]) else None,
        )
        if not math.isfinite(week.prior_sig) or year_frac <= 0 or spot <= 0:
            week.skip = "no-vol"
        else:
            same_sig = float(row0["vol"]) if pd.notna(row0["vol"]) else week.prior_sig
            week.strikes = {
                "prior": _strike_map(spot, year_frac, week.prior_sig),
                "same": _strike_map(spot, year_frac, same_sig),
            }
            primary = week.strikes["prior"]["0.03"]
            sigma_put = round_strike(sigma_put_strike(spot, year_frac, week.prior_sig))
            delta_put = primary["put_rounded"]
            # Further out of the money is the lower put strike. On a normal week
            # 0.03 delta is about 1.9 sigma, so the 2.90 floor is the traded put.
            week.put_short = min(delta_put, sigma_put)
            week.put_long = week.put_short - WIDTH
            week.call_short = primary["call_rounded"]
            week.call_long = week.call_short + WIDTH
            week.put_short_raw = primary["put"]
            week.call_short_raw = primary["call"]
            week.strikes["traded_put"] = {
                "delta_rounded": delta_put,
                "sigma_rounded": sigma_put,
                "used": week.put_short,
            }
            if week.put_long <= 0 or week.call_short <= week.put_short:
                week.skip = "no-vol"
                week.put_short = None
                week.call_short = None
        weeks.append(week)
    sessions = [stamp for stamp in window.index if start <= stamp <= stop]
    n_fallback = int(same.reindex(window.index).isna().sum()) if len(window) else 0
    note = (
        f"VXN/VIX median scale is {scale:.3f}. "
        f"{n_fallback} sessions in the window have no VXN print and use scaled VIX."
    )
    return weeks, sessions, note


def _strike_map(spot: float, year_frac: float, sigma: float) -> dict:
    out = {}
    for delta in DELTAS:
        put = strike_for_delta(spot, year_frac, RATE, sigma, delta, "put")
        call = strike_for_delta(spot, year_frac, RATE, sigma, delta, "call")
        out[f"{delta:.3g}"] = {
            "put": put,
            "call": call,
            "put_rounded": round_strike(put),
            "call_rounded": round_strike(call),
        }
    return out


def _snap(df: pd.DataFrame, day: pd.Timestamp, entry: pd.Timestamp, expiry: pd.Timestamp, entry_frac: float) -> Snap:
    row = df.loc[day]
    remaining = max((expiry - day).days, 0) / 365.0
    settle = day == expiry
    t_trade = FRIDAY_RESIDUAL if settle else remaining
    if day == entry and not settle:
        t_trade = entry_frac
    prior = float(row["prior_vol"]) if pd.notna(row["prior_vol"]) else float("nan")
    day_sig = float(row["vol"]) if pd.notna(row["vol"]) else prior
    prev_close = float(row["prev_close"]) if pd.notna(row["prev_close"]) else float(row["open"])
    return Snap(
        day=day,
        t_trade=t_trade,
        settle=settle,
        prior_sig=prior,
        day_sig=day_sig,
        prev_close=prev_close,
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
    )


def load_bars(refresh: bool = False) -> pd.DataFrame:
    cache = _cache_path()
    if cache.exists() and not refresh:
        frame = pd.read_csv(cache, parse_dates=["date"])
        frame = frame.set_index("date").sort_index()
        return _with_prev_close(frame)
    frame = _download_bars()
    cache.parent.mkdir(parents=True, exist_ok=True)
    out = frame.copy()
    out.index.name = "date"
    out.to_csv(cache)
    return _with_prev_close(frame)


def _with_prev_close(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.sort_index()
    frame["prev_close"] = frame["close"].shift(1)
    return frame


def _cache_path() -> Path:
    return Path(__file__).resolve().parents[2] / ".cache" / "condor" / "bars.csv"


def _download_bars() -> pd.DataFrame:
    import yfinance as yf

    raw = yf.download(
        ["QQQ", "^VXN", "^VIX"],
        start="1999-01-01",
        auto_adjust=False,
        progress=False,
        threads=False,
        group_by="ticker",
    )
    if raw is None or len(raw) == 0:
        raise RuntimeError("Yahoo returned no QQQ / VXN / VIX bars")
    frame = pd.DataFrame(
        {
            "open": _field(raw, "QQQ", "Open"),
            "high": _field(raw, "QQQ", "High"),
            "low": _field(raw, "QQQ", "Low"),
            "close": _field(raw, "QQQ", "Close"),
            "vxn": _field(raw, "^VXN", "Close"),
            "vix": _field(raw, "^VIX", "Close"),
        }
    )
    frame.index = pd.to_datetime(frame.index).tz_localize(None).normalize()
    frame = frame.sort_index()
    frame = frame.dropna(subset=["open", "high", "low", "close"])
    if frame.empty:
        raise RuntimeError("QQQ OHLC was empty after the Yahoo download")
    return frame


def _field(raw: pd.DataFrame, ticker: str, name: str) -> pd.Series:
    if isinstance(raw.columns, pd.MultiIndex):
        level0 = set(raw.columns.get_level_values(0))
        if ticker in level0:
            series = raw[(ticker, name)]
        elif name in level0:
            series = raw[(name, ticker)]
        else:
            raise RuntimeError(f"Yahoo columns have no {ticker} {name}")
    else:
        series = raw[name]
    series = series.copy()
    series.index = pd.to_datetime(series.index).tz_localize(None).normalize()
    return pd.to_numeric(series, errors="coerce")


class CondorStrategy:
    id = "qqq-condor"
    name = "QQQ Condor"
    description = (
        "Weekly QQQ iron condor. Put at or beyond 2.90 sigma, call at the 0.03 delta, 10-point wings, Monday open to Friday close. "
        "Books: hold, stop at 2× credit, stop at a 3% VAR loss, and that stop with FOMC/CPI/gap/short-week filters."
    )

    def run(self, ctx: RunContext, only: str | None = None) -> list[dict]:
        refresh = bool(getattr(ctx.source, "refresh", False))
        frame = load_bars(refresh=refresh)
        reports = build_reports(frame, ctx.capital, ctx.end, ctx.start_year, ctx.source)
        if only is None:
            return reports
        chosen = [report for report in reports if report["id"] == only]
        if not chosen:
            raise RuntimeError(f"unknown condor book {only}")
        return chosen
