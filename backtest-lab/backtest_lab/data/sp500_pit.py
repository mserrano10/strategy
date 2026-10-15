"""Point-in-time S&P 500 membership from Wikipedia.

Start from today's constituents on "List of S&P 500 companies" and walk
backward through the selected-changes table. That table now lives on
"Historical components of the S&P 500", which the list page links to.
A change dated after the as-of day is undone: the added ticker comes out,
the removed ticker goes back in. A current constituent is also kept out
until its own "Date added".
"""

from __future__ import annotations

import io
from datetime import date

import pandas as pd

from backtest_lab.data.symbols import yahoo_symbol
from backtest_lab.data.yfinance_source import USER_AGENT, WIKIPEDIA

CHANGES_PAGE = "https://en.wikipedia.org/wiki/Historical_components_of_the_S%26P_500"

BANKRUPTCY_WORDS = ("bankrupt", "chapter 7", "chapter 11", "liquidat")
RENAME_WORDS = ("ticker", "symbol", "renamed", "name change")


def load_point_in_time(refresh: bool = False, cache_dir=None) -> "PointInTimeSP500":
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "universes"
    if cache_dir is not None:
        root = Path(cache_dir)
    current_path = root / "sp500_pit_current.csv"
    changes_path = root / "sp500_changes.csv"
    if not refresh and current_path.exists() and changes_path.exists():
        current = pd.read_csv(current_path, comment="#")
        changes = pd.read_csv(changes_path, comment="#")
        changes["date"] = pd.to_datetime(changes["date"])
        return PointInTimeSP500(current, changes, source="cache")
    current, changes = fetch_wikipedia()
    root.mkdir(parents=True, exist_ok=True)
    stamp = date.today().isoformat()
    current_path.write_text(
        f"# Point-in-time seed: current S&P 500 constituents.\n# fetched_on: {stamp}\n"
        + current.to_csv(index=False)
    )
    changes_path.write_text(
        f"# Selected changes used to walk the S&P 500 backward.\n# fetched_on: {stamp}\n"
        f"# page: {CHANGES_PAGE}\n"
        + changes.to_csv(index=False)
    )
    print(f"saved point-in-time tables → {current_path.parent} ({len(current)} names, {len(changes)} changes)")
    return PointInTimeSP500(current, changes, source="wikipedia")


def fetch_wikipedia() -> tuple[pd.DataFrame, pd.DataFrame]:
    import urllib.request

    current_html = _get(urllib.request, WIKIPEDIA)
    changes_html = _get(urllib.request, CHANGES_PAGE)
    current = _parse_current(pd.read_html(io.StringIO(current_html))[0])
    changes = _parse_changes(_changes_table(pd.read_html(io.StringIO(changes_html))))
    return current, changes


class PointInTimeSP500:
    def __init__(self, current: pd.DataFrame, changes: pd.DataFrame, source: str = "wikipedia"):
        self.source = source
        self.current = _normalize_current(current)
        self.changes = _normalize_changes(changes)
        self.names = _name_map(self.current, self.changes)
        self.sectors = (
            self.current.set_index("ticker")["sector"].to_dict() if "sector" in self.current.columns else {}
        )
        self._removals = _removal_index(self.changes)
        self._renames = _rename_index(self.changes)

    def members_on(self, asof) -> pd.DataFrame:
        asof = pd.Timestamp(asof)
        members = set()
        for row in self.current.itertuples(index=False):
            added = row.date_added
            if added is None or pd.isna(added) or pd.Timestamp(added) <= asof:
                members.add(row.ticker)
        # Newest first. A name that is added and later removed has to be
        # put back by the removal and then taken out again by the addition.
        # Oldest-first would leave it in the set.
        later = self.changes[self.changes["date"] > asof].sort_values("date", ascending=False)
        for row in later.itertuples(index=False):
            added = _ticker(row.added)
            removed = _ticker(row.removed)
            if added:
                members.discard(added)
            if removed:
                members.add(removed)
        rows = []
        for ticker in sorted(members):
            rows.append(
                {
                    "ticker": ticker,
                    "name": self.names.get(ticker, ""),
                    "sector": self.sectors.get(ticker, ""),
                }
            )
        return pd.DataFrame(rows, columns=["ticker", "name", "sector"])

    def removal(self, ticker: str) -> dict | None:
        return self._removals.get(ticker)

    def renames(self) -> list[dict]:
        return list(self._renames)

    def removals(self) -> dict[str, dict]:
        return self._removals

    def tickers_between(self, first_year: int, last_year: int) -> list[str]:
        found = set()
        for year in range(first_year, last_year + 1):
            found.update(self.members_on(f"{year}-12-31")["ticker"].tolist())
        return sorted(found)

    def changes_per_year(self) -> dict[int, int]:
        counts = self.changes["date"].dt.year.value_counts().to_dict()
        return {int(year): int(count) for year, count in counts.items()}

    def spells(self) -> dict[str, list[tuple[pd.Timestamp, pd.Timestamp | None]]]:
        """Membership intervals. The end is exclusive, or None while still a member.

        Intervals follow `members_on`, so a ticker that left and later came back
        (McKesson, or a reused symbol) has two spells. Prices outside a spell
        are not that spell's company.
        """
        cached = getattr(self, "_spells", None)
        if cached is not None:
            return cached
        bounds = {pd.Timestamp("1950-01-01"), pd.Timestamp("2099-12-31")}
        bounds.update(pd.Timestamp(value) for value in self.changes["date"])
        if "date_added" in self.current.columns:
            bounds.update(pd.Timestamp(value) for value in self.current["date_added"].dropna())
        dates = sorted(bounds)
        snapshots = [(day, set(self.members_on(day)["ticker"])) for day in dates]
        found: dict[str, list[tuple[pd.Timestamp, pd.Timestamp | None]]] = {}
        open_start: dict[str, pd.Timestamp] = {}
        seen: set[str] = set()
        for day, members in snapshots:
            gone = seen - members
            for ticker in gone:
                start = open_start.pop(ticker)
                found.setdefault(ticker, []).append((start, day))
            for ticker in members - seen:
                open_start[ticker] = day
            seen = members
        for ticker, start in open_start.items():
            found.setdefault(ticker, []).append((start, None))
        self._spells = found
        return found


def is_bankruptcy(reason: str | None) -> bool:
    text = (reason or "").lower()
    return any(word in text for word in BANKRUPTCY_WORDS)


def is_rename(reason: str | None) -> bool:
    text = (reason or "").lower()
    return any(word in text for word in RENAME_WORDS)


def _get(urllib_request, url: str) -> str:
    request = urllib_request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib_request.urlopen(request, timeout=60) as response:
        return response.read().decode("utf-8", errors="replace")


def _changes_table(tables: list[pd.DataFrame]) -> pd.DataFrame:
    for table in tables:
        flat = [" ".join(str(part) for part in col).lower() if isinstance(col, tuple) else str(col).lower() for col in table.columns]
        if any("effective" in col or col == "date" for col in flat) and any("ticker" in col or "added" in col for col in flat):
            return table
    raise RuntimeError("Wikipedia changes page has no selected-changes table")


def _parse_current(table: pd.DataFrame) -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            "ticker": table["Symbol"].map(yahoo_symbol),
            "name": table["Security"].astype(str),
            "sector": table["GICS Sector"].astype(str) if "GICS Sector" in table.columns else "",
            "date_added": pd.to_datetime(table["Date added"], errors="coerce") if "Date added" in table.columns else pd.NaT,
        }
    )
    frame = frame.dropna(subset=["ticker"]).drop_duplicates("ticker")
    if len(frame) < 400:
        raise RuntimeError(f"current S&P list looks short ({len(frame)} names)")
    return frame.reset_index(drop=True)


def _parse_changes(table: pd.DataFrame) -> pd.DataFrame:
    if isinstance(table.columns, pd.MultiIndex):
        table = table.copy()
        table.columns = ["date", "added", "added_name", "removed", "removed_name", "reason", "refs"][: len(table.columns)]
    else:
        lookup = {str(col).strip().lower(): col for col in table.columns}
        # Single-level headers vary. Require the columns the multiindex parse uses.
        rename = {}
        for key, target in (("date", "date"), ("effective date", "date")):
            if key in lookup:
                rename[lookup[key]] = target
        table = table.rename(columns=rename)
    expected = ["date", "added", "added_name", "removed", "removed_name", "reason"]
    if "date" not in table.columns:
        raise RuntimeError(f"could not read S&P change dates from columns {list(table.columns)}")
    for column in expected:
        if column not in table.columns:
            table[column] = None
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(table["date"], errors="coerce"),
            "added": table["added"].map(yahoo_symbol),
            "added_name": table["added_name"].astype(str),
            "removed": table["removed"].map(yahoo_symbol),
            "removed_name": table["removed_name"].astype(str),
            "reason": table["reason"].astype(str),
        }
    )
    frame = frame.dropna(subset=["date"])
    frame = frame[frame["added"].notna() | frame["removed"].notna()]
    return frame.sort_values("date").reset_index(drop=True)


def _normalize_current(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["ticker"] = out["ticker"].map(yahoo_symbol)
    out["date_added"] = pd.to_datetime(out["date_added"], errors="coerce")
    out = out.dropna(subset=["ticker"]).drop_duplicates("ticker")
    return out.reset_index(drop=True)


def _normalize_changes(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["date"] = pd.to_datetime(out["date"])
    out["added"] = out["added"].map(yahoo_symbol) if "added" in out.columns else None
    out["removed"] = out["removed"].map(yahoo_symbol) if "removed" in out.columns else None
    if "reason" not in out.columns:
        out["reason"] = ""
    out["reason"] = out["reason"].fillna("").astype(str).replace({"nan": ""})
    return out.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)


def _name_map(current: pd.DataFrame, changes: pd.DataFrame) -> dict[str, str]:
    names = {}
    for row in changes.itertuples(index=False):
        removed = _ticker(row.removed)
        added = _ticker(row.added)
        if removed and _clean_name(row.removed_name):
            names[removed] = _clean_name(row.removed_name)
        if added and _clean_name(row.added_name):
            names[added] = _clean_name(row.added_name)
    for row in current.itertuples(index=False):
        if _clean_name(row.name):
            names[row.ticker] = _clean_name(row.name)
    return names


def _ticker(value) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or text.lower() == "nan":
        return None
    return text


def _clean_name(value) -> str:
    text = "" if value is None or (isinstance(value, float)) else str(value)
    if text.lower() in {"", "nan", "none"}:
        return ""
    return text.strip()


def _removal_index(changes: pd.DataFrame) -> dict[str, dict]:
    """Last removal of each ticker, unless a later row adds that ticker again.

    A later add is a new spell (or the current listing). Keeping the old
    removal would clip the new company's prices. Membership spells still
    close at the old removal date.
    """
    found: dict[str, dict] = {}
    for row in changes.itertuples(index=False):
        removed = _ticker(row.removed)
        if not removed:
            continue
        found[removed] = {
            "date": pd.Timestamp(row.date),
            "reason": row.reason,
            "bankruptcy": is_bankruptcy(row.reason),
            "name": _clean_name(row.removed_name),
        }
    # If the ticker was added again after that removal, it is a current
    # identity (or a later spell). Drop the stale removal so we don't clip
    # the new listing's prices.
    for row in changes.itertuples(index=False):
        added = _ticker(row.added)
        if not added or added not in found:
            continue
        if pd.Timestamp(row.date) > found[added]["date"]:
            del found[added]
    return found


def _rename_index(changes: pd.DataFrame) -> list[dict]:
    rows = []
    for row in changes.itertuples(index=False):
        old = _ticker(row.removed)
        new = _ticker(row.added)
        if old and new and is_rename(row.reason):
            rows.append(
                {
                    "date": pd.Timestamp(row.date),
                    "old": old,
                    "new": new,
                    "reason": row.reason,
                }
            )
    return rows
