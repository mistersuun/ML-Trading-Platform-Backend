# Which trading strategies actually hold up? (evidence review, 2026-10-06)

Plain-language review for the owner. It ranks the strategies found online and in the academic literature by how strong
the evidence is, and says which of them fit this platform. This is research for reference only, not personal financial advice.
The pre-registered test plan that follows from this review is in
[`preregistration-2026-10.md`](preregistration-2026-10.md).

## Summary (10 lines)

1. No strategy here is a reliable money machine. Published edges shrink by about half after publication, and most strategies promoted online fail honest tests.
2. The best-evidenced strategy is slow trend following across asset classes (a century of data, replicated, low cost). In long-only ETF form, though, it mainly controls drawdowns rather than adding return. The platform already runs it as the 10% trend sleeve.
3. Second is stock momentum (buy the past year's winners). It replicates robustly, but it crashes hard at times and needs a broad stock universe that we do not have, so this platform cannot test it.
4. Low-volatility investing and volatility targeting reduce risk. Neither reliably adds return.
5. Buying short pullbacks in uptrends (Connors RSI(2)) has the best practitioner record of anything our scanner can test. It is not peer-reviewed, and next-day-open execution plus costs eat much of it.
6. The turn-of-the-month effect is real in long histories but small and fading. Use it only to time monthly rebalances.
7. Avoid: sector-ETF rotation, Donchian/Turtle breakouts on ETFs, GEM dual momentum (it has lagged since 2010), overnight trading, pre-FOMC drift and earnings drift in large caps.
8. Reject outright: candlesticks, RSI/MACD/golden-cross crossovers on single stocks, ICT/SMC, Elliott Wave, harmonic patterns, day trading and 0DTE options.
9. None of the 20 current scanner patterns is a documented anomaly in its published form. Only 2 new patterns are pre-registered for the pooled (D18) test: an RSI(2) pullback and a 52-week-high breakout.
10. Honest expectation: the pooled test as built (DSR gated at p < 0.025, `DSR_P_MAX / 2`) needs an annualised Sharpe of about 2.4 to 2.5 on the 252-bar warm-up construction D18(c) adopted (2.40 at T = 672, 2.48 at T = 630, N = 22, V = 1/T; about 2.2 to 2.3 is the p < 0.05 figure, which is not the gate), and more once T_eff falls below T. The 2.9 figure belongs to the 504-bar construction (T = 378). So most likely nothing passes. Real money stays in the core and the trend sleeve.

---

## 1. How to read the evidence

Four facts from the replication literature apply to every strategy below.

- **Publication decay.** McLean & Pontiff (JF 2016) studied 97 published predictors. Their returns were about 26% lower out of sample and about 58% lower after publication, so a reasonable working assumption is half the published return.
- **Many anomalies do not replicate.** Hou, Xue & Zhang (RFS 2020) re-tested 452 anomalies with value-weighted returns and no microcaps. About 65% fail t > 1.96, and 96% of the "trading frictions" group fails (short-term reversal, illiquidity, idiosyncratic volatility). Jensen, Kelly & Pedersen (JF 2023) are more optimistic: built consistently, over 80% of factors replicate, and momentum and low-risk are among the robust themes. The disagreement is mostly about construction choices. Neither paper rescues small, high-turnover effects.
- **Multiple testing.** Harvey, Liu & Zhu (RFS 2016) argue that a new factor should clear t > 3, not t > 2. The platform's D15/D18 deflated-Sharpe gate applies the same idea.
- **Costs.** Novy-Marx & Velikov (RFS 2016) find that only strategies with roughly under 50% monthly turnover survive trading costs, and that a buy/hold band is the most effective way to cut them.

Grades used below:

| Grade | Meaning |
|---|---|
| **A** | Replicated by independent academics, works after publication, survives costs |
| **B** | Replicated, but materially decayed, with cost or construction caveats, or the evidence is for a different form than we can trade |
| **C** | Practitioner evidence and independent re-tests, but not peer-reviewed, or only a short live record |
| **D** | Weak or conflicting evidence |
| **F** | Fails honest tests |

How sure each figure is: figures with a link were seen in a source this session. Items marked *(snippet)* come from search-result
snippets because the page itself could not be fetched. Items marked *(memory)* were not re-checked. The repo's own 5-year checks are
real IBKR data, but they cover one mostly bull-market regime and are far too short to rank strategies.

## 2. Ranked review (best-evidenced first)

| # | Strategy | Grade | What it buys you | Fit for this platform |
|---|---|---|---|---|
| 1 | Diversified time-series momentum (trend following) | A (long/short futures form) | Crisis protection, positive long-run return | Only its long-only ETF form (#3) is tradeable here |
| 2 | Cross-sectional stock momentum (12-1, 52-week-high rank) | A-/B | A real premium with crash risk | Not testable here (universe too small, survivorship) |
| 3 | Long-only trend filter on asset-class ETFs (Faber 10-month SMA) | B | Drawdown control, not extra return | **Already the trend sleeve** (allocation layer) |
| 4 | Low-volatility tilt | B- | Lower risk, not alpha | Allocation idea only |
| 5 | Turn of the month | C+ | Small calendar tilt | Rebalance timing only |
| 6 | Short-term mean reversion in uptrends (Connors RSI(2), IBS) | C | Small, frequent bounces | **Best scanner fit**; pre-registered (RSI(2) only) |
| 7 | 52-week-high breakout (time-series form) | C | Continuation after new highs | Scanner, marginal; pre-registered |
| 8 | Canary / dual-momentum TAA (HAA, VAA, GEM) | C (HAA) / D (GEM) | Drawdown control with overfitting risk | Allocation layer; not proposed now |
| 9 | Volatility targeting | C as risk control, D as alpha | Smoother ride | Sizing overlay only; deferred |
| 10 | 200-day SMA on a single index | D | Lower drawdown, lower return | Covered by #3 |

### 1. Diversified time-series momentum (trend following): grade A, in its original form

- **Rule.** For each market, if the past 12-month excess return is positive, hold it long; otherwise hold it short (or go to cash in a long-only version). Positions are scaled to equal volatility and rebalanced monthly.
- **Evidence.**
  - Moskowitz, Ooi & Pedersen (JFE 2012) found it significant in 58 futures markets from 1985 to 2009.
  - Hurst, Ooi & Pedersen (2017) found positive net-of-fee returns in every decade from 1880 to 2016, with a full-sample (1880-2016) net Sharpe of about 0.76 after 2/20 fees and costs (the 0.4 quoted earlier was a per-decade or secondary figure, not the full-sample one), and the best results in equity bear markets.
  - It is replicated, it works after publication, its turnover is low, and it survives costs.
- **Critiques.**
  - Kim, Tse & Wald (2016) found that most of the alpha comes from volatility scaling: about 1.27% a month with scaling versus 0.41% without, and the edge vanished over 2009-13.
  - Huang et al. (JFE 2020) found weak predictability asset by asset.
- **Recent decade.** It was weak from 2010 to 2019. The SG Trend index gained +27.3% in 2022 (verified), and results in 2023-24 were poor.
- **Decay.** Moderate.
- **Caution for us.** This evidence is for **long/short, leveraged, vol-scaled futures**. In 2022 the long/short version earned money mainly by shorting bonds, and a long-only ETF sleeve can only go to cash. The platform's own review already flagged this as "evidence-transfer bias" (portfolio-recommendations.md, reviewer section).
- **Sources.** [AQR, Century of Evidence](https://www.aqr.com/Insights/Research/Journal-Article/A-Century-of-Evidence-on-Trend-Following-Investing) ·
  [MOP 2012](https://papers.ssrn.com/abstract=2089463) · [Kim-Tse-Wald via Quantpedia](https://quantpedia.com/deconstructing-the-time-series-momentum-strategy/) ·
  [Huang et al. 2020](https://ideas.repec.org/a/eee/jfinec/v135y2020i3p774-794.html) · [SG Trend 2022](https://thefullfx.com/year-end-drop-fails-to-dampen-stellar-2022-for-trend-followers/)

### 2. Cross-sectional stock momentum: grade A- / B

- **Rule.** Each month, rank a broad stock universe by return from 12 months ago to 1 month ago, or by price divided by its 52-week high (George & Hwang 2004). Buy the top group and rebalance monthly with a buy/hold band.
- **Evidence.**
  - Jegadeesh & Titman (1993) found about 1% a month, and the effect persisted in 1990s data (JT 2001).
  - It is among the most robust anomalies in both the HXZ and JKP replications.
  - It survives costs with buy/hold bands (Novy-Marx & Velikov).
  - The 52-week-high version is positive in 17 of 18 international markets, 12 of them significantly. It is mixed when applied to market indices.
- **Recent decade.** The 2000s were a "lost decade". The premium was about 3.5% a year in the 2010s, below its long-run average, and there was a crash in November 2020.
- **Crashes.** The long-short version lost about 73% from March to May 2009 (Daniel & Moskowitz, "Momentum Crashes", NBER w20439; the 2016 JFE version). An earlier figure of 82% in this document had no source and was removed.
- **Decay.** Material but not zero. The long-only leg captures less than long-short does.
- **Fit.** Poor here. It needs hundreds of stocks and survivorship-free index membership. Our 13 single stocks, 8 of them mega-tech, amount to a sector bet, not a momentum test.
- **Sources.** [Novy-Marx & Velikov 2016](https://www.nber.org/papers/w20721) · [Daniel & Moskowitz](https://www.nber.org/papers/w20439) ·
  [JKP 2023](https://www.nber.org/papers/w28432) · [52-week high, international](https://experts.nau.edu/en/publications/the-52-week-high-and-momentum-investing-in-international-stock-in/) ·
  [52-week high on indices](https://research-repository.griffith.edu.au/items/007b71f1-d78c-57a9-8ad4-84690e1371c2)

### 3. Long-only trend filter on asset-class ETFs (Faber GTAA, 10-month SMA): grade B

- **Rule.** At each month-end, hold each of about 5 asset-class ETFs (US stocks, international stocks, Treasuries, REITs, commodities) if its close is above its 10-month average; otherwise that slice goes to T-bills.
- **Evidence.**
  - Faber (2007) found similar returns to buy-and-hold with much smaller drawdowns.
  - An independent re-test from January 2006 to March 2025, with no re-tuning, gave a CAGR of 6.05%, Sharpe 0.68 and maximum drawdown -11.7% (verified in the earlier portfolio review).
  - Zakamulin finds the outperformance weak once data-snooping is controlled, and concentrated in a few bear markets.
- **Recent decade.** It lagged buy-and-hold in 2009-12 (4.95% vs 11.64% a year). The Cambria GTAA ETF lost money after its 2010 launch.
  - In the repo's own 5-year IBKR check, the long-only Faber sleeve lost 9.1% in 2022, partly during its warm-up period.
  - Adding it lowered the excess Sharpe from 0.68 to 0.64. That cost about 0.8 points a year of return and bought 1.7 points of drawdown reduction.
- **Decay.** The drawdown control persisted; the return edge did not.
- **Fit.** Excellent, and it is **already implemented** as the 10% trend sleeve (`allocation.trend_signals`: 8/10/12-month SMA votes on SPY, VEA, IEF, GLD, DBC, VNQ). Treat it as insurance with a cost, and do not re-tune its lookbacks on 5 years of data.
- **Sources.** [Concretum re-test](https://concretumgroup.com/global-tactical-asset-allocation/) · [Faber 2007](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=962461) ·
  [Canadian Couch Potato on GTAA](https://canadiancouchpotato.com/2013/12/30/the-failed-promise-of-market-timing/) · [Quantpedia, asset-class trend following](https://quantpedia.com/strategies/asset-class-trend-following)

### 4. Low-volatility tilt: grade B- (risk tool, not alpha)

- **Rule.** Hold the lowest-volatility fifth of large caps, or simply USMV/SPLV, and rebalance quarterly.
- **Evidence.**
  - Betting-against-beta (Frazzini & Pedersen 2014) is strong on paper.
  - Novy-Marx & Velikov (2022) show that this comes from a construction that effectively equal-weights microcaps. Net of costs it is modest and explained by profitability and investment factors.
  - Idiosyncratic-volatility effects fail the HXZ value-weighted tests *(memory)*.
- **Recent decade.** SPLV and USMV matched the S&P 500 with lower beta from 2011 to 2020, then lagged badly in the mega-cap rally of 2020-25.
- **Fit.** It is a risk-reduction idea for the core, not a scanner pattern. It is not proposed now.
- **Sources.** [Betting Against Betting Against Beta](https://mysimon.rochester.edu/novy-marx/research/BABAB.pdf) · [Advisor Perspectives](https://www.advisorperspectives.com/articles/2019/06/17/low-volatility-investing-works-sometimes)

### 5. Turn of the month: grade C+

- **Rule.** Hold the index from the last trading day of the month through the 3rd trading day of the next month.
- **Evidence.**
  - Ariel (1987), Lakonishok & Smidt (1988) and McConnell & Xu (FAJ 2008) document it. In US value-weighted stocks from 1926 to 2005 it earned 0.15% a day inside the window versus about 0 outside, and it appears in 31 of 35 countries.
  - Other work finds it disappears in S&P futures and spot after 1990. Practitioner tests from 2005 to 2024 still show a smaller, noisy effect.
- **Recent decade.** Positive but small and noisy *(snippet)*.
- **Decay.** Strong.
- **Fit.**
  - It does not work as a scanner signal: all 17 symbols fire in the same week, so 349 "trades" are really about 21 market-wide bets, which fails the D18 cluster gate by construction.
  - As a standalone strategy it sits out the market about 80% of the time.
  - The only use proposed is to schedule monthly allocation rebalances inside the window.
- **Sources.** [McConnell & Xu 2008](https://rpc.cfainstitute.org/research/financial-analysts-journal/2008/equity-returns-at-the-turn-of-the-month) · [Quantpedia, turn of the month](https://quantpedia.com/strategies/turn-of-the-month-in-equity-indexes)

### 6. Short-term mean reversion in uptrends (Connors RSI(2), IBS): grade C

- **Rules.**
  - **RSI(2), Connors & Alvarez 2008.** If the close is above the 200-day average and the 2-day RSI is below 10, buy. Exit at the first close above the 5-day average. No stop.
  - **IBS.** IBS = (close - low) / (high - low). Buy at the close when IBS < 0.2 and sell at the next close, or when IBS > 0.8.
- **Evidence.**
  - Neither rule is peer-reviewed as a strategy. Practitioner re-tests (Alvarez; EasyLanguageMastery 2019; Backtest substack) say RSI(2) still works with some decay; one says it was "slightly less effective" after 2009.
  - Indirect backdrop only: Lewellen (RFS 2002) documents negative autocorrelation of size, book-to-market and industry portfolios at intermediate (monthly to annual) horizons, on data that ends before 2000. That is NOT evidence about daily index reversal, which is what a 2-day RSI rule trades, and the linked PDF was not checked. A short-horizon daily-reversal source is still needed *(unverified)*. A June 2026 arXiv paper reports that SPY's daily mean reversion was still significant but weakest in 2022 *(snippet, unverified)*. Price Action Lab (2021) says "mean reversion is waning".
  - IBS is documented on equity ETFs (Pagonidis, NAAIM 2014) and on country ETFs (arXiv 2306.12434). Kinlay found a significant deterioration on SPY in 2015-16.
- **Recent decade.** A research worker's own SPY check from 2022-07 to 2026-10 used close-to-close fills, 1 bp costs and no dividends:
  - RSI(2) with the 200-day filter: Sharpe 1.53, 7.9% CAGR, invested about 12% of the time.
  - RSI(2) without the filter: Sharpe 1.37.
  - IBS: Sharpe 0.84 to 1.13.

  These numbers are close-to-close, cover a single symbol and overlap the platform's hold-out period, so they are **not evidence the platform can use** (see the disclosure in the pre-registration).
- **Decay.** Moderate, and it varies with the market regime.
- **Fit.** It is the best fit for the pooled scanner: daily bars, long-only, many events (387 pooled pre-hold-out trades over 96 entry weeks on all 17 symbols for RSI(2) on the adopted 252-bar build; an earlier draft quoted 229 over 67 weeks from the 504-bar definition). Three things work against it:
  - The platform enters at the **next open**, which misses part of the bounce. Much of the documented edge is close-to-close.
  - Costs are about 0.3% per round trip.
  - D18 proposes to gate the **1-bar delay** stress test.

  IBS has a 1-day hold, so all three hit it hardest. That is why only RSI(2) is pre-registered.
- **Sources.** [Connors & Alvarez rules](https://thechartist.com.au/wp-content/uploads/2024/12/504/ShortTermTradingStrategiesThatWork.pdf) · [Alvarez on RSI(2)](https://alvarezquanttrading.com/blog/rsi2-relative-strength-index-analysis/) ·
  [Backtest substack, 2-period RSI](https://backtest.substack.com/p/the-2-period-rsi-a-simple-system) · [Lewellen, autocorrelations](https://faculty.tuck.dartmouth.edu/images/uploads/faculty/jonathan-lewellen/Autocorrelations.pdf) ·
  [Pagonidis, IBS](https://www.naaim.org/wp-content/uploads/2014/04/00V_Alexander_Pagonidis_The-IBS-Effect-Mean-Reversion-in-Equity-ETFs-1.pdf) · [Kinlay on IBS](https://jonathankinlay.com/2019/07/the-internal-bar-strength-indicator/) ·
  [Price Action Lab](https://www.priceactionlab.com/Blog/2021/09/mean-reversion-waning/)

### 7. 52-week-high breakout (time-series form): grade C

- **Rule.** Buy on the first close above the highest close of the previous 252 trading days.
- **Evidence.**
  - Huddart, Lang & Yetman (Management Science 2009) is an event study of stocks crossing their 52-week high. Returns afterwards are reliably positive: about +0.63% in the next week for small caps and about +0.18% for large caps, gross *(secondary: these next-week abnormal-return figures come from a CXO Advisory summary, and the link below is the INFORMS supplement page, not the paper)*.
  - The bigger 52-week-high literature (George & Hwang 2004) is **cross-sectional** (ranking stocks by nearness to the high). Our version is an adaptation of it, not the published test.
- **Recent decade.** No independent post-2015 test of the time-series form was found.
- **Decay.** Probably similar to momentum.
- **Fit.** Marginal. Before the hold-out it gives about 179 pooled trades over 68 entry weeks on 16 symbols on the adopted 252-bar build (an earlier draft quoted 67 over 38 weeks on 12 symbols from the 504-bar definition), above the D18 count gates.
  - For large caps, the published one-week gross effect (about 0.18%) is smaller than our round-trip cost, so the case rests on a longer continuation that the ATR target has to capture.
  - It is pre-registered as the one continuation hypothesis, because the registry currently has no academically anchored continuation pattern.
- **Sources.** [Huddart-Lang-Yetman 2009](https://pubsonline.informs.org/doi/suppl/10.1287/mnsc.1080.0920) · [George & Hwang 2004](https://academicnewsletter.sufe.edu.cn/info/361244)

### 8. Canary and dual-momentum asset allocation (HAA, VAA, BAA, GEM): grade C for HAA, D for GEM

- **Rules.**
  - **GEM.** Each month, if SPY's 12-month return beats T-bills, hold the better of SPY and ex-US stocks; otherwise hold bonds.
  - **HAA.** If TIP's momentum is positive, hold the top 4 of 8 risk ETFs; otherwise hold the better of IEF and BIL.
- **Evidence.**
  - GEM has lagged the S&P 500 since 2010, by about 4.8 points a year *(snippet)*. It is very sensitive to the lookback and the rebalance date (ReSolve 2019). It lost about 10-24% in 2022 because its "safe" asset was bonds *(secondary source)*.
  - The Keller family (HAA, VAA, BAA) has the best real-time tracking record on Allocate Smartly since about 2017. BAA was positive in 2022 *(secondary source)*.
  - An independent re-implementation estimated a 45.5% probability of backtest overfitting across the Keller variants.
- **Fit.** Allocation layer, monthly. It would replace the trend sleeve, not add to it, and it is **not proposed now**. If it is ever considered, test it on long mutual-fund or index proxy history, not on 5-year ETF bars.
- **Sources.** [Allocate Smartly, HAA](https://allocatesmartly.com/hybrid-asset-allocation/) · [ReSolve, GEM](https://investresolve.com/inc/uploads/pdf/global-equity-momentum-a-craftsmans-perspective.pdf) ·
  [Keller re-implementation](https://github.com/SimonKreis/keller-strategies) · [Petit 2026, GEM *(snippet only)*](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=7427878)

### 9. Volatility targeting: grade C as a risk tool, D as a return source

- **Rule.** Weight = min(1, target volatility / recent realised volatility); the rest goes to T-bills.
- **Evidence.**
  - Harvey et al. (2018) find that it raises the Sharpe of equities from about 0.40 to 0.50 and shrinks the left tail.
  - Cederburg et al. (JFE 2020) tested 103 strategies and found **no systematic out-of-sample gain**; the Moreira-Muir alphas cannot be captured in real time.
  - In the SPY check it gave Sharpe 1.13 vs 1.11, with a maximum drawdown of -15% vs -19%.
- **Fit.** It is a sizing overlay. Because Kim-Tse-Wald attribute much of trend following's alpha to vol scaling, it is listed as a *deferred* option for the trend sleeve in the pre-registration, off by default.
- **Sources.** [Harvey et al. 2018](https://people.duke.edu/~charvey/Research/Published_Papers/P135_The_impact_of.pdf) · [Cederburg et al. 2020](https://www.lehigh.edu/~xuy219/research/COWY.pdf)

### 10. 200-day SMA filter on a single index: grade D as alpha

- **Rule.** Hold SPY while its close is above the 200-day average; otherwise hold T-bills.
- **Evidence.** Once data-snooping is controlled, the outperformance is weak and comes from a few bear markets (Zakamulin). It gets whipsawed (2011, 2015-16, 2018, 2020, 2023).
- **Recent result.** In the SPY check it gave 12.5% CAGR vs 18.0% for buy-and-hold, the same Sharpe, and about half the drawdown.
- **Fit.** It is already covered, in a better diversified form, by the trend sleeve (#3). The scanner's `sma_200_trend` is a daily crossing of this rule on single names, which is not the documented form.
- **Sources.** [CXO, moving-average horse race](https://www.cxoadvisory.com/technical-trading/long-run-moving-average-horse-race-for-timing-the-u-s-stock-market) · [Alpha Architect](https://alphaarchitect.com/2020/03/an-empirical-challenge-for-trend-following/)

### Documented, but avoid on this platform

| Strategy | Why not |
|---|---|
| Sector-ETF momentum rotation (top 3 of 11 SPDR sector ETFs) | It underperformed equal weight from 1998 to 2015 (CXO; 4.4-5.0% vs 6.2% CAGR). Post-2000 sector-ETF tests are insignificant (Du, Denning & Zhao 2014). |
| Donchian/Turtle breakouts on ETFs | The edge was in diversified futures with shorting and decayed after the early 1990s. On SPY from 2022 to 2026: 4.4% CAGR, Sharpe 0.54. |
| Short-term reversal in single stocks (losers vs winners, 1 week to 1 month) | Costs eat it (Novy-Marx & Velikov), and 96% of trading-friction anomalies fail in HXZ. |
| Overnight drift (buy at the close, sell at the open) | The premium window has averaged about zero since 2021 (NY Fed SR 917) *(unverified: the date range claimed here is not supported by that report as cited)*. About 500 trades a year, and the next-open engine cannot execute it. |
| Pre-FOMC drift | It disappeared after 2015 (Kurov, Wolfe & Gilbert 2021), and there are only about 8 events a year. |
| Post-earnings drift (PEAD) | It has been non-existent for large caps since about 2006 (Martineau 2022), and there is no earnings calendar in our data. |
| Same-calendar-month seasonality (Heston & Sadka) | It needs 10-20 years of history per stock and has high turnover. |
| High-volume return premium | It is a cross-sectional, one-month effect, and only 19 events occur in our data. |
| Pairs trading | It needs shorting. Returns decayed from 0.86% to 0.24% a month (Do & Faff, verified earlier). |

## 3. Popular online strategies that do not hold up, and why

| What you see online | What the evidence says | Why it fails |
|---|---|---|
| **Candlestick patterns** (hammer, engulfing, doji...) | Marshall, Young & Rose (2006) tested 28 patterns on DJIA stocks from 1992 to 2002: no value, and one "bullish" pattern reliably preceded declines. A follow-up in Japan, where candlesticks originated, was also null *(memory)*. | Pattern-spotting on noise; there is no economic reason for a persistent edge. |
| **RSI/MACD crossovers, golden cross on single stocks** | Brock, Lakonishok & LeBaron (1992) looked good in-sample. Sullivan, Timmermann & White (1999) checked about 7,846 rules for data snooping: the best one was insignificant out of sample. Bajgrowicz & Scaillet (2012, DJIA 1897-2011) found nobody could have picked the future winners, and small costs wipe out even the in-sample gains. | With thousands of rule variants, some look great by chance. |
| **ICT / "smart money concepts", Elliott Wave, harmonic patterns** | No peer-reviewed out-of-sample evidence. | The rules are subjective and adjusted after the fact, so they cannot be falsified. |
| **Day trading for a living** | In Taiwan, fewer than 1% of day traders are predictably profitable net of fees (Barber, Lee, Liu & Odean 2014). In Brazil, 97% of those who persisted more than 300 days lost money, and 1.1% earned more than the minimum wage (Chague et al.). | Costs, and competing against faster, better-informed traders. |
| **0DTE options** | Retail traders lost about $358k a day on SPX 0DTE options, and more than 60% of that was transaction costs (Beckmeyer, Branger & Gayda 2023). | Spreads and a negative-expectation game. |
| **"My TradingView backtest shows 80% win rate"** | The Quantopian study (Wiecki et al. 2016, 888 algorithms) found that backtest Sharpe explained almost none of the live Sharpe (R² < 0.025), and more backtesting made it worse. | Repainting/lookahead (`request.security` without the `[1]` offset), zero default commission and slippage, optimistic fills, only good backtests get posted, and today's index members tested back in time (survivorship). |
| **The options wheel / covered calls as "income"** | It earns a real variance risk premium, but the return is mostly equity beta plus short volatility, with crash risk (Israelov & Nielsen 2015, *memory*). | It is not free money. It is long-only in spirit but lags in bull markets, and it needs options approval and 100-share lots. |
| **Crypto trading bots** | Mixed, sample-dependent academic evidence. Commercial bots are unverified, and many are scams. | Not executable here anyway (crypto is research-only, D3). |
| **GEM dual momentum "beats the market"** | It has lagged the S&P 500 since 2010 *(snippet)* and lost money in 2022 holding bonds. | The backtest was chosen in hindsight, and the results depend on the rebalance date. |
| **Sector rotation on momentum** | It underperformed equal weight after publication (CXO). | Sector ETFs lack the dispersion that drove the earlier industry-momentum results. |

## 4. What this means for the platform

- **Real money.** Keep the diversified core plus the 10% trend sleeve. That is the strategy with the best evidence, and it is already built. Do not re-tune it on 5-year data.
- **Scanner.**
  - None of the 20 current patterns implements a documented anomaly in its published form. They are generic members of the moving-average and breakout families, which failed data-snooping tests, plus oscillator rules on 14-20 day windows, where the reversal evidence is at 2-5 days.
  - `macd_crossover` and `macd_hist_reversal` produce identical signals.
  - The pre-registration adds only **two** literature-defined patterns (an RSI(2) pullback and a 52-week-high breakout), with fixed published parameters. Each extra pattern raises the bar for all of them.
- **Expectation.** D18 needs a pooled annualised Sharpe of about 2.9 over 378 out-of-sample days on the 504-bar construction, and about 2.4 to 2.5 (the p < 0.025 gate as built; 2.2 to 2.3 is the p < 0.05 figure) over the roughly 630 to 672 days of the 252-bar warm-up construction D18(c) adopted (higher once T_eff is below T). No published version of these rules is close to that net of next-open execution and 0.3% round-trip costs. The most likely outcome is shadow-only signals and no pattern eligible to trade, and that is an acceptable, honest result.
- **Execution insights worth keeping even without a strategy:**
  - Charge 5-10 bp per side for ETFs and 10-20 bp for single stocks.
  - Assume about half the published return.
  - Require t > 3 or a deflated Sharpe for anything new.
  - Prefer to schedule monthly rebalances near the turn of the month.

## 5. Source quality notes

- Fetching from arXiv, CXO Advisory, SSRN, Allocate Smartly, NAAIM pages, Concretum and quant4free was mostly blocked in this session. Figures from those sites come from search snippets. In particular, the June 2026 arXiv paper on SPY mean reversion (2606.29591) and Petit (SSRN, September 2026) on GEM were seen only as snippets and are unverified.
- The SPY checks quoted above (RSI(2), IBS, 200-day SMA, Donchian, vol targeting) were run by a research worker in its scratchpad on IBKR SPY daily bars from 2022-07 to 2026-10. They use close-to-close fills, price-only data and 0% on cash. **They overlap the platform's hold-out (2025-07-01 onward)** and are disclosed in the pre-registration. They were not used to choose any parameter.
- Verified in the earlier portfolio review: the Faber 2006-2025 re-test, SG Trend +27.3% in 2022, and the Do & Faff pairs decay.
- Detailed worker notes from 2026-10-06 (academic, practitioner, skeptic, platform-fit) were kept in the session scratchpad and are not committed.
