"""Next-year returns of the S&P's best returners.

For each signal year, rank point-in-time members by that calendar year's
total return, then record what the #1 name and the top five did the
following year. A partial next year (the live year) is shown and kept out
of the crash rates.

Missing next-year returns are reported three ways: dropped, filled with
−100%, and filled with that year's SPY return. A prior-year return above
+1000% is not allowed to win the ranking.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from backtest_lab.engine import EXTREME_ANNUAL_RETURN, prior_year_returns, without_unverified_extremes


def build_event_study(panel: pd.DataFrame, pit, start_year: int, end: date, notes: list[str], spy=None) -> dict:
    rows = []
    last_signal = end.year - 1 if not _year_is_complete(panel, end.year) else end.year
    spy_returns = _spy_calendar_returns(spy)
    # The next year of `last_signal` has to exist as a partial or full year.
    # Ranking year Y needs a session in Y. Stop at the last year that has one
    # and that also has a later session to measure.
    for year in range(start_year, end.year + 1):
        if not _has_year(panel, year) or not _has_year(panel, year - 1):
            continue
        if year >= end.year and not _year_is_complete(panel, year):
            continue
        if not _has_year(panel, year + 1):
            continue
        members = pit.members_on(date(year, 12, 31))
        allowed = members["ticker"].tolist()
        names = members.set_index("ticker")["name"].to_dict()
        signal = prior_year_returns(panel.reindex(columns=allowed), year)
        ranked, extreme = without_unverified_extremes(signal)
        ranked = ranked.sort_values(ascending=False)
        unpriceable_names = sorted(set(allowed) - set(signal.dropna().index))
        nxt = prior_year_returns(panel, year + 1)
        partial = not _year_is_complete(panel, year + 1)
        top = []
        for ticker, ret in ranked.head(5).items():
            next_ret = _maybe(nxt, ticker)
            unverified_next = next_ret is not None and next_ret > EXTREME_ANNUAL_RETURN
            if unverified_next:
                next_ret = None
            removal = pit.removal(ticker)
            modeled = bool(
                removal
                and removal["bankruptcy"]
                and next_ret is not None
                and next_ret <= -0.99
                and pd.Timestamp(removal["date"]).year == year + 1
            )
            top.append(
                {
                    "ticker": ticker,
                    "name": names.get(ticker, ""),
                    "return": float(ret),
                    "next_return": next_ret,
                    "next_unverified": unverified_next,
                    "bankruptcy_modeled": modeled,
                }
            )
        winner = top[0] if top else None
        next_values = [item["next_return"] for item in top if item["next_return"] is not None]
        rows.append(
            {
                "year": year,
                "next_year": year + 1,
                "partial_next_year": partial,
                "members": int(len(allowed)),
                "priced": int(len(signal.dropna())),
                "unpriceable": int(len(unpriceable_names)),
                "unpriceable_names": unpriceable_names,
                "extreme_unverified": extreme,
                "spy_next": spy_returns.get(year + 1),
                "winner": winner,
                "top5": top,
                "top5_equal_weight_next": (sum(next_values) / len(next_values)) if next_values else None,
            }
        )
    summary = summarize(rows)
    changes = pit.changes_per_year()
    return {
        "id": "event-study",
        "kind": "event-study",
        "name": "Best-returner event study",
        "description": (
            "Each year, the #1 and top 5 total returns inside the point-in-time S&P 500 "
            "at that year-end, and the return of those same stocks the next year."
        ),
        "start_year": start_year,
        "end": end.isoformat(),
        "last_signal_year_considered": last_signal,
        "notes": notes,
        "changes_per_year": {str(year): count for year, count in sorted(changes.items())},
        "summary": summary,
        "years": rows,
    }


def summarize(rows: list[dict]) -> dict:
    complete = [row for row in rows if not row["partial_next_year"] and row.get("winner")]
    winners = [row["winner"] for row in complete if row["winner"] and row["winner"]["next_return"] is not None]
    slots = [
        item
        for row in complete
        for item in row["top5"]
        if item["next_return"] is not None
    ]
    missing_winners = [
        row["winner"]["ticker"]
        for row in complete
        if row["winner"] and row["winner"]["next_return"] is None and not row["winner"].get("next_unverified")
    ]
    return {
        "completed_years": len(complete),
        "winner_observed": len(winners),
        "winner_down_30": _count(winners, -0.30),
        "winner_down_50": _count(winners, -0.50),
        "winner_missing_next": missing_winners,
        "top5_observed": len(slots),
        "top5_down_30": _count(slots, -0.30),
        "top5_down_50": _count(slots, -0.50),
        "worst_winners": _worst(winners, 5),
        "worst_top5": _worst(slots, 5),
        "thresholds": {"down": [-0.30, -0.50]},
        "distribution": {
            "note": (
                "Finished next years only. Excluded drops a missing next return. "
                "Worst fills it with -100%. Neutral fills it with that next year's SPY total return. "
                "Hit rate is the share of observations that beat SPY. "
                "A next-year print above +1000% is unverified and is left out of all three."
            ),
            "winner": _scenario_block(complete, "winner"),
            "top5": _scenario_block(complete, "top5"),
        },
    }


def coverage_brackets(coverage: list[dict], cohorts: list[dict], spy: pd.Series, capital: float) -> dict:
    """Hole-weighted bounds around a published book.

    Fraction f of that year's members had no signal price. Worst replaces
    that fraction of the held-year return with -100%. Neutral replaces it
    with SPY over the same window. Excluded is the book as run.
    """
    wealth = {"excluded": float(capital), "worst": float(capital), "neutral": float(capital)}
    rows = []
    series = _positive_series(spy)
    for cohort in cohorts:
        book = cohort.get("period_return")
        if book is None:
            continue
        year = int(cohort["year"])
        match = next((row for row in coverage if int(row["year"]) == year), None)
        members = int(match["members"]) if match else 0
        if match and match.get("bracket_base"):
            base = int(match["bracket_base"])
            missing = int(match.get("bracket_missing") or 0)
            frac = (missing / base) if base else 0.0
        else:
            missing = int(match["unpriceable"]) if match else 0
            frac = (missing / members) if members else 0.0
        spy_ret = _window_return(series, cohort.get("rebalance_date"), cohort.get("period_end"))
        worst = (1.0 - frac) * float(book) + frac * (-1.0)
        neutral_fill = spy_ret if spy_ret is not None else float(book)
        neutral = (1.0 - frac) * float(book) + frac * neutral_fill
        wealth["excluded"] *= 1.0 + float(book)
        wealth["worst"] *= 1.0 + worst
        wealth["neutral"] *= 1.0 + neutral
        rows.append(
            {
                "year": year,
                "members": members,
                "priced": int(match["priced"]) if match else None,
                "coverage": (1.0 - frac) if members else None,
                "book_return": float(book),
                "spy_return": spy_ret,
                "worst_return": worst,
                "neutral_return": neutral,
            }
        )

    def packed(name: str) -> dict:
        final = wealth[name]
        return {
            "total_return": (final / float(capital) - 1.0) if capital else None,
            "final_equity": final,
        }

    return {
        "note": (
            "Hole-weighted bound. Fraction f of members had no usable signal-year return. "
            "Worst replaces that fraction with -100% for the holding year. "
            "Neutral replaces it with SPY's return over the same holding window. "
            "Excluded is the published book, which skips unpriceable names. "
            "This does not mean the missing name was the winner."
        ),
        "excluded": packed("excluded"),
        "worst": packed("worst"),
        "neutral": packed("neutral"),
        "years": rows,
    }


def _scenario_block(rows: list[dict], which: str) -> dict:
    observations = []
    for row in rows:
        items = [row["winner"]] if which == "winner" else list(row.get("top5") or [])
        spy = row.get("spy_next")
        for item in items:
            if not item or item.get("next_unverified"):
                continue
            observations.append((item.get("next_return"), spy))
    return {
        "excluded": _distribution([(value, spy) for value, spy in observations if value is not None]),
        "worst": _distribution([(value if value is not None else -1.0, spy) for value, spy in observations]),
        "neutral": _distribution(
            [
                (value if value is not None else spy, spy)
                for value, spy in observations
                if value is not None or spy is not None
            ]
        ),
    }


def _distribution(pairs: list[tuple]) -> dict:
    values = [float(value) for value, _spy in pairs if value is not None]
    empty = {
        "n": 0,
        "median": None,
        "mean": None,
        "hit_rate_vs_spy": None,
        "pct_below_neg_30": None,
        "pct_below_neg_50": None,
    }
    if not values:
        return empty
    ordered = sorted(values)
    mid = len(ordered) // 2
    median = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0
    comparable = [(float(value), float(spy)) for value, spy in pairs if value is not None and spy is not None]
    hit = None if not comparable else sum(1 for value, spy in comparable if value > spy) / len(comparable)
    n = len(values)
    return {
        "n": n,
        "median": median,
        "mean": sum(values) / n,
        "hit_rate_vs_spy": hit,
        "pct_below_neg_30": sum(1 for value in values if value < -0.30) / n,
        "pct_below_neg_50": sum(1 for value in values if value < -0.50) / n,
    }


def _count(items: list[dict], threshold: float) -> int:
    return sum(1 for item in items if item["next_return"] < threshold)


def _worst(items: list[dict], n: int) -> list[dict]:
    ordered = sorted(items, key=lambda item: item["next_return"])
    return ordered[:n]


def _maybe(scores: pd.Series, ticker: str):
    if ticker not in scores.index:
        return None
    value = scores.loc[ticker]
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if value != value:
        return None
    return value


def _spy_calendar_returns(spy) -> dict[int, float]:
    series = _positive_series(spy)
    if series is None:
        return {}
    frame = series.to_frame("SPY")
    found = {}
    for year in sorted(set(int(stamp.year) for stamp in frame.index)):
        ret = prior_year_returns(frame, year)
        if "SPY" in getattr(ret, "index", []):
            found[year] = float(ret["SPY"])
    return found


def _positive_series(spy):
    if spy is None:
        return None
    series = pd.Series(spy).dropna().astype(float)
    series = series[series > 0]
    if len(series) == 0:
        return None
    series.index = pd.DatetimeIndex(pd.to_datetime(series.index))
    return series[~series.index.duplicated(keep="last")].sort_index()


def _window_return(spy, start, end):
    if spy is None or start is None or end is None:
        return None
    start = pd.Timestamp(start)
    end = pd.Timestamp(end)
    before = spy[spy.index <= start]
    after = spy[spy.index <= end]
    if before.empty or after.empty:
        return None
    opened = float(before.iloc[-1])
    closed = float(after.iloc[-1])
    if opened <= 0:
        return None
    return closed / opened - 1.0


def _has_year(panel: pd.DataFrame, year: int) -> bool:
    return bool(len(panel.index[panel.index.year == year]))


def _year_is_complete(panel: pd.DataFrame, year: int) -> bool:
    days = panel.index[panel.index.year == year]
    if len(days) == 0:
        return False
    return pd.Timestamp(days[-1]).month == 12 and pd.Timestamp(days[-1]).day >= 15
