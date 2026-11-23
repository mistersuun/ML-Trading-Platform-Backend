"""Y7 "Mover sprint" (rules frozen in docs/mover-paper/PREREG.md). Paper only: no orders anywhere.

    .venv/bin/python scripts/mover_paper.py rank docs/mover-paper/checks/2026-11-23T1005.json
    .venv/bin/python scripts/mover_paper.py check docs/mover-paper/checks/2026-11-23T1005.json

Check file:
{"ts": "YYYY-MM-DDTHH:MM" (New York), "kind": "regular" | "fill" | "final",
 "prices": {"NVDA": {"last", "open", "prior_close"}, ...}   (real-time; regular checks only),
 "quotes": [{"contract_id", "underlying", "right", "strike", "expiry", "bid", "ask"}]   (15 minutes delayed)}

`rank` lists the movers and the strike level each needs. `check` applies, in order: a stale order is cancelled,
a due order is filled (fill checks), the open position's exit rules, then a new order (regular checks).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "mover-paper"
START, TARGET, BUST = 1_000.0, 200_000.0, 100.0
STAKE, ONE_MAX, TP, STOP, COMMISSION = 0.50, 0.75, 2.0, 0.5, 0.65
MAX_ENTRIES, LAST_ORDER_HOUR, FILL_MIN, FILL_MAX, MOVERS_TRIED, STRIKES_TRIED = 2, 14, 17, 40, 2, 4
# A name's "typical day" in percent. An order needs a move of at least one typical day.
TYPICAL = {"SPY": 1.0, "QQQ": 1.3, "AAPL": 1.7, "MSFT": 1.7, "AMZN": 2.0, "GOOGL": 2.0, "META": 2.3, "NVDA": 3.0,
           "TSLA": 3.5, "AMD": 3.5}


def new_state() -> dict:
    return {"cash": START, "position": None, "pending": None, "trades": [],
            "day": {"date": None, "entries": 0, "stopped": False}, "status": "live", "last_ts": None,
            "inception": None, "spy_start": None}


def _t(ts: str) -> dt.datetime:
    return dt.datetime.strptime(ts, "%Y-%m-%dT%H:%M")


def rank(prices: dict) -> list[dict]:
    """Movers in order: last beyond both the open and the prior close, move of at least one typical day."""
    out = []
    for sym, typ in TYPICAL.items():
        p = prices.get(sym)
        if not p or not all(p.get(k) for k in ("last", "open", "prior_close")):
            continue
        last, op, pc = float(p["last"]), float(p["open"]), float(p["prior_close"])
        chg = (last / pc - 1) * 100
        if last < op and last < pc:
            right = "P"
        elif last > op and last > pc:
            right = "C"
        else:
            continue
        score = abs(chg) / typ
        if score < 1.0:
            continue
        otm = typ / 2 / 100                              # half a typical day out of the money
        level = last * (1 - otm) if right == "P" else last * (1 + otm)
        out.append({"symbol": sym, "right": right, "change_pct": round(chg, 2), "score": round(score, 2),
                    "strike_level": round(level, 2), "last": last})
    return sorted(out, key=lambda m: (-m["score"], m["symbol"]))


def _fits(ask: float, cash: float) -> int:
    unit = ask * 100 + COMMISSION
    qty = math.floor(STAKE * cash / unit)
    if qty == 0 and unit <= ONE_MAX * cash:
        qty = 1
    return qty


def choose(movers: list[dict], quotes: list[dict], cash: float, date: str):
    """First of the top movers with a contract that fits: nearest expiry, strikes from the level outward."""
    for m in movers[:MOVERS_TRIED]:
        qs = [q for q in quotes if q.get("underlying") == m["symbol"] and q.get("right") == m["right"]
              and q.get("expiry", "") >= date]
        if not qs:
            continue
        exp = min(q["expiry"] for q in qs)
        if m["right"] == "P":
            qs = sorted((q for q in qs if q["expiry"] == exp and float(q["strike"]) <= m["strike_level"]),
                        key=lambda q: -float(q["strike"]))
        else:
            qs = sorted((q for q in qs if q["expiry"] == exp and float(q["strike"]) >= m["strike_level"]),
                        key=lambda q: float(q["strike"]))
        for q in qs[:STRIKES_TRIED]:
            if q.get("ask") and float(q["ask"]) > 0 and _fits(float(q["ask"]), cash) >= 1:
                return m, q
    return None


def can_order(state: dict, ts: str, kind: str) -> bool:
    d = state["day"]
    return (state["status"] == "live" and kind == "regular" and state["position"] is None
            and state["pending"] is None and d["entries"] < MAX_ENTRIES and not d["stopped"]
            and int(ts[11:13]) <= LAST_ORDER_HOUR)


def _roll_day(state: dict, date: str, ev: list) -> None:
    if state["day"]["date"] == date:
        return
    p = state["position"]
    if p is not None:                                    # the final check was missed: recorded as a total loss
        state["trades"].append({**p, "exit": 0.0, "exit_ts": date, "why": "final check missed", "pnl": -p["cost"]})
        ev.append(f"{p['label']} x{p['qty']}: final check missed, recorded as a total loss ({-p['cost']:+.2f})")
        state["position"] = None
    if state["pending"] is not None:
        ev.append(f"order for {state['pending']['label']} cancelled (not filled the day it was placed)")
        state["pending"] = None
    state["day"] = {"date": date, "entries": 0, "stopped": False}


def _sell(state: dict, price: float, ts: str, why: str) -> str:
    p = state["position"]
    proceeds = price * 100 * p["qty"] - (COMMISSION * p["qty"] if price > 0 else 0.0)
    state["cash"] += proceeds
    pnl = proceeds - p["cost"]
    state["trades"].append({**p, "exit": price, "exit_ts": ts, "why": why, "pnl": round(pnl, 2)})
    state["position"] = None
    return f"sell {p['label']} x{p['qty']} at {price:.2f} ({why}): P&L {pnl:+.2f}"


def check(state: dict, snap: dict) -> dict:
    ts, kind = snap["ts"], snap.get("kind", "regular")
    if kind not in ("regular", "fill", "final"):
        raise SystemExit(f"unknown kind {kind}")
    if state["last_ts"] and ts <= state["last_ts"]:
        raise SystemExit(f"check {ts} is not after the last check {state['last_ts']}")
    ev: list[str] = []
    if state["status"] != "live":
        return {"ts": ts, "kind": kind, "events": [f"account is {state['status']}: nothing to do"],
                "equity": round(state["cash"], 2), "status": state["status"]}
    date = ts[:10]
    _roll_day(state, date, ev)
    prices, quotes = snap.get("prices") or {}, snap.get("quotes") or []
    qmap = {q["contract_id"]: q for q in quotes if q.get("contract_id") is not None}
    spy = (prices.get("SPY") or {}).get("last")
    if state["inception"] is None and spy:
        state["inception"], state["spy_start"] = ts, float(spy)

    o = state["pending"]
    if o is not None:                                    # an order fills on a quote taken 17 to 40 minutes later
        age = (_t(ts) - _t(o["ts"])).total_seconds() / 60
        q = qmap.get(o["contract_id"])
        if kind == "final" or age > FILL_MAX:
            ev.append(f"order for {o['label']} cancelled ({'final check' if kind == 'final' else 'no fill check in time'})")
            state["pending"] = None
        elif age >= FILL_MIN:
            ask = float(q["ask"]) if q and q.get("ask") else 0.0
            qty = _fits(ask, state["cash"]) if ask > 0 else 0
            if qty < 1:
                ev.append(f"order for {o['label']} cancelled ({'no ask' if ask <= 0 else f'ask {ask:.2f} does not fit the cash'})")
            else:
                cost = qty * (ask * 100 + COMMISSION)
                state["cash"] -= cost
                state["position"] = {**{k: o[k] for k in ("contract_id", "label", "underlying", "right", "strike",
                                                           "expiry", "reason")},
                                     "qty": qty, "entry": ask, "ordered_ts": o["ts"], "entry_ts": ts,
                                     "cost": round(cost, 2), "mark": float(q.get("bid") or 0)}
                state["day"]["entries"] += 1
                ev.append(f"buy {o['label']} x{qty} at {ask:.2f} (US${cost:.2f}); ordered {o['ts'][11:]}: {o['reason']}")
            state["pending"] = None
        else:
            ev.append(f"order for {o['label']} waits for its fill check")

    p = state["position"]
    if p is not None and kind != "fill":
        q = qmap.get(p["contract_id"])
        bid = float(q["bid"]) if q and q.get("bid") is not None else None
        if bid is not None and state["cash"] + bid * 100 * p["qty"] >= TARGET:
            ev.append(_sell(state, bid, ts, "target reached"))
            state["status"] = "target"
        elif kind == "final":
            ev.append(_sell(state, bid or 0.0, ts, "final check"))
        elif bid is None:
            ev.append(f"no bid for {p['label']}: held")
        elif bid >= TP * p["entry"]:
            ev.append(_sell(state, bid, ts, "take profit"))
        elif bid <= STOP * p["entry"]:
            ev.append(_sell(state, bid, ts, "stop"))
            state["day"]["stopped"] = True
        else:
            p["mark"] = bid
            ev.append(f"hold {p['label']} x{p['qty']}: bid {bid:.2f} vs entry {p['entry']:.2f}")

    if can_order(state, ts, kind):
        movers = rank(prices)
        if not movers:
            ev.append("no order: no name has moved a typical day beyond both its open and its prior close")
        else:
            pick = choose(movers, quotes, state["cash"], date)
            top = ", ".join(f"{m['symbol']} {m['change_pct']:+.1f}% ({m['score']:.1f}x)" for m in movers[:3])
            if pick is None:
                ev.append(f"no order: movers {top}; no contract fits the cash (US${state['cash']:.2f})")
            else:
                m, q = pick
                label = f"{m['symbol']} {q['expiry']} {float(q['strike']):g}{m['right']}"
                reason = f"{m['symbol']} {m['change_pct']:+.1f}% ({m['score']:.1f}x a typical day)"
                state["pending"] = {"ts": ts, "contract_id": q["contract_id"], "label": label,
                                    "underlying": m["symbol"], "right": m["right"], "strike": float(q["strike"]),
                                    "expiry": q["expiry"], "reason": reason, "seen_ask": float(q["ask"])}
                ev.append(f"order: buy {label} (delayed ask {float(q['ask']):.2f}); {reason}; movers {top}")

    if state["status"] == "live" and state["position"] is None and state["pending"] is None and state["cash"] < BUST:
        state["status"] = "busted"
        ev.append(f"BUSTED: cash US${state['cash']:.2f}")
    state["last_ts"] = ts
    pos = state["position"]
    eq = state["cash"] + (pos["mark"] * 100 * pos["qty"] if pos else 0.0)
    row = {"ts": ts, "kind": kind, "events": ev or ["nothing to do"], "equity": round(eq, 2),
           "cash": round(state["cash"], 2), "status": state["status"], "pending": bool(state["pending"])}
    if spy and state["spy_start"]:
        row["bench_value"] = state["bench_last"] = round(START * float(spy) / state["spy_start"], 2)
    return row


def render(state: dict, row: dict) -> str:
    t = state["trades"]
    wins = sum(1 for x in t if x["pnl"] > 0)
    lines = ["# Y7 Mover sprint: US$1,000 to US$200,000", "",
             f"As of {row['ts']} New York. Paper only; rules frozen in `PREREG.md`. Started {state['inception']}.", "",
             "| | Value |", "|---|---:|", f"| Equity | **US${row['equity']:,.2f}** |",
             f"| Status | {state['status']} |", f"| Progress to US$200,000 | {row['equity'] / TARGET:.2%} |",
             f"| US$1,000 in SPY | US${state.get('bench_last', START):,.2f} |",
             f"| Closed trades | {len(t)} ({wins} wins) |", "", "## Last check", "", *[f"- {e}" for e in row["events"]], ""]
    p, o = state["position"], state["pending"]
    if p:
        lines += [f"Open: {p['label']} x{p['qty']}, entry {p['entry']:.2f} at {p['entry_ts']}, last bid "
                  f"{p.get('mark', 0):.2f}.", ""]
    if o:
        lines += [f"Order waiting for its fill: {o['label']}, placed {o['ts']}.", ""]
    lines += ["## Trades", "", "| Contract | Qty | Why | In | Entry | Out | Exit | Exit reason | P&L |",
              "|---|---:|---|---|---:|---|---:|---|---:|"]
    for x in t:
        lines.append(f"| {x['label']} | {x['qty']} | {x.get('reason', '')} | {x['entry_ts'][5:]} | {x['entry']:.2f} | "
                     f"{x['exit_ts'][5:]} | {x['exit']:.2f} | {x['why']} | {x['pnl']:+,.2f} |")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("rank", "check"):
        sub.add_parser(name).add_argument("file")
    a = ap.parse_args(argv)
    sp = OUT / "state.json"
    state = json.loads(sp.read_text()) if sp.exists() else new_state()
    snap = json.loads(Path(a.file).read_text())
    if a.cmd == "rank":
        probe = state if state["day"]["date"] == snap["ts"][:10] else {**state, "position": None, "pending": None,
                                                                         "day": {"date": None, "entries": 0,
                                                                                 "stopped": False}}
        print(json.dumps({"status": state["status"], "cash": round(state["cash"], 2),
                          "open_position": state["position"] and {k: state["position"][k] for k in ("contract_id", "label")},
                          "pending": state["pending"] and {k: state["pending"][k] for k in ("contract_id", "label", "ts")},
                          "order_possible": can_order(probe, snap["ts"], snap.get("kind", "regular")),
                          "order_possible_after_an_exit": can_order({**probe, "position": None}, snap["ts"],
                                                                    snap.get("kind", "regular")),
                          "movers": rank(snap.get("prices") or {})[:MOVERS_TRIED],
                          "quote": f"nearest expiry; the {STRIKES_TRIED} listed strikes at or beyond strike_level "
                                   "(below it for puts, above it for calls)"}, indent=1))
        return 0
    row = check(state, snap)
    sp.write_text(json.dumps(state, indent=1))
    with (OUT / "journal.jsonl").open("a") as f:
        f.write(json.dumps(row) + "\n")
    (OUT / "report.md").write_text(render(state, row))
    print(json.dumps(row, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
