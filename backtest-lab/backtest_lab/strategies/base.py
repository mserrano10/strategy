"""Strategy plugins.

A plugin is a small object with an id and a `run` method. Rank books share
one class configured with `n` and a signal. The TQQQ memorandum is its own
plugin because it is not an annual rank.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from backtest_lab.data.base import DataSource


@dataclass
class RunContext:
    source: DataSource
    start_year: int
    end: date
    capital: float
    cost_bps: float
    rebalance: str = "annual"
    refresh_universe: bool = False
