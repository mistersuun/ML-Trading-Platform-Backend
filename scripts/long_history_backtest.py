"""Long-history check of the core / core+trend design on proxy total-return series.

Owner-run (needs data the sandbox cannot download). Expected input: a directory with one
CSV per proxy, named <PROXY>.csv, header ``date,total_return_index`` (any frequency; the
last value in each month is used; the index level is a total-return index, e.g. adjusted
close / mutual-fund NAV with distributions reinvested). Proxies:

    US equity     VFINX or VTSMX     dev ex-US   VGTSX or EAFE   EM   VEIEX
    IEF           VFITX              TLT         VUSTX           TIPS VIPSX (2000+)
    REIT (opt.)   VGSIX or NAREIT    gold        GOLD            commodities COMMOD
    T-bills       DTB3 (FRED, header ``date,yield_pct``; or a TBILL total_return_index file)

Where a proxy has not started yet, a fallback fills in (EM->dev ex-US->US, TIPS->IEF,
commodities->gold, REIT->US); the report lists every substitution.

    uv run python scripts/long_history_backtest.py /path/to/csvs --out report.md
    uv run python scripts/long_history_backtest.py --synthetic --out /tmp/report.md
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import allocation  # noqa: E402
from portfolio_backtester import backtest_weights, stats, trend_sleeve  # noqa: E402

# sleeve -> candidate proxy files, in priority order (later ones fill earlier history)
PROXIES = {"US": ["VFINX", "VTSMX"], "DEV": ["VGTSX", "EAFE"], "EM": ["VEIEX"],
           "IEF": ["VFITX"], "TLT": ["VUSTX"], "TIPS": ["VIPSX"], "TBILL": ["TBILL", "DTB3"],
           "GOLD": ["GOLD"], "COMMOD": ["COMMOD"], "REIT": ["VGSIX", "NAREIT"]}
FALLBACK = {"DEV": "US", "EM": "DEV", "TIPS": "IEF", "COMMOD": "GOLD", "REIT": "US"}
REQUIRED = ["US", "IEF", "TLT", "TBILL", "GOLD"]
CORE_SLEEVE = {"VTI": "US", "VEA": "DEV", "VWO": "EM", "IEF": "IEF", "TLT": "TLT",
               "TIP": "TIPS", "SGOV": "TBILL", "GLDM": "GOLD", "PDBC": "COMMOD"}
TREND_SLEEVE = {"SPY": "US", "VEA": "DEV", "IEF": "IEF", "GLD": "GOLD", "DBC": "COMMOD", "VNQ": "REIT"}
WINDOWS = {"2000-02": ("2000-01", "2002-12"), "2008": ("2008-01", "2008-12"),
           "2013": ("2013-01", "2013-12"), "2020": ("2020-01", "2020-12"),
           "2022": ("2022-01", "2022-12"), "1980-2000": ("1980-01", "2000-12")}


def _month_series(df: pd.DataFrame) -> pd.Series:
    dates = pd.to_datetime(df.iloc[:, 0])
    s = pd.Series(pd.to_numeric(df.iloc[:, 1], errors="coerce").to_numpy(), index=dates).dropna()
    s.index = s.index.to_period("M")
    return s[~s.index.duplicated(keep="last")].sort_index()


def load_returns(csv_dir: Path) -> dict[str, pd.Series]:
    """proxy name -> monthly total return series (decimal)."""
    out = {}
    for p in sorted(Path(csv_dir).glob("*.csv")):
        df = pd.read_csv(p)
        s = _month_series(df)
        if "yield" in str(df.columns[1]).lower():      # annualised yield in percent
            r = s / 100.0 / 12.0
        else:
            r = s.pct_change().dropna()
        out[p.stem.upper()] = r
    return out


def build_sleeves(proxy_returns: dict[str, pd.Series]) -> tuple[pd.DataFrame, list[str]]:
    """Splice proxies into one return column per sleeve; apply fallbacks. Returns (df, notes)."""
    notes, cols = [], {}
    for sl, names in PROXIES.items():
        parts = [proxy_returns[n] for n in names if n in proxy_returns]
        if not parts:
            continue
        s = parts[0]
        for extra in parts[1:]:
            s = s.combine_first(extra)
        cols[sl] = s.sort_index()
    for sl in REQUIRED:
        if sl not in cols:
            raise SystemExit(f"missing required proxy for {sl}: one of {PROXIES[sl]}")
    start = max(cols[sl].index[0] for sl in REQUIRED)
    end = min(cols[sl].index[-1] for sl in REQUIRED)
    idx = pd.period_range(start, end, freq="M")
    for sl, fb in FALLBACK.items():
        base = cols.get(sl)
        filler = cols.get(fb) if fb in cols else None
        if base is None:
            cols[sl] = filler
            notes.append(f"{sl}: no proxy supplied, using {fb} for the whole sample")
        elif filler is not None:
            first = base.index[0]
            if first > start:
                cols[sl] = base.combine_first(filler[filler.index < first])
                notes.append(f"{sl}: using {fb} before {first}")
    df = pd.DataFrame({k: v.reindex(idx) for k, v in cols.items()}).dropna(axis=0, how="any")
    return df, notes


def run_strategies(sl: pd.DataFrame) -> dict[str, pd.Series]:
    core_w: dict[str, float] = {}
    for sym, w in allocation.CORE_TARGETS.items():
        core_w[CORE_SLEEVE[sym]] = core_w.get(CORE_SLEEVE[sym], 0.0) + w
    core = backtest_weights(sl, core_w, rebalance="bands").returns
    assets = list(TREND_SLEEVE.values())
    prices = (1 + sl[assets]).cumprod()
    trend = trend_sleeve(prices, sl, assets, cash="TBILL")
    mix = pd.DataFrame({"core": core, "trend": trend})
    both = backtest_weights(mix, {"core": allocation.CORE_PCT, "trend": allocation.TREND_SLEEVE_PCT},
                            rebalance="monthly").returns
    return {"core": core, "core+trend": both,
            "60/40": backtest_weights(sl, {"US": 0.6, "IEF": 0.4}, rebalance="monthly").returns,
            "100% equity": sl["US"].rename("equity")}


def _fmt(st: dict | None, key: str) -> str:
    return "n/a" if st is None or not np.isfinite(st[key]) else f"{st[key] * 100:.1f}%" \
        if key != "sharpe" else f"{st[key]:.2f}"


def make_report(sl: pd.DataFrame, notes: list[str]) -> str:
    strat = run_strategies(sl)
    rf = sl["TBILL"]
    L = ["# Long-history proxy backtest", "",
         f"Sample {sl.index[0]} to {sl.index[-1]} ({len(sl)} months). Monthly total returns; "
         "core uses 5/25 bands, core+trend is 90/10 rebalanced monthly. No costs or taxes.", ""]
    if notes:
        L += ["Substitutions:"] + [f"- {n}" for n in notes] + [""]
    L += ["## Full sample", "", "| Strategy | CAGR | Vol | Max DD | Excess Sharpe (vs T-bills) | Worst year |",
          "|---|---|---|---|---|---|"]
    for name, r in strat.items():
        st = stats(r, rf)
        L.append(f"| {name} | {_fmt(st, 'cagr')} | {_fmt(st, 'vol')} | {_fmt(st, 'max_drawdown')} "
                 f"| {_fmt(st, 'sharpe')} | {_fmt(st, 'worst_year')} |")
    L += ["", "## Named windows (total return / max drawdown within window)", "",
          "| Window | " + " | ".join(strat) + " |", "|---|" + "---|" * len(strat)]
    for wname, (a, b) in WINDOWS.items():
        cells = []
        for r in strat.values():
            w = r[(r.index >= pd.Period(a, "M")) & (r.index <= pd.Period(b, "M"))]
            if len(w) < 6:
                cells.append("n/a")
                continue
            tot = (1 + w).prod() - 1
            dd = stats(w)["max_drawdown"]
            cells.append(f"{tot * 100:.1f}% / {dd * 100:.1f}%")
        L.append(f"| {wname} | " + " | ".join(cells) + " |")
    L += ["", "Windows with fewer than 6 months of data show n/a. Proxy series are not the ETFs: "
          "treat results as a sanity check on drawdown and regime behaviour, not a forecast."]
    return "\n".join(L) + "\n"


def synthetic_returns(seed: int = 7) -> dict[str, pd.Series]:
    """Seeded fake proxy returns (different start dates to exercise the fallbacks)."""
    rng = np.random.default_rng(seed)
    spec = {  # name: (start, mean/yr, vol/yr)
        "VFINX": ("1976-01", 0.10, 0.15), "VTSMX": ("1992-01", 0.10, 0.15),
        "VGTSX": ("1990-06", 0.07, 0.17), "VEIEX": ("1994-06", 0.08, 0.22),
        "VFITX": ("1976-01", 0.05, 0.05), "VUSTX": ("1976-01", 0.06, 0.11),
        "VIPSX": ("2000-06", 0.04, 0.06), "GOLD": ("1976-01", 0.05, 0.16),
        "COMMOD": ("1991-01", 0.03, 0.15), "TBILL": ("1976-01", 0.035, 0.005)}
    end = pd.Period("2025-12", "M")
    out = {}
    for name, (start, mu, vol) in spec.items():
        idx = pd.period_range(start, end, freq="M")
        out[name] = pd.Series(rng.normal(mu / 12, vol / 12 ** 0.5, len(idx)).clip(-0.5, 0.5), index=idx)
    out["TBILL"] = out["TBILL"].clip(lower=0)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv_dir", nargs="?", help="directory of <PROXY>.csv files")
    ap.add_argument("--synthetic", action="store_true", help="use seeded fake proxies (smoke test)")
    ap.add_argument("--out", help="write the markdown report here (default: stdout)")
    a = ap.parse_args(argv)
    if a.synthetic:
        pr = synthetic_returns()
    elif a.csv_dir:
        pr = load_returns(Path(a.csv_dir))
    else:
        ap.error("give a csv_dir or --synthetic")
    sl, notes = build_sleeves(pr)
    rep = make_report(sl, notes)
    if a.synthetic:
        rep = "> SYNTHETIC DATA - smoke test only, numbers are meaningless.\n\n" + rep
    if a.out:
        Path(a.out).write_text(rep)
    else:
        print(rep)
    return 0


if __name__ == "__main__":
    sys.exit(main())
