"""HTML report built from scan results (validated or OOS-positive candidates only)."""
from __future__ import annotations

import html as html_lib
import logging
from datetime import datetime

import config
from services.scan import UNVALIDATED, _num, _pct

logger = logging.getLogger(__name__)


def generate_report(results: dict, output: str = "report.html"):
    """Generate a comprehensive HTML report. Only validated or OOS-positive candidates are ever passed in
    (the legacy in-sample informational tier is retired)."""
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

    def esc(x) -> str:
        return html_lib.escape(str(x))

    if all_signals:
        html += "<h2>🔧 Technical & ML Signals (out-of-sample)</h2>"
        html += (f'<p class="meta">All statistics are walk-forward out-of-sample (before the hold-out start '
                 f'{config.HOLDOUT_START}); in-sample figures are not shown. Only <b>oos_validated</b> signals '
                 f'are order-eligible.</p><table>')
        html += ("<tr><th>Dir</th><th>Symbol</th><th>Pattern</th><th>Validation</th><th>OOS Win Rate</th>"
                 "<th>OOS PF</th><th>OOS Return</th><th>OOS Drawdown</th><th>OOS Sharpe</th>"
                 "<th>OOS Trades</th><th>Hold-out</th><th>BH q</th></tr>")
        for s in all_signals:
            cls = "buy" if "BUY" in s.get("direction", "") else "sell"
            ho, q = s.get("holdout_return"), s.get("bh_adjusted_p")
            html += f"""<tr><td class="{cls}">{esc(s.get('direction','?'))}</td>
            <td>{esc(s.get('symbol','?'))}</td><td>{esc(s.get('pattern', 'ml_ensemble'))}</td>
            <td>{esc(s.get('validation_status', UNVALIDATED))}</td>
            <td>{esc(s.get('win_rate','n/a'))}</td><td>{esc(s.get('profit_factor','n/a'))}</td>
            <td>{esc(s.get('total_return','n/a'))}</td><td>{esc(s.get('max_drawdown','n/a'))}</td>
            <td>{esc(s.get('sharpe','n/a'))}</td><td>{esc(s.get('total_trades','n/a'))}</td>
            <td>{esc(_pct(ho) if isinstance(ho, (int, float)) else 'n/a')}</td>
            <td>{esc(_num(q, 3) if isinstance(q, (int, float)) else 'n/a')}</td></tr>"""
        html += "</table>"

    if pairs:
        html += "<h2>📈 Pairs / Stat-Arb Signals (alert-only, in-sample backtest)</h2><table>"
        html += ("<tr><th>Dir</th><th>Pair</th><th>Z-Score</th><th>Half-Life</th><th>BH-adj. p</th>"
                 "<th>Win Rate</th><th>PF</th><th>Return</th><th>Sharpe</th></tr>")
        for p in pairs:
            hl = p.get("half_life")
            html += f"""<tr><td>{esc(p.get('signal_direction','?'))}</td>
            <td>{esc(p.get('symbol_a','?'))}/{esc(p.get('symbol_b','?'))}</td>
            <td>{esc(_num(p.get('current_zscore')))}</td><td>{esc(_num(hl, 1) + 'd' if hl is not None else 'n/a')}</td>
            <td>{esc(_num(p.get('adj_pvalue'), 3))}</td>
            <td>{esc(_pct(p.get('win_rate')))}</td><td>{esc(_num(p.get('profit_factor')))}</td>
            <td>{esc(_pct(p.get('total_return'), 2))}</td><td>{esc(_num(p.get('sharpe_ratio')))}</td></tr>"""
        html += "</table>"

    html += """<p class="meta">⚠️ This is a decision-support tool, not financial advice.
Past performance does not guarantee future results. Always manage risk.</p>
</body></html>"""

    with open(output, "w") as f:
        f.write(html)
    logger.info(f"Report saved to {output}")
