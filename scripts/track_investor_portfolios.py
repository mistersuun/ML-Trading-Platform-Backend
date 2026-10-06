"""Forward-track "famous investor" shadow portfolios next to SPY (offline: reads state/external_bars only).

    .venv/bin/python scripts/track_investor_portfolios.py [--asof YYYY-MM-DD] [--bars-dir DIR]

Definitions: docs/forward-test/2026-10/investor-portfolios.json. Each portfolio is a buy-and-hold basket with
weights fixed at inception. Inception is the close of 2026-10-06; while that bar is not on file yet, the latest
available close is used and the note says so (it is re-resolved on every run, so it moves to 10-06 as soon as the
bar arrives). Output: one row per as-of date appended to investor-tracking.jsonl (re-running the same date
replaces that row) and investor-tracking.md rewritten. A ticker with no usable bars is dropped, the remaining
weights are renormalised, and the drop is logged and listed in the row.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import external_bars  # noqa: E402

logger = logging.getLogger("investor_tracker")

OUT_DIR = ROOT / "docs" / "forward-test" / "2026-10"
DEFS_PATH = OUT_DIR / "investor-portfolios.json"
JSONL_PATH = OUT_DIR / "investor-tracking.jsonl"
MD_PATH = OUT_DIR / "investor-tracking.md"
BENCH = "SPY"


# --------------------------------------------------------------------------------------------- loading
def load_definitions(path: Path = DEFS_PATH) -> dict:
    return json.loads(Path(path).read_text())


def load_close(symbol: str, root: Optional[Path] = None) -> pd.Series:
    """Close series by session date. Falls back to a plain per-file parse (no OHLC sanity check) when the merged
    frame fails validation, e.g. an old bad High/Low bar in a base file: closes are all this tracker needs."""
    asset = "etf" if symbol in {"SPY", "QQQ", "NANC", "GURU", "GVIP"} else "equity"
    try:
        df = external_bars.load_bars(symbol, asset, root)
    except external_bars.ExternalBarsError as exc:
        logger.warning("%s: validation failed (%s); using unchecked closes", symbol, exc)
        frames = [external_bars.parse_payload(json.loads(p.read_text()), asset)
                  for p in external_bars.symbol_files(symbol, root)]
        frames = [f for f in frames if not f.empty]
        if not frames:
            return pd.Series(dtype=float)
        df = pd.concat(frames)
        df = df[~df.index.duplicated(keep="last")].sort_index()
    if df.empty:
        return pd.Series(dtype=float)
    return df["Close"].astype(float)


def all_tickers(defs: dict) -> list[str]:
    seen: list[str] = []
    for p in defs["portfolios"].values():
        for t in p["positions"]:
            if t not in seen:
                seen.append(t)
    return seen


def load_all(tickers: list[str], root: Optional[Path] = None) -> dict[str, pd.Series]:
    closes: dict[str, pd.Series] = {}
    for t in tickers:
        s = load_close(t, root)
        if s.empty:
            logger.warning("%s: no bars on file", t)
        else:
            closes[t] = s
    return closes


# --------------------------------------------------------------------------------------------- math
def _norm_weights(positions: dict[str, float], available: set[str]) -> tuple[dict[str, float], list[str]]:
    used = {t: w for t, w in positions.items() if t in available and w > 0}
    missing = [t for t in positions if t not in used]
    tot = sum(used.values())
    return ({t: w / tot for t, w in used.items()} if tot > 0 else {}), missing


def price_at(series: pd.Series, day: pd.Timestamp) -> Optional[float]:
    """Last close at or before `day`; None if the series starts after it."""
    s = series[series.index <= day]
    return float(s.iloc[-1]) if len(s) else None


def resolve_inception(closes: dict[str, pd.Series], target: str, asof: pd.Timestamp) -> tuple[pd.Timestamp, str]:
    """The target date if the benchmark has its bar, else the latest benchmark date on or before asof."""
    tgt = pd.Timestamp(target)
    spy = closes.get(BENCH)
    if spy is None or spy.empty:
        raise RuntimeError(f"no {BENCH} bars: cannot resolve inception")
    if tgt in spy.index and tgt <= asof:
        return tgt, f"close of {tgt.date()}"
    avail = spy.index[spy.index <= min(asof, tgt)]
    if not len(avail):
        raise RuntimeError(f"no {BENCH} bar on or before {tgt.date()}")
    d = avail[-1]
    return d, f"close of {tgt.date()} not available yet; latest available close {d.date()} used"


def basket_value(positions: dict[str, float], closes: dict[str, pd.Series], start: pd.Timestamp,
                 end: pd.Timestamp, capital: float = 10_000.0, index: Optional[pd.DatetimeIndex] = None
                 ) -> tuple[pd.Series, dict[str, float], list[str]]:
    """Buy-and-hold value of `capital` bought at the `start` close with weights renormalised over tickers that
    have a price at `start`. The series runs over `index` (default: SPY dates in [start, end]) with the last known
    close carried forward across dates a ticker did not trade (e.g. a LSE or TSX holiday)."""
    avail = {t for t in positions if t in closes and price_at(closes[t], start) is not None}
    w, missing = _norm_weights(positions, avail)
    if not w:
        return pd.Series(dtype=float), {}, missing
    if index is None:
        base = closes[BENCH]
        index = base.index[(base.index >= start) & (base.index <= end)]
    value = pd.Series(0.0, index=index)
    for t, wt in w.items():
        s = closes[t]
        p0 = price_at(s, start)
        aligned = s.reindex(s.index.union(index)).ffill().reindex(index)
        value = value + capital * wt * aligned / p0
    return value.dropna(), w, missing


def window_return(positions: dict[str, float], closes: dict[str, pd.Series], end: pd.Timestamp,
                  days: int) -> tuple[Optional[float], float]:
    """Static-weight return over the trailing `days` calendar days ending `end`. Tickers lacking history that far
    back are dropped and the rest renormalised; the second value is the share of the original weight covered."""
    start = end - pd.Timedelta(days=days)
    covered = {t: w for t, w in positions.items()
               if t in closes and len(closes[t]) and closes[t].index[0] <= start + pd.Timedelta(days=4)}
    tot = sum(positions.values())
    cov = sum(covered.values()) / tot if tot else 0.0
    if not covered:
        return None, 0.0
    w, _ = _norm_weights(covered, set(covered))
    ret = 0.0
    for t, wt in w.items():
        p0, p1 = price_at(closes[t], start), price_at(closes[t], end)
        if p0 is None:                      # history starts a few days after `start`: use its first close
            p0 = float(closes[t].iloc[0])
        if p1 is None:
            return None, 0.0
        ret += wt * (p1 / p0 - 1.0)
    return ret, cov


def evaluate(defs: dict, closes: dict[str, pd.Series], asof: pd.Timestamp) -> dict:
    """The tracking row for `asof` (see module doc)."""
    capital = float(defs.get("start_value_usd", 10_000))
    incep, note = resolve_inception(closes, defs["inception_target"], asof)
    spy = closes[BENCH]
    last = spy.index[spy.index <= asof][-1]
    index = spy.index[(spy.index >= incep) & (spy.index <= last)]
    rows: dict[str, dict] = {}
    for key, p in defs["portfolios"].items():
        val, w, missing = basket_value(p["positions"], closes, incep, last, capital, index)
        if val.empty:
            rows[key] = {"name": p["name"], "error": "no usable tickers", "missing": missing}
            logger.error("%s: no usable tickers", key)
            continue
        if missing:
            logger.warning("%s: missing %s; renormalised over %s", key, missing, sorted(w))
        daily = val.pct_change().dropna()
        r1m, c1m = window_return(p["positions"], closes, last, 30)
        r1y, c1y = window_return(p["positions"], closes, last, 365)
        rows[key] = {
            "name": p["name"],
            "value": round(float(val.iloc[-1]), 2),
            "ret_since_inception": float(val.iloc[-1] / capital - 1.0),
            "daily_returns": {d.strftime("%Y-%m-%d"): float(r) for d, r in daily.items()},
            "ret_1d": float(daily.iloc[-1]) if len(daily) else None,
            "ret_1m": r1m, "cov_1m": round(c1m, 3),
            "ret_1y": r1y, "cov_1y": round(c1y, 3),
            "weights": {t: round(x, 4) for t, x in w.items()},
            "missing": missing,
        }
    spy_ret = rows.get("spy", {}).get("ret_since_inception")
    for r in rows.values():
        if "error" not in r:
            r["vs_spy"] = (r["ret_since_inception"] - spy_ret) if spy_ret is not None else None
    return {"date": last.strftime("%Y-%m-%d"), "inception_date": incep.strftime("%Y-%m-%d"),
            "inception_note": note, "start_value_usd": capital, "portfolios": rows}


# --------------------------------------------------------------------------------------------- output
def append_row(row: dict, path: Path = JSONL_PATH) -> None:
    """Idempotent per date: an existing row for the same date is replaced, others are kept in date order."""
    rows = []
    if Path(path).exists():
        rows = [json.loads(x) for x in Path(path).read_text().splitlines() if x.strip()]
    rows = [r for r in rows if r.get("date") != row["date"]] + [row]
    rows.sort(key=lambda r: r["date"])
    Path(path).write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))


def _pct(x: Optional[float], plus: bool = True) -> str:
    if x is None:
        return "n/a"
    v = round(x * 100, 2) + 0.0             # +0.0 turns -0.00 into 0.00
    return f"{v:{'+' if plus else ''}.2f}%"


def render_md(rows: list[dict], defs: dict) -> str:
    cur = rows[-1]
    out = [f"# Investor shadow portfolios vs SPY (as of {cur['date']})", "",
           f"Inception {cur['inception_date']}: {cur['inception_note']}. Start value ${cur['start_value_usd']:,.0f} each, "
           "buy-and-hold, price return only. Paper tracking, not advice. Weights are secondary-source 13F data, "
           "unverified against SEC.", "",
           "| Portfolio | Value | Since inception | vs SPY | 1d | 1M | 1Y | Missing |",
           "|---|---:|---:|---:|---:|---:|---:|---|"]
    order = [k for k in defs["portfolios"] if k in cur["portfolios"]]
    for key in order:
        r = cur["portfolios"][key]
        if "error" in r:
            out.append(f"| {r['name']} | n/a | n/a | n/a | n/a | n/a | n/a | {', '.join(r['missing'])} |")
            continue
        y = _pct(r["ret_1y"]) + ("" if r["cov_1y"] >= 0.999 or r["ret_1y"] is None else f" ({r['cov_1y']:.0%} cov.)")
        m = _pct(r["ret_1m"]) + ("" if r["cov_1m"] >= 0.999 or r["ret_1m"] is None else f" ({r['cov_1m']:.0%} cov.)")
        out.append(f"| {r['name']} | ${r['value']:,.2f} | {_pct(r['ret_since_inception'])} | {_pct(r.get('vs_spy'))} | "
                   f"{_pct(r['ret_1d'])} | {m} | {y} | {', '.join(r['missing']) or '-'} |")
    out += ["", "1M and 1Y are trailing static-weight returns to the as-of date for context; where a ticker has no "
            "history that far back it is left out, the rest renormalised, and the weight coverage is shown.", "",
            "## Daily value of $10,000", ""]
    keys = [k for k in order if "error" not in cur["portfolios"][k]]
    out.append("| Date | " + " | ".join(cur["portfolios"][k]["name"] for k in keys) + " |")
    out.append("|---|" + "---:|" * len(keys))
    # rebuild value history from the latest row's daily returns (each starts at the start value)
    dates = sorted({d for k in keys for d in cur["portfolios"][k]["daily_returns"]})
    cap = cur["start_value_usd"]
    out.append(f"| {cur['inception_date']} | " + " | ".join(f"{cap:,.2f}" for _ in keys) + " |")
    acc = {k: cap for k in keys}
    for d in dates:
        for k in keys:
            acc[k] *= 1.0 + cur["portfolios"][k]["daily_returns"].get(d, 0.0)
        out.append(f"| {d} | " + " | ".join(f"{acc[k]:,.2f}" for k in keys) + " |")
    out += ["", "## Notes", ""]
    for key, p in defs["portfolios"].items():
        out.append(f"- **{p['name']}**: {p['basis']}. Skipped: {p.get('skipped', 'none')}.")
    return "\n".join(out) + "\n"


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--asof", default=date.today().isoformat(), help="evaluate up to this date (default today)")
    ap.add_argument("--bars-dir", default=None, help="external bars dir (default config.EXTERNAL_BARS_DIR)")
    ap.add_argument("--defs", default=str(DEFS_PATH))
    ap.add_argument("--no-write", action="store_true", help="print the table only")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    defs = load_definitions(Path(a.defs))
    root = Path(a.bars_dir) if a.bars_dir else None
    closes = load_all(all_tickers(defs), root)
    row = evaluate(defs, closes, pd.Timestamp(a.asof))
    if a.no_write:
        print(render_md([row], defs))
        return 0
    append_row(row)
    rows = [json.loads(x) for x in JSONL_PATH.read_text().splitlines() if x.strip()]
    MD_PATH.write_text(render_md(rows, defs))
    print(render_md(rows, defs).split("## Daily value")[0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
