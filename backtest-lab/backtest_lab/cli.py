"""Command line. `python -m backtest_lab run <strategy> --start 2010 --end today`."""

from __future__ import annotations

import argparse
import sys
from datetime import date

from backtest_lab import __version__
from backtest_lab.data import get_source
from backtest_lab.report import write_report
from backtest_lab.strategies import STRATEGIES, VARIANT_IDS, get_strategy
from backtest_lab.strategies.base import RunContext


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="backtest_lab",
        description="Backtest Lab v0. Local simulations. The site page only displays committed JSON.",
    )
    parser.add_argument("--version", action="version", version=f"backtest-lab {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="list strategies")

    run = sub.add_parser("run", help="run one strategy, or 'all'")
    run.add_argument("strategy", help="strategy id, or all")
    run.add_argument("--start", required=True, type=int, help="first calendar year to hold, e.g. 2015")
    run.add_argument("--end", default="today", help="today, a year (2024), or a date (2024-10-15)")
    run.add_argument("--source", default="yfinance", choices=["yfinance", "sharadar"])
    run.add_argument("--cost-bps", type=float, default=0.0, help="one-way cost in basis points (default 0)")
    run.add_argument("--capital", type=float, default=10_000.0)
    run.add_argument(
        "--rebalance",
        default="annual",
        help="rank books: annual (year-end close). v0 implements annual only.",
    )
    run.add_argument("--out", default=None, help="output directory (default: backtest-lab/results)")
    run.add_argument("--refresh-data", action="store_true", help="ignore the local Yahoo cache")
    run.add_argument("--refresh-universe", action="store_true", help="re-download the current S&P 500 list")

    table = sub.add_parser("table", help="print the markdown table for results already on disk")
    table.add_argument("--out", default=None)

    args = parser.parse_args(argv)
    if args.command == "list":
        _print_list()
        return 0
    out = _out_dir(getattr(args, "out", None))
    if args.command == "table":
        print(markdown_table(out))
        return 0
    return _run(args, out)


def parse_end(value: str, today: date | None = None) -> date:
    today = today or date.today()
    text = value.strip().lower()
    if text == "today":
        return today
    if len(text) == 4 and text.isdigit():
        return date(int(text), 12, 31)
    return date.fromisoformat(value)


def markdown_table(out_dir) -> str:
    import json

    rows = []
    manifest_path = out_dir / "manifest.json"
    if not manifest_path.exists():
        return "_No results yet._"
    manifest = json.loads(manifest_path.read_text())
    for item in manifest.get("reports", []):
        report = json.loads((out_dir / item["file"]).read_text())
        metrics = report["metrics"]
        rows.append(
            [
                report["name"],
                _pct(metrics.get("total_return")),
                _pct(metrics.get("cagr")),
                _pct(metrics.get("max_drawdown")),
                _pct(metrics.get("volatility")),
                _num(metrics.get("sharpe")),
                _money(metrics.get("final_equity")),
                f"{report['start']} → {report['end']}",
                "free, survivorship-biased" if not report["data_source"]["survivorship_bias_free"] else "free source",
            ]
        )
    header = [
        "Strategy",
        "Total return",
        "CAGR",
        "Max drawdown",
        "Volatility",
        "Sharpe (rf=0)",
        "Final equity",
        "Window",
        "Data",
    ]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * len(header)) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")
    lines.append("Starting capital $10,000. No benchmark. Sharpe is annualized from daily returns with rf = 0.")
    return "\n".join(lines)


def _run(args, out) -> int:
    end = parse_end(args.end)
    if args.start < 1990 or args.start > end.year:
        raise SystemExit(f"--start {args.start} is outside the end date {end.isoformat()}")
    source = get_source(args.source, refresh=args.refresh_data)
    ctx = RunContext(
        source=source,
        start_year=args.start,
        end=end,
        capital=args.capital,
        cost_bps=args.cost_bps,
        rebalance=args.rebalance,
        refresh_universe=args.refresh_universe,
    )
    if args.strategy == "all":
        targets = [(strategy, None) for strategy in STRATEGIES]
    else:
        strategy = get_strategy(args.strategy)
        only = args.strategy if args.strategy in VARIANT_IDS else None
        targets = [(strategy, only)]

    failures = []
    for strategy, only in targets:
        label = strategy.id if only is None else only
        print(f"\n=== {label} ===")
        try:
            if only is None:
                reports = strategy.run(ctx)
            else:
                reports = strategy.run(ctx, only=only)
        except Exception as exc:  # noqa: BLE001 — one book should not erase the others
            failures.append(f"{label}: {exc}")
            print(f"FAILED {label}: {exc}", file=sys.stderr)
            continue
        for report in reports:
            path = write_report(report, out)
            _print_report(report, path)
    print("\n" + markdown_table(out))
    if failures:
        print("\nFailures:", file=sys.stderr)
        for line in failures:
            print(f"- {line}", file=sys.stderr)
        return 1
    return 0


def _print_list() -> None:
    print(f"backtest-lab {__version__}")
    for strategy in STRATEGIES:
        print(f"  {strategy.id:<16} {strategy.name}")
    print("  all               run every strategy")
    print("tqqq also writes tqqq-buy-hold and tqqq-two-bullet.")


def _print_report(report: dict, path) -> None:
    metrics = report["metrics"]
    print(report["data_source"]["banner"])
    print(
        f"{report['id']}  {report['start']} → {report['end']}  "
        f"total {_pct(metrics.get('total_return'))}  "
        f"CAGR {_pct(metrics.get('cagr'))}  "
        f"maxDD {_pct(metrics.get('max_drawdown'))}  "
        f"vol {_pct(metrics.get('volatility'))}  "
        f"Sharpe {_num(metrics.get('sharpe'))}"
    )
    print(f"wrote {path}")


def _out_dir(value: str | None):
    from pathlib import Path

    if value:
        return Path(value)
    return Path(__file__).resolve().parents[1] / "results"


def _pct(value) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.1f}%"


def _num(value) -> str:
    if value is None:
        return "—"
    return f"{value:.2f}"


def _money(value) -> str:
    if value is None:
        return "—"
    return f"${value:,.0f}"


