"""Next-day direction calls (rules frozen in docs/direction-calls/PREREG.md). Paper only.

    .venv/bin/python scripts/direction_calls.py template            # skeleton for tonight's calls (prints JSON)
    .venv/bin/python scripts/direction_calls.py check FILE          # validate a calls file before committing it
    .venv/bin/python scripts/direction_calls.py score               # rebuild docs/direction-calls/report.md

A calls file is {"asof": "YYYY-MM-DD", "target": "YYYY-MM-DD", "made_at": ISO-8601 UTC,
"calls": [{"symbol", "direction": "up"|"down", "confidence": 0.50..1.00, "reason"}]} with every UNIVERSE name.
Late files (made_at at or after the target session's open) are excluded from scoring.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT = ROOT / "docs" / "direction-calls"
UNIVERSE = ("SPY", "QQQ", "NVDA", "AAPL", "MSFT", "AMZN", "META", "TSLA", "GOOGL", "AMD")
ETFS = {"SPY", "QQQ"}
LOOKS = ("2026-12-31", "2027-02-26", "2027-04-15")
MIN_CALLS, MIN_NIGHTS, MIN_ACC, ALPHA = 100, 30, 0.60, 0.01


def validate(d: dict) -> list[str]:
    errs = []
    for k in ("asof", "target", "made_at", "calls"):
        if k not in d:
            errs.append(f"missing {k}")
    if errs:
        return errs
    if d["target"] <= d["asof"]:
        errs.append("target must be after asof")
    syms = [c.get("symbol") for c in d["calls"]]
    if sorted(syms) != sorted(UNIVERSE):
        errs.append(f"calls must cover exactly {', '.join(UNIVERSE)} once each")
    for c in d["calls"]:
        if c.get("direction") not in ("up", "down"):
            errs.append(f"{c.get('symbol')}: direction must be up or down")
        conf = c.get("confidence")
        if not isinstance(conf, (int, float)) or not 0.5 <= conf <= 1.0:
            errs.append(f"{c.get('symbol')}: confidence must be within 0.50..1.00")
        if not str(c.get("reason", "")).strip():
            errs.append(f"{c.get('symbol')}: reason is required")
    return errs


def is_late(made_at: str, target: str) -> bool:
    """True if made at or after 09:30 New York on the target session."""
    t = pd.Timestamp(made_at)
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    open_ny = pd.Timestamp(f"{target} 09:30", tz="America/New_York").tz_convert("UTC")
    return t >= open_ny


def binom_p(k: int, n: int) -> float:
    """One-sided P(X >= k) for X ~ Binomial(n, 0.5)."""
    if n == 0:
        return float("nan")
    return float(sum(math.comb(n, i) for i in range(k, n + 1)) / 2 ** n)


def score_rows(files: list[dict], closes: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for d in files:
        late = is_late(d["made_at"], d["target"])
        a, t = pd.Timestamp(d["asof"]), pd.Timestamp(d["target"])
        for c in d["calls"]:
            s = c["symbol"]
            row = {"asof": d["asof"], "target": d["target"], "symbol": s, "direction": c["direction"],
                   "confidence": float(c["confidence"]), "late": late, "scored": False}
            if s in closes and a in closes.index and t in closes.index:
                c0, c1 = closes.at[a, s], closes.at[t, s]
                ci = closes.index.get_loc(a)
                prev = closes[s].iloc[ci - 1] if ci > 0 else float("nan")
                if pd.notna(c0) and pd.notna(c1) and c1 != c0 and not late:
                    out = "up" if c1 > c0 else "down"
                    row.update(scored=True, outcome=out, correct=c["direction"] == out,
                               momentum_correct=(("up" if c0 > prev else "down") == out) if pd.notna(prev) else None,
                               up_correct=out == "up", ret=c1 / c0 - 1)
            rows.append(row)
    return pd.DataFrame(rows)


def summarize(df: pd.DataFrame) -> dict:
    s = df[df["scored"]] if len(df) else df
    n = len(s)
    if n == 0:
        return {"n": 0, "nights": 0}
    k = int(s["correct"].sum())
    p_conf = s["confidence"]
    brier = float(((p_conf - s["correct"].astype(float)) ** 2).mean())
    nights = s.groupby("asof").apply(lambda g: ((g["direction"] == "up").mean() > 0.5) == ((g["outcome"] == "up").mean() > 0.5),
                                     include_groups=False)
    bands = {}
    for lo, hi, name in ((0.5, 0.6, "0.50-0.59"), (0.6, 0.7, "0.60-0.69"), (0.7, 1.01, "0.70+")):
        b = s[(s["confidence"] >= lo) & (s["confidence"] < hi)]
        bands[name] = (len(b), float(b["correct"].mean()) if len(b) else float("nan"))
    mom = s["momentum_correct"].dropna()
    res = {"n": n, "k": k, "acc": k / n, "p": binom_p(k, n), "brier": brier, "nights": int(nights.size),
           "night_acc": float(nights.mean()), "momentum_acc": float(mom.mean()) if len(mom) else float("nan"),
           "up_acc": float(s["up_correct"].mean()), "bands": bands,
           "per_symbol": s.groupby("symbol")["correct"].agg(["size", "mean"]).to_dict("index")}
    res["pass"] = bool(n >= MIN_CALLS and res["nights"] >= MIN_NIGHTS and res["acc"] >= MIN_ACC and res["p"] < ALPHA
                       and res["acc"] > res["momentum_acc"] and res["acc"] > res["up_acc"] and res["night_acc"] > 0.5)
    return res


def render(res: dict, df: pd.DataFrame, asof: str) -> str:
    look = asof in LOOKS
    head = ["# Next-day direction calls", "",
            f"As of {asof}. Rules frozen in `PREREG.md`. Paper only. "
            + ("**This is a pre-registered look.**" if look else f"Informational; only the looks on {', '.join(LOOKS)} decide."), ""]
    if res["n"] == 0:
        pending = int((~df["scored"]).sum()) if len(df) else 0
        return "\n".join(head + [f"No scored calls yet ({pending} pending).", ""])
    f = lambda x: "n/a" if x != x else f"{x:.1%}"  # noqa: E731
    lines = head + [
        "| Measure | Value |", "|---|---:|",
        f"| Scored calls | {res['n']} over {res['nights']} nights |",
        f"| Accuracy | **{f(res['acc'])}** ({res['k']}/{res['n']}) |",
        f"| One-sided p vs 50% | {res['p']:.3f} |",
        f"| Night-level accuracy (majority call) | {f(res['night_acc'])} |",
        f"| Momentum baseline | {f(res['momentum_acc'])} |",
        f"| Always-up baseline | {f(res['up_acc'])} |",
        f"| Brier score (0.25 = coin) | {res['brier']:.3f} |", "",
        f"Pass rule: at least {MIN_CALLS} calls and {MIN_NIGHTS} nights, accuracy at least {MIN_ACC:.0%}, p < {ALPHA}, "
        "above both baselines, and night-level accuracy above 50%. "
        + (f"**Status at this look: {'PASS' if res['pass'] else 'FAIL'}.**" if look else ""), "",
        "## By confidence", "", "| Confidence | Calls | Accuracy |", "|---|---:|---:|",
        *[f"| {k} | {v[0]} | {f(v[1])} |" for k, v in res["bands"].items()], "",
        "## By name", "", "| Name | Calls | Accuracy |", "|---|---:|---:|",
        *[f"| {k} | {v['size']} | {f(v['mean'])} |" for k, v in sorted(res["per_symbol"].items())], ""]
    late = int(df["late"].sum())
    if late:
        lines.append(f"{late} call(s) were late (made after the target open) and are excluded.\n")
    return "\n".join(lines)


def load_closes(now=None) -> pd.DataFrame:
    import external_bars
    from data.calendars import get_calendar
    cal = get_calendar("XNYS")
    return pd.DataFrame({s: external_bars.closed_bars(s, now, cal, "etf" if s in ETFS else "equity")["Close"]
                         .astype(float) for s in UNIVERSE})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["template", "check", "score"])
    ap.add_argument("file", nargs="?")
    a = ap.parse_args(argv)
    if a.cmd == "template":
        from data.calendars import get_calendar
        closes = load_closes()
        last = closes.index[-1]
        cal = get_calendar("XNYS")
        nxt = [x for x in cal.sessions(last, last + pd.Timedelta(days=10)) if x > last][0]
        print(json.dumps({"asof": str(last.date()), "target": str(pd.Timestamp(nxt).date()),
                          "made_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                          "calls": [{"symbol": s, "direction": "", "confidence": 0.5, "reason": ""} for s in UNIVERSE]},
                         indent=1))
        return 0
    if a.cmd == "check":
        d = json.loads(Path(a.file).read_text())
        errs = validate(d)
        if not errs and is_late(d["made_at"], d["target"]):
            errs.append("made_at is at or after the target session's open: this file would be late")
        print("ok" if not errs else "\n".join(errs))
        return 0 if not errs else 1
    files = [json.loads(p.read_text()) for p in sorted((OUT / "calls").glob("*.json"))]
    closes = load_closes()
    df = score_rows(files, closes)
    res = summarize(df)
    (OUT / "report.md").write_text(render(res, df, str(closes.index[-1].date())))
    print({k: v for k, v in res.items() if k not in ("bands", "per_symbol")})
    return 0


if __name__ == "__main__":
    sys.exit(main())
