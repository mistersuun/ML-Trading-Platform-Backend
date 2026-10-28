"""Single-look backtest of the Portfolio Pilot (docs/pilot/PREREG.md, D25) -> docs/pilot/backtest.md.

    .venv/bin/python scripts/pilot_backtest.py

Reads IBKR bars saved in state/pilot_bars/ (<SYM>.W.json weekly US proxies, <SYM>.D.json daily TSX ETFs). Offline.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import pilot as pl  # noqa: E402

BARS = ROOT / "state" / "pilot_bars"
OUT = ROOT / "docs" / "pilot" / "backtest.md"                     # committed: without S0
OUT_PRIVATE = ROOT / "state" / "pilot" / "backtest-private.md"    # git-ignored: with S0 (the owner's mix)

# S0 = the owner's own mix and leverage, read from a git-ignored file (holdings are never committed):
# state/pilot/s0_mix.json = {"values": {TSX symbol: CAD market value}, "leverage": float}.
S0_FILE = ROOT / "state" / "pilot" / "s0_mix.json"
STATIC_LEV = 1.45


def load(sym: str, kind: str) -> pd.Series:
    d = json.loads((BARS / f"{sym}.{kind}.json").read_text())
    t = pd.to_datetime(d["time"], utc=True)
    if kind == "W":
        idx = t.tz_convert(None).to_period("W-FRI").end_time.normalize()
    else:
        idx = t.tz_convert("America/New_York").tz_localize(None).normalize()
    s = pd.Series(d["close"], index=idx, dtype=float)
    return s[~s.index.duplicated(keep="last")].sort_index()


def owner_mix() -> tuple[dict[str, float], float]:
    d = json.loads(S0_FILE.read_text())
    return d["values"], float(d["leverage"])


def owner_weights() -> dict[str, float]:
    by = {}
    for t, v in owner_mix()[0].items():
        k = pl.SLEEVE_BY_TSX[t]
        by[k] = by.get(k, 0.0) + v
    tot = sum(by.values())
    return {k: v / tot for k, v in by.items()}


def table(results, f) -> list[str]:
    out = ["| Strategy | CAGR | Volatility | Sharpe | Max drawdown | Worst 12 months | Avg / max leverage | "
           "Lowest margin cushion | Turnover / yr | Costs + interest (x start) | Final (start = 1) |",
           "|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|"]
    for r in results:
        m = pl.metrics(r, f)
        out.append(f"| {m['name']} | {m['cagr']:+.1%} | {m['vol']:.1%} | {m['sharpe']:.2f} | {m['max_dd']:.1%} | "
                   f"{m['worst_12m']:.1%} | {m['avg_lev']:.2f} / {m['max_lev']:.2f} | {m['min_cushion']:.0%} | "
                   f"{m['turnover']:.2f}x | {m['costs'] + m['financing']:.2f} | {m['final']:.2f} |")
    return out


def run(prices: pd.DataFrame, f: pl.Freq, start, spy_key: str) -> tuple[list, dict]:
    strategic = {s.key: s.weight for s in pl.SLEEVES}
    res = [
        pl.simulate(prices, f, "S0 your account as held", "static", start, owner_weights(), owner_mix()[1]),
        pl.simulate(prices, f, "S1 restructured static (1.45x)", "static", start, strategic, STATIC_LEV),
        pl.simulate(prices, f, "S2 S1 + trend gate (1.45x)", "trend", start, fixed_lev=STATIC_LEV),
        pl.simulate(prices, f, "**S3 Pilot** (trend gate + vol target)", "pilot", start),
        pl.simulate(prices, f, f"B1 {spy_key} only, no leverage", "static", start, {"us_equity": 1.0}, 1.0),
        pl.simulate(prices, f, "B2 strategic weights, no leverage", "static", start, strategic, 1.0),
    ]
    return res, {"start": start}


def windows(res, names, periods) -> list[str]:
    out = ["| Period | " + " | ".join(names) + " |", "|---|" + "---:|" * len(names)]
    for label, a, b in periods:
        row = [label]
        for r in res:
            e = r.equity[(r.equity.index >= pd.Timestamp(a)) & (r.equity.index <= pd.Timestamp(b))]
            row.append(f"{e.iloc[-1] / e.iloc[0] - 1:+.0%}" if len(e) > 1 else "n/a")
        out.append("| " + " | ".join(row) + " |")
    return out


def main() -> int:
    # Part 1: weekly US proxies.
    weekly = {s.key: load(s.proxy, "W") for s in pl.SLEEVES}
    wp = pd.DataFrame(weekly).sort_index()
    wp = wp.iloc[:-1]                                     # drop the unfinished current week
    spy = wp["us_equity"].dropna()
    start_w = spy.index[pl.WEEKLY.long + 1]
    res_w, _ = run(wp, pl.WEEKLY, start_w, "SPY")
    entry = {k: wp[k].dropna().index[min(pl.WEEKLY.long, len(wp[k].dropna()) - 1)].date() for k in wp.columns}

    # Part 2: daily TSX ETFs.
    tickers = {t: load(t, "D") for s in pl.SLEEVES for t in s.tsx}
    dp = pl.sleeve_prices(tickers)
    start_d = dp["us_equity"].dropna().index[pl.DAILY.long + 1]
    res_d, _ = run(dp, pl.DAILY, start_d, "VFV")

    names = ["S0", "S1", "S2", "S3 Pilot", "B1", "B2"]
    for private in (True, False):
        write_report(private, names, res_w, res_d, wp, dp, start_w, start_d, entry)
    return 0


def write_report(private, names, res_w, res_d, wp, dp, start_w, start_d, entry) -> None:
    if not private:
        names, res_w, res_d = names[1:], res_w[1:], res_d[1:]
    lines = ["# Portfolio Pilot backtest (single look, rules frozen in PREREG.md before this was run)", "",
             ("Strategies: S0 = your account's current mix at its current leverage (private input); " if private else
              "Strategies (S0, the owner's own mix, is reported only in the git-ignored private copy): ") +
             "S1 = the strategic core/satellite mix "
             "at 1.45x; S2 = S1 with the satellite trend gate; S3 = the Pilot (trend gate + leverage from a 15% "
             "volatility target, 0.5x to 1.5x, traded only outside the bands); B1 = the S&P 500 alone, no "
             "leverage; B2 = the strategic mix, no leverage. Costs 10 bp per traded dollar; the loan pays 3.58% a year "
             "for the whole period; cash earns nothing. Margin cushion = equity / positions (a margin call is "
             "assumed near 30%).", "",
             f"## 1. Long history: weekly US proxies, {start_w.date()} to {wp.index[-1].date()}", "",
             *table(res_w, pl.WEEKLY), "",
             "Sleeves enter once their proxy has 52 weeks of history: "
             + ", ".join(f"{k} {v}" for k, v in sorted(entry.items(), key=lambda kv: kv[1])) + ". "
             "Before that, their weight is spread over the other sleeves (so bonds are absent before 2018 and "
             "semis before 2017).", "",
             "### Crisis windows (total return over the window)", "",
             *windows(res_w, names, [("2008 crash (2008-05 to 2009-03)", "2008-05-01", "2009-03-13"),
                                     ("Commodity bust (2011-09 to 2015-12)", "2011-09-01", "2015-12-31"),
                                     ("Late 2018", "2018-09-28", "2018-12-28"),
                                     ("COVID crash (2020-02 to 2020-03)", "2020-02-14", "2020-03-27"),
                                     ("2022 bear market", "2021-12-31", "2022-10-14"),
                                     ("Bull run 2023-2025", "2023-01-06", "2025-12-26")]), "",
             f"## 2. Your actual TSX ETFs, daily, {start_d.date()} to {dp.index[-1].date()}", "",
             *table(res_d, pl.DAILY), "",
             "Short sample (about 3 years after the 1-year warm-up), mostly a bull market: an implementation check, "
             "not evidence.", ""]
    out = OUT_PRIVATE if private else OUT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines))
    print(f"wrote {out}")


if __name__ == "__main__":
    sys.exit(main())
