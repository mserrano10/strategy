"""Pluggable price and universe interface.

A source owns both the prices and the membership list, because swapping in
Sharadar is what removes survivorship bias. Strategies should read
`free_source` and `survivorship_bias_free` from the source and copy them
onto every report. Do not hardcode the Yahoo banner inside a strategy.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

import pandas as pd


class DataSource(ABC):
    id: str
    label: str
    free_source: bool
    survivorship_bias_free: bool

    @abstractmethod
    def banner(self) -> str:
        """One sentence a report can print in front of the numbers."""

    def caveats(self) -> list[str]:
        return []

    @abstractmethod
    def universe(self, refresh: bool = False) -> pd.DataFrame:
        """Columns: ticker, name, sector. Plus a `source` column when known."""

    @abstractmethod
    def history(self, tickers: list[str], start: date, end: date) -> "History":
        """Daily bars. `adjusted_close` includes dividends; `close` does not."""

    @abstractmethod
    def cap_basis(self, tickers: list[str]) -> dict[str, pd.Series]:
        """Shares on the same split basis as `history().close`.

        Market cap on a date is `basis.asof(date) * close`. An empty dict
        entry means the name cannot be ranked by size.
        """


class History:
    def __init__(
        self,
        adjusted_close: pd.DataFrame,
        close: pd.DataFrame,
        high: pd.DataFrame,
        open: pd.DataFrame,
        missing: list[str],
    ):
        self.adjusted_close = adjusted_close
        self.close = close
        self.high = high
        self.open = open
        self.missing = missing
