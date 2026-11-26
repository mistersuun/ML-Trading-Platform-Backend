# Pooled shadow, forward scoring

As of 2026-11-25. Rules frozen in `PREREG.md` (2026-11-14). Paper only: pooled signals are never orders. Informational; only the looks on 2026-12-31, 2027-02-26, 2027-04-15 decide.

Signals recorded: 320 over 17 sessions. De-duplicated trades: 209: 126 completed, 83 open.

| Statistic | Mean | One-sided p | Entry weeks |
|---|---:|---:|---:|
| Trade excess vs SPY (same window) | -1.26% | n/a | 2 |
| Excess vs the symbol's all-days control | -0.51% | n/a | 2 |
| Same, without the best 2 entry weeks | n/a | | |

Size: 126 completed trades (needs 60) and 2 entry weeks (needs 10): not met, so inference is n/a. Pass rule: both p < 0.0167 and the ex-top-2 mean > 0. 

## By pattern (descriptive, no test)

| Pattern | Completed | Mean excess vs SPY | Mean vs control |
|---|---:|---:|---:|
| keltner_reversion | 12 | -1.11% | -1.00% |
| trend_momentum_combo | 11 | +0.64% | +1.95% |
| rsi_divergence | 9 | -0.96% | +0.52% |
| bollinger_bounce | 9 | +1.90% | +3.03% |
| atr_breakout | 9 | -1.68% | -1.28% |
| stochastic_cross | 9 | +0.05% | +1.76% |
| ichimoku_cloud | 9 | -2.48% | -2.90% |
| inside_bar_breakout | 8 | -1.16% | +0.24% |
| macd_crossover | 6 | -3.72% | -3.43% |
| sma_200_trend | 6 | -4.45% | -2.74% |
| ema_crossover | 6 | -1.24% | -0.61% |
| donchian_breakout | 5 | -0.77% | -0.34% |
| triple_ema | 5 | -1.22% | -0.78% |
| rsi_reversal | 5 | -5.44% | -2.49% |
| mean_rev_composite | 5 | -2.27% | +0.81% |
| rsi2_pullback | 4 | +2.60% | -0.57% |
| adx_trend | 2 | -0.01% | +0.50% |
| high52_breakout | 2 | +0.02% | -0.86% |
| volume_breakout | 2 | -0.47% | +1.44% |
| confluence | 1 | -3.41% | -1.82% |
| bollinger_squeeze | 1 | +4.05% | -1.05% |

## Open trades (marked, not scored)

| Pattern | Symbol | Entry | Mark | Return | vs SPY |
|---|---|---|---|---:|---:|
| donchian_breakout | AMD | 2026-11-13 | 2026-11-25 | -28.36% | -22.66% |
| macd_crossover | GOOG | 2026-11-13 | 2026-11-25 | -9.64% | -3.93% |
| triple_ema | TSLA | 2026-11-13 | 2026-11-25 | -5.44% | +0.27% |
| ichimoku_cloud | CVX | 2026-11-16 | 2026-11-25 | -4.13% | +0.27% |
| keltner_reversion | CVX | 2026-11-16 | 2026-11-25 | -4.13% | +0.27% |
| rsi2_pullback | AMZN | 2026-11-17 | 2026-11-25 | -4.99% | -2.05% |
| rsi2_pullback | SPY | 2026-11-17 | 2026-11-25 | -2.94% | +0.00% |
| rsi2_pullback | JPM | 2026-11-18 | 2026-11-25 | -3.29% | -1.02% |
| rsi2_pullback | MSFT | 2026-11-18 | 2026-11-25 | -3.08% | -0.81% |
| rsi_divergence | GOOG | 2026-11-18 | 2026-11-25 | -3.37% | -1.10% |
| bollinger_bounce | IWM | 2026-11-19 | 2026-11-25 | -0.82% | +1.99% |
| ichimoku_cloud | AMZN | 2026-11-19 | 2026-11-25 | -10.33% | -7.52% |
| rsi2_pullback | CVX | 2026-11-19 | 2026-11-25 | +1.67% | +4.48% |
| rsi2_pullback | XOM | 2026-11-19 | 2026-11-25 | -2.01% | +0.79% |
| stochastic_cross | GOOG | 2026-11-19 | 2026-11-25 | -6.77% | -3.96% |
| stochastic_cross | IWM | 2026-11-19 | 2026-11-25 | -0.82% | +1.99% |
| stochastic_cross | QQQ | 2026-11-19 | 2026-11-25 | -5.54% | -2.73% |
| rsi_divergence | GS | 2026-11-20 | 2026-11-25 | -3.08% | -3.51% |
| rsi_divergence | META | 2026-11-20 | 2026-11-25 | +4.63% | +4.20% |
| rsi_divergence | QQQ | 2026-11-20 | 2026-11-25 | -0.77% | -1.20% |
| stochastic_cross | BAC | 2026-11-20 | 2026-11-25 | +0.19% | -0.25% |
| stochastic_cross | JPM | 2026-11-20 | 2026-11-25 | -1.25% | -1.69% |
| rsi2_pullback | AAPL | 2026-11-23 | 2026-11-25 | +2.22% | +3.28% |
| rsi_divergence | TSLA | 2026-11-23 | 2026-11-25 | +1.40% | +2.46% |
| rsi_divergence | XOM | 2026-11-23 | 2026-11-25 | +0.09% | +1.15% |
| stochastic_cross | CVX | 2026-11-23 | 2026-11-25 | +2.40% | +3.46% |
| stochastic_cross | DIA | 2026-11-23 | 2026-11-25 | +1.43% | +2.49% |
| stochastic_cross | XOM | 2026-11-23 | 2026-11-25 | +0.09% | +1.15% |
| atr_breakout | DIA | 2026-11-24 | 2026-11-25 | +1.58% | +1.37% |
| atr_breakout | IWM | 2026-11-24 | 2026-11-25 | +1.84% | +1.62% |
| atr_breakout | NVDA | 2026-11-24 | 2026-11-25 | -6.16% | -6.37% |
| atr_breakout | QQQ | 2026-11-24 | 2026-11-25 | -0.96% | -1.17% |
| atr_breakout | SPY | 2026-11-24 | 2026-11-25 | +0.21% | +0.00% |
| atr_breakout | TSLA | 2026-11-24 | 2026-11-25 | -0.86% | -1.08% |
| bollinger_bounce | BAC | 2026-11-24 | 2026-11-25 | +1.38% | +1.17% |
| bollinger_bounce | DIA | 2026-11-24 | 2026-11-25 | +1.58% | +1.37% |
| bollinger_bounce | GS | 2026-11-24 | 2026-11-25 | -1.81% | -2.02% |
| bollinger_bounce | JPM | 2026-11-24 | 2026-11-25 | +0.62% | +0.41% |
| bollinger_bounce | META | 2026-11-24 | 2026-11-25 | +3.53% | +3.32% |
| bollinger_bounce | MSFT | 2026-11-24 | 2026-11-25 | -0.18% | -0.39% |
| bollinger_bounce | QQQ | 2026-11-24 | 2026-11-25 | -0.96% | -1.17% |
| bollinger_bounce | SPY | 2026-11-24 | 2026-11-25 | +0.21% | +0.00% |
| bollinger_bounce | TSLA | 2026-11-24 | 2026-11-25 | -0.86% | -1.08% |
| confluence | BAC | 2026-11-24 | 2026-11-25 | +1.38% | +1.17% |
| confluence | GS | 2026-11-24 | 2026-11-25 | -1.81% | -2.02% |
| ichimoku_cloud | AAPL | 2026-11-24 | 2026-11-25 | +2.12% | +1.91% |
| ichimoku_cloud | TSLA | 2026-11-24 | 2026-11-25 | -0.86% | -1.08% |
| keltner_reversion | AMZN | 2026-11-24 | 2026-11-25 | -3.00% | -3.22% |
| keltner_reversion | GOOG | 2026-11-24 | 2026-11-25 | -2.82% | -3.03% |
| keltner_reversion | TSLA | 2026-11-24 | 2026-11-25 | -0.86% | -1.08% |
| mean_rev_composite | BAC | 2026-11-24 | 2026-11-25 | +1.38% | +1.17% |
| mean_rev_composite | DIA | 2026-11-24 | 2026-11-25 | +1.58% | +1.37% |
| mean_rev_composite | GS | 2026-11-24 | 2026-11-25 | -1.81% | -2.02% |
| mean_rev_composite | IWM | 2026-11-24 | 2026-11-25 | +1.84% | +1.62% |
| mean_rev_composite | JPM | 2026-11-24 | 2026-11-25 | +0.62% | +0.41% |
| mean_rev_composite | META | 2026-11-24 | 2026-11-25 | +3.53% | +3.32% |
| mean_rev_composite | MSFT | 2026-11-24 | 2026-11-25 | -0.18% | -0.39% |
| mean_rev_composite | NVDA | 2026-11-24 | 2026-11-25 | -6.16% | -6.37% |
| mean_rev_composite | QQQ | 2026-11-24 | 2026-11-25 | -0.96% | -1.17% |
| mean_rev_composite | SPY | 2026-11-24 | 2026-11-25 | +0.21% | +0.00% |
| rsi_reversal | BAC | 2026-11-24 | 2026-11-25 | +1.38% | +1.17% |
| rsi_reversal | DIA | 2026-11-24 | 2026-11-25 | +1.58% | +1.37% |
| rsi_reversal | GOOG | 2026-11-24 | 2026-11-25 | -2.82% | -3.03% |
| rsi_reversal | GS | 2026-11-24 | 2026-11-25 | -1.81% | -2.02% |
| rsi_reversal | NVDA | 2026-11-24 | 2026-11-25 | -6.16% | -6.37% |
| rsi_reversal | QQQ | 2026-11-24 | 2026-11-25 | -0.96% | -1.17% |
| rsi_reversal | SPY | 2026-11-24 | 2026-11-25 | +0.21% | +0.00% |
| sma_200_trend | AMD | 2026-11-24 | 2026-11-25 | -4.49% | -4.71% |
| stochastic_cross | GS | 2026-11-24 | 2026-11-25 | -1.81% | -2.02% |
| stochastic_cross | MSFT | 2026-11-24 | 2026-11-25 | -0.18% | -0.39% |
| trend_momentum_combo | DIA | 2026-11-24 | 2026-11-25 | +1.58% | +1.37% |
| trend_momentum_combo | IWM | 2026-11-24 | 2026-11-25 | +1.84% | +1.62% |
| trend_momentum_combo | META | 2026-11-24 | 2026-11-25 | +3.53% | +3.32% |
| trend_momentum_combo | MSFT | 2026-11-24 | 2026-11-25 | -0.18% | -0.39% |
| trend_momentum_combo | QQQ | 2026-11-24 | 2026-11-25 | -0.96% | -1.17% |
| trend_momentum_combo | SPY | 2026-11-24 | 2026-11-25 | +0.21% | +0.00% |
| inside_bar_breakout | AAPL | 2026-11-25 | 2026-11-25 | +0.56% | -1.36% |
| trend_momentum_combo | JPM | 2026-11-25 | 2026-11-25 | +1.99% | +0.07% |
| triple_ema | AAPL | 2026-11-25 | 2026-11-25 | +0.56% | -1.36% |
| inside_bar_breakout | META | next session | - | n/a | n/a |
| keltner_reversion | META | next session | - | n/a | n/a |
| macd_crossover | BAC | next session | - | n/a | n/a |
| rsi_divergence | AMD | next session | - | n/a | n/a |

Costs: 15 bp per round trip on completed trades; open marks carry no cost. Entry at the next open, exit at the 10th session's close. Signals from 10-15 to 11-02 were lost in the 11-03 reset and are not included.
