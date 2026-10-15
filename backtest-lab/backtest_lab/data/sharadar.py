"""Sharadar stub. Swap this in later; v0 does not fetch it.

Nasdaq Data Link tables the finished adapter should read, with the API key
taken from the environment variable NASDAQ_DATA_LINK_API_KEY:

- SHARADAR/SEP       daily prices. Use `closeadj` for dividend- and
                     split-adjusted closes. `close` is split-adjusted only.
- SHARADAR/SF1       fundamentals. Use `marketcap` (prefer the ARQ/MRQ
                     dimension that is current as of the rebalance date,
                     not a later restatement).
- SHARADAR/TICKERS   security master, including delisted names
                     (`isdelisted`, `firstpricedate`, `lastpricedate`).

Point-in-time S&P membership has to come from a membership history built
on that master. Reusing today's constituent list here would put the
survivorship bias back.

The Python client is `nasdaqdatalink.get_table(...)` from the optional
`nasdaq-data-link` package (`pip install 'backtest-lab[sharadar]'`).
"""

from __future__ import annotations

import os
from datetime import date

import pandas as pd

from backtest_lab.data.base import DataSource, History

API_KEY_ENV = "NASDAQ_DATA_LINK_API_KEY"
SEP_TABLE = "SHARADAR/SEP"
SF1_TABLE = "SHARADAR/SF1"
TICKERS_TABLE = "SHARADAR/TICKERS"


class SharadarSource(DataSource):
    """Intentionally unfinished. Methods fail loudly so a caller cannot
    mistake the stub for a survivorship-bias-free result.
    """

    id = "sharadar"
    label = "Sharadar via Nasdaq Data Link (stub)"
    free_source = False
    # Design intent of the finished adapter. No method returns data in v0.
    survivorship_bias_free = True
    implemented = False

    def __init__(self, api_key: str | None = None):
        if api_key is None:
            api_key = os.environ.get(API_KEY_ENV)
        self.api_key = api_key.strip() if api_key else None

    def banner(self) -> str:
        return (
            "Sharadar adapter is a v0 stub and produced no prices. "
            "A finished adapter should be survivorship-bias-free via "
            f"{TICKERS_TABLE} (including delisted names), {SEP_TABLE} closeadj, "
            f"and {SF1_TABLE} marketcap."
        )

    def caveats(self) -> list[str]:
        return [self.banner()]

    def universe(self, refresh: bool = False) -> pd.DataFrame:
        self._unavailable(TICKERS_TABLE, "security master, including delisted names")

    def history(self, tickers: list[str], start: date, end: date) -> History:
        self._unavailable(SEP_TABLE, "daily prices; use the closeadj column")

    def cap_basis(self, tickers: list[str]) -> dict[str, pd.Series]:
        self._unavailable(SF1_TABLE, "point-in-time marketcap")

    def _unavailable(self, table: str, detail: str) -> None:
        if not self.api_key:
            raise RuntimeError(
                f"Sharadar adapter requires {API_KEY_ENV} in the environment. "
                "v0 reads the variable and then stops; it does not call Nasdaq Data Link."
            )
        raise NotImplementedError(
            "Sharadar fetch is not implemented in v0. "
            f"Intended table: {table} ({detail}). "
            "Call nasdaqdatalink.get_table(...) with the key from "
            f"{API_KEY_ENV}. See backtest_lab/data/sharadar.py and the README."
        )
