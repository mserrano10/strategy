"""Data-source plugins. Importing this package does not import yfinance."""

from __future__ import annotations

from backtest_lab.data.base import DataSource


def get_source(name: str, refresh: bool = False) -> DataSource:
    key = name.strip().lower()
    if key == "yfinance":
        from backtest_lab.data.yfinance_source import YFinanceSource

        return YFinanceSource(refresh=refresh)
    if key == "sharadar":
        from backtest_lab.data.sharadar import SharadarSource

        return SharadarSource()
    known = "yfinance, sharadar"
    raise SystemExit(f"Unknown data source {name!r}. Expected one of: {known}")


__all__ = ["DataSource", "get_source"]
