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

import pandas as pd

DUST = 1e-6
CARRY_DAYS = 7


def apply_identity(panel: pd.DataFrame, pit) -> tuple[pd.DataFrame, list[str]]:
    """Return a copy of `panel` safe to rank and to hold. `pit` is a PointInTimeSP500."""
    out = panel.copy()
    notes = []
    for rename in pit.renames():
        _stitch_rename(out, rename["old"], rename["new"], pd.Timestamp(rename["date"]))
    spells = pit.spells()
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
    if new not in panel.columns:
        return
    successor = panel[new].copy()
    cutoff = when + pd.Timedelta(days=CARRY_DAYS)
    successor.loc[successor.index > cutoff] = pd.NA
    if old not in panel.columns:
        panel[old] = successor
        return
    current = panel[old]
    before = current[(current.index <= cutoff) & current.notna() & (current > 0)]
    if before.empty:
        panel[old] = successor
        return
    gap = current.isna() & successor.notna() & (panel.index <= cutoff)
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
            if nxt is None:
                region = panel.index > cutoff
            else:
                region = (panel.index > cutoff) & (panel.index < nxt)
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


def _positive(value) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return number == number and number > 0
