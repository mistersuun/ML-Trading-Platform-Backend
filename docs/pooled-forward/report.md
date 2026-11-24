# Pooled shadow, forward scoring

As of 2026-11-24. Rules frozen in `PREREG.md` (2026-11-14). Paper only: pooled signals are never orders. Informational; only the looks on 2026-12-31, 2027-02-26, 2027-04-15 decide.

Signals recorded: 307 over 16 sessions. De-duplicated trades: 205: 111 completed, 94 open.

| Statistic | Mean | One-sided p | Entry weeks |
|---|---:|---:|---:|
| Trade excess vs SPY (same window) | -1.18% | n/a | 2 |
| Excess vs the symbol's all-days control | -0.55% | n/a | 2 |
| Same, without the best 2 entry weeks | n/a | | |

Size: 111 completed trades (needs 60) and 2 entry weeks (needs 10): not met, so inference is n/a. Pass rule: both p < 0.0167 and the ex-top-2 mean > 0. 

## By pattern (descriptive, no test)

| Pattern | Completed | Mean excess vs SPY | Mean vs control |
|---|---:|---:|---:|
| keltner_reversion | 11 | -1.03% | -1.38% |
| rsi_divergence | 9 | -0.96% | +0.88% |
| stochastic_cross | 9 | +0.05% | +1.63% |
| trend_momentum_combo | 9 | +0.60% | +2.36% |
| atr_breakout | 9 | -1.68% | -1.74% |
| bollinger_bounce | 8 | +1.68% | +0.57% |
| inside_bar_breakout | 7 | -1.38% | +0.05% |
| ichimoku_cloud | 7 | +0.19% | -0.44% |
| rsi_reversal | 5 | -5.44% | -2.79% |
| triple_ema | 5 | -1.22% | -0.85% |
| mean_rev_composite | 5 | -2.27% | +0.41% |
| ema_crossover | 4 | -0.70% | +0.61% |
| rsi2_pullback | 4 | +2.60% | -0.72% |
| sma_200_trend | 4 | -6.98% | -3.32% |
| donchian_breakout | 4 | -2.01% | -0.24% |
| macd_crossover | 3 | -1.64% | -0.19% |
| adx_trend | 2 | -0.01% | +0.67% |
| high52_breakout | 2 | +0.02% | -0.76% |
| volume_breakout | 2 | -0.47% | +1.33% |
| confluence | 1 | -3.41% | -5.54% |
| bollinger_squeeze | 1 | +4.05% | -1.21% |

## Open trades (marked, not scored)

| Pattern | Symbol | Entry | Mark | Return | vs SPY |
|---|---|---|---|---:|---:|
| bollinger_bounce | NVDA | 2026-11-12 | 2026-11-24 | +2.91% | +7.58% |
| donchian_breakout | AAPL | 2026-11-12 | 2026-11-24 | -1.05% | +3.62% |
| ema_crossover | GS | 2026-11-12 | 2026-11-24 | -12.08% | -7.41% |
| ema_crossover | TSLA | 2026-11-12 | 2026-11-24 | -0.40% | +4.27% |
| ichimoku_cloud | AMD | 2026-11-12 | 2026-11-24 | -27.44% | -22.77% |
| ichimoku_cloud | IWM | 2026-11-12 | 2026-11-24 | -5.08% | -0.41% |
| inside_bar_breakout | DIA | 2026-11-12 | 2026-11-24 | -4.70% | -0.03% |
| keltner_reversion | QQQ | 2026-11-12 | 2026-11-24 | -6.20% | -1.53% |
| macd_crossover | AAPL | 2026-11-12 | 2026-11-24 | -1.05% | +3.62% |
| macd_crossover | AMD | 2026-11-12 | 2026-11-24 | -27.44% | -22.77% |
| macd_crossover | META | 2026-11-12 | 2026-11-24 | -5.69% | -1.02% |
| sma_200_trend | QQQ | 2026-11-12 | 2026-11-24 | -6.20% | -1.53% |
| sma_200_trend | TSLA | 2026-11-12 | 2026-11-24 | -0.40% | +4.27% |
| trend_momentum_combo | CVX | 2026-11-12 | 2026-11-24 | -2.92% | +1.75% |
| trend_momentum_combo | XOM | 2026-11-12 | 2026-11-24 | -5.96% | -1.29% |
| donchian_breakout | AMD | 2026-11-13 | 2026-11-24 | -27.08% | -21.65% |
| macd_crossover | GOOG | 2026-11-13 | 2026-11-24 | -7.77% | -2.34% |
| triple_ema | TSLA | 2026-11-13 | 2026-11-24 | -4.31% | +1.13% |
| ichimoku_cloud | CVX | 2026-11-16 | 2026-11-24 | -5.14% | -1.02% |
| keltner_reversion | CVX | 2026-11-16 | 2026-11-24 | -5.14% | -1.02% |
| rsi2_pullback | AMZN | 2026-11-17 | 2026-11-24 | -1.94% | +0.72% |
| rsi2_pullback | SPY | 2026-11-17 | 2026-11-24 | -2.66% | +0.00% |
| rsi2_pullback | JPM | 2026-11-18 | 2026-11-24 | -3.18% | -1.19% |
| rsi2_pullback | MSFT | 2026-11-18 | 2026-11-24 | -1.85% | +0.14% |
| rsi_divergence | GOOG | 2026-11-18 | 2026-11-24 | -1.37% | +0.62% |
| bollinger_bounce | IWM | 2026-11-19 | 2026-11-24 | -1.45% | +1.07% |
| ichimoku_cloud | AMZN | 2026-11-19 | 2026-11-24 | -7.44% | -4.92% |
| rsi2_pullback | CVX | 2026-11-19 | 2026-11-24 | +0.60% | +3.13% |
| rsi2_pullback | XOM | 2026-11-19 | 2026-11-24 | -1.92% | +0.60% |
| stochastic_cross | GOOG | 2026-11-19 | 2026-11-24 | -4.84% | -2.32% |
| stochastic_cross | IWM | 2026-11-19 | 2026-11-24 | -1.45% | +1.07% |
| stochastic_cross | QQQ | 2026-11-19 | 2026-11-24 | -4.92% | -2.39% |
| rsi_divergence | GS | 2026-11-20 | 2026-11-24 | -2.48% | -3.20% |
| rsi_divergence | META | 2026-11-20 | 2026-11-24 | +1.19% | +0.46% |
| rsi_divergence | QQQ | 2026-11-20 | 2026-11-24 | -0.11% | -0.84% |
| stochastic_cross | BAC | 2026-11-20 | 2026-11-24 | +0.23% | -0.50% |
| stochastic_cross | JPM | 2026-11-20 | 2026-11-24 | -1.14% | -1.87% |
| rsi2_pullback | AAPL | 2026-11-23 | 2026-11-24 | +1.78% | +2.56% |
| rsi_divergence | TSLA | 2026-11-23 | 2026-11-24 | +2.61% | +3.38% |
| rsi_divergence | XOM | 2026-11-23 | 2026-11-24 | +0.18% | +0.96% |
| stochastic_cross | CVX | 2026-11-23 | 2026-11-24 | +1.33% | +2.10% |
| stochastic_cross | DIA | 2026-11-23 | 2026-11-24 | +1.12% | +1.89% |
| stochastic_cross | XOM | 2026-11-23 | 2026-11-24 | +0.18% | +0.96% |
| atr_breakout | DIA | 2026-11-24 | 2026-11-24 | +1.27% | +0.77% |
| atr_breakout | IWM | 2026-11-24 | 2026-11-24 | +1.18% | +0.68% |
| atr_breakout | NVDA | 2026-11-24 | 2026-11-24 | -2.34% | -2.84% |
| atr_breakout | QQQ | 2026-11-24 | 2026-11-24 | -0.30% | -0.81% |
| atr_breakout | SPY | 2026-11-24 | 2026-11-24 | +0.50% | +0.00% |
| atr_breakout | TSLA | 2026-11-24 | 2026-11-24 | +0.32% | -0.18% |
| bollinger_bounce | BAC | 2026-11-24 | 2026-11-24 | +1.42% | +0.92% |
| bollinger_bounce | DIA | 2026-11-24 | 2026-11-24 | +1.27% | +0.77% |
| bollinger_bounce | GS | 2026-11-24 | 2026-11-24 | -1.20% | -1.70% |
| bollinger_bounce | JPM | 2026-11-24 | 2026-11-24 | +0.74% | +0.23% |
| bollinger_bounce | META | 2026-11-24 | 2026-11-24 | +0.12% | -0.38% |
| bollinger_bounce | MSFT | 2026-11-24 | 2026-11-24 | +1.09% | +0.58% |
| bollinger_bounce | QQQ | 2026-11-24 | 2026-11-24 | -0.30% | -0.81% |
| bollinger_bounce | SPY | 2026-11-24 | 2026-11-24 | +0.50% | +0.00% |
| bollinger_bounce | TSLA | 2026-11-24 | 2026-11-24 | +0.32% | -0.18% |
| confluence | BAC | 2026-11-24 | 2026-11-24 | +1.42% | +0.92% |
| confluence | GS | 2026-11-24 | 2026-11-24 | -1.20% | -1.70% |
| ichimoku_cloud | AAPL | 2026-11-24 | 2026-11-24 | +1.69% | +1.18% |
| ichimoku_cloud | TSLA | 2026-11-24 | 2026-11-24 | +0.32% | -0.18% |
| keltner_reversion | AMZN | 2026-11-24 | 2026-11-24 | +0.12% | -0.39% |
| keltner_reversion | GOOG | 2026-11-24 | 2026-11-24 | -0.81% | -1.31% |
| keltner_reversion | TSLA | 2026-11-24 | 2026-11-24 | +0.32% | -0.18% |
| mean_rev_composite | BAC | 2026-11-24 | 2026-11-24 | +1.42% | +0.92% |
| mean_rev_composite | DIA | 2026-11-24 | 2026-11-24 | +1.27% | +0.77% |
| mean_rev_composite | GS | 2026-11-24 | 2026-11-24 | -1.20% | -1.70% |
| mean_rev_composite | IWM | 2026-11-24 | 2026-11-24 | +1.18% | +0.68% |
| mean_rev_composite | JPM | 2026-11-24 | 2026-11-24 | +0.74% | +0.23% |
| mean_rev_composite | META | 2026-11-24 | 2026-11-24 | +0.12% | -0.38% |
| mean_rev_composite | MSFT | 2026-11-24 | 2026-11-24 | +1.09% | +0.58% |
| mean_rev_composite | NVDA | 2026-11-24 | 2026-11-24 | -2.34% | -2.84% |
| mean_rev_composite | QQQ | 2026-11-24 | 2026-11-24 | -0.30% | -0.81% |
| mean_rev_composite | SPY | 2026-11-24 | 2026-11-24 | +0.50% | +0.00% |
| rsi_reversal | BAC | 2026-11-24 | 2026-11-24 | +1.42% | +0.92% |
| rsi_reversal | DIA | 2026-11-24 | 2026-11-24 | +1.27% | +0.77% |
| rsi_reversal | GOOG | 2026-11-24 | 2026-11-24 | -0.81% | -1.31% |
| rsi_reversal | GS | 2026-11-24 | 2026-11-24 | -1.20% | -1.70% |
| rsi_reversal | NVDA | 2026-11-24 | 2026-11-24 | -2.34% | -2.84% |
| rsi_reversal | QQQ | 2026-11-24 | 2026-11-24 | -0.30% | -0.81% |
| rsi_reversal | SPY | 2026-11-24 | 2026-11-24 | +0.50% | +0.00% |
| sma_200_trend | AMD | 2026-11-24 | 2026-11-24 | -2.79% | -3.29% |
| stochastic_cross | GS | 2026-11-24 | 2026-11-24 | -1.20% | -1.70% |
| stochastic_cross | MSFT | 2026-11-24 | 2026-11-24 | +1.09% | +0.58% |
| trend_momentum_combo | DIA | 2026-11-24 | 2026-11-24 | +1.27% | +0.77% |
| trend_momentum_combo | IWM | 2026-11-24 | 2026-11-24 | +1.18% | +0.68% |
| trend_momentum_combo | META | 2026-11-24 | 2026-11-24 | +0.12% | -0.38% |
| trend_momentum_combo | MSFT | 2026-11-24 | 2026-11-24 | +1.09% | +0.58% |
| trend_momentum_combo | QQQ | 2026-11-24 | 2026-11-24 | -0.30% | -0.81% |
| trend_momentum_combo | SPY | 2026-11-24 | 2026-11-24 | +0.50% | +0.00% |
| inside_bar_breakout | AAPL | next session | - | n/a | n/a |
| trend_momentum_combo | JPM | next session | - | n/a | n/a |
| triple_ema | AAPL | next session | - | n/a | n/a |

Costs: 15 bp per round trip on completed trades; open marks carry no cost. Entry at the next open, exit at the 10th session's close. Signals from 10-15 to 11-02 were lost in the 11-03 reset and are not included.
