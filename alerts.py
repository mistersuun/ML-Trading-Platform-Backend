"""
Alert System — console, Telegram, Discord, email notifications.
"""

import html
import logging
import smtplib
import time
from dataclasses import dataclass
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

import requests

import config
import logging_setup

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


TELEGRAM_LIMIT = 4096
DISCORD_LIMIT = 2000
MAX_RETRIES = 2
ALERT_KINDS = ("signal", "order_decision", "halt", "reconcile_mismatch", "briefing_failed", "heartbeat")

# Injectable so tests never really sleep.
_sleep = time.sleep


@dataclass
class AlertResult:
    sent: bool
    channel: str = ""
    status: str = ""
    error: str = ""

    def __bool__(self) -> bool:
        return self.sent


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


def _http_error(e: Exception) -> str:
    """Type + status only: requests errors embed the URL (and so the bot token)."""
    resp = getattr(e, "response", None)
    status = getattr(resp, "status_code", None)
    return f"{type(e).__name__}" + (f" status={status}" if status else "")


def _retry_after(resp) -> float:
    try:
        return float(resp.json()["parameters"]["retry_after"])
    except Exception:
        pass
    try:
        return float(resp.headers.get("Retry-After"))
    except Exception:
        return 1.0


def _post_with_retry(url: str, payload: dict, fallback_payload: dict = None):
    """POST with 429/5xx retries (max 2) and one plain-text retry on a 400.
    Returns the final response, or None if a network error exhausted the retries."""
    resp = None
    retries = 0
    plain_tried = False
    while True:
        try:
            resp = requests.post(url, json=payload, timeout=10)
        except requests.RequestException as e:
            logger.error("Alert POST failed: %s", _http_error(e))
            resp = None
        code = getattr(resp, "status_code", None)
        if resp is not None and code is not None and code < 400:
            return resp
        if code == 400 and fallback_payload is not None and not plain_tried:
            plain_tried = True
            payload = fallback_payload
            continue
        if retries < MAX_RETRIES and (resp is None or code == 429 or code >= 500):
            wait = _retry_after(resp) if code == 429 else 0.5 * (2 ** retries)
            retries += 1
            _sleep(min(wait, 30))
            continue
        return resp


def _status_of(resp) -> str:
    return str(getattr(resp, "status_code", "no_response"))


def send_alert(message: str, method: str = None, kind: str = "signal",
               dedup_key: str = None, conn=None) -> AlertResult:
    method = method or config.ALERT_METHOD
    message = logging_setup.redact(message)  # a secret in an alert body must never leave the process
    if conn is not None and dedup_key:
        if conn.execute("SELECT 1 FROM alerts_sent WHERE dedup_key = ?", (dedup_key,)).fetchone():
            return AlertResult(False, method, "deduplicated")
    dispatch = {
        "console": _console, "telegram": _telegram,
        "discord": _discord, "email": _email,
    }
    handler = dispatch.get(method, _console)
    try:
        status = handler(message)
    except Exception as e:
        logger.error("Alert failed (%s, kind=%s): %s", method, kind, _http_error(e))
        if method != "console":
            _console(message)
        return AlertResult(False, method, "error", _http_error(e))
    if status is True:
        status = "ok"
    if status != "ok":
        logger.error("Alert not delivered (%s, kind=%s): %s", method, kind, status)
        if method != "console":
            _console(message)
        return AlertResult(False, method, "failed", str(status))
    if conn is not None and dedup_key:
        conn.execute(
            "INSERT OR IGNORE INTO alerts_sent (dedup_key, sent_at, channel, kind) VALUES (?,?,?,?)",
            (dedup_key, datetime.utcnow().isoformat(timespec="seconds"), method, kind))
        conn.commit()
    return AlertResult(True, method, "ok")


def _console(msg: str):
    print("\n" + msg + "\n")
    return "ok"


def _telegram(msg: str):
    """Returns "ok" or a failure description (never contains the URL/token)."""
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        return "telegram not configured"
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    raw = msg
    while True:  # truncate so the *escaped* text fits the limit
        text = html.escape(_truncate(raw, TELEGRAM_LIMIT), quote=False)
        if len(text) <= TELEGRAM_LIMIT:
            break
        raw = raw[: len(raw) - max(1, len(text) - TELEGRAM_LIMIT)]
    payload = {"chat_id": config.TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
    plain = {"chat_id": config.TELEGRAM_CHAT_ID, "text": _truncate(msg, TELEGRAM_LIMIT)}
    resp = _post_with_retry(url, payload, plain)
    if resp is not None and resp.status_code < 400:
        return "ok"
    return f"telegram http {_status_of(resp)}"


def _discord(msg: str):
    if not config.DISCORD_WEBHOOK_URL:
        return "discord not configured"
    resp = _post_with_retry(config.DISCORD_WEBHOOK_URL,
                            {"content": _truncate(msg, DISCORD_LIMIT)})
    if resp is not None and resp.status_code < 400:
        return "ok"
    return f"discord http {_status_of(resp)}"


def _email(msg: str):
    if not all([config.SMTP_USER, config.SMTP_PASS, config.ALERT_EMAIL_TO]):
        return "email not configured"
    email = MIMEMultipart()
    email["From"] = config.SMTP_USER
    email["To"] = config.ALERT_EMAIL_TO
    email["Subject"] = "🤖 Trading Bot Alert"
    email.attach(MIMEText(msg, "plain"))
    with smtplib.SMTP(config.SMTP_SERVER, config.SMTP_PORT) as s:
        s.starttls()
        s.login(config.SMTP_USER, config.SMTP_PASS)
        s.send_message(email)
    return "ok"


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
    return bool(send_alert("\n".join(sections)))
