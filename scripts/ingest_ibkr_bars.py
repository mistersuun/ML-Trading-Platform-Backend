#!/usr/bin/env python3
"""Write raw IBKR ``get_price_history`` JSON into the external bar directory (forward paper test).

    python scripts/ingest_ibkr_bars.py AAPL /path/to/get_price_history_output.json
    cat output.json | python scripts/ingest_ibkr_bars.py AAPL -
    python scripts/ingest_ibkr_bars.py AAPL out.json --topup      # writes AAPL.<last-bar-date>.json (daily top-up)

The default writes ``<SYMBOL>.json`` (full history; replaces the file). ``--topup`` writes a separate
``<SYMBOL>.<YYYYMMDD>.json`` which `external_bars` merges by timestamp, so appending days never rewrites
history. The payload is validated (columns, equal lengths, positive prices, High/Low sanity) before anything is
written. Offline: no network, no IBKR calls.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import external_bars  # noqa: E402

TOPUP_MAX_REL = 0.01
KEEP = ("time", "open", "high", "low", "close", "volume")


def ingest(symbol: str, text: str, topup: bool = False, root=None, asset_class: str = "equity") -> Path:
    """Validate `text` (IBKR JSON) and write it under `root` (default config.EXTERNAL_BARS_DIR). Returns the path."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as e:
        raise external_bars.ExternalBarsError(f"not valid JSON: {e}") from e
    df = external_bars.parse_payload(payload, asset_class)
    problems = external_bars.check_frame(df)
    if problems:
        raise external_bars.ExternalBarsError("; ".join(problems))
    if df.index.duplicated().any():
        raise external_bars.ExternalBarsError("duplicate session dates in payload")
    if topup:                                      # a top-up must agree with the data it overlaps
        try:
            old = external_bars.load_bars(symbol, asset_class, root=root)
        except external_bars.ExternalBarsError:
            old = df.iloc[0:0]
        both = old.index.intersection(df.index)
        if len(both):
            rel = ((df.loc[both, "Close"] / old.loc[both, "Close"]) - 1).abs().max()
            if rel > TOPUP_MAX_REL:
                raise external_bars.ExternalBarsError(
                    f"top-up closes differ from the stored history by {rel:.1%} on overlapping dates "
                    f"(split/adjustment event?): re-fetch the full history (no --topup)")
    clean = {k: payload[k] for k in KEEP}          # drop vendor metadata (expires, source, ...)
    d = Path(root) if root is not None else external_bars.bars_dir()
    d.mkdir(parents=True, exist_ok=True)
    s = external_bars._safe(symbol)
    name = f"{s}.{df.index[-1].strftime('%Y%m%d')}.json" if topup else f"{s}.json"
    dest = d / name
    tmp = dest.with_name(dest.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(clean, separators=(",", ":")))
    os.replace(tmp, dest)
    if not topup:                                  # a fresh full history supersedes (and must not be overridden by) old top-ups
        for p in external_bars.symbol_files(symbol, d):
            if p != dest:
                p.unlink()
    return dest


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("symbol", help="platform symbol, e.g. AAPL, SPY, BTC-USD")
    ap.add_argument("path", nargs="?", default="-", help="JSON file, or - for stdin (default)")
    ap.add_argument("--topup", action="store_true", help="write <SYMBOL>.<YYYYMMDD>.json instead of replacing <SYMBOL>.json")
    ap.add_argument("--asset-class", default="equity", help="equity|etf|index (New York session dates) or crypto|fx|futures (UTC)")
    args = ap.parse_args(argv)
    text = sys.stdin.read() if args.path == "-" else Path(args.path).read_text()
    try:
        dest = ingest(args.symbol, text, topup=args.topup, asset_class=args.asset_class)
    except external_bars.ExternalBarsError as e:
        print(f"error: {args.symbol}: {e}", file=sys.stderr)
        return 1
    df = external_bars.load_bars(args.symbol, args.asset_class)
    print(f"{args.symbol}: wrote {dest.name}; {len(df)} merged bars {df.index[0].date()} .. {df.index[-1].date()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
