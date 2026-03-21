"""
Alert System — console, Telegram, Discord, email notifications.
"""

import json
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

import requests

import config

logger = logging.getLogger(__name__)


def format_signal_alert(result: dict, signal_direction: str, current_price: float) -> str:
    direction = "🟢 BUY" if "BUY" in signal_direction or signal_direction == "1" else "🔴 SELL"
    return f"""
{'='*50}
{direction} SIGNAL — {result.get('symbol', 'N/A')}
{'='*50}
Pattern:        {result.get('pattern', 'N/A')}
Price:          ${current_price:,.2f}

Backtest Stats:
  Win Rate:       {result.get('win_rate', 'N/A')}
  Profit Factor:  {result.get('profit_factor', 'N/A')}
  Total Return:   {result.get('total_return', 'N/A')}
  Max Drawdown:   {result.get('max_drawdown', 'N/A')}
  Sharpe:         {result.get('sharpe', 'N/A')}
  Sortino:        {result.get('sortino', 'N/A')}
  Trades:         {result.get('total_trades', 'N/A')}
  Expectancy:     {result.get('expectancy', 'N/A')}

Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
{'='*50}
""".strip()


def format_pairs_alert(result: dict) -> str:
    return f"""
{'='*50}
📊 PAIRS SIGNAL — {result.get('symbol_a','?')}/{result.get('symbol_b','?')}
{'='*50}
Direction:    {result.get('signal_direction', 'N/A')}
Z-Score:      {result.get('current_zscore', 0):.2f}
Half-Life:    {result.get('half_life', 0):.1f} days
Correlation:  {result.get('correlation', 0):.3f}

Backtest:
  Win Rate:       {result.get('win_rate', 0):.1%}
  Profit Factor:  {result.get('profit_factor', 0):.2f}
  Total Return:   {result.get('total_return', 0):.2%}
  Sharpe:         {result.get('sharpe_ratio', 0):.2f}
  Trades:         {result.get('total_trades', 0)}

Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
{'='*50}
""".strip()


def send_alert(message: str, method: str = None) -> bool:
    method = method or config.ALERT_METHOD
    dispatch = {
        "console": _console, "telegram": _telegram,
        "discord": _discord, "email": _email,
    }
    handler = dispatch.get(method, _console)
    try:
        return handler(message)
    except Exception as e:
        logger.error(f"Alert failed ({method}): {e}")
        if method != "console":
            _console(message)
        return False


def _console(msg: str) -> bool:
    print("\n" + msg + "\n")
    return True


def _telegram(msg: str) -> bool:
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        logger.error("Telegram not configured")
        return False
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    resp = requests.post(url, json={
        "chat_id": config.TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"
    }, timeout=10)
    resp.raise_for_status()
    return True


def _discord(msg: str) -> bool:
    if not config.DISCORD_WEBHOOK_URL:
        return False
    requests.post(config.DISCORD_WEBHOOK_URL, json={"content": msg}, timeout=10).raise_for_status()
    return True


def _email(msg: str) -> bool:
    if not all([config.SMTP_USER, config.SMTP_PASS, config.ALERT_EMAIL_TO]):
        return False
    email = MIMEMultipart()
    email["From"] = config.SMTP_USER
    email["To"] = config.ALERT_EMAIL_TO
    email["Subject"] = "🤖 Trading Bot Alert"
    email.attach(MIMEText(msg, "plain"))
    with smtplib.SMTP(config.SMTP_SERVER, config.SMTP_PORT) as s:
        s.starttls()
        s.login(config.SMTP_USER, config.SMTP_PASS)
        s.send_message(email)
    return True


def send_daily_summary(technical: list, pairs: list, ml_signals: list) -> bool:
    sections = ["📊 DAILY SCAN SUMMARY", "=" * 40]

    if technical:
        sections.append(f"\n🔧 Technical Patterns: {len(technical)} signals")
        for r in technical:
            sections.append(f"  {r.get('direction','?')} {r.get('symbol','?')} — "
                          f"{r.get('pattern','?')} (WR:{r.get('win_rate','?')})")

    if pairs:
        sections.append(f"\n📈 Pairs Trading: {len(pairs)} signals")
        for r in pairs:
            sections.append(f"  {r.get('signal_direction','?')} "
                          f"{r.get('symbol_a','?')}/{r.get('symbol_b','?')} "
                          f"(z={r.get('current_zscore',0):.2f})")

    if ml_signals:
        sections.append(f"\n🤖 ML Signals: {len(ml_signals)} signals")
        for r in ml_signals:
            sections.append(f"  {r.get('direction','?')} {r.get('symbol','?')} "
                          f"(conf:{r.get('confidence','?')})")

    if not (technical or pairs or ml_signals):
        sections.append("\nNo signals triggered today.")

    sections.append(f"\nTime: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    return send_alert("\n".join(sections))
