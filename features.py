"""
Feature Engineering — stationary, fixed-schema features from OHLCV data.

Every feature at row t uses only data through bar t. Targets are built by
ml.labels.make_labels and are never part of the feature matrix X.
Columns are identical for every input (volume features are NaN when volume is
zero); +/-inf is mapped to NaN.
"""

import re

import numpy as np
import pandas as pd
from ta import trend, momentum, volatility
import logging

import config
from ml.labels import make_labels

logger = logging.getLogger(__name__)

CONSEC_CAP = 10
LEGACY_TARGET_HORIZONS = (1, 3, 5)
LEGACY_UP_HORIZONS = (1, 3)


def _volume_features(c, h, l, v) -> dict:
    vn = v.where(v > 0)  # zero volume -> NaN so FX/index data gets NaN (not fake) volume features
    out = {}
    for window in [5, 10, 21]:
        out[f"vol_ratio_price_{window}"] = vn / vn.rolling(window).mean()
    mu, sd = vn.rolling(21).mean(), vn.rolling(21).std()
    out["volume_z_21"] = (vn - mu) / sd.replace(0, np.nan)
    signed = np.sign(c.diff()) * vn
    out["obv_slope_10"] = signed.rolling(10).sum() / vn.rolling(10).sum()
    tp = (h + l + c) / 3
    vwap = (vn * tp).rolling(21).sum() / vn.rolling(21).sum()
    out["vwap_dist"] = (c - vwap) / vwap
    return out


def compute_features(df: pd.DataFrame, include_targets: bool = True) -> pd.DataFrame:
    """
    Compute the feature set from OHLCV data (causal: row t uses data through t only).

    include_targets=True appends the legacy `target_*` columns (built by make_labels) for
    inspection and backwards compatibility; ML code must use get_feature_columns() / X
    only. Pass include_targets=False for a pure feature frame.
    """
    feat = {}
    c = df["Close"].astype(float)
    h = df["High"].astype(float)
    l = df["Low"].astype(float)
    o = df["Open"].astype(float)
    v = df["Volume"].astype(float)
    r1 = c.pct_change()

    for period in [1, 2, 3, 5, 10, 21, 63]:
        feat[f"ret_{period}d"] = c.pct_change(period)

    for window in [5, 10, 21, 63]:
        feat[f"vol_{window}d"] = r1.rolling(window).std()
        feat[f"vol_ratio_{window}d"] = r1.rolling(window).std() / r1.rolling(window * 2).std()

    gk = 0.5 * np.log(h / l) ** 2 - (2 * np.log(2) - 1) * np.log(c / o) ** 2
    for window in [10, 21]:
        feat[f"gk_vol_{window}d"] = gk.rolling(window).mean()

    for period in [5, 10, 21, 50, 200]:
        sma = c.rolling(period).mean()
        feat[f"sma_{period}_dist"] = (c - sma) / sma
    for period in [9, 21, 50]:
        ema = trend.ema_indicator(c, window=period)
        feat[f"ema_{period}_dist"] = (c - ema) / ema

    # MACD, price-normalised
    macd_obj = trend.MACD(c)
    feat["macd"] = macd_obj.macd() / c
    feat["macd_signal"] = macd_obj.macd_signal() / c
    feat["macd_hist"] = macd_obj.macd_diff() / c
    feat["macd_hist_change"] = feat["macd_hist"].diff()

    adx_obj = trend.ADXIndicator(h, l, c, window=14)
    feat["adx"] = adx_obj.adx()
    feat["di_plus"] = adx_obj.adx_pos()
    feat["di_minus"] = adx_obj.adx_neg()
    feat["di_diff"] = feat["di_plus"] - feat["di_minus"]

    for w in (14, 7, 21):
        feat[f"rsi_{w}"] = momentum.rsi(c, window=w)
    stoch = momentum.StochasticOscillator(h, l, c, window=14, smooth_window=3)
    feat["stoch_k"] = stoch.stoch()
    feat["stoch_d"] = stoch.stoch_signal()
    feat["williams_r"] = momentum.williams_r(h, l, c, lbp=14)
    for period in [5, 10, 21]:
        feat[f"roc_{period}"] = momentum.roc(c, window=period)

    bb = volatility.BollingerBands(c, window=20, window_dev=2)
    feat["bb_width"] = (bb.bollinger_hband() - bb.bollinger_lband()) / bb.bollinger_mavg()
    feat["bb_pct"] = bb.bollinger_pband()

    for window in [7, 14, 21]:  # ATR as a fraction of price
        feat[f"atr_{window}_pct"] = volatility.average_true_range(h, l, c, window=window) / c

    for window in [20, 50]:
        dc_high = h.rolling(window).max()
        dc_low = l.rolling(window).min()
        feat[f"dc_{window}_pct"] = (c - dc_low) / (dc_high - dc_low + 1e-10)

    feat.update(_volume_features(c, h, l, v))

    rng = h - l + 1e-10
    feat["body_size"] = (c - o).abs() / rng
    feat["upper_shadow"] = (h - pd.concat([c, o], axis=1).max(axis=1)) / rng
    feat["lower_shadow"] = (pd.concat([c, o], axis=1).min(axis=1) - l) / rng
    bullish = (c > o).astype(int)
    feat["is_bullish"] = bullish
    g_bull = (bullish != bullish.shift()).cumsum()
    feat["consec_bullish"] = (bullish * 1).groupby(g_bull).cumsum().where(bullish == 1, 0).clip(upper=CONSEC_CAP)
    bearish = 1 - bullish
    g_bear = (bearish != bearish.shift()).cumsum()
    feat["consec_bearish"] = bearish.groupby(g_bear).cumsum().clip(upper=CONSEC_CAP)

    for window in [21, 63]:
        feat[f"skew_{window}"] = r1.rolling(window).skew()
        feat[f"kurt_{window}"] = r1.rolling(window).kurt()

    out = pd.DataFrame(feat, index=df.index).replace([np.inf, -np.inf], np.nan)

    if include_targets:
        for hz in LEGACY_TARGET_HORIZONS:
            lab = make_labels(df, hz)
            out[f"target_{hz}d"] = lab["fwd_ret"]
            if hz in LEGACY_UP_HORIZONS:
                out[f"target_up_{hz}d"] = lab["y"]
    return out


def get_feature_columns(feat_df: pd.DataFrame) -> list[str]:
    """Return only feature columns (exclude targets)."""
    return [c for c in feat_df.columns if not c.startswith("target_")]


def horizon_from_target(target_col: str) -> int:
    m = re.fullmatch(r"target_up_(\d+)d", target_col)
    return int(m.group(1)) if m else config.ML_HORIZON_BARS


def prepare_ml_data(
    df: pd.DataFrame, target_col: str = "target_up_1d", horizon: int | None = None,
    dead_cutoff: int | None = None,
) -> tuple[pd.DataFrame, pd.Series, list[str]]:
    """
    Feature matrix X and label y for ML training.

    Rows with unresolved/zero labels or NaN features are dropped. Feature columns that are
    entirely NaN (volume features on zero-volume data) are filled with 0.0 so the schema is
    identical across instruments. No target_ column can reach X.

    `dead_cutoff`: when given, "entirely NaN" is decided on the first `dead_cutoff` rows only (the history
    before the out-of-sample start), so training and prediction use the same schema.
    """
    hz = horizon if horizon is not None else horizon_from_target(target_col)
    feat = compute_features(df, include_targets=False)
    feature_cols = get_feature_columns(feat)
    X = feat[feature_cols]
    dead = X.columns[(X.iloc[:dead_cutoff] if dead_cutoff is not None else X).isna().all()]
    X = X.copy()
    X[dead] = 0.0
    y = make_labels(df, hz)["y"]
    ok = X.notna().all(axis=1) & y.notna()
    X, y = X[ok], y[ok].astype(int)
    assert not any(c.startswith("target_") for c in X.columns), "target column leaked into X"
    assert np.isfinite(X.to_numpy(float)).all()
    return X, y, feature_cols
