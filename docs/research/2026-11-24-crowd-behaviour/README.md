# Can crowd reactions be predicted? Six studies with adversarial review (2026-11-24)

The owner on 2026-11-24: crowds react to news with the same fear and greed every time, so past reactions should help predict what prices do next. Fourteen agents tested that on the daily bars in `state/external_bars` (SPY, QQQ and about 125 stocks, 2021-11-05 to 2026-11-23). Paper research only; nothing here is an order or advice.

## Method

- Six teams, one hypothesis family each. Signals and thresholds were written down before outcomes were computed.
- Discovery sample 2021-11-05 to 2024-12-31; hold-out 2025-01-01 to 2026-11-23. An effect counts only if it holds in the hold-out.
- t-statistics are clustered by date; episodes are event dates at least 10 sessions apart.
- One adversarial reviewer per team re-ran the code, recomputed the strongest effect independently and ran a placebo. Where study and review disagree, the review stands.
- Known bias: the universe was chosen in 2026 from names popular now (survivorship), which flatters buy-the-dip results.

## Verdicts

| Study | Strongest effect survives review | Reviewer's confidence |
|---|---|---|
| Opening gaps: fade or follow (`gaps/`) | no | high |
| Large one-day moves: bounce, fade or drift (`shocks/`) | no | high |
| Market-wide fear: washouts and relief rallies (`breadth/`) | no | high |
| Leaders and followers (`contagion/`) | no | medium |
| Size of moves: clustering and crash frequency (`magnitude/`) | yes | high |
| Calendar and streak effects (`calendar/`) | no | medium |

## What each reviewer concluded

### Opening gaps: fade or follow

No gap rule holds out of sample in a tradable way. The one large cell is stocks that gapped down more than 4% and then closed above their open. Their next-day excess was +0.69% (t 2.2, 66 episodes) in 2021-2024 and -0.88% (t -3.0, 40 episodes) in 2025-2026. This holds against the stock's own mean, against SPY and against the same-day stock average, and it is not explained by the April 2025 tariff shock or November 2026. The sign flips between periods, so it cannot be traded as one rule.

After an up-gap that closed red, SPY and QQQ drifted up +0.6% to +1.3% over the next 3 days, but on only 12-19 episodes with t below 1.8. Most of that is market beta: QQQ against SPY is only +0.18% in the hold-out. A placebo shifted 20 days reproduces it, so the evidence is weak.

Same-day fades of large up-gaps average about -0.1% to -0.5%, with |t| below 1.4, below the 0.15% cost level and not consistent across periods. For yesterday's setup (SPY +5% gap, 3 sigma, right after a -2 sigma day) there is roughly one precedent in five years, so the data says nothing about it.

**Rule still worth a paper test:** none. At most an observation-only paper log, not a rule: on days when QQQ gaps up more than 1 sigma of its own 20-day close-to-close volatility (computed from the prior 20 sessions) and closes below its open, record QQQ's 3-day close-to-close return minus SPY's. Abandon it unless 20 or more new episodes show an excess of at least +0.15%.

Errors and weaknesses the reviewer found:

- No look-ahead found. sigma uses closes t-20..t-1, gap and zprior are known at the open, and forward windows run from Close_t and drop spans that cross a flagged day. I reproduced their full output exactly: 276 variants, 15 dropped days, 0 rows pass.
- Split filter mislabelled. All 15 dropped days look like real moves, not splits. The bars appear already split-adjusted: NVDA's 10:1 split in June 2024 shows no jump (121.0 -> 120.9 -> 121.8). So the filter removed real extreme events such as APP, ASTS x3, GME x3, MRNA +177% and UPST -56%, and caught 0 actual splits.
- Their caveat says the April 2025 tariff shock and November 2026 explain the hold-out sign flip in the gap<-4% & close-above-open next-day row. I refuted this: excluding Mar 28 - Apr 30 2025 the hold-out is still -0.83% (t -2.71), and excluding November 2026 it is -0.87% (t -2.94).
- Excess in the hold-out is measured against each symbol's mean over the hold-out itself. This is a mild in-sample look-ahead. It is harmless for describing effects but cannot be used for trading.
- The headline hit rates are pooled stock-days, not clustered by date (they disclose this). The 3- and 5-day windows overlap, which inflates t.
- QQQ z>+1 & close-below-open fwd3 (their 'most consistent' cell) is mostly market beta. Against SPY the excess shrinks to +0.49% (discovery) and +0.18% (hold-out, t 1.27), from +1.27% and +0.86% against QQQ's own mean. Shifting the signal 20 sessions later gives a larger discovery 'effect' (+1.46%, t 2.8) than the real signal, so the discovery result is regime drift, not a gap reaction.
- Their reported n_event_dates and n_episodes add discovery and hold-out together, which makes the samples look larger than they are within each period.
- Arithmetic check: my independent figures match theirs to within about 0.05pp (288 vs 291 discovery dates for cell A, from slightly different sigma and edge handling).

### Large one-day moves: bounce, fade or drift

After a stock's up day of 3 or more of its own 20-day sigma, the next 5 sessions beat SPY by about +0.4-0.5% more than random days for the same stock in the same year. Discovery: +0.78% raw, against a shuffle placebo of +0.31%, p about 0.05. Hold-out: +0.52% raw, against a placebo of +0.11%, p about 0.1, t=1.0, 43 episodes. The sign is the same in both samples, but a few dates drive it: the median is only +0.1-0.2%, and dropping the 5 best hold-out dates leaves -0.10%. All this supports is that 'shorting euphoria has not paid in this survivor-biased universe'. It is not a positive, tradable momentum edge. 'Buy the panic' changed sign between samples (-0.25% in 2021-24, +0.65% in 2025-26) and is not a stable effect. Next-day direction for every stock signal stays at about 46-54%, below the 58-65% options break-even.

**Rule still worth a paper test:** none. At most a negative filter: do not open short or bearish next-day positions only because a stock closed at z>=+3 (r_t over the std of the prior 20 returns). That filter is a risk guard, not an expected-value rule.

Errors and weaknesses the reviewer found:

- No look-ahead found. Sigma uses the prior 20 returns with today excluded (rolling std then shift(1)). Forward returns run from the shock-day close. The volume ratio uses the full day's volume, which is only valid for close entry, and a next-open entry check gave almost the same result (+0.70% vs +0.78% in discovery).
- Benchmark mismatch inflates the headline. The event mean is an average over dates (equal weight per date), but the 'unconditional' benchmark is a pooled stock-day mean. When I shuffled the same stocks' events to random dates within the same year, the null came out at +0.31% per 5 days in discovery and +0.11% in hold-out, not 0. About 40% of the reported +0.78% discovery 'edge' comes from this weighting and from which stocks fire most, not from the shock.
- Fragile to a few dates. For UP z>=3 at 5 days, the median date-level excess is only +0.12% in discovery and +0.24% in hold-out, against means of +0.78% and +0.52%. Removing the 5 best dates cuts discovery to +0.37% (t=1.17) and makes the hold-out -0.10% (t=-0.22).
- The noise floor is high. The +20-session shifted placebo (no real signal) gave a hold-out 5-day excess of -0.94% with t=-2.49. A hold-out |t| of about 2.5 can therefore come from nothing, so the real hold-out t=1.0 means nothing.
- Hit rates are pooled stock-days, not clustered by date, and use raw rather than excess returns. In the hold-out, pooled next-day 'up' was 46.4% but the date average was 52.7%. Because of that mismatch, the short side shows a '53% hit' while its mean loses, so the hit-rate column cannot be read as an edge.
- The 'holds' test is too loose: same sign plus 50% of the size, with no significance needed. 26 of 100 cells 'hold', including cells where discovery was about 0 (S3 1d) and losing trades. Their 'strongest effect' was picked after seeing all 200 cell-tests, so it is post-hoc.
- Survivorship. The universe beats SPY by +0.15% per 5 days unconditionally. Using each stock's own same-sample mean instead lowers the effect to +0.70% (t=1.92) in discovery and +0.45% (t=0.87) in hold-out.
- Minor issues. pct_change on the wide frame pads across missing bars (I found 1 isolated missing bar, so the impact is negligible). The 15 dropped moves beyond 45% are removed from the signal but still sit inside other events' forward windows. No splits were found, and I agree with the 15 dropped days.

### Market-wide fear: washouts and relief rallies

Days when 30% or more of the 125 stocks close at a 20-day low are followed by SPY 5-day returns that are not reliably different from normal. Pooled over event dates, the excess is +0.17% in discovery (HAC t 0.5, 90 dates, 17 episodes) and +0.86% in hold-out (HAC t 1.25, 31 dates, 12 episodes). With weights that use only past information, it is -0.02% and +0.36%. One non-overlapping trade per window gives +0.05% and +0.14%. The +1% 'bounce' (t 2.6) comes from grouping dates into episodes with future information: an episode's last date is, by construction, followed by no new lows. The sign is weakly positive in both samples, but the size is not stable, no cell clears t 2 on honest inference, and each sample has fewer than 20 episodes. Next-day hit rates (42% / 53%) are nowhere near the 58-65% options break-even. Nothing survives as a predictive effect.

**Rule still worth a paper test:** none

Errors and weaknesses the reviewer found:

- LOOK-AHEAD IN THE EPISODE WEIGHTING (fatal for the headline). An episode ends on the last event date that has no further event in the next 9 sessions, so the grouping uses future data. For LOW30, 'no new 20-day-low breadth for 9 sessions' means the market did not keep falling, so each episode's last date has a mechanically good forward return. SPY 5d excess on the last date of each episode is +2.92% (discovery) and +2.41% (hold-out). On all other event dates it is -0.47% and -0.11%. Single-date episodes, which are entirely 'last dates', are +2.84% and +1.09%. Episode weighting gives these days far more weight than pooled weighting does, so the reported +0.98% / +1.16% (t 2.6) comes from how the dates were grouped, not from the signal. The same bias inflates every episode-weighted cell in their grid (W40, BOT70 and the rest). It explains their own puzzles: the large gap between episode-weighted and pooled figures, and the first-date test erasing the effect. The first-date test is the only causal test they ran, and it showed nothing.
- Pooled stock-days were not the issue: t was computed across episodes, which is a form of date clustering. But that t is computed on the look-ahead-weighted values above, so it is invalid. With pooled event dates and a Newey-West HAC t (lag h), no S5 cell reaches |t| 1.7 in either sample.
- Their claim that 'the rebound comes from signal days deep inside long selloffs' is backwards. In the discovery sample, dates inside multi-date episodes average +0.05% excess. Dates that are not the last of their episode average -0.47%. The positive figure comes from the end-of-episode dates, which can only be identified after the fact.
- Their 'holds' rule (same sign and at least 1/3 of the size) was applied to the biased episode-weighted excess, so the 'holds_out_of_sample: true' flags for S4, S5, S6 and S7 are not credible.
- Minor: the signal uses the close of day t and entry is assumed at that same close. That is executable only with a market-on-close order, so it is mildly optimistic.
- Minor: 'split' handling. All 15 |1d|>45% moves were dropped. As they say, these are mostly genuine earnings or meme moves rather than splits. That removes real crash and squeeze days from breadth, but the effect is small. I reproduced the count of 15.
- Survivorship: excess over each sample's own unconditional mean removes the drift level but not the extra rebound tendency of names chosen in 2026. This flatters EW buy-the-dip figures and was not otherwise handled. It does not change the verdict.
- Multiple testing: 128 variants, so several nominal t > 2 cells are expected by chance. Combined with the weighting bias, the grid's t > 2 cells carry no evidential weight.

### Leaders and followers

There is no next-day spillover of fear or greed from a leader to its group in either direction, for leader, SPY or group-median shocks. One thing shows up in both periods. On days when a leader (NVDA, AAPL, TSLA, COIN, BABA or CRM) moves beyond 3 sigma, the group members that fell most, or rose least, beat the ones that did best over the next day by about +0.56% in 2021-24 and +0.97% in 2025-26 (next-day c2c, date-clustered). The 2021-24 figure is not significant (t 1.26-1.56) and comes mostly from TSLA and COIN. The 2025-26 figure is significant (t 2.6-3.2) but rests on only 19 episodes.

The effect is not present on ordinary days or on high-dispersion days without a leader shock (spread about 0). In a within-year shuffle placebo, the real discovery value sits at the edge of the placebo band and the hold-out sits outside it.

If you enter at the next open (o2c), the gap shrinks to +0.4% to +0.5% (t about 1.6 in each block, medians +0.05% to +0.15%). That is roughly the cost of a two-leg long-short trade. It fails the pre-registered bar (discovery t >= 2), and it was picked as the best of 14 variants after both blocks had been seen. Verdict: suggestive, not established, not tradable.

**Rule still worth a paper test:** This is a forward paper test only, kept as a watch item; no conclusion until there are at least 20 new episodes.

- **Trigger:** NVDA, AAPL, TSLA, COIN, BABA or CRM closes with |close-to-close return| / std(prior 60 daily returns) > 3.
- **Ranking:** sort that leader's pre-registered followers by day-0 return minus SPY's.
- **Trade:** at the next open, go long the equal-weighted bottom half and short the equal-weighted top half. Exit at that day's close.
- **Logging:** record the gross spread per event date and judge it net of about 0.30% cost (two legs).
- **Kill rule:** drop it if, after 20 new episodes, the mean net spread is <= 0 or t < 1.5.

Errors and weaknesses the reviewer found:

- No look-ahead in the signal itself. z uses the std of the 60 prior sessions (shift(1)), and the forward returns are correctly shifted by -1/-2/-3. Running study.py reproduces every number in their table.
- S6 does not depend on the shock's sign. Signing by the leader's direction and then taking the bottom half minus the top half gives exactly 'day-0 within-group losers minus winners' whichever way the leader moved. So the 'catch-up toward the leader' story is really a cross-sectional reversal measured only on leader-shock days.
- They waved S6 away as 'probably ordinary short-term reversal'. My baselines contradict that. The same losers-minus-winners spread on all days is -0.04% (discovery) and -0.01% (hold-out). On non-event days with follower dispersion at least as high as on event days it is -0.09% and +0.03%. Whatever S6 is, it is specific to the leader-shock days.
- Selection: S6 was named the strongest effect after both blocks had been seen (best of 14), so its hold-out is not a clean out-of-sample test. The hold-out also shows the opposite 'reversal' pattern in S2/S3 (t -2.05/-3.34), which points to a 2025-26 regime rather than a stable law.
- Small samples. My independent count is 99 event dates and 51 episodes: 32 in discovery and 19 in the hold-out. The hold-out is under 20 episodes, so it is 'weak' by the project rule.
- The discovery effect is concentrated. It comes from TSLA (+2.67%, 10 dates) and COIN (+1.24%, 9 dates). AAPL (-0.29%), CRM (-0.25%), BABA (-0.06%) and NVDA (+0.09%) are flat or negative in discovery.
- Execution: the c2c version needs entry at the day-0 close, which is the same price that defines the signal. The tradable next-open-to-close version is about half the size in the hold-out (+0.41%), and its medians are only +0.05% to +0.15%. Means are driven by outliers.
- Minor issues. Demeaning uses each block's full-sample mean, a mild centering look-ahead that is harmless for spreads. 3-day returns are summed rather than compounded. The >45% filter dropped 15 genuine earnings or squeeze jumps rather than splits; the data appear split-adjusted, since NVDA 2024 shows no -90% day. Neither changes the conclusion.

### Size of moves: clustering and crash frequency

Next-day volatility clustering in single stocks is real and replicates. After a stock closes more than 3 trailing sigmas away (either direction), the chance that its next day is more than 1.5x its typical move rises from about 22% to 36% (discovery 2021-24) and 45-46% (hold-out 2025-26). That is +15 to +16 probability points, date-clustered t 7-8, and two placebos come out null. After 2-sigma days the gain is +7 to +8 points. The effect is mostly idiosyncratic (probably earnings-type) shocks and fades within about 5 sessions. It says nothing about direction. At index level (SPY) the sample is too small and does not replicate: 22/17 episodes, discovery about 0 points. Market-wide shock days are only about 13 episodes. Whether a next-day straddle after a shock makes or loses money cannot be decided from this data. Under shock-inclusive implied vol it loses about 2.4-2.7%; under pre-shock vol it gains about 0.3-0.8%. Both are assumptions. In this sample SPY never fell 8% in one day (worst -5.85%, 2025-04-04). It fell 8% or more over 5 sessions in one episode (April 2025, -11.5%). As general knowledge, not from this data: one-day 8%+ index falls happened only in 1929-33, October 1987, October-November 2008 and March 2020.

**Rule still worth a paper test:** No stock trade rule. The only thing worth a paper test is measurement, not trading. On each session when a liquid optionable name closes more than 3x its trailing 60-day sigma (either direction, not an index), log the real quoted ATM straddle mid near the close on the nearest expiry. Compare it to the realised |close t+1 - close t| and to the 1-day share of the straddle's value decay. Collect at least 20 distinct episodes before drawing any conclusion. Only then decide whether the realised move exceeds the quoted straddle mid net of the spread. Until real quotes exist, no straddle buy or sell rule is supported.

Errors and weaknesses the reviewer found:

- Straddle result depends on the assumed cost. The cost is 0.8 x 1.2 x EWMA10 vol including the shock day. With span 10 the shock carries about 18% of the variance weight, so after a 3-sigma day the assumed cost (mean 5.50%) is far above the realised next-day move (3.12%). If the cost uses pre-shock vol (EWMA through t-1, or sigma60) with the same 1.2x premium, the post-3-sigma straddle turns POSITIVE: +0.30%/+0.38% (disc/hold, t 2.1/2.8), and +0.78%/+0.80% at a 1.0x premium. The finding 'buying post-shock straddles loses 1.6-1.7%' is an assumption, not a result. Neither sign can be decided without real option quotes. Post-earnings IV crush (most shocks are idiosyncratic, probably earnings) means the shock-inclusive model probably overstates the buyer's cost.
- The all-days straddle baseline loses -0.53% at 1.2x by construction (cost 0.96 x RMS vol, against E|r| of roughly 0.8 x sigma). At 1.0x the loss is only -0.08%/-0.06%. Labelling straddle rows as 'holds out of sample' is meaningless: the model replicates itself.
- Episode counts (115-123) are a calendar artefact. With 129 names nearly every 10-session block contains an event, so episodes come out at about 1267/10. They are not independent market episodes. Market-wide 3-sigma dates (at least 10% of names shocking together) are only 16 dates and about 13 episodes: 7 in discovery (excess +13pp, t 1.1) and 9 in hold-out (+41pp, t 4.2). That is weak. The pooled effect is driven by idiosyncratic shocks: 1,939 of 2,359 stock-events.
- SPY-level clustering does not replicate. Discovery is -1.3pp (my number; theirs +0.6), hold-out +15.7pp (t 1.9), on 22/17 episodes. The team did report this as weak, which is correct, but the answer to the owner implies index-level clustering ('today's swing is the clustering pattern itself') that this sample does not show for SPY.
- Persistence claim ('stays near 1.1x for about 20 sessions') is measured against the pre-shock yardstick, and the unconditional ratio is already 1.03. The extra elevation is about 7%. Against an updated yardstick, my 5-session-lag placebo shows no excess (-0.4pp/+1.0pp). The practical elevated state lasts a few days, not 20 sessions.
- Tail table: SPY did have one +8% day (2025-04-09, +10.5%). The answer only discusses the down side. Eight overlapping 5-session windows were at or below -8%, all from one episode (April 2025, worst -11.5% ending 2025-04-08). A multi-day 8% fall happened once in this sample; a one-day 8% fall never did.
- Minor issues: dead code at study.py lines 167-169. The unconditional baselines use the full sample period (fine as a measurement, not a signal). The MRNA +177% on 2026-08-19 was dropped as a 'split' but is more likely a data error or reverse split. No look-ahead found in the clustering signal: sigma60 and typ are shifted by one day, and the outcome is t+1.

### Calendar and streak effects

Over 2021-2026, SPY's next-day return after 4 or more consecutive down closes has averaged above normal: +0.62% in 2021-24 (t=3.6, but only 18 episodes and mostly from 2022) and +0.88% in 2025-26 (t=1.1, 10 episodes). The 2025-26 figure depends almost entirely on the 2025-04-09 tariff-pause day. Without April 2025 it is +0.24% (t=0.5), and without its 2 best days it is -0.08%. The median hold-out excess (+0.52%) and the hit rate (11/16) still point the same way but are not significant (binomial p=0.17). The best the evidence supports is a weak, non-significant tendency for SPY to bounce after sharp multi-day selloffs in high-volatility regimes. It is not a reliable streak-specific edge, and none of the 54 calendar rules survives Holm. The Thanksgiving and other calendar rules are noise or untestable (n=5).

**Rule still worth a paper test:** Paper-log only, and with no prior that it works. At each SPY close where SPY has closed lower than the previous close on 4 or more consecutive sessions, record a hypothetical long SPY from that close to the next close. Log only the first day each streak reaches 4, so there is one trade per streak. Pre-commit to evaluating after 20 new streak episodes. Count it as alive only if the mean after the 0.15% cost is > 0, the median is > 0, and the result survives dropping its single largest trade. Do not use short-dated options on it: the hit rate on independent episodes has a 95% CI that easily includes the 58-65% break-even.

Errors and weaknesses the reviewer found:

- No look-ahead found. The streak signal uses closes up to day t and the outcome is day t+1 (shift is correct). The Friday and Turnaround Tuesday rules are aligned correctly. The calendar dates do not depend on outcomes. SPY and QQQ are one value per date, so their t-statistics really are date-level. There were 0 split-like moves (>45%) in SPY or QQQ.
- The hold-out result for the 'strongest effect' depends on one outlier. Of the 16 hold-out trades, 2025-04-09 (the tariff-pause day, +10.45%) is the largest. Without April 2025 the hold-out mean falls from +0.88% to +0.24% (t=0.49). Without the top 2 outcomes (+10.45% and the +4.66% excess on 2026-11-23) it is -0.08%. Because the 'holds' verdict is driven by the mean, it rests on a single news day.
- The discovery edge is concentrated in the 2022 bear market. The top 3 outcomes are all from 2022 (Mar 8, Aug 9, Oct 12). The hit rate of 22/33 against a 52.6% base gives a one-sided binomial p of only 0.073. The hold-out hit rate of 11/16 against 53.5% gives p=0.165.
- Overlapping signals: a 7-day streak fires 4 times. Counting only the first day each streak reaches 4 gives disc n=22, +0.50%, t=2.49 and hold n=11, +1.10%, t=1.14. The hold-out is still dominated by April 2025.
- The narrative about last week is wrong. SPY fell 7 sessions in a row (Nov 12 to Nov 20), not 4. The rule as written bought at the Nov 17, 18, 19 and 20 closes. Those trades returned -0.31%, -1.96%, -3.64% and +4.71%, a net loss of about 1.3% over 4 trades before the bounce paid. So the recent episode is not clean support for the rule.
- Multiple testing and nesting. k=3/4/5 are nested variants of one idea, and the winner sits at BH q~0.055 (p=0.001 x 54). Holm finds 0 of 54. The shift-20 placebo still shows +0.30% in discovery, so part of the discovery 'edge' is a general high-volatility 2022 rebound drift, not anything specific to the streak.
- Minor: the excess is measured against the period mean including the event days (negligible). The R06/R07 Thanksgiving rules (n=5) should not have been counted as testable in the BH family.

## Bottom line

- **Direction:** no rule for next-day or next-week direction survived. Next-day hit rates for every signal sit between about 42% and 56%, below the 58% to 65% a short-dated option buyer needs.
- **Size:** large moves cluster. After a stock closes more than 3 of its own daily sigmas away, the chance that the next day is more than 1.5 times a typical day rises from about 22% to 36% (2021-24) and 45% (2025-26). It fades within about 5 sessions and says nothing about direction.
- **Options on size:** whether buying next-day options after a shock pays cannot be decided from daily bars; it depends on what the options cost, and there is no option price history here.
- **An 8% day in SPY:** none in this sample; the worst was -5.85% on 2025-04-04.

## News scout on the morning of 2026-11-24

Note: this was searched via WebSearch only (9 searches). Several results disagree with each other or carry mislabeled dates, so figures are provisional.

1. Why stocks are lower (11-24)
- Chip/AI weakness is the main reported driver (Yahoo Finance live blog, Invezz, Investing.com). Nasdaq opened ~1.6% lower, and intraday readings of -1.3% to -1.8% were reported. Headlines framed it as "Nvidia rally fades" and "investors test Nvidia-fueled rally."
- Rates: coverage cites renewed rate-hike expectations and this week's inflation reading as the last big data point before the December Fed meeting (BigGo, summary of search).
- Benzinga attributed futures weakness to Bessent unveiling fresh Iran sanctions and Hormuz warnings. A separate search could not confirm this.
- Context from sources: Friday 11-20 the S&P entered correction territory; Monday it closed 7,237.17 (+4.73%).
- Nvidia follow-ups: Reuters and Nvidia's 8-K (11-23) say the board voted unanimously after input from senior US government levels, and Trump publicly backed the move. Both parties criticized Trump's role (Schumer among them). An independent board committee's investigation continues.
- SEC: the WSJ reported a civil inquiry into Nvidia disclosures (Singapore sales). The SEC would not confirm. Date not found.

2. AI names this morning
- Nvidia about -5% after +31% Monday. Micron -8%, AMD -9%, Broadcom -4% (summary of Investing.com, Yahoo Finance). Reasons given: fading rally, rate fears. Intraday figures only.

3. Background
- 11-12: Nvidia announced Huang's resignation. Colette Kress became interim CEO and Harvey Jones became chair. The board cited personal conduct violating the code of conduct. Reuters described extramarital affairs.
- Earnings call (about 11-17, per sources): Jones said the board "does not intend to reinstate Jensen." Huang said he would be "honoured" to return. Nvidia fell more than 10% after his exit.
- Weekend: Trump-brokered reversal reported. 8-K dated 11-23.

4. Scheduled events
- Not found for 2026. Only Thanksgiving timing is known. The Fed's calendar page showed a possible 11-25 statistical release, but the snippet was jumbled.
- By pattern from prior years, Wednesday would carry jobless claims, durable goods and GDP revision, and a 20-year auction may be early in the week. Treat these as unverified.

5. Strategists on "relief rally being sold"
- No named strategist quotes found for 11-23 or 11-24. Yahoo Finance says it is "testing" the rally. Fool.com and 24/7 Wall St ran pieces on the correction and the record point gain.

Most likely explanation for this morning's fall
- Partial profit-taking and fading of Monday's one-day Nvidia-driven relief rally, which closed below its open, amid chip weakness and rate and inflation worries, with possible geopolitical news (Iran and Hormuz) on top. Confidence: medium-low, because no source explicitly attributes it to profit-taking and the news-flow evidence is mixed.

Could not verify
- Named strategist commentary
- 2026 data, auction and Fed-speaker schedule
- Final or exact morning index levels
- SEC inquiry date or confirmation
- The Bessent and Iran item
- Any conditions attached to Huang's reinstatement or board changes
- Which news stories are stale: several results are 2024-25 and were discarded.
- Exact date of the board statement: sources say it came on the earnings call, while your brief says 11-17 (the AP item dated 11-17 mentions earnings, and the 11-12 resignation date is from Nvidia's release).

## Files

- `<team>/study.py` and `results.txt`: each team's pre-registered study and its output.
- `<team>/review/`: the reviewer's independent scripts and outputs.
- `workflow-result.json`: the full structured output of all fourteen agents, including the synthesis.
