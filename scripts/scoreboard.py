"""Master scoreboard for all paper arms (pre-registered: docs/scoreboard/PREREG.md; ladder: docs/scoreboard/GRADUATION.md).

    .venv/bin/python scripts/scoreboard.py            -> docs/scoreboard/scoreboard.md
    .venv/bin/python scripts/scoreboard.py --today 2026-12-31

Paper only, offline: reads the committed journals under docs/ and SPY/QQQ closes from state/external_bars. It never
changes an arm's rules and never calls a broker. Outside the fixed look dates the report carries an "informational, not a
look" banner. Missing data is shown as n/a, never filled in.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import external_bars  # noqa: E402
import scoreboard as sb  # noqa: E402

DOCS = ROOT / "docs"
OUT = DOCS / "scoreboard" / "scoreboard.md"
QUANT = {"trend": "Quant: trend following", "momentum": "Quant: cross-sectional momentum",
         "lowvol": "Quant: low volatility", "high52": "Quant: 52-week-high proximity", "rsi2": "Quant: RSI-2 mean reversion"}
AGGRESSIVE = {"lev_trend": "Aggressive: 3x Nasdaq trend", "top1_mom": "Aggressive: all-in top momentum stock"}
OPTIONS = {"L": "Options Book L ($500 lottery)", "P": "Options Book P (SPY put writer)", "M": "Options Book M ($1,000 momentum calls)"}
BASKETS = {"appaloosa_13f": "Appaloosa", "berkshire_13f": "Berkshire", "consensus": "Consensus best ideas", "etfs": "ETF basket",
           "fairfax": "Fairfax (CAD)", "himalaya_13f": "Himalaya", "pershing_13f": "Pershing Square", "tci_13f": "TCI",
           "vehicles": "Vehicles", "altimeter_13f": "Altimeter", "fundsmith_13f": "Fundsmith", "punchcard_13f": "Punchcard",
           "sequoia_nav": "Sequoia", "qqq": "QQQ control", "spy": "SPY control", "activist_13d": "Activist 13D",
           "insider_cluster": "Insider cluster"}
CONTROLS = {"inv_spy", "inv_qqq", "ft_account"}   # shown, never promotable (PREREG)


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def last_per_date(rows: list[dict], key: str) -> dict[str, dict]:
    """Later rows win a repeated date (a journal that is appended to may carry a re-run)."""
    return {r[key]: r for r in rows if r.get(key)}


def series(values: dict, anchor: Optional[tuple] = None) -> pd.Series:
    s = pd.Series({pd.Timestamp(k): float(v) for k, v in values.items() if v is not None}, dtype=float).sort_index()
    if anchor is not None:
        s = pd.concat([pd.Series({pd.Timestamp(anchor[0]): float(anchor[1])}), s])
        s = s[~s.index.duplicated(keep="last")].sort_index()
    return s


def arm(aid, label, equity, bench, bench_name, start_value, trades=None, note="") -> dict:
    return {"id": aid, "label": label, "equity": equity, "bench": bench, "bench_name": bench_name,
            "start": start_value, "trades": trades, "note": note, "family": aid not in CONTROLS}


def load_closes(symbol: str, asof: str) -> pd.Series:
    now = datetime.fromisoformat(asof).replace(hour=23, minute=0, tzinfo=timezone.utc)
    try:
        df = external_bars.closed_bars(symbol, now=now)
    except Exception as e:            # no session calendar available: fall back to everything on file
        print(f"closed_bars failed for {symbol} ({type(e).__name__}); using load_bars", file=sys.stderr)
        df = external_bars.load_bars(symbol)
        df = df[df.index <= pd.Timestamp(asof)]
    return df["Close"].astype(float)


def arms_daytrade(rows) -> list[dict]:
    by = last_per_date(rows, "asof")
    out = []
    for book, aid, bname in (("QQQ", "dt_qqq", "QQQ"), ("SPY", "dt_spy", "SPY")):
        eq = {d: r["books"][book]["equity"] for d, r in by.items() if book in r.get("books", {})}
        bv = {d: r["books"][book]["bench_value"] for d, r in by.items() if book in r.get("books", {})}
        trades = sum(1 for r in by.values() if (r.get("books", {}).get(book, {}).get("result") or {}).get("trade"))
        out.append(arm(aid, f"ORB {book}", series(eq, ("2026-10-14", 25000.0)), series(bv, ("2026-10-14", 25000.0)),
                       f"{bname} buy-and-hold", 25000.0, trades))
    return out


def arms_gapgo(rows, closes) -> list[dict]:
    pnl = {r["date"]: r.get("pnl", 0.0) for r in rows if r.get("date")}
    eq, level = {}, 5000.0
    for d in sorted(pnl):
        level += pnl[d]
        eq[d] = round(level, 2)
    state = DOCS / "gapgo-paper" / "state.json"
    n_trades = len(json.loads(state.read_text()).get("trades", [])) if state.exists() else None
    return [arm("gapgo", "Gap-and-go", series(eq, ("2026-10-16", 5000.0)), closes["SPY"], "SPY", 5000.0, n_trades,
                "equity rebuilt from the journal's daily pnl")]


def arms_accounts(rows, mapping, prefix, anchor_date, closes) -> list[dict]:
    by = last_per_date(rows, "asof")
    out = []
    for key, label in mapping.items():
        eq = {d: r["accounts"][key]["equity"] for d, r in by.items() if key in r.get("accounts", {})}
        out.append(arm(f"{prefix}_{key}", label, series(eq, (anchor_date, 1000.0)), closes["SPY"], "SPY", 1000.0))
    return out


def arms_options(rows) -> list[dict]:
    by = last_per_date(rows, "asof")
    out = []
    for book, label in OPTIONS.items():
        have = {d: r["books"][book] for d, r in by.items() if book in r.get("books", {})}
        if not have:
            continue
        last = have[max(have)]
        first = have[min(have)]
        start = {"L": 500.0, "P": 100000.0, "M": 1000.0}[book]
        out.append(arm(f"opt_{book}", label, series({d: b["equity"] for d, b in have.items()}),
                       series({d: b["bench_value"] for d, b in have.items()}), f"{last.get('bench', first.get('bench'))} buy-and-hold",
                       start, int(last.get("closed", 0)), "daily window starts at the book's first journal row"))
    return out


def arms_forward_test(rows, closes) -> list[dict]:
    days = last_per_date([r for r in rows if r.get("type") == "day"], "date")
    eq = {d: r["account"]["equity"] for d, r in days.items() if r.get("account")}
    return [arm("ft_account", "Forward-test pipeline account (cash; 1-share plumbing test)", series(eq), closes["SPY"], "SPY",
                100000.0, None, "not a strategy")]


def arms_baskets(rows, closes, sessions) -> tuple[list[dict], list[str]]:
    if not rows:
        return [], []
    last = rows[-1]
    out, pending = [], []
    for key, p in last.get("portfolios", {}).items():
        label = BASKETS.get(key, key)
        rets = {d: v for d, v in (p.get("daily_returns") or {}).items() if v is not None}
        if not rets:
            pending.append(label)
            continue
        first = pd.Timestamp(min(rets))
        pos = sessions.searchsorted(first)
        anchor = sessions[pos - 1] if pos > 0 else first - pd.Timedelta(days=1)
        level, eq = 10000.0, {anchor: 10000.0}
        for d in sorted(rets):
            level *= 1 + rets[d]
            eq[pd.Timestamp(d)] = level
        note = "CAD priced, USD benchmark" if key == "fairfax" else ""
        out.append(arm(f"inv_{key.replace('_13f', '').replace('_nav', '')}", label + " basket", series(eq), closes["SPY"],
                       "SPY", 10000.0, None, note))
    return out, pending


def pct(x, nd=2) -> str:
    return "n/a" if x is None else f"{x * 100:+.{nd}f}%"


def bp(x) -> str:
    return "n/a" if x is None else f"{x * 1e4:+.1f}"


def num(x, nd=2) -> str:
    return "n/a" if x is None else f"{x:.{nd}f}"


def share(x) -> str:
    return "n/a" if x is None else f"{x * 100:.0f}%"


def ci(lo, hi) -> str:
    return "n/a" if lo is None else f"[{lo * 1e4:+.1f}, {hi * 1e4:+.1f}]"


def build(today: str, asof_limit: Optional[str] = None) -> dict:
    closes = {s: load_closes(s, asof_limit or today) for s in ("SPY", "QQQ")}
    sessions = closes["SPY"].index
    inv = read_jsonl(DOCS / "forward-test" / "2026-10" / "investor-tracking.jsonl")
    baskets, pending = arms_baskets(inv, closes, sessions)
    arms = (arms_daytrade(read_jsonl(DOCS / "daytrade-paper" / "journal.jsonl"))
            + arms_gapgo(read_jsonl(DOCS / "gapgo-paper" / "journal.jsonl"), closes)
            + arms_accounts(read_jsonl(DOCS / "quant-accounts" / "journal.jsonl"), QUANT, "q", "2026-10-14", closes)
            + arms_accounts(read_jsonl(DOCS / "aggressive-accounts" / "journal.jsonl"), AGGRESSIVE, "ag", "2026-10-15", closes)
            + arms_options(read_jsonl(DOCS / "options-paper" / "journal.jsonl"))
            + arms_forward_test(read_jsonl(DOCS / "forward-test" / "2026-10" / "journal.jsonl"), closes)
            + baskets)
    for a in arms:
        a["ret"] = sb.build_returns(a["equity"], a["bench"], sessions)
        a["stats"] = sb.arm_stats(a["ret"], a["start"], a["equity"], a["bench"])
    family = [a for a in arms if a["family"] and a["stats"]["p"] is not None]
    groups = sb.clusters({a["id"]: a["ret"]["arm"].where(~a["ret"]["gap"]) for a in family})
    adj = sb.family_test({a["id"]: a["stats"]["p"] for a in family}, groups)
    for a in arms:
        a["adj_p"] = adj.get(a["id"])
        a["rule"] = sb.promotion(a["stats"], a["adj_p"], a["trades"]) if a["family"] else None
    return {"arms": arms, "groups": groups, "pending": pending, "closes": closes}


def render(res: dict, today: str) -> str:
    arms, groups = res["arms"], res["groups"]
    ls = sb.look_status(today)
    if ls["is_look"]:
        banner = f"**LOOK {ls['number']} of {len(sb.LOOKS)} ({today}).** The decision column applies the frozen rule."
    else:
        nxt = ls["next"] or "none left"
        banner = (f"**Informational, not a look ({today}).** Next look: {nxt}. Looks are fixed ({', '.join(sb.LOOKS)}); "
                  "nothing below promotes an arm.")
    asof = max((a["stats"]["last"] for a in arms if a["stats"]["last"] is not None), default=None)
    L = [f"# Master scoreboard (data to {asof.date() if asof is not None else 'n/a'}, built {today})", "",
         banner, "",
         "Paper only. Rules in `PREREG.md` (frozen 2026-11-12), ladder in `GRADUATION.md`. Daily excess return over each arm's "
         "pre-named benchmark, over the arm's own window. No arm's rules are ever changed by this page.", "",
         "## Returns", "",
         "| Arm | Benchmark | Window | Days (gap) | Total | Bench | Excess |", "|---|---|---|---:|---:|---:|---:|"]
    for a in arms:
        s = a["stats"]
        win = "n/a" if s["first"] is None else f"{s['first'].date()} to {s['last'].date()}"
        L.append(f"| {a['label']} (`{a['id']}`) | {a['bench_name']} | {win} | {s['n_days']} ({s['n_gap']}) | "
                 f"{pct(s['total'])} | {pct(s['bench_total'])} | {pct(s['excess_total'])} |")
    for name in res["pending"]:
        L.append(f"| {name} | n/a | n/a | 0 (0) | n/a | n/a | n/a |")
    L += ["", "Gap = a return taken between journal dates that are not consecutive sessions (a missing day); it is kept and flagged.", "",
          "## Statistics (daily, basis points)", "",
          "| Arm | Mean excess/day | 95% CI (block bootstrap) | Ex-top-2 mean | Ex-top-2 CI | Beta | Alpha/day | NW t | Eff. days | Top-2 days' share of P&L |",
          "|---|---:|---|---:|---|---:|---:|---:|---:|---:|"]
    for a in arms:
        s = a["stats"]
        L.append(f"| `{a['id']}` | {bp(s['mean'])} | {ci(s['ci_lo'], s['ci_hi'])} | {bp(s['mean_x2'])} | {ci(s['ci_lo_x2'], s['ci_hi_x2'])} | "
                 f"{num(s['beta'])} | {bp(s['alpha'])} | {num(s['t_alpha'])} | {num(s['n_eff'], 1)} | "
                 f"{share(s['top2_share'])} |")
    L += ["", f"Bootstrap: circular blocks of {sb.BLOCK} days, {sb.N_BOOT:,} resamples, seed {sb.SEED}. Fewer than {sb.MIN_DAYS} days: n/a. "
          "Share of P&L is n/a unless total P&L is positive.", "",
          "## Family test and rule checks", "",
          f"Holm across clusters, alpha {sb.ALPHA_FAMILY} / {len(sb.LOOKS)} = {sb.ALPHA_LOOK:.4f} per look. "
          f"Size = independent days (or trades for trade-based arms), needs {sb.MIN_INDEPENDENT}.", "",
          "| Arm | Holm-adj p (cluster) | p < alpha | Ex-top-2 CI > 0 | Size | Size ok | " + ("Promoted |" if ls["is_look"] else "All three today (informational) |"),
          "|---|---:|:---:|:---:|---:|:---:|:---:|"]
    yn = lambda b: "yes" if b else "no"  # noqa: E731
    for a in arms:
        r = a["rule"]
        if r is None:
            L.append(f"| `{a['id']}` | not in family | | | | | |")
            continue
        size = "n/a" if r["size"] is None else (f"{r['size']} trade(s)" if a["trades"] is not None else f"{r['size']:.1f} days")
        L.append(f"| `{a['id']}` | {num(a['adj_p'], 3)} | {yn(r['p_ok'])} | {yn(r['top2_ok'])} | {size} | {yn(r['size_ok'])} | {yn(r['promoted'])} |")
    L += ["", "Not in the family test: `inv_spy`, `inv_qqq` (controls) and `ft_account` (a cash account). Arms without data are untested (n/a).", "",
          "## Clusters (daily returns correlated above 0.7 count as one bet)", ""]
    multi = [g for g in groups if len(g) > 1]
    if multi:
        for g in multi:
            L.append("- " + ", ".join(f"`{k}`" for k in g))
    else:
        L.append("- none: every arm is its own cluster")
    L.append(f"- {len(groups)} clusters from {sum(len(g) for g in groups)} testable arms")
    notes = [f"`{a['id']}`: {a['note']}" for a in arms if a["note"]]
    L += ["", "## Notes", ""] + [f"- {n}" for n in notes]
    L += ["- Quant and aggressive accounts fill at the next open while the benchmark runs close to close; their first day is approximate.",
          "- Basket and control arms use SPY, from each basket's own first return day. Definition-only baskets have no returns yet."]
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--today", default=date.today().isoformat(), help="date of this report (decides the look banner)")
    ap.add_argument("--asof", default=None, help="last market date to use (default: --today)")
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args(argv)
    res = build(a.today, a.asof)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(res, a.today))
    print(f"wrote {out} ({len(res['arms'])} arms)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
