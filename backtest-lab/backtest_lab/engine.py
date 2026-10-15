"""Year-end equal-weight portfolio simulator.

The strategy decides what to hold. This module only applies those weights
to adjusted closes, charges an optional cost on traded notional, and
records the cohort that was actually owned.

Dividends: a constant share count marked on dividend-adjusted closes
reproduces total return, including reinvested dividends. That is the v0
model. It is not a cash-dividend ledger.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class Holding:
    ticker: str
    weight: float
    name: str | None = None
    sector: str | None = None
    prior_year_return: float | None = None
    market_cap: float | None = None


@dataclass
class Book:
    """Target portfolio bought at the year-end close and held through `hold_end`."""

    year: int
    rebalance: pd.Timestamp
    hold_end: pd.Timestamp
    holdings: list[Holding]
    eligible: int
    universe_size: int


@dataclass
class Simulation:
    equity: pd.Series
    cohorts: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def trade_to_weights(
    equity: float,
    prices: pd.Series,
    old_shares: dict[str, float],
    weights: dict[str, float],
    cost_rate: float,
) -> tuple[dict[str, float], float, float]:
    """Rebalance to `weights` of post-cost equity.

    Cost is `cost_rate` times the dollar value traded (sells and buys both
    count). The post-cost equity is the fixed point of that charge, so
    overlapping positions are not taxed as a full liquidation.

    Returns (new_shares, cost_dollars, post_equity).
    """
    if equity <= 0:
        raise ValueError("equity must be positive")
    if not weights:
        raise ValueError("target weights are empty")
    weight_sum = sum(weights.values())
    if abs(weight_sum - 1.0) > 1e-6:
        raise ValueError(f"weights must sum to 1, got {weight_sum}")

    old_values = {ticker: shares * float(prices[ticker]) for ticker, shares in old_shares.items()}
    investable = float(equity)
    cost = 0.0
    for _ in range(12):
        desired = {ticker: investable * weight for ticker, weight in weights.items()}
        traded = 0.0
        for ticker in set(old_values) | set(desired):
            traded += abs(desired.get(ticker, 0.0) - old_values.get(ticker, 0.0))
        cost = traded * cost_rate
        investable = float(equity) - cost
    if investable <= 0:
        raise ValueError("transaction costs consume the whole portfolio")
    new_shares = {
        ticker: (investable * weight) / float(prices[ticker])
        for ticker, weight in weights.items()
        if weight > 0
    }
    return new_shares, cost, investable


def simulate_annual(
    prices: pd.DataFrame,
    books: list[Book],
    initial: float,
    cost_bps: float,
) -> Simulation:
    """Mark `prices` (adjusted closes) between the year-end books.

    On each rebalance date the curve stores the post-trade value. Cohort
    return runs from that post-trade entry to the next rebalance close
    *before* the next trade, so a cohort's return does not include the
    cost of entering the following book.
    """
    if not books:
        raise ValueError("no rebalance books")
    if initial <= 0:
        raise ValueError("initial capital must be positive")
    prices = prices.sort_index()
    cost_rate = cost_bps / 10_000.0
    shares: dict[str, float] = {}
    values: dict[pd.Timestamp, float] = {}
    cohorts: list[dict] = []
    warnings: list[str] = []

    for index, book in enumerate(books):
        reb = pd.Timestamp(book.rebalance)
        if reb not in prices.index:
            raise ValueError(f"{book.year} rebalance {reb.date()} is not in the price index")
        px = prices.loc[reb]
        if shares:
            pre = _mark(shares, px)
        else:
            pre = float(initial)
        if index > 0:
            _close_cohort(cohorts[-1], pre, reb)

        weights, notes = _usable_weights(book, px, shares)
        warnings.extend(notes)
        new_shares, cost, post = trade_to_weights(pre, px, shares, weights, cost_rate)
        values[reb] = post

        if index + 1 < len(books):
            nxt = pd.Timestamp(books[index + 1].rebalance)
            span = prices.index[(prices.index >= reb) & (prices.index < nxt)]
        else:
            hold_end = pd.Timestamp(book.hold_end)
            span = prices.index[(prices.index >= reb) & (prices.index <= hold_end)]
        # Include the rebalance close so a missing print the next session
        # forward-fills from the price we just traded, then drop that seed row.
        if len(span) > 1:
            seg = prices.reindex(span)[list(new_shares)].ffill().iloc[1:]
            if seg.isna().any().any():
                missing = sorted(set(seg.columns[seg.isna().any()]))
                raise ValueError(f"no price path for {missing} after {reb.date()}")
            series = seg.mul(pd.Series(new_shares), axis=1).sum(axis=1)
            for stamp, value in series.items():
                values[pd.Timestamp(stamp)] = float(value)

        cohorts.append(
            {
                "year": book.year,
                "rebalance_date": reb.date().isoformat(),
                "period_end": None,
                "period_return": None,
                "entry_equity": post,
                "entry_cost": cost,
                "exit_equity": None,
                "eligible": book.eligible,
                "universe_size": book.universe_size,
                "holdings": [
                    {
                        "ticker": holding.ticker,
                        "name": holding.name,
                        "sector": holding.sector,
                        "weight": weights[holding.ticker],
                        "prior_year_return": holding.prior_year_return,
                        "market_cap": holding.market_cap,
                    }
                    for holding in book.holdings
                    if holding.ticker in weights
                ],
            }
        )
        shares = new_shares

    equity = pd.Series(values, dtype=float).sort_index()
    equity.index = pd.DatetimeIndex(equity.index)
    last_dt = pd.Timestamp(equity.index[-1])
    _close_cohort(cohorts[-1], float(equity.iloc[-1]), last_dt)
    # The final mark is not a trade, so exit equals the published equity.
    return Simulation(equity=equity, cohorts=cohorts, warnings=warnings)


def cohort_windows(
    index: pd.DatetimeIndex,
    start_year: int,
    end,
) -> list[tuple[int, pd.Timestamp, pd.Timestamp]]:
    """(calendar year held, year-end rebalance, last mark of the holding year)."""
    index = pd.DatetimeIndex(pd.to_datetime(index)).sort_values().unique()
    last_dt = index[index <= pd.Timestamp(end)]
    if len(last_dt) == 0:
        raise ValueError(f"no prices on or before {end}")
    last_dt = pd.Timestamp(last_dt[-1])
    windows: list[tuple[int, pd.Timestamp, pd.Timestamp]] = []
    year = start_year
    while year <= end.year:
        reb_days = index[index.year == year - 1]
        if len(reb_days) == 0:
            break
        reb = pd.Timestamp(reb_days[-1])
        if reb > last_dt:
            break
        hold_days = index[(index.year == year) & (index <= last_dt)]
        if len(hold_days) == 0:
            break
        hold_end = pd.Timestamp(hold_days[-1])
        if hold_end <= reb:
            break
        windows.append((year, reb, hold_end))
        if hold_end >= last_dt:
            break
        year += 1
    if not windows:
        raise ValueError(f"no annual cohorts between {start_year} and {end}")
    return windows


def prior_year_returns(adjusted: pd.DataFrame, year: int) -> pd.Series:
    """Total return over calendar `year`, year-end close to year-end close.

    Both endpoints must be real prints. A forward-filled price is not a print,
    so a halted name cannot win the ranking on a stale close.
    """
    end_days = adjusted.index[adjusted.index.year == year]
    start_days = adjusted.index[adjusted.index.year == year - 1]
    if len(end_days) == 0 or len(start_days) == 0:
        return pd.Series(dtype=float)
    px1 = adjusted.loc[end_days[-1]]
    px0 = adjusted.loc[start_days[-1]]
    ret = px1 / px0 - 1.0
    ok = px0.gt(0) & px1.gt(0) & np.isfinite(ret)
    return ret.where(ok).dropna()


def _usable_weights(book: Book, prices: pd.Series, old_shares: dict[str, float]) -> tuple[dict[str, float], list[str]]:
    warnings = []
    for ticker in old_shares:
        price = prices.get(ticker)
        if price is None or not _positive(price):
            raise ValueError(f"cannot sell {ticker} on {book.rebalance.date()}: missing close")
    kept = []
    for holding in book.holdings:
        price = prices.get(holding.ticker)
        if _positive(price):
            kept.append(holding)
        else:
            warnings.append(
                f"{book.year}: dropped {holding.ticker} at rebalance because its close was missing"
            )
    if not kept:
        raise ValueError(f"{book.year}: no holdings have a close on {book.rebalance.date()}")
    weight = 1.0 / len(kept)
    return {holding.ticker: weight for holding in kept}, warnings


def _close_cohort(cohort: dict, exit_equity: float, when: pd.Timestamp) -> None:
    entry = float(cohort["entry_equity"])
    cohort["period_end"] = pd.Timestamp(when).date().isoformat()
    cohort["exit_equity"] = float(exit_equity)
    cohort["period_return"] = float(exit_equity) / entry - 1.0 if entry else None


def _mark(shares: dict[str, float], prices: pd.Series) -> float:
    total = 0.0
    for ticker, amount in shares.items():
        total += amount * float(prices[ticker])
    return total


def _positive(value) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return number == number and number > 0
