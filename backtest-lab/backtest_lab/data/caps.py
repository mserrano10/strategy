"""Free-data market-cap approximation.

Yahoo's `Close` (auto_adjust=False) is split-adjusted onto today's share
basis and is not dividend-adjusted. Yahoo's historical share counts are the
actual shares reported on that date, so they still sit on the pre-split
basis. Multiplying those shares by every split that happened *after* the
share-count date puts them on the same basis as `Close`.

    market_cap(t) = shares(t*) × Π splits_after(t*) × split_adjusted_close(t)

`t*` is the latest share record on or before `t`. If the first record is
later than `t`, that earliest record is used. Names with no share history
fall back to today's share count, which is already on the current split
basis (constant-share approximation).

This misses buybacks and issuance between the share record and `t`, and it
is not a point-in-time fundamental. Sharadar SF1 `marketcap` replaces it.
"""

from __future__ import annotations

import pandas as pd


def normalize_series(series: pd.Series | None) -> pd.Series:
    if series is None or len(series) == 0:
        return pd.Series(dtype=float)
    idx = pd.DatetimeIndex(pd.to_datetime(series.index, utc=True)).tz_convert(None).normalize()
    out = pd.Series(pd.to_numeric(series.to_numpy(), errors="coerce"), index=idx, dtype=float)
    out = out[~out.index.duplicated(keep="last")].sort_index().dropna()
    return out


def split_adjusted_share_basis(shares: pd.Series, splits: pd.Series) -> pd.Series:
    """Actual shares converted onto the split basis of Yahoo's `Close`."""
    shares = normalize_series(shares)
    splits = normalize_series(splits)
    shares = shares[shares > 0]
    if shares.empty:
        return pd.Series(dtype=float)
    split_dates = list(splits.index)
    split_values = [float(value) for value in splits.to_numpy()]
    basis: dict[pd.Timestamp, float] = {}
    for stamp, share_count in shares.items():
        factor = 1.0
        for split_day, ratio in zip(split_dates, split_values):
            if split_day > stamp and ratio > 0:
                factor *= ratio
        basis[pd.Timestamp(stamp)] = float(share_count) * factor
    return pd.Series(basis, dtype=float).sort_index()


def basis_from_current_shares(shares: float) -> pd.Series:
    """Fallback when Yahoo has no historical share count."""
    if shares is None or not (shares > 0):
        return pd.Series(dtype=float)
    return pd.Series({pd.Timestamp("1900-01-01"): float(shares)})


# A split-adjusted close above this, paired with a live print under LIVE_PRINT_OK,
# is Yahoo's reverse-split history blowing up. NVR and other real four-digit
# stocks stay under it. Berkshire Hathaway class A is above both bounds, so the
# live-print test keeps it.
CORRUPT_CLOSE = 20_000.0
LIVE_PRINT_OK = 10_000.0
# Above the largest real company. A bigger "cap" is a broken share or price series.
MAX_PLAUSIBLE_CAP = 15_000_000_000_000.0


def corrupt_close_tickers(close: pd.DataFrame) -> list[str]:
    """Tickers whose adjusted history is not a usable price."""
    bad = []
    for ticker in close.columns:
        column = pd.to_numeric(close[ticker], errors="coerce").dropna()
        if column.empty:
            continue
        if float(column.max()) > CORRUPT_CLOSE and float(column.iloc[-1]) < LIVE_PRINT_OK:
            bad.append(str(ticker))
    return bad


def market_cap(basis: pd.Series, close: float, asof: pd.Timestamp) -> float | None:
    if basis is None or len(basis) == 0 or close is None or not (close > 0):
        return None
    asof = pd.Timestamp(asof).tz_localize(None).normalize()
    before = basis[basis.index <= asof]
    share_basis = float(before.iloc[-1] if len(before) else basis.iloc[0])
    if not (share_basis > 0):
        return None
    cap = share_basis * float(close)
    if cap > MAX_PLAUSIBLE_CAP:
        return None
    return cap
