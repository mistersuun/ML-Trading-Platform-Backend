# Pooled shadow, forward scoring

As of 2026-11-19. Rules frozen in `PREREG.md` (2026-11-14). Paper only: pooled signals are never orders. Informational; only the looks on 2026-12-31, 2027-02-26, 2027-04-15 decide.

Signals recorded: 228 over 13 sessions. De-duplicated trades: 147: 75 completed, 72 open.

| Statistic | Mean | One-sided p | Entry weeks |
|---|---:|---:|---:|
| Trade excess vs SPY (same window) | -0.45% | n/a | 1 |
| Excess vs the symbol's all-days control | -0.48% | n/a | 1 |
| Same, without the best 2 entry weeks | n/a | | |

Size: 75 completed trades (needs 60) and 1 entry weeks (needs 10): not met, so inference is n/a. Pass rule: both p < 0.0167 and the ex-top-2 mean > 0. 

## By pattern (descriptive, no test)

| Pattern | Completed | Mean excess vs SPY | Mean vs control |
|---|---:|---:|---:|
| trend_momentum_combo | 8 | +0.88% | +1.09% |
| stochastic_cross | 7 | -1.40% | -0.77% |
| atr_breakout | 6 | -1.11% | -1.12% |
| bollinger_bounce | 6 | +1.72% | -1.24% |
| ichimoku_cloud | 6 | -1.00% | -1.32% |
| rsi_divergence | 6 | -2.70% | +0.59% |
| keltner_reversion | 5 | -0.10% | -0.98% |
| inside_bar_breakout | 5 | -0.08% | +0.39% |
| rsi2_pullback | 4 | +2.60% | -0.20% |
| donchian_breakout | 4 | -2.01% | -0.81% |
| triple_ema | 3 | -0.63% | -1.05% |
| mean_rev_composite | 3 | +3.12% | -1.69% |
| macd_crossover | 2 | +0.57% | +0.12% |
| volume_breakout | 2 | -0.47% | +0.20% |
| ema_crossover | 2 | -1.26% | -0.60% |
| high52_breakout | 2 | +0.02% | -1.12% |
| bollinger_squeeze | 1 | +4.05% | -1.47% |
| adx_trend | 1 | -0.34% | +0.40% |
| sma_200_trend | 1 | -19.77% | +0.08% |
| rsi_reversal | 1 | -2.65% | -2.02% |

## Open trades (marked, not scored)

| Pattern | Symbol | Entry | Mark | Return | vs SPY |
|---|---|---|---|---:|---:|
| bollinger_bounce | TSLA | 2026-11-09 | 2026-11-19 | +3.95% | +7.46% |
| confluence | AMD | 2026-11-09 | 2026-11-19 | +3.41% | +6.92% |
| keltner_reversion | IWM | 2026-11-09 | 2026-11-19 | -4.77% | -1.25% |
| mean_rev_composite | NVDA | 2026-11-09 | 2026-11-19 | -15.61% | -12.10% |
| mean_rev_composite | SPY | 2026-11-09 | 2026-11-19 | -3.52% | +0.00% |
| rsi_divergence | SPY | 2026-11-09 | 2026-11-19 | -3.52% | +0.00% |
| rsi_divergence | XOM | 2026-11-09 | 2026-11-19 | -2.98% | +0.53% |
| rsi_reversal | AMD | 2026-11-09 | 2026-11-19 | +3.41% | +6.92% |
| rsi_reversal | NVDA | 2026-11-09 | 2026-11-19 | -15.61% | -12.10% |
| rsi_reversal | QQQ | 2026-11-09 | 2026-11-19 | -2.65% | +0.86% |
| sma_200_trend | AMD | 2026-11-09 | 2026-11-19 | +3.41% | +6.92% |
| stochastic_cross | CVX | 2026-11-09 | 2026-11-19 | -2.06% | +1.45% |
| stochastic_cross | XOM | 2026-11-09 | 2026-11-19 | -2.98% | +0.53% |
| triple_ema | AMZN | 2026-11-09 | 2026-11-19 | -4.50% | -0.98% |
| atr_breakout | AMD | 2026-11-10 | 2026-11-19 | -2.78% | +1.51% |
| atr_breakout | IWM | 2026-11-10 | 2026-11-19 | -5.56% | -1.28% |
| atr_breakout | SPY | 2026-11-10 | 2026-11-19 | -4.29% | +0.00% |
| bollinger_bounce | XOM | 2026-11-10 | 2026-11-19 | -1.81% | +2.48% |
| ichimoku_cloud | TSLA | 2026-11-10 | 2026-11-19 | +1.06% | +5.35% |
| inside_bar_breakout | GS | 2026-11-10 | 2026-11-19 | -6.89% | -2.60% |
| inside_bar_breakout | META | 2026-11-10 | 2026-11-19 | -6.49% | -2.21% |
| keltner_reversion | AMD | 2026-11-10 | 2026-11-19 | -2.78% | +1.51% |
| keltner_reversion | DIA | 2026-11-10 | 2026-11-19 | -5.89% | -1.60% |
| keltner_reversion | GS | 2026-11-10 | 2026-11-19 | -6.89% | -2.60% |
| keltner_reversion | META | 2026-11-10 | 2026-11-19 | -6.49% | -2.21% |
| keltner_reversion | TSLA | 2026-11-10 | 2026-11-19 | +1.06% | +5.35% |
| macd_crossover | MSFT | 2026-11-10 | 2026-11-19 | -8.27% | -3.98% |
| rsi_reversal | GOOG | 2026-11-10 | 2026-11-19 | -5.55% | -1.26% |
| sma_200_trend | IWM | 2026-11-10 | 2026-11-19 | -5.56% | -1.28% |
| sma_200_trend | META | 2026-11-10 | 2026-11-19 | -6.49% | -2.21% |
| trend_momentum_combo | DIA | 2026-11-10 | 2026-11-19 | -5.89% | -1.60% |
| adx_trend | BAC | 2026-11-11 | 2026-11-19 | -4.52% | +0.88% |
| ema_crossover | DIA | 2026-11-11 | 2026-11-19 | -6.12% | -0.72% |
| ema_crossover | IWM | 2026-11-11 | 2026-11-19 | -6.64% | -1.24% |
| rsi_divergence | CVX | 2026-11-11 | 2026-11-19 | -2.74% | +2.66% |
| triple_ema | JPM | 2026-11-11 | 2026-11-19 | -6.61% | -1.21% |
| bollinger_bounce | NVDA | 2026-11-12 | 2026-11-19 | -3.32% | +1.74% |
| donchian_breakout | AAPL | 2026-11-12 | 2026-11-19 | -2.40% | +2.65% |
| ema_crossover | GS | 2026-11-12 | 2026-11-19 | -7.55% | -2.50% |
| ema_crossover | TSLA | 2026-11-12 | 2026-11-19 | -3.06% | +2.00% |
| ichimoku_cloud | AMD | 2026-11-12 | 2026-11-19 | -14.11% | -9.05% |
| ichimoku_cloud | IWM | 2026-11-12 | 2026-11-19 | -6.12% | -1.06% |
| inside_bar_breakout | DIA | 2026-11-12 | 2026-11-19 | -6.41% | -1.35% |
| keltner_reversion | QQQ | 2026-11-12 | 2026-11-19 | -5.18% | -0.13% |
| macd_crossover | AAPL | 2026-11-12 | 2026-11-19 | -2.40% | +2.65% |
| macd_crossover | AMD | 2026-11-12 | 2026-11-19 | -14.11% | -9.05% |
| macd_crossover | META | 2026-11-12 | 2026-11-19 | -4.67% | +0.39% |
| sma_200_trend | QQQ | 2026-11-12 | 2026-11-19 | -5.18% | -0.13% |
| sma_200_trend | TSLA | 2026-11-12 | 2026-11-19 | -3.06% | +2.00% |
| trend_momentum_combo | CVX | 2026-11-12 | 2026-11-19 | -3.95% | +1.10% |
| trend_momentum_combo | XOM | 2026-11-12 | 2026-11-19 | -5.54% | -0.48% |
| donchian_breakout | AMD | 2026-11-13 | 2026-11-19 | -13.69% | -7.87% |
| macd_crossover | GOOG | 2026-11-13 | 2026-11-19 | -9.63% | -3.81% |
| triple_ema | TSLA | 2026-11-13 | 2026-11-19 | -6.86% | -1.05% |
| ichimoku_cloud | CVX | 2026-11-16 | 2026-11-19 | -6.15% | -1.64% |
| keltner_reversion | CVX | 2026-11-16 | 2026-11-19 | -6.15% | -1.64% |
| rsi2_pullback | AMZN | 2026-11-17 | 2026-11-19 | +0.04% | +3.09% |
| rsi2_pullback | SPY | 2026-11-17 | 2026-11-19 | -3.05% | +0.00% |
| rsi2_pullback | JPM | 2026-11-18 | 2026-11-19 | -2.23% | +0.15% |
| rsi2_pullback | MSFT | 2026-11-18 | 2026-11-19 | -1.42% | +0.96% |
| rsi_divergence | GOOG | 2026-11-18 | 2026-11-19 | -3.35% | -0.97% |
| bollinger_bounce | IWM | 2026-11-19 | 2026-11-19 | -2.53% | +0.39% |
| ichimoku_cloud | AMZN | 2026-11-19 | 2026-11-19 | -5.58% | -2.66% |
| rsi2_pullback | CVX | 2026-11-19 | 2026-11-19 | -0.47% | +2.45% |
| rsi2_pullback | XOM | 2026-11-19 | 2026-11-19 | -1.49% | +1.43% |
| stochastic_cross | GOOG | 2026-11-19 | 2026-11-19 | -6.76% | -3.84% |
| stochastic_cross | IWM | 2026-11-19 | 2026-11-19 | -2.53% | +0.39% |
| stochastic_cross | QQQ | 2026-11-19 | 2026-11-19 | -3.88% | -0.96% |
| rsi_divergence | GS | next session | - | n/a | n/a |
| rsi_divergence | QQQ | next session | - | n/a | n/a |
| stochastic_cross | BAC | next session | - | n/a | n/a |
| stochastic_cross | JPM | next session | - | n/a | n/a |

Costs: 15 bp per round trip on completed trades; open marks carry no cost. Entry at the next open, exit at the 10th session's close. Signals from 10-15 to 11-02 were lost in the 11-03 reset and are not included.
