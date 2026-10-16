# Backtest Lab v0

Local portfolio backtests for Atlas Wealth. The Python engine runs on your machine. The page at `backtest-lab/index.html` only displays JSON that has already been committed under `backtest-lab/results/`. It is served by GitHub Pages and is **not linked from the portal index**.

## Install

```bash
cd backtest-lab
python3 -m pip install -r requirements.txt
```

Python 3.11 or newer. Optional, for a future Sharadar implementation:

```bash
python3 -m pip install -e '.[sharadar]'
```

## Run

From `backtest-lab/`:

```bash
python3 -m backtest_lab list
python3 -m backtest_lab run best-returner --start 2010 --end today
python3 -m backtest_lab run all --start 2015 --end today
python3 -m backtest_lab table
```

`--end` accepts `today`, a year (`2024`, meaning 31 Dec), or a date (`2024-10-15`). `--start` is the first calendar year the book is held. Rank books are chosen at the previous year-end close, so a 2015 start buys on the last session of 2014.

Other flags:

| Flag | Default | Meaning |
| --- | --- | --- |
| `--source` | `yfinance` | `yfinance` or `sharadar` |
| `--cost-bps` | `0` | Cost on traded notional, in basis points. Buys and sells both count. |
| `--capital` | `10000` | Starting cash |
| `--rebalance` | `annual` | Rank books only. v0 implements year-end close. |
| `--out` | `results/` | Where JSON, CSV, and PNG are written |
| `--refresh-data` | off | Ignore the on-disk Yahoo cache (`.cache/`, not committed) |
| `--refresh-universe` | off | Re-download the current S&P 500 list |

Each run writes:

- `results/<id>.json` — metrics, yearly returns, cohorts, events, daily equity
- `results/<id>.csv` — the equity curve (`date`, `equity`, `drawdown`, `daily_return`)
- `results/<id>.png` — equity curve
- `results/manifest.json` — the index the page loads

Open the page from the site at `/backtest-lab/`, or locally with a static server (`python3 -m http.server` inside `backtest-lab`). Fetching the JSON from a `file://` URL will not work.

## Strategies

| Id | What it holds |
| --- | --- |
| `best-returner` | The single best total return in the prior calendar year |
| `top5-returners` | The five best, equal weight |
| `biggest` | The largest approximated market cap at the prior year-end close |
| `top5-biggest` | The five largest, equal weight |
| `tqqq` | Two books: `tqqq-buy-hold` and `tqqq-two-bullet` |
| `qqq-condor` | Four books: hold, stop at 2× credit, stop at 3% of VAR, and that stop with week filters |

You can rerun one TQQQ book with `run tqqq-buy-hold` or `run tqqq-two-bullet`.

Rank books default to **today's S&P 500 constituents**. That is survivorship-biased. Every one of those reports says so, in the CLI, in the JSON (`data_source.survivorship_bias_free: false`), and on the page.

Two other universes are configurable:

| `--universe` | What it is |
| --- | --- |
| `sp500-current` | Today's S&P 500, applied to every year. Survivorship-biased. |
| `sp500-pit` | fja05680/sp500 historical constituent snapshots from 1996 (`universes/sp500_constituents_fja.csv`). Wikipedia's selected-changes table supplies bankruptcy notes, ticker continuations, and changes after the file's last row. |
| `us-listed` | Current NASDAQ, NYSE, and NYSE American common stocks, kept when approximated market cap at the prior year-end is at least `--min-cap` (`10b`, `2b`). Delisted names are missing, so this is still survivorship-biased. Listings whose split-adjusted history peaks above $20,000 while the latest print is under $10,000 are dropped (broken Yahoo reverse-split series). |

```bash
python3 -m backtest_lab run pit --start 2015 --end today
python3 -m backtest_lab run best-returner --universe sp500-pit --start 2015 --end today
python3 -m backtest_lab run size --start 2015 --end today
python3 -m backtest_lab run best-returner --universe us-listed --min-cap 10b --start 2015 --end today
python3 -m backtest_lab run event-study --start 2000 --end today
```

`pit` reruns the four rank books without overwriting the current-list results (`best-returner-pit`, and the same suffix on the other three). `size` writes `best-returner-10b`, `best-returner-2b`, `top5-returners-10b`, and `top5-returners-2b`.

The snapshot file is the membership list from 1996. It is not a CRSP tape: delisted prices are still whatever Yahoo (and, when it answers, Stooq) will serve, and a name with no print is logged rather than invented. A prior-year return above +1000% is dropped unless a second vendor confirms it. Wikipedia's selected-changes table is thin before about 2011, so bankruptcy notes from those years are incomplete even when the snapshot says who was in the index.

Removed tickers are priced from Yahoo first, then Stooq. A recycled ticker (prints that only exist after the removal) is not used. Names that still cannot be priced are counted by year on the report. If Wikipedia's reason is a bankruptcy and a last real print exists, the path after the removal is marked to about zero (−100%). Stooq's file has no dividend column, so a Stooq fill is price return, and the report says which tickers those were. If Stooq answers with a browser check, nothing is filled and the gap stays in the log.

`event-study` lists, for each year from `--start`, the #1 and top 5 point-in-time members by that year's total return and those stocks' next-year returns. Crash rates (down more than 30% or 50%) use only years whose next year ran through mid-December. The partial live year is in the table and out of the rates. The same finished years are summarized against SPY (median, mean, hit rate). A missing next year can be dropped, filled with −100%, or filled with that year's SPY return. A prior-year return above +1000% cannot win the ranking.

Every book, including TQQQ, gets an SPY total-return line on the PNG and on the page, rebased to the book's starting capital. The summary table has one SPY reference row. SPY is not a holding. The rest of the site does not plot SPY.

The TQQQ books do not pick stocks. Survivorship bias of the index does not apply to them. They are still flagged as free Yahoo data.

`tqqq-two-bullet` follows the rule on the TQQQ memorandum (`tqqq/index.html`):

- 90% SPY, bought once. The 90/10 split is never rebalanced.
- 10% satellite, in two 5% bullets, parked in BIL (Treasury-bill ETF) as the memo's Treasury MMF.
- Bullet 1 buys when TQQQ is down at least 50% from its high-water mark. Bullet 2 buys at an 80% drawdown from that **same locked** mark.
- Each bullet deploys 50% / 25% / 25% of the cash it held at the trigger, at the close, on the trigger date and about 30 and 60 calendar days later.
- The cycle floor is the lowest TQQQ close since the trigger. A limit sells the TQQQ shares at 10× that floor.
- The high-water mark is seeded from TQQQ's history before the start date and is not reset when a cycle ends.

The memo's pre-2010 table uses a simulated 3× Nasdaq-100. v0 does not. It trades listed TQQQ only (inception 2010).

`qqq-condor` is the tracked weekly QQQ iron condor. The put is the further of the 0.03-delta strike and the calculator's 2.90-sigma floor. The call is the 0.03 delta. Wings are 10 points, Monday open to Friday. Prices are Black–Scholes on Yahoo's QQQ bars and `^VXN` (scaled `^VIX` when `^VXN` is missing), with a bid/ask haircut on the entry credit. The close mark starts at that credit and moves with the model, so the spread is not booked as an opening loss. The 3% book exits when the loss, net of that week's credit, reaches 3% of Monday net worth. Contracts use the desk budget, 18.75% of net worth. It reports the modelled price of the breached vertical next to the $0.65 tracking figure. The page states that VAR definition as an assumption to confirm.

```bash
python3 -m backtest_lab run qqq-condor --start 1999 --end today
```

## How to add a strategy

1. Add a plugin under `backtest_lab/strategies/`. A rank variant is a config of `AnnualRank`:

   ```python
   AnnualRank(
       "top10-returners",
       "Top 10 Returners",
       n=10,
       mode="return",  # or "cap"
       description="Equal-weight top 10 prior-year returns.",
   )
   ```

   Anything that is not an annual rank (the TQQQ rule is the example) is a class with `id`, `name`, `description`, and `run(ctx) -> list[dict]`. Build the result with `assemble_report` so the page, the CSV, and the PNG stay in one shape. Put the simulation in a pure function and unit-test it from `tests/` with made-up prices. Do not hit Yahoo in tests.

2. Append the object to `STRATEGIES` in `backtest_lab/strategies/__init__.py`.

3. If the page should show it, add the result id to `DISPLAY_ORDER` in `backtest_lab/constants.py`. A plugin may write more than one result. `tqqq` writes two.

4. Copy `free_source` and `survivorship_bias_free` from the data source onto the report. Do not hardcode "this one is fine" for a book that ranks current S&P members.

5. Run it and, if the page should update, commit the new files under `results/`.

`RunContext` carries the source, the start year, the end date, capital, cost, and the rebalance setting. Rank plugins reject anything other than `--rebalance annual`.

## How to swap the data source

Prices and membership move together. A strategy calls `ctx.source` and does not know whether the bars came from Yahoo or Sharadar.

```bash
python3 -m backtest_lab run best-returner --source sharadar --start 2015 --end today
```

v0's Sharadar class is a stub (`backtest_lab/data/sharadar.py`). It reads `NASDAQ_DATA_LINK_API_KEY` and then stops, naming the table it would call:

| Table | Use |
| --- | --- |
| `SHARADAR/SEP` | Daily prices. `closeadj` is the dividend- and split-adjusted close. `close` is split-adjusted only. |
| `SHARADAR/SF1` | `marketcap`, as of the rebalance date, not a later restatement. |
| `SHARADAR/TICKERS` | Security master, **including delisted names**. |

The finished adapter should implement `universe`, `history`, and `cap_basis` on `DataSource`. Set `survivorship_bias_free = True` only when membership is point-in-time. Reusing `universes/sp500_current.csv` inside the Sharadar class would put the bias back. The stub's banner already says it produced no prices, so a half-wired run cannot be mistaken for a clean result.

Register any new source in `get_source` (`backtest_lab/data/__init__.py`) and add it to the `--source` choices in `cli.py`.

## Market-cap approximation (free source only)

Yahoo's `Close` is split-adjusted onto today's basis and is not dividend-adjusted. Yahoo's historical share counts are the actual shares on the report date. v0 converts them:

```
market_cap(t) = shares(t*) × (splits after t*) × split_adjusted_close(t)
```

`t*` is the latest share record on or before `t`, or the earliest record if none exists yet. Names with no share history fall back to today's share count. Buybacks and issuance between the record and `t` are not corrected. Rank order among the very largest names is usually stable; it is not a fundamental. Sharadar `SF1.marketcap` replaces it. The approximation is repeated on every size book and in `data_source` caveats.

## Caveats

- **Survivorship bias.** Current S&P members, and the current US listing file, include winners that are still listed and exclude names that were removed. The point-in-time walk is only as complete as Wikipedia's selected-changes table. A bias-free universe needs Sharadar. Free rank results stay labeled `SURVIVORSHIP-BIASED` unless the banner says the book is a point-in-time attempt.
- **SPY is a reference.** Adjusted-close total return, same dates, same starting capital. It is drawn on Backtest Lab charts only.
- **Free data.** Yahoo's adjusted bars are vendor data. They are not a primary tape.
- **Dividends.** Rank books and TQQQ buy-and-hold mark a constant share count on adjusted closes, which matches total return. The two-bullet TQQQ position is marked on the split-adjusted close because the memo's limits are traded prices. That book's TQQQ dividend is omitted. SPY and BIL in that book use adjusted closes.
- **Fills.** Signals and entries use the same close. That is slightly optimistic versus the next open. The two-bullet exit fills at the limit when the daily high reaches it, or at the open if the open has gapped through the limit. The floor is updated after that check, so today's low cannot set a limit today's high already traded through.
- **Costs.** Default is zero. `--cost-bps` charges the rate on the dollar value traded. Headline total return and CAGR are measured from starting capital, so an entry cost is included. Cohort returns for rank books run from the post-trade entry to the next rebalance close *before* the next trade.
- **Sharpe.** Annualized daily simple returns, 252-day year, risk-free rate zero. It is not excess return over T-bills. Volatility is the same daily series.
- **Rebalance.** v0 rank books trade once a year, at the year-end close. The flag is `--rebalance annual` so a later frequency can be added without rewriting the plugins.
- **Partial years.** The last calendar year is marked `partial` when the curve has not reached late December. `--end today` does that to the current year.
- **Not advice.** This is a research ledger. It is not a recommendation to trade.

## Forward test

The forward test is a separate ledger. It does not rerun the backtests and it does not rewrite `results/<strategy>.json`.

Each stock book is the 2026 cohort already in those files: the names the rule held after the 2025-12-31 close. Top 5 Biggest is ranked by company, so Alphabet is one line (GOOGL, the larger class) and the fifth company is AMZN. The published backtest still lists GOOG and GOOGL as two holdings. The return starts at the Oct 15, 2026 official close. Point-in-time Best Returner and Top 5 Returners are their own sleeves. The two cap books match across the current list and the point-in-time list on this cohort, so each has one sleeve. TQQQ buy-and-hold is not included. The two-bullet book carries the engine state from the Oct 14, 2026 close (SPY shares, bullet cash, unlocked high-water mark) and keeps applying the live rules: 50% and 80% drawdowns, 50/25/25 tranches, and a sale at 10× the lowest close since the trigger.

Frozen picks live in `forward/picks.json`. The page reads `results/forward-test.json`. Entry prices stay blank until the first completed cash session on or after Oct 15. A same-day Yahoo bar before 21:00 UTC is not that close.

```bash
cd backtest-lab
python3 -m backtest_lab forward-mark
```

GitHub Actions runs that command at 21:30 UTC on weekdays (`.github/workflows/forward-test.yml`) and commits those two files.

## Tests

```bash
cd backtest-lab
python3 -m unittest discover -s tests
```

Tests use synthetic prices. They do not call Yahoo.
