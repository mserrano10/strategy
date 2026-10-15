"""Forward test from the Oct 15, 2026 close.

The annual books are the 2026 cohorts already published in ``results/``.
Top 5 Biggest is the exception: the forward sleeve ranks companies, so
Alphabet is one line. This module does not rerun those backtests and does
not rewrite their files. A daily job marks the frozen weights, and it keeps
stepping the two-bullet rules.
"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from backtest_lab.metrics import compute_metrics

LABEL = "Forward test, live since Oct 15, 2026"
INCEPTION = date(2026, 10, 15)
CAPITAL = 10_000.0
CLOSE_HOUR_UTC = 21
PICKS_VERSION = 2

# Current-list cap books and their point-in-time twins. Included once when
# the 2026 holdings match. A mismatch is a hard error so a new name is not
# dropped on the floor.
SHARED_CAP_BOOKS = (
    ("biggest", "biggest-pit"),
    ("top5-biggest", "top5-biggest-pit"),
)

# Report id, in page order. TQQQ buy-and-hold is intentionally absent.
BOOK_IDS = (
    "best-returner",
    "best-returner-pit",
    "best-returner-10b",
    "best-returner-2b",
    "top5-returners",
    "top5-returners-pit",
    "top5-returners-10b",
    "top5-returners-2b",
    "biggest",
    "top5-biggest",
    "tqqq-two-bullet",
)

ENTRY_RULE = (
    "For the stock books, entry is the adjusted close on the first completed US "
    "cash session on or after 2026-10-15. A same-day bar is not that close until "
    "21:00 UTC, which is after both the 20:00 UTC (EDT) and 21:00 UTC (EST) cash "
    "closes. The entry price is written once and is not revised if Yahoo later "
    "restates the historical print. Later marks divide that session's adjusted "
    "close by the frozen entry. The two-bullet book does not use those ratios. "
    "It carries the engine state from the Oct 14, 2026 close and applies the "
    "live rules on each forward session. Its chart is rebased so that session "
    "equals the starting capital."
)

AMBIGUITIES = [
    (
        "The forward test starts at the Oct 15, 2026 close. A same-day Yahoo bar "
        "before 21:00 UTC is an intraday print and is not the entry. Until that "
        "close exists, entry prices stay blank and the books have no return."
    ),
    (
        "Annual books use the 2026 cohort chosen at the 2025-12-31 close. They "
        "are not re-ranked here. The backtest path from that rebalance through "
        "the last session before inception is not part of the forward return."
    ),
    (
        "Point-in-time Best Returner and Top 5 Returners are their own sleeves, "
        "because their 2026 names differ from the current-list books. Biggest and "
        "Top 5 Biggest hold the same names in both universes on this cohort, so "
        "each cap rule has one sleeve."
    ),
    (
        "TQQQ buy-and-hold is not in the forward test. The TQQQ book is the "
        "two-bullet sleeve."
    ),
    (
        "The two-bullet forward book keeps the live rules. The engine state is "
        "taken at the Oct 14, 2026 close: SPY shares, both bullet cash balances, "
        "and the unlocked high-water mark. Each new session can arm a bullet at "
        "a 50% or 80% drawdown, buy 50/25/25 at the trigger and about 30 and 60 "
        "days later, or sell at 10× the lowest close since the trigger."
    ),
    (
        "Top 5 Biggest ranks companies. Alphabet counts once, as GOOGL, the larger "
        "share class at the 2025 close. GOOG does not take a second slot, so the "
        "fifth company is AMZN. The published backtest still lists both classes. "
        "The current list and the point-in-time list agree on this forward cohort."
    ),
]


def lab_root() -> Path:
    return Path(__file__).resolve().parents[1]


def picks_path() -> Path:
    return lab_root() / "forward" / "picks.json"


def published_path() -> Path:
    return lab_root() / "results" / "forward-test.json"


def results_dir() -> Path:
    return lab_root() / "results"


def utc_now(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def session_complete(session: date, now: datetime) -> bool:
    """True once `session` is a finished weekday cash session.

    Matches ``calendarutil.required_through``: the close counts from 21:00 UTC.
    """
    now = utc_now(now)
    if session.weekday() >= 5:
        return False
    if session < now.date():
        return True
    return session == now.date() and now.hour >= CLOSE_HOUR_UTC


def close_is_due(inception: date, now: datetime) -> bool:
    """The inception close can exist only after that session has finished."""
    now = utc_now(now)
    if now.date() > inception:
        return True
    return session_complete(inception, now)


def build_picks(reports: Path | None = None) -> dict:
    """Freeze the current-year cohorts. Entry prices are left blank."""
    reports = reports or results_dir()
    loaded = {book_id: _read_report(reports, book_id) for book_id in _required_reports()}
    _require_shared_cap_books(loaded)
    books = [_book_from_report(loaded[book_id], loaded) for book_id in BOOK_IDS]
    return {
        "picks_version": PICKS_VERSION,
        "label": LABEL,
        "inception": INCEPTION.isoformat(),
        "initial_capital": CAPITAL,
        "entry_session": None,
        "spy_entry_price": None,
        "entry_rule": ENTRY_RULE,
        "ambiguities": list(AMBIGUITIES),
        "books": books,
    }


def marked_equity(
    holdings: list[dict],
    prices: dict[str, float],
    entries: dict[str, float],
    capital: float,
    *,
    is_entry: bool,
) -> float:
    """Dollar value of a frozen weight vector.

    The entry session is exactly `capital`. A zero weight contributes nothing,
    so an uninvested TQQQ sleeve does not need a price.
    """
    if is_entry:
        return float(capital)
    level = 0.0
    for holding in holdings:
        weight = float(holding["weight"])
        if weight == 0.0:
            continue
        ticker = holding["ticker"]
        level += weight * (float(prices[ticker]) / float(entries[ticker]))
    return float(capital) * level


def entry_session_date(panel: pd.DataFrame, inception: date, now: datetime) -> date | None:
    """First completed session on or after inception that the panel actually has."""
    if panel is None or panel.empty:
        return None
    for stamp in pd.DatetimeIndex(panel.index).sort_values():
        session = pd.Timestamp(stamp).date()
        if session < inception:
            continue
        if session_complete(session, now):
            return session
    return None


def mark_curve(
    holdings: list[dict],
    panel: pd.DataFrame,
    entries: dict[str, float],
    entry_on: date,
    capital: float,
    now: datetime,
) -> list[dict]:
    """Daily equity from the entry session through the last completed session."""
    needed = _live_tickers(holdings)
    last: dict[str, float] = {}
    rows: list[dict] = []
    for stamp in pd.DatetimeIndex(panel.index).sort_values():
        session = pd.Timestamp(stamp).date()
        if session < entry_on or not session_complete(session, now):
            continue
        row = panel.loc[stamp]
        if isinstance(row, pd.DataFrame):
            row = row.iloc[-1]
        for ticker in needed:
            value = row[ticker] if ticker in row.index else None
            if value is not None and not pd.isna(value):
                last[ticker] = float(value)
        if any(ticker not in last for ticker in needed):
            continue
        equity = marked_equity(
            holdings,
            last,
            entries,
            capital,
            is_entry=session == entry_on,
        )
        rows.append({"date": session.isoformat(), "equity": equity})
    return rows


def mark_to_market(
    *,
    now: datetime | None = None,
    prices: pd.DataFrame | None = None,
    tqqq: dict | None = None,
    picks_file: Path | None = None,
    published_file: Path | None = None,
    reports: Path | None = None,
    refresh: bool = False,
    write: bool = True,
) -> dict:
    """Update entry prices once, then rewrite the published marks.

    Holdings, weights, and derivation are never replaced once ``picks_file``
    exists. Pass ``prices`` in tests to avoid the network.
    """
    now = utc_now(now)
    picks_file = picks_file or picks_path()
    published_file = published_file or published_path()
    picks = _load_or_build(picks_file, reports)
    inception = date.fromisoformat(picks["inception"])

    if picks.get("entry_session"):
        panel, tqqq = _panel_and_tape(picks, inception, now, refresh, prices, tqqq)
        published = _publish_live(picks, panel, now, tqqq)
    elif not close_is_due(inception, now):
        published = _publish_waiting(
            picks,
            "awaiting-close",
            "Entry prices lock at the Oct 15, 2026 close. That session is not complete yet, so these books have no return.",
        )
    else:
        panel, tqqq = _panel_and_tape(picks, inception, now, refresh, prices, tqqq)
        session = entry_session_date(panel, inception, now)
        if session is None or not _row_covers(panel, session, _tickers_to_price(picks)):
            published = _publish_waiting(
                picks,
                "awaiting-prices",
                "The Oct 15, 2026 session is complete, and a full set of entry prints is not in yet.",
            )
        else:
            picks = _lock_entries(picks, panel, session)
            published = _publish_live(picks, panel, now, tqqq)

    if write:
        _write_json(picks_file, picks)
        _write_json(published_file, published)
    return published


def main_mark(refresh: bool = False) -> int:
    published = mark_to_market(refresh=refresh)
    as_of = published.get("as_of") or "—"
    print(f"{published['label']}: {published['status']} through {as_of}")
    return 0


def _required_reports() -> list[str]:
    ids = list(BOOK_IDS)
    for _primary, twin in SHARED_CAP_BOOKS:
        if twin not in ids:
            ids.append(twin)
    return ids


def _read_report(reports: Path, book_id: str) -> dict:
    path = reports / f"{book_id}.json"
    payload = json.loads(path.read_text())
    if payload.get("id") != book_id:
        raise ValueError(f"{path} id is {payload.get('id')}, expected {book_id}")
    return payload


def _cohort_2026(report: dict) -> dict:
    matches = [row for row in report.get("cohorts") or [] if row.get("year") == 2026]
    if len(matches) != 1:
        raise ValueError(f"{report.get('id')} has {len(matches)} cohorts for 2026")
    return matches[0]


def _require_shared_cap_books(loaded: dict[str, dict]) -> None:
    for primary, twin in SHARED_CAP_BOOKS:
        left = _identity(_cohort_2026(loaded[primary])["holdings"])
        right = _identity(_cohort_2026(loaded[twin])["holdings"])
        if left != right:
            raise ValueError(
                f"{primary} and {twin} 2026 holdings differ ({left} vs {right}); "
                "the forward test would need a separate sleeve"
            )


def _identity(holdings: list[dict]) -> list[tuple]:
    return [
        (row["ticker"], round(float(row["weight"]), 10), round(float(row.get("market_cap") or 0), 2))
        for row in holdings
    ]


def _book_from_report(report: dict, loaded: dict[str, dict]) -> dict:
    cohort = _cohort_2026(report)
    book_id = report["id"]
    if book_id == "tqqq-two-bullet":
        return _tqqq_book()
    if book_id == "top5-biggest":
        return _top5_company_book(report, loaded)
    else:
        holdings = [_rank_holding(row) for row in cohort["holdings"]]
        derivation = _rank_derivation(report, cohort)
        signal_date = cohort["rebalance_date"]
        also = _shared_note(book_id, loaded)
        if also:
            derivation = derivation + " " + also
    _require_weights(book_id, holdings)
    book = {
        "id": book_id,
        "name": report["name"],
        "cohort_year": 2026,
        "signal_date": signal_date,
        "derivation": derivation,
        "holdings": holdings,
    }
    if also:
        book["shared_sleeve"] = also
    return book


def company_key(name: str) -> str:
    """Share classes of one company share the name before the class label."""
    text = name or ""
    for marker in (" (Class ", " Class "):
        if marker in text:
            text = text.split(marker)[0]
    return text.strip() or name


def select_companies(rows: list[dict], n: int) -> list[dict]:
    """Largest companies, one line each. The larger share class is the line kept."""
    chosen: list[dict] = []
    seen: set[str] = set()
    ordered = sorted(rows, key=lambda row: (-float(row["market_cap"]), row["ticker"]))
    for row in ordered:
        key = company_key(row.get("name") or row["ticker"])
        if key in seen:
            continue
        seen.add(key)
        chosen.append(row)
        if len(chosen) == n:
            break
    return chosen


def _top5_company_book(report: dict, loaded: dict[str, dict]) -> dict:
    rank = json.loads((lab_root() / "forward" / "cap-rank-2025.json").read_text())
    current = select_companies(rank["current"], 5)
    pit = select_companies(rank["pit"], 5)
    if [row["ticker"] for row in current] != [row["ticker"] for row in pit]:
        raise ValueError("current and point-in-time company ranks differ; Top 5 Biggest needs two sleeves")
    holdings = []
    for row in current:
        holdings.append(
            {
                "ticker": row["ticker"],
                "name": row["name"],
                "weight": 0.2,
                "prior_year_return": float(row["prior_year_return"]),
                "market_cap": float(row["market_cap"]),
                "entry_price": None,
                "price_field": "adjusted_close",
            }
        )
    _require_weights(report["id"], holdings)
    names = ", ".join(row["ticker"] for row in holdings)
    derivation = (
        f"2026 cohort of {report['name']}, chosen at the {rank['signal_date']} close, ranked by company. "
        "Signal: approximated market cap at the prior year-end close. "
        "Universe: today's S&P 500. The point-in-time membership produces the same five companies, so the books share this sleeve. "
        "Share classes of one company take one slot, and the slot is the larger class at that close. "
        "Alphabet is held as GOOGL. "
        f"The five companies are equal weight: {names}. "
        "The published backtest still counts GOOG and GOOGL as two holdings. This forward book holds one Alphabet line. "
        "The position is held until the next year-end close and is not re-ranked in this test. "
        "The forward return starts at the Oct 15, 2026 close, so the backtest's 2026 path is not included."
    )
    note = _shared_note("top5-biggest", loaded)
    return {
        "id": report["id"],
        "name": report["name"],
        "cohort_year": 2026,
        "signal_date": rank["signal_date"],
        "derivation": derivation,
        "holdings": holdings,
        "shared_sleeve": note,
    }


def _tqqq_book() -> dict:
    state = json.loads((lab_root() / "forward" / "tqqq-handoff.json").read_text())
    holdings = _handoff_holdings(state)
    _require_weights("tqqq-two-bullet", holdings)
    ath = float(state["ath"])
    derivation = (
        "Live two-bullet book. The engine is handed off at the "
        f"{state['as_of']} close and then follows the rules from the Oct 15, 2026 close. "
        f"The high-water mark is {ath:.4f} and is unlocked, because the cycle is flat after the "
        "May 26, 2026 limit exit (both bullets sold at 80.80, ten times a floor of 8.08). "
        f"Bullet 1 buys when the split-adjusted TQQQ close is at or below {ath * 0.5:.4f} "
        "(50% below that mark). Bullet 2 buys at or below "
        f"{ath * 0.2:.4f} (80% below the same locked mark). "
        "Each bullet spends 50% of the cash it holds at the trigger, then 25% on the first "
        "session on or after 30 calendar days and 25% after 60. "
        "A limit sells every TQQQ share at 10× the lowest close since the trigger. "
        "The SPY core is never rebalanced against the satellite. "
        "A drawdown after inception is taken. TQQQ is marked on the split-adjusted close. "
        "SPY and BIL use adjusted closes."
    )
    return {
        "id": "tqqq-two-bullet",
        "name": "TQQQ Two-Bullet 90/10",
        "cohort_year": 2026,
        "signal_date": state["as_of"],
        "engine": "tqqq-two-bullet",
        "derivation": derivation,
        "handoff": state,
        "rule_state": _rule_view(state),
        "holdings": holdings,
    }


def _handoff_holdings(state: dict) -> list[dict]:
    equity = float(state["equity"])
    bullet1 = state["bullet1"]
    bullet2 = state["bullet2"]
    spy_value = equity - float(bullet1["cash"]) - float(bullet2["cash"])
    if float(bullet1["shares"]) or float(bullet2["shares"]):
        raise ValueError("the Oct 14 handoff still holds TQQQ; the sleeve split needs a TQQQ price")
    rows = [
        ("SPY", "SPDR S&P 500 ETF Trust", "core", spy_value, "adjusted_close"),
        ("TQQQ", "Bullet 1 ProShares UltraPro QQQ", "bullet-1", 0.0, "split_adjusted_close"),
        ("TQQQ", "Bullet 2 ProShares UltraPro QQQ", "bullet-2", 0.0, "split_adjusted_close"),
        ("BIL", "Bullet 1 dry powder (Treasury-bill proxy)", "bullet-1", float(bullet1["cash"]), "adjusted_close"),
        ("BIL", "Bullet 2 dry powder (Treasury-bill proxy)", "bullet-2", float(bullet2["cash"]), "adjusted_close"),
    ]
    holdings = []
    for ticker, name, sleeve, value, field in rows:
        holdings.append(
            {
                "ticker": ticker,
                "name": name,
                "sleeve": sleeve,
                "weight": 0.0 if equity == 0 else value / equity,
                "entry_price": None,
                "price_field": field,
            }
        )
    return holdings


def _rule_view(state: dict | None) -> dict | None:
    if not state:
        return None
    return {
        "as_of": state.get("as_of"),
        "high_water_mark": state.get("ath"),
        "locked_high_water_mark": state.get("locked_ath"),
        "cycle": state.get("cycle"),
        "floor": state.get("floor"),
    }


def _rank_holding(row: dict) -> dict:
    holding = {
        "ticker": row["ticker"],
        "name": row["name"],
        "weight": float(row["weight"]),
        "prior_year_return": float(row["prior_year_return"]),
        "market_cap": float(row["market_cap"]),
        "entry_price": None,
        "price_field": "adjusted_close",
    }
    return holding


def _tqqq_holding(row: dict) -> dict:
    ticker = row["ticker"]
    return {
        "ticker": ticker,
        "name": row["name"],
        "sleeve": _sleeve_name(row["name"], ticker),
        "weight": float(row["weight"]),
        "entry_price": None,
        "price_field": "split_adjusted_close" if ticker == "TQQQ" else "adjusted_close",
    }


def _sleeve_name(name: str, ticker: str) -> str:
    if ticker == "SPY":
        return "core"
    if "Bullet 1" in name:
        return "bullet-1"
    if "Bullet 2" in name:
        return "bullet-2"
    return "position"


def _require_weights(book_id: str, holdings: list[dict]) -> None:
    total = sum(float(row["weight"]) for row in holdings)
    if abs(total - 1.0) > 1e-9:
        raise ValueError(f"{book_id} weights sum to {total}, not 1")


def _rank_derivation(report: dict, cohort: dict) -> str:
    picks = "; ".join(_pick_phrase(row) for row in cohort["holdings"])
    weight = "The book is a single name." if len(cohort["holdings"]) == 1 else "The five names are equal weight."
    return (
        f"2026 cohort of {report['name']}, chosen at the {cohort['rebalance_date']} close. "
        f"Signal: {report['signal']}. {_universe_sentence(report, cohort)} "
        f"{weight} The position is held until the next year-end close and is not re-ranked in this test. "
        f"Picks: {picks}. "
        "The forward return starts at the Oct 15, 2026 close, so the backtest's 2026 path is not included."
    )


def _pick_phrase(row: dict) -> str:
    prior = float(row["prior_year_return"]) * 100.0
    return (
        f"{row['ticker']} ({row['name']}, 2025 total return {prior:+.1f}%, "
        f"cap {_format_cap(float(row['market_cap']))} at the signal close)"
    )


def _format_cap(value: float) -> str:
    if value >= 1e12:
        return f"${value / 1e12:.3f}T"
    if value >= 1e9:
        return f"${value / 1e9:.2f}B"
    return f"${value / 1e6:.1f}M"


def _universe_sentence(report: dict, cohort: dict) -> str:
    universe = report["universe"]
    eligible = cohort.get("eligible")
    size = cohort.get("universe_size")
    counts = f"{eligible} names were eligible out of {size}."
    if universe.get("id") == "us-listed":
        floor = float(universe["min_market_cap"]) / 1e9
        return (
            f"Universe: current NASDAQ, NYSE, and NYSE American common stocks with approximated "
            f"market cap of at least ${floor:.0f}B at the 2025 year-end. {counts} "
            "Delisted names are absent. A listing whose split-adjusted history peaks above $20,000 "
            "while the latest print is under $10,000 is dropped. A prior-year return above +1000% "
            "is dropped unless a second vendor confirms it. A cap above $15 trillion is ignored."
        )
    if universe.get("point_in_time"):
        return (
            "Universe: the fja05680 point-in-time S&P 500 membership at the 2025 year-end, "
            f"not today's constituent list. {counts}"
        )
    return (
        "Universe: today's S&P 500, applied to the 2025 signal year. "
        f"{counts} That list is survivorship-biased."
    )


def _shared_note(book_id: str, loaded: dict[str, dict]) -> str | None:
    for primary, twin in SHARED_CAP_BOOKS:
        if book_id == primary:
            twin_name = loaded[twin]["name"]
            return (
                f"{twin_name} holds the same names on this cohort, so the two books share this sleeve."
            )
    return None


def _load_or_build(path: Path, reports: Path | None) -> dict:
    if path.exists():
        payload = json.loads(path.read_text())
        version = payload.get("picks_version")
        # Files written by tests omit the version. A stale forward file carries
        # an older version and is rebuilt. Entry prices are still blank until
        # the Oct 15 close, so a rebuild does not throw away a lock.
        if version is None or version == PICKS_VERSION:
            return payload
    return build_picks(reports)


def _tickers_to_price(picks: dict) -> list[str]:
    found = ["SPY"]
    for book in picks["books"]:
        for holding in book["holdings"]:
            if float(holding["weight"]) > 0 and holding["ticker"] not in found:
                found.append(holding["ticker"])
    return found


def _live_tickers(holdings: list[dict]) -> list[str]:
    return list(dict.fromkeys(row["ticker"] for row in holdings if float(row["weight"]) > 0))


def _panel_and_tape(picks, inception, now, refresh, prices, tqqq):
    if prices is not None:
        return prices, tqqq
    history = _fetch_history(picks, inception, now, refresh)
    panel = history.adjusted_close
    if panel is None or panel.empty:
        panel = pd.DataFrame()
    else:
        panel = panel.sort_index()
        panel = panel[~panel.index.duplicated(keep="last")]
    tape = {
        "close": history.close,
        "high": history.high,
        "open": history.open,
        "adjusted_close": panel,
    }
    return panel, tape


def _fetch_history(picks: dict, inception: date, now: datetime, refresh: bool):
    from datetime import timedelta

    from backtest_lab.data.yfinance_source import YFinanceSource

    source = YFinanceSource(refresh=refresh)
    start_date = inception - timedelta(days=10)
    return source.history(_symbols_to_fetch(picks), start_date, now.date())


def _symbols_to_fetch(picks: dict) -> list[str]:
    symbols = _tickers_to_price(picks)
    if any(book.get("engine") == "tqqq-two-bullet" for book in picks["books"]):
        for symbol in ("TQQQ", "SPY", "BIL"):
            if symbol not in symbols:
                symbols.append(symbol)
    return symbols


def _row_covers(panel: pd.DataFrame, session: date, tickers: list[str]) -> bool:
    if panel is None or panel.empty:
        return False
    stamps = [stamp for stamp in panel.index if pd.Timestamp(stamp).date() == session]
    if not stamps:
        return False
    row = panel.loc[stamps[-1]]
    if isinstance(row, pd.DataFrame):
        row = row.iloc[-1]
    for ticker in tickers:
        if ticker not in row.index or pd.isna(row[ticker]):
            return False
    return True


def _lock_entries(picks: dict, panel: pd.DataFrame, session: date) -> dict:
    """Write entry prices once. A later call with a different print does not move them."""
    if picks.get("entry_session"):
        return picks
    locked = deepcopy(picks)
    stamps = [stamp for stamp in panel.index if pd.Timestamp(stamp).date() == session]
    row = panel.loc[stamps[-1]]
    if isinstance(row, pd.DataFrame):
        row = row.iloc[-1]
    prices = {ticker: float(row[ticker]) for ticker in _tickers_to_price(locked)}
    locked["entry_session"] = session.isoformat()
    locked["spy_entry_price"] = prices["SPY"]
    for book in locked["books"]:
        for holding in book["holdings"]:
            if float(holding["weight"]) > 0:
                holding["entry_price"] = prices[holding["ticker"]]
    return locked


def _entries_from_picks(picks: dict) -> dict[str, float]:
    entries = {"SPY": float(picks["spy_entry_price"])}
    for book in picks["books"]:
        for holding in book["holdings"]:
            if holding.get("entry_price") is not None:
                entries[holding["ticker"]] = float(holding["entry_price"])
    return entries


def _publish_live(picks: dict, panel: pd.DataFrame, now: datetime, tqqq: dict | None = None) -> dict:
    entry_on = date.fromisoformat(picks["entry_session"])
    entries = _entries_from_picks(picks)
    capital = float(picks["initial_capital"])
    spy_holdings = [{"ticker": "SPY", "weight": 1.0}]
    spy_curve = mark_curve(spy_holdings, panel, entries, entry_on, capital, now)
    books = []
    for book in picks["books"]:
        item = deepcopy(book)
        if book.get("engine") == "tqqq-two-bullet":
            curve, holdings, events, rule_state = mark_two_bullet(book, tqqq, entry_on, now, capital)
            item["equity"] = curve
            item["events"] = events
            item["rule_state"] = rule_state
            if holdings:
                item["holdings"] = holdings
        else:
            item["equity"] = mark_curve(book["holdings"], panel, entries, entry_on, capital, now)
        item["metrics"] = _metrics(item["equity"])
        books.append(item)
    as_of = spy_curve[-1]["date"] if spy_curve else None
    return _payload(
        picks,
        books,
        {
            "name": "S&P 500 (SPY)",
            "ticker": "SPY",
            "weight": 1.0,
            "entry_price": picks.get("spy_entry_price"),
            "price_field": "adjusted_close",
            "equity": spy_curve,
            "metrics": _metrics(spy_curve),
        },
        "live" if spy_curve else "awaiting-prices",
        (
            f"Marked through {as_of}. Entry session {picks['entry_session']}. "
            f"Each book and SPY start at ${capital:,.0f}."
            if as_of
            else "Entry prices are locked, and a completed mark is not in yet."
        ),
        as_of,
    )


def mark_two_bullet(book: dict, tape: dict | None, entry_on: date, now: datetime, capital: float):
    """Step the handed-off two-bullet engine through completed sessions and rebase to `capital`."""
    from backtest_lab.strategies.tqqq import resume_two_bullet

    handoff = book["handoff"]
    view = _rule_view(handoff)
    if not tape:
        return [], [], [], view
    close = _symbol_series(tape.get("close"), "TQQQ")
    high = _symbol_series(tape.get("high"), "TQQQ")
    opened = _symbol_series(tape.get("open"), "TQQQ")
    spy = _symbol_series(tape.get("adjusted_close"), "SPY")
    bil = _symbol_series(tape.get("adjusted_close"), "BIL")
    close = _completed_sessions(close, now)
    high = _completed_sessions(high, now)
    opened = _completed_sessions(opened, now)
    spy = _completed_sessions(spy, now)
    bil = _completed_sessions(bil, now)
    if close.empty:
        return [], [], [], view
    resumed = resume_two_bullet(handoff, spy, bil, close, high, opened, cost_bps=0.0)
    points = []
    for stamp, equity in resumed["equity"].items():
        session = pd.Timestamp(stamp).date()
        if session < entry_on:
            continue
        points.append((session, float(equity)))
    if not points:
        return [], [], [], view
    scale = capital / points[0][1]
    curve = [{"date": session.isoformat(), "equity": equity * scale} for session, equity in points]
    holdings = [_snapshot_holding(row) for row in resumed["holdings"]]
    events = [event for event in resumed["events"] if event["date"] >= entry_on.isoformat()]
    return curve, holdings, events, _rule_view(resumed["state"])


def _symbol_series(panel, symbol: str) -> pd.Series:
    if panel is None or len(getattr(panel, "index", [])) == 0 or symbol not in getattr(panel, "columns", []):
        return pd.Series(dtype=float)
    series = panel[symbol].dropna().astype(float).sort_index()
    return series[~series.index.duplicated(keep="last")]


def _completed_sessions(series: pd.Series, now: datetime) -> pd.Series:
    if series is None or series.empty:
        return pd.Series(dtype=float)
    keep = [stamp for stamp in series.index if session_complete(pd.Timestamp(stamp).date(), now)]
    if not keep:
        return pd.Series(dtype=float)
    return series.loc[keep]


def _snapshot_holding(row: dict) -> dict:
    ticker = row["ticker"]
    return {
        "ticker": ticker,
        "name": row["name"],
        "sleeve": _sleeve_name(row["name"], ticker),
        "weight": 0.0 if row.get("weight") is None else float(row["weight"]),
        "entry_price": None,
        "price_field": "split_adjusted_close" if ticker == "TQQQ" else "adjusted_close",
    }


def _publish_waiting(picks: dict, status: str, detail: str) -> dict:
    books = []
    for book in picks["books"]:
        item = deepcopy(book)
        item["equity"] = []
        item["metrics"] = None
        item["events"] = []
        books.append(item)
    spy = {
        "name": "S&P 500 (SPY)",
        "ticker": "SPY",
        "weight": 1.0,
        "entry_price": picks.get("spy_entry_price"),
        "price_field": "adjusted_close",
        "equity": [],
        "metrics": None,
    }
    return _payload(picks, books, spy, status, detail, None)


def _payload(picks: dict, books: list[dict], spy: dict, status: str, detail: str, as_of: str | None) -> dict:
    return {
        "label": picks["label"],
        "inception": picks["inception"],
        "initial_capital": picks["initial_capital"],
        "status": status,
        "as_of": as_of,
        "entry_session": picks.get("entry_session"),
        "status_detail": detail,
        "entry_rule": picks["entry_rule"],
        "ambiguities": picks["ambiguities"],
        "separate_from_backtests": True,
        "spy": spy,
        "books": books,
    }


def _metrics(curve: list[dict]) -> dict | None:
    if len(curve) < 2:
        if len(curve) == 1:
            return {
                "total_return": 0.0,
                "cagr": None,
                "max_drawdown": 0.0,
                "volatility": None,
                "sharpe": None,
                "final_equity": curve[0]["equity"],
                "trading_days": 1,
                "start": curve[0]["date"],
                "end": curve[0]["date"],
            }
        return None
    series = pd.Series(
        [row["equity"] for row in curve],
        index=pd.to_datetime([row["date"] for row in curve]),
    )
    metrics = compute_metrics(series)
    return {
        "total_return": metrics["total_return"],
        "cagr": metrics["cagr"],
        "max_drawdown": metrics["max_drawdown"],
        "volatility": metrics["volatility"],
        "sharpe": metrics["sharpe"],
        "final_equity": metrics["final_equity"],
        "trading_days": metrics["trading_days"],
        "start": metrics["start"],
        "end": metrics["end"],
    }


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2) + "\n"
    if path.exists() and path.read_text() == text:
        return
    path.write_text(text)
