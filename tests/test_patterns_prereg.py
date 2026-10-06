"""Fixture tests for the pre-registered patterns (docs/research/preregistration-2026-10.md section 3)."""
import ast
import inspect
import re
import textwrap
from pathlib import Path

import numpy as np
import pandas as pd

import patterns
import patterns_prereg as PP
import pooled_validation as PV


def _frame(close, start="2020-01-01"):
    close = np.asarray(close, dtype=float)
    idx = pd.bdate_range(start, periods=len(close))
    return pd.DataFrame({"Open": close, "High": close * 1.01, "Low": close * 0.99, "Close": close,
                         "Volume": 1e6}, index=idx)


def _sig(fn, close):
    return fn(_frame(close))["signal"]


# ---------------------------------------------------------------- P1
def _p1_series():
    up = list(np.linspace(100, 200, 260))          # long uptrend: close > SMA200 from bar 199 on
    return up + [199, 197, 194, 190]               # four down closes: RSI(2) collapses


def test_p1_entry_dates_exact():
    close = np.array(_p1_series())
    s = _sig(PP.rsi2_pullback, close)
    import ta
    rsi2 = ta.momentum.rsi(pd.Series(close), window=2, fillna=False).to_numpy()
    sma200 = pd.Series(close).rolling(200, min_periods=200).mean().to_numpy()
    expect = np.where(np.isfinite(sma200) & (close > sma200) & (rsi2 < 10), 1, 0)
    assert (s.to_numpy() == 1).sum() >= 1
    assert (s.to_numpy()[expect == 1] == 1).all()
    assert (s.to_numpy() == 1).tolist() == (expect == 1).tolist()
    # first signal is on the first bar where RSI(2) < 10 (a late bar, in the pullback), never during the pure rally
    first = int(np.argmax(s.to_numpy() == 1))
    assert first >= 260


def test_p1_trend_filter_blocks_entry_below_sma200():
    down = list(np.linspace(200, 100, 260)) + [99, 97, 94, 90]     # downtrend: close < SMA200, RSI(2) tiny
    s = _sig(PP.rsi2_pullback, down)
    assert (s == 1).sum() == 0


def test_p1_exit_is_close_above_sma5_and_entry_wins():
    close = np.array(_p1_series() + [195, 200, 205])
    s = _sig(PP.rsi2_pullback, close).to_numpy()
    sma5 = pd.Series(close).rolling(5, min_periods=5).mean().to_numpy()
    for i in range(len(close)):
        if s[i] == -1:
            assert close[i] > sma5[i] and s[i] != 1
    assert (s == -1).any()                          # the rebound closes above SMA(5)


def test_p1_nan_warmup_gives_no_signal():
    s = _sig(PP.rsi2_pullback, np.linspace(100, 50, 150))           # < 200 bars: SMA200 NaN
    assert (s == 1).sum() == 0


def test_p1_no_lookahead():
    close = np.array(_p1_series() + [195, 200, 205, 190, 185])
    full = _sig(PP.rsi2_pullback, close).to_numpy()
    for t in range(210, len(close)):
        part = _sig(PP.rsi2_pullback, close[: t + 1]).to_numpy()
        assert part[t] == full[t]                                   # a signal on D uses data through D's close only
        assert (part == full[: t + 1]).all()


# ---------------------------------------------------------------- P2
def test_p2_crossing_date_exact_and_only_first_close():
    close = np.concatenate([100 + 5 * np.sin(np.arange(300) / 20.0)])   # bounded: max ~105
    close = np.concatenate([close, [106, 107, 108, 100, 99, 109]])      # 106 crosses; 107,108 stay above; 109 crosses again
    s = _sig(PP.high52_breakout, close).to_numpy()
    assert np.flatnonzero(s == 1).tolist() == [300, 305]
    assert (s == -1).sum() == 0                                         # no signal exit by design


def test_p2_prior_window_excludes_today_and_equal_close_is_not_a_cross():
    close = np.concatenate([np.linspace(100, 110, 252), [110.0]])       # equals prior max: not strictly above
    s = _sig(PP.high52_breakout, close).to_numpy()
    assert (s == 1).sum() == 0
    close2 = np.concatenate([np.linspace(100, 110, 252), [110.01]])
    assert np.flatnonzero(_sig(PP.high52_breakout, close2).to_numpy() == 1).tolist() == [252]


def test_p2_needs_252_prior_bars():
    s = _sig(PP.high52_breakout, np.linspace(100, 150, 252))            # only 251 prior bars at the last bar
    assert (s == 1).sum() == 0


def test_p2_no_lookahead():
    rng = np.random.default_rng(3)
    close = 100 * np.exp(np.cumsum(rng.normal(0.001, 0.01, 600)))
    full = _sig(PP.high52_breakout, close).to_numpy()
    for t in (260, 300, 450, 599):
        part = _sig(PP.high52_breakout, close[: t + 1]).to_numpy()
        assert (part == full[: t + 1]).all()


# ---------------------------------------------------------------- engine: signal on D enters at the next open
def test_signal_on_d_enters_at_the_next_open_through_the_engine():
    import validation as V
    close = np.linspace(100, 160, 600)                       # strictly rising: the one crossing is bar 252
    df = _frame(close)
    df["Open"] = df["Close"] * 1.002
    end = df.index[-1] + pd.Timedelta(days=1)
    sig = PP.high52_breakout(df)["signal"]
    d = int(np.flatnonzero(sig.to_numpy() == 1)[0])
    assert d == 252
    cand = V.executable_candidates(["high52_breakout"])[0]
    wf = V.walk_forward(df, [cand], patterns=PP.PREREG_PATTERNS, train=252, test=126, step=126, end=end)
    assert wf.oos_trades, "the crossing must produce a trade"
    assert wf.oos_trades[0].entry_index == d + 1             # filled on the bar AFTER the signal bar


# ---------------------------------------------------------------- registration and isolation
def test_registered_in_pooled_family_only():
    assert set(PP.PREREG_PATTERNS) == {"rsi2_pullback", "high52_breakout"}
    assert not (set(PP.PREREG_PATTERNS) & set(patterns.PATTERN_REGISTRY))     # per-symbol scan untouched
    reg = PV.pooled_registry()
    assert set(PP.PREREG_PATTERNS) <= set(reg)
    names = PV.pooled_pattern_names()
    assert len(names) == 21 and "macd_hist_reversal" not in names
    assert PV.pooled_universe_trials() == 22                                  # retired stays in N; +2 pre-registered


def test_per_symbol_universe_trials_unchanged():
    import validation
    assert validation.universe_trials() == 780


def test_code_matches_the_preregistration_document():
    """Integrity (pre-registration 7.6): the implemented functions equal the code blocks in the document."""
    doc = (Path(__file__).resolve().parents[1] / "docs/research/preregistration-2026-10.md").read_text()
    blocks = re.findall(r"```python\n(.*?)```", doc, flags=re.S)

    def canon(src):
        fn = ast.parse(textwrap.dedent(src)).body[0]
        if ast.get_docstring(fn):
            fn.body = fn.body[1:]
        fn.returns = None
        for a in fn.args.args:
            a.annotation = None
        return ast.dump(fn)

    doc_fns = {canon(b) for b in blocks}
    for fn in (PP.rsi2_pullback, PP.high52_breakout):
        assert canon(inspect.getsource(fn)) in doc_fns, fn.__name__


def test_the_frozen_preregistration_hash_in_d19_matches_the_file():
    """D19 freezes the SHA-256 of the pre-registration; editing the file without a new decision entry fails here."""
    import hashlib
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    decisions = (root / "docs" / "decisions.md").read_text()
    d19 = decisions[decisions.index("## D19"):]
    m = re.search(r"SHA-256 of `docs/research/preregistration-2026-10.md`[^`]*`([0-9a-f]{64})`", d19)
    assert m, "D19 must record the SHA-256 of the pre-registration"
    digest = hashlib.sha256((root / "docs" / "research" / "preregistration-2026-10.md").read_bytes()).hexdigest()
    assert m.group(1) == digest
