"""Command line. `python -m backtest_lab run <strategy> --start 2010 --end today`."""

from __future__ import annotations

import argparse
import sys
from datetime import date

from backtest_lab import __version__
from backtest_lab.data import get_source
from backtest_lab.report import attach_benchmark, write_report
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
    run.add_argument(
        "--universe",
        default="sp500-current",
        choices=["sp500-current", "sp500-pit", "us-listed"],
        help="sp500-current (survivorship-biased), sp500-pit, or us-listed",
    )
    run.add_argument(
        "--min-cap",
        default=None,
        help="size floor for us-listed, e.g. 10b or 2b",
    )
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

    mark = sub.add_parser(
        "forward-mark",
        help="mark the frozen Oct 15, 2026 forward-test books; does not rerun backtests",
    )
    mark.add_argument("--refresh-data", action="store_true", help="ignore the local Yahoo cache")

    args = parser.parse_args(argv)
    if args.command == "list":
        _print_list()
        return 0
    if args.command == "forward-mark":
        from backtest_lab.forward import main_mark

        return main_mark(refresh=args.refresh_data)
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
        bias = report["data_source"]["survivorship_bias_free"]
        universe = (report.get("universe") or {}).get("id", "")
        if universe == "sp500-pit":
            data = "free, point-in-time attempt"
        elif bias:
            data = "free source"
        else:
            data = "free, survivorship-biased"
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
                data,
            ]
        )
    if rows:
        sample = json.loads((out_dir / manifest["reports"][0]["file"]).read_text())
        bench = (sample.get("benchmark") or {}).get("metrics")
        if bench:
            rows.append(
                [
                    "S&P 500 (SPY)",
                    _pct(bench.get("total_return")),
                    _pct(bench.get("cagr")),
                    _pct(bench.get("max_drawdown")),
                    _pct(bench.get("volatility")),
                    _num(bench.get("sharpe")),
                    _money(bench.get("final_equity")),
                    f"{bench.get('start')} → {bench.get('end')}",
                    "reference, total return",
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
    lines.append(
        "Starting capital $10,000. SPY total return is a reference row, rebased to the same capital. "
        "Sharpe is annualized from daily returns with rf = 0."
    )
    return "\n".join(lines)


def _run(args, out) -> int:
    end = parse_end(args.end)
    if args.start < 1990 or args.start > end.year:
        raise SystemExit(f"--start {args.start} is outside the end date {end.isoformat()}")
    source = get_source(args.source, refresh=args.refresh_data)
    if args.strategy == "event-study":
        return _event_study(args, source, end, out)
    jobs = _jobs(args)
    failures = []
    spy = _spy_series(source, args.start, end)
    for strategy, only, universe_id, min_cap in jobs:
        label = strategy.id if only is None else only
        if universe_id != "sp500-current":
            label = f"{label} [{universe_id}{'' if not min_cap else ' ' + _cap_tag(min_cap)}]"
        print(f"\n=== {label} ===")
        ctx = RunContext(
            source=source,
            start_year=args.start,
            end=end,
            capital=args.capital,
            cost_bps=args.cost_bps,
            rebalance=args.rebalance,
            refresh_universe=args.refresh_universe,
            universe_id=universe_id,
            min_market_cap=min_cap,
        )
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
            if spy is not None:
                attach_benchmark(report, spy)
                _attach_brackets(report, spy)
            path = write_report(report, out)
            _print_report(report, path)
    print("\n" + markdown_table(out))
    if failures:
        print("\nFailures:", file=sys.stderr)
        for line in failures:
            print(f"- {line}", file=sys.stderr)
        return 1
    return 0


def _jobs(args):
    universe_id = args.universe
    min_cap = parse_min_cap(args.min_cap) if args.min_cap else None
    rank = [item for item in STRATEGIES if item.id in {"best-returner", "top5-returners", "biggest", "top5-biggest"}]
    returns_only = [item for item in rank if item.mode == "return"]
    if args.strategy == "all":
        return [(strategy, None, "sp500-current", None) for strategy in STRATEGIES]
    if args.strategy == "pit":
        return [(strategy, None, "sp500-pit", None) for strategy in rank]
    if args.strategy == "size":
        floors = [min_cap] if min_cap else [10_000_000_000.0, 2_000_000_000.0]
        return [
            (strategy, None, "us-listed", floor)
            for floor in floors
            for strategy in returns_only
        ]
    strategy = get_strategy(args.strategy)
    only = args.strategy if args.strategy in VARIANT_IDS else None
    return [(strategy, only, universe_id, min_cap)]


def _event_study(args, source, end, out) -> int:
    import json

    from backtest_lab.data.adjust import apply_identity
    from backtest_lab.data.sp500_pit import load_point_in_time
    from backtest_lab.data.stooq import supplement
    from backtest_lab.eventstudy import build_event_study

    pit = load_point_in_time(refresh=args.refresh_universe)
    tickers = pit.tickers_between(args.start - 1, end.year)
    for rename in pit.renames():
        if rename["new"] not in tickers:
            tickers.append(rename["new"])
    start = date(args.start - 2, 1, 1)
    history = source.history(tickers, start, end)
    status = supplement(history, tickers, start, end, source.cache_dir / "stooq")
    adjusted, notes = apply_identity(history.adjusted_close, pit)
    if status.blocked:
        notes.append("Stooq returned a browser check, so it filled no delisted names.")
    elif status.filled:
        notes.append(f"Stooq filled {len(status.filled)} tickers. Those bars are price return, not total return.")
    spy = _spy_series(source, args.start, end)
    payload = build_event_study(adjusted, pit, args.start, end, notes, spy=spy)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "event-study.json"
    path.write_text(json.dumps(payload, indent=2) + "\n")
    manifest_path = out / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
    else:
        manifest = {"title": "Backtest Lab", "reports": []}
    manifest["event_study"] = "event-study.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    summary = payload["summary"]
    print(
        f"event study {args.start}–{end.isoformat()}  "
        f"completed years {summary['completed_years']}  "
        f"#1 down >30% {summary['winner_down_30']}/{summary['winner_observed']}  "
        f"#1 down >50% {summary['winner_down_50']}/{summary['winner_observed']}  "
        f"top5 down >30% {summary['top5_down_30']}/{summary['top5_observed']}  "
        f"top5 down >50% {summary['top5_down_50']}/{summary['top5_observed']}"
    )
    _print_distribution(summary.get("distribution"))
    for row in payload["years"]:
        names = ", ".join(row.get("unpriceable_names") or []) or "—"
        print(f"  {row['year']}: members {row['members']}  priced {row['priced']}  unpriceable {row['unpriceable']}  {names}")
    print(f"wrote {path}")
    return 0


def _attach_brackets(report: dict, spy) -> None:
    coverage = (report.get("universe") or {}).get("coverage")
    cohorts = report.get("cohorts") or []
    if not coverage or not cohorts or report.get("kind") != "annual-rank":
        return
    from backtest_lab.eventstudy import coverage_brackets

    report["robustness"] = coverage_brackets(coverage, cohorts, spy, float(report["initial_capital"]))


def _print_distribution(distribution: dict | None) -> None:
    if not distribution:
        return
    for label, block in (("winner", distribution.get("winner")), ("top5", distribution.get("top5"))):
        if not block:
            continue
        for scenario in ("excluded", "worst", "neutral"):
            stats = block.get(scenario) or {}
            print(
                f"  {label} {scenario}: n={stats.get('n')}  "
                f"median {_pct(stats.get('median'))}  mean {_pct(stats.get('mean'))}  "
                f"hit vs SPY {_pct(stats.get('hit_rate_vs_spy'))}  "
                f"<−30% {_pct(stats.get('pct_below_neg_30'))}  "
                f"<−50% {_pct(stats.get('pct_below_neg_50'))}"
            )


def _spy_series(source, start_year: int, end):
    try:
        history = source.history(["SPY"], date(start_year - 2, 1, 1), end)
    except Exception as exc:  # noqa: BLE001
        print(f"SPY benchmark unavailable: {exc}", file=sys.stderr)
        return None
    frame = history.adjusted_close
    if frame is None or "SPY" not in frame.columns:
        print("SPY benchmark unavailable: no adjusted close", file=sys.stderr)
        return None
    return frame["SPY"].dropna()


def parse_min_cap(value: str) -> float:
    text = value.strip().lower().replace(",", "").replace("$", "").replace("_", "")
    mult = 1.0
    if text.endswith("b"):
        mult = 1_000_000_000.0
        text = text[:-1]
    elif text.endswith("m"):
        mult = 1_000_000.0
        text = text[:-1]
    return float(text) * mult


def _cap_tag(value: float) -> str:
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.0f}b"
    return f"{value / 1_000_000:.0f}m"


def _print_list() -> None:
    print(f"backtest-lab {__version__}")
    for strategy in STRATEGIES:
        print(f"  {strategy.id:<16} {strategy.name}")
    print("  all               run every strategy on today's S&P 500")
    print("  pit               four rank books on the point-in-time S&P 500")
    print("  size              best and top-5 returners at $10B and $2B")
    print("  event-study       next-year returns of #1 and top 5 since --start")
    print("tqqq also writes tqqq-buy-hold and tqqq-two-bullet.")
    print("qqq-condor writes hold, 2× credit, 3% of VAR, and the filtered 3% book.")


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
    condor = (report.get("condor") or {}).get("accounts", {}).get("10000")
    if condor:
        print(
            f"  trades {condor.get('n')}  win {_pct(condor.get('win_rate'))}  "
            f"expectancy {_pct(condor.get('expectancy_pct'))}  "
            f"gaps {condor.get('gaps')}  ruined {condor.get('ruined')}"
        )
    trigger = ((report.get("condor") or {}).get("calibration") or {}).get("trigger") or {}
    shown = (trigger.get("since_2020") or {}).get("median_tested_vertical")
    ratio = (trigger.get("since_2020") or {}).get("ratio_to_reference")
    if shown is None:
        shown = trigger.get("median_tested_vertical")
        ratio = trigger.get("ratio_to_reference")
    if shown is not None and ratio is not None:
        print(
            f"  3% stop since 2020, breached vertical median ${shown:.2f} "
            f"vs $0.65 ({ratio:.2f}×)"
        )
    robustness = report.get("robustness")
    if robustness:
        print(
            "  brackets  "
            f"excluded {_pct(robustness['excluded'].get('total_return'))}  "
            f"neutral {_pct(robustness['neutral'].get('total_return'))}  "
            f"worst {_pct(robustness['worst'].get('total_return'))}"
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


