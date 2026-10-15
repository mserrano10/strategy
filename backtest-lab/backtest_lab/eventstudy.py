"""Next-year returns of the S&P's best returners.

For each signal year, rank point-in-time members by that calendar year's
total return, then record what the #1 name and the top five did the
following year. A partial next year (the live year) is shown and kept out
of the crash rates.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from backtest_lab.engine import prior_year_returns


def build_event_study(panel: pd.DataFrame, pit, start_year: int, end: date, notes: list[str]) -> dict:
    rows = []
    last_signal = end.year - 1 if not _year_is_complete(panel, end.year) else end.year
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
        ranked = signal.dropna().sort_values(ascending=False)
        unpriceable_names = sorted(set(allowed) - set(ranked.index))
        nxt = prior_year_returns(panel, year + 1)
        partial = not _year_is_complete(panel, year + 1)
        top = []
        for ticker, ret in ranked.head(5).items():
            next_ret = _maybe(nxt, ticker)
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
                "priced": int(len(ranked)),
                "unpriceable": int(len(unpriceable_names)),
                "unpriceable_names": unpriceable_names,
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
        if row["winner"] and row["winner"]["next_return"] is None
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


def _has_year(panel: pd.DataFrame, year: int) -> bool:
    return bool(len(panel.index[panel.index.year == year]))


def _year_is_complete(panel: pd.DataFrame, year: int) -> bool:
    days = panel.index[panel.index.year == year]
    if len(days) == 0:
        return False
    return pd.Timestamp(days[-1]).month == 12 and pd.Timestamp(days[-1]).day >= 15
