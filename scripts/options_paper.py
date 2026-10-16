"""Options paper test (pre-registered in docs/options-paper/PREREG.md). Paper only: no orders anywhere.

    .venv/bin/python scripts/options_paper.py plan [--asof YYYY-MM-DD]
    .venv/bin/python scripts/options_paper.py step --quotes docs/options-paper/quotes/YYYY-MM-DD.json [--asof ...]

`plan` reads the underlying daily bars (state/external_bars, offline) and the book state, and prints which option
quotes the session needs: the open contracts, plus a strike/expiry window for each wanted entry. The quotes are then
fetched read-only from IBKR (get_option_parameters / get_option_data / get_price_snapshot) and saved as
{"asof": "YYYY-MM-DD", "quotes": [{"contract_id", "underlying", "right": "C"|"P", "strike", "expiry": "YYYY-MM-DD",
"bid", "ask", "delta"}]}. `step` applies the frozen rules to those quotes: exits first, then entries, fills at
the ask (buy) / bid (sell) plus commission. It updates docs/options-paper/state.json, appends journal.jsonl and
rewrites report.md. A session date can be stepped once; dates must increase.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import external_bars  # noqa: E402

OUT_DIR = ROOT / "docs" / "options-paper"
STATE_PATH = OUT_DIR / "state.json"
JOURNAL_PATH = OUT_DIR / "journal.jsonl"
REPORT_PATH = OUT_DIR / "report.md"

COMMISSION = 0.65          # US$ per contract
MULT = 100
L_START, P_START = 500.0, 100_000.0
L_UNDERLYINGS = ("QQQ", "SPY")   # priority order
L_MAX_OPEN, L_BUDGET_FRAC = 2, 0.30
L_DTE, L_TARGET_DTE = (21, 45), 30
L_TP, L_STOP, L_TIME_EXIT = 2.0, 0.4, 5
P_UNDERLYING = "SPY"
P_DTE, P_TARGET_DTE, P_DELTA = (30, 45), 37, 0.20
P_TP, P_TIME_EXIT = 0.5, 7
BENCH = {"L": "QQQ", "P": "SPY", "M": "QQQ"}
# Book M (added 2026-10-16, docs/aggressive-accounts/PREREG.md): calls on the strongest momentum stocks.
M_START, M_SLOTS, M_BUDGET_FRAC = 1_000.0, 2, 0.50
M_DTE, M_TARGET_DTE, M_DELTA = (60, 120), 90, 0.35
M_TP, M_STOP, M_TIME_EXIT, M_TOPN = 3.0, 0.4, 30, 5


# ------------------------------------------------------------------------------------------------- data
def load_closes(symbol: str, root: Optional[Path] = None) -> pd.Series:
    return external_bars.load_bars(symbol, "etf", root)["Close"].astype(float)


def load_closes_any(symbol: str, root: Optional[Path] = None) -> pd.Series:
    return external_bars.load_bars(symbol, "equity", root)["Close"].astype(float)


def momentum_ranking(asof, root: Optional[Path] = None) -> list[str]:
    """Top M_TOPN stocks of the quant-accounts universe by 12-1 month return, as of `asof`'s close."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import quant_accounts as qa
    _, c = qa.load_panel(["SPY", *qa.STOCKS], root)
    c = c.loc[:pd.Timestamp(asof)]
    u = [s for s in qa.STOCKS if s in c]
    if len(c) < 253:
        return []
    score = (c[u].iloc[-22] / c[u].iloc[-253] - 1).dropna()
    return list(score.nlargest(M_TOPN).index)


def signal(closes: pd.Series) -> Optional[str]:
    """'C' (close above its 50-day SMA and at a 20-session closing high), 'P' (the mirror) or None.
    `closes` ends at the decision session."""
    if len(closes) < 51:
        return None
    c = float(closes.iloc[-1])
    sma = float(closes.iloc[-50:].mean())
    prior = closes.iloc[-21:-1]
    if c > sma and c >= float(prior.max()):
        return "C"
    if c < sma and c <= float(prior.min()):
        return "P"
    return None


def _d(x) -> date:
    return x if isinstance(x, date) else date.fromisoformat(str(x)[:10])


def dte(expiry, asof) -> int:
    return (_d(expiry) - _d(asof)).days


def mid(q: dict) -> float:
    return (float(q["bid"]) + float(q["ask"])) / 2.0


def pick_expiry(expiries, asof, window, target) -> Optional[str]:
    ok = [e for e in set(expiries) if window[0] <= dte(e, asof) <= window[1]]
    return min(ok, key=lambda e: (abs(dte(e, asof) - target), e)) if ok else None


def choose_lottery(quotes: list[dict], underlying: str, right: str, spot: float, asof, budget: float):
    """(quote, qty) for Book L, or None. OTM only, fits the budget, largest |delta|."""
    qs = [q for q in quotes if q["underlying"] == underlying and q["right"] == right]
    exp = pick_expiry([q["expiry"] for q in qs], asof, L_DTE, L_TARGET_DTE)
    best = None
    for q in qs:
        if q["expiry"] != exp:
            continue
        bid, ask, k = float(q["bid"] or 0), float(q["ask"] or 0), float(q["strike"])
        if bid <= 0 or ask <= 0 or ask < bid:
            continue
        if (right == "C" and k <= spot) or (right == "P" and k >= spot):
            continue
        if ask - bid > max(0.05, 0.20 * mid(q)):
            continue
        qty = math.floor(budget / (ask * MULT + COMMISSION))
        if qty < 1:
            continue
        score = abs(float(q["delta"])) if q.get("delta") is not None else -abs(k - spot) / spot
        if best is None or score > best[2]:
            best = (q, qty, score)
    return (best[0], best[1]) if best else None


def choose_momentum_call(quotes: list[dict], ranking: list[str], spots: dict, asof, budget: float, held: set):
    """(quote, qty) for Book M, or None. Walks the momentum ranking; for the first stock (not already held) with an
    affordable OTM call in the 60-120 DTE window (closest to 90), takes the one with delta closest to 0.35."""
    for sym in ranking:
        if sym in held or sym not in spots:
            continue
        qs = [q for q in quotes if q["underlying"] == sym and q["right"] == "C" and q.get("delta") is not None]
        exp = pick_expiry([q["expiry"] for q in qs], asof, M_DTE, M_TARGET_DTE)
        ok = []
        for q in qs:
            bid, ask, k = float(q["bid"] or 0), float(q["ask"] or 0), float(q["strike"])
            if q["expiry"] != exp or bid <= 0 or ask < bid or k <= spots[sym]:
                continue
            if ask - bid > max(0.10, 0.25 * mid(q)):
                continue
            qty = math.floor(budget / (ask * MULT + COMMISSION))
            if qty >= 1:
                ok.append((abs(float(q["delta"]) - M_DELTA), k, q, qty))
        if ok:
            _, _, q, qty = min(ok, key=lambda t: (t[0], t[1]))
            return q, qty
    return None


def choose_put(quotes: list[dict], asof, cash: float):
    """(quote, qty) for Book P, or None: SPY put, |delta| closest to 0.20, cash-secured."""
    qs = [q for q in quotes if q["underlying"] == P_UNDERLYING and q["right"] == "P"
          and q.get("delta") is not None and float(q["bid"] or 0) > 0 and float(q["ask"] or 0) > 0]
    exp = pick_expiry([q["expiry"] for q in qs], asof, P_DTE, P_TARGET_DTE)
    qs = [q for q in qs if q["expiry"] == exp]
    if not qs:
        return None
    q = min(qs, key=lambda q: (abs(abs(float(q["delta"])) - P_DELTA), float(q["strike"])))
    qty = math.floor(cash / (float(q["strike"]) * MULT))
    return (q, qty) if qty >= 1 else None


# ------------------------------------------------------------------------------------------------ state
def new_state() -> dict:
    return {"inception": None, "last_asof": None, "bench_start": {},
            "books": {"L": {"start": L_START, "cash": L_START, "positions": [], "closed": []},
                      "P": {"start": P_START, "cash": P_START, "positions": [], "closed": []},
                      "M": {"start": M_START, "cash": M_START, "positions": [], "closed": []}}}


def load_state(path: Path = STATE_PATH) -> dict:
    state = json.loads(Path(path).read_text()) if Path(path).exists() else new_state()
    state["books"].setdefault("M", {"start": M_START, "cash": M_START, "positions": [], "closed": []})
    return state


def _pos(q: dict, qty: int, side: str, price: float, asof) -> dict:
    return {"contract_id": int(q["contract_id"]), "underlying": q["underlying"], "right": q["right"],
            "strike": float(q["strike"]), "expiry": str(q["expiry"])[:10], "qty": int(qty), "side": side,
            "entry_price": round(price, 4), "entry_date": str(asof), "last_mid": round(mid(q), 4)}


def _label(p: dict) -> str:
    return f"{p['underlying']} {p['expiry']} {p['strike']:g}{p['right']}"


def intrinsic(p: dict, spot: float) -> float:
    return max(0.0, spot - p["strike"]) if p["right"] == "C" else max(0.0, p["strike"] - spot)


def book_equity(book: dict) -> float:
    sign = {"long": 1, "short": -1}
    return book["cash"] + sum(sign[p["side"]] * p["last_mid"] * MULT * p["qty"] for p in book["positions"])


def _close(book: dict, p: dict, price: float, asof, reason: str, events: list) -> None:
    fees = COMMISSION * p["qty"]
    if p["side"] == "long":
        book["cash"] += price * MULT * p["qty"] - fees
        pnl = (price - p["entry_price"]) * MULT * p["qty"] - 2 * fees
    else:
        book["cash"] -= price * MULT * p["qty"] + fees
        pnl = (p["entry_price"] - price) * MULT * p["qty"] - 2 * fees
    book["positions"].remove(p)
    book["closed"].append({**p, "exit_price": round(price, 4), "exit_date": str(asof), "reason": reason,
                           "pnl": round(pnl, 2)})
    events.append(f"close {_label(p)} x{p['qty']} at {price:.2f} ({reason}), P&L {pnl:+.2f}")


def _manage(name: str, book: dict, qmap: dict, spots: dict, asof, events: list) -> None:
    for p in list(book["positions"]):
        q = qmap.get(p["contract_id"])
        left = dte(p["expiry"], asof)
        if left <= 0 and (q is None or left < 0):
            px = intrinsic(p, spots[p["underlying"]])
            p["last_mid"] = px
            _close(book, p, px, asof, "expired (intrinsic)", events)
            continue
        if q is None or float(q["bid"] or 0) < 0 or float(q["ask"] or 0) <= 0:
            events.append(f"no usable quote for {_label(p)}; held at last mark {p['last_mid']:.2f}")
            continue
        p["last_mid"] = round(mid(q), 4)
        bid, ask = float(q["bid"] or 0), float(q["ask"])
        if name == "L":
            if bid >= L_TP * p["entry_price"]:
                _close(book, p, bid, asof, "take profit (2x)", events)
            elif bid <= L_STOP * p["entry_price"]:
                _close(book, p, bid, asof, "stop (-60%)", events)
            elif left <= L_TIME_EXIT:
                _close(book, p, bid, asof, f"time exit ({left}d left)", events)
        elif name == "M":
            if bid >= M_TP * p["entry_price"]:
                _close(book, p, bid, asof, "take profit (3x)", events)
            elif bid <= M_STOP * p["entry_price"]:
                _close(book, p, bid, asof, "stop (-60%)", events)
            elif left <= M_TIME_EXIT:
                _close(book, p, bid, asof, f"time exit ({left}d left)", events)
        else:
            if ask <= P_TP * p["entry_price"]:
                _close(book, p, ask, asof, "take profit (50% of credit)", events)
            elif left <= P_TIME_EXIT:
                _close(book, p, ask, asof, f"time exit ({left}d left)", events)


def step(state: dict, asof, quotes: list[dict], closes: dict[str, pd.Series],
         ranking: Optional[list[str]] = None) -> tuple[dict, dict]:
    """Apply one session. `closes` holds each underlying's closes (they are cut at `asof` here)."""
    asof = _d(asof)
    if state["last_asof"] and asof <= _d(state["last_asof"]):
        raise ValueError(f"{asof} is not after the last stepped session {state['last_asof']}")
    cut = {s: c.loc[:pd.Timestamp(asof)] for s, c in closes.items()}
    for s, c in cut.items():
        if c.empty or c.index[-1].date() != asof:
            raise ValueError(f"no {s} close for {asof}")
    spots = {s: float(c.iloc[-1]) for s, c in cut.items()}
    qmap = {int(q["contract_id"]): q for q in quotes}
    events: dict[str, list[str]] = {"L": [], "P": [], "M": []}
    if state["inception"] is None:
        state["inception"] = str(asof)
        state["bench_start"] = {b: spots[s] for b, s in BENCH.items()}
    if "M" not in state["bench_start"]:
        state["bench_start"]["M"] = spots[BENCH["M"]]
        state["m_inception"] = str(asof)

    L, P = state["books"]["L"], state["books"]["P"]
    _manage("L", L, qmap, spots, asof, events["L"])
    _manage("P", P, qmap, spots, asof, events["P"])

    # Book L entries: at most one per session, at most L_MAX_OPEN open, one per underlying.
    held = {p["underlying"] for p in L["positions"]}
    signals = {}
    for sym in L_UNDERLYINGS:
        signals[sym] = signal(cut[sym])
    if len(L["positions"]) < L_MAX_OPEN:
        for sym in L_UNDERLYINGS:
            sig = signals[sym]
            if not sig or sym in held:
                continue
            budget = min(L_BUDGET_FRAC * book_equity(L), L["cash"])
            pick = choose_lottery(quotes, sym, sig, spots[sym], asof, budget)
            if pick is None:
                events["L"].append(f"{sym} {'call' if sig == 'C' else 'put'} signal; no contract fits the "
                                   f"US${budget:.2f} budget")
                continue
            q, qty = pick
            price = float(q["ask"])
            L["cash"] -= price * MULT * qty + COMMISSION * qty
            p = _pos(q, qty, "long", price, asof)
            L["positions"].append(p)
            events["L"].append(f"buy {_label(p)} x{qty} at {price:.2f} (delta {q.get('delta')})")
            break

    # Book P entry: whenever no put is open.
    if not P["positions"]:
        pick = choose_put(quotes, asof, P["cash"])
        if pick is None:
            events["P"].append("no eligible put in the quotes")
        else:
            q, qty = pick
            price = float(q["bid"])
            P["cash"] += price * MULT * qty - COMMISSION * qty
            p = _pos(q, qty, "short", price, asof)
            P["positions"].append(p)
            events["P"].append(f"sell {_label(p)} x{qty} at {price:.2f} (delta {q.get('delta')})")

    # Book M: momentum calls, at most M_SLOTS open (one per stock), one entry per session.
    M = state["books"]["M"]
    _manage("M", M, qmap, spots, asof, events["M"])
    if ranking and len(M["positions"]) < M_SLOTS:
        budget = min(M_BUDGET_FRAC * book_equity(M), M["cash"])
        pick = choose_momentum_call(quotes, ranking, spots, asof, budget, {p["underlying"] for p in M["positions"]})
        if pick is None:
            events["M"].append(f"no affordable call on {', '.join(ranking)} within US${budget:.2f}")
        else:
            q, qty = pick
            price = float(q["ask"])
            M["cash"] -= price * MULT * qty + COMMISSION * qty
            p = _pos(q, qty, "long", price, asof)
            M["positions"].append(p)
            events["M"].append(f"buy {_label(p)} x{qty} at {price:.2f} (delta {q.get('delta')})")
    state["last_asof"] = str(asof)
    row = {"asof": str(asof), "spots": spots, "signals": signals, "ranking": ranking, "events": events, "books": {}}
    for name, book in state["books"].items():
        b = BENCH[name]
        bench_val = book["start"] * spots[b] / state["bench_start"][name]
        row["books"][name] = {"equity": round(book_equity(book), 2), "cash": round(book["cash"], 2),
                              "open": len(book["positions"]), "closed": len(book["closed"]),
                              "bench": b, "bench_value": round(bench_val, 2)}
    return state, row


def plan(state: dict, asof, closes: dict[str, pd.Series], ranking: Optional[list[str]] = None) -> dict:
    """What quotes the session needs (the fetcher uses this; it changes no state)."""
    asof = _d(asof)
    cut = {s: c.loc[:pd.Timestamp(asof)] for s, c in closes.items()}
    spots = {s: float(c.iloc[-1]) for s, c in cut.items()}
    out = {"asof": str(asof), "spots": spots, "open_contracts": [], "entries": []}
    for name, book in state["books"].items():
        for p in book["positions"]:
            out["open_contracts"].append({"book": name, "contract_id": p["contract_id"], "label": _label(p)})
    L = state["books"]["L"]
    held = {p["underlying"] for p in L["positions"]}
    if len(L["positions"]) < L_MAX_OPEN:
        for sym in L_UNDERLYINGS:
            sig = signal(cut[sym])
            if sig and sym not in held:
                lo, hi = (1.0, 1.20) if sig == "C" else (0.80, 1.0)   # fetch window only; the rule is in choose_lottery
                out["entries"].append({"book": "L", "underlying": sym, "right": sig, "dte": list(L_DTE),
                                       "target_dte": L_TARGET_DTE,
                                       "strikes": [round(spots[sym] * lo), round(spots[sym] * hi)]})
    if not state["books"]["P"]["positions"]:
        s = spots[P_UNDERLYING]
        out["entries"].append({"book": "P", "underlying": P_UNDERLYING, "right": "P", "dte": list(P_DTE),
                               "target_dte": P_TARGET_DTE, "strikes": [round(s * 0.88), round(s * 0.98)]})
    M = state["books"].get("M")
    if M is not None and ranking and len(M["positions"]) < M_SLOTS:
        held_m = {p["underlying"] for p in M["positions"]}
        out["entries"].append({"book": "M", "underlyings_in_order": [x for x in ranking if x not in held_m],
                               "right": "C", "dte": list(M_DTE), "target_dte": M_TARGET_DTE,
                               "strikes": "spot to spot x 1.25 (OTM), about 5 strikes around delta 0.35; walk the "
                                          "list until one has a call whose ask x 100 fits "
                                          f"{M_BUDGET_FRAC:.0%} of Book M equity"})
    return out


# ----------------------------------------------------------------------------------------------- output
def stats(closed: list[dict]) -> dict:
    wins = [t["pnl"] for t in closed if t["pnl"] > 0]
    losses = [t["pnl"] for t in closed if t["pnl"] <= 0]
    n = len(closed)
    return {"n": n, "win_rate": len(wins) / n if n else None,
            "avg_win": sum(wins) / len(wins) if wins else None,
            "avg_loss": sum(losses) / len(losses) if losses else None,
            "total": sum(t["pnl"] for t in closed)}


def render(state: dict, rows: list[dict]) -> str:
    last = rows[-1]
    names = {"L": "Book L: $500 lottery (buys calls/puts on QQQ/SPY)", "P": "Book P: SPY cash-secured put writer",
             "M": "Book M: $1,000 momentum calls (aggressive group, from 2026-10-16)"}
    out = [f"# Options paper test (as of {last['asof']})", "",
           f"Paper only, rules in `PREREG.md` (frozen 2026-10-15). Inception {state['inception']}. "
           "Fills at ask/bid plus US$0.65 per contract; positions marked at mid. Not advice.", "",
           "| Book | Start | Equity | Return | Benchmark | Bench value | Open | Closed |",
           "|---|---:|---:|---:|---|---:|---:|---:|"]
    for k, b in state["books"].items():
        r = last["books"][k]
        out.append(f"| {names[k]} | ${b['start']:,.0f} | ${r['equity']:,.2f} | {r['equity'] / b['start'] - 1:+.2%} | "
                   f"{r['bench']} buy-and-hold | ${r['bench_value']:,.2f} | {r['open']} | {r['closed']} |")
    for k, b in state["books"].items():
        out += ["", f"## {names[k]}", ""]
        if b["positions"]:
            out += ["Open:", "", "| Contract | Qty | Side | Entry | Entry date | Mark |", "|---|---:|---|---:|---|---:|"]
            out += [f"| {_label(p)} | {p['qty']} | {p['side']} | {p['entry_price']:.2f} | {p['entry_date']} | "
                    f"{p['last_mid']:.2f} |" for p in b["positions"]]
        else:
            out.append("No open position.")
        if b["closed"]:
            s = stats(b["closed"])
            out += ["", f"Closed trades: {s['n']}, win rate {s['win_rate']:.0%}, total P&L {s['total']:+,.2f}"
                    + (f", average win {s['avg_win']:+,.2f}" if s["avg_win"] is not None else "")
                    + (f", average loss {s['avg_loss']:+,.2f}" if s["avg_loss"] is not None else ""), "",
                    "| Contract | Qty | Entry | Exit | Dates | Reason | P&L |", "|---|---:|---:|---:|---|---|---:|"]
            out += [f"| {_label(t)} | {t['qty']} | {t['entry_price']:.2f} | {t['exit_price']:.2f} | "
                    f"{t['entry_date']} to {t['exit_date']} | {t['reason']} | {t['pnl']:+,.2f} |" for t in b["closed"]]
    out += ["", "## Session log", ""]
    for r in rows[-15:][::-1]:
        ev = "; ".join(f"{k}: {e}" for k, es in r["events"].items() for e in es) or "no action"
        eqs = ", ".join(f"{k} ${b['equity']:,.2f}" for k, b in r["books"].items())
        out.append(f"- **{r['asof']}** {eqs}. {ev}")
    out += ["", "Caveats: closing quotes are used for both decision and fill; Book P's collateral earns nothing here "
            "(a real account would earn about the T-bill rate); no early assignment or dividends are modelled. "
            "Fewer than 30 closed trades per book is an illustration, not evidence.", ""]
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["plan", "step"])
    ap.add_argument("--asof")
    ap.add_argument("--quotes")
    ap.add_argument("--bars-dir")
    a = ap.parse_args(argv)
    root = Path(a.bars_dir) if a.bars_dir else None
    closes = {s: load_closes(s, root) for s in sorted(set(L_UNDERLYINGS) | {P_UNDERLYING})}
    asof = _d(a.asof) if a.asof else closes[P_UNDERLYING].index[-1].date()
    ranking = momentum_ranking(asof, root)
    for s in ranking:
        closes[s] = load_closes_any(s, root)
    state = load_state()
    if a.cmd == "plan":
        print(json.dumps(plan(state, asof, closes, ranking), indent=2))
        return 0
    if not a.quotes:
        ap.error("step needs --quotes")
    payload = json.loads(Path(a.quotes).read_text())
    if payload.get("asof") and _d(payload["asof"]) != asof:
        ap.error(f"quotes are for {payload['asof']}, not {asof}")
    state, row = step(state, asof, payload["quotes"], closes, ranking)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n")
    with JOURNAL_PATH.open("a") as fh:
        fh.write(json.dumps(row) + "\n")
    rows = [json.loads(x) for x in JOURNAL_PATH.read_text().splitlines() if x.strip()]
    REPORT_PATH.write_text(render(state, rows))
    print(json.dumps(row, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
