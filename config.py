"""
Trading Bot v2 — Configuration
All settings centralized here. Override via .env file.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent

# ══════════════════════════════════════════════════════════════
#  WATCHLIST — Symbols to scan across all markets
# ══════════════════════════════════════════════════════════════

WATCHLIST = {
    "stocks": [
        "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "META", "GOOG", "AMD",
        "SPY", "QQQ", "IWM", "DIA",
        "JPM", "GS", "BAC",
        "XOM", "CVX",
    ],
    "crypto": [
        "BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD",
        "ADA-USD", "AVAX-USD", "DOGE-USD", "LINK-USD",
    ],
    "forex": [
        "EURUSD=X", "GBPUSD=X", "USDJPY=X", "AUDUSD=X",
        "USDCAD=X", "USDCHF=X", "NZDUSD=X",
    ],
    "futures": [
        "GC=F", "SI=F", "CL=F", "NG=F",
        "ES=F", "NQ=F", "YM=F",
    ],
}

# ── Pairs for Statistical Arbitrage ──
# Format: (symbol_a, symbol_b)
PAIRS = [
    ("AAPL", "MSFT"),
    ("GOOG", "META"),
    ("XOM", "CVX"),
    ("GS", "JPM"),
    ("AMD", "NVDA"),
    ("SPY", "QQQ"),
    ("BTC-USD", "ETH-USD"),
    ("EURUSD=X", "GBPUSD=X"),
    ("GC=F", "SI=F"),
    ("CL=F", "NG=F"),
]

# ══════════════════════════════════════════════════════════════
#  DATA SOURCES — Priority order for fetching
# ══════════════════════════════════════════════════════════════

DATA_SOURCE_PRIORITY = ["alpaca", "alphavantage", "yfinance"]

ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
ALPACA_BASE_URL = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
ALPACA_DATA_URL = "https://data.alpaca.markets"

ALPHA_VANTAGE_KEY = os.getenv("ALPHA_VANTAGE_KEY", "")

POLYGON_API_KEY = os.getenv("POLYGON_API_KEY", "")

# ══════════════════════════════════════════════════════════════
#  BACKTESTING
# ══════════════════════════════════════════════════════════════

LOOKBACK_DAYS = 365 * 2
BACKTEST_INITIAL_CAPITAL = 10_000
COMMISSION_PCT = 0.001          # 0.1%
SLIPPAGE_PCT = 0.0005           # 0.05%
RISK_FREE_RATE = 0.05           # For Sharpe calculation

# ══════════════════════════════════════════════════════════════
#  PATTERN VALIDATION
# ══════════════════════════════════════════════════════════════

MIN_WIN_RATE = 0.52
MIN_TRADES = 8
MIN_PROFIT_FACTOR = 1.15
MIN_SHARPE = 0.3
VALIDATION_WINDOW_DAYS = 3

# ══════════════════════════════════════════════════════════════
#  RISK MANAGEMENT
# ══════════════════════════════════════════════════════════════

MAX_POSITION_SIZE_PCT = 0.05    # 5% of portfolio per trade
MAX_PORTFOLIO_RISK_PCT = 0.15   # 15% max total exposure
STOP_LOSS_PCT = 0.02            # 2% stop loss
TAKE_PROFIT_PCT = 0.04          # 4% take profit (2:1 RR)
MAX_CORRELATED_POSITIONS = 3    # Max positions in correlated assets
MAX_DRAWDOWN_HALT_PCT = 0.10    # Halt trading if drawdown exceeds 10%

# ══════════════════════════════════════════════════════════════
#  ML SETTINGS
# ══════════════════════════════════════════════════════════════

ML_TRAIN_TEST_SPLIT = 0.75
ML_N_ESTIMATORS = 200
ML_LOOKBACK_BARS = 60           # Features computed over this window
ML_RETRAIN_DAYS = 30            # Retrain model every N days
ML_MIN_CONFIDENCE = 0.60        # Minimum prediction confidence to act

# ══════════════════════════════════════════════════════════════
#  PAIRS TRADING / STAT ARB
# ══════════════════════════════════════════════════════════════

PAIRS_LOOKBACK = 252            # 1 year for cointegration test
PAIRS_ZSCORE_ENTRY = 2.0        # Enter when z-score exceeds this
PAIRS_ZSCORE_EXIT = 0.5         # Exit when z-score reverts to this
PAIRS_ZSCORE_STOP = 3.5         # Stop loss z-score
PAIRS_MIN_HALF_LIFE = 5         # Min mean-reversion half-life (days)
PAIRS_MAX_HALF_LIFE = 120       # Max mean-reversion half-life (days)
PAIRS_COINT_PVALUE = 0.05       # Cointegration p-value threshold

# ══════════════════════════════════════════════════════════════
#  STRESS TEST
# ══════════════════════════════════════════════════════════════

STRESS_REGIMES = {
    "high_volatility": {"vol_percentile_min": 75, "vol_percentile_max": 100},
    "low_volatility":  {"vol_percentile_min": 0,  "vol_percentile_max": 25},
    "bull_market":     {"return_percentile_min": 75, "return_percentile_max": 100},
    "bear_market":     {"return_percentile_min": 0,  "return_percentile_max": 25},
    "high_volume":     {"vol_type": "volume", "percentile_min": 75, "percentile_max": 100},
    "low_volume":      {"vol_type": "volume", "percentile_min": 0,  "percentile_max": 25},
}

MONTE_CARLO_SIMULATIONS = 1000
MONTE_CARLO_CONFIDENCE = 0.95

# ══════════════════════════════════════════════════════════════
#  ALERTS
# ══════════════════════════════════════════════════════════════

ALERT_METHOD = os.getenv("ALERT_METHOD", "console")  # console, telegram, discord, email

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
ALERT_EMAIL_TO = os.getenv("ALERT_EMAIL_TO", "")

# ══════════════════════════════════════════════════════════════
#  CLAUDE AI (optional summaries)
# ══════════════════════════════════════════════════════════════

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = "claude-sonnet-4-20250514"

# ══════════════════════════════════════════════════════════════
#  PAPER TRADING
# ══════════════════════════════════════════════════════════════

PAPER_TRADE_ENABLED = os.getenv("PAPER_TRADE_ENABLED", "false").lower() == "true"
PAPER_TRADE_MAX_ORDER_VALUE = 1000  # Max $ per paper trade order
# Emergency interlock (WS0.4): orders only when TRADING_MODE=paper AND PAPER_TRADE_ENABLED=true
TRADING_MODE = os.getenv("TRADING_MODE", "off").strip().lower()
if TRADING_MODE not in ("off", "paper"):
    TRADING_MODE = "off"

# ══════════════════════════════════════════════════════════════
#  PHASE 1 — EXECUTION, RISK STATE, SIGNAL SLEEVE (docs/decisions.md D5)
#  These replace the placeholders above for anything that reaches the broker.
#  The core/trend allocation is NOT traded through here (D1: proposals only).
# ══════════════════════════════════════════════════════════════

def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default

STATE_DB_PATH = os.getenv("STATE_DB_PATH", str(Path(__file__).parent / "state" / "trading.db"))
KILL_SWITCH_FILE = os.getenv("KILL_SWITCH_FILE", str(Path(__file__).parent / "state" / "KILL"))

# Signal-sleeve equity is a configured dollar amount, never derived from broker equity.
SIGNAL_SLEEVE_EQUITY = _f("SIGNAL_SLEEVE_EQUITY", 10_000.0)
RISK_PER_TRADE_PCT = 0.005          # 0.5% of sleeve risked to the stop (allowed 0.25-1.0%)
ATR_PERIOD = 20
ATR_STOP_MULT = 2.0                 # initial stop = 2 x ATR(20) from the fill
MAX_SYMBOL_PCT = 0.10               # max notional per symbol, fraction of sleeve
MAX_GROSS_EXPOSURE_PCT = 1.00       # no leverage
MAX_PORTFOLIO_HEAT_PCT = 0.04       # total open risk-to-stop
MAX_OPEN_POSITIONS = 8
MAX_POSITIONS_PER_CLUSTER = 2
MAX_ORDERS_PER_DAY = 5
MAX_ORDER_NOTIONAL = _f("MAX_ORDER_NOTIONAL", 1000.0)   # absolute $ cap; order cap = min(this, MAX_SYMBOL_PCT*sleeve)
DAILY_LOSS_STOP_PCT = 0.015         # no new entries for the day
WEEKLY_LOSS_STOP_PCT = 0.03         # no new entries for the week
DRAWDOWN_LADDER = ((0.05, 0.5), (0.08, 0.25))   # (drawdown from sleeve peak, risk multiplier)
DRAWDOWN_HALT_PCT = 0.10            # persisted hard halt; manual `risk resume --confirm`
CANCEL_ON_HALT = os.getenv("CANCEL_ON_HALT", "true").lower() == "true"

# Correlation clusters for MAX_POSITIONS_PER_CLUSTER (symbols not listed are their own cluster)
CLUSTERS = {
    "us_index": ["SPY", "QQQ", "IWM", "DIA"],
    "mega_tech": ["AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOG", "AMD", "TSLA"],
    "banks": ["JPM", "GS", "BAC"],
    "energy": ["XOM", "CVX"],
}

# Signal eligibility for orders (D6). Phase 1 has no OOS validation yet, so nothing is eligible.
ORDER_ELIGIBLE_STATUSES = ("oos_validated", "deflated_validated")

# Pairs excluded from the default scan (D3)
RESEARCH_ONLY_PAIRS = [("GC=F", "SI=F"), ("CL=F", "NG=F")]
