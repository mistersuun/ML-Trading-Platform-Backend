"""Single-look walk-forward test of the cross-sectional ML ranker (docs/xs-ranker/PREREG.md) -> docs/xs-ranker/backtest.md.

    .venv/bin/python scripts/xs_ranker_backtest.py

Reads verbatim IBKR daily bars from git-ignored state/xs_bars/<SYM>.json (FIVE_YEARS). Offline.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import xs_ranker as xs  # noqa: E402

import os  # noqa: E402

BARS = Path(os.environ.get("XS_BARS", ROOT / "state" / "xs_bars"))     # override only for a dry run
OUT = Path(os.environ.get("XS_OUT", ROOT / "docs" / "xs-ranker" / "backtest.md"))
SCORES_OUT = OUT.parent / "xs_scores.csv" if "XS_OUT" in os.environ else ROOT / "state" / "xs_scores.csv"            # git-ignored working output
NAMES = {"ml": "ML top 20% (LightGBM)", "ridge": "Ridge top 20%", "momentum": "Momentum top 20% (12-1)",
         "universe": "Universe, equal weight", "spy": "SPY"}


def load() -> tuple[pd.DataFrame, pd.DataFrame]:
    closes, vols = {}, {}
    for p in sorted(BARS.glob("*.json")):
        d = json.loads(p.read_text())
        idx = pd.to_datetime(d["time"], utc=True).tz_convert("America/New_York").tz_localize(None).normalize()
        closes[p.stem] = pd.Series(d["close"], index=idx, dtype=float).groupby(level=0).last()
        vols[p.stem] = pd.Series(d["volume"], index=idx, dtype=float).groupby(level=0).last()
    return pd.DataFrame(closes).sort_index(), pd.DataFrame(vols).sort_index()


def fmt_perf(name: str, r: pd.Series, bench: pd.Series | None, turnover: pd.Series | None) -> str:
    p = xs.perf(r)
    ex = xs.excess_stats(r, bench) if bench is not None else None
    ex_s = f"{ex['mean']:+.2%} (t {ex['t']:.2f})" if ex else "n/a"
    to = f"{turnover.iloc[1:].mean():.0%}" if turnover is not None and len(turnover) > 1 else "n/a"
    return (f"| {name} | {p['cagr']:+.1%} | {p['vol']:.1%} | {p['sharpe']:.2f} | {p['max_dd']:.1%} | {ex_s} | {to} | "
            f"{p['final']:.2f} |")


def main() -> int:
    close, volume = load()
    spy = close.pop("SPY")
    volume = volume.drop(columns=["SPY"])
    spy_dates = spy.dropna().index
    close, volume = close.reindex(spy_dates), volume.reindex(spy_dates)
    dates = xs.month_ends(spy_dates)
    panel = xs.build_panel(close, volume, spy, dates)
    scores = xs.walk_forward(panel)
    books = xs.portfolios(scores)
    scored_dates = sorted(scores.loc[scores["fwd"].notna(), "date"].unique())
    spy_m = pd.Series({d: spy.loc[dates[dates.index(d) + 1]] / spy.loc[d] - 1 for d in scored_dates})
    ics = {k: xs.ic_stats(scores, k) for k in ("ml", "ridge", "momentum")}
    ok, checks = xs.verdict(ics["ml"], books)

    # feature importance of a final model trained on everything with a known target
    from lightgbm import LGBMRegressor
    tr = panel[panel["target"].notna()]
    imp = pd.Series(LGBMRegressor(**xs.LGB_PARAMS).fit(tr[list(xs.FEATURES)].values, tr["target"].values)
                    .feature_importances_, index=xs.FEATURES).sort_values(ascending=False)
    n_elig = panel.groupby("date").size()
    uni = books["universe"].returns

    lines = [
        "# Cross-sectional ML ranker: walk-forward test (single look, rules frozen in PREREG.md before this was run)", "",
        f"Data: {close.shape[1]} US stocks, daily IBKR bars {spy_dates[0].date()} to {spy_dates[-1].date()}. "
        f"Eligible stocks per month: {n_elig.min()} to {n_elig.max()} (median {int(n_elig.median())}). "
        f"Scored months (out of sample): {len(scored_dates)}, {scored_dates[0].date()} to {scored_dates[-1].date()} "
        "(each is the holding month that starts on that month-end).", "",
        f"## Verdict: {'ADOPTED as a paper account' if ok else 'NOT adopted'}", "",
        *[f"- {c}" for c in checks], "",
        "## Ranking skill (monthly rank IC: Spearman correlation of score and next-month return)", "",
        "| Score | Mean IC | t-statistic | Months positive | Months |", "|---|---:|---:|---:|---:|",
        *[f"| {NAMES[k]} | {v['mean']:+.3f} | {v['t']:.2f} | {v['pos']:.0%} | {v['n']} |" for k, v in ics.items()], "",
        "## Portfolios (monthly rebalance at the month-end close, 10 bp per side on traded weight)", "",
        "| Portfolio | CAGR | Volatility | Sharpe | Max drawdown | Monthly excess vs universe | Turnover / month | "
        "Final (start = 1) |", "|---|---:|---:|---:|---:|---:|---:|---:|",
        *[fmt_perf(NAMES[k], books[k].returns, uni if k != "universe" else None, books[k].turnover)
          for k in ("ml", "ridge", "momentum", "universe")],
        fmt_perf(NAMES["spy"], spy_m, uni, None), "",
        "The universe was picked in 2026 and leans to stocks that had already risen, so absolute returns here are "
        "inflated for every line. Judge the ML against the universe and momentum lines, not against SPY.", "",
        "## What the final model leans on (LightGBM split counts)", "",
        "| Feature | Splits |", "|---|---:|", *[f"| {k} | {int(v)} |" for k, v in imp.items()], "",
        "## Month by month (net returns)", "",
        "| Month start | ML | Ridge | Momentum | Universe | ML IC |", "|---|---:|---:|---:|---:|---:|",
        *[f"| {d.date()} | {books['ml'].returns[d]:+.1%} | {books['ridge'].returns[d]:+.1%} | "
          f"{books['momentum'].returns[d]:+.1%} | {uni[d]:+.1%} | {ics['ml']['series'].get(d, float('nan')):+.2f} |"
          for d in scored_dates], "",
    ]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines))
    scores.to_csv(SCORES_OUT, index=False)
    print("\n".join(lines[:30]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
