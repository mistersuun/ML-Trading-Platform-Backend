"""Cut one New York session out of a file-saved IBKR get_price_history payload, without retyping any value.

    .venv/bin/python scripts/slice_session_bars.py <saved_tool_output> <YYYY-MM-DD> <out.json>

A `step_count=1000` intraday fetch is too large to return inline, so the MCP client saves it to a file. This copies
the bars whose New York date is the session, field by field, into a payload of the same shape (the format
`daytrade_paper.py` and `gapgo_paper.py` read). Hand-copying inline payloads introduced typos (2026-10-29).
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
SERIES = ("time", "volume", "open", "close", "high", "low")


def slice_session(payload: dict, session: date) -> dict:
    times = payload["time"]
    keep = [i for i, t in enumerate(times)
            if datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(NY).date() == session]
    if not keep:
        raise ValueError(f"no bars for {session} in the payload")
    out = {k: v for k, v in payload.items() if k not in SERIES}
    step = int(payload.get("chart_step", 300))
    last = datetime.fromisoformat(times[keep[-1]].replace("Z", "+00:00")) + timedelta(seconds=step)
    out["chart_start"] = times[keep[0]]
    out["chart_end"] = last.strftime("%Y-%m-%dT%H:%M:%SZ")
    for k in SERIES:
        out[k] = [payload[k][i] for i in keep]
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    src, session, dst = Path(argv[0]), date.fromisoformat(argv[1]), Path(argv[2])
    out = slice_session(json.loads(src.read_text()), session)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(out, separators=(",", ":")))
    print(f"wrote {dst}: {len(out['time'])} bars {out['chart_start']} .. {out['chart_end']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
