# Pooled shadow, forward scoring

As of 2026-11-23. Rules frozen in `PREREG.md` (2026-11-14). Paper only: pooled signals are never orders. Informational; only the looks on 2026-12-31, 2027-02-26, 2027-04-15 decide.

Signals recorded: 293 over 15 sessions. De-duplicated trades: 196: 106 completed, 90 open.

| Statistic | Mean | One-sided p | Entry weeks |
|---|---:|---:|---:|
| Trade excess vs SPY (same window) | -1.34% | n/a | 2 |
| Excess vs the symbol's all-days control | -1.21% | n/a | 2 |
| Same, without the best 2 entry weeks | n/a | | |

Size: 106 completed trades (needs 60) and 2 entry weeks (needs 10): not met, so inference is n/a. Pass rule: both p < 0.0167 and the ex-top-2 mean > 0. 

## By pattern (descriptive, no test)

| Pattern | Completed | Mean excess vs SPY | Mean vs control |
|---|---:|---:|---:|
| keltner_reversion | 11 | -1.03% | -1.95% |
| trend_momentum_combo | 9 | +0.60% | +1.97% |
| stochastic_cross | 9 | +0.05% | +1.31% |
| atr_breakout | 9 | -1.68% | -2.23% |
| bollinger_bounce | 8 | +1.68% | -0.03% |
| rsi_divergence | 8 | -1.48% | +1.00% |
| inside_bar_breakout | 7 | -1.38% | -0.30% |
| ichimoku_cloud | 7 | +0.19% | -0.46% |
| rsi_reversal | 5 | -5.44% | -3.26% |
| mean_rev_composite | 5 | -2.27% | -0.23% |
| sma_200_trend | 4 | -6.98% | -4.03% |
| triple_ema | 4 | -0.86% | -1.05% |
| rsi2_pullback | 4 | +2.60% | -0.67% |
| donchian_breakout | 4 | -2.01% | -0.26% |
| macd_crossover | 3 | -1.64% | -0.21% |
| ema_crossover | 2 | -1.26% | +0.38% |
| high52_breakout | 2 | +0.02% | -0.53% |
| volume_breakout | 2 | -0.47% | +1.18% |
| confluence | 1 | -3.41% | -9.78% |
| adx_trend | 1 | -0.34% | +0.58% |
| bollinger_squeeze | 1 | +4.05% | -1.02% |

## Open trades (marked, not scored)

| Pattern | Symbol | Entry | Mark | Return | vs SPY |
|---|---|---|---|---:|---:|
| adx_trend | BAC | 2026-11-11 | 2026-11-23 | -5.37% | -0.81% |
| ema_crossover | DIA | 2026-11-11 | 2026-11-23 | -5.16% | -0.61% |
| ema_crossover | IWM | 2026-11-11 | 2026-11-23 | -6.10% | -1.55% |
| rsi_divergence | CVX | 2026-11-11 | 2026-11-23 | -2.90% | +1.66% |
| triple_ema | JPM | 2026-11-11 | 2026-11-23 | -7.65% | -3.10% |
| bollinger_bounce | NVDA | 2026-11-12 | 2026-11-23 | +9.31% | +13.52% |
| donchian_breakout | AAPL | 2026-11-12 | 2026-11-23 | -2.44% | +1.76% |
| ema_crossover | GS | 2026-11-12 | 2026-11-23 | -10.16% | -5.96% |
| ema_crossover | TSLA | 2026-11-12 | 2026-11-23 | -0.20% | +4.00% |
| ichimoku_cloud | AMD | 2026-11-12 | 2026-11-23 | -20.38% | -16.18% |
| ichimoku_cloud | IWM | 2026-11-12 | 2026-11-23 | -5.58% | -1.37% |
| inside_bar_breakout | DIA | 2026-11-12 | 2026-11-23 | -5.45% | -1.25% |
| keltner_reversion | QQQ | 2026-11-12 | 2026-11-23 | -4.30% | -0.10% |
| macd_crossover | AAPL | 2026-11-12 | 2026-11-23 | -2.44% | +1.76% |
| macd_crossover | AMD | 2026-11-12 | 2026-11-23 | -20.38% | -16.18% |
| macd_crossover | META | 2026-11-12 | 2026-11-23 | -4.45% | -0.24% |
| sma_200_trend | QQQ | 2026-11-12 | 2026-11-23 | -4.30% | -0.10% |
| sma_200_trend | TSLA | 2026-11-12 | 2026-11-23 | -0.20% | +4.00% |
| trend_momentum_combo | CVX | 2026-11-12 | 2026-11-23 | -4.11% | +0.10% |
| trend_momentum_combo | XOM | 2026-11-12 | 2026-11-23 | -6.75% | -2.54% |
| donchian_breakout | AMD | 2026-11-13 | 2026-11-23 | -19.99% | -15.02% |
| macd_crossover | GOOG | 2026-11-13 | 2026-11-23 | -6.79% | -1.82% |
| triple_ema | TSLA | 2026-11-13 | 2026-11-23 | -4.11% | +0.86% |
| ichimoku_cloud | CVX | 2026-11-16 | 2026-11-23 | -6.30% | -2.65% |
| keltner_reversion | CVX | 2026-11-16 | 2026-11-23 | -6.30% | -2.65% |
| rsi2_pullback | AMZN | 2026-11-17 | 2026-11-23 | -0.73% | +1.45% |
| rsi2_pullback | SPY | 2026-11-17 | 2026-11-23 | -2.18% | +0.00% |
| rsi2_pullback | JPM | 2026-11-18 | 2026-11-23 | -3.32% | -1.81% |
| rsi2_pullback | MSFT | 2026-11-18 | 2026-11-23 | -2.57% | -1.06% |
| rsi_divergence | GOOG | 2026-11-18 | 2026-11-23 | -0.32% | +1.19% |
| bollinger_bounce | IWM | 2026-11-19 | 2026-11-23 | -1.97% | +0.08% |
| ichimoku_cloud | AMZN | 2026-11-19 | 2026-11-23 | -6.31% | -4.26% |
| rsi2_pullback | CVX | 2026-11-19 | 2026-11-23 | -0.63% | +1.42% |
| rsi2_pullback | XOM | 2026-11-19 | 2026-11-23 | -2.75% | -0.70% |
| stochastic_cross | GOOG | 2026-11-19 | 2026-11-23 | -3.83% | -1.78% |
| stochastic_cross | IWM | 2026-11-19 | 2026-11-23 | -1.97% | +0.08% |
| stochastic_cross | QQQ | 2026-11-19 | 2026-11-23 | -2.99% | -0.94% |
| rsi_divergence | GS | 2026-11-20 | 2026-11-23 | -0.35% | -1.57% |
| rsi_divergence | META | 2026-11-20 | 2026-11-23 | +2.52% | +1.30% |
| rsi_divergence | QQQ | 2026-11-20 | 2026-11-23 | +1.91% | +0.69% |
| stochastic_cross | BAC | 2026-11-20 | 2026-11-23 | -0.64% | -1.86% |
| stochastic_cross | JPM | 2026-11-20 | 2026-11-23 | -1.28% | -2.50% |
| rsi2_pullback | AAPL | 2026-11-23 | 2026-11-23 | +0.35% | +0.64% |
| rsi_divergence | TSLA | 2026-11-23 | 2026-11-23 | +2.82% | +3.11% |
| rsi_divergence | XOM | 2026-11-23 | 2026-11-23 | -0.66% | -0.37% |
| stochastic_cross | CVX | 2026-11-23 | 2026-11-23 | +0.08% | +0.37% |
| stochastic_cross | DIA | 2026-11-23 | 2026-11-23 | +0.32% | +0.61% |
| stochastic_cross | XOM | 2026-11-23 | 2026-11-23 | -0.66% | -0.37% |
| atr_breakout | DIA | next session | - | n/a | n/a |
| atr_breakout | NVDA | next session | - | n/a | n/a |
| atr_breakout | QQQ | next session | - | n/a | n/a |
| atr_breakout | TSLA | next session | - | n/a | n/a |
| bollinger_bounce | BAC | next session | - | n/a | n/a |
| bollinger_bounce | DIA | next session | - | n/a | n/a |
| bollinger_bounce | GS | next session | - | n/a | n/a |
| bollinger_bounce | JPM | next session | - | n/a | n/a |
| bollinger_bounce | META | next session | - | n/a | n/a |
| bollinger_bounce | MSFT | next session | - | n/a | n/a |
| bollinger_bounce | QQQ | next session | - | n/a | n/a |
| bollinger_bounce | SPY | next session | - | n/a | n/a |
| bollinger_bounce | TSLA | next session | - | n/a | n/a |
| confluence | BAC | next session | - | n/a | n/a |
| confluence | GS | next session | - | n/a | n/a |
| ichimoku_cloud | AAPL | next session | - | n/a | n/a |
| keltner_reversion | AMZN | next session | - | n/a | n/a |
| keltner_reversion | GOOG | next session | - | n/a | n/a |
| mean_rev_composite | BAC | next session | - | n/a | n/a |
| mean_rev_composite | DIA | next session | - | n/a | n/a |
| mean_rev_composite | GS | next session | - | n/a | n/a |
| mean_rev_composite | IWM | next session | - | n/a | n/a |
| mean_rev_composite | JPM | next session | - | n/a | n/a |
| mean_rev_composite | META | next session | - | n/a | n/a |
| mean_rev_composite | MSFT | next session | - | n/a | n/a |
| mean_rev_composite | NVDA | next session | - | n/a | n/a |
| mean_rev_composite | QQQ | next session | - | n/a | n/a |
| mean_rev_composite | SPY | next session | - | n/a | n/a |
| rsi_reversal | BAC | next session | - | n/a | n/a |
| rsi_reversal | DIA | next session | - | n/a | n/a |
| rsi_reversal | GS | next session | - | n/a | n/a |
| rsi_reversal | NVDA | next session | - | n/a | n/a |
| rsi_reversal | QQQ | next session | - | n/a | n/a |
| rsi_reversal | SPY | next session | - | n/a | n/a |
| sma_200_trend | AMD | next session | - | n/a | n/a |
| stochastic_cross | GS | next session | - | n/a | n/a |
| stochastic_cross | MSFT | next session | - | n/a | n/a |
| trend_momentum_combo | IWM | next session | - | n/a | n/a |
| trend_momentum_combo | META | next session | - | n/a | n/a |
| trend_momentum_combo | MSFT | next session | - | n/a | n/a |
| trend_momentum_combo | QQQ | next session | - | n/a | n/a |
| trend_momentum_combo | SPY | next session | - | n/a | n/a |

Costs: 15 bp per round trip on completed trades; open marks carry no cost. Entry at the next open, exit at the 10th session's close. Signals from 10-15 to 11-02 were lost in the 11-03 reset and are not included.
