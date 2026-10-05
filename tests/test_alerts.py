"""WS1.7 acceptance: reliable, redacted alerts. Offline; no real sleeping."""
import logging
import sqlite3

import pytest
import requests

import alerts
import config
import logging_setup

TOKEN = "123456:ABCdef"


def _resp(status, body=None):
    r = requests.Response()
    r.status_code = status
    if body is not None:
        import json as _j
        r._content = _j.dumps(body).encode()
    return r


@pytest.fixture
def tg(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "42")
    sleeps = []
    monkeypatch.setattr(alerts, "_sleep", sleeps.append)
    s = type("S", (), {})()
    s.calls, s.sleeps, s.script = [], sleeps, []

    def post(url, json=None, **kw):
        s.calls.append(json)
        return s.script.pop(0) if s.script else _resp(200)

    monkeypatch.setattr(alerts.requests, "post", post)
    return s


def test_html_escaped_no_400(tg):
    r = alerts.send_alert("ema_crossover *[x]* a.b! <b> & co", method="telegram")
    assert r and r.sent and r.channel == "telegram"
    assert tg.calls[0]["parse_mode"] == "HTML"
    assert tg.calls[0]["text"] == "ema_crossover *[x]* a.b! &lt;b&gt; &amp; co"


def test_400_parse_error_one_plain_retry(tg):
    tg.script = [_resp(400), _resp(200)]
    r = alerts.send_alert("a <b", method="telegram")
    assert r.sent and len(tg.calls) == 2
    assert "parse_mode" not in tg.calls[1] and tg.calls[1]["text"] == "a <b"


def test_persistent_400_fails_after_one_plain_retry(tg):
    tg.script = [_resp(400), _resp(400)]
    r = alerts.send_alert("x", method="telegram")
    assert not r and not r.sent and len(tg.calls) == 2


def test_truncation(tg):
    alerts.send_alert("x" * 6000, method="telegram")
    assert len(tg.calls[0]["text"]) <= 4096
    alerts.send_alert("<" * 6000, method="telegram")  # escaping expands the text
    assert len(tg.calls[1]["text"]) <= 4096


def test_discord_truncation(monkeypatch):
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "https://discord.test/hook")
    seen = []
    monkeypatch.setattr(alerts.requests, "post",
                        lambda url, json=None, **k: seen.append(json) or _resp(204))
    assert alerts.send_alert("y" * 5000, method="discord").sent
    assert len(seen[0]["content"]) <= 2000


def test_429_retry_after_honoured(tg):
    tg.script = [_resp(429, {"parameters": {"retry_after": 1}}), _resp(200)]
    assert alerts.send_alert("hi", method="telegram").sent
    assert len(tg.calls) == 2 and tg.sleeps == [1.0]


def test_5xx_backoff_max_two_retries(tg):
    tg.script = [_resp(502), _resp(503), _resp(500), _resp(200)]
    r = alerts.send_alert("hi", method="telegram")
    assert not r and len(tg.calls) == 3 and len(tg.sleeps) == 2


def test_5xx_then_ok(tg):
    tg.script = [_resp(500), _resp(200)]
    assert alerts.send_alert("hi", method="telegram").sent


def test_token_not_in_logs_on_404(tg, caplog):
    logging_setup.install_redaction()
    tg.script = [_resp(404)]
    with caplog.at_level(logging.DEBUG):
        r = alerts.send_alert("hi", method="telegram")
    assert not r and caplog.records
    assert TOKEN not in caplog.text and "ABCdef" not in caplog.text


def test_network_error_does_not_log_url(tg, monkeypatch, caplog):
    def boom(url, json=None, **kw):
        raise requests.ConnectionError(f"failed {url}")
    monkeypatch.setattr(alerts.requests, "post", boom)
    with caplog.at_level(logging.DEBUG):
        r = alerts.send_alert("hi", method="telegram")
    assert not r and TOKEN not in caplog.text


def test_redaction_masks_alpaca_secret_and_patterns(monkeypatch, caplog):
    monkeypatch.setattr(config, "ALPACA_SECRET_KEY", "SUPERSECRETKEY123")
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "")
    logging_setup.install_redaction()
    log = logging.getLogger("redaction-test")
    with caplog.at_level(logging.INFO):
        log.info("key is SUPERSECRETKEY123")
        log.info("secret %s", "SUPERSECRETKEY123")
        log.info("url https://api.telegram.org/bot999:AAbb_cc-d/sendMessage?apikey=zzz123&x=1")
    assert "SUPERSECRETKEY123" not in caplog.text
    assert "999:AAbb_cc-d" not in caplog.text and "zzz123" not in caplog.text


def test_install_redaction_idempotent():
    lg = logging.getLogger("idem-test")
    f1 = logging_setup.install_redaction(lg)
    f2 = logging_setup.install_redaction(lg)
    assert f1 is f2 and len(lg.filters) == 1


def test_dedup_sends_once(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE alerts_sent (id INTEGER PRIMARY KEY, dedup_key TEXT NOT NULL UNIQUE,"
                 " sent_at TEXT NOT NULL, channel TEXT, kind TEXT)")
    out = []
    monkeypatch.setattr(alerts, "_console", lambda m: out.append(m) or "ok")
    r1 = alerts.send_alert("m", method="console", kind="halt", dedup_key="k1", conn=conn)
    r2 = alerts.send_alert("m", method="console", kind="halt", dedup_key="k1", conn=conn)
    r3 = alerts.send_alert("m", method="console", kind="halt", dedup_key="k2", conn=conn)
    assert r1.sent and not r2.sent and r2.status == "deduplicated" and r3.sent
    assert len(out) == 2
    assert conn.execute("SELECT kind FROM alerts_sent WHERE dedup_key='k1'").fetchone() == ("halt",)


def test_failed_send_not_recorded_for_dedup(tg):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE alerts_sent (id INTEGER PRIMARY KEY, dedup_key TEXT NOT NULL UNIQUE,"
                 " sent_at TEXT NOT NULL, channel TEXT, kind TEXT)")
    tg.script = [_resp(404)]
    assert not alerts.send_alert("m", method="telegram", dedup_key="k", conn=conn)
    assert conn.execute("SELECT COUNT(*) FROM alerts_sent").fetchone()[0] == 0


def test_alert_result_truthiness():
    assert alerts.AlertResult(True) and not alerts.AlertResult(False)


# ── redaction gaps: non-string args, partial webhook path, stack_info, alert bodies ──

SECRET = "SUPERSECRETKEY123"


@pytest.fixture
def secret_logger(monkeypatch):
    monkeypatch.setattr(config, "ALPACA_SECRET_KEY", SECRET)
    logging_setup.install_redaction()
    return logging.getLogger("redaction-gaps")


@pytest.mark.parametrize("arg", [[SECRET], (SECRET, 1), {"k": SECRET}, SECRET.encode(), {SECRET},
                                 type("Obj", (), {"__repr__": lambda s: f"Obj({SECRET})"})()])
def test_redaction_masks_non_string_args(secret_logger, caplog, arg):
    with caplog.at_level(logging.INFO):
        secret_logger.info("value %s", arg)
        secret_logger.info("value %r", arg)
    assert caplog.records and SECRET not in caplog.text


def test_redaction_masks_dict_args_and_stack_info(secret_logger, caplog):
    with caplog.at_level(logging.INFO):
        secret_logger.info("%(k)s", {"k": SECRET})
        secret_logger.info("with stack", stack_info=True, extra={"x": 1})
    assert SECRET not in caplog.text
    rec = logging.LogRecord("n", logging.INFO, __file__, 1, "m", (), None)
    rec.stack_info = f"Stack trace {SECRET}"
    logging_setup.RedactionFilter().filter(rec)
    assert SECRET not in rec.stack_info


def test_redaction_masks_partial_discord_webhook_path(monkeypatch, caplog):
    monkeypatch.setattr(config, "DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/123456789/tok_EN-abc")
    logging_setup.install_redaction()
    with caplog.at_level(logging.INFO):  # what urllib3 retry warnings print: just the path
        logging.getLogger("urllib3").info("Retrying: /api/webhooks/123456789/tok_EN-abc")
        logging.getLogger("urllib3").info("token piece 123456789/tok_EN-abc and tok_EN-abc")
        logging.getLogger("urllib3").info("Retrying: /api/webhooks/999/OtherTokenXYZ")  # unconfigured: pattern
    assert "tok_EN-abc" not in caplog.text and "OtherTokenXYZ" not in caplog.text


def test_alert_body_containing_a_secret_is_masked(monkeypatch, capsys):
    monkeypatch.setattr(config, "ALPACA_SECRET_KEY", SECRET)
    r = alerts.send_alert(f"oops the key is {SECRET}", method="console")
    out = capsys.readouterr().out
    assert r.sent and SECRET not in out and "***" in out


def test_alert_body_masked_on_telegram_payload(tg, monkeypatch):
    monkeypatch.setattr(config, "ALPACA_SECRET_KEY", SECRET)
    assert alerts.send_alert(f"key {SECRET}", method="telegram").sent
    assert SECRET not in str(tg.calls)
