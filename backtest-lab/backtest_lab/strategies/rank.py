"""Annual year-end rank strategies on the current S&P 500.

`mode="return"` ranks the prior calendar year's total return.
`mode="cap"` ranks approximated market cap at that year-end close.
Both hold the top `n` names in equal weight until the next year-end close.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from backtest_lab.data.caps import corrupt_close_tickers, market_cap
from backtest_lab.engine import (
    Book,
    Holding,
    cohort_windows,
    prior_year_returns,
    simulate_annual,
    without_unverified_extremes,
)
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
        universe_id = ctx.universe_id or "sp500-current"
        if universe_id == "us-listed" and self.mode != "return":
            raise SystemExit(
                "The US common-stock universe is the size-floor return screen. "
                "Run best-returner or top5-returners."
            )
        start = date(ctx.start_year - 2, 1, 1)
        membership = None
        stooq_note = None
        identity_notes: list[str] = []
        if universe_id == "sp500-current":
            members = ctx.source.universe(refresh=ctx.refresh_universe)
            tickers = members["ticker"].tolist()
            history = ctx.source.history(tickers, start, ctx.end)
            self._check_coverage(tickers, history.missing)
            adjusted = history.adjusted_close
            traded = history.close
        elif universe_id == "sp500-pit":
            from backtest_lab.data.adjust import apply_identity
            from backtest_lab.data.sp500_pit import load_point_in_time
            from backtest_lab.data.stooq import supplement

            membership = load_point_in_time(refresh=ctx.refresh_universe)
            tickers = membership.tickers_between(ctx.start_year - 1, ctx.end.year)
            # Renames (FB → META) need the successor's prices even when the
            # new ticker was not itself a year-end member in the early years.
            for rename in membership.renames():
                if rename["new"] not in tickers:
                    tickers.append(rename["new"])
            history = ctx.source.history(tickers, start, ctx.end)
            status = supplement(
                history,
                tickers,
                start,
                ctx.end,
                ctx.source.cache_dir / "stooq",
            )
            if status.blocked:
                stooq_note = (
                    "Stooq answered with a browser check instead of a CSV, so it filled no "
                    "delisted names on this run. Unpriceable tickers are listed on the book."
                )
            elif status.filled:
                stooq_note = (
                    f"Stooq filled {len(status.filled)} tickers Yahoo did not have. "
                    "Those bars have no dividend adjustment (price return)."
                )
            adjusted, identity_notes = apply_identity(history.adjusted_close, membership)
            traded, _ = apply_identity(history.close, membership)
            members = membership.members_on(ctx.end)
        elif universe_id == "us-listed":
            from backtest_lab.data.listings import load_us_common

            if not ctx.min_market_cap:
                raise SystemExit("us-listed needs --min-cap, for example 10b or 2b.")
            members = load_us_common(refresh=ctx.refresh_universe)
            members = members.rename(columns={"exchange": "sector"})
            members["sector"] = members["sector"].fillna("")
            tickers = members["ticker"].tolist()
            history = ctx.source.history(tickers, start, ctx.end)
            adjusted = history.adjusted_close
            traded = history.close
        else:
            raise SystemExit(f"Unknown universe {universe_id!r}.")
        priced = [ticker for ticker in adjusted.columns if adjusted[ticker].notna().any()]
        basis = ctx.source.cap_basis(priced)
        extra = _missing_warning(history.missing) if universe_id == "sp500-current" else []
        if stooq_note:
            extra.append(stooq_note)
        extra.extend(identity_notes)
        report_id, report_name, meta, banner = _identity(self, universe_id, ctx.min_market_cap, membership, members)
        report = self.build(
            adjusted=adjusted,
            traded_close=traded,
            basis=basis,
            members=members,
            start_year=ctx.start_year,
            end=ctx.end,
            capital=ctx.capital,
            cost_bps=ctx.cost_bps,
            source=ctx.source,
            extra_warnings=extra,
            fallback_names=list(getattr(ctx.source, "_fallback_caps", [])),
            membership=membership,
            min_market_cap=ctx.min_market_cap,
            report_id=report_id,
            report_name=report_name,
            universe_meta=meta,
            banner=banner,
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
        membership=None,
        min_market_cap: float | None = None,
        report_id: str | None = None,
        report_name: str | None = None,
        universe_meta: dict | None = None,
        banner: str | None = None,
    ) -> dict:
        names = members.set_index("ticker")["name"].to_dict() if "name" in members.columns else {}
        sectors = members.set_index("ticker")["sector"].to_dict() if "sector" in members.columns else {}
        windows = cohort_windows(adjusted.index, start_year, end)
        books: list[Book] = []
        warnings = list(extra_warnings or [])
        signal = "prior calendar-year total return" if self.mode == "return" else "approximated market cap at the prior year-end close"
        if fallback_names and self.mode == "cap":
            preview = ", ".join(fallback_names[:8])
            suffix = "" if len(fallback_names) <= 8 else ", …"
            warnings.append(
                f"{len(fallback_names)} names used today's share count "
                f"(no Yahoo share history): {preview}{suffix}"
            )
        corrupt = corrupt_close_tickers(traded_close) if min_market_cap else []
        if corrupt:
            preview = ", ".join(corrupt[:8])
            suffix = "" if len(corrupt) <= 8 else ", …"
            warnings.append(
                f"Dropped {len(corrupt)} listings whose split-adjusted history peaks above "
                f"$20,000 while the latest print is under $10,000 ({preview}{suffix}). "
                "Yahoo's reverse-split adjustment made those series unusable."
            )
        coverage = []
        for year, reb, hold_end in windows:
            year_members = members if membership is None else membership.members_on(reb)
            year_names = year_members.set_index("ticker")["name"].to_dict() if len(year_members) else {}
            year_sectors = year_members.set_index("ticker")["sector"].to_dict() if len(year_members) else {}
            names.update({key: value for key, value in year_names.items() if value})
            sectors.update(year_sectors)
            allowed = set(year_members["ticker"].tolist())
            returns = prior_year_returns(adjusted.reindex(columns=list(allowed)), year - 1)
            close_row = traded_close.loc[reb] if reb in traded_close.index else None
            caps = _caps_on(close_row, basis, reb) if close_row is not None else pd.Series(dtype=float)
            caps = caps.reindex(list(allowed)).dropna()
            extreme_names: list[str] = []
            if self.mode == "return":
                scores, extreme_names = without_unverified_extremes(returns)
                signal = "prior calendar-year total return"
            else:
                scores = caps
                signal = "approximated market cap at the prior year-end close"
            eligible = scores.dropna()
            if min_market_cap:
                if corrupt:
                    eligible = eligible.drop(index=[ticker for ticker in corrupt if ticker in eligible.index], errors="ignore")
                big_enough = caps[caps >= min_market_cap].index
                eligible = eligible.reindex(big_enough).dropna()
                signal = f"{signal}, market cap at least {min_market_cap:,.0f}"
            unpriced = sorted(allowed - set(returns.dropna().index))
            if min_market_cap:
                extreme_in_universe = [
                    ticker
                    for ticker in extreme_names
                    if ticker not in set(corrupt)
                    and ticker in caps.index
                    and float(caps[ticker]) >= min_market_cap
                ]
                # Delisted names are absent from a current listing file, so they
                # are not in this hole. The hole is the cap-clearing names whose
                # prior-year return was dropped as unverified.
                bracket_missing = len(extreme_in_universe)
                bracket_base = len(eligible) + bracket_missing
            else:
                bracket_missing = len(unpriced)
                bracket_base = len(allowed)
            bankrupt = []
            if membership is not None:
                for ticker, ret in returns.dropna().items():
                    removal = membership.removal(ticker)
                    if removal and removal["bankruptcy"] and ret <= -0.99:
                        if pd.Timestamp(removal["date"]).year == year - 1:
                            bankrupt.append(ticker)
            coverage.append(
                {
                    "year": year,
                    "signal_year": year - 1,
                    "members": int(len(allowed)),
                    "priced": int(returns.dropna().shape[0]) if self.mode == "return" else int(caps.shape[0]),
                    "eligible": int(len(eligible)),
                    "unpriceable": int(len(unpriced)) if self.mode == "return" else int(len(allowed) - len(caps)),
                    "unpriceable_names": unpriced if membership is not None else unpriced[:40],
                    "bankruptcy_modeled": bankrupt,
                    "extreme_unverified": extreme_names,
                    "bracket_missing": int(bracket_missing),
                    "bracket_base": int(bracket_base),
                }
            )
            if eligible.empty:
                raise RuntimeError(f"{self.id}: no eligible names for the {year} book")
            picks = _top_n(eligible, self.n)
            if len(picks) < self.n:
                warnings.append(
                    f"{year}: only {len(picks)} eligible names for a top-{self.n} book"
                )
            holdings = []
            for ticker in picks:
                flag = _holding_flag(membership, ticker, reb, hold_end)
                holdings.append(
                    Holding(
                        ticker=ticker,
                        weight=1.0 / len(picks),
                        name=names.get(ticker),
                        sector=None if sectors.get(ticker) is None or sectors.get(ticker) == "" else str(sectors.get(ticker)),
                        prior_year_return=_maybe(returns, ticker),
                        market_cap=_maybe(caps, ticker),
                        flag=flag,
                    )
                )
            books.append(
                Book(
                    year=year,
                    rebalance=reb,
                    hold_end=hold_end,
                    holdings=holdings,
                    eligible=int(len(eligible)),
                    universe_size=int(len(allowed)),
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
            if membership is None and not min_market_cap:
                caveats.append(
                    "Both share classes are eligible when both are in the current S&P 500. "
                    "GOOG and GOOGL can therefore occupy two of the top-five slots."
                )
        else:
            caveats.append(
                "Market caps printed on the holdings are the free-data approximation and are not used to select names. "
                + MARKET_CAP_NOTE
            )
        if coverage:
            thin = [row for row in coverage if row["unpriceable"]]
            if thin and membership is not None:
                preview = ", ".join(
                    f"{row['signal_year']}: {row['unpriceable']}" for row in coverage
                )
                warnings.append(
                    "Unpriceable S&P members by signal year (no usable return): " + preview
                )
            extreme_names = sorted({name for row in coverage for name in row.get("extreme_unverified") or []})
            if extreme_names:
                preview = ", ".join(extreme_names[:12])
                suffix = "" if len(extreme_names) <= 12 else ", …"
                warnings.append(
                    "Dropped unverified prior-year returns above +1000% "
                    f"({preview}{suffix}). No second vendor confirmed those prints, "
                    "so they cannot win a book."
                )
            bankrupt_names = sorted({name for row in coverage for name in row["bankruptcy_modeled"]})
            if bankrupt_names:
                warnings.append(
                    "Modeled at -100% because Wikipedia records a bankruptcy and no year-end print survived: "
                    + ", ".join(bankrupt_names)
                )
        if universe_meta is None:
            universe = {
                "id": "sp500-current",
                "survivorship_bias_free": bool(source.survivorship_bias_free),
                "point_in_time": False,
                "count": int(len(members)),
                "source": str(members["source"].iloc[0]) if "source" in members.columns and len(members) else None,
                "note": "Current constituents applied to every historical year.",
                "coverage": coverage,
            }
        else:
            universe = dict(universe_meta)
            universe["coverage"] = coverage
            universe["count"] = int(round(sum(row["members"] for row in coverage) / len(coverage))) if coverage else int(len(members))
        return assemble_report(
            strategy_id=report_id or self.id,
            name=report_name or self.name,
            description=(universe.get("description") or self.description),
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
            banner=banner,
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


def _holding_flag(membership, ticker: str, reb, hold_end) -> str | None:
    if membership is None:
        return None
    removal = membership.removal(ticker)
    if not removal:
        return None
    when = pd.Timestamp(removal["date"])
    if not (pd.Timestamp(reb) < when <= pd.Timestamp(hold_end) + pd.Timedelta(days=7)):
        return None
    if removal["bankruptcy"]:
        return "Bankruptcy delisting, modeled at -100% after the last real print."
    return f"Left the index {when.date().isoformat()}. Later path uses the last print."


def _identity(strategy, universe_id: str, min_cap: float | None, membership, members: pd.DataFrame):
    if universe_id == "sp500-current":
        return None, None, None, None
    if universe_id == "sp500-pit":
        counts = membership.changes_per_year()
        early = sum(counts.get(year, 0) for year in range(1990, 2011))
        report_id = f"{strategy.id}-pit"
        name = f"{strategy.name} (point-in-time)"
        fja = bool(getattr(membership, "_snapshots", None))
        if fja:
            note = (
                "Membership is the fja05680/sp500 snapshot file (S&P 500 Historical Components & Changes), "
                "last row carried forward with Wikipedia changes after that file ends. "
                "Wikipedia's selected-changes table supplies bankruptcy notes and ticker continuations. "
                f"That table records {len(membership.changes)} changes"
                f" ({early} of them before 2011) and is not a complete delisting tape. "
                "A prior-year return above +1000% is dropped unless a second vendor confirms it. "
                "Sharadar TICKERS is the complete membership file."
            )
            source_name = "fja05680/sp500 snapshots + wikipedia selected changes"
        else:
            note = (
                "Membership is today's S&P 500 walked back through Wikipedia's selected-changes table "
                "(Historical components of the S&P 500, linked from List of S&P 500 companies). "
                f"The table records {len(membership.changes)} changes"
                f" ({early} of them before 2011). Years with only a handful of rows are only partly point-in-time. "
                "Sharadar TICKERS is the complete membership file."
            )
            source_name = "wikipedia:List of S&P 500 companies + Historical components selected changes"
        meta = {
            "id": "sp500-pit",
            "survivorship_bias_free": False,
            "point_in_time": True,
            "source": source_name,
            "note": note,
            "changes_per_year": {str(year): count for year, count in sorted(counts.items())},
            "description": strategy.description.replace("current S&P 500", "point-in-time S&P 500"),
        }
        banner = (
            "POINT-IN-TIME ATTEMPT — free prices. "
            + (
                "Membership is the fja05680/sp500 history from 1996, not a survivorship-bias-free CRSP tape. "
                if fja
                else "Membership is Wikipedia's selected-changes table, which is thin before ~2011. "
            )
            + "Names Yahoo and Stooq cannot price are logged. "
            "Prior-year returns above +1000% are dropped unless a second vendor confirms them. "
            "Sharadar (SEP / SF1 / TICKERS) is the exact source."
        )
        return report_id, name, meta, banner
    billions = (min_cap or 0) / 1e9
    tag = f"{billions:.0f}b" if billions >= 1 else f"{(min_cap or 0) / 1e6:.0f}m"
    report_id = f"{strategy.id}-{tag}"
    name = f"{strategy.name} (≥ ${billions:.0f}B)" if billions >= 1 else f"{strategy.name} (≥ ${min_cap:,.0f})"
    note = (
        "Universe is the current NASDAQ, NYSE, and NYSE American common-stock list "
        f"with an approximated market cap of at least ${min_cap:,.0f} at the prior year-end. "
        "Delisted names are missing from a current listing file, so this book is survivorship-biased. "
        "Listings whose split-adjusted history peaks above $20,000 while the latest print is under "
        "$10,000 are dropped (Yahoo reverse-split failures). "
        "A prior-year return above +1000% is dropped unless a second vendor confirms it. "
        "A cap above $15 trillion is ignored. "
        "Sharadar TICKERS plus SF1 marketcap would include delisted names and replace the cap approximation."
    )
    meta = {
        "id": "us-listed",
        "survivorship_bias_free": False,
        "point_in_time": False,
        "min_market_cap": min_cap,
        "source": "nasdaqtrader:nasdaqlisted.txt + otherlisted.txt",
        "note": note,
        "listings": int(len(members)),
    }
    banner = (
        "SURVIVORSHIP-BIASED — free Yahoo prices on today's US common-stock listings, "
        f"kept only when approximated market cap was at least ${min_cap:,.0f}. "
        "Delisted companies are absent. Prior-year returns above +1000% are dropped unless verified. "
        "Sharadar would make this exact."
    )
    return report_id, name, meta, banner


def _missing_warning(missing: list[str]) -> list[str]:
    if not missing:
        return []
    preview = ", ".join(missing[:12])
    suffix = "" if len(missing) <= 12 else ", …"
    return [f"Dropped {len(missing)} constituents with no Yahoo price history: {preview}{suffix}"]
