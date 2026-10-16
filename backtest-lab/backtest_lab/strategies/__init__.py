"""Registered strategies. Add a plugin by appending it to STRATEGIES."""

from __future__ import annotations

from backtest_lab.constants import DISPLAY_ORDER
from backtest_lab.strategies.condor import CondorStrategy
from backtest_lab.strategies.rank import AnnualRank
from backtest_lab.strategies.tqqq import TqqqStrategy

STRATEGIES = [
    AnnualRank(
        "best-returner",
        "Best Returner",
        n=1,
        mode="return",
        description=(
            "At each year-end close, buy the current S&P 500 constituent with the best "
            "total return over the calendar year just ended, and hold that single name "
            "until the next year-end close."
        ),
    ),
    AnnualRank(
        "top5-returners",
        "Top 5 Returners",
        n=5,
        mode="return",
        description=(
            "At each year-end close, buy the five current S&P 500 constituents with the "
            "best total returns over the calendar year just ended, in equal weight, and "
            "hold them until the next year-end close."
        ),
    ),
    AnnualRank(
        "biggest",
        "Biggest",
        n=1,
        mode="cap",
        description=(
            "At each year-end close, buy the current S&P 500 constituent with the largest "
            "approximated market cap, and hold that single name until the next year-end close."
        ),
    ),
    AnnualRank(
        "top5-biggest",
        "Top 5 Biggest",
        n=5,
        mode="cap",
        description=(
            "At each year-end close, buy the five current S&P 500 constituents with the "
            "largest approximated market caps, in equal weight, and hold them until the "
            "next year-end close."
        ),
    ),
    TqqqStrategy(),
    CondorStrategy(),
]

# `python -m backtest_lab run tqqq` writes both books. These ids rerun one of them.
VARIANT_IDS = {
    "tqqq-buy-hold": "tqqq",
    "tqqq-two-bullet": "tqqq",
    "qqq-condor-hold": "qqq-condor",
    "qqq-condor-stop-2x": "qqq-condor",
    "qqq-condor-stop-3pct": "qqq-condor",
    "qqq-condor-filtered": "qqq-condor",
}


def get_strategy(strategy_id: str):
    for strategy in STRATEGIES:
        if strategy.id == strategy_id:
            return strategy
    if strategy_id in VARIANT_IDS:
        return get_strategy(VARIANT_IDS[strategy_id])
    known = ", ".join([item.id for item in STRATEGIES] + ["all"])
    raise SystemExit(f"Unknown strategy {strategy_id!r}. Expected one of: {known}")
