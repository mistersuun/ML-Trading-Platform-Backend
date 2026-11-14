"""Short-dated SPY option "lottery" accounts Y1-Y3 (rules frozen in docs/lottery-paper/PREREG.md). Paper only.

    .venv/bin/python scripts/lottery_paper.py plan [--asof YYYY-MM-DD]
    .venv/bin/python scripts/lottery_paper.py step --quotes docs/lottery-paper/quotes/YYYY-MM-DD.json [--asof ...]

`plan` reads SPY's finished daily bars and prints tonight's wanted contracts (expiry = next NYSE session, right and
strike per account). Their quotes are fetched read-only from IBKR and saved as
{"asof": "YYYY-MM-DD", "quotes": [{"contract_id", "right", "strike", "expiry", "bid", "ask"}]}. `step` first settles
open contracts that expired at or before the session at intrinsic value of SPY's close, then buys tonight's
contracts at the ask. A session can be stepped once; dates must increase.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Optional

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT = ROOT / "docs" / "lottery-paper"
START, COMMISSION, BUST = 1_000.0, 0.65, 100.0
ACCOUNTS = {
    "Y1": {"name": "calls every day", "direction": "call", "otm": 0.003, "stake": 0.20},
    "Y2": {"name": "follow the trend", "direction": "trend", "otm": 0.003, "stake": 0.20},
    "Y3": {"name": "YOLO roll", "direction": "trend", "otm": 0.005, "stake": 1.00},
}
TREND_N = 20
MIN_ONE = 0.40


# ------------------------------------------------------------------------------------------------ rules
def right_for(acct: dict, closes: pd.Series) -> str:
    if acct["direction"] == "call":
        return "C"
    sma = closes.iloc[-TREND_N:].mean()
    return "C" if closes.iloc[-1] >= sma else "P"


def strike_for(right: str, close: float, otm: float) -> float:
    return float(math.ceil(close * (1 + otm) - 1e-9)) if right == "C" else float(math.floor(close * (1 - otm) + 1e-9))


def intrinsic(right: str, strike: float, s: float) -> float:
    return max(0.0, s - strike) if right == "C" else max(0.0, strike - s)


def wanted(closes: pd.Series, next_session: pd.Timestamp, state: dict) -> dict[str, dict]:
    out = {}
    for k, a in ACCOUNTS.items():
        if state["accounts"][k]["busted"]:
            continue
        r = right_for(a, closes)
        out[k] = {"right": r, "strike": strike_for(r, float(closes.iloc[-1]), a["otm"]),
                  "expiry": str(next_session.date())}
    return out


# ------------------------------------------------------------------------------------------------ state
def new_state() -> dict:
    return {"inception": None, "last_asof": None, "bench": None,
            "accounts": {k: {"cash": START, "positions": [], "trades": [], "busted": False} for k in ACCOUNTS}}


def equity(acct: dict, spot: Optional[float] = None) -> float:
    """Cash plus open contracts at their entry cost (marks are not available between closes)."""
    return acct["cash"] + sum(p["qty"] * 100 * p["entry"] for p in acct["positions"])


def step(state: dict, asof: str, closes: pd.Series, next_session: pd.Timestamp, quotes: list[dict]) -> dict:
    if state["last_asof"] and asof <= state["last_asof"]:
        raise SystemExit(f"session {asof} is not after the last stepped session {state['last_asof']}")
    s = float(closes.loc[pd.Timestamp(asof)])
    events = {k: [] for k in ACCOUNTS}
    # 1. settle everything that expired at or before this session
    for k, acct in state["accounts"].items():
        keep = []
        for p in acct["positions"]:
            if p["expiry"] > asof:
                keep.append(p)
                continue
            px = closes.get(pd.Timestamp(p["expiry"]))
            if px is None or pd.isna(px):
                keep.append(p)
                events[k].append(f"cannot settle {p['label']}: no SPY close for {p['expiry']}")
                continue
            val = intrinsic(p["right"], p["strike"], float(px))
            fee = COMMISSION * p["qty"] if val > 0 else 0.0
            proceeds = val * 100 * p["qty"] - fee
            acct["cash"] += proceeds
            pnl = proceeds - p["cost"]
            acct["trades"].append({**p, "exit_date": p["expiry"], "settle": round(val, 4), "spy_close": float(px),
                                   "pnl": round(pnl, 2)})
            events[k].append(f"settle {p['label']} x{p['qty']} at {val:.2f} (SPY {float(px):.2f}): P&L {pnl:+.2f}")
        acct["positions"] = keep
        if not acct["busted"] and not acct["positions"] and acct["cash"] < BUST:
            acct["busted"] = True
            events[k].append(f"BUSTED: equity US${acct['cash']:.2f} is below US${BUST:.0f}")
    # 2. tonight's entries
    want = wanted(closes.loc[:pd.Timestamp(asof)], next_session, state)
    qmap = {(q["right"], float(q["strike"]), q["expiry"]): q for q in quotes}
    for k, w in want.items():
        acct = state["accounts"][k]
        q = qmap.get((w["right"], w["strike"], w["expiry"]))
        label = f"SPY {w['expiry']} {w['strike']:g}{w['right']}"
        if q is None or not q.get("ask") or float(q["ask"]) <= 0:
            events[k].append(f"no trade: no ask for {label}")
            continue
        ask = float(q["ask"])
        stake = ACCOUNTS[k]["stake"] * equity(acct)
        unit = ask * 100 + COMMISSION
        qty = math.floor(min(stake, acct["cash"]) / unit)
        if qty == 0 and unit <= MIN_ONE * equity(acct) and unit <= acct["cash"]:
            qty = 1                                            # one contract if it costs at most 40% of equity
        if qty < 1:
            events[k].append(f"no trade: {label} at {ask:.2f} does not fit US${stake:.2f}")
            continue
        cost = qty * (ask * 100 + COMMISSION)
        acct["cash"] -= cost
        acct["positions"].append({"contract_id": q.get("contract_id"), "label": label, "right": w["right"],
                                  "strike": w["strike"], "expiry": w["expiry"], "qty": qty, "entry": ask,
                                  "entry_date": asof, "spy_entry": s, "cost": round(cost, 2)})
        events[k].append(f"buy {label} x{qty} at {ask:.2f} (cost US${cost:.2f})")
    if state["inception"] is None:
        state["inception"], state["bench"] = asof, {"spy_start": s}
    state["last_asof"] = asof
    return {"asof": asof, "spy": s, "events": events,
            "accounts": {k: {"equity": round(equity(a), 2), "cash": round(a["cash"], 2), "open": len(a["positions"]),
                             "closed": len(a["trades"]), "busted": a["busted"]} for k, a in state["accounts"].items()},
            "bench_value": round(START * s / state["bench"]["spy_start"], 2)}


# ------------------------------------------------------------------------------------------------ io
def spy_closes(asof: Optional[str] = None) -> tuple[pd.Series, pd.Timestamp]:
    import external_bars
    from data.calendars import get_calendar
    cal = get_calendar("XNYS")
    c = external_bars.closed_bars("SPY", None, cal, "etf")["Close"].astype(float)
    if asof:
        c = c.loc[:pd.Timestamp(asof)]
    last = c.index[-1]
    nxt = [d for d in cal.sessions(last, last + pd.Timedelta(days=10)) if d > last][0]
    return c, pd.Timestamp(nxt)


def render(state: dict, row: dict) -> str:
    lines = ["# Short-dated SPY option accounts (Y1-Y3)", "",
             f"As of {row['asof']}. Paper only; rules frozen in `PREREG.md`. US$1,000 each from {state['inception']}. "
             f"SPY bought with US$1,000 at the same close: **US${row['bench_value']:,.2f}**.", "",
             "| Account | Equity | Trades | Wins | Best trade | Open | Status |", "|---|---:|---:|---:|---:|---:|---|"]
    for k, a in state["accounts"].items():
        t = a["trades"]
        wins = sum(1 for x in t if x["pnl"] > 0)
        best = max((x["pnl"] for x in t), default=None)
        lines.append(f"| {k} {ACCOUNTS[k]['name']} | US${row['accounts'][k]['equity']:,.2f} | {len(t)} | {wins} | "
                     f"{'-' if best is None else f'{best:+,.2f}'} | {len(a['positions'])} | "
                     f"{'BUSTED' if a['busted'] else 'live'} |")
    lines += ["", "Open contracts are carried at their entry cost until they settle at the next close.", "",
              "## Tonight", ""]
    for k, ev in row["events"].items():
        lines.append(f"- **{k}**: " + ("; ".join(ev) if ev else "nothing"))
    lines += ["", "## Closed trades", "", "| Account | Contract | Qty | Entry | Settle | SPY close | P&L |",
              "|---|---|---:|---:|---:|---:|---:|"]
    for k, a in state["accounts"].items():
        for x in a["trades"]:
            lines.append(f"| {k} | {x['label']} | {x['qty']} | {x['entry']:.2f} | {x['settle']:.2f} | "
                         f"{x['spy_close']:.2f} | {x['pnl']:+,.2f} |")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["plan", "step"])
    ap.add_argument("--quotes")
    ap.add_argument("--asof")
    a = ap.parse_args(argv)
    sp = OUT / "state.json"
    state = json.loads(sp.read_text()) if sp.exists() else new_state()
    closes, nxt = spy_closes(a.asof)
    asof = str(closes.index[-1].date())
    if a.cmd == "plan":
        due = [p["label"] for acc in state["accounts"].values() for p in acc["positions"] if p["expiry"] <= asof]
        print(json.dumps({"asof": asof, "spy_close": float(closes.iloc[-1]), "expiry": str(nxt.date()),
                          "settle_tonight": due, "wanted": wanted(closes, nxt, state)}, indent=2))
        return 0
    if not a.quotes:
        raise SystemExit("step needs --quotes")
    q = json.loads(Path(a.quotes).read_text())
    if q.get("asof") != asof:
        raise SystemExit(f"quotes are for {q.get('asof')}, the session is {asof}")
    row = step(state, asof, closes, nxt, q["quotes"])
    sp.write_text(json.dumps(state, indent=1))
    with (OUT / "journal.jsonl").open("a") as f:
        f.write(json.dumps(row) + "\n")
    (OUT / "report.md").write_text(render(state, row))
    print(json.dumps(row, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
