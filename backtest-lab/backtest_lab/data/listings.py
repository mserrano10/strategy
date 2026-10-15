"""Current US common-stock listings from Nasdaq Trader.

This is who is listed today, not who was listed at each year-end. Delisted
names are absent. That is the survivorship bias of the free size-floor universe.
"""

from __future__ import annotations

import io
import re
from datetime import date

import pandas as pd

from backtest_lab.data.symbols import yahoo_symbol
from backtest_lab.data.yfinance_source import USER_AGENT

NASDAQ_LISTED = "https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt"
OTHER_LISTED = "https://www.nasdaqtrader.com/dynamic/symdir/otherlisted.txt"

# NYSE and NYSE American. Arca and BZX are mostly funds and special vehicles.
_EXCHANGES = {"N", "A"}
_BAD_NAME = re.compile(
    r"warrant|preferred|preference|unit|right|debenture|\bnotes?\b|\bbonds?\b|\bfund\b|\betf\b|\betn\b",
    re.IGNORECASE,
)


def load_us_common(refresh: bool = False, cache_dir=None) -> pd.DataFrame:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "universes"
    if cache_dir is not None:
        root = Path(cache_dir)
    path = root / "us_listed_common.csv"
    if path.exists() and not refresh:
        frame = pd.read_csv(path, comment="#")
        frame["ticker"] = frame["ticker"].astype(str)
        return frame
    frame = fetch_us_common()
    root.mkdir(parents=True, exist_ok=True)
    header = (
        f"# Current NASDAQ/NYSE/NYSE American common stocks.\n"
        f"# fetched_on: {date.today().isoformat()}\n"
        f"# survivorship_bias_free: false\n"
        f"# delisted names are not in this file\n"
    )
    path.write_text(header + frame.to_csv(index=False))
    print(f"saved US common-stock list → {path} ({len(frame)} names)")
    return frame


def fetch_us_common() -> pd.DataFrame:
    import urllib.request

    nasdaq = _read_pipe(_get(urllib.request, NASDAQ_LISTED))
    other = _read_pipe(_get(urllib.request, OTHER_LISTED))
    nasdaq_rows = []
    for row in nasdaq.itertuples(index=False):
        if str(getattr(row, "ETF", "Y")) == "Y" or str(getattr(row, "Test_Issue", "Y")) == "Y":
            continue
        ticker = yahoo_symbol(row.Symbol)
        name = str(row.Security_Name)
        if ticker and _common_name(name):
            nasdaq_rows.append({"ticker": ticker, "name": name.strip(), "exchange": "NASDAQ"})
    other_rows = []
    for row in other.itertuples(index=False):
        if str(getattr(row, "ETF", "Y")) == "Y" or str(getattr(row, "Test_Issue", "Y")) == "Y":
            continue
        if str(getattr(row, "Exchange", "")) not in _EXCHANGES:
            continue
        ticker = yahoo_symbol(getattr(row, "ACT_Symbol", None))
        name = str(row.Security_Name)
        exchange = "NYSE" if str(row.Exchange) == "N" else "NYSE American"
        if ticker and _common_name(name):
            other_rows.append({"ticker": ticker, "name": name.strip(), "exchange": exchange})
    frame = pd.DataFrame(nasdaq_rows + other_rows)
    frame = frame.drop_duplicates("ticker").sort_values("ticker").reset_index(drop=True)
    if len(frame) < 1500:
        raise RuntimeError(f"US common-stock list looks short ({len(frame)} names)")
    return frame


def _common_name(name: str) -> bool:
    text = name or ""
    if _BAD_NAME.search(text):
        return False
    lowered = text.lower()
    if "common stock" in lowered or "common share" in lowered:
        return True
    if "ordinary share" in lowered or "ordinary stock" in lowered:
        return True
    if "american depositary" in lowered:
        return True
    return False


def _get(urllib_request, url: str) -> str:
    request = urllib_request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib_request.urlopen(request, timeout=60) as response:
        return response.read().decode("utf-8", errors="replace")


def _read_pipe(text: str) -> pd.DataFrame:
    lines = [line for line in text.splitlines() if line and not line.startswith("File Creation")]
    frame = pd.read_csv(io.StringIO("\n".join(lines)), sep="|")
    frame.columns = [str(col).strip().replace(" ", "_") for col in frame.columns]
    return frame
