"""Cross-sectional ML stock ranker (docs/xs-ranker/PREREG.md). Research and paper only: it ranks stocks, never trades.

Pure functions on wide DataFrames (index = trading dates, columns = symbols):
    features_on(close, volume, spy, date)   -> ranked feature table for one rebalance date
    build_panel(close, volume, spy, dates)  -> long panel of ranked features and the ranked next-month target
    walk_forward(panel, ...)                -> out-of-sample scores per date (ML, ridge, momentum)
    portfolios(scores, close, dates, ...)   -> monthly returns of the top-20% portfolios, the universe, and turnover
    summary(...)                            -> IC and performance statistics
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

MIN_BARS, MIN_PRICE, MIN_DOLLAR_VOL = 252, 5.0, 10e6
MIN_TRAIN_DATES = 12
TOP_FRAC, COST = 0.20, 0.0010
FEATURES = ("r21", "r63", "r126", "r252", "mom_12_1", "vol21", "vol63", "max21", "dist_high", "dollar_vol",
            "vol_trend", "beta", "idio_vol")
LGB_PARAMS = dict(n_estimators=300, learning_rate=0.03, num_leaves=15, min_child_samples=100, subsample=0.8,
                  subsample_freq=1, colsample_bytree=0.8, random_state=7, verbose=-1)


def month_ends(index: pd.DatetimeIndex) -> list[pd.Timestamp]:
    s = pd.Series(index, index=index)
    return list(s.groupby([index.year, index.month]).max())


def _rank(s: pd.Series) -> pd.Series:
    return s.rank(pct=True).fillna(0.5)


def features_on(close: pd.DataFrame, volume: pd.DataFrame, spy: pd.Series, date: pd.Timestamp) -> pd.DataFrame:
    """Raw features of the eligible stocks on `date` (data up to and including it), ranked cross-sectionally."""
    c = close.loc[:date]
    v = volume.loc[:date]
    if len(c) < MIN_BARS + 1:                       # a 252-day return needs 253 closes
        return pd.DataFrame(columns=FEATURES)
    n_bars = c.notna().sum()
    last = c.iloc[-1]
    dv21 = (c * v).iloc[-21:].mean()
    elig = n_bars.index[(n_bars >= MIN_BARS + 1) & (last >= MIN_PRICE) & (dv21 >= MIN_DOLLAR_VOL)]
    if len(elig) == 0:
        return pd.DataFrame(columns=FEATURES)
    c, v = c[elig], v[elig]
    r = c.pct_change(fill_method=None)
    m = r.iloc[-252:]
    s = spy.loc[:date].pct_change(fill_method=None).iloc[-252:].reindex(m.index)
    s_var = s.var()
    beta = m.apply(lambda col: col.cov(s) / s_var if s_var > 0 else np.nan)
    resid = m.sub(s.values[:, None] * beta.values[None, :])

    def ret(k):
        return c.iloc[-1] / c.iloc[-1 - k] - 1

    raw = pd.DataFrame({
        "r21": ret(21), "r63": ret(63), "r126": ret(126), "r252": ret(252),
        "mom_12_1": c.iloc[-22] / c.iloc[-253] - 1,
        "vol21": r.iloc[-21:].std(), "vol63": r.iloc[-63:].std(), "max21": r.iloc[-21:].max(),
        "dist_high": c.iloc[-1] / c.iloc[-252:].max() - 1,
        "dollar_vol": np.log(dv21[elig]),
        "vol_trend": v.iloc[-21:].mean() / v.iloc[-126:].mean(),
        "beta": beta, "idio_vol": resid.std(),
    })
    return raw.apply(_rank)


def build_panel(close: pd.DataFrame, volume: pd.DataFrame, spy: pd.Series, dates: list[pd.Timestamp]) -> pd.DataFrame:
    """Long panel: one row per (date, symbol) with ranked features, the raw next-period return and its rank.
    The last date has no target (NaN)."""
    rows = []
    for i, d in enumerate(dates):
        f = features_on(close, volume, spy, d)
        if f.empty:
            continue
        f = f.copy()
        if i + 1 < len(dates):
            fwd = close.loc[dates[i + 1], f.index] / close.loc[d, f.index] - 1
        else:
            fwd = pd.Series(np.nan, index=f.index)
        f["fwd"] = fwd
        f["target"] = fwd.rank(pct=True)
        f["date"] = d
        rows.append(f.rename_axis("symbol").reset_index())
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def walk_forward(panel: pd.DataFrame, min_train_dates: int = MIN_TRAIN_DATES, models=("ml", "ridge")) -> pd.DataFrame:
    """Out-of-sample scores. On date t a model is trained on rows of dates strictly before the previous date (their
    next-month target closed on or before t's previous month-end: a one-month purge), then scores t."""
    from lightgbm import LGBMRegressor
    from sklearn.linear_model import Ridge

    dates = sorted(panel["date"].unique())
    out = []
    for k, d in enumerate(dates):
        train_dates = dates[: max(k - 1, 0)]                 # purge: date k-1's target ends on date k
        if len(train_dates) < min_train_dates:
            continue
        tr = panel[panel["date"].isin(train_dates) & panel["target"].notna()]
        te = panel[panel["date"] == d]
        X, y = tr[list(FEATURES)].values, tr["target"].values
        res = te[["date", "symbol", "fwd", "mom_12_1"]].copy()
        if "ml" in models:
            res["ml"] = LGBMRegressor(**LGB_PARAMS).fit(X, y).predict(te[list(FEATURES)].values)
        if "ridge" in models:
            res["ridge"] = Ridge(alpha=1.0).fit(X, y).predict(te[list(FEATURES)].values)
        res["momentum"] = res.pop("mom_12_1")
        out.append(res)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


@dataclass
class Book:
    returns: pd.Series          # net monthly returns, indexed by the holding period's start date
    turnover: pd.Series         # traded weight per rebalance (one side)


def portfolios(scores: pd.DataFrame, score_cols=("ml", "ridge", "momentum"), top_frac: float = TOP_FRAC,
               cost: float = COST) -> dict[str, Book]:
    """Equal-weight top-fraction portfolios per score column, plus the equal-weight universe. Costs: `cost` per side
    on traded weight, charged in the month of the trade. Dates without a realised forward return are skipped."""
    books = {}
    scored = scores[scores["fwd"].notna()]
    for col in (*score_cols, "universe"):
        prev = pd.Series(dtype=float)
        rets, turns = {}, {}
        for d, g in scored.groupby("date"):
            if col == "universe":
                picks = g["symbol"]
            else:
                n = max(1, int(round(len(g) * top_frac)))
                picks = g.nlargest(n, col)["symbol"]
            w = pd.Series(1.0 / len(picks), index=picks.values)
            allsym = w.index.union(prev.index)
            traded = (w.reindex(allsym, fill_value=0) - prev.reindex(allsym, fill_value=0)).abs().sum()
            gross = float((g.set_index("symbol").loc[w.index, "fwd"] * w).sum())
            rets[d] = gross - cost * traded
            turns[d] = traded / 2 if len(prev) else traded
            # weights drift with returns until the next rebalance
            grown = w * (1 + g.set_index("symbol").loc[w.index, "fwd"])
            prev = grown / grown.sum()
        books[col] = Book(pd.Series(rets).sort_index(), pd.Series(turns).sort_index())
    return books


def ic_stats(scores: pd.DataFrame, col: str) -> dict:
    s = scores[scores["fwd"].notna()]
    ic = s.groupby("date").apply(lambda g: g[col].corr(g["fwd"], method="spearman"), include_groups=False).dropna()
    n = len(ic)
    t = ic.mean() / (ic.std(ddof=1) / np.sqrt(n)) if n > 1 and ic.std(ddof=1) > 0 else np.nan
    return {"mean": float(ic.mean()) if n else np.nan, "t": float(t), "pos": float((ic > 0).mean()) if n else np.nan,
            "n": n, "series": ic}


def perf(r: pd.Series, periods: int = 12) -> dict:
    eq = (1 + r).cumprod()
    years = len(r) / periods
    cagr = eq.iloc[-1] ** (1 / years) - 1 if years > 0 else np.nan
    vol = r.std(ddof=1) * np.sqrt(periods)
    return {"cagr": float(cagr), "vol": float(vol), "sharpe": float(r.mean() * periods / vol) if vol > 0 else np.nan,
            "max_dd": float((eq / eq.cummax() - 1).min()), "final": float(eq.iloc[-1])}


def excess_stats(r: pd.Series, bench: pd.Series) -> dict:
    x = (r - bench.reindex(r.index)).dropna()
    t = x.mean() / (x.std(ddof=1) / np.sqrt(len(x))) if len(x) > 1 and x.std(ddof=1) > 0 else np.nan
    return {"mean": float(x.mean()), "t": float(t), "pos": float((x > 0).mean())}


def verdict(ic_ml: dict, books: dict[str, Book]) -> tuple[bool, list[str]]:
    """The three pre-registered conditions."""
    ml, mom, uni = (perf(books[k].returns) for k in ("ml", "momentum", "universe"))
    ex = excess_stats(books["ml"].returns, books["universe"].returns)
    checks = [
        (ic_ml["t"] >= 2.0, f"ML mean monthly IC t-statistic {ic_ml['t']:.2f} (needs 2.0 or more)"),
        (ml["sharpe"] > mom["sharpe"], f"ML top 20% Sharpe {ml['sharpe']:.2f} vs momentum top 20% {mom['sharpe']:.2f}"),
        (ex["mean"] > 0, f"ML top 20% mean monthly excess over the universe {ex['mean']:+.2%}"),
    ]
    return all(ok for ok, _ in checks), [("PASS " if ok else "FAIL ") + msg for ok, msg in checks]
