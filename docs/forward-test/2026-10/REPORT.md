# Forward test report

Days journaled: 8

| date | status | equity | return % | SPY % | DD % | cand | intents | orders | fills | pos | recon | warn/err |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2026-10-05 | ok | 100,000 | 0.0 | 0.0 | 0.0 | 0 | 0 | 0 | 0 | 0 | ok | 1/0 |
| 2026-10-06 | ok | 100,000 | 0.0 | 0.55 | 0.0 | 0 | 0 | 0 | 0 | 0 | ok | 1/0 |
| 2026-10-07 | ok | 100,000 | 0.0 | 0.308 | 0.0 | 0 | 0 | 0 | 0 | 0 | ok | 1/0 |
| 2026-10-08 | ok | 100,000 | 0.0 | -0.116 | -0.003 | 0 | 0 | 0 | 0 | 0 | ok | 1/0 |
| 2026-10-09 | ok | 100,000 | 0.0 | 0.483 | 0.0 | 0 | 0 | 0 | 0 | 0 | ok | 1/0 |
| 2026-10-12 | ok | 100,000 | 0.0 | 0.422 | -0.0 | 0 | 0 | 0 | 0 | 0 | ok | 1/0 |
| 2026-10-13 | ok | 100,000 | 0.0 | 0.41 | -0.001 | 0 | 0 | 0 | 0 | 0 | ok | 1/0 |
| 2026-10-14 | ok | 100,000 | 0.0 | -0.675 | -0.004 | 0 | 0 | 0 | 0 | 0 | ok | 1/0 |

Equity, return, fills and positions above EXCLUDE the PLUMBING TEST — not a strategy (see its own section below).

Latest: equity 100,000.00 (0.0% vs SPY -0.675%), drawdown -0.004%, risk multiplier 1.0, halted=False

## Shadow signals (forward returns of alert-only / non-eligible signals)

| kind | reason | horizon | n | mean signed % | hit rate |
|---|---|---|---|---|---|
| raw_pattern | alert_only:unvalidated_raw_signal | 1d | 137 | -0.02 | 53% |
| raw_pattern | alert_only:unvalidated_raw_signal | 5d | 59 | +0.82 | 59% |

Shadow score slots still pending: 132

## PLUMBING TEST — not a strategy

A single SPY long to exercise the order path. Excluded from every table and statistic above and from the shadow study; its result says nothing about any strategy.

Latest state: closed, exit closed_at_open, P&L -1.62

- 2026-10-06 [entry] submitted qty=1 stop=755.72 tp=814.15
- 2026-10-07 [status] open
- 2026-10-08 [status] open
- 2026-10-09 [status] open
- 2026-10-12 [status] open
- 2026-10-13 [close] submitted qty=1 stop=None tp=None ['cancelled_protective_legs:2']
- 2026-10-14 [status] closed

## Errors

- none

## Crashes

- none
