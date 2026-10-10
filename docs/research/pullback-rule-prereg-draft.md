# Pre-registration DRAFT: consolidated short-term pullback in an uptrend (`pullback_uptrend`)

- **Date:** 2026-10-10
- **Status:** **DRAFT. Not frozen, not registered, not counted in N, not implemented.** It becomes a pre-registration only when decision D21 freezes its SHA-256 (D20 item 6) and the owner confirms. Until then the text may change, and no return of this rule may be computed on any data.
- **Source:** [`model-review-2026-10.md`](model-review-2026-10.md) programme item 5; the existing pre-registration [`preregistration-2026-10.md`](preregistration-2026-10.md) (same discipline: frozen hash, counts-only disclosure, rules from the literature with no tuning).
- **Not implemented before 2026-10-15.** The forward paper test runs nightly until 2026-10-14; nothing is added to `patterns.py`, `patterns_prereg.py` or the pooled registry while it runs.

## 1. Why one rule

The short-term mean-reversion family (`rsi_divergence`, `bollinger_bounce`, `stochastic_cross`, `rsi_reversal`, `mean_rev_composite`, `rsi2_pullback`) is the only family in the review that beat its hold-matched random baseline under every exit variant tested (+0.05 to +0.27 R per trade), with cluster-null p-values of 0.09 to 0.49: suggestive, not significant. Its six members are 0.6 to 0.86 correlated. One rule, tested once, says what six near-duplicates say with six times the multiplicity. Literature grade C (Connors and Alvarez 2008; IBS), replicated by practitioners, decayed since 2010 (review item 5). It does NOT replace the six existing rules in N: retired patterns stay in N.

## 2. Hypothesis (H3)

On US large caps and index ETFs in a long-term uptrend (close above the 200-day SMA), a short-term oversold reading (close below the lower 20-day Bollinger band, or RSI(3) below 15) is followed by a rebound toward the 5-day mean. Pooled across the universe at equal risk, with next-open execution and costs, the rule beats the long-only random-entry null and clears the D18 gates at its registered N. Expected outcome, per the review: shadow-only or no pass. Prior in the review: 30-40% to clear BH against the cluster null on 20-year data, 10-15% to clear the DSR; the 1-bar delay stress is the likely killer (the rebound is gone by the next open, as `rsi2_pullback` showed).

## 3. The rule (parameters fixed from the literature, no tuning)

| Element | Fixed value | Source |
|---|---|---|
| Trend filter | close > SMA(200) | Connors and Alvarez 2008 |
| Oversold A | close < lower Bollinger band, window 20, 2 standard deviations (`ta.volatility.BollingerBands(window=20, window_dev=2)`) | Bollinger's default; the repo's `bollinger_bounce` uses the same default |
| Oversold B | RSI(3) < 15 (`ta.momentum.rsi(close, window=3, fillna=False)`, Wilder smoothing as in the existing pre-registration) | short-window RSI of the Connors family; 3 and 15 are the review's values (D21 should cite the exact source page before freezing); 10 and 5 of `rsi2_pullback` are NOT re-tested |
| Entry | A OR B, AND the trend filter, on the signal bar's close; fills at the next open (`EXECUTION_MODE = "next_open"`) | review item 5 |
| Exit | the first of: a close above SMA(5) (signal exit at the next open); 10 bars held (time exit); the 2 x ATR(20) stop | review item 5 ("exit on close > SMA(5) or a 10-bar time stop; stop only, no target") |
| Target | NONE. The 3 x ATR target of the executable bracket is not used. | review item 5 |
| Direction / sizing | long-only; the equal-risk sleeve of D18 (risk per trade over the stop fraction) | D3, D18 |
| Universe | the pooled universe actually registered for the run (D22: A, B or C); no per-symbol selection | D18, D22 |
| Gates | exactly the D18 §5 pooled gates as built; hold-out read once; shadow-only on success | D18, D19 |

```python
def pullback_uptrend(df):
    out = df.copy()
    c = out["Close"]
    sma200 = c.rolling(200, min_periods=200).mean()
    sma5 = c.rolling(5, min_periods=5).mean()
    lband = ta.volatility.BollingerBands(c, window=20, window_dev=2).bollinger_lband()
    rsi3 = ta.momentum.rsi(c, window=3, fillna=False)
    entry = (c > sma200) & ((c < lband) | (rsi3 < 15))
    exit_ = (c > sma5) & ~entry          # entry wins if both hold on the same bar
    out["signal"] = 0
    out.loc[entry, "signal"] = 1
    out.loc[exit_, "signal"] = -1
    return out
```

The 10-bar time exit and the stop-only bracket are not part of the signal function: they are exit-engine properties.

**Exit convention (engine terms, to be frozen by D21):** the time exit is a `max_hold` of 10 bars counted from the entry fill bar (the fill bar is bar 1), filled at the open of bar 11. Precedence when several exits coincide on one bar: the 2 x ATR stop (intrabar) first, then the SMA(5) signal exit or the time exit, both at the next open.
**Implementation prerequisite (an open point, not decided here):** the executable bracket has no time stop today, and D20 item 1 adds a 20-bar one for the whole registry. This rule needs a pattern-specific 10-bar time exit and no target. How the engine carries a per-pattern exit spec (and whether this is part of the D20 version bump or a separate exit version) must be settled before D21 freezes, because the answer decides N. Nothing in this draft assumes it.

## 4. Counts-only feasibility (pre-hold-out, no returns)

Computed 2026-10-10 on `state/external_bars` for the 17 symbols of universe A, frames cut to dates strictly before `HOLDOUT_START = 2025-07-01` immediately after loading (last bar used 2025-06-30). The script prints trade counts, entry weeks (distinct ISO year-weeks of the entry bar) and symbols only. No return, P&L, Sharpe, win rate, equity curve or hold-out bar was computed or viewed; the hold-out store and `state/forward/*` were not opened. Counts were computed on the rule as written in section 3 with the signal exit and the 10-bar time exit (the exit convention of section 3 is the intended one; the one-off count script was not saved in the repository and did not record its bar-counting convention, so these counts are approximate and are not exactly reproducible; D21 must regenerate them with a committed script that follows section 3 before freezing), **without the ATR stop** (as in the existing pre-registration) and without the capped replay, one position per symbol at a time, entries at the next open. No parameter was chosen or changed from these counts: they are a feasibility check against the D18 count gates only.

| Window | Trades | Entry weeks | Symbols with >= 3 trades |
|---|---|---|---|
| All pre-hold-out bars with a valid SMA(200) (934 bars per symbol; META 766) | 241 | 81 | 17 of 17 |
| Signal bar at index 252 or later (an approximation of the 252-bar warm-up OOS construction of D18(c), 5 years of data) | 237 | 78 | 17 of 17 |

Per symbol (all valid bars): AAPL 16, AMD 15, AMZN 9, BAC 12, CVX 7, DIA 20, GOOG 16, GS 14, IWM 11, JPM 20, META 13, MSFT 12, NVDA 18, QQQ 17, SPY 16, TSLA 11, XOM 14.

Reading, counts only: the rule would clear the D18 count gates on universe A (>= 60 trades, >= 30 entry-week clusters, breadth >= 9 symbols with >= 3 trades). The trade count is below the 343-387 of `rsi2_pullback` because the entry needs the lower-band OR RSI(3) < 15 condition and holds are longer than 1 to 5 days. A hold-out estimate by extrapolation would be a guess and is not given. The counts for B and C are not computed (no B or C bar has been loaded, D22).

## 5. Trial accounting

| Item | Count |
|---|---|
| Pooled N today (D18, D19) | 22 |
| This rule, once frozen by D21 | N 22 -> 23 (review item 5): required pooled Sharpe 2.40 -> 2.41 at T = 672 days and 0.87 -> 0.88 at 20 years (review section 2.4) |
| The six existing mean-reversion rules | stay registered and in N; not replaced |
| Universe or exit version bumps | separate: D22 universe registration N += 22 once; the D20 time-stop version N += 22. If this rule is first looked at on a version that carries those bumps, the N of that version applies; the review table prices 23, 44 and 66 but no combination of them with this rule, and none is claimed here |

First look: the long-history run (D22 / D20 item 8), then the forward shadow as confirmation. The rule is not looked at on the 5-year bars before D21 freezes.

## 6. Locking rules (same as the existing pre-registration §7)

1. After D21 freezes this text, no parameter, rule, exit or universe change without a new trial (a new version, N += 1 for this pattern; the D18 §10 read cap applies).
2. Bug fixes restore the rule exactly as written, with a recorded before/after diff; anything else is a new trial.
3. The full pooled funnel is reported whether it passes or fails; a failed rule stays in N.
4. No selection between this rule and the six existing ones on results.
5. Integrity: D21 records the SHA-256 of this file (with its DRAFT status line replaced by the frozen status; the hash lives in `docs/decisions.md`, not in the file, as in the existing pre-registration), and the implemented function is tested against the code block in section 3 with an AST-equality fixture test.
6. The 1-bar delay stress, next-open entry and 0.3% round-trip costs are accepted handicaps and are not tuned around.

## 7. What D21 must still settle before freezing

- The per-pattern exit spec (10-bar time exit, no target) and its N accounting (section 3).
- That this rule's registry lives in the separate research module (as `PREREG_PATTERNS`), not in `PATTERN_REGISTRY`, so `universe_trials()` and the per-symbol `strategy_version` stay byte-identical.
- The owner's dated confirmation line.
