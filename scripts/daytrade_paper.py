"""Day-trading paper test: 5-minute opening range breakout (pre-registered in docs/daytrade-paper/PREREG.md).

    .venv/bin/python scripts/daytrade_paper.py step [--asof YYYY-MM-DD]     forward record (once per session)
    .venv/bin/python scripts/daytrade_paper.py replay                       warm-up replay of pre-freeze history

Paper only. Bars are raw IBKR get_price_history JSON (5-minute, regular hours) saved under
docs/daytrade-paper/bars/ as <SYMBOL>.<tag>.json; all files of a symbol are merged by timestamp (later files win).
`step` simulates each book's trade for the session (default: the latest session present for every symbol),
updates state.json, appends journal.jsonl and rewrites report.md. A session can be stepped once; dates increase.
`replay` simulates every complete session on file up to the freeze date into warmup.md, without touching state.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date, time
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "docs" / "daytrade-paper"
BARS_DIR = OUT_DIR / "bars"
STATE_PATH = OUT_DIR / "state.json"
JOURNAL_PATH = OUT_DIR / "journal.jsonl"
REPORT_PATH = OUT_DIR / "report.md"
WARMUP_PATH = OUT_DIR / "warmup.md"

NY = ZoneInfo("America/New_York")
SYMBOLS = ("QQQ", "SPY")
START = 25_000.0
RISK_FRAC, MAX_LEVERAGE, TARGET_R = 0.01, 4.0, 10.0
SLIP = 0.0001
COMM_PER_SHARE, COMM_MIN = 0.005, 1.0
FREEZE = date(2026, 10, 15)
FULL_SESSION_BARS = 78


# ------------------------------------------------------------------------------------------------ bars
def load_bars(symbol: str, bars_dir: Path = BARS_DIR) -> pd.DataFrame:
    """All files for `symbol`, merged; index = bar start in New York time; columns Open/High/Low/Close."""
    frames = []
    for p in sorted(Path(bars_dir).glob(f"{symbol}.*.json")):
        d = json.loads(p.read_text())
        idx = pd.to_datetime(d["time"], utc=True).tz_convert(NY)
        frames.append(pd.DataFrame({"Open": d["open"], "High": d["high"], "Low": d["low"], "Close": d["close"]},
                                   index=idx, dtype=float))
    if not frames:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close"])
    df = pd.concat(frames)
    return df[~df.index.duplicated(keep="last")].sort_index()


def session(df: pd.DataFrame, day: date) -> pd.DataFrame:
    s = df[df.index.date == day]
    return s[(s.index.time >= time(9, 30)) & (s.index.time < time(16, 0))]


def complete_sessions(df: pd.DataFrame) -> list[date]:
    days = sorted(set(df.index.date))
    return [d for d in days if len(session(df, d)) >= FULL_SESSION_BARS - 1 and session(df, d).index[0].time() == time(9, 30)]


def commission(shares: int) -> float:
    return max(COMM_MIN, COMM_PER_SHARE * shares)


# ------------------------------------------------------------------------------------------------ rule
def simulate_day(bars: pd.DataFrame, equity: float) -> Optional[dict]:
    """One ORB trade on one session's 5-minute bars (09:30 first). None when there is no trade."""
    if len(bars) < 3 or bars.index[0].time() != time(9, 30):
        return None
    first = bars.iloc[0]
    if first["Close"] == first["Open"]:
        return {"trade": False, "reason": "doji opening bar"}
    side = 1 if first["Close"] > first["Open"] else -1
    entry = float(bars.iloc[1]["Open"]) * (1 + SLIP * side)
    stop = float(first["Low"] if side == 1 else first["High"])
    risk = (entry - stop) * side
    if risk <= 0:
        return {"trade": False, "reason": "entry beyond the opening-range stop"}
    target = entry + side * TARGET_R * risk
    shares = math.floor(min(RISK_FRAC * equity / risk, MAX_LEVERAGE * equity / entry))
    if shares < 1:
        return {"trade": False, "reason": "size below one share"}
    exit_px, why, when = None, None, None
    for ts, b in bars.iloc[1:].iterrows():
        o, h, lo = float(b["Open"]), float(b["High"]), float(b["Low"])
        hit_stop = lo <= stop if side == 1 else h >= stop
        hit_tgt = h >= target if side == 1 else lo <= target
        if hit_stop:
            exit_px = min(o, stop) if side == 1 else max(o, stop)
            why, when = "stop", ts
            break
        if hit_tgt:
            exit_px = max(o, target) if side == 1 else min(o, target)
            why, when = "target (10R)", ts
            break
    if exit_px is None:
        exit_px, why, when = float(bars.iloc[-1]["Close"]), "end of day", bars.index[-1]
    exit_px *= (1 - SLIP * side)
    fees = commission(shares) * 2
    pnl = (exit_px - entry) * side * shares - fees
    return {"trade": True, "side": "long" if side == 1 else "short", "shares": shares,
            "entry": round(entry, 4), "stop": round(stop, 4), "target": round(target, 4),
            "exit": round(exit_px, 4), "exit_time": when.strftime("%H:%M"), "reason": why,
            "r": round(pnl / (RISK_FRAC * equity), 3), "pnl": round(pnl, 2), "fees": round(fees, 2)}


# ----------------------------------------------------------------------------------------------- books
def new_state() -> dict:
    return {"inception": None, "last_asof": None,
            "books": {s: {"equity": START, "bench_start": None, "trades": []} for s in SYMBOLS}}


def load_state() -> dict:
    return json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else new_state()


def step(state: dict, asof: date, bars: dict[str, pd.DataFrame]) -> tuple[dict, dict]:
    if state["last_asof"] and asof <= date.fromisoformat(state["last_asof"]):
        raise ValueError(f"{asof} is not after the last stepped session {state['last_asof']}")
    row = {"asof": str(asof), "books": {}}
    for s in SYMBOLS:
        day = session(bars[s], asof)
        if len(day) < FULL_SESSION_BARS - 1:
            raise ValueError(f"{s}: only {len(day)} regular-hours bars for {asof}")
        book = state["books"][s]
        if book["bench_start"] is None:
            book["bench_start"] = float(day.iloc[0]["Open"])
        res = simulate_day(day, book["equity"])
        if res and res.get("trade"):
            book["equity"] = round(book["equity"] + res["pnl"], 2)
            book["trades"].append({"date": str(asof), **res})
        bench = START * float(day.iloc[-1]["Close"]) / book["bench_start"]
        row["books"][s] = {"result": res, "equity": book["equity"], "bench_value": round(bench, 2)}
    if state["inception"] is None:
        state["inception"] = str(asof)
    state["last_asof"] = str(asof)
    return state, row


def summary(trades: list[dict], start: float = START) -> dict:
    n = len(trades)
    if not n:
        return {"n": 0}
    rs = [t["r"] for t in trades]
    mean = sum(rs) / n
    sd = (sum((r - mean) ** 2 for r in rs) / (n - 1)) ** 0.5 if n > 1 else float("nan")
    eq, peak, mdd = start, start, 0.0
    for t in trades:
        eq += t["pnl"]
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1)
    return {"n": n, "win_rate": sum(1 for t in trades if t["pnl"] > 0) / n, "mean_r": mean,
            "t_stat": mean / (sd / n ** 0.5) if n > 1 and sd > 0 else float("nan"),
            "total": sum(t["pnl"] for t in trades), "max_dd": mdd}


def _trade_table(trades: list[dict]) -> list[str]:
    out = ["| Date | Side | Shares | Entry | Exit | Exit time | Reason | R | P&L |",
           "|---|---|---:|---:|---:|---|---|---:|---:|"]
    out += [f"| {t['date']} | {t['side']} | {t['shares']} | {t['entry']:.2f} | {t['exit']:.2f} | {t['exit_time']} | "
            f"{t['reason']} | {t['r']:+.2f} | {t['pnl']:+,.2f} |" for t in trades]
    return out


def _summary_line(s: dict) -> str:
    if not s["n"]:
        return "No trades yet."
    return (f"{s['n']} trades, win rate {s['win_rate']:.0%}, mean {s['mean_r']:+.2f}R (t = {s['t_stat']:.2f}), "
            f"total P&L {s['total']:+,.2f}, max drawdown {s['max_dd']:.1%}")


def render(state: dict, rows: list[dict]) -> str:
    last = rows[-1]
    out = [f"# Day-trading paper test: 5-minute opening range breakout (as of {last['asof']})", "",
           f"Paper only, rules in `PREREG.md` (frozen 2026-10-15). Forward record from {state['inception']}. "
           "Warm-up replay of the pre-freeze sessions is in `warmup.md` and is not counted here. Not advice.", "",
           "| Book | Start | Equity | Return | Buy-and-hold | Trades |", "|---|---:|---:|---:|---:|---:|"]
    for s in SYMBOLS:
        b, r = state["books"][s], last["books"][s]
        out.append(f"| {s} ORB | ${START:,.0f} | ${b['equity']:,.2f} | {b['equity'] / START - 1:+.2%} | "
                   f"${r['bench_value']:,.2f} | {len(b['trades'])} |")
    for s in SYMBOLS:
        trades = state["books"][s]["trades"]
        out += ["", f"## {s}", "", _summary_line(summary(trades))]
        if trades:
            out += [""] + _trade_table(trades[-20:][::-1])
    out += ["", "A real edge needs at least 60 trades and t above about 2. Costs: 1 bp slippage per side, "
            "US$0.005 per share (US$1 minimum) commission; no borrow fee on shorts.", ""]
    return "\n".join(out)


def replay(bars: dict[str, pd.DataFrame]) -> str:
    out = ["# Day-trading warm-up replay (pre-freeze sessions, NOT part of the forward record)", "",
           "The pre-registered rules run once over the complete sessions on file before the freeze (2026-10-15). "
           "This checks the plumbing and shows what the rule does. It is about 12 sessions, far too few to judge "
           "anything.", ""]
    for s in SYMBOLS:
        eq, trades = START, []
        for d in [d for d in complete_sessions(bars[s]) if d < FREEZE]:
            res = simulate_day(session(bars[s], d), eq)
            if res and res.get("trade"):
                eq += res["pnl"]
                trades.append({"date": str(d), **res})
        out += [f"## {s}", "", _summary_line(summary(trades)) + f"; ending equity ${eq:,.2f}", ""]
        out += _trade_table(trades) + [""]
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["step", "replay"])
    ap.add_argument("--asof")
    a = ap.parse_args(argv)
    bars = {s: load_bars(s) for s in SYMBOLS}
    if a.cmd == "replay":
        WARMUP_PATH.write_text(replay(bars))
        print(WARMUP_PATH.read_text())
        return 0
    asof = date.fromisoformat(a.asof) if a.asof else min(complete_sessions(bars[s])[-1] for s in SYMBOLS)
    state, row = step(load_state(), asof, bars)
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n")
    with JOURNAL_PATH.open("a") as fh:
        fh.write(json.dumps(row) + "\n")
    rows = [json.loads(x) for x in JOURNAL_PATH.read_text().splitlines() if x.strip()]
    REPORT_PATH.write_text(render(state, rows))
    print(json.dumps(row, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
