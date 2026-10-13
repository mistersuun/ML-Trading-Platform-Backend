"""Write the FastAPI OpenAPI schema to ``openapi.json`` (repo root), deterministically (sorted keys).

    uv run python scripts/export_openapi.py          # regenerate the committed file
    uv run python scripts/export_openapi.py --check  # exit 1 if it is out of date
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OPENAPI_PATH = ROOT / "openapi.json"


def render() -> str:
    from server import app
    return json.dumps(app.openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    text = render()
    if "--check" in argv:
        current = OPENAPI_PATH.read_text(encoding="utf-8") if OPENAPI_PATH.exists() else ""
        if current != text:
            print("openapi.json is out of date: run `uv run python scripts/export_openapi.py`", file=sys.stderr)
            return 1
        return 0
    OPENAPI_PATH.write_text(text, encoding="utf-8")
    print(f"wrote {OPENAPI_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
