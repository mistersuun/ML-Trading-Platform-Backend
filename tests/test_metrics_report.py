import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import metrics_report as mr  # noqa: E402

BASELINE = ROOT / "docs" / "baseline" / "synthetic_metrics.csv"


def test_synthetic_run_reproduces_committed_csv():
    """Numeric (not byte-for-byte) comparison so a different CPU/libm cannot flake CI."""
    import io

    import numpy as np
    new = pd.read_csv(io.StringIO(mr.synthetic_csv()))
    old = pd.read_csv(BASELINE)
    assert list(new.columns) == list(old.columns) and len(new) == len(old)
    for col in old.columns:
        if pd.api.types.is_float_dtype(old[col]):
            assert np.allclose(new[col], old[col], rtol=1e-9, atol=1e-9, equal_nan=True), col
        else:
            assert (new[col] == old[col]).all(), col


def test_baseline_shape():
    df = pd.read_csv(BASELINE)
    from patterns import PATTERN_REGISTRY
    assert list(df.columns) == mr.COLUMNS
    assert len(df) == len(mr.SYNTHETIC_SYMBOLS) * len(PATTERN_REGISTRY)


def test_diff_reports_changed_rows(capsys):
    old = pd.read_csv(BASELINE)
    new = old.copy()
    new.loc[3, "total_trades"] += 1
    new.loc[7, "sharpe"] += 0.5
    d = mr.diff_reports(old, new)
    assert len(d) == 2
    assert mr.diff_reports(old, old).empty


def test_diff_cli(tmp_path, capsys):
    old = pd.read_csv(BASELINE)
    new = old.copy()
    new.loc[0, "win_rate"] = 0.99
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    old.to_csv(a, index=False)
    new.to_csv(b, index=False)
    mr.main(["--diff", str(a), str(b)])
    assert "1 changed rows" in capsys.readouterr().out
