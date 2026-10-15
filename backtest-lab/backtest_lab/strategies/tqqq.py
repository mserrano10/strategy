"""TQQQ buy-and-hold, and the two-bullet rule from tqqq/index.html.

The memorandum (strategy.atlaswealth.cc/tqqq/) specifies a 90/10 hybrid:

- 90% SPY, bought once and never rebalanced against the satellite.
- 10% satellite, split into two equal bullets of dry powder.
- Dry powder stays in Treasury bills. v0 uses BIL's adjusted close.
- Track TQQQ's high-water mark on the split-adjusted close.
- Bullet 1 starts buying when TQQQ is down at least 50% from that mark.
- Bullet 2 starts buying when TQQQ is down at least 80% from the *same locked* mark.
- Each bullet deploys 50%, then 25%, then 25% of the cash it held at the trigger,
  at that day's close, on the trigger date and on the first session on or after
  30 and 60 calendar days later. The memo calls this the volume-weighted tranche.
- The cycle floor is the lowest TQQQ close since the first trigger. It is updated
  at the close, after that day's limit check, so the check does not use a low
  that was not known at the open.
- A resting limit sells every TQQQ share at 10× the floor when the daily high
  reaches it. A gap through the limit fills at the open.
- While a cycle is open the high-water mark stays locked, and a new cycle cannot
  start on the exit day.
- The high-water mark is seeded with the max close before the backtest start,
  so a drawdown already in progress on day one is visible.

TQQQ marks inside the rule use the split-adjusted close, because the memo's
triggers are price limits ($44.05, $82.90). The small TQQQ distribution is
omitted. SPY and BIL use adjusted closes, so their dividends are included.
Buy-and-hold is a separate book and *does* use TQQQ's adjusted close.

The memo's pre-2010 table uses a simulated 3× Nasdaq-100. v0 does not. It
trades the listed TQQQ fund only.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from backtest_lab.report import assemble_report
from backtest_lab.strategies.base import RunContext

BULLET1_DRAWDOWN = 0.50
BULLET2_DRAWDOWN = 0.80
EXIT_MULTIPLE = 10.0
TRANCHE_OFFSETS = (0, 30, 60)
TRANCHE_FRACTIONS = (0.50, 0.25, 0.25)
CORE_WEIGHT = 0.90
BULLET_WEIGHT = 0.05

BUY_HOLD_CAVEATS = [
    "Buy-and-hold is 100% TQQQ. Total return uses Yahoo's adjusted close, so the small TQQQ dividend is included.",
    "The position is entered at the first mark's close. There is no high-water-mark rule in this book.",
    "This is not the memorandum's two-bullet rule. That book is reported separately.",
]

RULE_CAVEATS = [
    "90% SPY buy-and-hold and 10% satellite. The sleeves are never rebalanced back to 90/10.",
    "The satellite is two bullets of 5% each, parked in BIL (a Treasury-bill ETF) as the memo's Treasury MMF proxy.",
    "Bullet 1 deploys at a 50% drawdown from the high-water mark. Bullet 2 deploys at 80% from that same locked mark.",
    "Each bullet buys 50% / 25% / 25% of its cash at the trigger close, then on the first session on or after 30 and 60 calendar days.",
    "The cycle floor is the minimum TQQQ close since the trigger. The limit is checked against the prior floor, then the floor is updated. That avoids using today's low to set a limit today's high might have hit.",
    "The limit sells all TQQQ shares at 10× the floor when the daily high reaches it. If the open is already through the limit, the fill is the open.",
    "No new cycle starts on an exit day. Undeployed bullet cash stays in BIL.",
    "The high-water mark is the max split-adjusted close before the start date, then the running max while the book is flat.",
    "TQQQ inside this rule is marked on the split-adjusted close, not the dividend-adjusted close, because the memo's limits are traded prices. TQQQ distributions are omitted. SPY and BIL include dividends.",
    "Listed TQQQ only, from its 2010 inception. The memo's pre-2010 figures assume a simulated 3× Nasdaq-100 and are not reproduced.",
    "Entries fill at the signal close. That is a small optimistic bias versus the next open.",
]


@dataclass
class _Bullet:
    name: str
    cash: float
    shares: float = 0.0
    active: bool = False
    trigger: pd.Timestamp | None = None
    budget: float = 0.0
    tranches: int = 0

    def retire(self) -> None:
        self.shares = 0.0
        self.active = False
        self.trigger = None
        self.budget = 0.0
        self.tranches = 0


class TqqqStrategy:
    id = "tqqq"
    name = "TQQQ"
    description = (
        "Two books: TQQQ buy-and-hold, and the memorandum's 90/10 two-bullet rule "
        "(50% and 80% drawdown triggers, 50/25/25 tranches, exit at 10× the cycle floor)."
    )

    def run(self, ctx: RunContext, only: str | None = None) -> list[dict]:
        start = pd.Timestamp(date_from_year(ctx.start_year))
        # ATH seed needs the whole listed life, which begins in February 2010.
        history_start = min(start.date(), date_from_year(2010))
        symbols = ["TQQQ", "SPY", "BIL"]
        history = ctx.source.history(symbols, history_start, ctx.end)
        missing = [symbol for symbol in symbols if symbol in history.missing or symbol not in history.close.columns]
        if missing:
            raise RuntimeError(f"TQQQ book is missing Yahoo prices for {missing}")
        start_dt = _start_session(history.close.index, ctx.start_year)
        end_dt = _session_on_or_before(history.close.index, pd.Timestamp(ctx.end))
        if end_dt < start_dt:
            raise RuntimeError("TQQQ end date is before the start session")

        reports = []
        if only in (None, "tqqq-buy-hold"):
            reports.append(self._buy_hold(history, start_dt, end_dt, ctx))
        if only in (None, "tqqq-two-bullet"):
            reports.append(self._two_bullet(history, start_dt, end_dt, ctx))
        return reports

    def _buy_hold(self, history, start_dt, end_dt, ctx: RunContext) -> dict:
        adj = _slice_symbol(history.adjusted_close, "TQQQ", start_dt, end_dt)
        equity = simulate_buy_hold(adj, ctx.capital, ctx.cost_bps)
        caveats = _source_caveats(ctx) + BUY_HOLD_CAVEATS
        caveats.append(f"Transaction cost is {ctx.cost_bps:g} bps on the entry notional.")
        return assemble_report(
            strategy_id="tqqq-buy-hold",
            name="TQQQ Buy and Hold",
            description="Buy TQQQ at the start close and hold it. Total return, adjusted closes.",
            kind="tqqq-buy-hold",
            group="TQQQ",
            signal="none — buy and hold",
            rebalance="none",
            equity=equity,
            cohorts=[],
            events=[
                {
                    "date": pd.Timestamp(equity.index[0]).date().isoformat(),
                    "type": "buy",
                    "detail": "100% TQQQ at the adjusted close",
                }
            ],
            caveats=caveats,
            warnings=[],
            source=ctx.source,
            universe={"id": "none", "survivorship_bias_free": True, "note": "Single fund. No stock universe."},
            capital=ctx.capital,
            cost_bps=ctx.cost_bps,
            start_year=ctx.start_year,
            survivorship_bias_free=True,
        )

    def _two_bullet(self, history, start_dt, end_dt, ctx: RunContext) -> dict:
        close = _slice_symbol(history.close, "TQQQ", history.close.index.min(), end_dt)
        high = _slice_symbol(history.high, "TQQQ", history.high.index.min(), end_dt)
        opened = _slice_symbol(history.open, "TQQQ", history.open.index.min(), end_dt)
        spy = _slice_symbol(history.adjusted_close, "SPY", start_dt, end_dt)
        bil = _slice_symbol(history.adjusted_close, "BIL", start_dt, end_dt)
        seed = close[close.index < start_dt]
        seed_ath = float(seed.max()) if len(seed) else None
        result = simulate_two_bullet(
            spy_adj=spy,
            bil_adj=bil,
            tqqq_close=close,
            tqqq_high=high,
            tqqq_open=opened,
            start=start_dt,
            capital=ctx.capital,
            cost_bps=ctx.cost_bps,
            seed_ath=seed_ath,
        )
        caveats = _source_caveats(ctx) + RULE_CAVEATS
        caveats.append(f"Transaction cost is {ctx.cost_bps:g} bps on each buy and on the exit sell.")
        return assemble_report(
            strategy_id="tqqq-two-bullet",
            name="TQQQ Two-Bullet 90/10",
            description=self.description,
            kind="tqqq-two-bullet",
            group="TQQQ",
            signal="TQQQ drawdown from the locked high-water mark",
            rebalance="rules-based; the 90/10 sleeves are not rebalanced",
            equity=result["equity"],
            cohorts=result["cohorts"],
            events=result["events"],
            caveats=caveats,
            warnings=result["warnings"],
            source=ctx.source,
            universe={"id": "none", "survivorship_bias_free": True, "note": "SPY, TQQQ, and BIL only. No stock universe."},
            capital=ctx.capital,
            cost_bps=ctx.cost_bps,
            start_year=ctx.start_year,
            survivorship_bias_free=True,
        )


def simulate_buy_hold(adjusted: pd.Series, capital: float, cost_bps: float) -> pd.Series:
    px0 = float(adjusted.iloc[0])
    if not px0 > 0:
        raise ValueError("TQQQ start price is missing")
    invested = capital / (1.0 + cost_bps / 10_000.0)
    shares = invested / px0
    equity = shares * adjusted.astype(float)
    equity.name = "equity"
    return equity


def simulate_two_bullet(
    spy_adj: pd.Series,
    bil_adj: pd.Series,
    tqqq_close: pd.Series,
    tqqq_high: pd.Series,
    tqqq_open: pd.Series,
    start: pd.Timestamp,
    capital: float,
    cost_bps: float,
    seed_ath: float | None = None,
) -> dict:
    start = pd.Timestamp(start).tz_localize(None).normalize()
    dates = tqqq_close.index[tqqq_close.index >= start]
    if len(dates) < 2:
        raise ValueError("not enough TQQQ sessions")
    close = tqqq_close.reindex(dates).astype(float)
    high = tqqq_high.reindex(dates).astype(float).fillna(close)
    opened = tqqq_open.reindex(dates).astype(float).fillna(close)
    spy = spy_adj.reindex(dates).ffill().astype(float)
    bil = bil_adj.reindex(dates).ffill().astype(float)
    if spy.isna().any() or bil.isna().any() or close.isna().any():
        raise ValueError("SPY, BIL, or TQQQ has a gap that cannot be filled at the start")
    cost_rate = cost_bps / 10_000.0
    spy_shares = (capital * CORE_WEIGHT) / float(spy.iloc[0])
    bullet1 = _Bullet("bullet-1", capital * BULLET_WEIGHT)
    bullet2 = _Bullet("bullet-2", capital * BULLET_WEIGHT)
    ath = None if seed_ath is None else float(seed_ath)
    locked_ath = None
    floor = None
    cycle = False
    events = [
        {
            "date": pd.Timestamp(dates[0]).date().isoformat(),
            "type": "seed",
            "detail": (
                f"90% SPY, 5% bullet 1 in BIL, 5% bullet 2 in BIL. "
                f"Seeded high-water mark {ath:.4f}." if ath else
                "90% SPY, 5% bullet 1 in BIL, 5% bullet 2 in BIL. High-water mark starts at the first close."
            ),
            "ath": ath,
        }
    ]
    warnings: list[str] = []
    prev_bil = None
    year_ends = _year_end_set(dates)
    values, snapshots, state = _advance_two_bullet(
        dates=dates,
        close=close,
        high=high,
        opened=opened,
        spy=spy,
        bil=bil,
        cost_rate=cost_rate,
        spy_shares=spy_shares,
        bullet1=bullet1,
        bullet2=bullet2,
        ath=ath,
        locked_ath=locked_ath,
        floor=floor,
        cycle=cycle,
        prev_bil=prev_bil,
        events=events,
        year_ends=year_ends,
    )
    cohorts = _cohorts_from_snapshots(snapshots, capital)
    equity_series = pd.Series(values, dtype=float).sort_index()
    return {
        "equity": equity_series,
        "events": events,
        "cohorts": cohorts,
        "warnings": warnings,
        "state": state,
    }


def resume_two_bullet(
    state: dict,
    spy_adj: pd.Series,
    bil_adj: pd.Series,
    tqqq_close: pd.Series,
    tqqq_high: pd.Series,
    tqqq_open: pd.Series,
    cost_bps: float = 0.0,
) -> dict:
    """Continue a frozen two-bullet book. Sessions on or before ``state['as_of']`` are left as history."""
    as_of = pd.Timestamp(state["as_of"]).tz_localize(None).normalize()
    close = tqqq_close.astype(float).sort_index()
    close = close[~close.index.duplicated(keep="last")]
    dates = close.index[close.index > as_of]
    if len(dates) == 0:
        return {"equity": pd.Series(dtype=float), "events": [], "holdings": [], "state": state}
    high = tqqq_high.reindex(close.index).astype(float).fillna(close)
    opened = tqqq_open.reindex(close.index).astype(float).fillna(close)
    spy = spy_adj.reindex(close.index).ffill().astype(float)
    bil = bil_adj.reindex(close.index).ffill().astype(float)
    window = dates
    if spy.reindex(window).isna().any() or bil.reindex(window).isna().any() or close.reindex(window).isna().any():
        raise ValueError("SPY, BIL, or TQQQ has a gap on a forward session")
    bullet1 = _bullet_from_state(state["bullet1"])
    bullet2 = _bullet_from_state(state["bullet2"])
    events: list[dict] = []
    values, snapshots, new_state = _advance_two_bullet(
        dates=window,
        close=close,
        high=high,
        opened=opened,
        spy=spy,
        bil=bil,
        cost_rate=cost_bps / 10_000.0,
        spy_shares=float(state["spy_shares"]),
        bullet1=bullet1,
        bullet2=bullet2,
        ath=None if state.get("ath") is None else float(state["ath"]),
        locked_ath=None if state.get("locked_ath") is None else float(state["locked_ath"]),
        floor=None if state.get("floor") is None else float(state["floor"]),
        cycle=bool(state["cycle"]),
        prev_bil=None if state.get("prev_bil") is None else float(state["prev_bil"]),
        events=events,
        year_ends=set(),
    )
    equity = pd.Series(values, dtype=float).sort_index()
    holdings = snapshots[-1]["holdings"] if snapshots else []
    return {"equity": equity, "events": events, "holdings": holdings, "state": new_state}


def _advance_two_bullet(
    dates,
    close,
    high,
    opened,
    spy,
    bil,
    cost_rate,
    spy_shares,
    bullet1,
    bullet2,
    ath,
    locked_ath,
    floor,
    cycle,
    prev_bil,
    events,
    year_ends,
):
    values: dict[pd.Timestamp, float] = {}
    snapshots: list[dict] = []
    for dt in dates:
        stamp = pd.Timestamp(dt)
        px = float(close.loc[dt])
        hi = float(high.loc[dt])
        op = float(opened.loc[dt])
        bil_px = float(bil.loc[dt])
        if prev_bil is not None and prev_bil > 0:
            growth = bil_px / prev_bil
            bullet1.cash *= growth
            bullet2.cash *= growth
        prev_bil = bil_px

        exited_today = False
        if cycle and floor is not None and (bullet1.shares + bullet2.shares) > 0 and hi >= EXIT_MULTIPLE * floor:
            limit = EXIT_MULTIPLE * floor
            fill = op if op > limit else limit
            _liquidate(bullet1, fill, cost_rate, events, stamp, floor, limit)
            _liquidate(bullet2, fill, cost_rate, events, stamp, floor, limit)
            bullet1.retire()
            bullet2.retire()
            cycle = False
            floor = None
            locked_ath = None
            exited_today = True
            # Keep the old high-water mark. The memo's live page still quotes
            # the prior peak after the cycle is cashed. Ratchet it up if the
            # exit day's close itself is a new high, but do not arm a new
            # bullet on the exit day.
            if ath is None or px > ath:
                ath = px
            events.append(
                {
                    "date": stamp.date().isoformat(),
                    "type": "cycle-end",
                    "detail": f"Limit filled at {fill:.4f}. Satellite cash is back in BIL. A new cycle can start next session.",
                    "fill": fill,
                }
            )

        if not cycle and not exited_today:
            if ath is None or px > ath:
                ath = px
            if _drawn_down(px, ath, BULLET1_DRAWDOWN):
                cycle = True
                locked_ath = ath
                floor = px
                _arm(bullet1, stamp, events, px, locked_ath)
                if _drawn_down(px, locked_ath, BULLET2_DRAWDOWN):
                    _arm(bullet2, stamp, events, px, locked_ath)
        elif cycle and not bullet2.active and locked_ath and _drawn_down(px, locked_ath, BULLET2_DRAWDOWN):
            _arm(bullet2, stamp, events, px, locked_ath)

        if cycle:
            _deploy_due(bullet1, stamp, px, cost_rate, events)
            _deploy_due(bullet2, stamp, px, cost_rate, events)
            floor = px if floor is None else min(floor, px)

        equity = (
            spy_shares * float(spy.loc[dt])
            + bullet1.cash
            + bullet2.cash
            + (bullet1.shares + bullet2.shares) * px
        )
        values[stamp] = equity
        if stamp in year_ends or stamp == pd.Timestamp(dates[-1]):
            snapshots.append(_snapshot(stamp, equity, spy_shares, float(spy.loc[dt]), bullet1, bullet2, px))

    state = _engine_state(
        as_of=pd.Timestamp(dates[-1]),
        spy_shares=spy_shares,
        bullet1=bullet1,
        bullet2=bullet2,
        ath=ath,
        locked_ath=locked_ath,
        floor=floor,
        cycle=cycle,
        prev_bil=prev_bil,
        equity=float(values[pd.Timestamp(dates[-1])]),
    )
    return values, snapshots, state


def _engine_state(as_of, spy_shares, bullet1, bullet2, ath, locked_ath, floor, cycle, prev_bil, equity) -> dict:
    return {
        "as_of": pd.Timestamp(as_of).date().isoformat(),
        "spy_shares": float(spy_shares),
        "ath": None if ath is None else float(ath),
        "locked_ath": None if locked_ath is None else float(locked_ath),
        "floor": None if floor is None else float(floor),
        "cycle": bool(cycle),
        "prev_bil": None if prev_bil is None else float(prev_bil),
        "equity": float(equity),
        "bullet1": _bullet_state(bullet1),
        "bullet2": _bullet_state(bullet2),
    }


def _bullet_state(bullet: _Bullet) -> dict:
    trigger = None if bullet.trigger is None else pd.Timestamp(bullet.trigger).date().isoformat()
    return {
        "name": bullet.name,
        "cash": float(bullet.cash),
        "shares": float(bullet.shares),
        "active": bool(bullet.active),
        "trigger": trigger,
        "budget": float(bullet.budget),
        "tranches": int(bullet.tranches),
    }


def _bullet_from_state(payload: dict) -> _Bullet:
    trigger = payload.get("trigger")
    return _Bullet(
        name=payload["name"],
        cash=float(payload["cash"]),
        shares=float(payload.get("shares") or 0.0),
        active=bool(payload.get("active")),
        trigger=None if not trigger else pd.Timestamp(trigger),
        budget=float(payload.get("budget") or 0.0),
        tranches=int(payload.get("tranches") or 0),
    )


def _drawn_down(price: float, ath: float, drawdown: float) -> bool:
    """True when price is at or below (1 - drawdown) × the high-water mark.

    0.8 is not exact in binary floating point, so an 80% drop that lands on
    a round price (20 after a high of 100) has to count.
    """
    if ath <= 0:
        return False
    return price / ath <= (1.0 - drawdown) + 1e-9


def _arm(bullet: _Bullet, stamp: pd.Timestamp, events: list, price: float, locked_ath: float) -> None:
    bullet.active = True
    bullet.trigger = stamp
    bullet.budget = bullet.cash
    bullet.tranches = 0
    drawdown = BULLET1_DRAWDOWN if bullet.name == "bullet-1" else BULLET2_DRAWDOWN
    level = 1.0 - drawdown
    events.append(
        {
            "date": stamp.date().isoformat(),
            "type": "trigger",
            "bullet": bullet.name,
            "detail": (
                f"{bullet.name} armed at {price:.4f}, "
                f"{(1 - price / locked_ath):.1%} below the locked high-water mark {locked_ath:.4f} "
                f"(rule level {level:.0%})."
            ),
            "price": price,
            "ath": locked_ath,
            "budget": bullet.budget,
        }
    )


def _deploy_due(bullet: _Bullet, stamp: pd.Timestamp, price: float, cost_rate: float, events: list) -> None:
    if not bullet.active or bullet.trigger is None:
        return
    while bullet.tranches < len(TRANCHE_OFFSETS):
        due = bullet.trigger + pd.Timedelta(days=int(TRANCHE_OFFSETS[bullet.tranches]))
        if stamp < due:
            return
        fraction = TRANCHE_FRACTIONS[bullet.tranches]
        spend_target = fraction * bullet.budget
        spend = min(bullet.cash, spend_target)
        tranche_number = bullet.tranches + 1
        bullet.tranches += 1
        if spend <= 0 or price <= 0:
            continue
        net = spend / (1.0 + cost_rate)
        bought = net / price
        bullet.cash -= spend
        bullet.shares += bought
        events.append(
            {
                "date": stamp.date().isoformat(),
                "type": "buy",
                "bullet": bullet.name,
                "detail": (
                    f"{bullet.name} tranche {tranche_number}/3 bought {bought:.4f} shares at {price:.4f} "
                    f"({fraction:.0%} of the trigger budget)."
                ),
                "price": price,
                "shares": bought,
                "spend": spend,
            }
        )


def _liquidate(bullet, fill, cost_rate, events, stamp, floor, limit) -> None:
    if bullet.shares <= 0:
        return
    gross = bullet.shares * fill
    net = gross / (1.0 + cost_rate)
    sold = bullet.shares
    bullet.cash += net
    bullet.shares = 0.0
    events.append(
        {
            "date": stamp.date().isoformat(),
            "type": "sell",
            "bullet": bullet.name,
            "detail": (
                f"{bullet.name} sold {sold:.4f} shares at {fill:.4f}. "
                f"Limit was {limit:.4f} (10× floor {floor:.4f})."
            ),
            "price": fill,
            "shares": sold,
            "proceeds": net,
        }
    )


def _snapshot(stamp, equity, spy_shares, spy_px, bullet1: _Bullet, bullet2: _Bullet, tqqq_px: float) -> dict:
    rows = [
        ("SPY", "SPDR S&P 500 ETF Trust", spy_shares * spy_px, spy_shares),
        ("TQQQ", "Bullet 1 ProShares UltraPro QQQ", bullet1.shares * tqqq_px, bullet1.shares),
        ("TQQQ", "Bullet 2 ProShares UltraPro QQQ", bullet2.shares * tqqq_px, bullet2.shares),
        ("BIL", "Bullet 1 dry powder (Treasury-bill proxy)", bullet1.cash, None),
        ("BIL", "Bullet 2 dry powder (Treasury-bill proxy)", bullet2.cash, None),
    ]
    holdings = []
    for ticker, name, value, shares in rows:
        holdings.append(
            {
                "ticker": ticker,
                "name": name,
                "weight": value / equity if equity else None,
                "value": value,
                "shares": shares,
            }
        )
    return {"date": stamp, "equity": equity, "holdings": holdings}


def _cohorts_from_snapshots(snapshots: list[dict], capital: float) -> list[dict]:
    cohorts = []
    previous_equity = capital
    previous_date = None
    for snap in snapshots:
        stamp = pd.Timestamp(snap["date"])
        # The opening mark is the base, not a completed year, when it falls
        # in December of the year before the first holding year and a later
        # snapshot exists. Still publish it so the sleeve weights are visible.
        entry = previous_equity
        ret = snap["equity"] / entry - 1.0 if entry else None
        if previous_date is None:
            ret = None
        cohorts.append(
            {
                "year": int(stamp.year),
                "rebalance_date": None if previous_date is None else pd.Timestamp(previous_date).date().isoformat(),
                "period_end": stamp.date().isoformat(),
                "period_return": ret,
                "entry_equity": entry if previous_date is not None else snap["equity"],
                "exit_equity": snap["equity"],
                "eligible": None,
                "universe_size": None,
                "holdings": snap["holdings"],
                "note": "Mark-to-market sleeve weights. The 90/10 split is not rebalanced.",
            }
        )
        previous_equity = snap["equity"]
        previous_date = stamp
    return cohorts


def _year_end_set(dates: pd.DatetimeIndex) -> set[pd.Timestamp]:
    stamps = pd.DatetimeIndex(dates)
    ends = set()
    for year in sorted(set(int(stamp.year) for stamp in stamps)):
        in_year = stamps[stamps.year == year]
        ends.add(pd.Timestamp(in_year[-1]))
    return ends


def _slice_symbol(panel: pd.DataFrame, symbol: str, start, end) -> pd.Series:
    series = panel[symbol].dropna().astype(float).sort_index()
    series = series[(series.index >= pd.Timestamp(start)) & (series.index <= pd.Timestamp(end))]
    if series.empty:
        raise ValueError(f"no {symbol} prices in range")
    return series


def _start_session(index: pd.DatetimeIndex, start_year: int) -> pd.Timestamp:
    """Last session of the year before `start_year`, else the first session of that year."""
    prior = index[index.year == start_year - 1]
    if len(prior):
        return pd.Timestamp(prior[-1])
    during = index[index.year == start_year]
    if len(during) == 0:
        raise ValueError(f"no TQQQ session in {start_year - 1} or {start_year}")
    return pd.Timestamp(during[0])


def _session_on_or_before(index: pd.DatetimeIndex, when: pd.Timestamp) -> pd.Timestamp:
    when = pd.Timestamp(when)
    prior = index[index <= when]
    if len(prior) == 0:
        raise ValueError(f"no session on or before {when.date()}")
    return pd.Timestamp(prior[-1])


def _source_caveats(ctx: RunContext) -> list[str]:
    caveats = [
        (
            "FREE SOURCE — Yahoo Finance daily prices. This book does not pick stocks from the "
            "S&P 500, so index survivorship bias does not apply. The bars are still vendor-adjusted "
            "free data. Sharadar SEP is the intended price replacement."
        )
    ]
    if ctx.source.free_source:
        return caveats
    return list(ctx.source.caveats())


def date_from_year(year: int):
    from datetime import date

    return date(year, 1, 1)
