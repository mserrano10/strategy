"""Performance statistics shared by every book.

Sharpe and volatility are annualized from daily simple returns with a
252-day year and a risk-free rate of zero. That is the usual published
backtest convention; it is not an excess return over T-bills.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def compute_metrics(equity: pd.Series) -> dict:
    equity = _clean_equity(equity)
    start_v = float(equity.iloc[0])
    end_v = float(equity.iloc[-1])
    if start_v <= 0 or end_v <= 0:
        raise ValueError("equity must stay positive")
    total_return = end_v / start_v - 1.0
    span_days = int((equity.index[-1] - equity.index[0]).days)
    years = span_days / 365.25
    cagr = (end_v / start_v) ** (1.0 / years) - 1.0 if years > 0 else None
    drawdown = equity / equity.cummax() - 1.0
    max_dd = float(drawdown.min())
    max_dd_at = pd.Timestamp(drawdown.idxmin())
    peak_at = pd.Timestamp(equity.loc[:max_dd_at].idxmax())
    rets = equity.pct_change().dropna()
    if len(rets) > 1:
        vol = float(rets.std(ddof=1) * math.sqrt(TRADING_DAYS))
        sigma = float(rets.std(ddof=1))
        sharpe = float(rets.mean() / sigma * math.sqrt(TRADING_DAYS)) if sigma > 0 else None
    else:
        vol = None
        sharpe = None
    return {
        "total_return": total_return,
        "cagr": cagr,
        "max_drawdown": max_dd,
        "max_drawdown_date": max_dd_at.date().isoformat(),
        "peak_date": peak_at.date().isoformat(),
        "volatility": vol,
        "sharpe": sharpe,
        "sharpe_basis": "annualized daily simple returns, rf=0, 252-day year",
        "years": years,
        "trading_days": int(len(equity)),
        "initial_capital": start_v,
        "final_equity": end_v,
        "start": pd.Timestamp(equity.index[0]).date().isoformat(),
        "end": pd.Timestamp(equity.index[-1]).date().isoformat(),
    }


def yearly_returns(equity: pd.Series) -> list[dict]:
    """Calendar-year changes of the published equity curve.

    The product of (1 + return) equals final equity / first equity. A year
    that never reaches late December is marked partial (the live YTD year).
    """
    equity = _clean_equity(equity)
    rows = []
    first_year = int(equity.index[0].year)
    last_year = int(equity.index[-1].year)
    for year in range(first_year, last_year + 1):
        in_year = equity[equity.index.year == year]
        if in_year.empty:
            continue
        before = equity[equity.index.year < year]
        end_dt = pd.Timestamp(in_year.index[-1])
        end_v = float(in_year.iloc[-1])
        if before.empty:
            # Opening mark only (the December rebalance that starts the
            # next year's book). It is the base for the next year, not a
            # year of its own.
            if len(in_year) < 2:
                continue
            base = float(in_year.iloc[0])
            partial = True
        else:
            base = float(before.iloc[-1])
            partial = not (end_dt.month == 12 and end_dt.day >= 28)
        if base <= 0:
            continue
        rows.append(
            {
                "year": year,
                "return": end_v / base - 1.0,
                "equity": end_v,
                "partial": partial,
            }
        )
    return rows


def drawdown_series(equity: pd.Series) -> pd.Series:
    equity = _clean_equity(equity)
    return equity / equity.cummax() - 1.0


def _clean_equity(equity: pd.Series) -> pd.Series:
    if equity is None or len(equity) < 2:
        raise ValueError("need at least two equity points")
    out = equity.astype(float).sort_index()
    out = out[~out.index.duplicated(keep="last")]
    if out.isna().any():
        raise ValueError("equity series contains NaN")
    if not out.index.is_monotonic_increasing:
        raise ValueError("equity index must be sorted")
    return out
