# Pooled shadow, forward scoring

As of 2026-11-20. Rules frozen in `PREREG.md` (2026-11-14). Paper only: pooled signals are never orders. Informational; only the looks on 2026-12-31, 2027-02-26, 2027-04-15 decide.

Signals recorded: 237 over 14 sessions. De-duplicated trades: 151: 89 completed, 62 open.

| Statistic | Mean | One-sided p | Entry weeks |
|---|---:|---:|---:|
| Trade excess vs SPY (same window) | -1.56% | n/a | 2 |
| Excess vs the symbol's all-days control | -1.44% | n/a | 2 |
| Same, without the best 2 entry weeks | n/a | | |

Size: 89 completed trades (needs 60) and 2 entry weeks (needs 10): not met, so inference is n/a. Pass rule: both p < 0.0167 and the ex-top-2 mean > 0. 

## By pattern (descriptive, no test)

| Pattern | Completed | Mean excess vs SPY | Mean vs control |
|---|---:|---:|---:|
| stochastic_cross | 9 | +0.05% | +1.30% |
| rsi_divergence | 8 | -1.48% | +1.39% |
| trend_momentum_combo | 8 | +0.88% | +2.11% |
| bollinger_bounce | 7 | +1.89% | -0.64% |
| atr_breakout | 6 | -1.11% | -1.10% |
| keltner_reversion | 6 | -0.19% | -0.66% |
| ichimoku_cloud | 6 | -1.00% | -1.21% |
| mean_rev_composite | 5 | -2.27% | -0.06% |
| inside_bar_breakout | 5 | -0.08% | +0.75% |
| triple_ema | 4 | -0.86% | -1.46% |
| rsi_reversal | 4 | -6.98% | -4.27% |
| rsi2_pullback | 4 | +2.60% | -0.70% |
| donchian_breakout | 4 | -2.01% | -0.64% |
| macd_crossover | 2 | +0.57% | +0.47% |
| high52_breakout | 2 | +0.02% | -1.09% |
| sma_200_trend | 2 | -11.59% | -6.38% |
| ema_crossover | 2 | -1.26% | -0.06% |
| volume_breakout | 2 | -0.47% | +0.73% |
| confluence | 1 | -3.41% | -13.02% |
| adx_trend | 1 | -0.34% | +0.37% |
| bollinger_squeeze | 1 | +4.05% | -1.63% |

## Open trades (marked, not scored)

| Pattern | Symbol | Entry | Mark | Return | vs SPY |
|---|---|---|---|---:|---:|
| atr_breakout | AMD | 2026-11-10 | 2026-11-20 | -15.66% | -7.89% |
| atr_breakout | IWM | 2026-11-10 | 2026-11-20 | -8.28% | -0.51% |
| atr_breakout | SPY | 2026-11-10 | 2026-11-20 | -7.77% | +0.00% |
| bollinger_bounce | XOM | 2026-11-10 | 2026-11-20 | -1.16% | +6.62% |
| ichimoku_cloud | TSLA | 2026-11-10 | 2026-11-20 | -6.66% | +1.11% |
| inside_bar_breakout | GS | 2026-11-10 | 2026-11-20 | -13.13% | -5.36% |
| inside_bar_breakout | META | 2026-11-10 | 2026-11-20 | -12.04% | -4.27% |
| keltner_reversion | AMD | 2026-11-10 | 2026-11-20 | -15.66% | -7.89% |
| keltner_reversion | DIA | 2026-11-10 | 2026-11-20 | -7.75% | +0.02% |
| keltner_reversion | GS | 2026-11-10 | 2026-11-20 | -13.13% | -5.36% |
| keltner_reversion | META | 2026-11-10 | 2026-11-20 | -12.04% | -4.27% |
| keltner_reversion | TSLA | 2026-11-10 | 2026-11-20 | -6.66% | +1.11% |
| macd_crossover | MSFT | 2026-11-10 | 2026-11-20 | -10.80% | -3.03% |
| rsi_reversal | GOOG | 2026-11-10 | 2026-11-20 | -8.00% | -0.23% |
| sma_200_trend | IWM | 2026-11-10 | 2026-11-20 | -8.28% | -0.51% |
| sma_200_trend | META | 2026-11-10 | 2026-11-20 | -12.04% | -4.27% |
| trend_momentum_combo | DIA | 2026-11-10 | 2026-11-20 | -7.75% | +0.02% |
| adx_trend | BAC | 2026-11-11 | 2026-11-20 | -7.53% | +1.32% |
| ema_crossover | DIA | 2026-11-11 | 2026-11-20 | -7.98% | +0.86% |
| ema_crossover | IWM | 2026-11-11 | 2026-11-20 | -9.33% | -0.48% |
| rsi_divergence | CVX | 2026-11-11 | 2026-11-20 | -1.86% | +6.98% |
| triple_ema | JPM | 2026-11-11 | 2026-11-20 | -9.44% | -0.59% |
| bollinger_bounce | NVDA | 2026-11-12 | 2026-11-20 | -16.86% | -8.35% |
| donchian_breakout | AAPL | 2026-11-12 | 2026-11-20 | -4.26% | +4.25% |
| ema_crossover | GS | 2026-11-12 | 2026-11-20 | -13.75% | -5.24% |
| ema_crossover | TSLA | 2026-11-12 | 2026-11-20 | -10.46% | -1.95% |
| ichimoku_cloud | AMD | 2026-11-12 | 2026-11-20 | -25.49% | -16.97% |
| ichimoku_cloud | IWM | 2026-11-12 | 2026-11-20 | -8.82% | -0.31% |
| inside_bar_breakout | DIA | 2026-11-12 | 2026-11-20 | -8.26% | +0.25% |
| keltner_reversion | QQQ | 2026-11-12 | 2026-11-20 | -10.56% | -2.05% |
| macd_crossover | AAPL | 2026-11-12 | 2026-11-20 | -4.26% | +4.25% |
| macd_crossover | AMD | 2026-11-12 | 2026-11-20 | -25.49% | -16.97% |
| macd_crossover | META | 2026-11-12 | 2026-11-20 | -10.33% | -1.81% |
| sma_200_trend | QQQ | 2026-11-12 | 2026-11-20 | -10.56% | -2.05% |
| sma_200_trend | TSLA | 2026-11-12 | 2026-11-20 | -10.46% | -1.95% |
| trend_momentum_combo | CVX | 2026-11-12 | 2026-11-20 | -3.08% | +5.43% |
| trend_momentum_combo | XOM | 2026-11-12 | 2026-11-20 | -4.91% | +3.60% |
| donchian_breakout | AMD | 2026-11-13 | 2026-11-20 | -25.12% | -15.88% |
| macd_crossover | GOOG | 2026-11-13 | 2026-11-20 | -11.97% | -2.72% |
| triple_ema | TSLA | 2026-11-13 | 2026-11-20 | -13.97% | -4.73% |
| ichimoku_cloud | CVX | 2026-11-16 | 2026-11-20 | -5.30% | +2.69% |
| keltner_reversion | CVX | 2026-11-16 | 2026-11-20 | -5.30% | +2.69% |
| rsi2_pullback | AMZN | 2026-11-17 | 2026-11-20 | -4.06% | +2.52% |
| rsi2_pullback | SPY | 2026-11-17 | 2026-11-20 | -6.58% | +0.00% |
| rsi2_pullback | JPM | 2026-11-18 | 2026-11-20 | -5.19% | +0.75% |
| rsi2_pullback | MSFT | 2026-11-18 | 2026-11-20 | -4.15% | +1.79% |
| rsi_divergence | GOOG | 2026-11-18 | 2026-11-20 | -5.86% | +0.08% |
| bollinger_bounce | IWM | 2026-11-19 | 2026-11-20 | -5.34% | +1.11% |
| ichimoku_cloud | AMZN | 2026-11-19 | 2026-11-20 | -9.45% | -3.00% |
| rsi2_pullback | CVX | 2026-11-19 | 2026-11-20 | +0.43% | +6.88% |
| rsi2_pullback | XOM | 2026-11-19 | 2026-11-20 | -0.83% | +5.62% |
| stochastic_cross | GOOG | 2026-11-19 | 2026-11-20 | -9.17% | -2.72% |
| stochastic_cross | IWM | 2026-11-19 | 2026-11-20 | -5.34% | +1.11% |
| stochastic_cross | QQQ | 2026-11-19 | 2026-11-20 | -9.33% | -2.88% |
| rsi_divergence | GS | 2026-11-20 | 2026-11-20 | -4.33% | -1.00% |
| rsi_divergence | META | 2026-11-20 | 2026-11-20 | -3.78% | -0.45% |
| rsi_divergence | QQQ | 2026-11-20 | 2026-11-20 | -4.75% | -1.42% |
| stochastic_cross | BAC | 2026-11-20 | 2026-11-20 | -2.91% | +0.42% |
| stochastic_cross | JPM | 2026-11-20 | 2026-11-20 | -3.19% | +0.14% |
| rsi2_pullback | AAPL | next session | - | n/a | n/a |
| rsi_divergence | TSLA | next session | - | n/a | n/a |
| stochastic_cross | DIA | next session | - | n/a | n/a |

Costs: 15 bp per round trip on completed trades; open marks carry no cost. Entry at the next open, exit at the 10th session's close. Signals from 10-15 to 11-02 were lost in the 11-03 reset and are not included.
