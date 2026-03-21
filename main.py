#!/usr/bin/env python3
"""
Trading Pattern Bot v2 — Main Scanner

Runs the full pipeline: fetch → detect → backtest → validate → stress test → alert.

Usage:
    python main.py                          # Full scan (technical + pairs + ML)
    python main.py --mode technical         # Technical patterns only
    python main.py --mode pairs             # Pairs/stat-arb only
    python main.py --mode ml                # ML patterns only
    python main.py --symbol AAPL            # Single symbol
    python main.py --market crypto          # Single market
    python main.py --stress AAPL ema_crossover  # Full stress test on one combo
    python main.py --schedule --time 08:00  # Run daily
    python main.py --report                 # Generate HTML report
    python main.py --paper                  # Enable paper trade execution
"""

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import schedule

import config
from data_fetcher import fetch_ohlcv, fetch_watchlist
from patterns import PATTERN_REGISTRY, run_all_patterns
from ml_patterns import MLPatternDetector, ml_pattern_signal
from pairs_trading import scan_all_pairs
from backtester import classic_backtest, walk_forward_validate, BacktestResult
from stress_test import full_stress_test
from risk_manager import RiskManager
from alerts import (
    format_signal_alert, format_pairs_alert,
    send_alert, send_daily_summary,
)
from claude_integration import generate_summary
from paper_trader import execute_signal, get_account, get_positions

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

risk_mgr = RiskManager()


def check_recent_signal(df: pd.DataFrame, window: int = config.VALIDATION_WINDOW_DAYS) -> int:
    if "signal" not in df.columns or len(df) < 2:
        return 0
    recent = df.tail(window)["signal"]
    if (recent == 1).any():
        return 1
    elif (recent == -1).any():
        return -1
    return 0


# ══════════════════════════════════════════════════════════════
#  TECHNICAL PATTERN SCAN
# ══════════════════════════════════════════════════════════════

def scan_technical(
    data: dict[str, pd.DataFrame],
    patterns: list[str] | None = None,
    run_stress: bool = False,
    paper_trade: bool = False,
) -> list[dict]:
    """Scan all symbols with all technical patterns."""
    logger.info("\n🔧 TECHNICAL PATTERN SCAN")
    logger.info("=" * 50)

    pattern_list = patterns or list(PATTERN_REGISTRY.keys())
    all_triggered = []

    for symbol, df in data.items():
        logger.info(f"\n─── {symbol} ({len(df)} bars) ───")

        for pat_name in pattern_list:
            if pat_name not in PATTERN_REGISTRY:
                continue

            pat_func = PATTERN_REGISTRY[pat_name]

            try:
                signals_df = pat_func(df)
                result = classic_backtest(signals_df, symbol, pat_name)

                if not result.is_valid:
                    continue

                recent = check_recent_signal(signals_df)
                if recent == 0:
                    continue

                # Valid + recently triggered
                current_price = float(df["Close"].iloc[-1])
                summary = result.summary()
                summary["direction"] = "🟢 BUY" if recent == 1 else "🔴 SELL"
                summary["current_price"] = current_price
                summary["signal_value"] = recent

                # Optional stress test
                if run_stress:
                    stress = full_stress_test(df, symbol, pat_func, pat_name)
                    summary["stress_test"] = stress.get("assessment", {})
                    summary["monte_carlo"] = stress.get("monte_carlo", {})

                all_triggered.append(summary)

                alert_msg = format_signal_alert(summary, summary["direction"], current_price)
                send_alert(alert_msg)

                # Paper trade execution
                if paper_trade:
                    confidence = result.win_rate * result.profit_factor
                    execute_signal(symbol, recent, confidence, current_price)

                logger.info(f"  ✅ {pat_name}: {summary['direction']} "
                          f"WR={summary['win_rate']} PF={summary['profit_factor']}")

            except Exception as e:
                logger.warning(f"  {pat_name}: error — {e}")

    return all_triggered


# ══════════════════════════════════════════════════════════════
#  PAIRS / STAT-ARB SCAN
# ══════════════════════════════════════════════════════════════

def scan_pairs(
    data: dict[str, pd.DataFrame],
    paper_trade: bool = False,
) -> list[dict]:
    """Scan all configured pairs for stat-arb opportunities."""
    logger.info("\n📈 PAIRS TRADING SCAN")
    logger.info("=" * 50)

    results = scan_all_pairs(data)
    triggered = [r for r in results if r.get("has_signal")]

    for r in triggered:
        alert_msg = format_pairs_alert(r)
        send_alert(alert_msg)

        if paper_trade and r.get("signal_direction") != "NONE":
            # For pairs, we'd need to execute both legs
            logger.info(f"  📝 Pairs paper trade: {r['signal_direction']} "
                       f"{r['symbol_a']}/{r['symbol_b']}")

    logger.info(f"\nPairs scanned: {len(results)} | Signals: {len(triggered)}")
    return triggered


# ══════════════════════════════════════════════════════════════
#  ML PATTERN SCAN
# ══════════════════════════════════════════════════════════════

def scan_ml(
    data: dict[str, pd.DataFrame],
    paper_trade: bool = False,
) -> list[dict]:
    """Run ML models on all symbols."""
    logger.info("\n🤖 ML PATTERN SCAN")
    logger.info("=" * 50)

    all_signals = []

    for symbol, df in data.items():
        if len(df) < 200:
            continue

        try:
            logger.info(f"\n─── {symbol} ───")
            detector = MLPatternDetector()
            metrics = detector.train(df)

            mean_acc = metrics.get("mean_cv_accuracy", 0)
            logger.info(f"  Model accuracy: {mean_acc:.3f}")

            if mean_acc < 0.52:
                logger.info(f"  Skipping — accuracy too low")
                continue

            # Predict on full data (walk-forward)
            pred_df = detector.predict(df)

            recent = check_recent_signal(pred_df)
            if recent == 0:
                continue

            # Get confidence for most recent signal
            recent_conf = float(pred_df["ml_confidence"].iloc[-1])

            # Backtest the ML signals
            bt_result = classic_backtest(pred_df, symbol, "ml_ensemble")

            if bt_result.total_trades < 5:
                continue

            signal_info = {
                "symbol": symbol,
                "direction": "🟢 BUY" if recent == 1 else "🔴 SELL",
                "confidence": f"{recent_conf:.2f}",
                "model_accuracy": f"{mean_acc:.3f}",
                "win_rate": f"{bt_result.win_rate:.1%}",
                "profit_factor": f"{bt_result.profit_factor:.2f}",
                "total_return": f"{bt_result.total_return_pct:.2%}",
                "total_trades": bt_result.total_trades,
                "top_features": list(metrics.get("top_features", {}).keys())[:5],
            }

            all_signals.append(signal_info)

            logger.info(f"  ✅ ML Signal: {signal_info['direction']} "
                       f"conf={recent_conf:.2f} WR={bt_result.win_rate:.1%}")

            if paper_trade:
                execute_signal(symbol, recent, recent_conf, float(df["Close"].iloc[-1]))

        except Exception as e:
            logger.warning(f"  ML scan failed for {symbol}: {e}")

    return all_signals


# ══════════════════════════════════════════════════════════════
#  FULL SCAN
# ══════════════════════════════════════════════════════════════

def run_full_scan(
    markets: list[str] | None = None,
    patterns: list[str] | None = None,
    symbol_filter: str | None = None,
    modes: list[str] | None = None,
    run_stress: bool = False,
    paper_trade: bool = False,
) -> dict:
    """Run the complete scan pipeline."""
    modes = modes or ["technical", "pairs", "ml"]

    logger.info("\n" + "=" * 60)
    logger.info("🤖 TRADING BOT v2 — FULL SCAN")
    logger.info(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"Modes: {', '.join(modes)}")
    logger.info("=" * 60)

    # Fetch data
    if symbol_filter:
        data = {symbol_filter: fetch_ohlcv(symbol_filter)}
        data = {k: v for k, v in data.items() if not v.empty}
    else:
        data = fetch_watchlist(markets)

    if not data:
        logger.error("No data fetched. Check connections and symbols.")
        return {}

    logger.info(f"Data fetched for {len(data)} symbols\n")

    results = {
        "technical": [],
        "pairs": [],
        "ml": [],
    }

    # Run scans
    if "technical" in modes:
        results["technical"] = scan_technical(data, patterns, run_stress, paper_trade)

    if "pairs" in modes:
        results["pairs"] = scan_pairs(data, paper_trade)

    if "ml" in modes:
        results["ml"] = scan_ml(data, paper_trade)

    # Summary
    total = len(results["technical"]) + len(results["pairs"]) + len(results["ml"])
    logger.info("\n" + "=" * 60)
    logger.info(f"SCAN COMPLETE — {total} total signals")
    logger.info(f"  Technical: {len(results['technical'])}")
    logger.info(f"  Pairs:     {len(results['pairs'])}")
    logger.info(f"  ML:        {len(results['ml'])}")
    logger.info("=" * 60)

    # Daily summary alert
    send_daily_summary(results["technical"], results["pairs"], results["ml"])

    # AI summary
    if config.ANTHROPIC_API_KEY and total > 0:
        logger.info("\n📝 Generating AI summary...")
        ai = generate_summary(results["technical"], results["pairs"], results["ml"])
        if ai:
            print(f"\n{'='*50}\n📝 AI ANALYSIS\n{'='*50}\n{ai}\n")
            send_alert(f"📝 AI ANALYSIS:\n\n{ai}")

    # Paper trading status
    if paper_trade:
        acct = get_account()
        if acct:
            logger.info(f"\n💰 Paper Account: ${acct['equity']:,.2f} "
                       f"(cash: ${acct['cash']:,.2f})")
        positions = get_positions()
        if positions:
            logger.info(f"📂 Open positions: {len(positions)}")
            for p in positions:
                logger.info(f"  {p['symbol']}: {p['qty']} shares, "
                          f"P&L: ${p['unrealized_pl']:,.2f} ({p['unrealized_plpc']:.2%})")

    return results


# ══════════════════════════════════════════════════════════════
#  REPORT GENERATION
# ══════════════════════════════════════════════════════════════

def generate_report(results: dict, output: str = "report.html"):
    """Generate a comprehensive HTML report."""
    all_signals = results.get("technical", []) + results.get("ml", [])
    pairs = results.get("pairs", [])

    html = f"""<!DOCTYPE html>
<html><head><title>Trading Bot Report — {datetime.now().strftime('%Y-%m-%d')}</title>
<style>
body {{ font-family: -apple-system, sans-serif; max-width: 1400px; margin: 0 auto;
       padding: 20px; background: #0a0a0a; color: #e0e0e0; }}
h1 {{ color: #00ff88; }} h2 {{ color: #88bbff; margin-top: 30px; }}
table {{ border-collapse: collapse; width: 100%; margin: 15px 0; }}
th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #333; }}
th {{ background: #1a1a2e; color: #00ff88; }}
tr:hover {{ background: #1a1a2e; }}
.buy {{ color: #00ff88; }} .sell {{ color: #ff4444; }}
.section {{ background: #111; padding: 15px; border-radius: 8px; margin: 15px 0; }}
.meta {{ color: #666; font-size: 0.85em; }}
</style></head><body>
<h1>🤖 Trading Bot v2 Report</h1>
<p class="meta">Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} |
Technical: {len(results.get('technical', []))} |
Pairs: {len(pairs)} |
ML: {len(results.get('ml', []))}</p>
"""

    if all_signals:
        html += "<h2>🔧 Technical & ML Signals</h2><table>"
        html += ("<tr><th>Dir</th><th>Symbol</th><th>Pattern</th><th>Win Rate</th>"
                "<th>PF</th><th>Return</th><th>Drawdown</th><th>Sharpe</th><th>Trades</th></tr>")
        for s in all_signals:
            cls = "buy" if "BUY" in s.get("direction", "") else "sell"
            html += f"""<tr><td class="{cls}">{s.get('direction','?')}</td>
            <td>{s.get('symbol','?')}</td><td>{s.get('pattern','?')}</td>
            <td>{s.get('win_rate','?')}</td><td>{s.get('profit_factor','?')}</td>
            <td>{s.get('total_return','?')}</td><td>{s.get('max_drawdown','?')}</td>
            <td>{s.get('sharpe','?')}</td><td>{s.get('total_trades','?')}</td></tr>"""
        html += "</table>"

    if pairs:
        html += "<h2>📈 Pairs / Stat-Arb Signals</h2><table>"
        html += ("<tr><th>Dir</th><th>Pair</th><th>Z-Score</th><th>Half-Life</th>"
                "<th>Win Rate</th><th>PF</th><th>Return</th><th>Sharpe</th></tr>")
        for p in pairs:
            html += f"""<tr><td>{p.get('signal_direction','?')}</td>
            <td>{p.get('symbol_a','?')}/{p.get('symbol_b','?')}</td>
            <td>{p.get('current_zscore',0):.2f}</td><td>{p.get('half_life',0):.1f}d</td>
            <td>{p.get('win_rate',0):.1%}</td><td>{p.get('profit_factor',0):.2f}</td>
            <td>{p.get('total_return',0):.2%}</td><td>{p.get('sharpe_ratio',0):.2f}</td></tr>"""
        html += "</table>"

    html += """<p class="meta">⚠️ This is a decision-support tool, not financial advice.
Past performance does not guarantee future results. Always manage risk.</p>
</body></html>"""

    with open(output, "w") as f:
        f.write(html)
    logger.info(f"Report saved to {output}")


# ══════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Trading Pattern Bot v2")
    parser.add_argument("--mode", nargs="+", choices=["technical", "pairs", "ml"],
                        help="Scan modes to run")
    parser.add_argument("--symbol", type=str, help="Scan single symbol")
    parser.add_argument("--pattern", type=str, nargs="+", help="Specific patterns")
    parser.add_argument("--market", type=str, nargs="+",
                        choices=["stocks", "crypto", "forex", "futures"])
    parser.add_argument("--stress", nargs=2, metavar=("SYMBOL", "PATTERN"),
                        help="Full stress test on symbol/pattern combo")
    parser.add_argument("--schedule", action="store_true", help="Run on schedule")
    parser.add_argument("--time", type=str, default="08:00", help="Schedule time HH:MM")
    parser.add_argument("--report", action="store_true", help="Generate HTML report")
    parser.add_argument("--paper", action="store_true", help="Enable paper trading")

    args = parser.parse_args()

    # Single stress test mode
    if args.stress:
        sym, pat = args.stress
        logger.info(f"Running full stress test: {sym} / {pat}")
        df = fetch_ohlcv(sym)
        if df.empty:
            logger.error(f"No data for {sym}")
            return
        if pat not in PATTERN_REGISTRY:
            logger.error(f"Unknown pattern: {pat}")
            return
        report = full_stress_test(df, sym, PATTERN_REGISTRY[pat], pat)
        import json
        print(json.dumps(report, indent=2, default=str))
        return

    # Scheduled mode
    if args.schedule:
        def job():
            try:
                results = run_full_scan(
                    markets=args.market, patterns=args.pattern,
                    symbol_filter=args.symbol, modes=args.mode,
                    paper_trade=args.paper,
                )
                if args.report:
                    generate_report(results)
            except Exception as e:
                logger.error(f"Scheduled scan failed: {e}")
                send_alert(f"⚠️ Scan failed: {e}")

        logger.info(f"Scheduling daily scan at {args.time}")
        schedule.every().day.at(args.time).do(job)
        logger.info("Bot running. Press Ctrl+C to stop.\n")
        while True:
            schedule.run_pending()
            time.sleep(60)

    # One-shot scan
    results = run_full_scan(
        markets=args.market,
        patterns=args.pattern,
        symbol_filter=args.symbol,
        modes=args.mode,
        paper_trade=args.paper,
    )

    if args.report:
        generate_report(results)


if __name__ == "__main__":
    main()
