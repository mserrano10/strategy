"""Annual year-end rank strategies on the current S&P 500.

`mode="return"` ranks the prior calendar year's total return.
`mode="cap"` ranks approximated market cap at that year-end close.
Both hold the top `n` names in equal weight until the next year-end close.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from backtest_lab.data.caps import market_cap
from backtest_lab.engine import Book, Holding, cohort_windows, prior_year_returns, simulate_annual
from backtest_lab.report import assemble_report
from backtest_lab.strategies.base import RunContext

MARKET_CAP_NOTE = (
    "Historical market cap on the free source is an approximation: "
    "split-adjusted close × the nearest Yahoo share count, multiplied by "
    "splits that occurred after that share-count date. Dates before Yahoo's "
    "first share record use the earliest record. Names with no share history "
    "use today's share count. Buybacks, issuance, and stale records are not "
    "corrected. Sharadar SF1 marketcap replaces this."
)


class AnnualRank:
    def __init__(self, strategy_id: str, name: str, n: int, mode: str, description: str):
        if mode not in {"return", "cap"}:
            raise ValueError(mode)
        if n < 1:
            raise ValueError("n must be at least 1")
        self.id = strategy_id
        self.name = name
        self.n = n
        self.mode = mode
        self.description = description

    def run(self, ctx: RunContext) -> list[dict]:
        if ctx.rebalance != "annual":
            raise SystemExit(
                "v0 rank strategies only rebalance annually at the year-end close. "
                "Pass --rebalance annual."
            )
        if not ctx.source.survivorship_bias_free:
            # The whole point of the banner. Rank books on the free source
            # are biased; do not let a caller strip the flag.
            pass
        members = ctx.source.universe(refresh=ctx.refresh_universe)
        tickers = members["ticker"].tolist()
        start = date(ctx.start_year - 2, 1, 1)
        history = ctx.source.history(tickers, start, ctx.end)
        self._check_coverage(tickers, history.missing)
        # Cap is attached to every rank book, including return ranks, so the
        # holdings table can show size. Selection still follows `mode`.
        basis = ctx.source.cap_basis(list(history.adjusted_close.columns))
        report = self.build(
            adjusted=history.adjusted_close,
            traded_close=history.close,
            basis=basis,
            members=members,
            start_year=ctx.start_year,
            end=ctx.end,
            capital=ctx.capital,
            cost_bps=ctx.cost_bps,
            source=ctx.source,
            extra_warnings=_missing_warning(history.missing),
            fallback_names=list(getattr(ctx.source, "_fallback_caps", [])),
        )
        return [report]

    def build(
        self,
        adjusted: pd.DataFrame,
        traded_close: pd.DataFrame,
        basis: dict[str, pd.Series],
        members: pd.DataFrame,
        start_year: int,
        end: date,
        capital: float,
        cost_bps: float,
        source,
        extra_warnings: list[str] | None = None,
        fallback_names: list[str] | None = None,
    ) -> dict:
        names = members.set_index("ticker")["name"].to_dict() if "name" in members.columns else {}
        sectors = members.set_index("ticker")["sector"].to_dict() if "sector" in members.columns else {}
        windows = cohort_windows(adjusted.index, start_year, end)
        books: list[Book] = []
        warnings = list(extra_warnings or [])
        if fallback_names and self.mode == "cap":
            preview = ", ".join(fallback_names[:8])
            suffix = "" if len(fallback_names) <= 8 else ", …"
            warnings.append(
                f"{len(fallback_names)} names used today's share count "
                f"(no Yahoo share history): {preview}{suffix}"
            )
        for year, reb, hold_end in windows:
            returns = prior_year_returns(adjusted, year - 1)
            close_row = traded_close.loc[reb] if reb in traded_close.index else None
            caps = _caps_on(close_row, basis, reb) if close_row is not None else pd.Series(dtype=float)
            if self.mode == "return":
                scores = returns
                signal = "prior calendar-year total return"
            else:
                scores = caps
                signal = "approximated market cap at the prior year-end close"
            eligible = scores.dropna()
            if eligible.empty:
                raise RuntimeError(f"{self.id}: no eligible names for the {year} book")
            picks = _top_n(eligible, self.n)
            if len(picks) < self.n:
                warnings.append(
                    f"{year}: only {len(picks)} eligible names for a top-{self.n} book"
                )
            holdings = []
            for ticker in picks:
                holdings.append(
                    Holding(
                        ticker=ticker,
                        weight=1.0 / len(picks),
                        name=names.get(ticker),
                        sector=None if sectors.get(ticker) is None else str(sectors.get(ticker)),
                        prior_year_return=_maybe(returns, ticker),
                        market_cap=_maybe(caps, ticker),
                    )
                )
            books.append(
                Book(
                    year=year,
                    rebalance=reb,
                    hold_end=hold_end,
                    holdings=holdings,
                    eligible=int(len(eligible)),
                    universe_size=int(len(members)),
                )
            )
        simulation = simulate_annual(adjusted, books, capital, cost_bps)
        warnings.extend(simulation.warnings)
        caveats = list(source.caveats())
        caveats.append(
            "Rebalance is the year-end close. The signal uses only prices through that close. "
            "The fill is that same close, which is a small optimistic bias versus trading the next open."
        )
        caveats.append(
            "Equal weight. Dividends enter through adjusted closes: the book holds a constant "
            "share count of the adjusted series, which matches total return."
        )
        if self.mode == "cap":
            caveats.append(MARKET_CAP_NOTE)
            caveats.append(
                "Both share classes are eligible when both are in the current S&P 500. "
                "GOOG and GOOGL can therefore occupy two of the top-five slots."
            )
        else:
            caveats.append(
                "Market caps printed on the holdings are the free-data approximation and are not used to select names. "
                + MARKET_CAP_NOTE
            )
        universe = {
            "id": "sp500-current",
            "survivorship_bias_free": bool(source.survivorship_bias_free),
            "count": int(len(members)),
            "source": str(members["source"].iloc[0]) if "source" in members.columns and len(members) else None,
            "note": "Current constituents applied to every historical year.",
        }
        return assemble_report(
            strategy_id=self.id,
            name=self.name,
            description=self.description,
            kind="annual-rank",
            group="Size" if self.mode == "cap" else "Prior-year return",
            signal=signal,
            rebalance="annual year-end close",
            equity=simulation.equity,
            cohorts=simulation.cohorts,
            events=[],
            caveats=caveats,
            warnings=warnings,
            source=source,
            universe=universe,
            capital=capital,
            cost_bps=cost_bps,
            start_year=start_year,
            market_cap_note=MARKET_CAP_NOTE if self.mode == "cap" else None,
        )

    def _check_coverage(self, tickers: list[str], missing: list[str]) -> None:
        if not tickers:
            raise RuntimeError("universe is empty")
        ratio = len(missing) / len(tickers)
        if ratio > 0.30:
            preview = ", ".join(missing[:12])
            raise RuntimeError(
                f"{len(missing)} of {len(tickers)} constituents had no Yahoo prices ({preview}). "
                "Refusing to rank a universe this incomplete."
            )


def _caps_on(close_row: pd.Series, basis: dict[str, pd.Series], asof: pd.Timestamp) -> pd.Series:
    values = {}
    for ticker, price in close_row.items():
        series = basis.get(ticker)
        if series is None or len(series) == 0:
            continue
        try:
            price_value = float(price)
        except (TypeError, ValueError):
            continue
        cap = market_cap(series, price_value, asof)
        if cap is not None:
            values[ticker] = cap
    return pd.Series(values, dtype=float)


def _top_n(scores: pd.Series, n: int) -> list[str]:
    frame = scores.rename("score").reset_index()
    frame.columns = ["ticker", "score"]
    frame = frame.sort_values(["score", "ticker"], ascending=[False, True], kind="mergesort")
    return frame["ticker"].head(n).tolist()


def _maybe(scores: pd.Series, ticker: str):
    if scores is None or ticker not in scores.index:
        return None
    value = scores.loc[ticker]
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if value != value:
        return None
    return value


def _missing_warning(missing: list[str]) -> list[str]:
    if not missing:
        return []
    preview = ", ".join(missing[:12])
    suffix = "" if len(missing) <= 12 else ", …"
    return [f"Dropped {len(missing)} constituents with no Yahoo price history: {preview}{suffix}"]
