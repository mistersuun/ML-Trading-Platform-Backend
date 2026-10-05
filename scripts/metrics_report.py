"""Per-(symbol, pattern) backtest metrics report and diff tool.

    uv run python scripts/metrics_report.py --synthetic --out docs/baseline/synthetic_metrics.csv
    uv run python scripts/metrics_report.py data/snapshots/2026-10-04 --out new.csv
    uv run python scripts/metrics_report.py --diff old.csv new.csv
"""
from __future__ import annotations

import argparse
import io
import sys
import warnings
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

COLUMNS = ["symbol", "pattern", "total_trades", "win_rate", "profit_factor", "sharpe",
           "max_drawdown", "total_return", "is_valid"]
KEY = ["symbol", "pattern"]
SYNTHETIC_SYMBOLS = ["SYN_A", "SYN_B", "SYN_C"]
SYNTHETIC_N = 500


def synthetic_frames() -> dict[str, pd.DataFrame]:
    from tests.fixtures.synthetic import gbm_ohlc
    return {s: gbm_ohlc(SYNTHETIC_N, seed=100 + i) for i, s in enumerate(SYNTHETIC_SYMBOLS)}


def snapshot_frames(snap_dir: Path) -> dict[str, pd.DataFrame]:
    import json
    manifest = snap_dir / "manifest.json"
    names = {}
    if manifest.exists():
        for fn, meta in json.loads(manifest.read_text())["files"].items():
            names[fn] = meta["symbol"]
    out = {}
    for p in sorted(snap_dir.glob("*.csv")):
        df = pd.read_csv(p, index_col=0, parse_dates=True)
        out[names.get(p.name, p.stem)] = df
    return out


def compute_report(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    import backtester
    from patterns import PATTERN_REGISTRY

    rows = []
    for symbol in sorted(frames):
        for name, func in PATTERN_REGISTRY.items():
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                try:
                    sig = func(frames[symbol].copy())
                    r = backtester.classic_backtest(sig, symbol, name)
                except Exception as exc:  # noqa: BLE001
                    print(f"WARN {symbol}/{name}: {exc}", file=sys.stderr)
                    continue
            rows.append({
                "symbol": symbol, "pattern": name, "total_trades": r.total_trades,
                "win_rate": r.win_rate, "profit_factor": r.profit_factor,
                "sharpe": r.sharpe_ratio, "max_drawdown": r.max_drawdown_pct,
                "total_return": r.total_return_pct, "is_valid": bool(r.is_valid),
            })
    return pd.DataFrame(rows, columns=COLUMNS)


def report_to_csv(df: pd.DataFrame) -> str:
    buf = io.StringIO()
    df.to_csv(buf, index=False, float_format="%.8f", lineterminator="\n")
    return buf.getvalue()


def synthetic_csv() -> str:
    return report_to_csv(compute_report(synthetic_frames()))


def diff_reports(old: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    m = old.merge(new, on=KEY, how="outer", suffixes=("_old", "_new"), indicator=True)
    cols = [c for c in COLUMNS if c not in KEY]
    changed = []
    for _, row in m.iterrows():
        if row["_merge"] != "both":
            changed.append(row)
            continue
        for c in cols:
            a, b = row[c + "_old"], row[c + "_new"]
            if isinstance(a, float) or isinstance(b, float):
                if abs(float(a) - float(b)) > 1e-8:
                    changed.append(row)
                    break
            elif a != b:
                changed.append(row)
                break
    return pd.DataFrame(changed)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("snapshot", nargs="?", help="snapshot directory")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--out", help="output CSV path (default stdout)")
    ap.add_argument("--diff", nargs=2, metavar=("OLD", "NEW"))
    args = ap.parse_args(argv)

    if args.diff:
        old, new = (pd.read_csv(p) for p in args.diff)
        d = diff_reports(old, new)
        if d.empty:
            print("no changes")
        else:
            with pd.option_context("display.width", 250, "display.max_columns", 50,
                                   "display.max_rows", 1000):
                print(f"{len(d)} changed rows")
                print(d.to_string(index=False))
        return 0
    if args.synthetic:
        text = synthetic_csv()
    elif args.snapshot:
        text = report_to_csv(compute_report(snapshot_frames(Path(args.snapshot))))
    else:
        ap.error("give a snapshot dir, --synthetic or --diff")
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text)
        print(f"wrote {args.out}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
