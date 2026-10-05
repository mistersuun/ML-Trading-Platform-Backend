#!/usr/bin/env python3
"""
Trading Bot v2 — Visual Dashboard (Streamlit)

Launch:  streamlit run dashboard.py
"""

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
from datetime import datetime, timedelta
import json
import logging
import time

# ── Must be first Streamlit call ──
st.set_page_config(
    page_title="Trading Bot v2",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

import config
from services import backtest as backtest_svc, ml as ml_svc, pairs as pairs_svc, scan as scan_svc
from services.providers import default_provider
from patterns import PATTERN_REGISTRY, run_all_patterns
from backtester import BacktestResult   # type only; every backtest runs through services
from stress_test import full_stress_test, detect_regimes, monte_carlo_analysis, parameter_sensitivity
from pairs_trading import analyze_pair, is_valid_pair, generate_pair_signals, backtest_pair
from risk_manager import RiskManager
from dashboard_fmt import (fmt, pair_metric_texts, regime_profitability, safe_analyze_pair,
                           scanned_pair_texts)

logger = logging.getLogger(__name__)

st.warning("FROZEN LEGACY UI: this dashboard shows pre-fix metrics (known backtest/ranking "
           "bugs, see docs/research/robustness-roadmap.md). Do not use it for decisions.")

# ══════════════════════════════════════════════════════════════
#  STYLING
# ══════════════════════════════════════════════════════════════

st.markdown("""
<style>
    .stMetric { background: #111; border-radius: 8px; padding: 10px; }
    div[data-testid="stMetricValue"] { font-size: 1.4rem; }
    .signal-buy { color: #00ff88; font-weight: bold; font-size: 1.2em; }
    .signal-sell { color: #ff4444; font-weight: bold; font-size: 1.2em; }
    .signal-none { color: #888; }
    section[data-testid="stSidebar"] { background: #0d1117; }
    .block-container { padding-top: 1.5rem; }
</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════
#  CACHED DATA LOADING
# ══════════════════════════════════════════════════════════════

@st.cache_data(ttl=3600, show_spinner=False)
def load_symbol_data(symbol: str, period_days: int = 730) -> pd.DataFrame:
    return default_provider().ohlcv(symbol, period_days)


@st.cache_data(ttl=3600, show_spinner=False)
def load_watchlist_data(markets: tuple) -> dict:
    return default_provider().watchlist(list(markets))


# ══════════════════════════════════════════════════════════════
#  CHARTING HELPERS
# ══════════════════════════════════════════════════════════════

def plot_candlestick(df: pd.DataFrame, title: str = "", signals_df: pd.DataFrame = None,
                     height: int = 500) -> go.Figure:
    """Create an interactive candlestick chart with optional signal markers."""
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.03,
        row_heights=[0.75, 0.25],
        subplot_titles=[title, "Volume"],
    )

    fig.add_trace(go.Candlestick(
        x=df.index, open=df["Open"], high=df["High"],
        low=df["Low"], close=df["Close"], name="Price",
        increasing_line_color="#00ff88", decreasing_line_color="#ff4444",
    ), row=1, col=1)

    # Volume bars
    colors = ["#00ff88" if c >= o else "#ff4444" for c, o in zip(df["Close"], df["Open"])]
    fig.add_trace(go.Bar(
        x=df.index, y=df["Volume"], name="Volume",
        marker_color=colors, opacity=0.5,
    ), row=2, col=1)

    # Signal markers
    if signals_df is not None and "signal" in signals_df.columns:
        buys = signals_df[signals_df["signal"] == 1]
        sells = signals_df[signals_df["signal"] == -1]

        if not buys.empty:
            fig.add_trace(go.Scatter(
                x=buys.index, y=buys["Low"] * 0.995,
                mode="markers", name="Buy Signal",
                marker=dict(symbol="triangle-up", size=12, color="#00ff88"),
            ), row=1, col=1)

        if not sells.empty:
            fig.add_trace(go.Scatter(
                x=sells.index, y=sells["High"] * 1.005,
                mode="markers", name="Sell Signal",
                marker=dict(symbol="triangle-down", size=12, color="#ff4444"),
            ), row=1, col=1)

    fig.update_layout(
        template="plotly_dark", height=height,
        xaxis_rangeslider_visible=False,
        margin=dict(l=0, r=0, t=30, b=0),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    return fig


def plot_equity_curve(result: BacktestResult, height: int = 350) -> go.Figure:
    """Plot equity curve with drawdown."""
    eq = result.equity_curve
    if eq.empty:
        return go.Figure()

    peak = eq.cummax()
    dd = (eq - peak) / (peak + 1e-10) * 100

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.05,
        row_heights=[0.7, 0.3],
        subplot_titles=["Equity Curve", "Drawdown %"],
    )

    fig.add_trace(go.Scatter(
        x=eq.index, y=eq, mode="lines", name="Equity",
        line=dict(color="#00ff88", width=2), fill="tozeroy",
        fillcolor="rgba(0,255,136,0.1)",
    ), row=1, col=1)

    fig.add_trace(go.Scatter(
        x=dd.index, y=dd, mode="lines", name="Drawdown",
        line=dict(color="#ff4444", width=1), fill="tozeroy",
        fillcolor="rgba(255,68,68,0.2)",
    ), row=2, col=1)

    fig.update_layout(
        template="plotly_dark", height=height,
        margin=dict(l=0, r=0, t=30, b=0),
        showlegend=False,
    )
    return fig


def plot_monte_carlo(mc: dict, height: int = 350) -> go.Figure:
    """Plot Monte Carlo return distribution."""
    if "error" in mc:
        return go.Figure()

    # Simulate a distribution for visualization
    mean = mc.get("return_mean", 0)
    std = mc.get("return_std", 0.1)
    n = mc.get("n_simulations", 1000)
    samples = np.random.normal(mean, std, n)

    fig = go.Figure()
    fig.add_trace(go.Histogram(
        x=samples * 100, nbinsx=50, name="Simulated Returns",
        marker_color="rgba(0,255,136,0.6)",
    ))

    # Confidence interval lines
    ci_low = mc.get("return_ci_low", 0) * 100
    ci_high = mc.get("return_ci_high", 0) * 100

    fig.add_vline(x=mean * 100, line_dash="solid", line_color="#00ff88",
                  annotation_text=f"Mean: {mean:.1%}")
    fig.add_vline(x=ci_low, line_dash="dash", line_color="#ff4444",
                  annotation_text=f"5th: {ci_low:.1f}%")
    fig.add_vline(x=ci_high, line_dash="dash", line_color="#44ff44",
                  annotation_text=f"95th: {ci_high:.1f}%")
    fig.add_vline(x=0, line_dash="dot", line_color="#888")

    fig.update_layout(
        template="plotly_dark", height=height,
        title="Monte Carlo Return Distribution",
        xaxis_title="Return %", yaxis_title="Frequency",
        margin=dict(l=0, r=0, t=40, b=0),
        showlegend=False,
    )
    return fig


def plot_sensitivity_heatmap(sens_df: pd.DataFrame, metric: str = "sharpe",
                              height: int = 400) -> go.Figure:
    """Plot parameter sensitivity as a heatmap."""
    if sens_df.empty:
        return go.Figure()

    pivot = sens_df.pivot_table(
        index="stop_loss", columns="take_profit", values=metric
    )

    fig = go.Figure(data=go.Heatmap(
        z=pivot.values,
        x=[f"{x:.3f}" for x in pivot.columns],
        y=[f"{y:.3f}" for y in pivot.index],
        colorscale="RdYlGn",
        text=np.round(pivot.values, 2),
        texttemplate="%{text}",
        textfont={"size": 10},
    ))

    fig.update_layout(
        template="plotly_dark", height=height,
        title=f"Parameter Sensitivity — {metric.title()}",
        xaxis_title="Take Profit", yaxis_title="Stop Loss",
        margin=dict(l=0, r=0, t=40, b=0),
    )
    return fig


def plot_pairs_spread(signals_df: pd.DataFrame, analysis, height: int = 400) -> go.Figure:
    """Plot pairs trading spread and z-score with entry/exit zones."""
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.05,
        row_heights=[0.4, 0.6],
        subplot_titles=["Spread", "Z-Score with Trading Zones"],
    )

    # Spread
    fig.add_trace(go.Scatter(
        x=signals_df.index, y=signals_df["spread"],
        mode="lines", name="Spread", line=dict(color="#88bbff", width=1.5),
    ), row=1, col=1)

    # Z-score
    z = signals_df["zscore"]
    fig.add_trace(go.Scatter(
        x=z.index, y=z, mode="lines", name="Z-Score",
        line=dict(color="#ffffff", width=1.5),
    ), row=2, col=1)

    # Zones
    for val, color, label in [
        (config.PAIRS_ZSCORE_ENTRY, "#ff4444", "Entry"),
        (-config.PAIRS_ZSCORE_ENTRY, "#00ff88", "Entry"),
        (config.PAIRS_ZSCORE_EXIT, "#888", "Exit"),
        (-config.PAIRS_ZSCORE_EXIT, "#888", "Exit"),
        (config.PAIRS_ZSCORE_STOP, "#ff0000", "Stop"),
        (-config.PAIRS_ZSCORE_STOP, "#ff0000", "Stop"),
    ]:
        fig.add_hline(y=val, line_dash="dot", line_color=color,
                      annotation_text=label, row=2, col=1)

    fig.add_hline(y=0, line_dash="solid", line_color="#444", row=2, col=1)

    # Signal markers on z-score
    buys = signals_df[signals_df["signal"] == 1]
    sells = signals_df[signals_df["signal"] == -1]

    if not buys.empty:
        fig.add_trace(go.Scatter(
            x=buys.index, y=buys["zscore"], mode="markers", name="Long Spread",
            marker=dict(symbol="triangle-up", size=8, color="#00ff88"),
        ), row=2, col=1)
    if not sells.empty:
        fig.add_trace(go.Scatter(
            x=sells.index, y=sells["zscore"], mode="markers", name="Short Spread",
            marker=dict(symbol="triangle-down", size=8, color="#ff4444"),
        ), row=2, col=1)

    fig.update_layout(
        template="plotly_dark", height=height,
        margin=dict(l=0, r=0, t=30, b=0),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    return fig


def plot_regime_comparison(regime_results: dict, height: int = 400) -> go.Figure:
    """Bar chart comparing pattern performance across regimes."""
    labels, win_rates, profit_factors, returns = [], [], [], []

    for name, result in regime_results.items():
        if isinstance(result, BacktestResult) and result.total_trades > 0 \
                and not getattr(result, "insufficient", False):
            labels.append(name.replace("_", " ").title())
            win_rates.append(result.win_rate * 100)
            profit_factors.append(result.profit_factor)
            returns.append(result.total_return_pct * 100)

    if not labels:
        return go.Figure()

    fig = make_subplots(rows=1, cols=3, subplot_titles=["Win Rate %", "Profit Factor", "Return %"])

    fig.add_trace(go.Bar(x=labels, y=win_rates, marker_color="#00ff88", name="Win Rate"), row=1, col=1)
    fig.add_trace(go.Bar(x=labels, y=profit_factors, marker_color="#88bbff", name="PF"), row=1, col=2)

    ret_colors = ["#00ff88" if r > 0 else "#ff4444" for r in returns]
    fig.add_trace(go.Bar(x=labels, y=returns, marker_color=ret_colors, name="Return"), row=1, col=3)

    fig.add_hline(y=50, line_dash="dot", line_color="#888", row=1, col=1)
    fig.add_hline(y=1.0, line_dash="dot", line_color="#888", row=1, col=2)
    fig.add_hline(y=0, line_dash="dot", line_color="#888", row=1, col=3)

    fig.update_layout(
        template="plotly_dark", height=height, showlegend=False,
        margin=dict(l=0, r=0, t=40, b=0),
    )
    return fig


# ══════════════════════════════════════════════════════════════
#  SIDEBAR NAVIGATION
# ══════════════════════════════════════════════════════════════

st.sidebar.title("🤖 Trading Bot v2")
st.sidebar.markdown("---")

page = st.sidebar.radio(
    "Navigation",
    ["📊 Dashboard", "🔧 Technical Scanner", "📈 Pairs Trading",
     "🤖 ML Signals", "🔬 Stress Test Lab", "⚙️ Settings"],
    label_visibility="collapsed",
)

st.sidebar.markdown("---")

# Global settings in sidebar
with st.sidebar.expander("🌐 Quick Settings", expanded=False):
    selected_markets = st.multiselect(
        "Markets", ["stocks", "crypto", "forex", "futures"],
        default=["stocks", "crypto"],
    )
    lookback_days = st.slider("Lookback (days)", 180, 1095, 730, step=90)


# ══════════════════════════════════════════════════════════════
#  PAGE: DASHBOARD
# ══════════════════════════════════════════════════════════════

if page == "📊 Dashboard":
    st.title("📊 Dashboard — Market Overview")

    # Quick scan button
    col_btn, col_status = st.columns([1, 3])
    with col_btn:
        run_scan = st.button("🚀 Run Full Scan", type="primary", use_container_width=True)

    if run_scan or "scan_results" in st.session_state:
        if run_scan:
            with st.spinner("Fetching market data..."):
                data = load_watchlist_data(tuple(selected_markets))
            st.session_state["data"] = data
            st.session_state["scan_time"] = datetime.now()

            # Run technical scan
            tech_results = []
            pairs_results = []
            progress = st.progress(0, text="Scanning patterns...")

            progress.progress(0.5, text="Scanning patterns...")
            for sig in scan_svc.in_sample_scan(data):
                price = data[sig.symbol]["Close"].iloc[-1]
                tech_results.append({
                    "symbol": sig.symbol, "pattern": sig.pattern,
                    "direction": "🟢 BUY" if sig.signal == "BUY" else "🔴 SELL",
                    "price": f"${price:,.2f}", "win_rate": fmt(sig.win_rate, ".1%"),
                    "profit_factor": fmt(sig.profit_factor, ".2f"), "sharpe": fmt(sig.sharpe, ".2f"),
                    "total_return": fmt(sig.total_return, ".1%"), "max_drawdown": fmt(sig.max_drawdown, ".1%"),
                    "total_trades": sig.total_trades})

            progress.empty()

            # Run pairs scan
            with st.spinner("Scanning pairs..."):
                pairs_results = pairs_svc.scan_pairs_data(data)
                pairs_triggered = [p for p in pairs_results if p.get("has_signal")]

            st.session_state["scan_results"] = tech_results
            st.session_state["pairs_results"] = pairs_triggered

        tech_results = st.session_state.get("scan_results", [])
        pairs_triggered = st.session_state.get("pairs_results", [])
        scan_time = st.session_state.get("scan_time", datetime.now())

        # Summary metrics
        st.markdown(f"*Last scan: {scan_time.strftime('%Y-%m-%d %H:%M:%S')}*")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Technical Signals", len(tech_results))
        c2.metric("Pairs Signals", len(pairs_triggered))
        c3.metric("Buy Signals", len([t for t in tech_results if "BUY" in t.get("direction", "")]))
        c4.metric("Sell Signals", len([t for t in tech_results if "SELL" in t.get("direction", "")]))

        # Technical signals table
        if tech_results:
            st.subheader("🔧 Active Technical Signals")
            df_signals = pd.DataFrame(tech_results)
            display_cols = ["direction", "symbol", "pattern", "price", "win_rate",
                           "profit_factor", "sharpe", "total_return", "max_drawdown", "total_trades"]
            available = [c for c in display_cols if c in df_signals.columns]
            st.dataframe(df_signals[available], use_container_width=True, hide_index=True)

        # Pairs signals
        if pairs_triggered:
            st.subheader("📈 Active Pairs Signals")
            for p in pairs_triggered:
                with st.expander(
                    f"{p.get('signal_direction','?')} — {p['symbol_a']}/{p['symbol_b']} "
                    f"| z={scanned_pair_texts(p)['zscore']}"
                ):
                    pt = scanned_pair_texts(p)
                    pc1, pc2, pc3, pc4 = st.columns(4)
                    pc1.metric("Z-Score", pt["zscore"])
                    pc2.metric("Win Rate", pt["win_rate"])
                    pc3.metric("Half-Life", pt["half_life"])
                    pc4.metric("Profit Factor", pt["profit_factor"])

        if not tech_results and not pairs_triggered:
            st.info("No signals triggered. This is normal — the filters are intentionally strict.")

    else:
        st.info("Press **Run Full Scan** to analyze all markets and patterns.")

        # Show watchlist overview
        st.subheader("Configured Watchlist")
        for market, symbols in config.WATCHLIST.items():
            if market in selected_markets:
                st.markdown(f"**{market.upper()}**: {', '.join(symbols)}")


# ══════════════════════════════════════════════════════════════
#  PAGE: TECHNICAL SCANNER
# ══════════════════════════════════════════════════════════════

elif page == "🔧 Technical Scanner":
    st.title("🔧 Technical Pattern Scanner")

    col1, col2 = st.columns([1, 1])
    with col1:
        # Flatten all symbols
        all_symbols = []
        for m in selected_markets:
            all_symbols.extend(config.WATCHLIST.get(m, []))
        symbol = st.selectbox("Symbol", all_symbols, index=0)

    with col2:
        pattern_name = st.selectbox("Pattern", list(PATTERN_REGISTRY.keys()), index=0)

    if symbol and pattern_name:
        with st.spinner(f"Fetching {symbol}..."):
            df = load_symbol_data(symbol, lookback_days)

        if df.empty:
            st.error(f"No data for {symbol}")
        else:
            run = backtest_svc.run_on_frame(df, symbol, pattern_name)
            signals, result = run.signals, run.result

            # Signal status
            recent = 0
            tail = signals.tail(config.VALIDATION_WINDOW_DAYS)["signal"]
            if (tail == 1).any():
                recent = 1
            elif (tail == -1).any():
                recent = -1

            if recent == 1:
                st.markdown('<p class="signal-buy">🟢 ACTIVE BUY SIGNAL</p>', unsafe_allow_html=True)
            elif recent == -1:
                st.markdown('<p class="signal-sell">🔴 ACTIVE SELL SIGNAL</p>', unsafe_allow_html=True)
            else:
                st.markdown('<p class="signal-none">⚪ No recent signal</p>', unsafe_allow_html=True)

            # Metrics row
            m1, m2, m3, m4, m5, m6 = st.columns(6)
            m1.metric("Win Rate", f"{result.win_rate:.1%}")
            m2.metric("Profit Factor", f"{result.profit_factor:.2f}")
            m3.metric("Sharpe", f"{result.sharpe_ratio:.2f}")
            m4.metric("Total Return", f"{result.total_return_pct:.1%}")
            m5.metric("Max Drawdown", f"{result.max_drawdown_pct:.1%}")
            m6.metric("Trades", result.total_trades)

            valid_badge = "✅ Valid" if result.is_valid else "❌ Invalid"
            st.caption(f"Pattern Status: {valid_badge} "
                       f"(min WR≥{config.MIN_WIN_RATE:.0%}, PF≥{config.MIN_PROFIT_FACTOR}, "
                       f"Sharpe≥{config.MIN_SHARPE}, trades≥{config.MIN_TRADES})")

            # Chart
            st.plotly_chart(plot_candlestick(df.tail(200), f"{symbol} — {pattern_name}", signals.tail(200)),
                           use_container_width=True)

            # Equity curve
            st.subheader("Equity Curve & Drawdown")
            st.plotly_chart(plot_equity_curve(result), use_container_width=True)

            # Trade log
            if result.trades:
                with st.expander(f"📋 Trade Log ({len(result.trades)} trades)", expanded=False):
                    trade_data = [{
                        "Entry": t.entry_date.strftime("%Y-%m-%d") if t.entry_date else "",
                        "Exit": t.exit_date.strftime("%Y-%m-%d") if t.exit_date else "",
                        "Dir": "LONG" if t.direction == 1 else "SHORT",
                        "Entry $": f"{t.entry_price:.2f}",
                        "Exit $": f"{t.exit_price:.2f}",
                        "PnL %": f"{t.pnl_pct:.2%}",
                        "Reason": t.exit_reason,
                    } for t in result.trades]
                    st.dataframe(pd.DataFrame(trade_data), use_container_width=True, hide_index=True)

            # Multi-pattern comparison
            st.subheader("📊 Compare All Patterns on This Symbol")
            if st.button("Run All Patterns", key="run_all"):
                comparison = []
                bar = st.progress(0.5)
                for r in backtest_svc.compare_patterns(df, symbol):
                    row = r.summary()
                    row["is_valid"] = "✅" if r.is_valid else "❌"
                    comparison.append(row)
                bar.empty()

                if comparison:
                    cdf = pd.DataFrame(comparison)
                    display = ["pattern", "is_valid", "win_rate", "profit_factor", "sharpe",
                              "total_return", "max_drawdown", "total_trades"]
                    available = [c for c in display if c in cdf.columns]
                    st.dataframe(
                        cdf[available].sort_values("profit_factor", ascending=False, key=lambda x: x.str.rstrip('%').astype(float, errors='ignore')),
                        use_container_width=True, hide_index=True,
                    )


# ══════════════════════════════════════════════════════════════
#  PAGE: PAIRS TRADING
# ══════════════════════════════════════════════════════════════

elif page == "📈 Pairs Trading":
    st.title("📈 Statistical Arbitrage / Pairs Trading")

    pair_options = [f"{a} / {b}" for a, b in config.PAIRS]
    selected_pair = st.selectbox("Select Pair", pair_options, index=0)

    if selected_pair:
        sym_a, sym_b = selected_pair.split(" / ")

        with st.spinner(f"Analyzing {sym_a}/{sym_b}..."):
            df_a = load_symbol_data(sym_a, lookback_days)
            df_b = load_symbol_data(sym_b, lookback_days)

        if df_a.empty or df_b.empty:
            st.error("Could not fetch data for one or both symbols")
        else:
            analysis, df_a_aligned, df_b_aligned, warn = safe_analyze_pair(df_a, df_b, sym_a, sym_b)
            if analysis is None:
                st.warning(warn)
            else:
                valid = is_valid_pair(analysis)

                # Status
                status_color = "🟢" if valid else "🔴"
                st.markdown(f"### {status_color} Pair {'VALID' if valid else 'INVALID'} for Trading")
                st.caption("Pairs are alert-only and the backtest below is in-sample.")

                # Metrics
                pm = pair_metric_texts(analysis)
                c1, c2, c3, c4, c5, c6 = st.columns(6)
                c1.metric("Cointegration p", pm["coint_p"],
                         delta="Pass" if analysis.coint_pvalue < 0.05 else "Fail")
                c2.metric("Correlation", pm["correlation"])
                c3.metric("Half-Life", pm["half_life"])
                c4.metric("Current Z-Score", pm["zscore"])
                c5.metric("Hedge Ratio", pm["hedge_ratio"])
                c6.metric("ADF p-value", pm["adf_p"])

                # Signal
                z = analysis.current_zscore
                zt = pm["zscore"]
                if z is not None and abs(z) > config.PAIRS_ZSCORE_ENTRY:
                    if z < -config.PAIRS_ZSCORE_ENTRY:
                        st.success(f"🟢 **LONG SPREAD** — Buy {sym_a}, Sell {sym_b} (z={zt})")
                    else:
                        st.error(f"🔴 **SHORT SPREAD** — Sell {sym_a}, Buy {sym_b} (z={zt})")
                else:
                    st.info(f"No active signal (z={zt}, need |z|>{config.PAIRS_ZSCORE_ENTRY})")

                # Generate signals and plot
                signals = generate_pair_signals(df_a_aligned, df_b_aligned, analysis)
                st.plotly_chart(plot_pairs_spread(signals, analysis, height=500),
                               use_container_width=True)

                # Normalized prices comparison
                st.subheader("Normalized Price Comparison")
                norm_a = df_a_aligned["Close"] / df_a_aligned["Close"].iloc[0] * 100
                norm_b = df_b_aligned["Close"] / df_b_aligned["Close"].iloc[0] * 100
                fig_norm = go.Figure()
                fig_norm.add_trace(go.Scatter(x=norm_a.index, y=norm_a, name=sym_a,
                                              line=dict(color="#00ff88")))
                fig_norm.add_trace(go.Scatter(x=norm_b.index, y=norm_b, name=sym_b,
                                              line=dict(color="#ff8844")))
                fig_norm.update_layout(template="plotly_dark", height=300,
                                       margin=dict(l=0, r=0, t=10, b=0))
                st.plotly_chart(fig_norm, use_container_width=True)

                # Backtest
                if valid:
                    bt = backtest_pair(signals, analysis)
                    if bt.get("total_trades", 0) > 0:
                        st.subheader("Backtest Results")
                        b1, b2, b3, b4 = st.columns(4)
                        b1.metric("Win Rate", fmt(bt.get("win_rate"), ".1%"))
                        b2.metric("Profit Factor", fmt(bt.get("profit_factor"), ".2f"))
                        b3.metric("Total Return", fmt(bt.get("total_return"), ".2%"))
                        b4.metric("Sharpe", fmt(bt.get("sharpe_ratio"), ".2f"))

        # Scan all pairs
        st.markdown("---")
        st.subheader("🔍 Scan All Configured Pairs")
        if st.button("Scan All Pairs", type="primary"):
            data = {}
            bar = st.progress(0, text="Fetching pair data...")
            all_syms = set()
            for a, b in config.PAIRS:
                all_syms.add(a)
                all_syms.add(b)
            sym_list = list(all_syms)
            for i, s in enumerate(sym_list):
                bar.progress((i + 1) / len(sym_list), text=f"Fetching {s}...")
                d = load_symbol_data(s, lookback_days)
                if not d.empty:
                    data[s] = d
            bar.empty()

            results = pairs_svc.scan_pairs_data(data)
            if results:
                rdf = pd.DataFrame(results)
                display_cols = ["symbol_a", "symbol_b", "has_signal", "signal_direction",
                               "current_zscore", "win_rate", "profit_factor",
                               "total_return", "half_life", "correlation", "total_trades"]
                available = [c for c in display_cols if c in rdf.columns]
                st.dataframe(rdf[available].sort_values("current_zscore", key=abs, ascending=False),
                            use_container_width=True, hide_index=True)
            else:
                st.info("No valid cointegrated pairs found in current data.")


# ══════════════════════════════════════════════════════════════
#  PAGE: ML SIGNALS
# ══════════════════════════════════════════════════════════════

elif page == "🤖 ML Signals":
    st.title("🤖 Machine Learning Pattern Recognition")

    all_symbols = []
    for m in selected_markets:
        all_symbols.extend(config.WATCHLIST.get(m, []))
    symbol = st.selectbox("Symbol", all_symbols, index=0, key="ml_symbol")

    if symbol:
        with st.spinner(f"Fetching {symbol}..."):
            df = load_symbol_data(symbol, lookback_days)

        if df.empty or len(df) < 200:
            st.warning(f"Need 200+ bars for ML. Got {len(df)} for {symbol}.")
        else:
            if st.button("🧠 Train & Predict", type="primary"):
                with st.spinner("Computing features & training models..."):
                    # Legacy view: train on 75%, predict on all (use /api/ml/predict for out-of-sample numbers)
                    ml_run = ml_svc.train_and_predict(df, symbol)
                    metrics = ml_run.metrics

                st.subheader("Model Performance")
                mc1, mc2, mc3 = st.columns(3)
                mc1.metric("CV Accuracy", f"{metrics.get('mean_cv_accuracy', 0):.3f}")
                mc2.metric("Accuracy Std", f"± {metrics.get('std_cv_accuracy', 0):.3f}")
                mc3.metric("Features Used", metrics.get("n_features", 0))

                # CV scores chart
                cv_scores = metrics.get("cv_scores", [])
                if cv_scores:
                    fig_cv = go.Figure()
                    fig_cv.add_trace(go.Bar(
                        x=[f"Fold {i+1}" for i in range(len(cv_scores))],
                        y=cv_scores, marker_color="#88bbff",
                    ))
                    fig_cv.add_hline(y=0.5, line_dash="dot", line_color="#ff4444",
                                    annotation_text="Random baseline")
                    fig_cv.update_layout(template="plotly_dark", height=250,
                                        title="Walk-Forward CV Accuracy",
                                        margin=dict(l=0, r=0, t=40, b=0))
                    st.plotly_chart(fig_cv, use_container_width=True)

                # Top features
                top_feats = metrics.get("top_features", {})
                if top_feats:
                    st.subheader("Top 10 Features by Importance")
                    feat_df = pd.DataFrame({
                        "Feature": list(top_feats.keys()),
                        "Importance": list(top_feats.values()),
                    })
                    fig_feat = px.bar(feat_df, x="Importance", y="Feature",
                                     orientation="h", color="Importance",
                                     color_continuous_scale="Viridis")
                    fig_feat.update_layout(template="plotly_dark", height=350,
                                          margin=dict(l=0, r=0, t=10, b=0),
                                          yaxis=dict(autorange="reversed"))
                    st.plotly_chart(fig_feat, use_container_width=True)

                # Predictions
                pred_df = ml_run.predictions

                # Show recent predictions
                st.subheader("Recent ML Predictions")
                recent = pred_df.tail(20)[["Close", "signal", "ml_confidence"]].copy()
                recent["Direction"] = recent["signal"].map({1: "🟢 BUY", -1: "🔴 SELL", 0: "—"})
                recent["Confidence"] = recent["ml_confidence"].apply(lambda x: f"{x:.2f}")
                st.dataframe(recent[["Close", "Direction", "Confidence"]],
                            use_container_width=True)

                # Chart with ML signals
                st.subheader("Price Chart with ML Signals")
                st.plotly_chart(
                    plot_candlestick(df.tail(200), f"{symbol} — ML Ensemble", pred_df.tail(200)),
                    use_container_width=True,
                )

                # Backtest ML signals
                bt = ml_run.backtest
                if bt.total_trades > 0:
                    st.subheader("ML Signal Backtest")
                    b1, b2, b3, b4, b5 = st.columns(5)
                    b1.metric("Win Rate", f"{bt.win_rate:.1%}")
                    b2.metric("Profit Factor", f"{bt.profit_factor:.2f}")
                    b3.metric("Sharpe", f"{bt.sharpe_ratio:.2f}")
                    b4.metric("Return", f"{bt.total_return_pct:.1%}")
                    b5.metric("Trades", bt.total_trades)

                    st.plotly_chart(plot_equity_curve(bt), use_container_width=True)


# ══════════════════════════════════════════════════════════════
#  PAGE: STRESS TEST LAB
# ══════════════════════════════════════════════════════════════

elif page == "🔬 Stress Test Lab":
    st.title("🔬 Stress Test Laboratory")

    col1, col2 = st.columns([1, 1])
    with col1:
        all_symbols = []
        for m in selected_markets:
            all_symbols.extend(config.WATCHLIST.get(m, []))
        symbol = st.selectbox("Symbol", all_symbols, index=0, key="stress_sym")
    with col2:
        pattern = st.selectbox("Pattern", list(PATTERN_REGISTRY.keys()), index=0, key="stress_pat")

    if st.button("🧪 Run Full Stress Test", type="primary"):
        with st.spinner(f"Fetching {symbol}..."):
            df = load_symbol_data(symbol, lookback_days)

        if df.empty:
            st.error("No data")
        else:
            pat_func = PATTERN_REGISTRY[pattern]

            # ── 1. REGIME ANALYSIS ──
            st.subheader("1. Regime Analysis")
            with st.spinner("Testing across market regimes..."):
                from stress_test import stress_test_regimes
                regime_results = stress_test_regimes(df, symbol, pat_func, pattern)

            st.plotly_chart(plot_regime_comparison(regime_results, height=350),
                           use_container_width=True)

            # Detailed regime table
            regime_data = []
            for name, r in regime_results.items():
                if isinstance(r, BacktestResult):
                    s = r.summary()
                    s["regime"] = name
                    regime_data.append(s)
            if regime_data:
                rdf = pd.DataFrame(regime_data)
                st.dataframe(
                    rdf[["regime", "win_rate", "profit_factor", "sharpe",
                         "total_return", "max_drawdown", "total_trades"]],
                    use_container_width=True, hide_index=True,
                )

            # ── 2. MONTE CARLO ──
            st.subheader("2. Monte Carlo Simulation")
            full_result = regime_results.get("full_period")
            if full_result and full_result.total_trades >= 5:
                with st.spinner(f"Running {config.MONTE_CARLO_SIMULATIONS} simulations..."):
                    mc = monte_carlo_analysis(full_result)

                mc1, mc2, mc3, mc4 = st.columns(4)
                mc1.metric("Prob. Profitable", f"{mc.get('prob_positive', 0):.1%}")
                mc2.metric("Expected Return", f"{mc.get('return_mean', 0):.2%}")
                mc3.metric("Worst 5%", f"{mc.get('return_5th_pct', 0):.2%}")
                mc4.metric("Best 95%", f"{mc.get('return_95th_pct', 0):.2%}")

                st.plotly_chart(plot_monte_carlo(mc), use_container_width=True)

                with st.expander("📊 Full Monte Carlo Stats"):
                    st.json({k: f"{v:.4f}" if isinstance(v, float) else v for k, v in mc.items()})
            else:
                st.warning("Insufficient trades for Monte Carlo simulation")

            # ── 3. PARAMETER SENSITIVITY ──
            st.subheader("3. Parameter Sensitivity")
            with st.spinner("Sweeping SL/TP parameters..."):
                sens = parameter_sensitivity(df, symbol, pattern)

            if not sens.empty:
                metric_choice = st.radio("Metric", ["sharpe", "profit_factor", "win_rate", "total_return"],
                                        horizontal=True)
                st.plotly_chart(plot_sensitivity_heatmap(sens, metric_choice), use_container_width=True)

                # Robustness assessment
                sharpe_std = sens["sharpe"].std()
                if sharpe_std < 0.5:
                    st.success(f"✅ **Robust** — Sharpe varies only ±{sharpe_std:.2f} across parameters")
                elif sharpe_std < 1.0:
                    st.warning(f"⚠️ **Moderately sensitive** — Sharpe varies ±{sharpe_std:.2f}")
                else:
                    st.error(f"❌ **Fragile** — Sharpe varies ±{sharpe_std:.2f} — likely overfit")

            # ── 4. WALK-FORWARD VALIDATION ──
            st.subheader("4. Walk-Forward Out-of-Sample")
            with st.spinner("Running walk-forward folds..."):
                wf_results = backtest_svc.walk_forward_results(df, symbol, pattern, n_splits=5)

            if wf_results:
                wf_data = [r.summary() for r in wf_results if r.total_trades > 0]
                if wf_data:
                    st.dataframe(pd.DataFrame(wf_data), use_container_width=True, hide_index=True)
                    avg_oos_wr = np.mean([r.win_rate for r in wf_results if r.total_trades > 0])
                    if avg_oos_wr > 0.50:
                        st.success(f"✅ Avg out-of-sample win rate: {avg_oos_wr:.1%}")
                    else:
                        st.error(f"❌ Avg out-of-sample win rate: {avg_oos_wr:.1%} — possible overfit")

            # ── OVERALL VERDICT ──
            st.subheader("🏁 Overall Verdict")
            checks = []
            if full_result and full_result.is_valid:
                checks.append("✅ Backtest metrics pass")
            else:
                checks.append("❌ Backtest metrics fail")

            regime_ok, regime_total = regime_profitability(regime_results)   # skips insufficient regimes
            if regime_total > 0 and regime_ok / regime_total >= 0.5:
                checks.append(f"✅ Profitable in {regime_ok}/{regime_total} regimes")
            else:
                checks.append(f"❌ Only profitable in {regime_ok}/{regime_total} regimes")

            if not sens.empty and sens["sharpe"].std() < 1.0:
                checks.append("✅ Parameter robust")
            else:
                checks.append("❌ Parameter sensitive")

            if mc.get("prob_positive", 0) > 0.6:
                checks.append(f"✅ Monte Carlo: {mc['prob_positive']:.0%} chance of profit")
            else:
                checks.append(f"❌ Monte Carlo: only {mc.get('prob_positive', 0):.0%} chance of profit")

            for check in checks:
                st.markdown(check)

            passed = sum(1 for c in checks if c.startswith("✅"))
            if passed >= 3:
                st.success(f"**VERDICT: {passed}/4 checks passed — Pattern looks tradeable**")
            elif passed >= 2:
                st.warning(f"**VERDICT: {passed}/4 checks passed — Proceed with caution**")
            else:
                st.error(f"**VERDICT: {passed}/4 checks passed — Avoid trading this pattern**")


# ══════════════════════════════════════════════════════════════
#  PAGE: SETTINGS
# ══════════════════════════════════════════════════════════════

elif page == "⚙️ Settings":
    st.title("⚙️ Configuration")

    st.subheader("Current Settings")

    with st.expander("📋 Validation Thresholds", expanded=True):
        st.code(f"""
Min Win Rate:       {config.MIN_WIN_RATE:.0%}
Min Profit Factor:  {config.MIN_PROFIT_FACTOR}
Min Sharpe Ratio:   {config.MIN_SHARPE}
Min Trades:         {config.MIN_TRADES}
Validation Window:  {config.VALIDATION_WINDOW_DAYS} days
        """)

    with st.expander("💰 Risk Management"):
        st.code(f"""
Max Position Size:      {config.MAX_POSITION_SIZE_PCT:.0%} of portfolio
Max Portfolio Risk:     {config.MAX_PORTFOLIO_RISK_PCT:.0%} total exposure
Stop Loss:              {config.STOP_LOSS_PCT:.1%}
Take Profit:            {config.TAKE_PROFIT_PCT:.1%}
Risk/Reward Ratio:      1:{config.TAKE_PROFIT_PCT/config.STOP_LOSS_PCT:.0f}
Max Drawdown Halt:      {config.MAX_DRAWDOWN_HALT_PCT:.0%}
Max Correlated Pos:     {config.MAX_CORRELATED_POSITIONS}
        """)

    with st.expander("📈 Pairs Trading"):
        st.code(f"""
Z-Score Entry:     ±{config.PAIRS_ZSCORE_ENTRY}
Z-Score Exit:      ±{config.PAIRS_ZSCORE_EXIT}
Z-Score Stop:      ±{config.PAIRS_ZSCORE_STOP}
Min Half-Life:     {config.PAIRS_MIN_HALF_LIFE} days
Max Half-Life:     {config.PAIRS_MAX_HALF_LIFE} days
Coint. p-value:    {config.PAIRS_COINT_PVALUE}
        """)

    with st.expander("🤖 ML Settings"):
        st.code(f"""
Train/Test Split:   {config.ML_TRAIN_TEST_SPLIT:.0%}
N Estimators:       {config.ML_N_ESTIMATORS}
Min Confidence:     {config.ML_MIN_CONFIDENCE:.0%}
Lookback Bars:      {config.ML_LOOKBACK_BARS}
Retrain Interval:   {config.ML_RETRAIN_DAYS} days
        """)

    with st.expander("🔌 Data Sources"):
        sources = []
        if config.ALPACA_API_KEY:
            sources.append("✅ Alpaca (configured)")
        else:
            sources.append("❌ Alpaca (not configured)")
        if config.ALPHA_VANTAGE_KEY:
            sources.append("✅ Alpha Vantage (configured)")
        else:
            sources.append("❌ Alpha Vantage (not configured)")
        sources.append("✅ yfinance (always available)")

        for s in sources:
            st.markdown(s)

    with st.expander("📢 Alerts"):
        st.markdown(f"**Method:** {config.ALERT_METHOD}")
        if config.TELEGRAM_BOT_TOKEN:
            st.markdown("✅ Telegram configured")
        if config.DISCORD_WEBHOOK_URL:
            st.markdown("✅ Discord configured")
        if config.SMTP_USER:
            st.markdown("✅ Email configured")

    st.markdown("---")
    st.caption("Edit `config.py` and `.env` to change these settings. "
               "Restart the dashboard after making changes.")


# ══════════════════════════════════════════════════════════════
#  FOOTER
# ══════════════════════════════════════════════════════════════

st.sidebar.markdown("---")
st.sidebar.caption("⚠️ Not financial advice. Past performance ≠ future results.")
