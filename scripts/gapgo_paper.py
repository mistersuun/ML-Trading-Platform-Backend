"""Gap-and-go day-trading paper account, US$5,000 (pre-registered: docs/gapgo-paper/PREREG.md).

    .venv/bin/python scripts/gapgo_paper.py scan [--date YYYY-MM-DD]     gap candidates (which 5-minute bars to fetch)
    .venv/bin/python scripts/gapgo_paper.py step [--date YYYY-MM-DD]     simulate the session, update the record
    .venv/bin/python scripts/gapgo_paper.py replay --from D1 --to D2     warm-up replay into warmup.md (no state)

Paper only, offline. Daily bars: state/external_bars (IBKR, merged files). 5-minute regular-hours bars for the
candidates: docs/gapgo-paper/bars/<SYMBOL>.<tag>.json (raw IBKR JSON, merged by timestamp). Everything used for a
decision is known by 09:35 New York time: the gap at the open, the 20-day averages before the session, and the first
5-minute bar.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date, time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import external_bars  # noqa: E402
import daytrade_paper as dtp  # noqa: E402

OUT_DIR = ROOT / "docs" / "gapgo-paper"
BARS_DIR = OUT_DIR / "bars"
STATE_PATH, JOURNAL_PATH, REPORT_PATH = OUT_DIR / "state.json", OUT_DIR / "journal.jsonl", OUT_DIR / "report.md"
START = 5_000.0
GAP_MIN, PRICE_MIN, ADV_DOLLAR_MIN = 0.04, 5.0, 20e6
OPEN_VOL_FRAC = 0.05              # first 5-minute bar volume >= 5% of the 20-day average daily volume
MAX_TRADES, RISK_FRAC, MAX_NOTIONAL_FRAC = 2, 0.01, 0.5
ENTRY_DEADLINE = time(10, 30)      # the breakout must trigger by the 10:25-10:30 bar
TARGET_R = 2.0
SLIP = 0.0010                      # 10 bp per side
COMM_PER_SHARE, COMM_MIN = 0.005, 1.0

ETF_OR_FOREIGN = {"SPY", "QQQ", "IWM", "DIA", "TQQQ", "SGOV", "EFA", "IEF", "GLD", "DBC", "VTV", "RSP", "SMH", "QUAL",
                  "NANC", "GVIP", "GURU", "ACWI", "PSHD", "FFH", "SEQUX", "BH", "GLRE"}


def universe() -> list[str]:
    names = set()
    for p in Path(external_bars.bars_dir()).glob("*.json"):
        names.add(p.name.split(".")[0])
    return sorted(n for n in names if n not in ETF_OR_FOREIGN)


def daily(symbol: str) -> pd.DataFrame:
    try:
        return external_bars.load_bars(symbol, "equity")
    except external_bars.ExternalBarsError:
        return pd.DataFrame()


def candidates(day: date, symbols=None) -> list[dict]:
    """Gap-ups of at least 4% at the open of `day`, price >= $5, 20-day average dollar volume >= $20M."""
    out = []
    ts = pd.Timestamp(day)
    for s in symbols or universe():
        df = daily(s)
        if df.empty or ts not in df.index:
            continue
        i = df.index.get_loc(ts)
        if i < 21:
            continue
        prev = df.iloc[i - 1]
        hist = df.iloc[i - 20:i]
        gap = float(df.iloc[i]["Open"]) / float(prev["Close"]) - 1
        adv_dollar = float((hist["Close"] * hist["Volume"]).mean())
        if gap >= GAP_MIN and prev["Close"] >= PRICE_MIN and adv_dollar >= ADV_DOLLAR_MIN:
            out.append({"symbol": s, "gap": round(gap, 4), "prev_close": float(prev["Close"]),
                        "adv_shares": float(hist["Volume"].mean()), "adv_dollar": round(adv_dollar)})
    return sorted(out, key=lambda c: -c["gap"])


def commission(shares: int) -> float:
    return max(COMM_MIN, COMM_PER_SHARE * shares)


def simulate_trade(bars: pd.DataFrame, cand: dict, equity: float) -> dict:
    """Gap-and-go on one stock's session bars (09:30 first). Returns a trade or a no-trade reason."""
    if len(bars) < 3 or bars.index[0].time() != time(9, 30):
        return {"trade": False, "reason": "no complete opening bars"}
    first = bars.iloc[0]
    if not first["Close"] > first["Open"]:
        return {"trade": False, "reason": "first 5-minute bar not green"}
    vol = float(bars.iloc[0].get("Volume", float("nan")))
    if not vol >= OPEN_VOL_FRAC * cand["adv_shares"]:
        return {"trade": False, "reason": f"opening volume {vol:,.0f} below 5% of average daily volume"}
    trigger, stop = float(first["High"]) + 0.01, float(first["Low"])
    entry, i_entry = None, None
    for i in range(1, len(bars)):
        ts, b = bars.index[i], bars.iloc[i]
        if ts.time() >= ENTRY_DEADLINE:
            break
        if float(b["High"]) >= trigger:
            entry, i_entry = max(trigger, float(b["Open"])) * (1 + SLIP), i
            break
    if entry is None:
        return {"trade": False, "reason": "no breakout above the first bar's high by 10:30"}
    risk = entry - stop
    if risk <= 0:
        return {"trade": False, "reason": "entry below stop"}
    shares = math.floor(min(RISK_FRAC * equity / risk, MAX_NOTIONAL_FRAC * equity / entry))
    if shares < 2:
        return {"trade": False, "reason": "size below two shares"}
    half = shares // 2
    target = entry + TARGET_R * risk
    legs, cur_stop, open_sh, fees = [], stop, shares, commission(shares)
    for i in range(i_entry, len(bars)):
        b = bars.iloc[i]
        o, h, lo = float(b["Open"]), float(b["High"]), float(b["Low"])
        if lo <= cur_stop:
            px = min(o, cur_stop) if i > i_entry else cur_stop
            legs.append((open_sh, px * (1 - SLIP), "stop" if cur_stop == stop else "breakeven stop", bars.index[i]))
            open_sh = 0
            break
        if open_sh == shares and h >= target:
            legs.append((half, max(o, target) * (1 - SLIP) if i > i_entry else target * (1 - SLIP), "half at 2R",
                         bars.index[i]))
            open_sh -= half
            cur_stop = entry
    if open_sh:
        legs.append((open_sh, float(bars.iloc[-1]["Close"]) * (1 - SLIP), "close", bars.index[-1]))
    fees += sum(commission(q) for q, *_ in legs)
    pnl = sum(q * (px - entry) for q, px, *_ in legs) - fees
    return {"trade": True, "shares": shares, "entry": round(entry, 4), "entry_time": bars.index[i_entry].strftime("%H:%M"),
            "stop": round(stop, 4), "target": round(target, 4),
            "exits": [{"shares": q, "price": round(px, 4), "why": w, "time": t.strftime("%H:%M")} for q, px, w, t in legs],
            "r": round(pnl / (RISK_FRAC * equity), 3), "pnl": round(pnl, 2), "fees": round(fees, 2)}


def intraday(symbol: str) -> pd.DataFrame:
    frames = []
    for p in sorted(BARS_DIR.glob(f"{symbol}.*.json")):
        d = json.loads(p.read_text())
        idx = pd.to_datetime(d["time"], utc=True).tz_convert(dtp.NY)
        frames.append(pd.DataFrame({"Open": d["open"], "High": d["high"], "Low": d["low"], "Close": d["close"],
                                    "Volume": d["volume"]}, index=idx, dtype=float))
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames)
    return df[~df.index.duplicated(keep="last")].sort_index()


def run_day(day: date, equity: float) -> dict:
    """All candidates of a day, ranked by gap; trades the first MAX_TRADES that trigger (time-ordered capital)."""
    cands = candidates(day)
    results, trades = [], 0
    for c in cands:
        bars = intraday(c["symbol"])
        sess = dtp.session(bars, day) if not bars.empty else bars
        if sess is None or len(sess) < dtp.FULL_SESSION_BARS - 1:
            results.append({**c, "result": {"trade": False, "reason": "5-minute bars missing"}})
            continue
        if trades >= MAX_TRADES:
            results.append({**c, "result": {"trade": False, "reason": "daily trade limit reached"}})
            continue
        res = simulate_trade(sess, c, equity)
        if res["trade"]:
            trades += 1
        results.append({**c, "result": res})
    pnl = sum(r["result"].get("pnl", 0) for r in results)
    return {"date": str(day), "candidates": results, "pnl": round(pnl, 2)}


def new_state() -> dict:
    return {"inception": None, "last_date": None, "equity": START, "trades": []}


def summary(trades: list[dict]) -> dict:
    return dtp.summary(trades, START) if trades else {"n": 0}


def render(state: dict, rows: list[dict]) -> str:
    s = summary(state["trades"])
    out = [f"# Gap-and-go day-trading paper account (as of {rows[-1]['date']})", "",
           f"Paper only, US$5,000 start, rules in `PREREG.md` (frozen 2026-10-16). Forward record from "
           f"{state['inception']}. Equity **US${state['equity']:,.2f}** ({state['equity'] / START - 1:+.2%}).", "",
           dtp._summary_line(s) if s["n"] else "No trades yet.", "",
           "| Date | Stock | Gap | Entry | Exits | R | P&L |", "|---|---|---:|---:|---|---:|---:|"]
    for t in state["trades"][-25:][::-1]:
        ex = "; ".join(f"{e['shares']} @ {e['price']:.2f} {e['why']} {e['time']}" for e in t["exits"])
        out.append(f"| {t['date']} | {t['symbol']} | {t['gap']:+.1%} | {t['shares']} @ {t['entry']:.2f} {t['entry_time']} | "
                   f"{ex} | {t['r']:+.2f} | {t['pnl']:+,.2f} |")
    out += ["", "## Session log (latest first)", ""]
    for r in rows[-15:][::-1]:
        names = ", ".join(f"{c['symbol']} {c['gap']:+.1%} ({'trade' if c['result'].get('trade') else c['result']['reason']})"
                          for c in r["candidates"]) or "no gap candidates"
        out.append(f"- **{r['date']}** P&L {r['pnl']:+,.2f}. {names}")
    out += ["", "Costs: 10 bp slippage per side, US$0.005 per share (US$1 minimum). The scan uses a fixed universe of "
            "about 130 liquid US stocks, not a full-market scanner, so it misses small-cap gappers.", ""]
    return "\n".join(out)


def latest_session() -> date:
    return external_bars.load_bars("SPY", "etf").index[-1].date()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["scan", "step", "replay"])
    ap.add_argument("--date")
    ap.add_argument("--from", dest="d1")
    ap.add_argument("--to", dest="d2")
    a = ap.parse_args(argv)
    if a.cmd == "scan":
        day = date.fromisoformat(a.date) if a.date else latest_session()
        print(json.dumps({"date": str(day), "candidates": candidates(day)}, indent=2))
        return 0
    if a.cmd == "replay":
        days = [d.date() for d in external_bars.load_bars("SPY", "etf").index
                if date.fromisoformat(a.d1) <= d.date() <= date.fromisoformat(a.d2)]
        eq, trades, lines = START, [], []
        for d in days:
            r = run_day(d, eq)
            for c in r["candidates"]:
                if c["result"].get("trade"):
                    trades.append({"date": str(d), "symbol": c["symbol"], "gap": c["gap"], **c["result"]})
            eq += r["pnl"]
            lines.append(f"- {d}: " + (", ".join(f"{c['symbol']} {c['gap']:+.1%} "
                                                 f"({c['result'].get('pnl', c['result'].get('reason'))})"
                                                 for c in r["candidates"]) or "no gap candidates"))
        md = ["# Gap-and-go warm-up replay (before the freeze, NOT part of the forward record)", "",
              f"{len(days)} sessions, {a.d1} to {a.d2}. Ending equity US${eq:,.2f} from US$5,000. "
              + dtp._summary_line(summary(trades)), "", *lines, ""]
        if trades:
            md += ["| Date | Stock | Gap | R | P&L |", "|---|---|---:|---:|---:|"]
            md += [f"| {t['date']} | {t['symbol']} | {t['gap']:+.1%} | {t['r']:+.2f} | {t['pnl']:+,.2f} |" for t in trades]
        (OUT_DIR / "warmup.md").write_text("\n".join(md) + "\n")
        print("\n".join(md))
        return 0
    day = date.fromisoformat(a.date) if a.date else latest_session()
    state = json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else new_state()
    if state["last_date"] and day <= date.fromisoformat(state["last_date"]):
        print(f"{day} already stepped (last {state['last_date']})", file=sys.stderr)
        return 1
    r = run_day(day, state["equity"])
    for c in r["candidates"]:
        if c["result"].get("trade"):
            state["trades"].append({"date": str(day), "symbol": c["symbol"], "gap": c["gap"], **c["result"]})
    state["equity"] = round(state["equity"] + r["pnl"], 2)
    state["inception"] = state["inception"] or str(day)
    state["last_date"] = str(day)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n")
    with JOURNAL_PATH.open("a") as fh:
        fh.write(json.dumps(r) + "\n")
    rows = [json.loads(x) for x in JOURNAL_PATH.read_text().splitlines() if x.strip()]
    REPORT_PATH.write_text(render(state, rows))
    print(json.dumps(r, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
