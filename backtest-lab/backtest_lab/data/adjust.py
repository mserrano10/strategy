"""Clip recycled tickers and wipe bankruptcy paths.

Prices are kept only inside a membership spell (plus a few days after an
exit). A later spell — the same company back in the index, or a new company
that reused the ticker — keeps its own prints. A spell with no prints is
unpriceable.

A bankruptcy removal with a last real print is marked down to a dust price
afterwards, which is a -100% total loss. The dust price stays positive so
the portfolio math still has a number to hold.
"""

from __future__ import annotations

import re

import pandas as pd

DUST = 1e-6
CARRY_DAYS = 7
_DEAL_PRICE = re.compile(
    r"\$\s*((?:[0-9]{1,3}(?:,[0-9]{3})*|[0-9]+)(?:\.[0-9]+)?)\s+per share",
    re.IGNORECASE,
)


def apply_identity(panel: pd.DataFrame, pit) -> tuple[pd.DataFrame, list[str]]:
    """Return a copy of `panel` safe to rank and to hold. `pit` is a PointInTimeSP500."""
    out = panel.copy()
    notes = []
    for rename in pit.renames():
        _stitch_rename(out, rename["old"], rename["new"], pd.Timestamp(rename["date"]))
    spells = {ticker: list(ranges) for ticker, ranges in pit.spells().items()}
    # A rename is not an exit. Keep the successor's prices on the old symbol
    # for as long as the successor itself stays in the index.
    for rename in pit.renames():
        spells.setdefault(rename["old"], []).extend(spells.get(rename["new"]) or [])
    recycled = []
    for ticker in list(out.columns):
        ranges = spells.get(ticker)
        if not ranges:
            continue
        if not _mask_to_spells(out, ticker, ranges):
            recycled.append(ticker)
    for ticker, removal in pit.removals().items():
        if ticker not in out.columns or not removal["bankruptcy"]:
            continue
        _wipe_until_reentry(out, ticker, pd.Timestamp(removal["date"]), spells.get(ticker) or [])
    for ticker, removal in pit.removals().items():
        if ticker not in out.columns or removal["bankruptcy"]:
            continue
        price = deal_price(removal.get("reason"))
        if price is None:
            continue
        _fill_deal_exit(out, ticker, pd.Timestamp(removal["date"]), price, spells.get(ticker) or [])
    if recycled:
        preview = ", ".join(recycled[:8])
        suffix = "" if len(recycled) <= 8 else ", …"
        notes.append(
            f"{len(recycled)} removed tickers had no Yahoo/Stooq prints during a membership spell "
            f"(often a recycled symbol, or a delisted name Yahoo no longer stores): {preview}{suffix}"
        )
    _carry_year_end_prints(out)
    # Clipped and stitched cells are missing (pd.NA). Rank and simulate expect floats.
    out = out.apply(pd.to_numeric, errors="coerce")
    return out, notes


def _stitch_rename(panel: pd.DataFrame, old: str, new: str, when: pd.Timestamp) -> None:
    """Fill gaps in `old` from `new`. `when` is the rename date, kept for callers.

    Own prints on `old` win. The successor fills only missing cells, including
    after the rename, so a held share does not stop the day the ticker changes.
    """
    del when
    if new not in panel.columns:
        return
    successor = panel[new]
    if old not in panel.columns:
        panel[old] = successor.copy()
        return
    gap = panel[old].isna() & successor.notna()
    panel.loc[gap, old] = successor.loc[gap]


def _mask_to_spells(panel: pd.DataFrame, ticker: str, spells: list[tuple[pd.Timestamp, pd.Timestamp | None]]) -> bool:
    """Blank the gaps after a ticker leaves, until it is a member again.

    Prices before the first spell stay. A stock added in November still has
    a calendar-year return. Prices after the last exit are dropped, except
    for a few days so the exit print can be carried to year-end. A later
    spell (re-entry, or a reused ticker) keeps its own prints.

    Returns False when no spell contains a real print.
    """
    drop = pd.Series(False, index=panel.index)
    covered = False
    column = panel[ticker]
    ordered = list(spells)
    for index, (start, end) in enumerate(ordered):
        nxt = ordered[index + 1][0] if index + 1 < len(ordered) else None
        if end is None:
            probe = column[(column.index >= start) & column.notna() & (column > 0)]
        else:
            cutoff = end + pd.Timedelta(days=CARRY_DAYS)
            probe = column[(column.index >= start) & (column.index <= cutoff) & column.notna() & (column > 0)]
            # A positive print before this exit means the later spell is the
            # same issuer, temporarily out of the index (AMD, 2013–2017).
            # Keep those prices. A series that only starts after the exit is
            # a recycled ticker: blank the gap and the post-exit tail.
            prior = column[(column.index <= end) & column.notna() & (column > 0)]
            same_issuer = nxt is not None and len(prior) > 0
            if same_issuer:
                region = None
            elif nxt is None:
                region = panel.index > cutoff
            else:
                region = (panel.index > cutoff) & (panel.index < nxt)
            if region is not None:
                drop = drop | pd.Series(region, index=panel.index)
        if len(probe):
            covered = True
    panel.loc[drop, ticker] = pd.NA
    return covered


def _wipe_until_reentry(panel: pd.DataFrame, ticker: str, when: pd.Timestamp, spells) -> None:
    column = panel[ticker]
    prior = column[(column.index < when) & column.notna() & (column > 0)]
    if prior.empty:
        return
    dust = float(prior.iloc[-1]) * DUST
    nxt = None
    for start, _end in spells:
        if start > when:
            nxt = start
            break
    if nxt is None:
        panel.loc[panel.index >= when, ticker] = dust
    else:
        panel.loc[(panel.index >= when) & (panel.index < nxt), ticker] = dust


def _carry_year_end_prints(panel: pd.DataFrame) -> None:
    """Put the last positive print of a year onto that year's last session.

    An acquisition in June has no December print. Carrying the last trade
    to the year-end session lets the calendar return use the exit price
    without inventing a path on the days in between. The simulator forward-
    fills those days from the same last trade.
    """
    if panel.empty:
        return
    years = sorted(set(int(stamp.year) for stamp in panel.index))
    for year in years:
        in_year = panel.index[panel.index.year == year]
        if len(in_year) == 0:
            continue
        end = in_year[-1]
        window = panel.loc[in_year]
        for ticker in panel.columns:
            end_value = panel.at[end, ticker]
            if _positive(end_value):
                continue
            prints = window[ticker].dropna()
            prints = prints[prints > 0]
            if prints.empty:
                continue
            panel.at[end, ticker] = float(prints.iloc[-1])


def deal_price(reason: str | None) -> float | None:
    """Cash consideration when the Wikipedia note states a per-share price.

    The selected-changes table usually names the buyer and not the price.
    A match is used only as an exit fill where no print exists on or after
    the removal. It is not a second price source for a ranking return.
    """
    match = _DEAL_PRICE.search(reason or "")
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


def _fill_deal_exit(panel: pd.DataFrame, ticker: str, when: pd.Timestamp, price: float, spells) -> None:
    column = panel[ticker]
    prior = column[(column.index < when) & column.notna() & (column > 0)]
    if prior.empty or price <= 0:
        return
    after = column[(column.index >= when) & column.notna() & (column > 0)]
    if len(after):
        return
    nxt = None
    for start, _end in spells:
        if start > when:
            nxt = start
            break
    if nxt is None:
        region = panel.index >= when
    else:
        region = (panel.index >= when) & (panel.index < nxt)
    if not region.any():
        return
    panel.loc[region, ticker] = float(price)


def _positive(value) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return number == number and number > 0
