"""Y5 "Manager's book": discretionary, research-desk options paper account (docs/manager-book/PREREG.md). Paper only.

    .venv/bin/python scripts/manager_book.py status
    .venv/bin/python scripts/manager_book.py step --decisions docs/manager-book/decisions/YYYY-MM-DD.json \
                                                  --quotes docs/manager-book/quotes/YYYY-MM-DD.json

decisions = {"asof", "made_at", "research": {...free text...}, "actions": [
    {"action": "buy", "underlying", "right": "C"|"P", "strike", "expiry", "qty", "take_profit": price|null,
     "stop": price|null, "thesis"},
    {"action": "sell", "contract_id", "reason"},
    {"action": "set", "contract_id", "take_profit": price|null, "stop": price|null}]}   (actions may be empty)
quotes = {"asof", "quotes": [{"contract_id", "underlying", "right", "strike", "expiry", "bid", "ask",
                              "session_high": price|null}]}   (session_high: the option's intraday high that session)
Order of a step: expiries, resting take-profit orders (session_high), nightly stops, sells, sets, then buys.
The risk rules are enforced here; a violating buy is rejected and logged, never resized.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT = ROOT / "docs" / "manager-book"
UNIVERSE = ("SPY", "QQQ", "NVDA", "AAPL", "MSFT", "AMZN", "META", "TSLA", "GOOGL", "AMD")
ETFS = {"SPY", "QQQ"}
START, COMMISSION = 1_000.0, 0.65
MAX_TRADE, MAX_ONE, MAX_OPEN_COST, MAX_POS, MAX_NEW, HALT, MAX_DTE = 0.20, 0.30, 0.50, 3, 2, 400.0, 45


def new_state() -> dict:
    return {"inception": None, "last_asof": None, "spy_start": None, "cash": START, "positions": [], "closed": []}


def _label(p: dict) -> str:
    return f"{p['underlying']} {p['expiry']} {p['strike']:g}{p['right']}"


def equity(state: dict, qmap: Optional[dict] = None) -> float:
    """Cash plus open positions at the closing bid (or the last mark when no quote is given)."""
    eq = state["cash"]
    for p in state["positions"]:
        q = (qmap or {}).get(p["contract_id"])
        mark = float(q["bid"]) if q and q.get("bid") is not None else p.get("mark", p["entry"])
        eq += mark * 100 * p["qty"]
    return eq


def _close(state: dict, p: dict, price: float, asof: str, reason: str, fee: bool = True) -> str:
    proceeds = price * 100 * p["qty"] - (COMMISSION * p["qty"] if fee else 0.0)
    state["cash"] += proceeds
    pnl = proceeds - p["cost"]
    state["closed"].append({**p, "exit": round(price, 4), "exit_date": asof, "reason": reason, "pnl": round(pnl, 2)})
    state["positions"] = [x for x in state["positions"] if x is not p]
    return f"close {_label(p)} x{p['qty']} at {price:.2f} ({reason}): P&L {pnl:+.2f}"


def step(state: dict, decisions: dict, quotes: list[dict], closes: dict[str, pd.Series]) -> dict:
    asof = decisions["asof"]
    if state["last_asof"] and asof <= state["last_asof"]:
        raise SystemExit(f"session {asof} is not after the last stepped session {state['last_asof']}")
    qmap = {q["contract_id"]: q for q in quotes if q.get("contract_id") is not None}
    ev: list[str] = []
    # 1. expiries
    for p in list(state["positions"]):
        if p["expiry"] <= asof:
            px = closes.get(p["underlying"], pd.Series(dtype=float)).get(pd.Timestamp(p["expiry"]))
            if px is None or pd.isna(px):
                ev.append(f"cannot settle {_label(p)}: no close for {p['expiry']}")
                continue
            val = max(0.0, float(px) - p["strike"]) if p["right"] == "C" else max(0.0, p["strike"] - float(px))
            ev.append(_close(state, p, val, p["expiry"], "expiry", fee=val > 0))
    # 2. resting take-profit orders, 3. nightly stops (positions opened before tonight only)
    for p in list(state["positions"]):
        q = qmap.get(p["contract_id"])
        if p["entry_date"] >= asof or q is None:
            continue
        hi = q.get("session_high")
        if p.get("take_profit") and hi is not None and float(hi) >= p["take_profit"]:
            ev.append(_close(state, p, p["take_profit"], asof, "take-profit order"))
        elif p.get("stop") and q.get("bid") is not None and float(q["bid"]) <= p["stop"]:
            ev.append(_close(state, p, float(q["bid"]), asof, "stop"))
    # 4. manager sells and order changes
    for a in decisions.get("actions", []):
        p = next((x for x in state["positions"] if x["contract_id"] == a.get("contract_id")), None)
        if a["action"] == "sell":
            q = qmap.get(a["contract_id"])
            if p is None or q is None or q.get("bid") is None:
                ev.append(f"sell rejected: no open position or no bid for contract {a.get('contract_id')}")
            else:
                ev.append(_close(state, p, float(q["bid"]), asof, a.get("reason", "manager")))
        elif a["action"] == "set" and p is not None:
            p["take_profit"], p["stop"] = a.get("take_profit"), a.get("stop")
            ev.append(f"orders on {_label(p)}: take-profit {p['take_profit']}, stop {p['stop']}")
    # 5. buys, with the risk rules
    new = 0
    for a in decisions.get("actions", []):
        if a["action"] != "buy":
            continue
        label = f"{a['underlying']} {a['expiry']} {float(a['strike']):g}{a['right']}"
        q = next((x for x in quotes if x.get("underlying") == a["underlying"] and x.get("right") == a["right"]
                  and float(x["strike"]) == float(a["strike"]) and x.get("expiry") == a["expiry"]), None)
        eq = equity(state, qmap)
        dte = (pd.Timestamp(a["expiry"]) - pd.Timestamp(asof)).days
        why = None
        if a["underlying"] not in UNIVERSE:
            why = "underlying not in the universe"
        elif a["right"] not in ("C", "P") or int(a.get("qty", 0)) < 1:
            why = "long calls or puts only, at least 1 contract"
        elif not 1 <= dte <= MAX_DTE:
            why = f"expiry must be 1 to {MAX_DTE} days out"
        elif q is None or not q.get("ask") or float(q["ask"]) <= 0:
            why = "no ask"
        elif eq < HALT:
            why = f"halted: equity below US${HALT:.0f}"
        elif new >= MAX_NEW:
            why = f"at most {MAX_NEW} new trades a night"
        elif len(state["positions"]) >= MAX_POS:
            why = f"at most {MAX_POS} open positions"
        elif any(p["underlying"] == a["underlying"] for p in state["positions"]):
            why = "one position per underlying (no averaging down)"
        if why is None:
            qty, ask = int(a["qty"]), float(q["ask"])
            cost = qty * (ask * 100 + COMMISSION)
            open_cost = sum(p["cost"] for p in state["positions"])
            if cost > MAX_TRADE * eq and not (qty == 1 and cost <= MAX_ONE * eq):
                why = f"cost US${cost:.2f} is above {MAX_TRADE:.0%} of equity (US${eq:.2f})"
            elif open_cost + cost > MAX_OPEN_COST * eq:
                why = f"open cost would be US${open_cost + cost:.2f}, above {MAX_OPEN_COST:.0%} of equity"
            elif cost > state["cash"]:
                why = "not enough cash"
        if why:
            ev.append(f"buy {label} rejected: {why}")
            continue
        state["cash"] -= cost
        state["positions"].append({"contract_id": q.get("contract_id"), "underlying": a["underlying"],
                                   "right": a["right"], "strike": float(a["strike"]), "expiry": a["expiry"],
                                   "qty": qty, "entry": ask, "entry_date": asof, "cost": round(cost, 2),
                                   "take_profit": a.get("take_profit"), "stop": a.get("stop"),
                                   "thesis": a.get("thesis", ""), "mark": float(q["bid"] or 0)})
        new += 1
        ev.append(f"buy {label} x{qty} at {ask:.2f} (US${cost:.2f}); take-profit {a.get('take_profit')}, "
                  f"stop {a.get('stop')}")
    for p in state["positions"]:
        q = qmap.get(p["contract_id"])
        if q and q.get("bid") is not None:
            p["mark"] = float(q["bid"])
    spy = float(closes["SPY"].loc[pd.Timestamp(asof)])
    if state["inception"] is None:
        state["inception"], state["spy_start"] = asof, spy
    state["last_asof"] = asof
    if not decisions.get("actions"):
        ev.append("manager: no trade tonight")
    return {"asof": asof, "events": ev, "equity": round(equity(state), 2), "cash": round(state["cash"], 2),
            "open": len(state["positions"]), "closed": len(state["closed"]),
            "bench_value": round(START * spy / state["spy_start"], 2)}


def render(state: dict, row: dict, y2_equity: Optional[float]) -> str:
    c = state["closed"]
    wins = [x["pnl"] for x in c if x["pnl"] > 0]
    losses = [x["pnl"] for x in c if x["pnl"] <= 0]
    lines = ["# Y5 Manager's book", "",
             f"As of {row['asof']}. Paper only; rules frozen in `PREREG.md`. US$1,000 from {state['inception']}.", "",
             "| | Value |", "|---|---:|",
             f"| Equity | **US${row['equity']:,.2f}** |",
             f"| US$1,000 in SPY | US${row['bench_value']:,.2f} |",
             f"| Y2 mechanical trend account (its own US$1,000, from 2026-11-13) | "
             f"{'n/a' if y2_equity is None else f'US${y2_equity:,.2f}'} |",
             f"| Closed trades | {len(c)} ({len(wins)} wins) |",
             f"| Average win / average loss | {(sum(wins) / len(wins)) if wins else 0:+,.2f} / "
             f"{(sum(losses) / len(losses)) if losses else 0:+,.2f} |", "",
             "## Tonight", "", *[f"- {e}" for e in row["events"]], "", "## Open positions", "",
             "| Contract | Qty | Entry | Mark | Take-profit | Stop | Thesis |", "|---|---:|---:|---:|---:|---:|---|"]
    for p in state["positions"]:
        lines.append(f"| {_label(p)} | {p['qty']} | {p['entry']:.2f} | {p.get('mark', 0):.2f} | "
                     f"{p.get('take_profit') or '-'} | {p.get('stop') or '-'} | {p.get('thesis', '')} |")
    lines += ["", "## Closed trades", "", "| Contract | Qty | Entry | Exit | Reason | P&L | Thesis |",
              "|---|---:|---:|---:|---|---:|---|"]
    for x in c:
        lines.append(f"| {_label(x)} | {x['qty']} | {x['entry']:.2f} | {x['exit']:.2f} | {x['reason']} | "
                     f"{x['pnl']:+,.2f} | {x.get('thesis', '')} |")
    return "\n".join(lines) + "\n"


def load_closes() -> dict[str, pd.Series]:
    import external_bars
    from data.calendars import get_calendar
    cal = get_calendar("XNYS")
    return {s: external_bars.closed_bars(s, None, cal, "etf" if s in ETFS else "equity")["Close"].astype(float)
            for s in UNIVERSE}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["status", "step"])
    ap.add_argument("--decisions")
    ap.add_argument("--quotes")
    a = ap.parse_args(argv)
    sp = OUT / "state.json"
    state = json.loads(sp.read_text()) if sp.exists() else new_state()
    if a.cmd == "status":
        print(json.dumps({"last_asof": state["last_asof"], "cash": round(state["cash"], 2),
                          "equity_at_last_marks": round(equity(state), 2),
                          "limits": {"max_trade": round(MAX_TRADE * equity(state), 2),
                                     "max_one_contract": round(MAX_ONE * equity(state), 2),
                                     "max_open_cost": round(MAX_OPEN_COST * equity(state), 2)},
                          "positions": state["positions"]}, indent=1))
        return 0
    d = json.loads(Path(a.decisions).read_text())
    q = json.loads(Path(a.quotes).read_text()) if a.quotes else {"asof": d["asof"], "quotes": []}
    if q.get("asof") != d["asof"]:
        raise SystemExit(f"quotes are for {q.get('asof')}, the decisions for {d['asof']}")
    closes = load_closes()
    if pd.Timestamp(d["asof"]) != closes["SPY"].index[-1]:
        raise SystemExit(f"decisions are for {d['asof']}, the last closed session is {closes['SPY'].index[-1].date()}")
    row = step(state, d, q["quotes"], closes)
    sp.write_text(json.dumps(state, indent=1))
    with (OUT / "journal.jsonl").open("a") as f:
        f.write(json.dumps(row) + "\n")
    y2 = None
    lj = ROOT / "docs" / "lottery-paper" / "journal.jsonl"
    if lj.exists():
        y2 = json.loads(lj.read_text().splitlines()[-1])["accounts"]["Y2"]["equity"]
    (OUT / "report.md").write_text(render(state, row, y2))
    print(json.dumps(row, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
