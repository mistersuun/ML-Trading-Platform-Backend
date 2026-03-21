"""
Technical Pattern Detection — classic and advanced signals.

Each pattern function: df → df with 'signal' column (1=buy, -1=sell, 0=none).
"""

import pandas as pd
import numpy as np
from ta import trend, momentum, volatility, volume as ta_vol
from typing import Callable
import logging

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════
#  TREND FOLLOWING
# ══════════════════════════════════════════════════════════════

def ema_crossover(df: pd.DataFrame, fast: int = 9, slow: int = 21) -> pd.DataFrame:
    """EMA Crossover — classic trend signal."""
    out = df.copy()
    ef = trend.ema_indicator(out["Close"], window=fast)
    es = trend.ema_indicator(out["Close"], window=slow)
    out["signal"] = 0
    out.loc[(ef > es) & (ef.shift(1) <= es.shift(1)), "signal"] = 1
    out.loc[(ef < es) & (ef.shift(1) >= es.shift(1)), "signal"] = -1
    return out


def triple_ema(df: pd.DataFrame, fast: int = 5, mid: int = 13, slow: int = 34) -> pd.DataFrame:
    """Triple EMA alignment — strong trend confirmation."""
    out = df.copy()
    ef = trend.ema_indicator(out["Close"], window=fast)
    em = trend.ema_indicator(out["Close"], window=mid)
    es = trend.ema_indicator(out["Close"], window=slow)
    bullish = (ef > em) & (em > es)
    bearish = (ef < em) & (em < es)
    out["signal"] = 0
    out.loc[bullish & ~bullish.shift(1).fillna(False), "signal"] = 1
    out.loc[bearish & ~bearish.shift(1).fillna(False), "signal"] = -1
    return out


def macd_crossover(df: pd.DataFrame) -> pd.DataFrame:
    """MACD line vs signal line crossover."""
    out = df.copy()
    m = trend.MACD(out["Close"])
    ml, ms = m.macd(), m.macd_signal()
    out["signal"] = 0
    out.loc[(ml > ms) & (ml.shift(1) <= ms.shift(1)), "signal"] = 1
    out.loc[(ml < ms) & (ml.shift(1) >= ms.shift(1)), "signal"] = -1
    return out


def macd_histogram_reversal(df: pd.DataFrame) -> pd.DataFrame:
    """MACD histogram direction change — earlier signal than crossover."""
    out = df.copy()
    hist = trend.MACD(out["Close"]).macd_diff()
    out["signal"] = 0
    # Histogram turns positive from negative
    out.loc[(hist > 0) & (hist.shift(1) <= 0), "signal"] = 1
    out.loc[(hist < 0) & (hist.shift(1) >= 0), "signal"] = -1
    return out


def sma_200_trend(df: pd.DataFrame) -> pd.DataFrame:
    """Price crosses 200 SMA — long-term trend change."""
    out = df.copy()
    sma = out["Close"].rolling(200).mean()
    out["signal"] = 0
    out.loc[(out["Close"] > sma) & (out["Close"].shift(1) <= sma.shift(1)), "signal"] = 1
    out.loc[(out["Close"] < sma) & (out["Close"].shift(1) >= sma.shift(1)), "signal"] = -1
    return out


def adx_trend_strength(df: pd.DataFrame, window: int = 14, threshold: float = 25) -> pd.DataFrame:
    """ADX + Directional Index — signals in strong trends only."""
    out = df.copy()
    a = trend.ADXIndicator(out["High"], out["Low"], out["Close"], window=window)
    adx, dip, dim = a.adx(), a.adx_pos(), a.adx_neg()
    strong = adx > threshold
    out["signal"] = 0
    out.loc[(dip > dim) & (dip.shift(1) <= dim.shift(1)) & strong, "signal"] = 1
    out.loc[(dip < dim) & (dip.shift(1) >= dim.shift(1)) & strong, "signal"] = -1
    return out


def ichimoku_cloud(df: pd.DataFrame) -> pd.DataFrame:
    """Ichimoku Cloud breakout — buy above cloud, sell below."""
    out = df.copy()
    ic = trend.IchimokuIndicator(out["High"], out["Low"])
    span_a = ic.ichimoku_a()
    span_b = ic.ichimoku_b()
    cloud_top = pd.concat([span_a, span_b], axis=1).max(axis=1)
    cloud_bot = pd.concat([span_a, span_b], axis=1).min(axis=1)
    out["signal"] = 0
    out.loc[(out["Close"] > cloud_top) & (out["Close"].shift(1) <= cloud_top.shift(1)), "signal"] = 1
    out.loc[(out["Close"] < cloud_bot) & (out["Close"].shift(1) >= cloud_bot.shift(1)), "signal"] = -1
    return out


# ══════════════════════════════════════════════════════════════
#  MEAN REVERSION
# ══════════════════════════════════════════════════════════════

def rsi_oversold_overbought(df: pd.DataFrame, window: int = 14,
                             oversold: float = 30, overbought: float = 70) -> pd.DataFrame:
    """RSI mean reversion — bounce from extremes."""
    out = df.copy()
    r = momentum.rsi(out["Close"], window=window)
    out["signal"] = 0
    out.loc[(r > oversold) & (r.shift(1) <= oversold), "signal"] = 1
    out.loc[(r < overbought) & (r.shift(1) >= overbought), "signal"] = -1
    return out


def rsi_divergence(df: pd.DataFrame, window: int = 14, lookback: int = 20) -> pd.DataFrame:
    """RSI divergence — price makes new low but RSI doesn't (bullish divergence)."""
    out = df.copy()
    r = momentum.rsi(out["Close"], window=window)
    out["signal"] = 0

    price_low = out["Close"].rolling(lookback).min()
    rsi_at_price_low = r.rolling(lookback).min()

    # Bullish: price makes lower low, RSI makes higher low
    bull_div = (
        (out["Close"] <= price_low.shift(1) * 1.01) &
        (r > rsi_at_price_low.shift(1)) &
        (r < 40)
    )
    # Bearish: price makes higher high, RSI makes lower high
    price_high = out["Close"].rolling(lookback).max()
    rsi_at_price_high = r.rolling(lookback).max()
    bear_div = (
        (out["Close"] >= price_high.shift(1) * 0.99) &
        (r < rsi_at_price_high.shift(1)) &
        (r > 60)
    )

    out.loc[bull_div & ~bull_div.shift(1).fillna(False), "signal"] = 1
    out.loc[bear_div & ~bear_div.shift(1).fillna(False), "signal"] = -1
    return out


def bollinger_bounce(df: pd.DataFrame, window: int = 20, std_dev: float = 2.0) -> pd.DataFrame:
    """Bollinger Band mean reversion — bounce off bands."""
    out = df.copy()
    bb = volatility.BollingerBands(out["Close"], window=window, window_dev=std_dev)
    upper, lower = bb.bollinger_hband(), bb.bollinger_lband()
    out["signal"] = 0
    out.loc[(out["Close"] > lower) & (out["Close"].shift(1) <= lower.shift(1)), "signal"] = 1
    out.loc[(out["Close"] < upper) & (out["Close"].shift(1) >= upper.shift(1)), "signal"] = -1
    return out


def bollinger_squeeze(df: pd.DataFrame, window: int = 20, squeeze_pct: float = 0.03) -> pd.DataFrame:
    """Bollinger Squeeze — breakout after low volatility contraction."""
    out = df.copy()
    bb = volatility.BollingerBands(out["Close"], window=window, window_dev=2)
    width = (bb.bollinger_hband() - bb.bollinger_lband()) / bb.bollinger_mavg()

    squeeze = width < width.rolling(120).quantile(0.1)
    expansion = width > width.shift(1)

    out["signal"] = 0
    # Buy: squeeze ends with upward breakout
    out.loc[squeeze.shift(1) & expansion & (out["Close"] > bb.bollinger_mavg()), "signal"] = 1
    out.loc[squeeze.shift(1) & expansion & (out["Close"] < bb.bollinger_mavg()), "signal"] = -1
    return out


def stochastic_crossover(df: pd.DataFrame, k: int = 14, d: int = 3) -> pd.DataFrame:
    """Stochastic %K/%D crossover in extreme zones."""
    out = df.copy()
    s = momentum.StochasticOscillator(out["High"], out["Low"], out["Close"], window=k, smooth_window=d)
    sk, sd = s.stoch(), s.stoch_signal()
    out["signal"] = 0
    out.loc[(sk > sd) & (sk.shift(1) <= sd.shift(1)) & (sk < 30), "signal"] = 1
    out.loc[(sk < sd) & (sk.shift(1) >= sd.shift(1)) & (sk > 70), "signal"] = -1
    return out


def keltner_mean_reversion(df: pd.DataFrame, window: int = 20, atr_mult: float = 2.0) -> pd.DataFrame:
    """Keltner Channel mean reversion."""
    out = df.copy()
    kc = volatility.KeltnerChannel(out["High"], out["Low"], out["Close"],
                                    window=window, window_atr=window)
    upper = kc.keltner_channel_hband()
    lower = kc.keltner_channel_lband()
    out["signal"] = 0
    out.loc[(out["Close"] > lower) & (out["Close"].shift(1) <= lower.shift(1)), "signal"] = 1
    out.loc[(out["Close"] < upper) & (out["Close"].shift(1) >= upper.shift(1)), "signal"] = -1
    return out


# ══════════════════════════════════════════════════════════════
#  BREAKOUT / MOMENTUM
# ══════════════════════════════════════════════════════════════

def donchian_breakout(df: pd.DataFrame, window: int = 20) -> pd.DataFrame:
    """Donchian Channel Breakout — turtle trading style."""
    out = df.copy()
    dc_h = out["High"].rolling(window).max().shift(1)
    dc_l = out["Low"].rolling(window).min().shift(1)
    out["signal"] = 0
    out.loc[out["Close"] > dc_h, "signal"] = 1
    out.loc[out["Close"] < dc_l, "signal"] = -1
    return out


def volume_breakout(df: pd.DataFrame, price_window: int = 20, vol_mult: float = 2.0) -> pd.DataFrame:
    """Volume-confirmed breakout — price breakout + high volume."""
    out = df.copy()
    resist = out["High"].rolling(price_window).max().shift(1)
    support = out["Low"].rolling(price_window).min().shift(1)
    high_vol = out["Volume"] > out["Volume"].rolling(price_window).mean() * vol_mult
    out["signal"] = 0
    out.loc[(out["Close"] > resist) & high_vol, "signal"] = 1
    out.loc[(out["Close"] < support) & high_vol, "signal"] = -1
    return out


def atr_breakout(df: pd.DataFrame, window: int = 14, mult: float = 1.5) -> pd.DataFrame:
    """ATR Breakout — price moves > N * ATR from previous close."""
    out = df.copy()
    atr = volatility.average_true_range(out["High"], out["Low"], out["Close"], window=window)
    prev_close = out["Close"].shift(1)
    out["signal"] = 0
    out.loc[out["Close"] > prev_close + (atr * mult), "signal"] = 1
    out.loc[out["Close"] < prev_close - (atr * mult), "signal"] = -1
    return out


def inside_bar_breakout(df: pd.DataFrame) -> pd.DataFrame:
    """Inside Bar Breakout — consolidation bar followed by expansion."""
    out = df.copy()
    inside = (out["High"] < out["High"].shift(1)) & (out["Low"] > out["Low"].shift(1))
    out["signal"] = 0
    # Breakout candle after inside bar
    out.loc[
        inside.shift(1) & (out["Close"] > out["High"].shift(1)),
        "signal"
    ] = 1
    out.loc[
        inside.shift(1) & (out["Close"] < out["Low"].shift(1)),
        "signal"
    ] = -1
    return out


# ══════════════════════════════════════════════════════════════
#  ADVANCED / COMPOSITE
# ══════════════════════════════════════════════════════════════

def mean_reversion_composite(df: pd.DataFrame) -> pd.DataFrame:
    """Multi-indicator mean reversion — RSI + BB + Stoch must agree."""
    out = df.copy()
    rsi = momentum.rsi(out["Close"], window=14)
    bb = volatility.BollingerBands(out["Close"], window=20, window_dev=2)
    stoch = momentum.StochasticOscillator(out["High"], out["Low"], out["Close"])

    oversold = (rsi < 35) & (out["Close"] < bb.bollinger_lband()) & (stoch.stoch() < 25)
    overbought = (rsi > 65) & (out["Close"] > bb.bollinger_hband()) & (stoch.stoch() > 75)

    out["signal"] = 0
    # Entry on bounce from oversold
    out.loc[oversold.shift(1) & (out["Close"] > out["Close"].shift(1)), "signal"] = 1
    out.loc[overbought.shift(1) & (out["Close"] < out["Close"].shift(1)), "signal"] = -1
    return out


def trend_momentum_combo(df: pd.DataFrame) -> pd.DataFrame:
    """Trend + Momentum — EMA trend direction + RSI momentum confirmation."""
    out = df.copy()
    ema50 = trend.ema_indicator(out["Close"], window=50)
    ema200 = trend.ema_indicator(out["Close"], window=200)
    rsi = momentum.rsi(out["Close"], window=14)
    macd_h = trend.MACD(out["Close"]).macd_diff()

    uptrend = ema50 > ema200
    downtrend = ema50 < ema200

    out["signal"] = 0
    # Buy: uptrend + RSI pullback + MACD turning up
    out.loc[
        uptrend & (rsi > 35) & (rsi < 55) &
        (macd_h > macd_h.shift(1)) & (macd_h.shift(1) < 0),
        "signal"
    ] = 1
    out.loc[
        downtrend & (rsi < 65) & (rsi > 45) &
        (macd_h < macd_h.shift(1)) & (macd_h.shift(1) > 0),
        "signal"
    ] = -1
    return out


def confluence_signal(df: pd.DataFrame, min_agree: int = 3) -> pd.DataFrame:
    """Multi-pattern confluence — signal only when N+ patterns agree."""
    sub_patterns = [
        ema_crossover, macd_crossover, rsi_oversold_overbought,
        bollinger_bounce, stochastic_crossover, donchian_breakout
    ]
    signals = []
    for p in sub_patterns:
        try:
            signals.append(p(df)["signal"])
        except Exception:
            pass

    out = df.copy()
    if not signals:
        out["signal"] = 0
        return out

    combined = pd.concat(signals, axis=1)
    out["signal"] = 0
    out.loc[(combined == 1).sum(axis=1) >= min_agree, "signal"] = 1
    out.loc[(combined == -1).sum(axis=1) >= min_agree, "signal"] = -1
    return out


# ══════════════════════════════════════════════════════════════
#  PATTERN REGISTRY
# ══════════════════════════════════════════════════════════════

PATTERN_REGISTRY: dict[str, Callable] = {
    # Trend
    "ema_crossover":          ema_crossover,
    "triple_ema":             triple_ema,
    "macd_crossover":         macd_crossover,
    "macd_hist_reversal":     macd_histogram_reversal,
    "sma_200_trend":          sma_200_trend,
    "adx_trend":              adx_trend_strength,
    "ichimoku_cloud":         ichimoku_cloud,
    # Mean Reversion
    "rsi_reversal":           rsi_oversold_overbought,
    "rsi_divergence":         rsi_divergence,
    "bollinger_bounce":       bollinger_bounce,
    "bollinger_squeeze":      bollinger_squeeze,
    "stochastic_cross":       stochastic_crossover,
    "keltner_reversion":      keltner_mean_reversion,
    # Breakout / Momentum
    "donchian_breakout":      donchian_breakout,
    "volume_breakout":        volume_breakout,
    "atr_breakout":           atr_breakout,
    "inside_bar_breakout":    inside_bar_breakout,
    # Composite
    "mean_rev_composite":     mean_reversion_composite,
    "trend_momentum_combo":   trend_momentum_combo,
    "confluence":             confluence_signal,
}


def run_all_patterns(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Run every registered pattern on the given OHLCV data."""
    results = {}
    for name, func in PATTERN_REGISTRY.items():
        try:
            results[name] = func(df)
        except Exception as e:
            logger.warning(f"Pattern '{name}' failed: {e}")
    return results
