"""
Data Fetcher v2 — Multi-source with automatic failover.
Priority: Alpaca → Alpha Vantage → yfinance

Each source returns standardized OHLCV DataFrames.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd
import requests
import yfinance as yf

import config

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════
#  ALPACA DATA SOURCE
# ══════════════════════════════════════════════════════════════

def _fetch_alpaca(
    symbol: str, start: datetime, end: datetime, interval: str = "1Day"
) -> pd.DataFrame:
    """Fetch from Alpaca Markets Data API v2 (free with account)."""
    if not config.ALPACA_API_KEY or not config.ALPACA_SECRET_KEY:
        return pd.DataFrame()

    # Alpaca only supports US equities + crypto natively
    if "=" in symbol or symbol.endswith("=F"):
        return pd.DataFrame()

    is_crypto = symbol.endswith("-USD")
    if is_crypto:
        base_url = f"{config.ALPACA_DATA_URL}/v1beta3/crypto/us/bars"
        # Alpaca crypto uses BTC/USD format
        alpaca_symbol = symbol.replace("-USD", "/USD")
    else:
        base_url = f"{config.ALPACA_DATA_URL}/v2/stocks/{symbol}/bars"
        alpaca_symbol = symbol

    timeframe_map = {
        "1d": "1Day", "1Day": "1Day",
        "1h": "1Hour", "1Hour": "1Hour",
        "15m": "15Min", "15Min": "15Min",
        "5m": "5Min", "5Min": "5Min",
        "1m": "1Min", "1Min": "1Min",
    }
    tf = timeframe_map.get(interval, "1Day")

    headers = {
        "APCA-API-KEY-ID": config.ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": config.ALPACA_SECRET_KEY,
    }

    params = {
        "start": start.strftime("%Y-%m-%dT00:00:00Z"),
        "end": end.strftime("%Y-%m-%dT23:59:59Z"),
        "timeframe": tf,
        "limit": 10000,
        "adjustment": "all",  # Split/dividend adjusted
    }
    if is_crypto:
        params["symbols"] = alpaca_symbol

    try:
        all_bars = []
        page_token = None

        while True:
            if page_token:
                params["page_token"] = page_token

            resp = requests.get(base_url, headers=headers, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()

            if is_crypto:
                bars = data.get("bars", {}).get(alpaca_symbol, [])
            else:
                bars = data.get("bars", [])

            all_bars.extend(bars)
            page_token = data.get("next_page_token")
            if not page_token:
                break

        if not all_bars:
            return pd.DataFrame()

        df = pd.DataFrame(all_bars)
        df["t"] = pd.to_datetime(df["t"])
        df.set_index("t", inplace=True)
        df.index = df.index.tz_localize(None)
        df = df.rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"})
        df = df[["Open", "High", "Low", "Close", "Volume"]].astype(float)
        df.sort_index(inplace=True)
        df.dropna(inplace=True)

        logger.info(f"  Alpaca: {len(df)} bars for {symbol}")
        return df

    except Exception as e:
        logger.debug(f"  Alpaca failed for {symbol}: {e}")
        return pd.DataFrame()


# ══════════════════════════════════════════════════════════════
#  ALPHA VANTAGE DATA SOURCE
# ══════════════════════════════════════════════════════════════

def _fetch_alphavantage(
    symbol: str, start: datetime, end: datetime, interval: str = "1d"
) -> pd.DataFrame:
    """Fetch from Alpha Vantage (free tier: 25 calls/day)."""
    if not config.ALPHA_VANTAGE_KEY:
        return pd.DataFrame()

    # Determine function and clean symbol
    is_crypto = symbol.endswith("-USD")
    is_forex = "=" in symbol and not symbol.endswith("=F")

    if is_crypto:
        function = "DIGITAL_CURRENCY_DAILY"
        av_symbol = symbol.replace("-USD", "")
        params = {
            "function": function,
            "symbol": av_symbol,
            "market": "USD",
            "apikey": config.ALPHA_VANTAGE_KEY,
            "outputsize": "full",
        }
    elif is_forex:
        function = "FX_DAILY"
        clean = symbol.replace("=X", "")
        from_sym = clean[:3]
        to_sym = clean[3:]
        params = {
            "function": function,
            "from_symbol": from_sym,
            "to_symbol": to_sym,
            "apikey": config.ALPHA_VANTAGE_KEY,
            "outputsize": "full",
        }
    else:
        function = "TIME_SERIES_DAILY_ADJUSTED"
        params = {
            "function": function,
            "symbol": symbol.replace("=F", ""),
            "apikey": config.ALPHA_VANTAGE_KEY,
            "outputsize": "full",
        }

    try:
        resp = requests.get(
            "https://www.alphavantage.co/query", params=params, timeout=15
        )
        resp.raise_for_status()
        data = resp.json()

        # Find the time series key
        ts_key = None
        for k in data:
            if "Time Series" in k or "time series" in k.lower():
                ts_key = k
                break

        if not ts_key or ts_key not in data:
            return pd.DataFrame()

        df = pd.DataFrame.from_dict(data[ts_key], orient="index")
        df.index = pd.to_datetime(df.index)
        df.sort_index(inplace=True)

        # Normalize column names (Alpha Vantage prefixes with numbers)
        col_map = {}
        for col in df.columns:
            cl = col.lower()
            if "open" in cl:
                col_map[col] = "Open"
            elif "high" in cl:
                col_map[col] = "High"
            elif "low" in cl:
                col_map[col] = "Low"
            elif "close" in cl and "adjusted" not in cl:
                col_map[col] = "Close"
            elif "adjusted" in cl and "close" in cl:
                col_map[col] = "Adj_Close"
            elif "volume" in cl:
                col_map[col] = "Volume"

        df = df.rename(columns=col_map)

        # Use adjusted close if available
        if "Adj_Close" in df.columns:
            df["Close"] = df["Adj_Close"]

        required = ["Open", "High", "Low", "Close"]
        if not all(c in df.columns for c in required):
            return pd.DataFrame()

        if "Volume" not in df.columns:
            df["Volume"] = 0

        df = df[["Open", "High", "Low", "Close", "Volume"]].astype(float)
        df = df.loc[start:end]
        df.dropna(inplace=True)

        logger.info(f"  AlphaVantage: {len(df)} bars for {symbol}")
        return df

    except Exception as e:
        logger.debug(f"  AlphaVantage failed for {symbol}: {e}")
        return pd.DataFrame()


# ══════════════════════════════════════════════════════════════
#  YFINANCE DATA SOURCE (fallback)
# ══════════════════════════════════════════════════════════════

def _fetch_yfinance(
    symbol: str, start: datetime, end: datetime, interval: str = "1d"
) -> pd.DataFrame:
    """Fetch from yfinance (free, unofficial Yahoo Finance wrapper)."""
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(start=start, end=end, interval=interval)

        if df.empty:
            return pd.DataFrame()

        df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
        df.index = pd.to_datetime(df.index)
        df.index = df.index.tz_localize(None)
        df.dropna(inplace=True)

        logger.info(f"  yfinance: {len(df)} bars for {symbol}")
        return df

    except Exception as e:
        logger.debug(f"  yfinance failed for {symbol}: {e}")
        return pd.DataFrame()


# ══════════════════════════════════════════════════════════════
#  UNIFIED FETCH — tries sources in priority order
# ══════════════════════════════════════════════════════════════

SOURCE_MAP = {
    "alpaca": _fetch_alpaca,
    "alphavantage": _fetch_alphavantage,
    "yfinance": _fetch_yfinance,
}


def fetch_ohlcv(
    symbol: str,
    period_days: int = config.LOOKBACK_DAYS,
    interval: str = "1d",
    end_date: Optional[datetime] = None,
    sources: Optional[list[str]] = None,
) -> pd.DataFrame:
    """
    Fetch OHLCV with automatic failover across data sources.
    Returns standardized DataFrame with Open, High, Low, Close, Volume.
    """
    if end_date is None:
        end_date = datetime.now()
    start_date = end_date - timedelta(days=period_days)

    sources = sources or config.DATA_SOURCE_PRIORITY

    for source_name in sources:
        fetcher = SOURCE_MAP.get(source_name)
        if fetcher is None:
            continue

        df = fetcher(symbol, start_date, end_date, interval)
        if not df.empty and len(df) >= 30:
            return df

    logger.warning(f"All data sources failed for {symbol}")
    return pd.DataFrame()


def fetch_watchlist(
    markets: Optional[list[str]] = None,
    interval: str = "1d",
) -> dict[str, pd.DataFrame]:
    """Fetch all symbols in watchlist with failover."""
    if markets is None:
        markets = list(config.WATCHLIST.keys())

    symbols = []
    for m in markets:
        symbols.extend(config.WATCHLIST.get(m, []))

    results = {}
    for sym in symbols:
        df = fetch_ohlcv(sym, interval=interval)
        if not df.empty:
            results[sym] = df

    logger.info(f"Fetched {len(results)}/{len(symbols)} symbols")
    return results


def fetch_pair(sym_a: str, sym_b: str, period_days: int = config.PAIRS_LOOKBACK) -> Optional[tuple]:
    """Fetch aligned data for a pair of symbols."""
    df_a = fetch_ohlcv(sym_a, period_days=period_days)
    df_b = fetch_ohlcv(sym_b, period_days=period_days)

    if df_a.empty or df_b.empty:
        return None

    # Align on common dates
    common_idx = df_a.index.intersection(df_b.index)
    if len(common_idx) < 60:
        return None

    return df_a.loc[common_idx], df_b.loc[common_idx]
