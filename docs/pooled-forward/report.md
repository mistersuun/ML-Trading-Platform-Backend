# Pooled shadow, forward scoring

As of 2026-11-18. Rules frozen in `PREREG.md` (2026-11-14). Paper only: pooled signals are never orders. Informational; only the looks on 2026-12-31, 2027-02-26, 2027-04-15 decide.

Signals recorded: 220 over 12 sessions. De-duplicated trades: 141: 70 completed, 71 open.

| Statistic | Mean | One-sided p | Entry weeks |
|---|---:|---:|---:|
| Trade excess vs SPY (same window) | -0.58% | n/a | 1 |
| Excess vs the symbol's all-days control | -0.59% | n/a | 1 |
| Same, without the best 2 entry weeks | n/a | | |

Size: 70 completed trades (needs 60) and 1 entry weeks (needs 10): not met, so inference is n/a. Pass rule: both p < 0.0167 and the ex-top-2 mean > 0. 

## By pattern (descriptive, no test)

| Pattern | Completed | Mean excess vs SPY | Mean vs control |
|---|---:|---:|---:|
| trend_momentum_combo | 7 | +0.22% | +1.02% |
| stochastic_cross | 7 | -1.40% | -1.12% |
| atr_breakout | 6 | -1.11% | -0.95% |
| bollinger_bounce | 6 | +1.72% | -1.54% |
| ichimoku_cloud | 6 | -1.00% | -1.22% |
| inside_bar_breakout | 5 | -0.08% | +0.46% |
| keltner_reversion | 5 | -0.10% | -0.84% |
| rsi2_pullback | 4 | +2.60% | +0.21% |
| donchian_breakout | 4 | -2.01% | -0.69% |
| rsi_divergence | 3 | -5.59% | +0.30% |
| triple_ema | 3 | -0.63% | -0.99% |
| mean_rev_composite | 3 | +3.12% | -2.26% |
| volume_breakout | 2 | -0.47% | +0.20% |
| macd_crossover | 2 | +0.57% | -0.01% |
| high52_breakout | 2 | +0.02% | -1.00% |
| adx_trend | 1 | -0.34% | +0.56% |
| bollinger_squeeze | 1 | +4.05% | -1.47% |
| ema_crossover | 1 | -4.01% | -0.54% |
| sma_200_trend | 1 | -19.77% | -0.63% |
| rsi_reversal | 1 | -2.65% | -1.48% |

## Open trades (marked, not scored)

| Pattern | Symbol | Entry | Mark | Return | vs SPY |
|---|---|---|---|---:|---:|
| ema_crossover | AMZN | 2026-11-06 | 2026-11-18 | +3.05% | +3.68% |
| rsi_divergence | BAC | 2026-11-06 | 2026-11-18 | -1.27% | -0.65% |
| rsi_divergence | IWM | 2026-11-06 | 2026-11-18 | -0.68% | -0.06% |
| rsi_divergence | META | 2026-11-06 | 2026-11-18 | +1.41% | +2.03% |
| trend_momentum_combo | AAPL | 2026-11-06 | 2026-11-18 | +4.05% | +4.68% |
| bollinger_bounce | TSLA | 2026-11-09 | 2026-11-18 | +6.53% | +8.12% |
| confluence | AMD | 2026-11-09 | 2026-11-18 | +12.06% | +13.65% |
| keltner_reversion | IWM | 2026-11-09 | 2026-11-18 | -2.64% | -1.06% |
| mean_rev_composite | NVDA | 2026-11-09 | 2026-11-18 | -12.73% | -11.15% |
| mean_rev_composite | SPY | 2026-11-09 | 2026-11-18 | -1.58% | +0.00% |
| rsi_divergence | SPY | 2026-11-09 | 2026-11-18 | -1.58% | +0.00% |
| rsi_divergence | XOM | 2026-11-09 | 2026-11-18 | -1.98% | -0.39% |
| rsi_reversal | AMD | 2026-11-09 | 2026-11-18 | +12.06% | +13.65% |
| rsi_reversal | NVDA | 2026-11-09 | 2026-11-18 | -12.73% | -11.15% |
| rsi_reversal | QQQ | 2026-11-09 | 2026-11-18 | -0.47% | +1.11% |
| sma_200_trend | AMD | 2026-11-09 | 2026-11-18 | +12.06% | +13.65% |
| stochastic_cross | CVX | 2026-11-09 | 2026-11-18 | -2.36% | -0.77% |
| stochastic_cross | XOM | 2026-11-09 | 2026-11-18 | -1.98% | -0.39% |
| triple_ema | AMZN | 2026-11-09 | 2026-11-18 | -0.65% | +0.93% |
| atr_breakout | AMD | 2026-11-10 | 2026-11-18 | +5.36% | +7.73% |
| atr_breakout | IWM | 2026-11-10 | 2026-11-18 | -3.45% | -1.08% |
| atr_breakout | SPY | 2026-11-10 | 2026-11-18 | -2.37% | +0.00% |
| bollinger_bounce | XOM | 2026-11-10 | 2026-11-18 | -0.79% | +1.58% |
| ichimoku_cloud | TSLA | 2026-11-10 | 2026-11-18 | +3.57% | +5.94% |
| inside_bar_breakout | GS | 2026-11-10 | 2026-11-18 | -4.07% | -1.70% |
| inside_bar_breakout | META | 2026-11-10 | 2026-11-18 | -3.33% | -0.96% |
| keltner_reversion | AMD | 2026-11-10 | 2026-11-18 | +5.36% | +7.73% |
| keltner_reversion | DIA | 2026-11-10 | 2026-11-18 | -3.79% | -1.42% |
| keltner_reversion | GS | 2026-11-10 | 2026-11-18 | -4.07% | -1.70% |
| keltner_reversion | META | 2026-11-10 | 2026-11-18 | -3.33% | -0.96% |
| keltner_reversion | TSLA | 2026-11-10 | 2026-11-18 | +3.57% | +5.94% |
| macd_crossover | MSFT | 2026-11-10 | 2026-11-18 | -6.43% | -4.06% |
| rsi_reversal | GOOG | 2026-11-10 | 2026-11-18 | -2.70% | -0.33% |
| sma_200_trend | IWM | 2026-11-10 | 2026-11-18 | -3.45% | -1.08% |
| sma_200_trend | META | 2026-11-10 | 2026-11-18 | -3.33% | -0.96% |
| trend_momentum_combo | DIA | 2026-11-10 | 2026-11-18 | -3.79% | -1.42% |
| adx_trend | BAC | 2026-11-11 | 2026-11-18 | -3.85% | -0.35% |
| ema_crossover | DIA | 2026-11-11 | 2026-11-18 | -4.03% | -0.52% |
| ema_crossover | IWM | 2026-11-11 | 2026-11-18 | -4.55% | -1.04% |
| rsi_divergence | CVX | 2026-11-11 | 2026-11-18 | -3.03% | +0.47% |
| triple_ema | JPM | 2026-11-11 | 2026-11-18 | -5.48% | -1.97% |
| bollinger_bounce | NVDA | 2026-11-12 | 2026-11-18 | -0.02% | +3.14% |
| donchian_breakout | AAPL | 2026-11-12 | 2026-11-18 | -1.50% | +1.66% |
| ema_crossover | GS | 2026-11-12 | 2026-11-18 | -4.75% | -1.60% |
| ema_crossover | TSLA | 2026-11-12 | 2026-11-18 | -0.65% | +2.51% |
| ichimoku_cloud | AMD | 2026-11-12 | 2026-11-18 | -6.92% | -3.76% |
| ichimoku_cloud | IWM | 2026-11-12 | 2026-11-18 | -4.02% | -0.87% |
| inside_bar_breakout | DIA | 2026-11-12 | 2026-11-18 | -4.32% | -1.16% |
| keltner_reversion | QQQ | 2026-11-12 | 2026-11-18 | -3.05% | +0.10% |
| macd_crossover | AAPL | 2026-11-12 | 2026-11-18 | -1.50% | +1.66% |
| macd_crossover | AMD | 2026-11-12 | 2026-11-18 | -6.92% | -3.76% |
| macd_crossover | META | 2026-11-12 | 2026-11-18 | -1.45% | +1.71% |
| sma_200_trend | QQQ | 2026-11-12 | 2026-11-18 | -3.05% | +0.10% |
| sma_200_trend | TSLA | 2026-11-12 | 2026-11-18 | -0.65% | +2.51% |
| trend_momentum_combo | CVX | 2026-11-12 | 2026-11-18 | -4.24% | -1.09% |
| trend_momentum_combo | XOM | 2026-11-12 | 2026-11-18 | -4.56% | -1.40% |
| donchian_breakout | AMD | 2026-11-13 | 2026-11-18 | -6.46% | -2.53% |
| macd_crossover | GOOG | 2026-11-13 | 2026-11-18 | -6.90% | -2.97% |
| triple_ema | TSLA | 2026-11-13 | 2026-11-18 | -4.54% | -0.61% |
| ichimoku_cloud | CVX | 2026-11-16 | 2026-11-18 | -6.43% | -3.83% |
| keltner_reversion | CVX | 2026-11-16 | 2026-11-18 | -6.43% | -3.83% |
| rsi2_pullback | AMZN | 2026-11-17 | 2026-11-18 | +4.07% | +5.18% |
| rsi2_pullback | SPY | 2026-11-17 | 2026-11-18 | -1.11% | +0.00% |
| rsi2_pullback | JPM | 2026-11-18 | 2026-11-18 | -1.04% | -0.61% |
| rsi2_pullback | MSFT | 2026-11-18 | 2026-11-18 | +0.55% | +0.98% |
| rsi_divergence | GOOG | 2026-11-18 | 2026-11-18 | -0.44% | -0.01% |
| bollinger_bounce | IWM | next session | - | n/a | n/a |
| ichimoku_cloud | AMZN | next session | - | n/a | n/a |
| stochastic_cross | GOOG | next session | - | n/a | n/a |
| stochastic_cross | IWM | next session | - | n/a | n/a |
| stochastic_cross | QQQ | next session | - | n/a | n/a |

Costs: 15 bp per round trip on completed trades; open marks carry no cost. Entry at the next open, exit at the 10th session's close. Signals from 10-15 to 11-02 were lost in the 11-03 reset and are not included.
