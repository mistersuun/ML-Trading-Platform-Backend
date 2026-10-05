"""Snapshot daily bars for config.WATCHLIST to data/snapshots/<date>/.

Run LOCALLY by the owner (market-data hosts are not reachable from CI/sandbox):

    uv run python scripts/snapshot_bars.py [--date YYYY-MM-DD] [--days N] [--out data/snapshots]

Writes one CSV per symbol plus manifest.json (sha256, row count, source per file).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def safe_name(symbol: str) -> str:
    return symbol.replace("/", "_").replace("=", "_").replace("^", "_")


def all_symbols() -> list[str]:
    import config
    seen: list[str] = []
    for syms in config.WATCHLIST.values():
        for s in syms:
            if s not in seen:
                seen.append(s)
    return seen


def main(argv: list[str] | None = None) -> int:
    import config
    import data_fetcher

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", default=dt.date.today().isoformat())
    ap.add_argument("--days", type=int, default=config.LOOKBACK_DAYS)
    ap.add_argument("--out", default=str(ROOT / "data" / "snapshots"))
    args = ap.parse_args(argv)

    out_dir = Path(args.out) / args.date
    out_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, dict] = {}
    failures: list[str] = []
    for sym in all_symbols():
        try:
            df = data_fetcher.fetch_ohlcv(sym, period_days=args.days)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{sym}: {exc}")
            continue
        if df is None or df.empty:
            failures.append(f"{sym}: empty")
            continue
        path = out_dir / f"{safe_name(sym)}.csv"
        df.to_csv(path, index_label="Date")
        files[path.name] = {
            "symbol": sym,
            "rows": int(len(df)),
            "first": str(df.index[0]),
            "last": str(df.index[-1]),
            "sha256": sha256_file(path),
            "source": str(df.attrs.get("source", "data_fetcher.fetch_ohlcv")),
        }
        print(f"{sym}: {len(df)} rows")
    manifest = {
        "date": args.date,
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "lookback_days": args.days,
        "source": "data_fetcher.fetch_ohlcv",
        "files": files,
        "failures": failures,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))
    print(f"wrote {len(files)} files, {len(failures)} failures -> {out_dir}")
    return 1 if failures and not files else 0


if __name__ == "__main__":
    raise SystemExit(main())
