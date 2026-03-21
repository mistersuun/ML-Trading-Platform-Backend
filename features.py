"""
Feature Engineering — computes features from OHLCV data for ML models.

All features are lagged to prevent look-ahead bias.
Returns a DataFrame of features aligned with the input index.
"""

import numpy as np
import pandas as pd
from ta import trend, momentum, volatility, volume as ta_vol
import logging

import config

logger = logging.getLogger(__name__)


def compute_features(df: pd.DataFrame, lookback: int = config.ML_LOOKBACK_BARS) -> pd.DataFrame:
    """
    Compute a comprehensive feature set from OHLCV data.
    All features use only past data (no look-ahead bias).
    """
    feat = pd.DataFrame(index=df.index)
    c = df["Close"]
    h = df["High"]
    l = df["Low"]
    o = df["Open"]
    v = df["Volume"]

    # ── Price Returns ──
    for period in [1, 2, 3, 5, 10, 21, 63]:
        feat[f"ret_{period}d"] = c.pct_change(period)

    # ── Volatility Features ──
    for window in [5, 10, 21, 63]:
        feat[f"vol_{window}d"] = c.pct_change().rolling(window).std()
        feat[f"vol_ratio_{window}d"] = (
            c.pct_change().rolling(window).std() /
            c.pct_change().rolling(window * 2).std()
        )

    # Garman-Klass volatility (uses OHLC — more efficient estimator)
    for window in [10, 21]:
        gk = 0.5 * np.log(h / l) ** 2 - (2 * np.log(2) - 1) * np.log(c / o) ** 2
        feat[f"gk_vol_{window}d"] = gk.rolling(window).mean()

    # ── Trend Indicators ──
    for period in [5, 10, 21, 50, 200]:
        sma = c.rolling(period).mean()
        feat[f"sma_{period}_dist"] = (c - sma) / sma  # Distance from SMA as %

    # EMA features
    for period in [9, 21, 50]:
        ema = trend.ema_indicator(c, window=period)
        feat[f"ema_{period}_dist"] = (c - ema) / ema

    # MACD
    macd_obj = trend.MACD(c)
    feat["macd"] = macd_obj.macd()
    feat["macd_signal"] = macd_obj.macd_signal()
    feat["macd_hist"] = macd_obj.macd_diff()
    feat["macd_hist_change"] = feat["macd_hist"].diff()

    # ADX
    adx_obj = trend.ADXIndicator(h, l, c, window=14)
    feat["adx"] = adx_obj.adx()
    feat["di_plus"] = adx_obj.adx_pos()
    feat["di_minus"] = adx_obj.adx_neg()
    feat["di_diff"] = feat["di_plus"] - feat["di_minus"]

    # ── Momentum Indicators ──
    feat["rsi_14"] = momentum.rsi(c, window=14)
    feat["rsi_7"] = momentum.rsi(c, window=7)
    feat["rsi_21"] = momentum.rsi(c, window=21)

    # Stochastic
    stoch = momentum.StochasticOscillator(h, l, c, window=14, smooth_window=3)
    feat["stoch_k"] = stoch.stoch()
    feat["stoch_d"] = stoch.stoch_signal()

    # Williams %R
    feat["williams_r"] = momentum.williams_r(h, l, c, lbp=14)

    # ROC
    for period in [5, 10, 21]:
        feat[f"roc_{period}"] = momentum.roc(c, window=period)

    # ── Volatility Bands ──
    bb = volatility.BollingerBands(c, window=20, window_dev=2)
    feat["bb_width"] = (bb.bollinger_hband() - bb.bollinger_lband()) / bb.bollinger_mavg()
    feat["bb_pct"] = bb.bollinger_pband()  # % position within bands

    # ATR
    for window in [7, 14, 21]:
        feat[f"atr_{window}"] = volatility.average_true_range(h, l, c, window=window)
        feat[f"atr_{window}_pct"] = feat[f"atr_{window}"] / c  # Normalized

    # Donchian
    for window in [20, 50]:
        dc_high = h.rolling(window).max()
        dc_low = l.rolling(window).min()
        feat[f"dc_{window}_pct"] = (c - dc_low) / (dc_high - dc_low + 1e-10)

    # ── Volume Features ──
    if v.sum() > 0:
        for window in [5, 10, 21]:
            feat[f"vol_sma_{window}"] = v.rolling(window).mean()
            feat[f"vol_ratio_price_{window}"] = v / (v.rolling(window).mean() + 1)

        # OBV trend
        obv = ta_vol.on_balance_volume(c, v)
        feat["obv_slope_10"] = obv.diff(10) / (obv.rolling(10).mean() + 1e-10)

        # VWAP distance (approximation for daily)
        vwap = (v * (h + l + c) / 3).cumsum() / v.cumsum()
        feat["vwap_dist"] = (c - vwap) / (vwap + 1e-10)

    # ── Candlestick Features ──
    feat["body_size"] = (c - o).abs() / (h - l + 1e-10)
    feat["upper_shadow"] = (h - pd.concat([c, o], axis=1).max(axis=1)) / (h - l + 1e-10)
    feat["lower_shadow"] = (pd.concat([c, o], axis=1).min(axis=1) - l) / (h - l + 1e-10)
    feat["is_bullish"] = (c > o).astype(int)

    # Consecutive candle counts
    bullish = (c > o).astype(int)
    groups = (bullish != bullish.shift()).cumsum()
    feat["consec_bullish"] = bullish.groupby(groups).cumsum()
    feat["consec_bearish"] = (1 - bullish).groupby((1 - bullish != (1 - bullish).shift()).cumsum()).cumsum()

    # ── Higher Timeframe Context ──
    feat["weekly_ret"] = c.pct_change(5)
    feat["monthly_ret"] = c.pct_change(21)
    feat["quarterly_ret"] = c.pct_change(63)

    # ── Statistical Features ──
    for window in [21, 63]:
        rets = c.pct_change()
        feat[f"skew_{window}"] = rets.rolling(window).skew()
        feat[f"kurt_{window}"] = rets.rolling(window).kurt()

    # ── Target Variable (for supervised learning) ──
    # Forward return — ONLY used as training target, never as a feature
    feat["target_1d"] = c.pct_change(1).shift(-1)
    feat["target_3d"] = c.pct_change(3).shift(-3)
    feat["target_5d"] = c.pct_change(5).shift(-5)

    # Binary classification targets
    feat["target_up_1d"] = (feat["target_1d"] > 0).astype(int)
    feat["target_up_3d"] = (feat["target_3d"] > 0).astype(int)

    return feat


def get_feature_columns(feat_df: pd.DataFrame) -> list[str]:
    """Return only feature columns (exclude targets)."""
    return [c for c in feat_df.columns if not c.startswith("target_")]


def prepare_ml_data(
    df: pd.DataFrame, target_col: str = "target_up_1d"
) -> tuple[pd.DataFrame, pd.Series, list[str]]:
    """
    Prepare feature matrix X and target y for ML training.
    Drops NaN rows and separates features from target.
    """
    feat = compute_features(df)
    feature_cols = get_feature_columns(feat)

    # Drop rows with NaN in features or target
    combined = feat[feature_cols + [target_col]].dropna()

    X = combined[feature_cols]
    y = combined[target_col]

    return X, y, feature_cols
