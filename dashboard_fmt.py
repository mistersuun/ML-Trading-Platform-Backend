"""Import-safe formatting / guard helpers for the Streamlit dashboard (Phase 2 return shapes).

Phase 2 made several fields optional (half-life, profit factor, p-values are None when undefined),
pair analysis raises InsufficientData below ``PAIRS_MIN_ALIGNED_BARS`` aligned bars, and stress regimes
carry an ``insufficient`` flag. The dashboard goes through these helpers so None never reaches an f-string.
"""
from __future__ import annotations

import math
from typing import Optional

import config
from pairs_trading import InsufficientData, analyze_pair


def fmt(x, spec: str = ".2f", suffix: str = "", na: str = "n/a") -> str:
    """``format(x, spec) + suffix``; None / non-numeric / non-finite -> ``na``."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return na
    return format(v, spec) + suffix if math.isfinite(v) else na


def safe_analyze_pair(df_a, df_b, sym_a: str, sym_b: str):
    """Align on the common index and analyse. Returns (analysis, aligned_a, aligned_b, warning); on too little
    overlap the analysis is None and `warning` says why (the caller shows st.warning)."""
    common = df_a.index.intersection(df_b.index)
    if len(common) < config.PAIRS_MIN_ALIGNED_BARS:
        return None, None, None, (f"Only {len(common)} overlapping bars for {sym_a}/{sym_b}; "
                                  f"at least {config.PAIRS_MIN_ALIGNED_BARS} are required.")
    a, b = df_a.loc[common], df_b.loc[common]
    try:
        return analyze_pair(a, b, sym_a, sym_b), a, b, None
    except InsufficientData as e:
        return None, None, None, f"Cannot analyse {sym_a}/{sym_b}: {e}"


def pair_metric_texts(analysis) -> dict:
    """Display strings for the pair header metrics; undefined values read 'n/a'."""
    return {
        "coint_p": fmt(analysis.coint_pvalue, ".4f"),
        "correlation": fmt(analysis.correlation, ".3f"),
        "half_life": fmt(analysis.half_life, ".1f", " days"),
        "zscore": fmt(analysis.current_zscore, ".2f"),
        "hedge_ratio": fmt(analysis.hedge_ratio, ".4f"),
        "adf_p": fmt(getattr(analysis, "adf_pvalue", None), ".4f"),
    }


def scanned_pair_texts(p: dict) -> dict:
    """Display strings for one scan_all_pairs result."""
    return {
        "zscore": fmt(p.get("current_zscore"), ".2f"),
        "win_rate": fmt(p.get("win_rate"), ".1%"),
        "half_life": fmt(p.get("half_life"), ".0f", "d"),
        "profit_factor": fmt(p.get("profit_factor"), ".2f"),
    }


def regime_profitability(regime_results: dict) -> tuple[int, int]:
    """(profitable, total) over the regimes that carry enough trades: ``insufficient`` regimes are skipped."""
    ok = total = 0
    for name, r in regime_results.items():
        if name == "full_period" or getattr(r, "insufficient", False):
            continue
        total += 1
        ok += 1 if getattr(r, "total_return_pct", 0.0) > 0 else 0
    return ok, total
