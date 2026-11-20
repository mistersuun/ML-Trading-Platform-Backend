"""Y6 "Put sprint" (rules frozen in docs/sprint-paper/PREREG.md). Paper only: no orders anywhere.

    .venv/bin/python scripts/sprint_paper.py plan --spot 715.8 --open 716.2 --prev-close 715.87 --ts 2026-11-20T10:05
    .venv/bin/python scripts/sprint_paper.py check docs/sprint-paper/checks/2026-11-20T1005.json

`plan` prints which quotes this check needs (the open position's contract and, if an entry is possible, today's put
at the wanted strike). The check file is
{"ts": "YYYY-MM-DDTHH:MM" (New York), "asof": "HH:MM:SS", "final": bool, "spy": {"last", "open", "prior_close"},
 "quotes": [{"contract_id", "right": "P", "strike", "expiry", "bid", "ask"}]}.
`check` applies the rules in order: stale position, take-profit / stop / final exit, then an entry.
Option quotes are 15 minutes delayed, so `spy.last` is SPY at the quotes' own time and `asof` records that time
(amendment 2026-11-20 in the PREREG).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "sprint-paper"
START, TARGET, BUST = 1_000.0, 200_000.0, 100.0
OTM, STAKE, ONE_MAX, TP, STOP, COMMISSION = 0.005, 0.50, 0.75, 2.0, 0.5, 0.65
MAX_ENTRIES, LAST_ENTRY_HOUR = 2, 14
OTM_MAX, UNLISTED_FROM = 0.01, "2026-11-23"      # amendment 2 (2026-11-20): unlisted strikes


def new_state() -> dict:
    return {"cash": START, "position": None, "trades": [], "day": {"date": None, "entries": 0, "stopped": False},
            "status": "live", "last_ts": None, "inception": None, "spy_start": None}


def strike_for(spot: float) -> float:
    return float(math.floor(spot * (1 - OTM) + 1e-9))


def entry_signal(spy: dict) -> bool:
    return spy["last"] < spy["prior_close"] and spy["last"] < spy["open"]


def can_enter(state: dict, ts: str, final: bool) -> bool:
    d = state["day"]
    return (state["status"] == "live" and state["position"] is None and not final and d["entries"] < MAX_ENTRIES
            and not d["stopped"] and int(ts[11:13]) <= LAST_ENTRY_HOUR)


def _roll_day(state: dict, date: str, ev: list) -> None:
    if state["day"]["date"] == date:
        return
    p = state["position"]
    if p is not None:                                  # the final check was missed: recorded as a total loss
        state["trades"].append({**p, "exit": 0.0, "exit_ts": date, "why": "final check missed", "pnl": -p["cost"]})
        ev.append(f"{p['label']} x{p['qty']}: final check missed, recorded as a total loss ({-p['cost']:+.2f})")
        state["position"] = None
    state["day"] = {"date": date, "entries": 0, "stopped": False}


def _sell(state: dict, price: float, ts: str, why: str, asof: str | None = None) -> str:
    p = state["position"]
    proceeds = price * 100 * p["qty"] - (COMMISSION * p["qty"] if price > 0 else 0.0)
    state["cash"] += proceeds
    pnl = proceeds - p["cost"]
    state["trades"].append({**p, "exit": price, "exit_ts": ts, "exit_asof": asof, "why": why, "pnl": round(pnl, 2)})
    state["position"] = None
    return f"sell {p['label']} x{p['qty']} at {price:.2f} ({why}): P&L {pnl:+.2f}"


def check(state: dict, snap: dict) -> dict:
    ts, final, spy = snap["ts"], bool(snap.get("final")), snap["spy"]
    asof = snap.get("asof")                            # time of the delayed quotes (amendment 2026-11-20)
    if state["last_ts"] and ts <= state["last_ts"]:
        raise SystemExit(f"check {ts} is not after the last check {state['last_ts']}")
    ev: list[str] = []
    if state["status"] != "live":
        return {"ts": ts, "events": [f"account is {state['status']}: nothing to do"], "equity": round(state["cash"], 2),
                "status": state["status"]}
    date = ts[:10]
    _roll_day(state, date, ev)
    qmap = {q["contract_id"]: q for q in snap.get("quotes", []) if q.get("contract_id") is not None}
    if state["inception"] is None:
        state["inception"], state["spy_start"] = ts, spy["last"]
    p = state["position"]
    if p is not None:
        q = qmap.get(p["contract_id"])
        bid = float(q["bid"]) if q and q.get("bid") is not None else None
        if bid is not None and state["cash"] + bid * 100 * p["qty"] >= TARGET:
            ev.append(_sell(state, bid, ts, "target reached", asof))
            state["status"] = "target"
        elif final:
            ev.append(_sell(state, bid or 0.0, ts, "final check", asof))
        elif bid is None:
            ev.append(f"no bid for {p['label']}: held")
        elif bid >= TP * p["entry"]:
            ev.append(_sell(state, bid, ts, "take profit", asof))
        elif bid <= STOP * p["entry"]:
            ev.append(_sell(state, bid, ts, "stop", asof))
            state["day"]["stopped"] = True
        else:
            p["mark"] = bid
            ev.append(f"hold {p['label']} x{p['qty']}: bid {bid:.2f} vs entry {p['entry']:.2f}")
    if can_enter(state, ts, final):
        if not entry_signal(spy):
            ev.append(f"no entry: SPY {spy['last']:.2f} is not below both the prior close {spy['prior_close']:.2f} "
                      f"and the open {spy['open']:.2f}")
        else:
            k = strike_for(spy["last"])
            puts = [x for x in snap.get("quotes", []) if x.get("right") == "P" and x.get("expiry") == date]
            q = next((x for x in puts if float(x["strike"]) == k), None)
            if q is None and date >= UNLISTED_FROM:    # strike not listed: nearest listed strike below, within 1%
                near = [x for x in puts if spy["last"] * (1 - OTM_MAX) <= float(x["strike"]) < k]
                q = max(near, key=lambda x: float(x["strike"]), default=None)
                k = float(q["strike"]) if q else k
            label = f"SPY {date} {k:g}P"
            if q is None or not q.get("ask") or float(q["ask"]) <= 0:
                ev.append(f"no entry: no ask for {label}")
            else:
                ask = float(q["ask"])
                unit = ask * 100 + COMMISSION
                qty = math.floor(STAKE * state["cash"] / unit)
                if qty == 0 and unit <= ONE_MAX * state["cash"]:
                    qty = 1
                if qty < 1:
                    ev.append(f"no entry: {label} at {ask:.2f} does not fit the cash (US${state['cash']:.2f})")
                else:
                    cost = qty * unit
                    state["cash"] -= cost
                    state["position"] = {"contract_id": q["contract_id"], "label": label, "strike": k, "expiry": date,
                                         "qty": qty, "entry": ask, "entry_ts": ts, "entry_asof": asof,
                                         "cost": round(cost, 2),
                                         "spy_entry": spy["last"], "mark": float(q.get("bid") or 0)}
                    state["day"]["entries"] += 1
                    ev.append(f"buy {label} x{qty} at {ask:.2f} (US${cost:.2f}), SPY {spy['last']:.2f}")
    if state["status"] == "live" and state["position"] is None and state["cash"] < BUST:
        state["status"] = "busted"
        ev.append(f"BUSTED: cash US${state['cash']:.2f}")
    state["last_ts"] = ts
    eq = state["cash"] + (state["position"]["mark"] * 100 * state["position"]["qty"] if state["position"] else 0.0)
    return {"ts": ts, "asof": asof, "final": final, "spy": spy["last"], "events": ev or ["nothing to do"], "equity": round(eq, 2),
            "cash": round(state["cash"], 2), "status": state["status"],
            "bench_value": round(START * spy["last"] / state["spy_start"], 2)}


def render(state: dict, row: dict) -> str:
    t = state["trades"]
    wins = sum(1 for x in t if x["pnl"] > 0)
    lines = ["# Y6 Put sprint: US$1,000 to US$200,000", "",
             f"As of {row['ts']} New York. Paper only; rules frozen in `PREREG.md`. Started {state['inception']}.", "",
             "| | Value |", "|---|---:|", f"| Equity | **US${row['equity']:,.2f}** |",
             f"| Status | {state['status']} |", f"| Progress to US$200,000 | {row['equity'] / TARGET:.2%} |",
             f"| US$1,000 in SPY | US${row.get('bench_value', START):,.2f} |",
             f"| Closed trades | {len(t)} ({wins} wins) |", "", "## Last check", "", *[f"- {e}" for e in row["events"]], ""]
    p = state["position"]
    if p:
        lines += [f"Open: {p['label']} x{p['qty']}, entry {p['entry']:.2f} at {p['entry_ts']}, last bid "
                  f"{p.get('mark', 0):.2f}.", ""]
    lines += ["## Trades", "", "| Contract | Qty | In | Entry | Out | Exit | Why | P&L |", "|---|---:|---|---:|---|---:|---|---:|"]
    for x in t:
        lines.append(f"| {x['label']} | {x['qty']} | {x['entry_ts'][5:]} | {x['entry']:.2f} | {x['exit_ts'][5:]} | "
                     f"{x['exit']:.2f} | {x['why']} | {x['pnl']:+,.2f} |")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    pl = sub.add_parser("plan")
    for a in ("--spot", "--open", "--prev-close"):
        pl.add_argument(a, type=float, required=True)
    pl.add_argument("--ts", required=True)
    pl.add_argument("--final", action="store_true")
    ck = sub.add_parser("check")
    ck.add_argument("file")
    a = ap.parse_args(argv)
    sp = OUT / "state.json"
    state = json.loads(sp.read_text()) if sp.exists() else new_state()
    if a.cmd == "plan":
        spy = {"last": a.spot, "open": a.open, "prior_close": a.prev_close}
        same_day = state["day"]["date"] == a.ts[:10]
        probe = state if same_day else {**state, "day": {"date": a.ts[:10], "entries": 0, "stopped": False},
                                        "position": None}
        p = state["position"] if same_day else None
        entry = can_enter(probe, a.ts, a.final) and entry_signal(spy)
        print(json.dumps({"status": state["status"], "cash": round(state["cash"], 2),
                          "open_position": {"contract_id": p["contract_id"], "label": p["label"]} if p else None,
                          "entry_possible_now": entry,
                          "entry_possible_after_an_exit": can_enter({**probe, "position": None}, a.ts, a.final)
                          and entry_signal(spy),
                          "wanted": {"right": "P", "strike": strike_for(a.spot), "expiry": a.ts[:10]}}, indent=1))
        return 0
    snap = json.loads(Path(a.file).read_text())
    row = check(state, snap)
    sp.write_text(json.dumps(state, indent=1))
    with (OUT / "journal.jsonl").open("a") as f:
        f.write(json.dumps(row) + "\n")
    (OUT / "report.md").write_text(render(state, row))
    print(json.dumps(row, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
