# Best-returner books, free data

Window for every rerun below: 31 Dec 2014 through 15 Oct 2026. Starting capital $10,000. Rebalance is the year-end close. Sharpe uses daily returns, rf = 0, 252-day year. SPY is the adjusted-close total return on the same dates, rebased to $10,000. On this window SPY is **+353.7%**, **13.7% CAGR**, **−33.7% max drawdown**, 17.5% vol, Sharpe 0.82, final **$45,370**.

The current-list books were not rerun. They still end 14 Oct 2026 (SPY +354.4%, $45,442). One extra session does not change the comparison.

## Verdict

None of these books is investable as specified.

The point-in-time top five is the only return book whose construction is close to a research process: five names, a real membership file, and a result that still beats SPY after the data fixes in this note. It is a high-volatility backtest (34% vol, −38% drawdown), half the index is unpriced in the early 2000s, and about a quarter of members are still unpriced in 2014. That is a research result with a medium confidence band, not a fund.

The single-name point-in-time book compounds to about $4.1 million in the sample. The same twelve names come out after a stricter membership file. The path is one stock a year, 52% volatility, and a −51% drawdown. The sample win is not a mandate.

The size-floor books use today's listings. Delisted companies are absent. Dropping unverified prior-year moves above +1,000% flipped the $10 billion single-name book from a large loss, on the previous tape, to +600%. A result that moves that far when bad prints are removed is not a book to fund. The $2 billion single-name book loses to SPY and draws down 96%.

## Key table

| Book | Total | CAGR | Max DD | Vol | Sharpe | Final | Hole-weighted neutral | Hole-weighted worst | Confidence in the total |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| SPY | 353.7% | 13.7% | −33.7% | 17.5% | 0.82 | $45,370 | — | — | High as a reference |
| Best returner, current list | 6,687.9% | 43.0% | −80.1% | 62.7% | 0.88 | $678,792 | — | — | High as a biased replication |
| Best returner, point-in-time | 40,803.1% | 66.5% | −51.2% | 51.7% | 1.25 | $4,090,305 | 29,425.5% | 8,164.5% | Medium |
| Top 5, current list | 24,554.9% | 59.6% | −45.6% | 43.3% | 1.30 | $2,465,487 | — | — | High as a biased replication |
| Top 5, point-in-time | 3,554.9% | 35.7% | −38.2% | 34.3% | 1.06 | $365,495 | 2,840.0% | 638.5% | Medium |
| Best, ≥ $10B | 600.4% | 18.0% | −96.6% | 83.9% | 0.59 | $70,043 | 601.2% | 596.7% | Low |
| Top 5, ≥ $10B | 1,700.5% | 27.8% | −80.2% | 48.5% | 0.75 | $180,051 | 1,702.7% | 1,691.0% | Low |
| Best, ≥ $2B | −42.3% | −4.6% | −96.1% | 92.7% | 0.39 | $5,771 | −42.2% | −42.8% | Low |
| Top 5, ≥ $2B | 68.7% | 4.5% | −73.5% | 53.3% | 0.35 | $16,869 | 68.7% | 67.1% | Low |

Current-list rows are the prior run. They remain survivorship-biased by construction. Point-in-time and size-floor rows are this rerun.

Neutral replaces the missing fraction of each holding year with SPY. Worst replaces it with −100%. Excluded is the published total. For the S&P books the missing fraction is unpriced members over the snapshot count. For the size-floor books the missing fraction is only names that cleared the cap floor and were then dropped for an unverified +1,000% year. Delisted companies are not in that fraction, because they are not on the listing file. A size-floor bracket near the published total means the +1,000% rule removed few cap-clearing names. It does not mean the universe is complete.

A harsher rule, setting the single-name year to −100% whenever any member is unpriced, wipes out every point-in-time year, because coverage never reaches 100%. That bound is a wipeout. The hole-weighted column is the one to read. It is a weighted mix of the book that was actually held and a fill for the hole. It does not mean the missing name was the winner.

## What changed in the tape

Membership from 1996 is the fja05680/sp500 snapshot file (`universes/sp500_constituents_fja.csv`), about 490–506 names at each year-end. Wikipedia's selected-changes table still supplies bankruptcy notes and the changes after the file's last row (18 Aug 2026). A retired ticker is priced from the later ticker only when the snapshot shows a same-day handoff and a second fact agrees: Wikipedia dates that company well before the symbol change, or (from 2019) Wikipedia has no row calling the day an acquisition, spin, or index swap. Twitter to Arch Capital is not linked. Facebook to Meta, and CTL to Lumen, are.

A name that leaves the index and later returns keeps its prices across the gap when the series already has a print before the exit. A series that starts only after the exit is treated as a recycled ticker and the gap stays blank. AMD's 2017 return is about +80% on that rule. NRG was about +134% that year, so AMD still does not take the 2018 book. The single-name point-in-time path is the same twelve holdings and the same $4,090,305.

The top-five path changes in two years. The 2016 book replaces ACE with VeriSign, because the snapshot does not carry ACE as a 2015 member. The 2021 book holds LB, the ticker in the index at the 2020 close, and keeps L Brands' prices through the Bath & Body Works rename instead of stopping on the symbol change. That year's book return is +62.5%, the same as the prior Bath & Body Works holding. The top-five total moves from +3,633% to +3,555% because of the 2016 name, not because of LB.

Prior-year returns above +1,000% are dropped. Yahoo's close and adjusted close are one split adjustment, not two sources. Stooq, Tiingo, Alpha Vantage, Nasdaq's historical API, and archive.org did not provide a confirming print (see below). The dropped set includes NIO, AMC, RGC, and the other names above the line on the size-floor tapes. No point-in-time S&P winner in this window was above the line. The $10 billion single-name book was −66% before the drop and is +600% after it. The $2 billion single-name book was −93% and is −42%.

Wikipedia's change notes name the buyer and do not state a per-share cash price (0 of 411 rows). Where a note does say "$X per share," that price is used as the exit only if no print exists on or after the removal. It cannot create a ranking return by itself. Acquired names that still have a last Yahoo print exit at that print, carried to year-end.

## Coverage

Point-in-time price coverage is priced members over snapshot members. The signal year is the year whose return picks the book.

| Signal year | Members | Priced | Coverage |
| --- | ---: | ---: | ---: |
| 2000 | 492 | 244 | 49.6% |
| 2005 | 497 | 282 | 56.7% |
| 2010 | 497 | 340 | 68.4% |
| 2014 | 499 | 373 | 74.7% |
| 2015 | 502 | 384 | 76.5% |
| 2016 | 506 | 407 | 80.4% |
| 2017 | 505 | 420 | 83.2% |
| 2018 | 505 | 433 | 85.7% |
| 2019 | 505 | 445 | 88.1% |
| 2020 | 505 | 454 | 89.9% |
| 2021 | 505 | 464 | 91.9% |
| 2022 | 503 | 475 | 94.4% |
| 2023 | 503 | 479 | 95.2% |
| 2024 | 503 | 484 | 96.2% |
| 2025 | 503 | 492 | 97.8% |

2000–2010 in the table is the event-study universe. 2014–2025 is the book window.

The size-floor file is 5,369 current common stocks. Share of those listings with a usable prior-year return runs from 46% (signal year 2014) to 86% (2025). Names that also clear the cap floor: 472 to 813 at $10 billion, and 1,063 to 1,760 at $2 billion. Delisted names are not in the file.

## Event study since 2000

Finished next years only: 25 signal years, 2000 through 2024. The live year is WDC, +284% in 2025 and +112% from the 2025 close through 15 Oct 2026. It is in the chart and out of the rates.

Every one of those 25 winners, and all 125 top-five slots, has a next-year print. Filling a missing next year with −100% or with SPY therefore changes nothing. The hole is in the signal year, in the coverage table above, not in the winner's subsequent return.

| Sample | N | Median | Mean | Beat SPY | Below −30% | Below −50% |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| #1 next year | 25 | +5.9% | +20.6% | 44% | 20% (5/25) | 20% (5/25) |
| Top 5, each name | 125 | +8.2% | +17.0% | 47% | 16% (20/125) | 7.2% (9/125) |

The mean is above the median because a few next years are very large. The hit rate versus SPY is below half. The #1 name's worst next years:

| Winner | Signal year | Next year |
| --- | ---: | ---: |
| NVDA | +308% in 2001 | −83% |
| El Paso | +571% in 2009 | −68% |
| NOV | +140% in 2007 | −67% |
| NFLX | +219% in 2010 | −61% |
| El Paso | +639% in 2003 | −59% |

Confidence: low before 2011 (about half the members have no Yahoo history), medium from 2015 (coverage 77% and rising, membership within a few names of 500).

## Data caveats

These sources were attempted from this environment and did not add delisted prices:

- Stooq's CSV endpoint returns a browser proof-of-work. Solving it on one connection sets a cookie, and the following price request returns "Access denied." No delisted name was filled. The books log that.
- Tiingo's price API returns 403 without a token. Alpha Vantage's demo key refuses a normal symbol. No Tiingo, Alpha Vantage, Polygon, or Nasdaq Data Link key is configured.
- Nasdaq's quote API reports Celgene as unknown and returns zero historical rows for NIO.
- archive.org has no stored Stooq CSV for these symbols. A public macrotrends download returns 403.
- Kaggle is not logged in. GitHub searches for a free delisted-price tape returned no usable OHLCV file. fja05680/sp500 is constituents, which this study now uses, not prices.
- Yahoo returns a real 404 for names it has dropped (Celgene, Time Warner, Monsanto, Allergan, Raytheon). Those are empty on purpose, not a missed retry.

213 listings whose split-adjusted high is above $20,000 while the latest print is under $10,000 are dropped on the size-floor books. That filter is one Yahoo series failing a sanity check. It is not a second vendor. Berkshire Hathaway class A stays, because the latest print is still hundreds of thousands of dollars.

Market cap is split-adjusted close times a Yahoo share count. It is an approximation. A cap above $15 trillion is ignored. Single-name size-floor holdings such as USBC and JBIO sit on that approximation. Treat their selection as low confidence even when the printed cap clears $10 billion.

Sharadar SEP / SF1 / TICKERS would replace the membership file, the delisted prices, and the cap approximation. This study does not claim to be survivorship-bias-free.

## Bottom line by book

- **Current-list best and top five.** A clean replication of a biased screen. The totals are high confidence as arithmetic on today's S&P 500. They are not an estimate of what the screen would have earned.
- **Point-in-time best returner.** Same winners after the stricter tape. Medium confidence in the $4.1 million sample result. The book is one name, the drawdown is −51%, and early-window coverage is poor. Do not allocate.
- **Point-in-time top five.** The credible free-data version of the idea. +3,555% versus SPY +354% in this window, with a −38% drawdown and a hole-weighted worst case of +638%. Medium confidence. Still a research backtest: one decade, a censored price tape, and no costs beyond the model's bps. Do not allocate as a standalone product.
- **$10 billion and $2 billion screens.** Survivorship-biased, cap-approximated, and sensitive to the unverified-print rule. Low confidence. The $10 billion top five is the least fragile of the four and still draws down 80%. Do not allocate.
