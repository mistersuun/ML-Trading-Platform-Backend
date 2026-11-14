"""Weekly US$700 "moonshot" ticket (account Y4; rules frozen in docs/lottery-paper/MOONSHOT.md). Paper only.

    .venv/bin/python scripts/moonshot_paper.py plan [--asof YYYY-MM-DD]
    .venv/bin/python scripts/moonshot_paper.py step [--quotes docs/lottery-paper/quotes/moonshot-YYYY-MM-DD.json] [--asof ...]

Every week a fresh US$700 ticket goes all-in, at the close of the week's first session, on the call about 3% out of the
money, expiring at the week's last session, on the universe name with the strongest 5-session return. It is held to
expiry and settled at intrinsic value on that close. `plan` says whether tonight is an entry night and which contract
it wants; `step` settles a due ticket and, on an entry night, buys from the quotes file. Run `step` every session
(without --quotes when `plan` wants nothing).
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
UNIVERSE = ("TSLA", "NVDA", "AMD", "MSTR", "COIN", "PLTR", "SMCI", "HOOD")
TICKET, OTM, LOOKBACK, COMMISSION = 700.0, 0.03, 5, 0.65
TARGET = 1_000_000.0


def strike_inc(price: float) -> float:
    return 0.5 if price < 25 else 1.0 if price < 200 else 2.5 if price < 500 else 5.0


def call_strike(close: float) -> float:
    inc = strike_inc(close)
    return round(math.ceil(close * (1 + OTM) / inc - 1e-9) * inc, 2)


def pick(closes: pd.DataFrame) -> tuple[str, float]:
    r = closes.iloc[-1] / closes.iloc[-1 - LOOKBACK] - 1
    r = r.dropna()
    sym = r.sort_values(ascending=False).index[0]
    return sym, float(r[sym])


def week_bounds(day: pd.Timestamp, sessions: pd.DatetimeIndex) -> tuple[pd.Timestamp, pd.Timestamp]:
    iso = day.isocalendar()
    wk = [d for d in sessions if d.isocalendar().year == iso.year and d.isocalendar().week == iso.week]
    return wk[0], wk[-1]


def new_state() -> dict:
    return {"tickets": [], "last_asof": None}


def settle(state: dict, asof: str, closes: pd.DataFrame) -> list[str]:
    ev = []
    for t in state["tickets"]:
        if t.get("settled") or t["expiry"] > asof:
            continue
        px = closes[t["symbol"]].get(pd.Timestamp(t["expiry"]))
        if px is None or pd.isna(px):
            ev.append(f"cannot settle {t['label']}: no close for {t['expiry']}")
            continue
        val = max(0.0, float(px) - t["strike"])
        fee = COMMISSION * t["qty"] if val > 0 else 0.0
        t.update(settled=True, settle=round(val, 4), underlying_close=float(px),
                 proceeds=round(val * 100 * t["qty"] - fee + t["unspent"], 2))
        t["multiple"] = round(t["proceeds"] / TICKET, 4)
        ev.append(f"settle {t['label']} x{t['qty']} at {val:.2f} ({t['symbol']} {float(px):.2f}): "
                  f"US${TICKET:.0f} -> US${t['proceeds']:,.2f} ({t['multiple']:.2f}x)")
    return ev


def enter(state: dict, asof: str, want: dict, quotes: list[dict]) -> list[str]:
    q = next((x for x in quotes if x.get("underlying") == want["symbol"] and x.get("right") == "C"
              and float(x["strike"]) == want["strike"] and x.get("expiry") == want["expiry"]), None)
    label = f"{want['symbol']} {want['expiry']} {want['strike']:g}C"
    if q is None or not q.get("ask") or float(q["ask"]) <= 0:
        state["tickets"].append({"week_of": asof, "label": label, **want, "skipped": "no ask", "settled": True,
                                 "qty": 0, "unspent": TICKET, "proceeds": TICKET, "multiple": 1.0})
        return [f"no ticket this week: no ask for {label}"]
    ask = float(q["ask"])
    qty = math.floor(TICKET / (ask * 100 + COMMISSION))
    if qty < 1:
        state["tickets"].append({"week_of": asof, "label": label, **want, "skipped": "over budget", "settled": True,
                                 "qty": 0, "unspent": TICKET, "proceeds": TICKET, "multiple": 1.0})
        return [f"no ticket this week: {label} at {ask:.2f} costs more than US${TICKET:.0f}"]
    cost = qty * (ask * 100 + COMMISSION)
    state["tickets"].append({"week_of": asof, "label": label, **want, "contract_id": q.get("contract_id"), "qty": qty,
                             "entry": ask, "cost": round(cost, 2), "unspent": round(TICKET - cost, 2),
                             "settled": False})
    return [f"buy {label} x{qty} at {ask:.2f} (US${cost:,.2f}; US${TICKET - cost:,.2f} unspent)"]


def load(asof: Optional[str]):
    import external_bars
    from data.calendars import get_calendar
    cal = get_calendar("XNYS")
    closes = pd.DataFrame({s: external_bars.closed_bars(s, None, cal, "equity")["Close"].astype(float)
                           for s in UNIVERSE})
    spy = external_bars.closed_bars("SPY", None, cal, "etf")["Close"]
    closes = closes.reindex(spy.index)
    if asof:
        closes = closes.loc[:pd.Timestamp(asof)]
    last = closes.index[-1]
    sessions = cal.sessions(last - pd.Timedelta(days=7), last + pd.Timedelta(days=10))
    return closes, last, pd.DatetimeIndex(sessions)


def wanted(closes: pd.DataFrame, last: pd.Timestamp, sessions: pd.DatetimeIndex, state: dict) -> Optional[dict]:
    first, final = week_bounds(last, sessions)
    if last != first or any(t["week_of"] == str(last.date()) for t in state["tickets"]):
        return None
    sym, ret = pick(closes)
    c = float(closes[sym].iloc[-1])
    return {"symbol": sym, "ret5": round(ret, 4), "spot": c, "strike": call_strike(c), "expiry": str(final.date())}


def render(state: dict, asof: str) -> str:
    done = [t for t in state["tickets"] if t.get("settled")]
    spent = TICKET * len(state["tickets"])
    back = sum(t["proceeds"] for t in done)
    hits = lambda m: sum(1 for t in done if t.get("multiple", 0) >= m)  # noqa: E731
    lines = ["# Y4 weekly US$700 moonshot ticket", "",
             f"As of {asof}. Paper only; rules frozen in `MOONSHOT.md`. A fresh US$700 every week, all-in on one "
             "short-dated call. The question: how often does US$700 become US$1,000,000 (1,429x) in a week?", "",
             f"Tickets: {len(state['tickets'])} (US${spent:,.0f} staked); settled {len(done)}, returning "
             f"US${back:,.2f}. Weeks at 2x or more: {hits(2)}; 10x or more: {hits(10)}; "
             f"US$1M (1,429x): {hits(TARGET / TICKET)}.", "",
             "| Week of | Contract | 5-day move | Qty | Entry | Settle | Returned | Multiple |",
             "|---|---|---:|---:|---:|---:|---:|---:|"]
    for t in state["tickets"]:
        lines.append(f"| {t['week_of']} | {t['label']} | {t['ret5']:+.1%} | {t['qty']} | "
                     f"{t.get('entry', 0):.2f} | {('%.2f' % t['settle']) if 'settle' in t else ('-' if not t.get('skipped') else t['skipped'])} | "
                     f"{('US$%s' % format(t['proceeds'], ',.2f')) if t.get('settled') else 'open'} | "
                     f"{('%.2fx' % t['multiple']) if t.get('settled') else '-'} |")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["plan", "step"])
    ap.add_argument("--quotes")
    ap.add_argument("--asof")
    a = ap.parse_args(argv)
    sp = OUT / "moonshot-state.json"
    state = json.loads(sp.read_text()) if sp.exists() else new_state()
    closes, last, sessions = load(a.asof)
    asof = str(last.date())
    want = wanted(closes, last, sessions, state)
    if a.cmd == "plan":
        due = [t["label"] for t in state["tickets"] if not t.get("settled") and t["expiry"] <= asof]
        print(json.dumps({"asof": asof, "settle_tonight": due, "entry": want}, indent=2))
        return 0
    if state["last_asof"] and asof <= state["last_asof"]:
        raise SystemExit(f"session {asof} is not after the last stepped session {state['last_asof']}")
    ev = settle(state, asof, closes)
    if want:
        if not a.quotes:
            raise SystemExit("tonight is an entry night: step needs --quotes")
        q = json.loads(Path(a.quotes).read_text())
        if q.get("asof") != asof:
            raise SystemExit(f"quotes are for {q.get('asof')}, the session is {asof}")
        ev += enter(state, asof, want, q["quotes"])
    state["last_asof"] = asof
    sp.write_text(json.dumps(state, indent=1))
    if ev:
        with (OUT / "moonshot-journal.jsonl").open("a") as f:
            f.write(json.dumps({"asof": asof, "events": ev}) + "\n")
    (OUT / "moonshot-report.md").write_text(render(state, asof))
    print("\n".join(ev) if ev else "nothing tonight")
    return 0


if __name__ == "__main__":
    sys.exit(main())
