"""Yahoo Finance daily bars, cached on disk.

This is the free v0 source. It is not survivorship-bias-free: the only
universe it knows is today's S&P 500. Every report that uses it has to
say so. Historical size uses the approximation in `data/caps.py`.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from backtest_lab.calendarutil import required_through
from backtest_lab.data.base import DataSource, History
from backtest_lab.data.caps import (
    basis_from_current_shares,
    normalize_series,
    split_adjusted_share_basis,
)

log = logging.getLogger(__name__)

FIELDS = ("Adj Close", "Close", "High", "Open")
WIKIPEDIA = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
GITHUB_CONSTITUENTS = (
    "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/master/data/constituents.csv"
)
USER_AGENT = "AtlasWealthBacktestLab/0.1 (local research backtest)"


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


class YFinanceSource(DataSource):
    id = "yfinance"
    label = "Yahoo Finance free daily prices"
    free_source = True
    survivorship_bias_free = False

    def __init__(self, refresh: bool = False, cache_dir: Path | None = None):
        self.refresh = refresh
        self.cache_dir = cache_dir or (_root() / ".cache" / "yfinance")
        self.universe_path = _root() / "universes" / "sp500_current.csv"
        self._fallback_caps: list[str] = []

    def banner(self) -> str:
        return (
            "SURVIVORSHIP-BIASED — free Yahoo Finance source. "
            "The universe is the current S&P 500, not point-in-time membership. "
            "A survivorship-bias-free universe requires Sharadar (SEP / SF1 / TICKERS)."
        )

    def caveats(self) -> list[str]:
        return [
            self.banner(),
            (
                "Prices are Yahoo's daily bars. Adjusted closes (Adj Close) include "
                "splits and dividends. Traded closes (Close) are split-adjusted only "
                "and are what the market-cap approximation multiplies by shares."
            ),
            (
                "Historical market cap ≈ nearest reported shares × later splits × "
                "split-adjusted close. If Yahoo has no share history, today's share "
                "count is used instead. This is not point-in-time. Sharadar SF1 "
                "marketcap replaces it."
            ),
        ]

    def universe(self, refresh: bool = False) -> pd.DataFrame:
        if self.universe_path.exists() and not (refresh or self.refresh):
            frame = pd.read_csv(self.universe_path, comment="#")
            frame["ticker"] = frame["ticker"].astype(str)
            return frame
        frame = _fetch_sp500()
        self.universe_path.parent.mkdir(parents=True, exist_ok=True)
        stamp = date.today().isoformat()
        header = (
            f"# Current S&P 500 constituents used as a free-data proxy.\n"
            f"# fetched_on: {stamp}\n"
            f"# source: {frame['source'].iloc[0]}\n"
            f"# survivorship_bias_free: false\n"
        )
        body = frame.to_csv(index=False)
        self.universe_path.write_text(header + body)
        print(f"saved universe → {self.universe_path} ({len(frame)} names)")
        return frame

    def history(self, tickers: list[str], start: date, end: date) -> History:
        import yfinance as yf

        logging.getLogger("yfinance").setLevel(logging.CRITICAL)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        unique = list(dict.fromkeys(tickers))
        frames: dict[str, pd.DataFrame] = {}
        need: list[str] = []
        now = datetime.now(timezone.utc)
        for ticker in unique:
            cached = self._read_price_cache(ticker, start, end, now)
            if cached is not None:
                frames[ticker] = cached
            else:
                need.append(ticker)
        if need:
            print(f"downloading {len(need)} symbols from Yahoo Finance ({start} → {end})")
            fetched = _download_all(yf, need, start, end)
            for ticker, frame in fetched.items():
                self._write_price_cache(ticker, frame)
                frames[ticker] = frame
        missing = [ticker for ticker in unique if ticker not in frames]
        if missing:
            print(f"no prices for {len(missing)} symbols")
        return History(
            adjusted_close=_panel(frames, "Adj Close"),
            close=_panel(frames, "Close"),
            high=_panel(frames, "High"),
            open=_panel(frames, "Open"),
            missing=missing,
        )

    def cap_basis(self, tickers: list[str]) -> dict[str, pd.Series]:
        import yfinance as yf

        logging.getLogger("yfinance").setLevel(logging.CRITICAL)
        share_dir = self.cache_dir / "shares"
        share_dir.mkdir(parents=True, exist_ok=True)
        basis: dict[str, pd.Series] = {}
        need: list[str] = []
        for ticker in dict.fromkeys(tickers):
            path = share_dir / f"{_safe(ticker)}.pkl"
            if path.exists() and not self.refresh:
                payload = pd.read_pickle(path)
                series = payload["basis"]
                if payload.get("fallback"):
                    self._fallback_caps.append(ticker)
                if series is not None and len(series):
                    basis[ticker] = series
                continue
            need.append(ticker)
        if not need:
            return basis
        print(f"downloading share counts for {len(need)} symbols")
        from concurrent.futures import ThreadPoolExecutor, as_completed

        workers = min(8, len(need))
        done = 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_fetch_shares, yf, ticker) for ticker in need]
            for future in as_completed(futures):
                ticker, shares, splits, fallback = future.result()
                series, used_fallback = _to_basis(shares, splits, fallback)
                pd.to_pickle(
                    {"basis": series, "fallback": used_fallback, "fetched_on": date.today().isoformat()},
                    share_dir / f"{_safe(ticker)}.pkl",
                )
                if used_fallback:
                    self._fallback_caps.append(ticker)
                if len(series):
                    basis[ticker] = series
                done += 1
                if done % 50 == 0 or done == len(need):
                    print(f"  share counts {done}/{len(need)}")
        return basis

    def _read_price_cache(self, ticker: str, start: date, end: date, now: datetime) -> pd.DataFrame | None:
        path = self.cache_dir / f"{_safe(ticker)}.pkl"
        if self.refresh or not path.exists():
            return None
        payload = pd.read_pickle(path)
        frame = payload["frame"]
        fetched = date.fromisoformat(payload["fetched_on"])
        if _cache_covers(frame, fetched, start, end, now):
            return frame
        return None

    def _write_price_cache(self, ticker: str, frame: pd.DataFrame) -> None:
        pd.to_pickle(
            {"frame": frame, "fetched_on": date.today().isoformat()},
            self.cache_dir / f"{_safe(ticker)}.pkl",
        )


def _cache_covers(frame: pd.DataFrame, fetched_on: date, start: date, end: date, now: datetime) -> bool:
    if frame is None or frame.empty:
        return False
    # The requested start is a calendar date. The first bar is the next
    # session, so a cache that begins a few days later still covers it.
    if frame.index.min().date() > start + timedelta(days=10):
        return False
    need = required_through(end, now)
    if frame.index.max().date() >= need:
        return True
    # A same-day download can end early on a holiday. Don't hammer Yahoo again.
    short_by = need - frame.index.max().date()
    return fetched_on == now.date() and short_by <= timedelta(days=4)


def _panel(frames: dict[str, pd.DataFrame], field: str) -> pd.DataFrame:
    columns = {}
    for ticker, frame in frames.items():
        if field in frame.columns:
            columns[ticker] = frame[field]
    if not columns:
        return pd.DataFrame()
    panel = pd.DataFrame(columns).sort_index()
    panel = panel.dropna(how="all")
    panel.index = pd.DatetimeIndex(panel.index).normalize()
    return panel


def _download_all(yf, tickers: list[str], start: date, end: date) -> dict[str, pd.DataFrame]:
    found: dict[str, pd.DataFrame] = {}
    pending = list(tickers)
    attempts = {ticker: 0 for ticker in tickers}
    width = 40
    while pending:
        chunk = pending[:width]
        pending = pending[width:]
        got = _try_download(yf, chunk, start, end)
        if got is None:
            for ticker in chunk:
                attempts[ticker] += 1
                if attempts[ticker] <= 2:
                    pending.append(ticker)
                else:
                    print(f"  failed {ticker}")
            if len(chunk) > 1:
                width = max(1, width // 2)
            time.sleep(1.5)
            continue
        found.update(got)
        for ticker in chunk:
            if ticker not in got:
                attempts[ticker] += 1
                if attempts[ticker] <= 2:
                    pending.append(ticker)
        print(f"  prices {len(found)}/{len(tickers)}")
        time.sleep(0.25)
    return found


def _try_download(yf, tickers: list[str], start: date, end: date):
    request_end = (end if end >= date.today() else end) + timedelta(days=1)
    # yfinance treats `end` as exclusive. Ask through tomorrow when the
    # caller wants the latest session, so today's close is included after 21:00 UTC.
    if end >= date.today():
        request_end = date.today() + timedelta(days=1)
    last_error = None
    for attempt in range(3):
        try:
            raw = yf.download(
                tickers,
                start=start.isoformat(),
                end=request_end.isoformat(),
                auto_adjust=False,
                actions=False,
                progress=False,
                threads=True,
                group_by="column",
            )
            return _split_batch(raw, tickers)
        except Exception as exc:  # noqa: BLE001 — Yahoo errors vary by version
            last_error = exc
            time.sleep(1.5 * (attempt + 1))
    print(f"  chunk failed ({len(tickers)} names): {last_error}")
    return None


def _split_batch(raw: pd.DataFrame, tickers: list[str]) -> dict[str, pd.DataFrame]:
    if raw is None or len(raw) == 0:
        return {}
    out: dict[str, pd.DataFrame] = {}
    if isinstance(raw.columns, pd.MultiIndex):
        for ticker in tickers:
            pieces = {}
            for field in FIELDS:
                key = (field, ticker)
                if key in raw.columns:
                    pieces[field] = raw[key]
            frame = _clean_ohlc(pieces)
            if frame is not None:
                out[ticker] = frame
        return out
    # A one-name download comes back with a flat column index.
    pieces = {field: raw[field] for field in FIELDS if field in raw.columns}
    frame = _clean_ohlc(pieces)
    if frame is not None and tickers:
        out[tickers[0]] = frame
    return out


def _clean_ohlc(pieces: dict[str, pd.Series]) -> pd.DataFrame | None:
    if "Adj Close" not in pieces or "Close" not in pieces:
        return None
    frame = pd.DataFrame(pieces)
    idx = pd.DatetimeIndex(pd.to_datetime(frame.index, utc=True)).tz_convert(None).normalize()
    frame.index = idx
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    frame = frame.dropna(subset=["Adj Close", "Close"], how="any")
    frame = frame[(frame["Adj Close"] > 0) & (frame["Close"] > 0)]
    if frame.empty:
        return None
    return frame


def _fetch_shares(yf, ticker: str):
    tool = yf.Ticker(ticker)
    shares = None
    splits = None
    fallback = None
    try:
        shares = tool.get_shares_full(start="1990-01-01")
    except Exception:
        shares = None
    try:
        splits = tool.splits
    except Exception:
        splits = None
    empty = shares is None or len(getattr(shares, "index", [])) == 0
    if empty:
        try:
            fallback = float(tool.fast_info["shares"])
        except Exception:
            fallback = None
    return ticker, shares, splits, fallback


def _to_basis(shares, splits, fallback):
    share_series = normalize_series(None if shares is None else pd.Series(shares))
    split_series = normalize_series(None if splits is None else pd.Series(splits))
    basis = split_adjusted_share_basis(share_series, split_series)
    if len(basis):
        return basis, False
    fallback_basis = basis_from_current_shares(fallback)
    return fallback_basis, True


def _fetch_sp500() -> pd.DataFrame:
    import urllib.request

    errors = []
    try:
        request = urllib.request.Request(WIKIPEDIA, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=40) as response:
            html = response.read().decode("utf-8", errors="replace")
        # pandas treats a raw HTML string as a filename. Wrap it.
        import io
        tables = pd.read_html(io.StringIO(html))
        table = tables[0]
        frame = pd.DataFrame(
            {
                "ticker": _yahoo_symbols(table["Symbol"]),
                "name": table["Security"].astype(str),
                "sector": table["GICS Sector"].astype(str),
                "source": "wikipedia",
            }
        )
        return _dedupe(frame)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"wikipedia: {exc}")
    try:
        request = urllib.request.Request(GITHUB_CONSTITUENTS, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=40) as response:
            raw = pd.read_csv(response)
        sector = raw["GICS Sector"] if "GICS Sector" in raw.columns else ""
        frame = pd.DataFrame(
            {
                "ticker": _yahoo_symbols(raw["Symbol"]),
                "name": raw["Security"].astype(str),
                "sector": sector.astype(str) if hasattr(sector, "astype") else "",
                "source": "github:datasets/s-and-p-500-companies",
            }
        )
        return _dedupe(frame)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"github constituents csv: {exc}")
    raise RuntimeError(
        "Could not load the current S&P 500 constituent list. " + " | ".join(errors)
    )


def _yahoo_symbols(values) -> pd.Series:
    # Yahoo uses BRK-B; Wikipedia uses BRK.B.
    return values.astype(str).str.strip().str.replace(".", "-", regex=False)


def _dedupe(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame[frame["ticker"].ne("") & frame["ticker"].ne("nan")]
    frame = frame.drop_duplicates(subset=["ticker"]).reset_index(drop=True)
    if len(frame) < 400:
        raise RuntimeError(f"constituent list looks short ({len(frame)} names)")
    return frame


def _safe(ticker: str) -> str:
    return ticker.replace("/", "_").replace("\\", "_")
