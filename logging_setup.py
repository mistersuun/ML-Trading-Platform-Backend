"""Log redaction: mask configured secrets and token-shaped strings in every log record."""
import logging
import re

import config

_SECRET_KEYS = (
    "ALPACA_API_KEY", "ALPACA_SECRET_KEY", "TELEGRAM_BOT_TOKEN", "DISCORD_WEBHOOK_URL",
    "SMTP_PASS", "ANTHROPIC_API_KEY", "ALPHA_VANTAGE_KEY", "POLYGON_API_KEY",
)
_PATTERNS = (
    (re.compile(r"bot\d+:[A-Za-z0-9_-]+"), "bot***"),
    (re.compile(r"apikey=[^&\s]+"), "apikey=***"),
    (re.compile(r"/api/webhooks/\d+/[A-Za-z0-9_-]+"), "/api/webhooks/***"),
)
MASK = "***"


def _secrets() -> list:
    vals = {str(getattr(config, k, "") or "") for k in _SECRET_KEYS}
    hook = str(getattr(config, "DISCORD_WEBHOOK_URL", "") or "")
    if "/api/webhooks/" in hook:  # retry warnings log only the path, never the full URL
        vals.add(hook.split("/api/webhooks/", 1)[1].strip("/"))
        vals.add("/api/webhooks/" + hook.split("/api/webhooks/", 1)[1].strip("/"))
        token = hook.rstrip("/").rsplit("/", 1)[-1]
        if len(token) >= 8:
            vals.add(token)
    return sorted((v for v in vals if v), key=len, reverse=True)  # longest first


def redact(text: str) -> str:
    for s in _secrets():
        text = text.replace(s, MASK)
    for rx, repl in _PATTERNS:
        text = rx.sub(repl, text)
    return text


def _redact_arg(a):
    """Mask a log argument of any type. Containers and objects are rendered with repr/str first,
    so the secret cannot hide inside a list, bytes or a custom object."""
    if isinstance(a, str):
        return redact(a)
    if a is None or isinstance(a, (bool, int, float)):
        return a
    try:
        return redact(str(a))
    except Exception:
        return "<unprintable>"


class RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()  # render once, then redact the final text
        except Exception:
            msg = str(record.msg)
        record.msg = redact(msg)
        record.args = ()
        if record.exc_info and record.exc_info[1] is not None:
            record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = redact(record.exc_text)
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        return True


def install_redaction(logger: logging.Logger = None) -> RedactionFilter:
    """Attach the filter to `logger` (default root) and its handlers. Idempotent."""
    logger = logger or logging.getLogger()
    for f in logger.filters:
        if isinstance(f, RedactionFilter):
            flt = f
            break
    else:
        flt = RedactionFilter()
        logger.addFilter(flt)
    # Logger filters only apply to records logged directly on this logger, so also
    # guard handlers (which see records propagated from child loggers).
    for h in logger.handlers:
        if not any(isinstance(f, RedactionFilter) for f in h.filters):
            h.addFilter(flt)
    if logger is logging.getLogger():
        # Records from child loggers skip the root *logger's* filters and reach handlers
        # added later (e.g. test capture), so redact at record creation too.
        factory = logging.getLogRecordFactory()
        if not getattr(factory, "_redacting", False):
            def redacting_factory(*a, **k):
                record = factory(*a, **k)
                flt.filter(record)
                return record
            redacting_factory._redacting = True
            logging.setLogRecordFactory(redacting_factory)
    return flt
