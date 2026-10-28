"""Weekly Portfolio Pilot report for the owner's real account (advisory only, D1/D25). Never committed: the input and
output live under state/pilot/ (git-ignored) because they contain the owner's holdings.

    .venv/bin/python scripts/pilot_report.py state/pilot/account-YYYYMMDD.json [--leverage 1.45] [--mode static]

The account file is the verbatim output of the read-only IBKR tools, saved as
{"asof": "YYYY-MM-DD", "summary": <get_account_summary>, "positions": <get_account_positions>}.
Daily TSX bars come from state/pilot_bars/<SYM>.D*.json (base file plus dated top-ups, merged, later files win).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import pilot as pl  # noqa: E402

BARS = ROOT / "state" / "pilot_bars"
OUT_DIR = ROOT / "state" / "pilot"


def load_daily(sym: str) -> pd.Series:
    parts = []
    for p in sorted(BARS.glob(f"{sym}.D*.json")):
        d = json.loads(p.read_text())
        idx = pd.to_datetime(d["time"], utc=True).tz_convert("America/New_York").tz_localize(None).normalize()
        parts.append(pd.Series(d["close"], index=idx, dtype=float))
    if not parts:
        return pd.Series(dtype=float)
    s = pd.concat(parts)
    return s[~s.index.duplicated(keep="last")].sort_index()


def parse_account(path: Path) -> tuple[dict, float, float, str]:
    acct = json.loads(Path(path).read_text())
    summ, pos = acct["summary"], acct["positions"]
    pos = pos.get("positions", pos) if isinstance(pos, dict) else pos
    out = {}
    for p in pos:
        sym = p["contract_description"].split(" @")[0].split(".")[0].strip()
        out[sym] = {"qty": float(p["position"]), "price": float(p["market_price"])}
    return out, float(summ["total_cash_value"]), float(summ["net_liquidation"]), acct.get("asof", "")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("account")
    ap.add_argument("--leverage", type=float, default=pl.DEFAULT_POLICY["leverage"])
    ap.add_argument("--mode", default=pl.DEFAULT_POLICY["mode"], choices=["static", "trend", "pilot"])
    a = ap.parse_args(argv)
    positions, cash, nlv_ibkr, asof = parse_account(Path(a.account))
    closes = {t: load_daily(t) for s in pl.SLEEVES for t in s.tsx}
    closes = {t: c for t, c in closes.items() if not c.empty}
    p = pl.propose(positions, cash, closes, asof=asof, policy={"mode": a.mode, "leverage": a.leverage})
    md = pl.render(p)
    if abs(p.nlv - nlv_ibkr) > 0.01 * max(nlv_ibkr, 1):
        md += f"\nNote: net value from positions (C${p.nlv:,.0f}) differs from IBKR's (C${nlv_ibkr:,.0f}) by more than 1%.\n"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"report-{asof or 'latest'}.md"
    out.write_text(md)
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
