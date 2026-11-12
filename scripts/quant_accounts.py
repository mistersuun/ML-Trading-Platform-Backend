"""Five US$1,000 paper accounts, one per published quant strategy (pre-registered: docs/quant-accounts/PREREG.md).

    .venv/bin/python scripts/quant_accounts.py backtest      history analysis -> docs/quant-accounts/backtest.md
    .venv/bin/python scripts/quant_accounts.py forward       the live month -> docs/quant-accounts/report.md

Paper only, offline: reads daily bars from state/external_bars (IBKR, read-only top-ups). One simulator serves
both modes: decisions on a session's close, fills at the next session's open with slippage and IBKR-style
commissions, fractional shares. `forward` re-simulates deterministically from the frozen start decision
(close of 2026-10-14) through the latest closed bar, capped at the end date; report.md is that replay, while
journal.jsonl is append-only (see update_journal).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import external_bars  # noqa: E402

OUT_DIR = ROOT / "docs" / "quant-accounts"
START_CASH = 1_000.0
SLIP = 0.0005                       # 5 bp per side
COMM_PER_SHARE, COMM_MIN, COMM_MAX_FRAC = 0.0035, 0.35, 0.01
BAND = 0.02                         # skip a rebalance trade smaller than 2% of equity (new/closed positions always trade)
FORWARD_DECISION, FORWARD_END = date(2026, 10, 14), date(2026, 11, 16)

STOCKS = ["AAPL", "ABBV", "ADBE", "AMD", "AMZN", "AVGO", "BAC", "BRK_B", "COST", "CRM", "CSCO", "CVX", "GOOG", "GS",
          "HD", "JNJ", "JPM", "KO", "LLY", "META", "MRK", "MSFT", "NFLX", "NVDA", "ORCL", "PEP", "PG", "TSLA", "UNH",
          "V", "WMT", "XOM"]
TREND_ASSETS = ["SPY", "EFA", "IEF", "GLD", "DBC"]
CASH_ETF = "SGOV"
RSI_ETFS = ["SPY", "QQQ", "IWM", "DIA"]
ETFS = set(TREND_ASSETS) | {CASH_ETF} | set(RSI_ETFS)


# ------------------------------------------------------------------------------------------------- data
def load_panel(symbols, root: Optional[Path] = None, closed: bool = False,
               now=None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Open and Close frames on SPY's session calendar (missing symbols are left out, with a note on stderr).
    `closed` keeps finished XNYS sessions only (external_bars.closed_bars, as services/forward.py does)."""
    opens, closes = {}, {}
    for s in symbols:
        try:
            if closed:
                from data.calendars import get_calendar
                df = external_bars.closed_bars(s, now, get_calendar("XNYS"), "etf" if s in ETFS else "equity", root)
            else:
                df = external_bars.load_bars(s, "etf" if s in ETFS else "equity", root)
        except external_bars.ExternalBarsError as exc:
            print(f"warning: {s}: {exc}", file=sys.stderr)
            continue
        if df.empty:
            print(f"warning: {s}: no bars", file=sys.stderr)
            continue
        opens[s], closes[s] = df["Open"].astype(float), df["Close"].astype(float)
    cal = closes["SPY"].index
    return pd.DataFrame(opens).reindex(cal), pd.DataFrame(closes).reindex(cal)


def rsi(close: pd.Series, n: int = 2) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


# ------------------------------------------------------------------------------------------- strategies
# A strategy gets (closes up to and including the decision session, current weights) and returns the weight
# changes to make at the next open ({symbol: target weight}); symbols not mentioned keep their shares.
def _top(score: pd.Series, n: int, largest: bool = True) -> list[str]:
    s = score.dropna()
    return list((s.nlargest(n) if largest else s.nsmallest(n)).index)


def _equal(names: list[str], universe: list[str], held: dict) -> dict:
    tgt = {s: 1 / len(names) for s in names} if names else {}
    return {**{s: 0.0 for s in held if s in universe and s not in tgt}, **tgt}


def trend(c: pd.DataFrame, held: dict) -> dict:
    """Time-series momentum (12-month return > 0) on 5 asset classes, 20% each; off sleeves in T-bills."""
    if len(c) < 253:
        return {}
    r12 = c.iloc[-1] / c.iloc[-253] - 1
    on = [a for a in TREND_ASSETS if a in c and pd.notna(r12.get(a)) and r12[a] > 0]
    tgt = {a: (0.2 if a in on else 0.0) for a in TREND_ASSETS if a in c}
    if CASH_ETF in c and pd.notna(c[CASH_ETF].iloc[-1]):
        tgt[CASH_ETF] = 0.2 * (len(TREND_ASSETS) - len(on))
    return tgt


def xs_momentum(c: pd.DataFrame, held: dict) -> dict:
    """Cross-sectional 12-1 month momentum: top 5 stocks, equal weight."""
    if len(c) < 253:
        return {}
    u = [s for s in STOCKS if s in c]
    score = c[u].iloc[-22] / c[u].iloc[-253] - 1
    return _equal(_top(score, 5), u, held)


def low_vol(c: pd.DataFrame, held: dict) -> dict:
    """Low volatility: the 6 stocks with the lowest 1-year daily-return volatility, equal weight."""
    if len(c) < 253:
        return {}
    u = [s for s in STOCKS if s in c]
    r = c[u].iloc[-253:].pct_change(fill_method=None)
    vol = r.std().where(r.count() >= 200)
    return _equal(_top(vol, 6, largest=False), u, held)


def high52(c: pd.DataFrame, held: dict) -> dict:
    """52-week-high proximity (George & Hwang 2004): the 5 stocks closest to their 52-week high, equal weight."""
    if len(c) < 252:
        return {}
    u = [s for s in STOCKS if s in c]
    win = c[u].iloc[-252:]
    score = (win.iloc[-1] / win.max()).where(win.count() >= 200)
    return _equal(_top(score, 5), u, held)


def rsi2_reversion(c: pd.DataFrame, held: dict) -> dict:
    """Connors RSI(2): buy an index ETF (25% slice) when close > 200-day SMA and RSI(2) < 10; sell when close > 5-day SMA."""
    out = {}
    for s in RSI_ETFS:
        if s not in c or len(c) < 201:
            continue
        x = c[s].dropna()
        last = x.iloc[-1]
        if held.get(s, 0) > 0:
            if last > x.iloc[-5:].mean():
                out[s] = 0.0
        elif last > x.iloc[-200:].mean() and rsi(x).iloc[-1] < 10:
            out[s] = 0.25
    return out


STRATEGIES: dict[str, tuple[str, Callable, str]] = {
    "trend": ("Trend following (time-series momentum)", trend, "monthly"),
    "momentum": ("Cross-sectional momentum (12-1)", xs_momentum, "monthly"),
    "lowvol": ("Low volatility", low_vol, "monthly"),
    "high52": ("52-week-high proximity", high52, "monthly"),
    "rsi2": ("Short-term mean reversion (RSI-2)", rsi2_reversion, "daily"),
}


# ------------------------------------------------------------------------------------------------ simulator
def commission(shares: float, price: float) -> float:
    value = abs(shares) * price
    return min(max(COMM_PER_SHARE * abs(shares), COMM_MIN), COMM_MAX_FRAC * value)


def simulate(fn: Callable, freq: str, opens: pd.DataFrame, closes: pd.DataFrame, first_decision, last_day,
             cash: float = START_CASH) -> dict:
    """Decide on the close of `first_decision` (then monthly on each month's last session, or daily), fill at the
    next open. Returns the daily equity curve, trades and final holdings."""
    cal = closes.index
    i0 = cal.searchsorted(pd.Timestamp(first_decision))
    i1 = cal.searchsorted(pd.Timestamp(last_day), side="right") - 1
    shares: dict[str, float] = {}
    pending: dict = {}
    curve, trades, costs = {}, [], 0.0
    for i in range(i0, i1 + 1):
        day = cal[i]
        if i > i0 and pending:          # execute at this session's open
            px_open = opens.iloc[i]
            mark = {s: (px_open.get(s) if pd.notna(px_open.get(s)) else closes.iloc[i - 1].get(s)) for s in shares}
            eq = cash + sum(q * mark[s] for s, q in shares.items())
            def _delta(kv):
                o = px_open.get(kv[0])
                return 0.0 if o is None or pd.isna(o) else kv[1] * eq - shares.get(kv[0], 0.0) * o

            for s, w in sorted(pending.items(), key=_delta):     # sells (negative deltas) first, then buys
                o = px_open.get(s)
                if o is None or pd.isna(o):
                    continue
                cur = shares.get(s, 0.0)
                tgt_val = w * eq
                delta_val = tgt_val - cur * o
                is_open_close = (cur == 0 and w > 0) or (w == 0 and cur != 0)
                if abs(delta_val) < 1e-9 or (not is_open_close and abs(delta_val) < BAND * eq):
                    continue
                side = 1 if delta_val > 0 else -1
                fill = o * (1 + SLIP * side)
                q = (tgt_val / fill) - cur if w > 0 else -cur
                if side > 0:
                    q = min(q, max(0.0, (cash - COMM_MIN) / fill))
                if abs(q) < 1e-9:
                    continue
                fee = commission(q, fill)
                cash -= q * fill + fee
                costs += fee + abs(q) * o * SLIP
                shares[s] = cur + q
                if abs(shares[s]) < 1e-9:
                    shares.pop(s)
                trades.append({"date": str(day.date()), "symbol": s, "shares": round(q, 6), "price": round(fill, 4),
                               "fee": round(fee, 2)})
            pending = {}
        px = closes.iloc[i]
        last_px = closes.iloc[: i + 1].ffill().iloc[-1] if any(pd.isna(px.get(s)) for s in shares) else px
        equity = cash + sum(q * (last_px.get(s) if pd.notna(last_px.get(s)) else 0.0) for s, q in shares.items())
        curve[day] = equity
        nxt = cal[i + 1] if i + 1 < len(cal) else None
        month_end = nxt is None or nxt.month != day.month
        if i < i1 and (i == i0 or freq == "daily" or month_end):
            hist = closes.iloc[: i + 1]
            weights = {s: q * px[s] / equity for s, q in shares.items() if pd.notna(px.get(s))}
            pending = fn(hist, weights)
    return {"curve": pd.Series(curve), "trades": trades, "shares": shares, "cash": cash, "costs": costs}


# ------------------------------------------------------------------------------------------------- metrics
def metrics(curve: pd.Series, bench: pd.Series) -> dict:
    r = curve.pct_change().dropna()
    yrs = len(r) / 252
    cagr = (curve.iloc[-1] / curve.iloc[0]) ** (1 / yrs) - 1 if yrs > 0 else float("nan")
    vol = r.std() * math.sqrt(252)
    dd = (curve / curve.cummax() - 1).min()
    b = bench.reindex(curve.index).pct_change().dropna()
    beta = r.cov(b) / b.var() if b.var() > 0 else float("nan")
    m = curve.iloc[::21].pct_change().dropna()
    mb = bench.reindex(curve.index).iloc[::21].pct_change().dropna()
    ex = (m - mb).dropna()
    return {"cagr": cagr, "vol": vol, "sharpe": r.mean() / r.std() * math.sqrt(252) if r.std() > 0 else float("nan"),
            "max_dd": dd, "beta": beta, "months": len(ex), "beat_spy_months": float((ex > 0).mean()) if len(ex) else
            float("nan"), "ex_p10": float(ex.quantile(0.1)) if len(ex) else float("nan"),
            "ex_p90": float(ex.quantile(0.9)) if len(ex) else float("nan"), "total": curve.iloc[-1] / curve.iloc[0] - 1}


def run_backtest(opens, closes) -> str:
    end = FORWARD_DECISION
    spy = closes["SPY"]
    out = ["# Quant accounts: historical analysis (before the live month)", "",
           "Same simulator, rules and costs as the live accounts (5 bp slippage per side, IBKR-style commissions "
           "with a US$0.35 minimum, fractional shares, US$1,000 start). Each strategy starts once its 12-month "
           "lookback is available, so start dates differ; SPY over the same window is shown on each row.", "",
           "| Strategy | Window | Total | CAGR | Vol | Sharpe | Max DD | Beta | SPY total (same window) | "
           "Months beating SPY | 1-month excess, 10th to 90th pct | Costs |",
           "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|"]
    for key, (name, fn, freq) in STRATEGIES.items():
        need = TREND_ASSETS if key == "trend" else (RSI_ETFS if key == "rsi2" else STOCKS)
        avail = closes[[s for s in need if s in closes]].dropna(how="all")
        first_ok = avail.apply(lambda col: col.first_valid_index()).max()
        i_first = closes.index.searchsorted(first_ok) + 252
        if i_first >= len(closes.index):
            out.append(f"| {name} | not enough history | | | | | | | | | | |")
            continue
        start = closes.index[i_first]
        res = simulate(fn, freq, opens, closes, start, end)
        m = metrics(res["curve"], spy)
        sp = spy.loc[res["curve"].index]
        out.append(f"| {name} | {start.date()} to {end} | {m['total']:+.1%} | {m['cagr']:+.1%} | {m['vol']:.1%} | "
                   f"{m['sharpe']:.2f} | {m['max_dd']:.1%} | {m['beta']:.2f} | {sp.iloc[-1] / sp.iloc[0] - 1:+.1%} | "
                   f"{m['beat_spy_months']:.0%} of {m['months']} | {m['ex_p10']:+.1%} to {m['ex_p90']:+.1%} | "
                   f"${res['costs']:.2f} |")
    out += ["", "How to read this:",
            "- About 3 to 4 years of daily data, mostly a strong, tech-led bull market, so large-cap growth and "
            "momentum look good and defensive rules look weak. Published evidence spans decades (see PREREG.md).",
            "- The stock universe is 32 of today's large caps, chosen with hindsight (survivorship bias): it flatters "
            "every stock strategy.",
            "- The '1-month excess' range is the key calibration for the live test: it shows how far one month's "
            "result can land from SPY by chance alone.", ""]
    return "\n".join(out)


def run_forward(opens, closes, asof: Optional[date] = None) -> tuple[str, dict]:
    last = min(closes.index[-1].date(), FORWARD_END, asof or FORWARD_END)
    if last < FORWARD_DECISION:
        return (f"# Five quant paper accounts\n\nNot started: the first decision needs the {FORWARD_DECISION} close "
                f"(latest bar on file: {last}).\n"), {"asof": str(last), "accounts": {}}
    spy = closes["SPY"]
    rows, record = [], {"asof": str(last), "accounts": {}}
    for key, (name, fn, freq) in STRATEGIES.items():
        res = simulate(fn, freq, opens, closes, FORWARD_DECISION, last)
        eq = float(res["curve"].iloc[-1])
        held = {s: round(q, 4) for s, q in res["shares"].items()}
        record["accounts"][key] = {"equity": round(eq, 2), "cash": round(res["cash"], 2), "holdings": held,
                                   "trades": len(res["trades"]), "costs": round(res["costs"], 2)}
        rows.append((name, eq, res, held))
    i0 = closes.index.searchsorted(pd.Timestamp(FORWARD_DECISION))
    spy_ret = spy.loc[:pd.Timestamp(last)].iloc[-1] / opens["SPY"].iloc[i0 + 1] - 1 if len(spy) > i0 + 1 else 0.0
    out = [f"# Five quant paper accounts (as of {last})", "",
           f"Paper only. US$1,000 each, rules frozen in `PREREG.md`. First decision on the {FORWARD_DECISION} close, "
           f"fills from the next open; the test ends on the {FORWARD_END} close. SPY from the same first open: "
           f"{spy_ret:+.2%}.", "",
           "| Account | Equity | Return | vs SPY | Trades | Costs paid | Holdings |", "|---|---:|---:|---:|---:|---:|---|"]
    for name, eq, res, held in sorted(rows, key=lambda r: -r[1]):
        ret = eq / START_CASH - 1
        hs = ", ".join(sorted(held)) or "cash"
        out.append(f"| {name} | ${eq:,.2f} | {ret:+.2%} | {(ret - spy_ret) * 100:+.2f} pts | {len(res['trades'])} | "
                   f"${res['costs']:.2f} | {hs} |")
    out += ["", "One month cannot separate skill from luck: see `backtest.md` for how widely a single month lands "
            "around SPY by chance. Read this as a demonstration of five different ways to run money, not a ranking.",
            ""]
    record["spy_return"] = round(float(spy_ret), 6)
    return "\n".join(out), record


def replay_curves(strategies: dict, opens, closes, decision, last) -> dict:
    """The deterministic daily equity curve of every account, from `decision` through `last`."""
    return {k: simulate(fn, freq, opens, closes, decision, last)["curve"] for k, (_, fn, freq) in strategies.items()}


def update_journal(path: Path, curves: dict, make_record: Callable, decision: date, accept: bool = False,
                   today: Optional[str] = None) -> tuple[list, list]:
    """Journal rows are a permanent record: an existing row is never rewritten or reordered. For each session after
    `decision` on the replayed curves: no row yet -> appended (or inserted as a backfill when later rows exist);
    a row whose stored equity differs from the replay by more than US$0.01 -> REVISION warning plus a line in the
    revisions log next to the journal, stored row kept (`accept` replaces it with the replay, still logged).
    Returns (dates added, revisions)."""
    today = today or date.today().isoformat()
    lines = [x for x in path.read_text().splitlines() if x.strip()] if path.exists() else []
    rows = [json.loads(x) for x in lines]
    rev_path = path.with_name("revisions.jsonl")
    logged = {(r["date"], r["account"], r["stored"], r["replayed"], r.get("accepted", False))
              for r in (json.loads(x) for x in rev_path.read_text().splitlines() if x.strip())} \
        if rev_path.exists() else set()
    added, revisions = [], []
    for ts in next(iter(curves.values())).index:
        if ts.date() <= decision:
            continue
        day = str(ts.date())
        i = next((n for n, r in enumerate(rows) if r["asof"] == day), None)
        if i is None:
            rec = make_record(day)
            if rows and day < rows[-1]["asof"]:
                rec = {**rec, "backfilled": True, "backfilled_on": today}
                i = sum(1 for r in rows if r["asof"] < day)
            else:
                i = len(rows)
            rows.insert(i, rec)
            lines.insert(i, json.dumps(rec))
            added.append(day)
            continue
        diffs = [(k, a["equity"], round(float(curves[k].loc[ts]), 2)) for k, a in rows[i]["accounts"].items()
                 if k in curves and abs(a["equity"] - float(curves[k].loc[ts])) > 0.01]
        for k, old, new in diffs:
            print(f"REVISION {day} {k}: journal {old:.2f}, replay {new:.2f}" + (" (replaced)" if accept else
                  " (journal row kept; --accept-revision to replace)"), file=sys.stderr)
            revisions.append({"date": day, "account": k, "stored": old, "replayed": new})
        if diffs and accept:
            keep = {k: v for k, v in rows[i].items() if k in ("backfilled", "backfilled_on")}
            rows[i] = {**make_record(day), **keep}
            lines[i] = json.dumps(rows[i])
    new_revs = [r for r in revisions if (r["date"], r["account"], r["stored"], r["replayed"], accept) not in logged]
    if new_revs:
        with rev_path.open("a") as f:
            f.writelines(json.dumps({**r, "accepted": accept, "logged_on": today}) + "\n" for r in new_revs)
    if added or (revisions and accept):
        path.write_text("".join(x + "\n" for x in lines))
    return added, revisions


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["backtest", "forward"])
    ap.add_argument("--accept-revision", action="store_true",
                    help="forward: replace journal rows whose replay differs (the revision is still logged)")
    a = ap.parse_args(argv)
    syms = sorted(set(STOCKS) | ETFS)
    opens, closes = load_panel(syms, closed=a.cmd == "forward")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if a.cmd == "backtest":
        md = run_backtest(opens, closes)
        (OUT_DIR / "backtest.md").write_text(md)
        print(md)
        return 0
    md, rec = run_forward(opens, closes)
    (OUT_DIR / "report.md").write_text(md)
    if rec["accounts"]:
        curves = replay_curves(STRATEGIES, opens, closes, FORWARD_DECISION, date.fromisoformat(rec["asof"]))
        update_journal(OUT_DIR / "journal.jsonl", curves, lambda d: run_forward(opens, closes, date.fromisoformat(d))[1],
                       FORWARD_DECISION, a.accept_revision)
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
