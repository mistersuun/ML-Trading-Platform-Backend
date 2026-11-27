# Pooled shadow, forward scoring

As of 2026-11-27. Rules frozen in `PREREG.md` (2026-11-14). Paper only: pooled signals are never orders. Informational; only the looks on 2026-12-31, 2027-02-26, 2027-04-15 decide.

Signals recorded: 352 over 18 sessions. De-duplicated trades: 228: 129 completed, 99 open.

| Statistic | Mean | One-sided p | Entry weeks |
|---|---:|---:|---:|
| Trade excess vs SPY (same window) | -1.41% | n/a | 2 |
| Excess vs the symbol's all-days control | -0.50% | n/a | 2 |
| Same, without the best 2 entry weeks | n/a | | |

Size: 129 completed trades (needs 60) and 2 entry weeks (needs 10): not met, so inference is n/a. Pass rule: both p < 0.0167 and the ex-top-2 mean > 0. 

## By pattern (descriptive, no test)

| Pattern | Completed | Mean excess vs SPY | Mean vs control |
|---|---:|---:|---:|
| keltner_reversion | 12 | -1.11% | -0.82% |
| trend_momentum_combo | 11 | +0.64% | +2.10% |
| rsi_divergence | 9 | -0.96% | +0.45% |
| bollinger_bounce | 9 | +1.90% | +3.40% |
| atr_breakout | 9 | -1.68% | -1.05% |
| stochastic_cross | 9 | +0.05% | +2.04% |
| ichimoku_cloud | 9 | -2.48% | -2.63% |
| inside_bar_breakout | 8 | -1.16% | +0.14% |
| macd_crossover | 7 | -3.92% | -3.32% |
| triple_ema | 6 | -0.42% | -0.97% |
| sma_200_trend | 6 | -4.45% | -2.54% |
| ema_crossover | 6 | -1.24% | -0.59% |
| donchian_breakout | 6 | -4.16% | -3.15% |
| rsi_reversal | 5 | -5.44% | -2.00% |
| mean_rev_composite | 5 | -2.27% | +1.46% |
| rsi2_pullback | 4 | +2.60% | -0.38% |
| adx_trend | 2 | -0.01% | +0.28% |
| high52_breakout | 2 | +0.02% | -0.94% |
| volume_breakout | 2 | -0.47% | +1.39% |
| confluence | 1 | -3.41% | +0.61% |
| bollinger_squeeze | 1 | +4.05% | -0.94% |

## Open trades (marked, not scored)

| Pattern | Symbol | Entry | Mark | Return | vs SPY |
|---|---|---|---|---:|---:|
| ichimoku_cloud | CVX | 2026-11-16 | 2026-11-27 | -3.44% | -0.60% |
| keltner_reversion | CVX | 2026-11-16 | 2026-11-27 | -3.44% | -0.60% |
| rsi2_pullback | AMZN | 2026-11-17 | 2026-11-27 | -1.22% | +0.14% |
| rsi2_pullback | SPY | 2026-11-17 | 2026-11-27 | -1.36% | +0.00% |
| rsi2_pullback | JPM | 2026-11-18 | 2026-11-27 | -1.39% | -0.71% |
| rsi2_pullback | MSFT | 2026-11-18 | 2026-11-27 | -2.50% | -1.81% |
| rsi_divergence | GOOG | 2026-11-18 | 2026-11-27 | -2.85% | -2.16% |
| bollinger_bounce | IWM | 2026-11-19 | 2026-11-27 | +1.00% | +2.23% |
| ichimoku_cloud | AMZN | 2026-11-19 | 2026-11-27 | -6.77% | -5.54% |
| rsi2_pullback | CVX | 2026-11-19 | 2026-11-27 | +2.40% | +3.63% |
| rsi2_pullback | XOM | 2026-11-19 | 2026-11-27 | -0.71% | +0.52% |
| stochastic_cross | GOOG | 2026-11-19 | 2026-11-27 | -6.27% | -5.04% |
| stochastic_cross | IWM | 2026-11-19 | 2026-11-27 | +1.00% | +2.23% |
| stochastic_cross | QQQ | 2026-11-19 | 2026-11-27 | -3.00% | -1.77% |
| rsi_divergence | GS | 2026-11-20 | 2026-11-27 | -1.65% | -3.71% |
| rsi_divergence | META | 2026-11-20 | 2026-11-27 | +7.90% | +5.84% |
| rsi_divergence | QQQ | 2026-11-20 | 2026-11-27 | +1.91% | -0.16% |
| stochastic_cross | BAC | 2026-11-20 | 2026-11-27 | +1.28% | -0.78% |
| stochastic_cross | JPM | 2026-11-20 | 2026-11-27 | +0.69% | -1.38% |
| rsi2_pullback | AAPL | 2026-11-23 | 2026-11-27 | +3.07% | +2.52% |
| rsi_divergence | TSLA | 2026-11-23 | 2026-11-27 | +6.70% | +6.15% |
| rsi_divergence | XOM | 2026-11-23 | 2026-11-27 | +1.42% | +0.87% |
| stochastic_cross | CVX | 2026-11-23 | 2026-11-27 | +3.13% | +2.59% |
| stochastic_cross | DIA | 2026-11-23 | 2026-11-27 | +2.71% | +2.16% |
| stochastic_cross | XOM | 2026-11-23 | 2026-11-27 | +1.42% | +0.87% |
| atr_breakout | DIA | 2026-11-24 | 2026-11-27 | +2.86% | +1.03% |
| atr_breakout | IWM | 2026-11-24 | 2026-11-27 | +3.70% | +1.86% |
| atr_breakout | NVDA | 2026-11-24 | 2026-11-27 | -5.41% | -7.25% |
| atr_breakout | QQQ | 2026-11-24 | 2026-11-27 | +1.71% | -0.13% |
| atr_breakout | SPY | 2026-11-24 | 2026-11-27 | +1.84% | +0.00% |
| atr_breakout | TSLA | 2026-11-24 | 2026-11-27 | +4.32% | +2.48% |
| bollinger_bounce | BAC | 2026-11-24 | 2026-11-27 | +2.49% | +0.65% |
| bollinger_bounce | DIA | 2026-11-24 | 2026-11-27 | +2.86% | +1.03% |
| bollinger_bounce | GS | 2026-11-24 | 2026-11-27 | -0.36% | -2.20% |
| bollinger_bounce | JPM | 2026-11-24 | 2026-11-27 | +2.60% | +0.76% |
| bollinger_bounce | META | 2026-11-24 | 2026-11-27 | +6.77% | +4.93% |
| bollinger_bounce | MSFT | 2026-11-24 | 2026-11-27 | +0.42% | -1.42% |
| bollinger_bounce | QQQ | 2026-11-24 | 2026-11-27 | +1.71% | -0.13% |
| bollinger_bounce | SPY | 2026-11-24 | 2026-11-27 | +1.84% | +0.00% |
| bollinger_bounce | TSLA | 2026-11-24 | 2026-11-27 | +4.32% | +2.48% |
| confluence | BAC | 2026-11-24 | 2026-11-27 | +2.49% | +0.65% |
| confluence | GS | 2026-11-24 | 2026-11-27 | -0.36% | -2.20% |
| ichimoku_cloud | AAPL | 2026-11-24 | 2026-11-27 | +2.97% | +1.13% |
| ichimoku_cloud | TSLA | 2026-11-24 | 2026-11-27 | +4.32% | +2.48% |
| keltner_reversion | AMZN | 2026-11-24 | 2026-11-27 | +0.84% | -1.00% |
| keltner_reversion | GOOG | 2026-11-24 | 2026-11-27 | -2.30% | -4.13% |
| keltner_reversion | TSLA | 2026-11-24 | 2026-11-27 | +4.32% | +2.48% |
| mean_rev_composite | BAC | 2026-11-24 | 2026-11-27 | +2.49% | +0.65% |
| mean_rev_composite | DIA | 2026-11-24 | 2026-11-27 | +2.86% | +1.03% |
| mean_rev_composite | GS | 2026-11-24 | 2026-11-27 | -0.36% | -2.20% |
| mean_rev_composite | IWM | 2026-11-24 | 2026-11-27 | +3.70% | +1.86% |
| mean_rev_composite | JPM | 2026-11-24 | 2026-11-27 | +2.60% | +0.76% |
| mean_rev_composite | META | 2026-11-24 | 2026-11-27 | +6.77% | +4.93% |
| mean_rev_composite | MSFT | 2026-11-24 | 2026-11-27 | +0.42% | -1.42% |
| mean_rev_composite | NVDA | 2026-11-24 | 2026-11-27 | -5.41% | -7.25% |
| mean_rev_composite | QQQ | 2026-11-24 | 2026-11-27 | +1.71% | -0.13% |
| mean_rev_composite | SPY | 2026-11-24 | 2026-11-27 | +1.84% | +0.00% |
| rsi_reversal | BAC | 2026-11-24 | 2026-11-27 | +2.49% | +0.65% |
| rsi_reversal | DIA | 2026-11-24 | 2026-11-27 | +2.86% | +1.03% |
| rsi_reversal | GOOG | 2026-11-24 | 2026-11-27 | -2.30% | -4.13% |
| rsi_reversal | GS | 2026-11-24 | 2026-11-27 | -0.36% | -2.20% |
| rsi_reversal | NVDA | 2026-11-24 | 2026-11-27 | -5.41% | -7.25% |
| rsi_reversal | QQQ | 2026-11-24 | 2026-11-27 | +1.71% | -0.13% |
| rsi_reversal | SPY | 2026-11-24 | 2026-11-27 | +1.84% | +0.00% |
| sma_200_trend | AMD | 2026-11-24 | 2026-11-27 | -0.14% | -1.98% |
| stochastic_cross | GS | 2026-11-24 | 2026-11-27 | -0.36% | -2.20% |
| stochastic_cross | MSFT | 2026-11-24 | 2026-11-27 | +0.42% | -1.42% |
| trend_momentum_combo | DIA | 2026-11-24 | 2026-11-27 | +2.86% | +1.03% |
| trend_momentum_combo | IWM | 2026-11-24 | 2026-11-27 | +3.70% | +1.86% |
| trend_momentum_combo | META | 2026-11-24 | 2026-11-27 | +6.77% | +4.93% |
| trend_momentum_combo | MSFT | 2026-11-24 | 2026-11-27 | +0.42% | -1.42% |
| trend_momentum_combo | QQQ | 2026-11-24 | 2026-11-27 | +1.71% | -0.13% |
| trend_momentum_combo | SPY | 2026-11-24 | 2026-11-27 | +1.84% | +0.00% |
| inside_bar_breakout | AAPL | 2026-11-25 | 2026-11-27 | +1.39% | -2.18% |
| trend_momentum_combo | JPM | 2026-11-25 | 2026-11-27 | +3.99% | +0.42% |
| triple_ema | AAPL | 2026-11-25 | 2026-11-27 | +1.39% | -2.18% |
| inside_bar_breakout | META | 2026-11-27 | 2026-11-27 | +0.07% | -0.26% |
| keltner_reversion | META | 2026-11-27 | 2026-11-27 | +0.07% | -0.26% |
| macd_crossover | BAC | 2026-11-27 | 2026-11-27 | -0.35% | -0.68% |
| rsi_divergence | AMD | 2026-11-27 | 2026-11-27 | +1.06% | +0.72% |
| trend_momentum_combo | CVX | 2026-11-27 | 2026-11-27 | -0.32% | -0.66% |
| trend_momentum_combo | XOM | 2026-11-27 | 2026-11-27 | +0.26% | -0.07% |
| ema_crossover | TSLA | next session | - | n/a | n/a |
| high52_breakout | AAPL | next session | - | n/a | n/a |
| ichimoku_cloud | IWM | next session | - | n/a | n/a |
| keltner_reversion | BAC | next session | - | n/a | n/a |
| keltner_reversion | DIA | next session | - | n/a | n/a |
| keltner_reversion | IWM | next session | - | n/a | n/a |
| keltner_reversion | QQQ | next session | - | n/a | n/a |
| keltner_reversion | SPY | next session | - | n/a | n/a |
| macd_crossover | AAPL | next session | - | n/a | n/a |
| macd_crossover | META | next session | - | n/a | n/a |
| macd_crossover | TSLA | next session | - | n/a | n/a |
| sma_200_trend | AMZN | next session | - | n/a | n/a |
| sma_200_trend | META | next session | - | n/a | n/a |
| sma_200_trend | TSLA | next session | - | n/a | n/a |
| stochastic_cross | AMD | next session | - | n/a | n/a |
| trend_momentum_combo | AMD | next session | - | n/a | n/a |
| trend_momentum_combo | AMZN | next session | - | n/a | n/a |

Costs: 15 bp per round trip on completed trades; open marks carry no cost. Entry at the next open, exit at the 10th session's close. Signals from 10-15 to 11-02 were lost in the 11-03 reset and are not included.
