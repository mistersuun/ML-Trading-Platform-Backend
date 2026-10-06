# Famous-investor vehicles: priced from IBKR daily bars

*Research note, 2026-10-06. Not investment advice. Data: IBKR MCP `get_price_history`, ONE_DAY bars, FIVE_YEARS, read-only. Last bar 2026-10-05 (PSH/PSHD 2026-10-06, delayed 15 min). The analysis script is `analyze.py` in the session scratchpad (`vehicles/`); the method is below.*

These are the listed vehicles through which an ordinary account can literally own a famous investor's book (holding companies, an investment trust, and ETFs that copy 13F or congressional filings). All numbers are **price return only**. Dividends are excluded because the bar feed does not adjust for them (only NANC returned a corporate-actions list, about 0.4 USD of dividends in 3 years, which is not added in).

## Table

| Symbol | Vehicle | Investor | Venue | Ccy | 1y ann | 3y ann | 5y ann | Max DD (5y) | DD trough | Ann vol | Corr to SPY | Beta to SPY | First bar | Bars |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| BRK.B | Berkshire B | Buffett/Abel | NYSE | USD | 1.1% | 13.5% | 12.2% | -26.6% | 2022-10 | 17.2% | 0.58 | 0.58 | 2021-10-08 | 1252 |
| PSHD | Pershing Sq Holdings (USD line) | Ackman | LSE | USD | -21.1% | 11.4% | 5.5% | -31.9% | 2022-06 | 25.5% | 0.37 | 0.55 | 2021-10-08 | 1275 |
| PSH | Pershing Sq Holdings (GBP line) | Ackman | LSE | GBP | -19.8% | 8.4% | 6.1% | -28.5% | 2026-09 | 23.4% | 0.32 | 0.44 | 2021-10-08 | 1260 |
| FFH | Fairfax Financial | Watsa | TSX | CAD | -9.7% | 23.9% | 33.4% | -19.5% | 2026-06 | 24.1% | 0.31 | 0.43 | 2021-10-08 | 1252 |
| MKL | Markel Group | Markel/Gayner | NYSE | USD | -11.0% | 5.7% | 6.4% | -28.9% | 2022-09 | 22.5% | 0.43 | 0.56 | 2021-10-08 | 1252 |
| L | Loews | Tisch | NYSE | USD | 3.9% | 18.9% | 13.0% | -26.3% | 2022-09 | 19.2% | 0.51 | 0.57 | 2021-10-08 | 1252 |
| GLRE | Greenlight Re | Einhorn | NASDAQ | USD | 17.1% | 9.7% | 14.4% | -23.7% | 2026-09 | 26.7% | 0.30 | 0.47 | 2021-10-08 | 1252 |
| BH | Biglari Holdings B | Biglari | NYSE | USD | 10.1% | 32.7% | 16.8% | -48.0% | 2026-05 | 39.3% | 0.32 | 0.73 | 2021-10-08 | 1252 |
| GURU | Global X Guru Index ETF | 13F hedge-fund top picks | NYSE Arca | USD | 13.7% | 24.7% | 6.9% | -38.5% | 2022-10 | 20.6% | 0.89 | 1.07 | 2021-10-08 | 1252 |
| NANC | Subversive Dem. Congress Trading ETF | Congress (Dem tilt) | Cboe BZX | USD | 18.1% | 25.7% | n/a (SI 22.3% / 3.7y) | -20.9% | 2025-04 | 16.7% | 0.96 | 1.07 | 2023-02-07 | 917 |
| SPY | SPDR S&P 500 | benchmark | NYSE Arca | USD | 15.7% | 22.2% | 12.1% | -25.4% | 2022-10 | 17.2% | 1.00 | 1.00 | 2021-10-08 | 1252 |
| QQQ | Invesco QQQ | benchmark | NASDAQ | USD | 25.2% | 28.2% | 16.0% | -35.6% | 2022-12 | 22.9% | 0.95 | 1.27 | 2021-10-08 | 1252 |
| XIC | iShares Core S&P/TSX Capped | benchmark | TSX | CAD | 16.6% | 23.0% | 11.8% | -18.0% | 2022-10 | 13.4% | 0.78 | 0.61 | 2021-10-08 | 1252 |
| ACWI | iShares MSCI ACWI | benchmark | NASDAQ | USD | 15.4% | 20.8% | 9.7% | -27.7% | 2022-10 | 16.3% | 0.97 | 0.92 | 2021-10-08 | 1252 |

| Symbol | 1y vs SPY | 3y vs SPY | 5y vs SPY |
|---|---|---|---|
| BRK.B | -14.6 pp | -8.7 pp | +0.1 pp |
| PSHD | -36.8 pp | -10.8 pp | -6.6 pp |
| PSH | -35.5 pp | -13.8 pp | -6.0 pp |
| FFH | -25.4 pp | +1.7 pp | +21.2 pp |
| MKL | -26.7 pp | -16.5 pp | -5.7 pp |
| L | -11.8 pp | -3.3 pp | +0.9 pp |
| GLRE | +1.4 pp | -12.5 pp | +2.3 pp |
| BH | -5.6 pp | +10.5 pp | +4.7 pp |
| GURU | -2.0 pp | +2.5 pp | -5.2 pp |
| NANC | +2.4 pp | +3.5 pp | n/a |
| QQQ | +9.5 pp | +6.0 pp | +3.8 pp |
| XIC | +0.9 pp | +0.8 pp | -0.3 pp |
| ACWI | -0.3 pp | -1.4 pp | -2.4 pp |

`*` marks a window shorter than the stated period. Max drawdown and volatility are over the whole available history (5y for all but NANC, which has 3.7y). "Corr/Beta to SPY" use daily log returns on dates both series have a bar.

## What stands out

- **Nothing here reliably beat SPY over 5 years on price.** Only FFH (+21 pp a year, CAD price, helped by a 2022 to 2025 insurance and investment re-rating), BH (+4.7), GLRE (+2.3, off a low base), L (+0.9) and BRK.B (+0.1, a tie) are at or above SPY. PSH, MKL and GURU trail SPY by 5 to 7 pp a year. Equal to SPY is the typical outcome, which matches the existing note in `famous-investors.md`.
- **The last 12 months were poor for the holding companies.** 1y annualised return: PSHD -21%, MKL -11%, FFH -10%, BRK.B +1%, versus SPY +16%. The "own a great investor" trade has lagged a tech-led market. This is a one-year window and should not be over-read.
- **Berkshire and Loews are the low-risk ones**: volatility 17-19%, drawdown -26% (same as SPY's -25%), correlation to SPY about 0.5-0.6. They diversify a bit, but they did not cut the 2022 drawdown.
- **FFH, GLRE, BH, PSH have low SPY correlation (0.30-0.37)**, so they behave like separate bets. BH is the most volatile (39% vol, -48% drawdown). FFH has the best 5y number but also a -19.5% drawdown in 2026 and -10% over the last year.
- **GURU and NANC are just SPY with extra beta.** Correlation 0.89 and 0.96, beta 1.07. GURU gave 6.9% a year over 5y versus 12.1% for SPY, with a deeper drawdown (-38.5%), so copying many managers' 13F top picks did not pay. NANC is only 3.7 years old; it matches QQQ-like growth (3y 25.7% vs SPY 22.2%) at lower volatility than GURU, but its edge over SPY is small and mostly tech tilt.
- **QQQ beat everything over 1y, 3y and 5y**, so any vehicle with a tech tilt looks good against SPY in this window. Compare the vehicles to QQQ before crediting the investor.

## Method

- Contract ids come from `search_contracts`; the primary listing is used. Bars are `ONE_DAY`, `outside_rth=false`, `FIVE_YEARS`; 1252 bars for most symbols, first bar 2021-10-08.
- 1y/3y/5y annualised return: `(last / close at-or-before (last date - N years)) ** (1/years) - 1`, using actual elapsed years. 5y uses the first bar (4.99y).
- Max drawdown: worst `close / running peak - 1` on daily closes. Volatility: standard deviation of daily log returns times sqrt(252).
- Correlation and beta to SPY: daily log returns on common dates (consecutive common dates when one calendar has holidays the other lacks).
- Raw payloads saved in the scratchpad `vehicles/<SYMBOL>.json`. US-listed raw payloads are also in `state/external_bars/` (`BRK_B.json` for BRK.B, `MKL`, `L`, `GLRE`, `BH`, `GURU`, `ACWI`; `SPY` and `QQQ` already existed and match the new pull exactly).

## Caveats

- **Currency.** FFH and XIC are TSX lines in **CAD**; PSH is the LSE **GBP** line; returns are in local currency and say nothing about FX. A USD investor in CAD or GBP lines also takes currency risk. The SPY comparison for those rows is therefore not like for like (XIC vs SPY is the fair TSX comparison; XIC trailed SPY by under 1 pp at each horizon).
- **PSH vs PSHD.** IBKR lists `PSH` (conid 274797523) and `PSHD` (170204225), both on LSE. PSHD trades at about 1.33 times PSH, which is the GBPUSD rate, so PSH is treated as GBP and PSHD as USD. This is an inference from price levels, not a field in the feed. The USD line is the one to use for a USD account. The correlation to SPY for LSE lines is understated because London closes before New York (non-synchronous trading).
- **Price return only.** BRK.B, MKL and BH pay no dividend; L, FFH, GLRE and the ETFs do. Totals would be higher for L, FFH, GURU and ACWI; the gap to SPY changes by roughly their yield (SPY itself yields about 1.2%). The feed is close prices as printed, not dividend-adjusted.
- **BRK.B was not found by `search_contracts`** (empty results for "BRK B", "BRK.B", "BERKSHIRE HATHAWAY"). The known conid 72063691 was used; the price history is consistent with BRK.B (504.26 last close). Treat the identity as likely but verify before trading.
- **KRUZ not found.** `search_contracts` returned nothing for "KRUZ" and "Unusual Whales", so no data. KRUZ is excluded from the table.
- **NANC data is a transcription.** The first NANC response arrived inline and was not auto-saved. It was rebuilt from that response as close-only (917 bars; dates from the SPY calendar minus 2024-12-30, which NANC lacked). It is not a full OHLCV payload and is **not** in `state/external_bars/`. Re-pull NANC if it is needed for a backtest.
- **XIC and FFH first attempt with `exchange=TSE` returned "No market data permissions"**; the same call without an exchange (SMART) worked, but the data is flagged delayed (900 s). Delayed data does not matter for daily bars.
- **Small sample and regime dependence.** 5 years starts in Oct 2021, just before the 2022 drawdown. Rankings change if the window moves a year. 1y numbers are noisy.
- **Survivorship and selection.** The list was chosen because these are well-known names. That is a hindsight-friendly list.
- **No order was placed and no account data was read.** This is research only.
