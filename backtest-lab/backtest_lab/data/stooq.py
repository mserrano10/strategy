"""Stooq daily bars for tickers Yahoo no longer has.

Stooq's CSV endpoint sometimes answers with a browser check instead of
prices. A check on the first symbol skips the rest of the batch and is
recorded on the coverage log. Closes from Stooq are used as both the
traded close and the adjusted close: the file has no dividend column, so
those names are price return, not total return.
"""

from __future__ import annotations

import time
from datetime import date
from io import StringIO

import pandas as pd

STOOQ_URL = "https://stooq.com/q/d/l/?s={symbol}&i=d"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


class StooqStatus:
    def __init__(self):
        self.blocked = False
        self.filled: list[str] = []
        self.missed: list[str] = []


def supplement(history, tickers: list[str], start: date, end: date, cache_dir) -> StooqStatus:
    """Add columns for `tickers` that `history` does not already contain."""
    status = StooqStatus()
    missing = [ticker for ticker in tickers if ticker not in history.adjusted_close.columns]
    if not missing:
        return status
    cache_dir.mkdir(parents=True, exist_ok=True)
    frames = {}
    for ticker in missing:
        if status.blocked:
            status.missed.append(ticker)
            continue
        frame = _cached(cache_dir, ticker)
        if frame is None:
            frame, blocked = _download(ticker, cache_dir)
            if blocked:
                status.blocked = True
                status.missed.append(ticker)
                continue
        if frame is None or frame.empty:
            status.missed.append(ticker)
            continue
        window = frame[(frame.index.date >= start - _pad()) & (frame.index.date <= end)]
        if window.empty:
            status.missed.append(ticker)
            continue
        frames[ticker] = window
        status.filled.append(ticker)
        time.sleep(0.15)
    if not frames:
        return status
    extra_adj = pd.DataFrame({ticker: frame["Adj Close"] for ticker, frame in frames.items()})
    extra_close = pd.DataFrame({ticker: frame["Close"] for ticker, frame in frames.items()})
    extra_high = pd.DataFrame({ticker: frame["High"] for ticker, frame in frames.items()})
    extra_open = pd.DataFrame({ticker: frame["Open"] for ticker, frame in frames.items()})
    history.adjusted_close = _join(history.adjusted_close, extra_adj)
    history.close = _join(history.close, extra_close)
    history.high = _join(history.high, extra_high)
    history.open = _join(history.open, extra_open)
    history.missing = [ticker for ticker in history.missing if ticker not in frames]
    return status


def _pad():
    from datetime import timedelta

    return timedelta(days=10)


def _join(left: pd.DataFrame, right: pd.DataFrame) -> pd.DataFrame:
    if left is None or left.empty:
        return right.sort_index()
    out = left.join(right, how="outer")
    out.index = pd.DatetimeIndex(out.index).normalize()
    return out.sort_index()


def _cached(cache_dir, ticker: str):
    path = cache_dir / f"{ticker.replace('/', '_')}.pkl"
    if not path.exists():
        return None
    payload = pd.read_pickle(path)
    if payload.get("empty"):
        return pd.DataFrame()
    return payload.get("frame")


def _download(ticker: str, cache_dir):
    import urllib.request

    symbols = _candidates(ticker)
    for symbol in symbols:
        url = STOOQ_URL.format(symbol=symbol)
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/csv,*/*"})
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                body = response.read().decode("utf-8", errors="replace")
        except Exception:
            continue
        if body.lstrip().startswith("<!") or "verify your browser" in body.lower():
            return None, True
        frame = _parse(body)
        if frame is not None:
            pd.to_pickle({"frame": frame, "empty": False, "symbol": symbol}, cache_dir / f"{ticker}.pkl")
            return frame, False
    pd.to_pickle({"frame": None, "empty": True}, cache_dir / f"{ticker}.pkl")
    return None, False


def _candidates(ticker: str) -> list[str]:
    raw = ticker.lower()
    dotted = raw.replace("-", ".")
    values = [f"{raw}.us", f"{dotted}.us"]
    seen = []
    for value in values:
        if value not in seen:
            seen.append(value)
    return seen


def _parse(body: str) -> pd.DataFrame | None:
    if "Date" not in body[:40]:
        return None
    frame = pd.read_csv(StringIO(body))
    if "Date" not in frame.columns or "Close" not in frame.columns:
        return None
    frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce")
    frame = frame.dropna(subset=["Date", "Close"])
    frame = frame[frame["Close"] > 0]
    if frame.empty:
        return None
    frame = frame.set_index("Date").sort_index()
    frame.index = pd.DatetimeIndex(frame.index).normalize()
    close = frame["Close"].astype(float)
    high = frame["High"].astype(float) if "High" in frame.columns else close
    opened = frame["Open"].astype(float) if "Open" in frame.columns else close
    return pd.DataFrame({"Adj Close": close, "Close": close, "High": high, "Open": opened})
