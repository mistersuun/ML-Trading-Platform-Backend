"""
Claude Integration — AI-powered analysis summaries.
Optional: works without an API key, the bot functions fully without it.
"""

import json
import logging

import requests

import config

logger = logging.getLogger(__name__)


def generate_summary(
    technical_signals: list[dict],
    pairs_signals: list[dict],
    ml_signals: list[dict],
    stress_results: list[dict] = None,
) -> str | None:
    if not config.ANTHROPIC_API_KEY:
        return None

    prompt = f"""You are a quantitative trading analyst. Analyze these automated scan results
and provide a concise, actionable daily briefing.

TECHNICAL PATTERN SIGNALS:
{json.dumps(technical_signals, indent=2, default=str)}

PAIRS / STAT-ARB SIGNALS:
{json.dumps(pairs_signals, indent=2, default=str)}

ML MODEL SIGNALS:
{json.dumps(ml_signals, indent=2, default=str)}

{f"STRESS TEST HIGHLIGHTS: {json.dumps(stress_results, indent=2, default=str)}" if stress_results else ""}

Provide:
1. Top 3 most compelling opportunities and WHY (cross-reference backtest quality)
2. Red flags — any signals that look suspicious (overfitting, low sample, poor regime performance)
3. Pairs that are currently at extreme z-scores — actionable now?
4. ML confidence assessment — are the models agreeing with technical patterns?
5. Risk summary — recommended total exposure for today given the signals

Be direct. No filler. Under 400 words. End with a 1-sentence overall bias (bullish/bearish/neutral)."""

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "Content-Type": "application/json",
                "x-api-key": config.ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
            },
            json={
                "model": config.CLAUDE_MODEL,
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["content"][0]["text"]
    except Exception as e:
        logger.error(f"Claude API failed: {e}")
        return None
