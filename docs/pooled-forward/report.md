# Pooled shadow, forward scoring

As of 2026-11-17. Rules frozen in `PREREG.md` (2026-11-14). Paper only: pooled signals are never orders. Informational; only the looks on 2026-12-31, 2027-02-26, 2027-04-15 decide.

Signals recorded: 211 over 11 sessions. De-duplicated trades: 135: 26 completed, 109 open.

| Statistic | Mean | One-sided p | Entry weeks |
|---|---:|---:|---:|
| Trade excess vs SPY (same window) | -1.03% | n/a | 1 |
| Excess vs the symbol's all-days control | +0.00% | n/a | 1 |
| Same, without the best 2 entry weeks | n/a | | |

Size: 26 completed trades (needs 60) and 1 entry weeks (needs 10): not met, so inference is n/a. Pass rule: both p < 0.0167 and the ex-top-2 mean > 0. 

## By pattern (descriptive, no test)

| Pattern | Completed | Mean excess vs SPY | Mean vs control |
|---|---:|---:|---:|
| stochastic_cross | 7 | -1.40% | +0.00% |
| bollinger_bounce | 5 | +1.25% | +0.00% |
| trend_momentum_combo | 4 | -5.27% | +0.00% |
| mean_rev_composite | 3 | +3.12% | +0.00% |
| keltner_reversion | 2 | -0.88% | +0.00% |
| rsi_divergence | 2 | +0.88% | +0.00% |
| ichimoku_cloud | 1 | +1.18% | +0.00% |
| rsi2_pullback | 1 | +6.99% | +0.00% |
| sma_200_trend | 1 | -19.77% | +0.00% |

## Open trades (marked, not scored)

| Pattern | Symbol | Entry | Mark | Return | vs SPY |
|---|---|---|---|---:|---:|
| adx_trend | IWM | 2026-11-05 | 2026-11-17 | -5.18% | -0.86% |
| atr_breakout | AAPL | 2026-11-05 | 2026-11-17 | -0.56% | +3.76% |
| atr_breakout | AMZN | 2026-11-05 | 2026-11-17 | -5.02% | -0.70% |
| atr_breakout | BAC | 2026-11-05 | 2026-11-17 | -6.10% | -1.78% |
| atr_breakout | DIA | 2026-11-05 | 2026-11-17 | -5.35% | -1.03% |
| atr_breakout | JPM | 2026-11-05 | 2026-11-17 | -8.00% | -3.68% |
| atr_breakout | MSFT | 2026-11-05 | 2026-11-17 | -8.83% | -4.52% |
| bollinger_bounce | AAPL | 2026-11-05 | 2026-11-17 | -0.56% | +3.76% |
| bollinger_squeeze | AAPL | 2026-11-05 | 2026-11-17 | -0.56% | +3.76% |
| donchian_breakout | AMZN | 2026-11-05 | 2026-11-17 | -5.02% | -0.70% |
| donchian_breakout | DIA | 2026-11-05 | 2026-11-17 | -5.35% | -1.03% |
| donchian_breakout | JPM | 2026-11-05 | 2026-11-17 | -8.00% | -3.68% |
| donchian_breakout | MSFT | 2026-11-05 | 2026-11-17 | -8.83% | -4.52% |
| ema_crossover | MSFT | 2026-11-05 | 2026-11-17 | -8.83% | -4.52% |
| high52_breakout | AAPL | 2026-11-05 | 2026-11-17 | -0.56% | +3.76% |
| high52_breakout | MSFT | 2026-11-05 | 2026-11-17 | -8.83% | -4.52% |
| ichimoku_cloud | AAPL | 2026-11-05 | 2026-11-17 | -0.56% | +3.76% |
| ichimoku_cloud | DIA | 2026-11-05 | 2026-11-17 | -5.35% | -1.03% |
| ichimoku_cloud | JPM | 2026-11-05 | 2026-11-17 | -8.00% | -3.68% |
| ichimoku_cloud | MSFT | 2026-11-05 | 2026-11-17 | -8.83% | -4.52% |
| ichimoku_cloud | SPY | 2026-11-05 | 2026-11-17 | -4.31% | +0.00% |
| inside_bar_breakout | AMZN | 2026-11-05 | 2026-11-17 | -5.02% | -0.70% |
| inside_bar_breakout | IWM | 2026-11-05 | 2026-11-17 | -5.18% | -0.86% |
| inside_bar_breakout | MSFT | 2026-11-05 | 2026-11-17 | -8.83% | -4.52% |
| inside_bar_breakout | QQQ | 2026-11-05 | 2026-11-17 | -3.83% | +0.49% |
| inside_bar_breakout | SPY | 2026-11-05 | 2026-11-17 | -4.31% | +0.00% |
| keltner_reversion | AAPL | 2026-11-05 | 2026-11-17 | -0.56% | +3.76% |
| keltner_reversion | BAC | 2026-11-05 | 2026-11-17 | -6.10% | -1.78% |
| keltner_reversion | SPY | 2026-11-05 | 2026-11-17 | -4.31% | +0.00% |
| macd_crossover | AMZN | 2026-11-05 | 2026-11-17 | -5.02% | -0.70% |
| macd_crossover | DIA | 2026-11-05 | 2026-11-17 | -5.35% | -1.03% |
| rsi2_pullback | CVX | 2026-11-05 | 2026-11-17 | -1.76% | +2.55% |
| rsi2_pullback | TSLA | 2026-11-05 | 2026-11-17 | +1.64% | +5.96% |
| rsi2_pullback | XOM | 2026-11-05 | 2026-11-17 | -2.25% | +2.06% |
| rsi_divergence | NVDA | 2026-11-05 | 2026-11-17 | -20.68% | -16.36% |
| rsi_reversal | BAC | 2026-11-05 | 2026-11-17 | -6.10% | -1.78% |
| trend_momentum_combo | AMD | 2026-11-05 | 2026-11-17 | +11.37% | +15.68% |
| trend_momentum_combo | QQQ | 2026-11-05 | 2026-11-17 | -3.83% | +0.49% |
| trend_momentum_combo | SPY | 2026-11-05 | 2026-11-17 | -4.31% | +0.00% |
| triple_ema | AAPL | 2026-11-05 | 2026-11-17 | -0.56% | +3.76% |
| triple_ema | DIA | 2026-11-05 | 2026-11-17 | -5.35% | -1.03% |
| triple_ema | MSFT | 2026-11-05 | 2026-11-17 | -8.83% | -4.52% |
| volume_breakout | AMZN | 2026-11-05 | 2026-11-17 | -5.02% | -0.70% |
| volume_breakout | MSFT | 2026-11-05 | 2026-11-17 | -8.83% | -4.52% |
| ema_crossover | AMZN | 2026-11-06 | 2026-11-17 | -0.74% | -0.42% |
| rsi_divergence | BAC | 2026-11-06 | 2026-11-17 | -0.20% | +0.12% |
| rsi_divergence | IWM | 2026-11-06 | 2026-11-17 | -1.07% | -0.75% |
| rsi_divergence | META | 2026-11-06 | 2026-11-17 | +1.82% | +2.14% |
| trend_momentum_combo | AAPL | 2026-11-06 | 2026-11-17 | +3.89% | +4.21% |
| bollinger_bounce | TSLA | 2026-11-09 | 2026-11-17 | +5.53% | +6.81% |
| confluence | AMD | 2026-11-09 | 2026-11-17 | +6.41% | +7.70% |
| keltner_reversion | IWM | 2026-11-09 | 2026-11-17 | -3.03% | -1.74% |
| mean_rev_composite | NVDA | 2026-11-09 | 2026-11-17 | -10.13% | -8.85% |
| mean_rev_composite | SPY | 2026-11-09 | 2026-11-17 | -1.28% | +0.00% |
| rsi_divergence | SPY | 2026-11-09 | 2026-11-17 | -1.28% | +0.00% |
| rsi_divergence | XOM | 2026-11-09 | 2026-11-17 | +2.07% | +3.35% |
| rsi_reversal | AMD | 2026-11-09 | 2026-11-17 | +6.41% | +7.70% |
| rsi_reversal | NVDA | 2026-11-09 | 2026-11-17 | -10.13% | -8.85% |
| rsi_reversal | QQQ | 2026-11-09 | 2026-11-17 | -0.86% | +0.43% |
| sma_200_trend | AMD | 2026-11-09 | 2026-11-17 | +6.41% | +7.70% |
| stochastic_cross | CVX | 2026-11-09 | 2026-11-17 | +2.50% | +3.79% |
| stochastic_cross | XOM | 2026-11-09 | 2026-11-17 | +2.07% | +3.35% |
| triple_ema | AMZN | 2026-11-09 | 2026-11-17 | -4.31% | -3.02% |
| atr_breakout | AMD | 2026-11-10 | 2026-11-17 | +0.05% | +2.12% |
| atr_breakout | IWM | 2026-11-10 | 2026-11-17 | -3.84% | -1.76% |
| atr_breakout | SPY | 2026-11-10 | 2026-11-17 | -2.07% | +0.00% |
| bollinger_bounce | XOM | 2026-11-10 | 2026-11-17 | +3.31% | +5.38% |
| ichimoku_cloud | TSLA | 2026-11-10 | 2026-11-17 | +2.60% | +4.67% |
| inside_bar_breakout | GS | 2026-11-10 | 2026-11-17 | -3.59% | -1.52% |
| inside_bar_breakout | META | 2026-11-10 | 2026-11-17 | -2.94% | -0.87% |
| keltner_reversion | AMD | 2026-11-10 | 2026-11-17 | +0.05% | +2.12% |
| keltner_reversion | DIA | 2026-11-10 | 2026-11-17 | -2.72% | -0.65% |
| keltner_reversion | GS | 2026-11-10 | 2026-11-17 | -3.59% | -1.52% |
| keltner_reversion | META | 2026-11-10 | 2026-11-17 | -2.94% | -0.87% |
| keltner_reversion | TSLA | 2026-11-10 | 2026-11-17 | +2.60% | +4.67% |
| macd_crossover | MSFT | 2026-11-10 | 2026-11-17 | -6.81% | -4.73% |
| rsi_reversal | GOOG | 2026-11-10 | 2026-11-17 | -3.26% | -1.19% |
| sma_200_trend | IWM | 2026-11-10 | 2026-11-17 | -3.84% | -1.76% |
| sma_200_trend | META | 2026-11-10 | 2026-11-17 | -2.94% | -0.87% |
| trend_momentum_combo | DIA | 2026-11-10 | 2026-11-17 | -2.72% | -0.65% |
| adx_trend | BAC | 2026-11-11 | 2026-11-17 | -2.81% | +0.40% |
| ema_crossover | DIA | 2026-11-11 | 2026-11-17 | -2.96% | +0.25% |
| ema_crossover | IWM | 2026-11-11 | 2026-11-17 | -4.93% | -1.72% |
| rsi_divergence | CVX | 2026-11-11 | 2026-11-17 | +1.79% | +5.01% |
| triple_ema | JPM | 2026-11-11 | 2026-11-17 | -3.77% | -0.56% |
| bollinger_bounce | NVDA | 2026-11-12 | 2026-11-17 | +2.96% | +5.82% |
| donchian_breakout | AAPL | 2026-11-12 | 2026-11-17 | -1.65% | +1.21% |
| ema_crossover | GS | 2026-11-12 | 2026-11-17 | -4.28% | -1.42% |
| ema_crossover | TSLA | 2026-11-12 | 2026-11-17 | -1.58% | +1.27% |
| ichimoku_cloud | AMD | 2026-11-12 | 2026-11-17 | -11.61% | -8.75% |
| ichimoku_cloud | IWM | 2026-11-12 | 2026-11-17 | -4.40% | -1.54% |
| inside_bar_breakout | DIA | 2026-11-12 | 2026-11-17 | -3.26% | -0.40% |
| keltner_reversion | QQQ | 2026-11-12 | 2026-11-17 | -3.43% | -0.57% |
| macd_crossover | AAPL | 2026-11-12 | 2026-11-17 | -1.65% | +1.21% |
| macd_crossover | AMD | 2026-11-12 | 2026-11-17 | -11.61% | -8.75% |
| macd_crossover | META | 2026-11-12 | 2026-11-17 | -1.05% | +1.81% |
| sma_200_trend | QQQ | 2026-11-12 | 2026-11-17 | -3.43% | -0.57% |
| sma_200_trend | TSLA | 2026-11-12 | 2026-11-17 | -1.58% | +1.27% |
| trend_momentum_combo | CVX | 2026-11-12 | 2026-11-17 | +0.53% | +3.38% |
| trend_momentum_combo | XOM | 2026-11-12 | 2026-11-17 | -0.62% | +2.24% |
| donchian_breakout | AMD | 2026-11-13 | 2026-11-17 | -11.18% | -7.55% |
| macd_crossover | GOOG | 2026-11-13 | 2026-11-17 | -7.43% | -3.80% |
| triple_ema | TSLA | 2026-11-13 | 2026-11-17 | -5.44% | -1.81% |
| ichimoku_cloud | CVX | 2026-11-16 | 2026-11-17 | -1.77% | +0.53% |
| keltner_reversion | CVX | 2026-11-16 | 2026-11-17 | -1.77% | +0.53% |
| rsi2_pullback | AMZN | 2026-11-17 | 2026-11-17 | +0.24% | +1.05% |
| rsi2_pullback | SPY | 2026-11-17 | 2026-11-17 | -0.81% | +0.00% |
| rsi2_pullback | JPM | next session | - | n/a | n/a |
| rsi2_pullback | MSFT | next session | - | n/a | n/a |

Costs: 15 bp per round trip on completed trades; open marks carry no cost. Entry at the next open, exit at the 10th session's close. Signals from 10-15 to 11-02 were lost in the 11-03 reset and are not included.
