"""Forward scoring of the nightly pooled shadow signals (rules frozen in docs/pooled-forward/PREREG.md). Paper only.

    .venv/bin/python scripts/pooled_forward.py record   # append new sessions' signals to docs/pooled-forward/signals.jsonl
    .venv/bin/python scripts/pooled_forward.py score    # rebuild docs/pooled-forward/report.md

Reads the git-ignored pooled payloads (state/forward/results/pooled/) and finished-session daily bars; writes only
docs/pooled-forward/. Never places, sizes or alerts on anything.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import external_bars  # noqa: E402

PAYLOADS = ROOT / "state" / "forward" / "results" / "pooled"
OUT = ROOT / "docs" / "pooled-forward"
HOLD, COST = 10, 0.0015
ALPHA, MIN_TRADES, MIN_WEEKS = 0.05 / 3, 60, 10
LOOKS = ("2026-12-31", "2027-02-26", "2027-04-15")
ETFS = {"SPY", "QQQ", "IWM", "DIA"}


# ------------------------------------------------------------------------------------------------ record
def payload_sessions(paths) -> dict[str, dict]:
    """{asof: row} from pooled payload files; the latest-generated payload wins for a session."""
    best: dict[str, tuple[str, dict]] = {}
    for p in paths:
        try:
            d = json.loads(Path(p).read_text())
        except (OSError, ValueError):
            continue
        pl = d.get("payload") or {}
        asof, gen = pl.get("asof"), pl.get("generated_at") or d.get("generated_at") or ""
        if not asof or pl.get("status") != "ok":
            continue
        if asof not in best or gen > best[asof][0]:
            best[asof] = (gen, {"asof": asof, "run_id": pl.get("run_id"), "generated_at": gen,
                                "signals": pl.get("shadow_signals") or []})
    return {k: v for k, (_, v) in best.items()}


def record(journal: Path, payload_dir: Path) -> list[str]:
    """Append sessions not yet in the journal (append-only; an existing session is never rewritten)."""
    have = set()
    if journal.exists():
        have = {json.loads(l)["asof"] for l in journal.read_text().splitlines() if l.strip()}
    new = payload_sessions(sorted(payload_dir.glob("2*.json")))
    added = sorted(a for a in new if a not in have)
    if added:
        journal.parent.mkdir(parents=True, exist_ok=True)
        with journal.open("a") as f:
            for a in added:
                f.write(json.dumps(new[a], sort_keys=True) + "\n")
    return added


# ------------------------------------------------------------------------------------------------ score
def load_signals(journal: Path) -> pd.DataFrame:
    rows = []
    for l in journal.read_text().splitlines() if journal.exists() else []:
        if not l.strip():
            continue
        r = json.loads(l)
        for s in r["signals"]:
            if s.get("stale") or s.get("excluded"):
                continue
            rows.append({"asof": r["asof"], "symbol": s["symbol"], "pattern": s["pattern"],
                         "signal_bar_date": s["signal_bar_date"]})
    return pd.DataFrame(rows, columns=["asof", "symbol", "pattern", "signal_bar_date"])


def build_trades(sig: pd.DataFrame, opens: pd.DataFrame, closes: pd.DataFrame, hold: int = HOLD,
                 cost: float = COST) -> pd.DataFrame:
    """One row per de-duplicated trade. Sessions = the closes' index (SPY's calendar)."""
    cal = closes.index
    out = []
    last_exit: dict[tuple, pd.Timestamp] = {}
    for _, s in sig.sort_values(["signal_bar_date", "pattern", "symbol"]).iterrows():
        d = pd.Timestamp(s["signal_bar_date"])
        k = int(cal.searchsorted(d, side="right"))            # next session after the signal bar
        key = (s["pattern"], s["symbol"])
        if k >= len(cal):
            entry = None
            if key in last_exit and last_exit[key] >= cal[-1]:
                continue                                       # still inside an open trade of the same pair
        else:
            entry = cal[k]
            if key in last_exit and entry <= last_exit[key]:
                continue                                       # inside an open trade of the same pair
        x = k + hold - 1
        row = {"pattern": s["pattern"], "symbol": s["symbol"], "signal_bar_date": s["signal_bar_date"],
               "entry_date": entry, "exit_date": cal[x] if x < len(cal) else None, "done": x < len(cal)}
        if entry is None or s["symbol"] not in closes:
            row.update(done=False, missing=entry is not None)
            out.append(row)
            continue
        last_exit[key] = cal[x] if x < len(cal) else pd.Timestamp.max
        o = opens.at[entry, s["symbol"]]
        so = opens.at[entry, "SPY"]
        end = cal[min(x, len(cal) - 1)]
        c, sc = closes.at[end, s["symbol"]], closes.at[end, "SPY"]
        if not all(np.isfinite([o, so, c, sc])):
            row.update(done=False, missing=True)
            out.append(row)
            continue
        ret = c / o - 1 - (cost if row["done"] else 0.0)
        row.update(ret=ret, spy=sc / so - 1, excess=ret - (sc / so - 1), missing=False, mark_date=end)
        out.append(row)
    return pd.DataFrame(out)


def symbol_controls(opens: pd.DataFrame, closes: pd.DataFrame, start, hold: int = HOLD,
                    cost: float = COST) -> dict[str, float]:
    """Each symbol's mean 10-session excess over SPY across every entry date from `start` whose exit has closed."""
    cal = closes.index
    k0 = int(cal.searchsorted(pd.Timestamp(start), side="left"))
    ctrl = {}
    for sym in closes.columns:
        if sym == "SPY":
            continue
        xs = []
        for k in range(k0, len(cal) - hold + 1):
            e, x = cal[k], cal[k + hold - 1]
            o, c, so, sc = opens.at[e, sym], closes.at[x, sym], opens.at[e, "SPY"], closes.at[x, "SPY"]
            if all(np.isfinite([o, c, so, sc])):
                xs.append((c / o - 1 - cost) - (sc / so - 1))
        if xs:
            ctrl[sym] = float(np.mean(xs))
    return ctrl


def _t_sf(t: float, df: int) -> float:
    from scipy import stats
    return float(stats.t.sf(t, df))


def cluster_test(values: pd.Series, weeks: pd.Series) -> dict:
    """Mean of the week means, its standard error over weeks and a one-sided p (H1: mean > 0)."""
    wm = values.groupby(weeks).mean()
    n = len(wm)
    if n < 2:
        return {"mean": float(wm.mean()) if n else float("nan"), "se": float("nan"), "t": float("nan"),
                "p": float("nan"), "weeks": n}
    se = float(wm.std(ddof=1) / math.sqrt(n))
    t = float(wm.mean() / se) if se > 0 else float("nan")
    return {"mean": float(wm.mean()), "se": se, "t": t, "p": _t_sf(t, n - 1) if np.isfinite(t) else float("nan"),
            "weeks": n}


def evaluate(trades: pd.DataFrame, ctrl: dict[str, float]) -> dict:
    done = trades[trades["done"] & ~trades["missing"].astype(bool)].copy() if len(trades) else trades
    res = {"n_trades": int(len(done)), "n_open": int((~trades["done"]).sum()) if len(trades) else 0}
    if done.empty:
        return {**res, "weeks": 0, "size_ok": False, "pass": False}
    done["week"] = [f"{d.isocalendar().year}-W{d.isocalendar().week:02d}" for d in pd.to_datetime(done["entry_date"])]
    done["vs_ctrl"] = done["excess"] - done["symbol"].map(ctrl)
    prim = cluster_test(done["excess"], done["week"])
    c2 = cluster_test(done["vs_ctrl"].dropna(), done.loc[done["vs_ctrl"].notna(), "week"])
    wk = done.groupby("week")["vs_ctrl"].mean().dropna()
    ex2 = float(wk.drop(wk.nlargest(2).index).mean()) if len(wk) > 2 else float("nan")
    size_ok = len(done) >= MIN_TRADES and prim["weeks"] >= MIN_WEEKS
    ok = bool(size_ok and prim["p"] < ALPHA and c2["p"] < ALPHA and ex2 > 0)
    return {**res, "weeks": prim["weeks"], "primary": prim, "vs_control": c2, "vs_control_ex_top2_weeks": ex2,
            "size_ok": bool(size_ok), "pass": ok, "trades": done}


def load_prices(symbols, now=None) -> tuple[pd.DataFrame, pd.DataFrame]:
    from data.calendars import get_calendar
    cal = get_calendar("XNYS")
    o, c = {}, {}
    for s in sorted(set(symbols) | {"SPY"}):
        try:
            df = external_bars.closed_bars(s, now, cal, "etf" if s in ETFS else "equity")
        except external_bars.ExternalBarsError as exc:
            print(f"warning: {s}: {exc}", file=sys.stderr)
            continue
        if not df.empty:
            o[s], c[s] = df["Open"].astype(float), df["Close"].astype(float)
    idx = c["SPY"].index
    return pd.DataFrame(o).reindex(idx), pd.DataFrame(c).reindex(idx)


def _pct(x) -> str:
    return "n/a" if x is None or not np.isfinite(x) else f"{x:+.2%}"


def _p(x, inference: bool) -> str:
    return f"{x:.3f}" if inference and x is not None and np.isfinite(x) else "n/a"


def render(sig: pd.DataFrame, trades: pd.DataFrame, ev: dict, asof: str) -> str:
    look = asof in LOOKS
    lines = ["# Pooled shadow, forward scoring", "",
             f"As of {asof}. Rules frozen in `PREREG.md` (2026-11-14). Paper only: pooled signals are never orders. "
             + ("**This is a pre-registered look.**" if look else
                f"Informational; only the looks on {', '.join(LOOKS)} decide."), "",
             f"Signals recorded: {len(sig)} over {sig['asof'].nunique() if len(sig) else 0} sessions. "
             f"De-duplicated trades: {len(trades)}: {ev['n_trades']} completed, {ev['n_open']} open.", ""]
    if ev["n_trades"]:
        p, c2 = ev["primary"], ev["vs_control"]
        inf = ev["size_ok"]
        lines += ["| Statistic | Mean | One-sided p | Entry weeks |", "|---|---:|---:|---:|",
                  f"| Trade excess vs SPY (same window) | {_pct(p['mean'])} | "
                  f"{_p(p['p'], inf)} | {p['weeks']} |",
                  f"| Excess vs the symbol's all-days control | {_pct(c2['mean'])} | "
                  f"{_p(c2['p'], inf)} | {c2['weeks']} |",
                  f"| Same, without the best 2 entry weeks | {_pct(ev['vs_control_ex_top2_weeks'])} | | |", "",
                  f"Size: {ev['n_trades']} completed trades (needs {MIN_TRADES}) and {ev['weeks']} entry weeks "
                  f"(needs {MIN_WEEKS}): {'met' if inf else 'not met, so inference is n/a'}. "
                  f"Pass rule: both p < {ALPHA:.4f} and the ex-top-2 mean > 0. "
                  + (f"**Status at this look: {'PASS' if ev['pass'] else 'FAIL'}.**" if look else ""), "",
                  "## By pattern (descriptive, no test)", "",
                  "| Pattern | Completed | Mean excess vs SPY | Mean vs control |", "|---|---:|---:|---:|"]
        t = ev["trades"]
        g = t.groupby("pattern").agg(n=("excess", "size"), ex=("excess", "mean"), vc=("vs_ctrl", "mean"))
        lines += [f"| {k} | {int(r.n)} | {_pct(r.ex)} | {_pct(r.vc)} |" for k, r in g.sort_values("n", ascending=False).iterrows()]
        lines.append("")
    op = trades[~trades["done"]] if len(trades) else trades
    if len(op):
        lines += ["## Open trades (marked, not scored)", "", "| Pattern | Symbol | Entry | Mark | Return | vs SPY |",
                  "|---|---|---|---|---:|---:|"]
        for _, r in op.sort_values(["entry_date", "pattern", "symbol"], na_position="last").iterrows():
            ent = r["entry_date"].date() if pd.notna(r["entry_date"]) else "next session"
            mk = r["mark_date"].date() if pd.notna(r.get("mark_date")) else "-"
            lines.append(f"| {r['pattern']} | {r['symbol']} | {ent} | {mk} | {_pct(r.get('ret'))} | "
                         f"{_pct(r.get('excess'))} |")
        lines.append("")
    lines += ["Costs: 15 bp per round trip on completed trades; open marks carry no cost. Entry at the next open, exit "
              "at the 10th session's close. Signals from 10-15 to 11-02 were lost in the 11-03 reset and are not "
              "included.", ""]
    return "\n".join(lines)


def score(journal: Path, out: Path, now=None) -> dict:
    sig = load_signals(journal)
    syms = set(sig["symbol"]) if len(sig) else set()
    opens, closes = load_prices(syms, now)
    trades = build_trades(sig, opens, closes) if len(sig) else pd.DataFrame(columns=["done", "missing"])
    first_entry = trades["entry_date"].dropna().min() if len(trades) else None
    ctrl = symbol_controls(opens, closes, first_entry) if first_entry is not None else {}
    ev = evaluate(trades, ctrl)
    asof = str(closes.index[-1].date())
    out.write_text(render(sig, trades, ev, asof))
    return ev


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["record", "score"])
    a = ap.parse_args(argv)
    journal = OUT / "signals.jsonl"
    if a.cmd == "record":
        added = record(journal, PAYLOADS)
        print(f"recorded sessions: {', '.join(added) if added else 'none new'}")
    else:
        ev = score(journal, OUT / "report.md")
        print(f"completed trades {ev['n_trades']}, open {ev['n_open']}, weeks {ev['weeks']}, "
              f"size ok {ev['size_ok']}, pass {ev['pass']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
