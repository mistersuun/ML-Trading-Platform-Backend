# Quant playbook, October 2026: who really makes 15-20% a year, ICT, strategies, risk like a quant position trader, Bayes, models, news

*Lead-quant synthesis, 2026-10-11. Research for the owner, not personal financial advice. Nothing in the repo was changed except this file, and nothing was committed.*

**What this builds on.** It does not repeat [`strategy-evidence.md`](strategy-evidence.md), [`model-review-2026-10.md`](model-review-2026-10.md), [`famous-investors.md`](famous-investors.md) or [`famous-investor-vehicles.md`](famous-investor-vehicles.md); read those for the background. This note combines seven worker reports (ICT, strategies, accounts, trades/book construction, Bayes/risk, news, models) with two adversarial fact-check reports. **Every correction from the fact-checks has been applied**, refuted claims are dropped, and claims that could not be checked are labelled. The worker notes, scripts and raw IBKR pulls are in the session scratchpad `playbook/` folder (`accounts.md`, `strategies.md`, `trades.md`, `ict.md`, `bayes.md`, `news.md`, `models.md`, `factcheck.md`, and the `vehicles/`, `factcheck/`, `bayes_scripts/`, `models_scripts/` subfolders). They are not committed.

**How sure each number is**

| Label | Meaning |
|---|---|
| **(IBKR)** | Computed this session from read-only IBKR price bars of a public fund, ETF or stock |
| **(page)** | Read on the manager's or author's own page or PDF |
| **(snippet)** | Seen only in a search-result summary, because the page could not be fetched |
| **(memory, unverified)** | Not checked this session |

WebFetch failed on most domains, sec.gov and dataroma were blocked, and the web-search budget ran out before this synthesis. A few items therefore stay at snippet level.

**Hold-out discipline.**
- No trading rule's return was computed on the platform's own bars (`state/external_bars`), by me or by any worker.
- The only platform-bar numbers here are **event counts before 2025-07-01**: ICT events, overnight gaps, and the D21 draft's counts, which were already in the repo.
- Public funds, ETFs and stocks were priced from IBKR to check managers' records, which the brief allows.
- `state/holdings*.csv` and `state/ibkr_snapshot*.json` were not read.
- Only the read-only IBKR tools were used: `get_price_history`, `search_contracts` and `get_price_snapshot`.

---

## 1. Summary (10 lines)

1. **How much risk 15-20% a year needs.** Long-run compounded growth can never exceed the cash rate plus half the Sharpe ratio squared, even at the "optimal" (Kelly) leverage. With cash at about 4% *(memory, unverified)*, a steady 15% needs a true Sharpe of about **0.55**, and 20% needs about **0.65**. Both assume half-Kelly risk, which means about 27-33% annual volatility and drawdowns of 40-50%. Diversified long-only portfolios run at a Sharpe of about 0.3-0.5. The platform's core should be expected to make high single digits.
2. **Five-year records are mostly noise.** At 20% volatility, the standard error of a 5-year average return is about 9 points a year. A true 11% strategy shows "20% a year" about one time in six.
3. **Verified (IBKR-priced) 5-year records at 20% or more:** only one manager vehicle, **Fairfax (FFH.TO, +36% a year total return in CAD)**. Most of that came from a one-off re-rating; book value grew about 19-21% in 2025. At 15% or more: **Biglari (+16%, -48% drawdown)**, and **Contrafund at about 15.3%**, which is borderline and still trails QQQ. **Kinsale is dropped**: its 15.8% only appears with an October 2021 start.
4. **Most 20%+ results in this window were sector or currency exposure, not skill:** semiconductors (+38%), gold miners (+26%), energy (about +22%, depending on the window), Japanese trading houses (+34% in yen, +25% in USD). Even plain **QQQ made about 19.8% a year in CAD**, because the US dollar rose against the Canadian dollar.
5. **Persistence is close to zero.** Outside small caps, essentially no top-quartile US domestic equity fund of 2021 stayed top-quartile through 2025 (SPIVA). The quant funds that made about 20% over 5 years (AQR Style Premia 21.9%) made only 6-9% a year over 10 years, using about **12x gross notional** long and short.
6. **ICT/SMC: rejected, with more evidence than before.** Three independent mechanical tests in 2026 are all null. One daily-bar rule (a 20-day liquidity sweep, i.e. Turtle Soup) is specified, but it is about 10x too weak a test, and 64% of its events overlap the already-registered RSI(2) rule. The best use of it is an advisory split of an existing rule, not a new rule.
7. **The best evidence is for how quant position traders manage risk, not for a new signal.** That means volatility-scaled diversified trend, equal risk per asset class, correlation-aware caps, no-trade buffers and drawdown floors. The investable version of what they run is a managed-futures ETF (DBMF/KMLM): about 6-10% a year, but **+21-30% in 2022** when SPY fell 18%.
8. **Two real gaps in the platform's risk code** (both verified in source):
   - The 4% heat cap is not scaled by the drawdown ladder, so correlated stop-outs at an 8% drawdown can push past the 10% halt.
   - `resume()` resets the peak, so repeated halt-and-resume cycles have no lifetime floor.
9. **Bayes.** With a realistic skeptical prior, forward paper trading cannot confirm a retail-TA pattern on any human time scale. The source of a strategy (its prior) and the long-history run (D22) matter far more than the forward week. Build a Bayesian scoreboard *next to* the deflated-Sharpe gate, not in place of it.
10. **Models and news.**
    - The one credible model is a **monthly cross-sectional ETF ranker used as a small tilt**. The prior that it beats a no-fit momentum/low-volatility baseline is only 15-25%.
    - News/LLM sentiment has about zero expected alpha for a long-only mega-cap book. Use news for **risk flags** (earnings and attention vetoes), archived forward only.
    - The roadmap respects D20: nothing changes before 2026-10-15, the D22 long-history run comes first, and every new rule is paid for in N.

---

## 2. Accounts: who actually made 15-20%, and what can be copied

### 2.1 Verified records (priced from IBKR, 2021-10-11 to 2026-10-09, about 5.0 years)

Returns are annualised. TR means total return: dividends and distributions added back, split-adjusted. The fact-check re-pulled 13 of these independently, and its numbers are used where they differ. Currency is local unless stated.

| Vehicle | Manager / exposure | 5y a year | Max DD | Robust to start date? | How to follow, lag | What we can copy |
|---|---|---|---|---|---|---|
| **FFH.TO** | Fairfax (Watsa) | **+34.3% price, +36.2% TR (CAD)**; +32.7% TR in USD | -18.9% (2026-06) | Yes: 31.8-39.7% for starts 3-12 months later | Buy directly in the CAD account; lag 0 | Book value per share +18.9% in 2025 (+20.5% with dividend) (page/snippet). **Most of the 5y return is a re-rating that cannot repeat.** TR over the last year -7.7%; 14% below its peak. Size it as one stock (5% or less) |
| **BH** | Biglari Holdings | +16.4% (no dividend) | -48.0% | Yes, and if anything understated: later starts give 23-31% | US single stock; watchlist | One volatile owner-operator stock (about 39% volatility); not a process |
| KNSL | Kinsale (insurer) | 15.8% | -46.8% | **No: 4.7-10.3% for later starts** | — | **Dropped from the 15% tier** (start-date artefact) |
| FCNTX | Fidelity Contrafund | **about 15.3% TR** (the first pass missed a 2024-12-06 distribution) | — | Fidelity's own figure is 14.71% for 5y to 2026-06-30 (snippet) | US mutual fund; likely not buyable from a CAD account (memory, unverified) | Borderline 15%. **Trails QQQ (16.7%)**, so the "skill" is a growth tilt |
| BPTRX | Baron Partners | about 12% (calendar years 2021-25 compound to 12.3%) | about -45% (2022: -42.6%) | — | — | A snippet claiming 25.5% contradicts the fund's own calendar years and is rejected |
| Akre Focus | Akre | 2.83% vs S&P 12.06% (to 2026-03-31) (snippet) | — | — | — | Former star; reversed |
| SMVLX / SEQUX / POLRX / ARKK | Smead / Sequoia / Polen / ARK | 8.8% / about 6% / about 0% / -4.6% | ARKK -76% | — | — | The 2016-21 winners became the 2021-26 laggards |
| BRK.B, MKL, PSHD | Berkshire, Markel, Pershing (USD line) | 12.2% / 6.4% / 5.5% price | -27% / -29% / -32% | — | Direct; lag 0 | Prior note, to 2026-10-05. Roughly SPY or worse |
| CSU.TO, BN, EXO, DHR, BOC | Listed compounders | 7.6% / 3.8% price (2022 spin-off not added back) / 2.2% / -3.5% / -19.2% | CSU -56%, BOC -70% | — | TSX for CSU and BN | Holding a past compounder is not a 15% strategy |
| **GURU** | ETF of 13F top picks | about 6.2-6.9% | -38.5% | — | — | **Copying 13F filings in bulk lagged SPY (about 12%)** |
| **DBMF** | Managed-futures ETF | **10.4% TR**; 2022 +21.5% | -20.4% | **No: October 2021 is its best start; later starts give 5.0-7.6%** | US ETF; daily; lag 0 | The investable form of a CTA (trend-following futures fund) book. A crisis diversifier, not a 15% engine |
| KMLM | Managed-futures ETF | 5.7% TR; 2022 +30.5% | -22% month-end / about -28% daily | — | US ETF | As above; more volatile |
| AQMIX | AQR Managed Futures | about 13.7% for this window; AQR page 12.53% for 5y to 2026-03-31 (snippet) | about -14% | Daily data could not be re-pulled | US mutual fund | Benchmark only |
| QQQ | Nasdaq-100 (passive) | 16.0% price, 16.7% TR (USD); **about 19.8% in CAD** | -35.1% | — | TSX equivalents exist | **The bar every "great manager" must clear.** Compare in the same currency |
| SMH / GDX / XLE | Semis / gold miners / energy | 37.6% / 25.7% / about 22.5% TR | -45% / -47% / — | SMH yes. GDX: one year (+155% in 2025) does most of the work. XLE: no, 15.4-15.9% for shifted windows | ETFs | Sector bets that won this window. SPIVA logic says they are candidates for the next reversal |
| 8058.T | Mitsubishi Corp (a Berkshire holding) | +34.4% TR in yen; **+25.5% in USD unhedged** | -33.7% | — | Japanese listing | Berkshire funded it with yen debt, so its return was close to the yen figure. An unhedged CAD investor would not get that |

**Fund-page records (the manager's own page; returns to 2026-09-30, risk statistics to 2026-06-30; not IBKR-priced):**

| Fund | 5y | 10y | Notes |
|---|---|---|---|
| QSPIX (AQR Style Premia) | 21.92% | 8.89% | Volatility 12.3%, Sharpe 0.46, beta -0.01, **611.7% long / 593.3% short** |
| QMNIX (AQR Equity Market Neutral) | 19.64% | 6.56% | — |
| QMHIX (AQR Managed Futures HV) | 19.74% | 6.33% | — |
| AQMIX (AQR Managed Futures) | 15.04% | 5.44% | — |

- All four 5-year windows start right after AQR's 2018-2020 slump. **Quote the 10-year figure next to the 5-year one every time.**
- QMNIX's 5-year figure is front-loaded: year to date -2.16%, 1 year +3.70% (page).

### 2.2 Claimed records (press-reported, net, unaudited, not investable)

| Fund | Claimed 5y | Source quality | Copyable? |
|---|---|---|---|
| Citadel Wellington | about 20.5% a year for 2021-25: 2021 about 26%, 2022 about 38%, 2023 15.3%, 2024 15.2% (KBRA 15.21%; CNBC 15.1%), 2025 10.2% | All years now have snippet support; "people familiar" figures | **No.** Multi-strategy fund made of many separate trading teams, with high leverage. Copy the **risk process** (per-team drawdown stop-outs, volatility targets), not holdings |
| D.E. Shaw Composite | about 17.6% a year, **2022-25 (4 years)**: 24.7, 9.6, 18, 18.5 | Snippets (Bloomberg, Hedgeweek, Fortune) | No |
| Millennium | about 12% (2024 +15, 2025 +10.5) | Snippets | No |
| Bridgewater Pure Alpha | 2025 **about 33%** (the 34% figure has no source; "best year in 50 years" appears only on a blog); 2023 -7.6%; under 3% a year for 2012-24 | Snippets | No. Shows one big year sitting on a decade of near-zero |
| TCI (Hohn) | 2023 +32.7%, 2025 +27% (Forbes, snippet); 2024 not found; "18-20% since 2004" from Substack/Medium only (includes 2008 at about -43%) | Snippets / secondary | 13F covers only US longs; 45-day lag; already shadow-tracked |
| Renaissance RIEF | 2024 +22.7% (snippet); 2025 only partial-year figures | Snippet | No (qualified purchasers only) |
| Man AHL Diversified | 2021-25: +2.9, +12.5, -3.5, -0.2, about +5.3 to +5.7 (snippets conflict) | Snippets | — |
| SG CTA Index | 2022 +20.1%, 2023 about -3%, 2024 +2.4% (snippets); 2025 not verified | Snippets | Investable only through CTA ETFs |
| Whale Rock "copy portfolio" | 198% over 3 years to Q1 2026, HedgeFollow method (top-20 13F longs only) | Snippet | **No.** It is the AI/semiconductor run. 13F copy rankings are 3-year windows |

**Dropped or rejected:**
- Slate Path "188%": no source found.
- Punch Card "27% annualised": a paywalled headline with no period. The verified figure is **14.5% net for 2004-2011**.
- WhaleWisdom's "Appaloosa copy +1,241%": hindsight-selected marketing with no costs.
- The SPIVA "0.46%" figure: secondary (ETF Trends). The scorecard itself says that, small caps aside, **no** top-quartile domestic fund stayed top quartile. Above-median persistence for large caps was 4.5% vs 6.25% expected by chance (snippet).
- Australian proxy data: 31% stayed top quartile vs 25% by chance over non-overlapping 5-year periods, and 11-15% of funds were merged or closed (snippet).

**Corrections to the earlier notes:**
- Duquesne's largest Q2 2026 position is **Natera (16.6%)**, not TSM, which is second at 5.4%. Duquesne holds **85 issuers**, not about 60 or 95.
- Berkshire's Q2 2026 13F has 26 issuers. GOOGL is **12.6%**, not 9.4%. The top five are 71.9%; effective N is 8.1.

### 2.3 How their books are built, as rules the platform could code

These come from 433 13F filings (2021Q1-2026Q2) plus primary documents (`trades.md`). 13F artefacts inflate turnover for the low-turnover managers: CUSIP changes, confidential positions, redemptions, and partial books (Fairfax's 13F is about $2.6bn of a $70bn portfolio).

**Concentrated, low-turnover compounders** (Berkshire, TCI, Himalaya, Punch Card, Akre, Pershing):

| Measured | Value | Codeable rule (only for a separate "clone/quality" sleeve; none exists today) |
|---|---|---|
| Effective N | 3-10 (Berkshire period average 5.0, Q2 2026 8.1) | 8-12 names, effective N ≥ 6, top-5 weight ≤ 80% |
| Single-name cap | PSH policy: ≤ 25% of gross assets **at time of purchase** (page) | Entry cap 20%; allow drift to 30%; trim back above it |
| Turnover | about 5-15% a year one-way (Pershing 11-68%) | One-way turnover budget ≤ 25% a year; review weekly, act quarterly |
| Scaling in | 81-95% of new names start below 2% weight (Pershing is the exception) | Open at one-third of target; step to full over 2 rebalances if the rule still holds |
| Entry timing | median 20.5% below the 12-month high at entry (n = 14; **style, not evidence of edge, no base rate**) | Only for a pre-screened quality watchlist: enter at least 15% below the 12-month high |
| Adds and trims | Akre adds after lagging the book (81% of adds; median -8.4%); Fairfax and Punch Card trim after strength | Band rebalancing toward target. **Never in a momentum sleeve:** Altimeter and Duquesne do the opposite |
| Exits | Pershing exits all at once on a broken thesis (9 of 11); Berkshire trims big winners over quarters (AAPL -75% over about 6 quarters) | Thesis or signal break: full exit at the next rebalance. Size or valuation: trim in 2-4 steps |
| Cash | Residual with a floor (Berkshire buybacks stop below $30bn; PSH has no cash limit) | 2-5% T-bill floor, no maximum |
| Complexity | PSH sizes down levered or complex businesses (page) | 0.5x weight for net debt/EBITDA > 3, negative free cash flow, or banks, insurers and holding companies |

**Quant trend followers** (AQR, Winton, Man AHL, Turtles, Carver):
- They target about 10% portfolio volatility and size each market inversely to its volatility.
- They set risk budgets per sector or asset class: Winton's equities 20, FX 20 and bonds 19 *(due-diligence report, page)*; Man about 25% per asset class *(snippet)*.
- Winton caps margin per market and uses no stop losses, with an average holding period of about 4 months.
- The Turtles capped risk units per correlated group *(secondary sources)*.
- Carver trades only when the target moves more than 10% of an average position, and cuts all positions when estimated risk exceeds 2x target (normal), 4x (worst-case correlations) or 6x (extreme volatility) (page).

These become the platform design in section 5.

**Correction to `trades.md`:** its Turtle-unit rule says "6 units = 3% of the 5% heat". The platform's heat cap is **4%** (`config.MAX_PORTFOLIO_HEAT_PCT = 0.04`, D5). Six 0.5% units are therefore 75% of the cap, not 60%.

**Not recommended:**
- Druckenmiller-style "bet big" conviction sizing, which needs the skill being sized.
- Cloning Duquesne or Altimeter adds (50-106% a year turnover against a 45-day lag).
- CTA-level leverage on a long-only book.

---

## 3. ICT / Smart Money Concepts

### 3.1 Verdict per concept

| Concept | Verdict | Evidence (corrected) | Daily-bar fit |
|---|---|---|---|
| Fair value gap (FVG) | **F** | **Tangirala 2026** (GitHub README, page): 44 detectors × 8 horizons = 352 tests on 496 S&P 500 stocks, 2010-2026; **0 of 352** beat a ticker-matched random entry after BH-FDR. **StatOasis, SPY:** n = 1,122, 5-day edge -0.005%, t = -0.08 (page). **MPM:** about 40,000 futures FVGs. Price reacts about 5 points more than at random levels, but 0 of 5 constructions are profitable after costs. The apparent profit factor of 2.4 was look-ahead (it falls to about 1.0 when exits are resolved on 1-minute bars). **Platform counts:** 1,412 FVGs ≥ 0.25 ATR; 85% are overnight gaps; 46% fill fully within 10 bars | Codeable, but on stocks it is mostly gap-fill. Specified as R2 below; **not recommended** |
| Liquidity sweep / stop hunt (= Turtle Soup) | **D** | Osler (NY Fed SR150): stops do cluster, but cascades (continuation) are as likely as reversals. Tangirala: sweeps are the best ICT detector at +11-12 bp over 10 days, p = 0.23/0.29, not significant; his median minimum detectable edge is 45 bp, so small edges are not ruled out. StatOasis SPY: n = 547, +0.119%, t = 0.94 | The only concept that fits daily bars. R1 below |
| Order block / breaker | **F** | StatOasis SPY: n = 736, +0.121%, t = 1.22. Variants "beat random" 55-82% of the time, which is consistent with buying dips in a rising market; none beat buy-and-hold | 3+ free parameters, no distinct hypothesis |
| Market structure shift / BOS / CHoCH | **F** | Part of Tangirala's null set. Platform count: sweep followed by MSS within 5 bars = 115 events on 81 dates | Too rare; swing confirmation risks look-ahead |
| Optimal trade entry / premium-discount | **F** | StatOasis SPY: n = 206, -0.028%, t = -0.17; 0 of 18 variants beat random on SPY. The one t > 2 in 32 tests (OTE, DIA, 10-day, t = 2.07) is the expected false positive | A relabelled Fibonacci rule or oscillator |
| Killzones, silver bullet, power of three (AMD) | **F / untestable** | No independent test with a null. The documented intraday effects point to continuation: Gao-Han-Li-Zhou JFE 2018 (snippet) | Intraday only; outside D3 |
| SMT divergence | **F / no evidence** | No public statistics at all (snippet) | SPY/QQQ/DIA are near-collinear |
| ICT trader records | **F** | No audited record for the founder or students. FPFX prop data: about 14% pass and **about 7% of all traders ever get a payout**, i.e. about 45% of those who pass (secondary, snippet) | Not a follow or clone candidate |

**Caveats on the evidence:**
- All three ICT studies are unrefereed: a preprint and two vendor research pages. For a null result that is acceptable.
- StatOasis is frictionless. Its "none beat buy-and-hold" partly reflects time exits against an always-invested benchmark.

### 3.2 At most two codeable rules (event counts only, 17 symbols, before 2025-07-01, after the 252-bar warm-up)

**R1 `sweep_reclaim20`** (ICT sell-side sweep = Connors-Raschke Turtle Soup)
- **Rule.** All on the signal bar:
  - The low pierces the prior 20-day low.
  - The close is back above that low.
  - The prior low was set at least 4 bars earlier.
  - The close is in the upper half of the bar's range.
  - The close is above the 200-day SMA.
- **Trade.** Enter at the next open. Use the platform's 2×ATR stop and 3×ATR target, a 10-bar time exit, and resolve a same-bar stop and target as stop first. Code is in `ict.md` §5.
- **Parameters.** The 20-day low and the 4-bar age rule are Turtle Soup's published values *(memory, unverified: check against Connors and Raschke, *Street Smarts*, 1995, before registering)*.
- **Counts:**
  - 20-day version (upper half, above SMA200, before applying the age rule): **154 events, 55 entry weeks**.
  - 10-day version: 293 events, 89 weeks.
  - **64% (98 of 154) fall within ±2 bars of a `rsi2_pullback` (P1) signal**. The 10-day version overlaps 65%.
- **Power.** The smallest detectable edge is about **1.5% per trade**, against about 0.12% in the literature, so the test is about 10x underpowered. The fact-check calls this optimistic, because events cluster in weeks. Expect a null.
- **N cost.**
  - As a standalone pooled pattern it is +1 trial (the D20 accounting goes 66 → 67 with D21, so 68 with R1). The required Sharpe rises by about 0.01.
  - The real costs are attention and a hold-out read.
  - **Recommended form: an advisory conditioning split inside the already-registered P1.** P1's trades would be reported split by "sweep yes/no". This adds no pattern and should not change the return series. It must be declared in a decision entry **before the D22 look**, and whether it costs N is settled there (D18 §6.1).

**R2 `fvg_retrace`** (fully specified in `ict.md`; **recommend NOT registering**)
- **Counts:** 997 retrace entries on 384 dates (748 above SMA200).
- **Why not:** 85% of these FVGs are overnight gaps, and three independent tests are null. It would only add 1 to N.

**Bottom line.** ICT is closed as a research line. R1 is the only daily-bar candidate, and it is cheapest as an advisory split of a rule already in the ledger.

---

## 4. Other strategies, ranked by evidence and fit

Grades follow `strategy-evidence.md` (A = replicated, survives costs ... F = fails), with the fact-check corrections applied.

| Rank | Strategy | Grade (this form) | Key evidence | Fit here |
|---|---|---|---|---|
| 1 | **Volatility-scaled, multi-speed trend (Carver EWMAC), long/flat, on the trend sleeve** | **C** (A in the long/short futures form) | Carver's test on 2 futures since 1982 (page): slow-trend Sharpe 0.49 vs 0.38 for 60/40, monthly skew +0.08 vs -1.44. **But that test is long/short.** Flooring the signal at zero removes most of the crisis payoff, which came from short bond and stock positions. Kim-Tse-Wald: most trend alpha comes from volatility scaling. Baltas-Kosowski: continuous signals cut turnover by more than a third (snippet) | High. A version change to `allocation.trend_signals`, tested on long proxy history and judged on drawdown, not return |
| 2 | **Hold a managed-futures ETF (DBMF/KMLM) as a diversifier** | **B** | (IBKR) 2022: DBMF +21.5%, KMLM +30.5%, SPY -18.2%. 5y: DBMF 10.4% TR (best start; 5-8% for later starts), KMLM 5.7%. AQMIX beta to the S&P -0.13 (page) | Medium-high for the real account. US-listed, so CAD conversion and withholding tax apply. **No TSX-listed managed-futures ETF was found** (IBKR `search_contracts` returned only US DBMF/KMLM and a French UCITS DBMF line; the web check could not be run) |
| 3 | **Mean reversion and trend as separate sleeves** | **C** | Platform pre-hold-out look: mean-reversion entries beat hold-matched random by +0.05 to +0.27 R; trend and breakout rules are explained by drift (model review §2.1). **arXiv 2006.07847 is dropped as support:** it studies reversal of long futures trends, not short-horizon equity reversion | Both pieces exist. The MR leg still needs D22 and D21 |
| 4 | Quality / defensive ETF tilt (QUAL, USMV) | C (risk tool) | Quality Minus Junk and the JKP replication: robust (snippet). (IBKR) 5y: QUAL 12.3%, USMV 7.5%, SPY 13.7% | Core-tilt decision only; test on index history, never on 5-year bars |
| 5 | Industry trend breakout (Zarattini-Antonacci) | **C, with a leverage caveat** | Concretum: 18.5% a year, Sharpe 1.46, 1926-2024. **The volatility targeting allows up to 2x exposure; the Columbia replication caps leverage at 2.0.** Its walk-forward fell short of market benchmarks (arXiv 2412.14361, page) | Medium-low. Unlevered, long-only, expect well under the 0.5-0.7 Sharpe first suggested |
| 6 | Bond carry/curve tilt (SHY/IEF/TLT) | C | Koijen et al.: strong in global long/short form; fragile as a single-country, long-only duration timer (snippet) | Low; core bond slice only |
| 7 | Residual momentum | B | Blitz-Huij-Martens: about twice the risk-adjusted profit of plain momentum (snippet) | Not testable without a broad point-in-time stock universe |
| 8 | Dual / absolute momentum (GEM) | D | Independent tests: the absolute filter does the work and the relative leg adds nothing (snippets) | Already covered by the trend sleeve |
| 9 | Volatility-risk-premium ETFs (SVOL, JEPI, BXM) | C, not recommended | (IBKR) SVOL 7.5% a year, about -33% at the April 2025 low (snippet); JEPI about 7.8% vs SPY 13.7% | Loses in the same crises where trend gains |
| — | Skip | D-F | Same-month seasonality (not testable); earnings premium and PEAD (gone outside microcaps: t = 1.43 excluding microcaps, Subrahmanyam 2025, page); sector momentum after 2000; pre-holiday (large caps since 1983, Ko 2021, page); overnight drift (NightShares ETFs liquidated); FX carry (no live ETF) | — |

### Pre-registration candidates (at most 3 across sections 3 and 4)

| # | Candidate | Family it lives in | N cost | Prior | When |
|---|---|---|---|---|---|
| C1 | **R1 `sweep_reclaim20` as an advisory split of P1 trades** (or standalone) | Pooled scanner | 0 as a split (to be confirmed by decision entry); +1 standalone | under 5% to pass the DSR | Declared **before** the D22 look |
| C2 | **Trend sleeve v2** = EWMAC 16/64, 32/128, 64/256, long/flat, forecast capped, inverse-volatility legs capped at 2/N, the O3 volatility cap (scaler ≤ 1), and a 10% trade-to-edge buffer. Carver's published defaults, no tuning | Allocation layer (D4 core version) | One allocation-layer trial. Bundling P5 inverse-volatility and the O3 cap into it pays once instead of three times | 50-60% that it cuts max drawdown or worst-12-month loss vs v1 at about the same return; under 20% that it adds return | After D22 is read (D20 item 7 parks allocation work) |
| C3 (reserve) | Industry trend (Zarattini-Antonacci) with published parameters, **leverage capped at 1.0**, tested on Ken French industry data first | Allocation layer | One allocation-layer trial | Low | Only if C2 is done and the owner wants it; otherwise leave the slot empty |

Allocation-layer trials do not enter the pooled scanner's N, but they need their own registered count and a single look. Keep that ledger in the decision log.

---

## 5. Portfolio construction and risk management "like quant position traders"

**What quant position traders actually do, in one paragraph.**
- Most of what they do is risk management.
- They size every position to equal risk, cap risk by correlated group, and target a portfolio volatility.
- They trade only when the target moves enough to matter, and cut everything when risk estimates blow out.
- They treat a drawdown as a capital-preservation trigger, not as evidence about the strategy.
- They do not size by conviction, and they do not let one good backtest set the weights.

This design maps that onto the platform. The layers stay as in D4: core about 90%, trend sleeve about 10%, signal sleeve 0% of real money (paper only, at most 5% later). Nothing below changes before 2026-10-15.

### 5.1 Design, mapped to modules

**Kind** says what sort of change each item is:
- **Advisory:** shown only, changes no series.
- **D4:** a core/allocation version decision.
- **D5:** a signal-sleeve risk version decision.

| # | Component | Module | Kind | Rule | Validation (no hold-out peeking) | Evidence |
|---|---|---|---|---|---|---|
| R1 | **Floor-aware heat cap** | `execution._size_buy` | D5 (blocks entries only) | `heat_room ≤ min(4%, 10% − drawdown − 0.75%)` of the sleeve. **Verified:** `heat_room` uses `MAX_PORTFOLIO_HEAT_PCT` without the ladder multiplier, so at an 8% drawdown with full heat open, simultaneous stops reach about 12%, past the halt | Unit tests: remaining heat is 1.25% at an 8% drawdown and 0 at 9.5%. Synthetic Monte Carlo of correlated gaps through stops: P(drawdown > 10.5%) about 0. No market data | A (Grossman-Zhou/CPPI logic) |
| R2 | **Lifetime capital floor** | `risk_manager.resume` | D5 | A fixed floor (for example 80% of `SIGNAL_SLEEVE_EQUITY`) that `resume()` cannot reset. **Verified:** `resume()` sets `peak_equity` to current equity | Unit tests on a halt → resume → loss → halt sequence | A |
| R3 | **Keep the drawdown ladder thresholds** | `config.DRAWDOWN_LADDER` | Documentation | The 5% → 0.5x, 8% → 0.25x, 10% halt ladder is a step version of the Grossman-Zhou policy for a 90% floor (exact values 0.53 and 0.22; arithmetic re-checked). Do not tune it | — | A |
| R4 | **Correlation clusters plus a correlation-aware heat cap** | `config.CLUSTERS`, `execution._plan` | D5 (blocks entries only) | Today QQQ and the mega-tech names sit in different clusters, and the universe-B ETFs have none. Build clusters by linkage at ρ ≥ 0.6 on Ledoit-Wolf correlations of **instrument** returns before 2025-07-01, then freeze them. Add `sqrt(r'Cr) ≤ 2.5%` next to the 4% linear cap (8 positions at 0.5%: 1.41% one-sigma heat at ρ = 0, 3.22% at ρ = 0.6) | Clusters built once and frozen; report blocked entries as counts only | B |
| R5 | **Turtle-style unit limits** (scaled to 0.5% units and the 4% cap) | `execution._plan` | D5 | At most 2 units per instrument, 3 per tight cluster (ρ > 0.7), 5 per loose group, 6 in total (3% of the 4% cap) | Unit tests | C (secondary sources) |
| R6 | **Inverse-volatility legs plus the O3 cap in the trend sleeve** | `allocation.target_weights`, `portfolio_backtester.trend_sleeve` | D4 (part of C2) | Today each leg is `TREND_SLEEVE_PCT / len(universe)` (verified). Use 63-day inverse-volatility weights, each ≤ 2/N, no leverage; sleeve weight = min(1, target/realised volatility) | `scripts/long_history_backtest.py` on 1990+ index proxies, parameters fixed once; accept on worst drawdown and worst 12 months | B (risk tool; Cederburg et al.: no real-time alpha) |
| R7 | **Risk budgets per asset class** (trend sleeve, later the signal sleeve) | `allocation`, `execution` | D4/D5 | US equity ≤ 35%, international equity ≤ 20%, bonds ≤ 25%, real assets ≤ 20% of sleeve **risk**; recalibrate if a share exceeds 1.5x its budget. The numbers are a judgment call (Winton/Man style) | Long-proxy backtest, judged on drawdown | B |
| R8 | **Shrinkage covariance everywhere; HRP only for clustering** | wherever risk is computed | Advisory | Ledoit-Wolf for every covariance. Out-of-sample evidence for HRP over inverse-volatility is mixed, so use HRP's clustering step (R4) and not its weights | Golden test on a synthetic covariance | B |
| R9 | **Risk-contribution table plus Black-Litterman reverse optimisation** | `allocation.Proposal.render` | Advisory | Show each core holding's share of risk and the returns implied by D4's weights, as a sanity check. Never trade on it. Black-Litterman *views* from signals would carry about zero weight (James-Stein keeps 0 at t = 1) | Golden test | B |
| R10 | **Fractional/Bayesian Kelly as a cap only** | `execution._size_buy` | D5 | Replace `qty × intent.confidence` with a multiplier ≤ 1 risk unit equal to quarter-Kelly of the *posterior* edge (section 6), only for gate-passing signals. Kelly never binds on the unlevered ETF book (full Kelly is 1.9-3.1x leverage at Sharpe 0.3-0.5). Half Kelly keeps 75% of the growth for 25% of the variance; P(ever halving) is 12.5% at half Kelly vs 50% at full (math re-checked) | Tests: uncalibrated confidence (Brier skill ≤ 0) gives a multiplier of 0; it can never exceed 1 unit | B |
| R11 | **No-trade buffer, trade to the band edge** | `allocation.propose_rebalance`; trend sleeve | D4 | Rebalance only beyond the band and only *to its edge* (pysystemtrade `buffer_trade_to_edge`; buffer 10% of an average position) | Long-proxy backtest: accept if turnover falls ≥ 30% with tracking error < 0.5% a year | B |
| R12 | **Liquidity flag** | `allocation.Proposal.render` | Advisory | Flag proposal trades > 1% of 20-day ADV or with spread > 20 bp. ZRR trades only about CAD 0.1M a day, so a CAD 7k order is about 7% of ADV: use a limit order (IBKR volume bars) | Unit test with a fake volume series | C |
| R13 | **Time stop** | `execution`/engine | Already D20 item 1 (N += 22) | 20 bars, not tuned | Judged on T_eff, concurrency and capped-replay Sharpe ratio | — |

**Explicitly not in the design:**
- Bandits or Thompson sampling for capital.
- Black-Litterman views from signals.
- HRP weights for the core.
- Volatility-targeting the core or levering with a diversification multiplier.
- Protective puts or tail funds. Bonds, gold, cash and trend are the cheaper hedges (Israelov 2019; Harvey et al. 2019).
- Tuning the ladder or the stop multiples.
- A SPY-200 regime filter on scanner entries.

**One interaction to settle first.** The pooled capped replay applies the D5 caps, so R1, R4 and R5 would change a gate input. Either keep the capped replay pinned to the registered caps until the next registered version (and disclose the difference), or fold the risk changes into one versioned commit. That is an owner decision.

### 5.2 What the risk numbers imply

- **Drawdowns are weak evidence about a strategy.** A 10% drawdown in one year favours "Sharpe 0" over "Sharpe 0.5" by only about 1.7 to 1 (synthetic, not re-run by the fact-check). So never retire or select patterns on ladder hits.
- **Profitable sleeves will still hit the halt.** At 8% sleeve volatility, a Sharpe-0.5 sleeve hits the 10% halt within 3 years 62% of the time; at 6% volatility, 34% (synthetic). Expect halts on good sleeves unless sleeve volatility is about 4-6%, and say so in D5.
- **Long-only trend cannot reach a 10% volatility target** without adding equity beta. Use the volatility target as a cap, never as leverage.

---

## 6. Bayesian layer: a strategy scoreboard that gates capital

**Purpose.** The deflated Sharpe ratio (DSR) answers "may this trade at all?". A Bayesian posterior answers "how much should I believe it, and how much should I bet?". Keep the DSR as the gate (D6, D15, D18 as built) and add the posterior next to it.

### 6.1 Design

1. **Skeptical priors, written into the decision log before any D22 number is read** (spike-and-slab: P(real) and, if real, the true annual Sharpe):

| Class | P(real) | Sharpe if real | Applies to |
|---|---|---|---|
| Retail TA / ICT | 0.05-0.10 | N(0.3, 0.25) | The 20 legacy patterns, R1 |
| Practitioner rule with a published form (Connors family) | 0.15-0.2 | N(0.3, 0.2) | `rsi2_pullback`, D21 `pullback_uptrend` |
| Published anomaly, after publication | 0.5 | in-sample Sharpe × about 0.42 (McLean-Pontiff decay) | `high52_breakout` (an adaptation, so lean lower) |
| Replicated, century-long factor | 0.8 | N(0.4, 0.2) | Trend sleeve, managed-futures ETF |

The middle row is my interpolation and should be confirmed by the owner. The others come from `bayes.md`, calibrated on McLean-Pontiff, Harvey-Liu and Jensen-Kelly-Pedersen.

2. **Likelihood.** Use the Sharpe estimate with the repo's own PSR moments and the Bartlett effective sample size T_eff (`stats/pooled.py`), not the naive SE.
3. **Posterior.** Report P(real), E[Sharpe] and P(Sharpe > 0.25 net) per strategy.
4. **Chaining into forward data.** The backtest posterior becomes the prior for forward paper data. Before chaining, **subtract the selection effect**: use the DSR's expected-maximum Sharpe for N trials as an offset. Otherwise the multiple-testing luck is counted as evidence.
5. **Partial pooling within a family.** Shrink each pattern's Sharpe toward its family mean, with between-pattern variance τ² = max(0, var(SR) − mean(se²)). This is the Bayesian twin of the DSR. Given noise-level dispersion, expect everything to shrink to the family mean.
6. **Look schedule.** One look a month, registered in advance. A Bayes factor of at least 10, sustained for 3 consecutive looks, is the threshold. Do not use a posterior level close to the prior: under the "replicated" prior, a true-zero strategy crosses 0.8 within a year 71% of the time (synthetic).

### 6.2 How it gates capital

| Stage | Requirement |
|---|---|
| Shadow | Default for everything |
| Paper order-eligible | DSR gate passed (D18) **and** an owner decision entry. The posterior is shown but does not gate |
| Real capital (≤ 5% of the account, D4) | PSR(SR > 0) ≥ 0.95 on paper (D6) **and** forward Bayes factor ≥ 10 sustained for 3 looks **and** E[Sharpe] ≥ 0.3 net |
| Size within the sleeve | Quarter-Kelly of E[Sharpe], capped at 1 risk unit (R10) |
| Demotion back to shadow | P(real) falls back below its prior, or E[Sharpe] < 0, for 3 consecutive looks |

### 6.3 Honest expectations and pitfalls

- **The prior dominates.** Under the retail-TA prior, even a true Sharpe of 1.0 pushes P(real) above 0.5 within 10 years of forward data in only **38%** of simulations, and never reaches 0.8. A forward Sharpe of 2.0 caps P(real) at about 0.25 after 5 years. These are the Bayes worker's synthetic Monte Carlo results, directionally consistent with t-stat arithmetic but **not independently reproduced**.
- **Pitfalls:**
  - Picking priors after seeing data.
  - Counting selection twice, or not at all.
  - Treating correlated strategies (22 patterns ≈ 4 bets) as independent evidence.
  - Non-stationarity: discounting old data also discards evidence.
  - Peeking.
  - Reading drawdowns as evidence.
  - Using Thompson sampling or bandits. In simulation, with 1 real strategy out of 5, it put only 41% of days on the real one after 5 years, against 20% for random.
- **Where it lives.** A pure-function module next to `stats/selection.py` (for example `stats/bayes.py`; the name is the implementer's call), feeding an advisory column in the pooled funnel and the forward journal. It is reported, never a status input, until the owner says otherwise.

---

## 7. Models: the one worth building

**Recommendation: a pooled, monthly, cross-sectional ETF ranker, used only as a ±20% tilt inside the allocation layer.** It must beat a no-fit baseline before it is used.

**Why this and nothing else.**
- Power is the binding constraint, not the model class. With 17 symbols and 5 years, a rank IC needs about 0.065 for t = 2 (re-checked). Realistic ICs are 0.02-0.04.
- With 50 ETFs over 20 years of monthly data, the bar falls to about 0.018.
- Trees on engineered features match deep nets: Qlib CSI300 LightGBM IC 0.045 vs Transformer 0.026 (page).
- Off-the-shelf time-series foundation models have negative R² (Chronos-large -1.37%) (page).
- Published ML stock-selection edges decayed to about zero or negative after costs after 2010: Krauss-Do-Huck -14% to -25% a year in 2010-15; anomalies about 7 bp a month after 2005 in non-microcaps (page).

**Specification** (`models.md` §6):

| Element | Value |
|---|---|
| Universe | 40-50 liquid ETFs (sectors, countries, bonds, real assets) with 15-20 years of yfinance history, **registered before any bar is loaded**, data cut at 2025-06-30 |
| Label | Forward 21-day return from Open[t+1] to Open[t+22], minus the cross-sectional mean, divided by the asset's own 63-day volatility, ranked per date |
| Features (14, ranked per date) | 12-1, 6-1, 3-month and 1-month returns; 5-day return; price/52-week high; close/SMA200; trend-vote count; 63-day volatility; 21d/126d volatility ratio; 252-day beta; 126-day max drawdown; RSQR60; 63-day skew |
| Models | M0, no fit: average rank of momentum, 52-week high, trend votes and minus volatility. M1: ridge. M2: LightGBM, depth 2, 200 trees, `min_child_samples` 300, 5 seeds |
| Validation | Expanding annual walk-forward with 21-day purge and 10-day embargo (about 160 monthly out-of-sample dates), plus combinatorial purged CV (10 folds, 2 test folds, 9 paths); per-date IC metrics; IC after removing the M0 baseline; label-shift null |
| Accept rule | IC net of M0 > 2 SE; M2 beats M0 on ≥ 7 of 9 paths; better net of 10 bp per side; turnover ≤ 1.5x M0 |
| Use | Top third within each correlation cluster, inverse-volatility weights, hold band, tilt capped at ±20% |
| Kill rules | Revert to M0 if the model's rolling 24-month IC < 0 while M0's > 0; stop the tilt if posterior P(IC > 0) < 0.6 |

**Honest prior:**
- M0's walk-forward IC is above 0: 65-75%.
- **M1 or M2 beats M0 by more than 1 SE: 15-25%.**
- The tilt adds more than 0.5 point a year net: 30-40%.
- Median gross information ratio: about 0.15.
- With fewer than about 15 assets it collapses into the trend sleeve, and no ML is needed.

**Sequencing.** After the D22 read and after trend sleeve v2 (D20 item 7 parks allocation work). It is a new registered universe and a single look.

**What to read or borrow:**
- **pysystemtrade** (`defaults.yaml` values verified: forecast cap 20, average absolute forecast 10, diversification multiplier max 2.5, 125-day smoothing, Sharpe shrinkage 0.9, buffer 0.10 trading to the edge).
- **Qlib** (Alpha158 operators, next-bar label hygiene, IC/ICIR, TopkDropout as a turnover band).
- **skfolio** (`CombinatorialPurgedCV`, Ledoit-Wolf, HRP clustering).
- **numerai-tools** (scoring per date, neutralisation against known exposures).
- Jansen's *ML for Trading* repo; the J-Quants JPX winners repo (the lesson: few features, relative labels, luck dominates the leaderboard); cweill's Jane Street causal harness (leakage tests).
- mlfinlab only as a reference *(memory, unverified: its licence changed)*.
- **Read only as lists of pitfalls:** FinRL (no credible out-of-sample net evidence) and Chronos/TimesFM (at most a volatility-forecast benchmark against HAR/EWMA, and only foundation models pre-trained on financial data show gains).

**Not to build:** deep RL, deep nets on daily bars, the WorldQuant 101 alphas as signals, a daily single-name model on the 17 symbols (prior under 5%).

---

## 8. News and alternative data: an advisory-first pipeline

**Expected value.**
- **Alpha: about zero here.** Lopez-Lira-Tang's GPT headline strategy had Sharpe 2.97 before costs, but **the long leg alone earned only Sharpe 0.78 (8 bp a day)**, and at 20 bp round-trip it is unprofitable. Its Sharpe also decayed from 6.54 (2021Q4) to 1.22 (Jan-May 2024) (page).
- Ke-Kelly-Xiu value-weighted long-short Sharpe was 1.24 before costs, with prices fully adjusted by day 3 (page).
- The platform's universe is long-only mega-caps entered at the next open, with about 0.3% round-trip costs.
- **Risk flags: modest but real.** Avoiding entries just before earnings reduces gap-through-stop losses. Avoiding retail-attention spikes avoids a documented negative drift: top Robinhood buys earned -4.7% over 20 days (JF 2022). The -19.6% "extreme herding" figure is from the working paper only.

**Look-ahead is the main threat.**
- claude-opus-5-5's training cutoff (June 2026) is later than the 2025-07-01 hold-out start. Any historical backtest of Claude-scored headlines is contaminated, **biased in an unknown direction**: Glasserman-Lin found anonymised headlines did *better* in-sample.
- So **only forward data from deployment counts.**

**Data actually available:**
- The IBKR MCP has **no news and no fundamentals tool** (tool catalogue).
- The IBKR TWS API via `ib_async` (already installed) has free providers BRFG, BRFUPDN (analyst actions) and DJNL (snippet). Provider enablement and history depth are unverified.
- SEC EDGAR 8-K acceptance timestamps (Item 2.02 = earnings) and Form 4.
- FRED/ALFRED; FINRA short interest; VIX/VIX3M (VIX3M is on IBKR, contract 47511905).
- Alpha Vantage and Polygon keys already in `config.py`.
- **An earnings calendar is the most valuable missing field.** A free source is still to be chosen *(memory, unverified)*.

**Pipeline (phase 0, advisory, after the D20 freeze lifts, because `services/*` is frozen until 2026-10-15):**
1. **Archive.** Store an append-only `news_raw`: provider, article id, symbol, vendor time, **our own `received_at`**, headline and content hash. Never update rows and never re-download history for tests. A headline received after 09:25 ET counts toward the next session.
2. **Stale filter.** Mark near-duplicates within 72 hours as stale. Fresh news moved prices 36 bp vs 22 bp for stale news (Ke-Kelly-Xiu).
3. **Score** each headline once with a **frozen, anonymised FinBERT** (CPU, $0). Store the model hash. Claude does *not* produce the logged scores.
4. **Briefing.** Fold the top items and the flags (≤ about 1.5k tokens) into the **existing** nightly briefing (`services/briefing.py`, `claude_integration.py`). It stays text-only, its AST isolation from execution, brokers, signals, risk and alerts is unchanged, and it costs about **$0.006 a night**.
   - Optional: a separate batched Claude scoring call for up to 40 anonymised headlines at about $0.036 a night, under its own sub-cap of about $1 a month.
   - Both fit within the existing $0.50/day and $5/month caps. The $5 monthly cap is the binding one: worst-case output for the briefing is $0.12 a call.
5. **Two pre-registered shadow items** in the forward journal (1-day and 5-day scoring):
   - **`news_veto`:** a long pattern signal that coincides with fresh strongly negative news, earnings within 2 sessions, or a top-1% abnormal-volume × |return| day.
   - **`news_tilt`:** exists only to *measure* the expected near-zero result.
   - Thresholds are fixed before the first data. These are monitors, not patterns, so the pooled N is unchanged unless one is ever used to block real entries; that would change the return series and be a versioned trial.
6. **Validation.**
   - Judge the veto by gap-through-stop frequency and size, not by Sharpe.
   - Power: 538 overnight gaps above 3% across 17 symbols in about 3.7 years before the hold-out (count only). Detecting a 20 bp drift needs about 900 *independent* events. Gaps cluster on market-wide days, so **7 years is a floor, not an estimate**. Count distinct dates and weeks.
   - Promotion only with p < 0.05 on at least 2 years of forward shadow **and** as a versioned D18 trial.

**Cost.** About $0.13-0.85 a month in tokens, $0 in data, a few hundred MB of disk a year.

**Not worth it:** Google Trends (resampled on every pull, so not point-in-time), WallStreetBets/Reddit (lost predictive power after GameStop, RFS 2024), economic-surprise indices, retail "unusual options" services, and paid news feeds.

---

## 9. Roadmap and owner decisions

### 9.1 Trial budget (pooled scanner family; required annualised Sharpe for the DSR gate as built, from `model-review-2026-10.md` §2.4)

| Step | N | Required Sharpe at T = 672 / 10y / 20y |
|---|---|---|
| Today | 22 | 2.40 / 1.24 / 0.87 |
| After the D22 registration (universes B and C) | 44 | 2.57 / 1.33 / 0.94 |
| Plus the D20 time-stop version | 66 | 2.67 / 1.37 / 0.97 |
| Plus D21 `pullback_uptrend` | 67 | about the same (not tabled) |
| Plus R1 standalone (if chosen instead of the split) | 68 | about the same |

Each version bump that changes the return series costs N += 22. Each new rule costs +1. History lowers the bar far more than rules raise it, so get history first. Allocation-layer trials (C2, C3, the ETF ranker) are counted in their own ledger.

### 9.2 Sequence (D20 order respected)

| # | When | Step | Notes |
|---|---|---|---|
| 0 | Now to 2026-10-14 | **Nothing changes in code.** The forward paper test runs; the owner reads this note and records decisions | D20 freeze |
| 1 | Before the D22 look | **Write the Bayesian priors (6.1) and the look schedule into the decision log.** Declare whether the C1 sweep split of P1 is added (and its N treatment) | Must precede any long-history number |
| 2 | From 2026-10-15 | **D22 long-history pooled run** on the owner's machine (unchanged rules, single look, full funnel). Add the advisory partial-pooling column (6.1 item 5) only if it was registered in step 1 | D20 puts this first |
| 3 | After step 2 | **D20 item 1 clean-up commit with the 20-bar time stop** (N 44 → 66), item 3 (weekly ML/pairs demotion), item 2 (ML leak and label fix), item 4 (pairs validity fix), item 5 (slow tests) | As approved |
| 4 | With or right after step 3 | **Risk fixes R1 (floor-aware heat) and R2 (lifetime floor)**, then R4 and R5 (correlation clusters and units). Settle how the capped replay treats them (5.1) | D5 version; blocks entries only |
| 5 | After step 3 | **Freeze D21** (+1 → 67), regenerating its counts with a committed script | As approved |
| 6 | After the freeze lifts | **Advisory diagnostics:** R9 risk-contribution table, R12 liquidity flag, the Bayesian scoreboard column, news phase 0 (archive, FinBERT, briefing block, two shadow items) | N unchanged |
| 7 | After D22 is read (D20 item 7) | **Trend sleeve v2 (C2)** on 1990+ proxies: one D4 version, judged on drawdown. Then R11 trade-to-edge for the core | Allocation ledger |
| 8 | After step 7 | **ETF ranker (section 7):** register the universe, one look, shadow tilt for a year | Allocation ledger |
| 9 | Any time (real account, by hand) | Managed-futures diversifier decision (DBMF/KMLM, or a TSX equivalent if one exists); FFH.TO only as a sized single stock | Proposals only; the owner places trades (D1) |

### 9.3 Owner decisions

1. **Accept the realistic target.** The core plus trend is expected to make high single digits. 15-20% a year is not a design goal. Chasing it means equity-plus volatility, concentration or leverage, all excluded by D3/D4.
2. **R1 ICT sweep:** (a) an advisory split of P1 trades declared before the D22 look; (b) a standalone pattern (+1 N); or (c) close ICT with no trial (also defensible). Recommendation: (a), or (c).
3. **Register the Bayesian priors and the look schedule** (6.1) in the decision log before the D22 look, including confirming the "practitioner rule" prior row.
4. **Risk fixes** R1 (floor-aware heat cap) and R2 (lifetime floor, with the floor level chosen by the owner, for example 80% of the sleeve), and whether they go into the same versioned commit as the D20 clean-up or are kept out of the capped replay until the next version.
5. **Correlation clusters and the correlation-aware heat cap** (R4/R5), built once from pre-hold-out instrument returns and frozen.
6. **Trend sleeve v2 (C2)** as the one allocation trial after D22, bundling EWMAC long/flat, inverse-volatility legs and the O3 cap.
7. **Real account:** whether to hold a managed-futures ETF as a 5-15% diversifier, given currency conversion and US withholding tax and with no TSX equivalent confirmed. Whether FFH.TO belongs at all (if so, 5% or less, sized as one stock).
8. **News phase 0:** approve the archive plus FinBERT plus briefing block (about $0.13 a month), and whether to allow the optional Claude scoring sub-cap (about $1 a month). Choose an earnings-calendar source.
9. **ETF ranker:** approve registering a 40-50 ETF universe for a single-look model test after C2.
10. **Stop list confirmed:** no ICT patterns beyond (2), no volatility-selling ETFs, no 13F bulk copying, no hedge-fund return chasing, no bandits, no Kelly or conviction sizing, and no LLM-scored historical backtests.

---

## 10. Main sources (the full lists are in the scratchpad notes)

**ICT**
- Tangirala: https://github.com/harshatangirala/smc-ict-research
- StatOasis: https://statoasis.com/overfit/research/ict-backtest-what-survives
- MPM: https://mpmmarkets.com/research/does-the-fair-value-gap-strategy-work
- Osler: https://www.newyorkfed.org/medialibrary/media/research/staff_reports/sr150.pdf
- FPFX via Finance Magnates: https://www.financemagnates.com/forex/analysis/exclusive-only-7-of-300000-prop-trading-accounts-achieved-payouts/ (snippet)

**Funds**
- AQR fund pages: https://funds.aqr.com/funds/alternatives/aqr-style-premia-alternative-fund/qspix · https://funds.aqr.com/funds/alternatives/aqr-equity-market-neutral-fund/qmnix · https://funds.aqr.com/funds/alternatives/aqr-managed-futures-strategy-hv-fund/qmhix · https://funds.aqr.com/funds/alternatives/aqr-managed-futures-strategy-fund/aqmix
- Fairfax 2025 Q4 release: https://www.fairfax.ca/wp-content/uploads/2026/02/02_February_19_2026-FFH-2025-Q4-Press-Release-Final.pdf
- Pershing Square Holdings 2025 Annual Report: https://assets.pershingsquareholdings.com/wp-content/uploads/2026/02/18175039/Pershing-Square-Holdings-Ltd.-2025-Annual-Report.pdf
- Berkshire Q2 2026 10-Q: https://berkshirehathaway.com/qtrly/2ndqtr26.pdf
- 13F data: https://13f.info

**Hedge-fund returns (snippets)**
- Citadel and D.E. Shaw: https://www.cnbc.com/2025/01/02/ken-griffins-flagship-hedge-fund-at-citadel-climbs-15point1percent-in-2024.html · https://fortune.com/2026/01/02/top-hedge-fund-performers-2025-bridgewater-de-shaw-citadel-millennium/
- Bridgewater: https://www.hedgeweek.com/bridgewaters-pure-alpha-outpaces-peers-with-33-2025-gain/

**Persistence**
- SPIVA: https://www.spglobal.com/spdji/en/documents/spiva/persistence-scorecard-year-end-2025.pdf (snippet)

**Trend and risk**
- Carver: https://qoppac.blogspot.com/search?q=long+only · https://qoppac.blogspot.com/2020/05/when-endogenous-risk-management-isnt.html
- pysystemtrade defaults: https://raw.githubusercontent.com/robcarver17/pysystemtrade/master/sysdata/config/defaults.yaml
- Hurst-Ooi-Pedersen: https://fairmodel.econ.yale.edu/ec439/hurst.pdf
- Winton due diligence: https://data.treasury.ri.gov/dataset/96dcb86f-e97e-4b05-8ce2-a40289e477a6/resource/3bc987e1-c783-4404-b2a4-909423393f24/download/winton-cliffwater-investment-due-diligence-reportredacted.pdf
- Baltas-Kosowski: https://papers.ssrn.com/abstract=2140091 (snippet)
- Zarattini-Antonacci replication: https://arxiv.org/pdf/2412.14361
- Concretum: https://concretumgroup.com/a-century-of-profitable-industry-trends/
- Grossman-Zhou: https://ideas.repec.org/a/bla/mathfi/v3y1993i3p241-276.html
- Harvey et al., volatility targeting: https://people.duke.edu/~charvey/Research/Published_Papers/P135_The_impact_of.pdf
- Cederburg et al.: https://www.lehigh.edu/~xuy219/research/COWY.pdf
- Kaminski-Lo: https://dspace.mit.edu/bitstream/handle/1721.1/114876/Lo_When%20Do%20Stop-Loss.pdf
- Baker-McHale: https://ideas.repec.org/a/inm/ordeca/v10y2013i3p189-199.html

**Models**
- Qlib benchmarks: https://github.com/microsoft/qlib/blob/main/examples/benchmarks/README.md
- Rahimikia: https://arxiv.org/html/2511.18578v1
- Foundation models on mega-caps: https://arxiv.org/abs/2606.27100
- Krauss-Do-Huck: https://www.iwf.rw.fau.de/files/2016/03/03-2016.pdf
- Chen-Welch: https://arxiv.org/abs/2607.06502
- skfolio: https://github.com/skfolio/skfolio
- numerai-tools: https://github.com/numerai/numerai-tools

**News**
- Lopez-Lira-Tang: https://arxiv.org/pdf/2304.07619v6
- Ke-Kelly-Xiu: https://par.nsf.gov/servlets/purl/10289824
- Glasserman-Lin: https://arxiv.org/abs/2309.17322
- Look-ahead detection: https://arxiv.org/abs/2512.23847
- IBKR news providers: https://www.interactivebrokers.com/docs/general/market-data-subscriptions/popular-market-data-subscriptions/news-data (snippet)
