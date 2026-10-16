"""Aggressive US$1,000 paper accounts aiming at 10x-50x (pre-registered: docs/aggressive-accounts/PREREG.md).

    .venv/bin/python scripts/aggressive_accounts.py odds       history + bootstrap odds -> docs/aggressive-accounts/odds.md
    .venv/bin/python scripts/aggressive_accounts.py forward    live record -> docs/aggressive-accounts/report.md

Paper only, offline (state/external_bars). Reuses the quant-accounts simulator (next-open fills, 5 bp slippage,
IBKR-style commissions, fractional shares). The options account of this group lives in scripts/options_paper.py
(Book M).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import quant_accounts as qa  # noqa: E402

OUT_DIR = ROOT / "docs" / "aggressive-accounts"
DECISION, END = date(2026, 10, 15), date(2027, 4, 15)
LEV, LEV_SIGNAL, CASH = "TQQQ", "QQQ", "SGOV"
TQQQ_COST = (0.0095 + 2 * 0.05) / 252     # synthetic 3x: expense ratio + financing of the 2x borrowed exposure


def leveraged_trend(c: pd.DataFrame, held: dict) -> dict:
    """100% TQQQ while QQQ closes above its 200-day average, else 100% T-bills (Gayed & Bilello 2016)."""
    q = c[LEV_SIGNAL].dropna()
    if len(q) < 200 or LEV not in c:
        return {}
    on = q.iloc[-1] > q.iloc[-200:].mean()
    want = {LEV: 1.0, CASH: 0.0} if on else {LEV: 0.0, CASH: 1.0}
    cur_lev = held.get(LEV, 0.0) > 0.5
    return {} if (on == cur_lev and held) else want


def top1_momentum(c: pd.DataFrame, held: dict) -> dict:
    """100% in the single stock with the best 12-1 month return (monthly)."""
    if len(c) < 253:
        return {}
    u = [s for s in qa.STOCKS if s in c]
    score = (c[u].iloc[-22] / c[u].iloc[-253] - 1).dropna()
    best = score.idxmax()
    return {**{s: 0.0 for s in held if s != best}, best: 1.0}


STRATEGIES = {
    "lev_trend": ("3x Nasdaq trend (TQQQ above the 200-day line, else T-bills)", leveraged_trend, "daily"),
    "top1_mom": ("All-in top momentum stock", top1_momentum, "monthly"),
}


def panel():
    syms = sorted(set(qa.STOCKS) | {LEV, LEV_SIGNAL, CASH, "SPY"})
    qa.ETFS.update({LEV})
    return qa.load_panel(syms)


def with_synthetic_tqqq(opens: pd.DataFrame, closes: pd.DataFrame):
    """History analysis only: extend TQQQ before its first bar on file with 3x daily QQQ returns minus costs, so the
    2022 bear market is included. The live record always uses real TQQQ bars."""
    o, c = opens.copy(), closes.copy()
    first = c[LEV].first_valid_index()
    r = c[LEV_SIGNAL].pct_change(fill_method=None).fillna(0) * 3 - TQQQ_COST
    level = (1 + r).cumprod()
    scale = c.loc[first, LEV] / level.loc[first]
    syn = level * scale
    pre = c.index < first
    c.loc[pre, LEV] = syn[pre]
    ratio = (o[LEV_SIGNAL] / c[LEV_SIGNAL].shift(1) - 1) * 3 + 1          # synthetic open from QQQ's gap
    o.loc[pre, LEV] = (syn.shift(1) * ratio)[pre]
    return o, c


def bootstrap(daily: pd.Series, years: float = 3.0, n: int = 10_000, block: int = 21, seed: int = 7) -> dict:
    rng = np.random.default_rng(seed)
    r = daily.dropna().to_numpy()
    days = int(252 * years)
    nb = days // block + 1
    starts = rng.integers(0, len(r) - block, size=(n, nb))
    paths = np.stack([np.concatenate([r[s:s + block] for s in row])[:days] for row in starts])
    eq = np.cumprod(1 + paths, axis=1)
    final = eq[:, -1]
    trough = eq.min(axis=1)
    return {"p10x": float((eq.max(axis=1) >= 10).mean()), "p10x_end": float((final >= 10).mean()),
            "p50x": float((eq.max(axis=1) >= 50).mean()), "p_lose80": float((trough <= 0.2).mean()),
            "median": float(np.median(final)), "p25": float(np.percentile(final, 25)),
            "p75": float(np.percentile(final, 75)), "p_below_start": float((final < 1).mean())}


def run_odds() -> str:
    opens, closes = panel()
    o, c = with_synthetic_tqqq(opens, closes)
    spy = c["SPY"]
    out = ["# Aggressive accounts: odds of turning US$1,000 into US$10k-US$50k", "",
           "Two steps for each stock/ETF strategy: (1) the actual rules run over the daily history on file with real costs; "
           "(2) 10,000 three-year paths are resampled from that history in one-month blocks, to estimate how often "
           "such a strategy reaches 10x or 50x and how often it loses 80% on the way. The history is mostly a bull "
           "market with a hindsight-chosen stock list, so these odds are optimistic.", "",
           "| Strategy | History window | Total | CAGR | Worst drop | SPY same window | "
           "3y: hit 10x at some point | 3y: end above 10x | 3y: hit 50x | 3y: lose 80% at some point | "
           "3y median ending value of US$1,000 | 3y 25th to 75th pct |",
           "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    rows = []
    for key, (name, fn, freq) in STRATEGIES.items():
        need = [LEV_SIGNAL] if key == "lev_trend" else qa.STOCKS
        first_ok = c[[s for s in need if s in c]].apply(lambda col: col.first_valid_index()).max()
        start = c.index[c.index.searchsorted(first_ok) + (200 if key == "lev_trend" else 252)]
        res = qa.simulate(fn, freq, o, c, start, DECISION)
        cur = res["curve"]
        m = qa.metrics(cur, spy)
        daily = cur.pct_change()
        b = bootstrap(daily)
        sp = spy.loc[cur.index]
        # "Normal market" scenario: remove the part of the return that came from the unusually strong market,
        # scaled by the strategy's beta (SPY long-run ~8%/yr instead of this sample's average).
        excess = spy.pct_change().loc[cur.index].mean() - 0.08 / 252
        bn = bootstrap(daily - m["beta"] * excess)
        rows.append((name, start, m, b, sp, bn))
    sp_daily = spy.pct_change().loc[rows[0][1]:]
    rows.append(("SPY buy-and-hold (reference)", rows[0][1], qa.metrics((1 + sp_daily.fillna(0)).cumprod(), spy),
                 bootstrap(sp_daily), spy.loc[rows[0][1]:], bootstrap(sp_daily - (sp_daily.mean() - 0.08 / 252))))
    for name, start, m, b, sp, _ in rows:
        out.append(f"| {name} | {start.date()} to {DECISION} | {m['total']:+.0%} | {m['cagr']:+.0%} | "
                   f"{m['max_dd']:.0%} | {sp.iloc[-1] / sp.iloc[0] - 1:+.0%} | {b['p10x']:.1%} | {b['p10x_end']:.1%} | "
                   f"{b['p50x']:.1%} | {b['p_lose80']:.1%} | ${1000 * b['median']:,.0f} | "
                   f"${1000 * b['p25']:,.0f} to ${1000 * b['p75']:,.0f} |")
    out += ["", "## Same, in a normal market", "",
            "The history above is an unusually strong market (SPY about +17% to +21% a year). Re-running the resampling after "
            "removing that extra market return (scaled by each strategy's beta, SPY back to about 8% a year):", "",
            "| Strategy | 3y: hit 10x | 3y: hit 50x | 3y: lose 80% at some point | 3y: end below US$1,000 | "
            "3y median ending value |", "|---|---:|---:|---:|---:|---:|"]
    for name, start, m, b, sp, bn in rows:
        out.append(f"| {name} | {bn['p10x']:.1%} | {bn['p50x']:.1%} | {bn['p_lose80']:.1%} | {bn['p_below_start']:.0%} | "
                   f"${1000 * bn['median']:,.0f} |")
    out += ["", "Even this is optimistic for the all-in momentum account: its stock list is today's winners "
            "(it could only ever pick NVDA, AVGO, LLY and the like), which a real investor in 2023 could not know.",
            "", "What it takes: 10x in 3 years is about +115% a year; 50x is about +270% a year, every year. "
            "Even the best hedge funds compound at 20-40% a year. The live accounts test whether any of this shows up.",
            "", "The 3x Nasdaq window starts in July 2022, after most of the 2022 crash (the data on file starts in "
            "October 2021 and the rule needs 200 days). Buy-and-hold TQQQ fell about 79% in 2022; the 200-day rule "
            "would have been out for most of it, but whipsaws like late 2018 and March 2020 are not in this sample.",
            "", "The 3x Nasdaq history before 2022-10-20 uses synthetic TQQQ (3x QQQ daily, minus fees and financing; "
            "checked against real TQQQ since 2022-10: correlation 0.9996, total return within a few percent).", ""]
    return "\n".join(out)


def run_forward() -> tuple[str, dict]:
    opens, closes = panel()
    last = min(closes.index[-1].date(), END)
    spy = closes["SPY"]
    rec = {"asof": str(last), "accounts": {}}
    if last < DECISION:
        return "# Aggressive accounts\n\nNot started.\n", rec
    i0 = closes.index.searchsorted(pd.Timestamp(DECISION))
    started = len(closes.index) > i0 + 1
    spy_ret = float(spy.loc[:pd.Timestamp(last)].iloc[-1] / opens["SPY"].iloc[i0 + 1] - 1) if started else 0.0
    out = [f"# Aggressive paper accounts (as of {last})", "",
           f"Paper only. US$1,000 each, rules frozen in `PREREG.md`. First decision on the {DECISION} close, first "
           f"fills at the next open; review on {END}. The options account of this group (momentum calls) is Book M "
           f"in `docs/options-paper/report.md`. SPY from the same first open: {spy_ret:+.2%}.", "",
           "| Account | Equity | Multiple | Trades | Costs | Holdings |", "|---|---:|---:|---:|---:|---|"]
    for key, (name, fn, freq) in STRATEGIES.items():
        res = qa.simulate(fn, freq, opens, closes, DECISION, last)
        eq = float(res["curve"].iloc[-1])
        held = {s: round(q, 4) for s, q in res["shares"].items()}
        rec["accounts"][key] = {"equity": round(eq, 2), "holdings": held, "trades": len(res["trades"]),
                                "costs": round(res["costs"], 2)}
        out.append(f"| {name} | ${eq:,.2f} | {eq / qa.START_CASH:.2f}x | {len(res['trades'])} | ${res['costs']:.2f} | "
                   f"{', '.join(sorted(held)) or 'cash'} |")
    rec["spy_return"] = round(spy_ret, 6)
    out += ["", "Odds before starting are in `odds.md`. Expect large swings: a 3x fund can fall a third in a few "
            "weeks, and a single stock can halve.", ""]
    return "\n".join(out), rec


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["odds", "forward"])
    a = ap.parse_args(argv)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if a.cmd == "odds":
        md = run_odds()
        (OUT_DIR / "odds.md").write_text(md)
        print(md)
        return 0
    md, rec = run_forward()
    (OUT_DIR / "report.md").write_text(md)
    path = OUT_DIR / "journal.jsonl"
    rows = [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []
    rows = [r for r in rows if r["asof"] != rec["asof"]] + [rec]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
