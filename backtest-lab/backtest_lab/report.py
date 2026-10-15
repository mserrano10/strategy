"""JSON, CSV, and PNG reports, plus the manifest the static page reads."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from backtest_lab import __version__
from backtest_lab.metrics import compute_metrics, drawdown_series, yearly_returns
from backtest_lab.constants import DISPLAY_ORDER

FREE_SOURCE_PAGE_BANNER = (
    "These results use the free Yahoo Finance source. "
    "Books on today's listings are SURVIVORSHIP-BIASED: they are not the companies that existed at each year-end. "
    "Point-in-time books walk Wikipedia's selected S&P 500 changes backward; that table is thin before about 2011, "
    "and names that cannot be priced are logged. "
    "The size-floor books use today's NASDAQ/NYSE common stocks, so delisted names are missing. "
    "Sharadar (SEP / SF1 / TICKERS) is the exact replacement. "
    "Every equity chart includes SPY total return, rebased to the same starting capital, as a reference. "
    "SPY is not a strategy."
)


def assemble_report(
    strategy_id: str,
    name: str,
    description: str,
    kind: str,
    group: str,
    signal: str,
    rebalance: str,
    equity: pd.Series,
    cohorts: list[dict],
    events: list[dict],
    caveats: list[str],
    warnings: list[str],
    source,
    universe: dict,
    capital: float,
    cost_bps: float,
    start_year: int,
    market_cap_note: str | None = None,
    survivorship_bias_free: bool | None = None,
    banner: str | None = None,
) -> dict:
    equity = equity.astype(float).sort_index()
    equity = equity[~equity.index.duplicated(keep="last")]
    metrics = compute_metrics(equity)
    # Entry costs are taken before the first mark. Measure headline return
    # from the cash that was actually committed so the cost is not hidden.
    if capital > 0 and abs(metrics["final_equity"] / capital - 1 - metrics["total_return"]) > 1e-12:
        metrics["total_return"] = metrics["final_equity"] / capital - 1.0
        if metrics["years"] and metrics["years"] > 0:
            metrics["cagr"] = (metrics["final_equity"] / capital) ** (1.0 / metrics["years"]) - 1.0
        metrics["initial_capital"] = capital
    if survivorship_bias_free is None:
        survivorship_bias_free = bool(source.survivorship_bias_free)
    explicit_banner = banner
    if banner is None:
        banner = source.banner()
    if explicit_banner is None and survivorship_bias_free and source.free_source:
        banner = (
            "FREE SOURCE — Yahoo Finance daily prices. "
            "This book does not select a stock universe, so S&P 500 survivorship bias does not apply. "
            "Sharadar is still the intended replacement for vendor-adjusted free prices."
        )
    drawdown = drawdown_series(equity)
    return {
        "id": strategy_id,
        "name": name,
        "description": description,
        "kind": kind,
        "group": group,
        "signal": signal,
        "version": __version__,
        "rebalance": rebalance,
        "start_year": start_year,
        "start": metrics["start"],
        "end": metrics["end"],
        "initial_capital": capital,
        "cost_bps": cost_bps,
        "data_source": {
            "id": source.id,
            "label": source.label,
            "free_source": bool(source.free_source),
            "survivorship_bias_free": bool(survivorship_bias_free),
            "banner": banner,
        },
        "universe": universe,
        "market_cap_note": market_cap_note,
        "caveats": caveats,
        "warnings": warnings,
        "metrics": metrics,
        "yearly_returns": yearly_returns(equity),
        "cohorts": cohorts,
        "events": events,
        "equity": _equity_rows(equity, drawdown),
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }


def write_report(report: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = report["id"]
    json_path = out_dir / f"{stem}.json"
    csv_path = out_dir / f"{stem}.csv"
    png_path = out_dir / f"{stem}.png"
    json_path.write_text(json.dumps(_jsonable(report), indent=2, allow_nan=False) + "\n")
    _write_csv(report, csv_path)
    _write_png(report, png_path)
    _update_manifest(out_dir, report)
    return json_path


def attach_benchmark(report: dict, spy_adjusted: pd.Series) -> dict:
    """Rebase SPY total return onto this book's starting capital and dates.

    The line is a reference. It is not part of the strategy's holdings.
    """
    if spy_adjusted is None or len(spy_adjusted) == 0 or not report.get("equity"):
        report.setdefault("warnings", []).append("SPY benchmark was not available for this book.")
        return report
    dates = pd.to_datetime([row["date"] for row in report["equity"]])
    spy = spy_adjusted.astype(float).sort_index()
    spy = spy[~spy.index.duplicated(keep="last")]
    spy = spy[spy > 0]
    aligned = spy.reindex(spy.index.union(dates)).ffill().reindex(dates)
    if aligned.isna().any() or float(aligned.iloc[0]) <= 0:
        report.setdefault("warnings", []).append("SPY benchmark could not be aligned to every date of this book.")
        return report
    capital = float(report["initial_capital"])
    curve = capital * aligned / float(aligned.iloc[0])
    curve.index = pd.DatetimeIndex(dates)
    metrics = compute_metrics(curve)
    drawdown = drawdown_series(curve)
    report["benchmark"] = {
        "id": "spy",
        "name": "S&P 500 (SPY)",
        "note": (
            "SPY adjusted close (total return), rebased to this book's starting capital "
            "on the book's first date. Reference only."
        ),
        "metrics": metrics,
        "equity": _equity_rows(curve, drawdown),
    }
    return report


def write_manifest_banner(out_dir: Path) -> None:
    """Refresh the page-level warning even if no new report was added."""
    path = out_dir / "manifest.json"
    payload = _read_manifest(path)
    payload["banner"] = FREE_SOURCE_PAGE_BANNER
    payload["version"] = __version__
    path.write_text(json.dumps(payload, indent=2) + "\n")


def _equity_rows(equity: pd.Series, drawdown: pd.Series) -> list[dict]:
    rows = []
    previous = None
    for stamp, value in equity.items():
        day = pd.Timestamp(stamp).date().isoformat()
        equity_value = round(float(value), 2)
        daily = None if previous is None or previous == 0 else value / previous - 1.0
        rows.append(
            {
                "date": day,
                "equity": equity_value,
                "drawdown": round(float(drawdown.loc[stamp]), 6),
                "daily_return": None if daily is None else round(float(daily), 8),
            }
        )
        previous = float(value)
    return rows


def _write_csv(report: dict, path: Path) -> None:
    frame = pd.DataFrame(report["equity"])
    frame.to_csv(path, index=False)


def _write_png(report: dict, path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg", force=False)
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker

    frame = pd.DataFrame(report["equity"])
    frame["date"] = pd.to_datetime(frame["date"])
    values = frame["equity"].astype(float)
    fig, ax = plt.subplots(figsize=(10.6, 5.4), dpi=140)
    fig.patch.set_facecolor("#FAF9F6")
    ax.set_facecolor("#FFFFFF")
    benchmark = report.get("benchmark") or {}
    bench_frame = pd.DataFrame(benchmark.get("equity") or [])
    stacked = values
    if len(bench_frame):
        stacked = pd.concat([values, bench_frame["equity"].astype(float)], ignore_index=True)
    use_log = float(stacked.max()) > 8 * max(float(stacked.min()), 1.0)
    series = values if not use_log else values.clip(lower=1e-6)
    ax.plot(frame["date"], series, color="#0F172A", linewidth=1.7, label=report["name"])
    if len(bench_frame):
        bench_frame["date"] = pd.to_datetime(bench_frame["date"])
        bench_values = bench_frame["equity"].astype(float)
        if use_log:
            bench_values = bench_values.clip(lower=1e-6)
        ax.plot(
            bench_frame["date"],
            bench_values,
            color="#0369A1",
            linewidth=1.4,
            linestyle=(0, (4, 3)),
            label="S&P 500 (SPY)",
        )
        ax.legend(frameon=False, fontsize=8, loc="upper left")
    if use_log:
        ax.set_yscale("log")
    ax.set_title(report["name"], loc="left", color="#0F172A", fontsize=14, fontweight="bold", pad=12)
    metrics = report["metrics"]
    cagr = metrics.get("cagr")
    max_dd = metrics.get("max_drawdown")
    subtitle = (
        f"{report['start']}  →  {report['end']}"
        f"      CAGR {cagr:.1%}" if cagr is not None else f"{report['start']}  →  {report['end']}"
    )
    if max_dd is not None:
        subtitle += f"      Max DD {max_dd:.1%}"
    if use_log:
        subtitle += "      log scale"
    ax.set_ylabel("Portfolio value ($)", color="#64748B")
    ax.tick_params(colors="#64748B", labelsize=8)
    for spine in ax.spines.values():
        spine.set_color("#E2E8F0")
    ax.grid(True, color="#E2E8F0", linewidth=0.7)
    if not use_log:
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda value, _pos: f"${value:,.0f}"))
    ax.text(
        0.0,
        1.02,
        subtitle,
        transform=ax.transAxes,
        fontsize=8,
        color="#64748B",
        va="bottom",
    )
    banner = report["data_source"]["banner"]
    fig.text(0.01, 0.012, banner, fontsize=6.5, color="#64748B", ha="left", va="bottom", wrap=True)
    fig.tight_layout(rect=(0, 0.06, 1, 0.96))
    fig.savefig(path)
    plt.close(fig)


def _update_manifest(out_dir: Path, report: dict) -> None:
    path = out_dir / "manifest.json"
    payload = _read_manifest(path)
    entry = {
        "id": report["id"],
        "name": report["name"],
        "group": report["group"],
        "file": f"{report['id']}.json",
        "csv": f"{report['id']}.csv",
        "png": f"{report['id']}.png",
        "free_source": report["data_source"]["free_source"],
        "survivorship_bias_free": report["data_source"]["survivorship_bias_free"],
    }
    existing = {item["id"]: item for item in payload["reports"]}
    existing[entry["id"]] = entry
    payload["reports"] = [existing[key] for key in DISPLAY_ORDER if key in existing]
    # Keep anything the display order doesn't know about, in case a plugin was added.
    for key, item in existing.items():
        if key not in DISPLAY_ORDER:
            payload["reports"].append(item)
    payload["generated_at"] = report["generated_at"]
    payload["banner"] = FREE_SOURCE_PAGE_BANNER
    payload["version"] = __version__
    path.write_text(json.dumps(payload, indent=2) + "\n")


def _read_manifest(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {
        "title": "Backtest Lab",
        "version": __version__,
        "banner": FREE_SOURCE_PAGE_BANNER,
        "reports": [],
    }


def _jsonable(value):
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (np.floating, float)):
        number = float(value)
        if not math.isfinite(number):
            return None
        return number
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat()
    return value
