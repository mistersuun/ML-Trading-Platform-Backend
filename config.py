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
if os.getenv("DATA_SOURCE_PRIORITY", "").strip():       # e.g. DATA_SOURCE_PRIORITY=external
    DATA_SOURCE_PRIORITY = [s.strip() for s in os.getenv("DATA_SOURCE_PRIORITY", "").split(",") if s.strip()]

# Forward paper test (docs/forward-test/RUNBOOK.md): bars come from files the orchestrator writes (raw IBKR
# get_price_history JSON, one file per symbol) and orders go to a simulated paper broker. No network is needed.
FORWARD_TEST = os.getenv("FORWARD_TEST", "").strip().lower() in ("1", "true", "yes", "on")

ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", "")
ALPACA_BASE_URL = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
ALPACA_DATA_URL = "https://data.alpaca.markets"
ALPACA_FEED = os.getenv("ALPACA_FEED", "iex")  # stock data feed: iex (free) | sip | delayed_sip

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
# CLAUDE_MODEL wins; ANTHROPIC_MODEL is accepted as an alias.
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL") or os.getenv("ANTHROPIC_MODEL") or "claude-opus-5-5"
LLM_EFFORT = os.getenv("LLM_EFFORT", "medium")
LLM_TIMEOUT_S = 120
LLM_MAX_TOKENS = 16000
LLM_REFUSAL_FALLBACK = os.getenv("LLM_REFUSAL_FALLBACK", "default").strip().lower()  # default | off
# Nightly briefing (D17): advisory only, capped spend. A call is skipped when today's / this month's spend plus a
# worst-case estimate (LLM_BRIEFING_MAX_TOKENS of output) would exceed a budget.
LLM_BRIEFING_ENABLED = os.getenv("LLM_BRIEFING_ENABLED", "true").strip().lower() not in ("0", "false", "no", "off")
LLM_BRIEFING_MAX_TOKENS = 6000
LLM_DAILY_BUDGET_USD = float(os.getenv("LLM_DAILY_BUDGET_USD", "0.50"))
LLM_MONTHLY_BUDGET_USD = float(os.getenv("LLM_MONTHLY_BUDGET_USD", "5.00"))

# ══════════════════════════════════════════════════════════════
#  PAPER TRADING
# ══════════════════════════════════════════════════════════════

PAPER_TRADE_ENABLED = os.getenv("PAPER_TRADE_ENABLED", "false").lower() == "true"
PAPER_TRADE_MAX_ORDER_VALUE = 1000  # Max $ per paper trade order
# Emergency interlock (WS0.4): orders only when TRADING_MODE=paper AND PAPER_TRADE_ENABLED=true
TRADING_MODE = os.getenv("TRADING_MODE", "off").strip().lower()
if TRADING_MODE not in ("off", "paper"):
    TRADING_MODE = "off"
# Which paper broker execution talks to: "alpaca" (the Alpaca PAPER account, default) or "sim" (brokers/sim.py,
# a persisted offline simulation). There is no live broker.
PAPER_BROKER = os.getenv("PAPER_BROKER", "alpaca").strip().lower()
if PAPER_BROKER not in ("alpaca", "sim"):
    PAPER_BROKER = "alpaca"
SIM_SLIPPAGE_BPS = float(os.getenv("SIM_SLIPPAGE_BPS", "5") or 5)
SIM_START_CASH = float(os.getenv("SIM_START_CASH", "100000") or 100000)   # fictional dollars

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

def _repo_path(env_key: str, default: str) -> str:
    """Relative overrides resolve against the repo, not the cwd: cron/scheduler may run elsewhere,
    and a kill-switch file the process never looks at fails open."""
    p = Path(os.getenv(env_key, "") or default)
    return str(p if p.is_absolute() or str(p) == ":memory:" else Path(__file__).parent / p)


# The forward test keeps its own state DB so it can never touch the real one (unless STATE_DB_PATH is set).
STATE_DB_PATH = _repo_path("STATE_DB_PATH", "state/forward/trading.db" if FORWARD_TEST else "state/trading.db")
EXTERNAL_BARS_DIR = _repo_path("EXTERNAL_BARS_DIR", "state/external_bars")
SIM_BROKER_PATH = _repo_path("SIM_BROKER_PATH", "state/sim_broker.json")
FORWARD_JOURNAL_DIR = _repo_path("FORWARD_JOURNAL_DIR", "docs/forward-test/2026-10")
KILL_SWITCH_FILE = _repo_path("KILL_SWITCH_FILE", "state/KILL")

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
EQUITY_JUMP_HALT_PCT = _f("EQUITY_JUMP_HALT_PCT", 0.10)   # sleeve equity move between readings, no orders in between -> halt (D10)
EXIT_CANCEL_WAIT_SECONDS = _f("EXIT_CANCEL_WAIT_SECONDS", 10.0)  # max wait for cancelled bracket legs to go terminal
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

# Signal eligibility for orders (D6, WS4.2). oos_validated alone is NOT order-eligible: the candidate must also
# clear the Deflated Sharpe gate (N = the run's registry trial count).
ORDER_ELIGIBLE_STATUSES = ("deflated_validated",)
DSR_P_MAX = 0.05                    # deflated_validated needs DSR one-sided p = 1 - DSR < this
PBO_S = 16                          # CSCV blocks for the ADVISORY probability of backtest overfitting (not gated)

# Pairs excluded from the default scan (D3)
RESEARCH_ONLY_PAIRS = [("GC=F", "SI=F"), ("CL=F", "NG=F")]

# ══════════════════════════════════════════════════════════════
#  PHASE 2 — CORRECT RESULTS (roadmap WS2.1-WS2.7 + reviewer amendments)
# ══════════════════════════════════════════════════════════════

# Engine convention (D11): signal at bar t executes at the open of t+1; Sharpe uses rf=0 on daily
# mark-to-market returns of the actual position size. "close" execution is an explicit opt-in only.
EXECUTION_MODE = "next_open"
MIN_TRADES_OOS = 30

# Data
RESEARCH_LOOKBACK_DAYS = 365 * 8      # validation needs long history; display endpoints may use less
DATA_COVERAGE_MIN = 0.95              # share of expected sessions that must be present
DATA_MAX_ABS_DAILY_RETURN = 0.5       # flag (not drop) bars beyond this 1-day move

# Validation (D6). The hold-out starts at a FIXED calendar date and is never used for selection;
# results on it are read once per strategy version. Revisit (move forward) only with a new
# strategy version, and record the change in docs/decisions.md.
HOLDOUT_START = "2025-07-01"
WF_TRAIN_BARS = 504                   # ~2y
WF_TEST_BARS = 126                    # ~6m
WF_STEP_BARS = 126
OOS_PSR_MIN = 0.95
NULL_DRAWS = 1000
NULL_P_MAX = 0.05
COST_STRESS_MULT = 2.0
FDR_ALPHA = 0.05                      # Benjamini-Hochberg across ALL candidates tested in a run
SEED = 20261005

# ML
ML_HORIZON_BARS = 1
ML_EMBARGO_BARS = 5
ML_CV_TEST_BARS = 126

# Pairs
PAIRS_MIN_ALIGNED_BARS = 252
PAIRS_ROLLING_WINDOW = 60
PAIRS_TIME_STOP_HALF_LIVES = 3

# ══════════════════════════════════════════════════════════════
#  OWNER PORTFOLIO VIEW (decisions D1/D4: read-only, proposals only)
# ══════════════════════════════════════════════════════════════

# The owner's real holdings (`symbol,quantity` rows, a CASH row holds dollars). Never written by the platform.
HOLDINGS_CSV = _repo_path("HOLDINGS_CSV", "state/holdings.csv")
# Benchmark for the overview: monthly-rebalanced total-return mix.
BENCHMARK_WEIGHTS = {"SPY": 0.6, "IEF": 0.4}

# ══════════════════════════════════════════════════════════════
#  IBKR READ-ONLY ACCOUNT SYNC (docs/decisions.md D14)
#  Talks to a locally running IB Gateway with the API set to READ-ONLY (that Gateway setting is what actually blocks
#  orders). In the code, brokers/ibkr_readonly.py is guarded by AST tests that ban order APIs and by an allow-list
#  wrapper (best effort); connect(readonly=True) only skips order sync. The sync only reads the account into state
#  DB snapshots.
# ══════════════════════════════════════════════════════════════

IBKR_HOST = os.getenv("IBKR_HOST", "127.0.0.1")
IBKR_PORT = int(_f("IBKR_PORT", 4001))          # 4001 IB Gateway live, 4002 IB Gateway paper (7496/7497 for TWS)
IBKR_CLIENT_ID = int(_f("IBKR_CLIENT_ID", 17))
IBKR_ACCOUNT = os.getenv("IBKR_ACCOUNT", "").strip() or None   # optional: default is the first managed account
IBKR_TIMEOUT_S = _f("IBKR_TIMEOUT_S", 10.0)
# The nightly run only calls the sync when this is true (a machine without IB Gateway would alert every night).
IBKR_SYNC_ENABLED = os.getenv("IBKR_SYNC_ENABLED", "false").strip().lower() == "true"
BASE_CURRENCY = (os.getenv("BASE_CURRENCY", "CAD").strip().upper() or "CAD")
# 'cad' = the CAD-listed mapping (default; approved by the owner 2026-10-05, D14); 'us' = the D4 core in US tickers.
ALLOCATION_PROFILE = os.getenv("ALLOCATION_PROFILE", "cad").strip().lower()
if ALLOCATION_PROFILE not in ("us", "cad"):
    ALLOCATION_PROFILE = "cad"
MAX_LEVERAGE_WARN = _f("MAX_LEVERAGE_WARN", 1.0)   # gross positions / net liquidation above this -> warning


def enable_forward_test() -> None:
    """Switch this process to the forward-test setup (idempotent): external bars only, simulated paper broker and a
    separate state DB. Paper trading stays OFF here on purpose: only `services.forward.step` turns it on, locally and
    for the duration of the step, so no other command (run-nightly, scan --paper) can place sim orders on a stale
    sim clock. Used by `main.py forward ...` and FORWARD_TEST=1."""
    global FORWARD_TEST, PAPER_BROKER, TRADING_MODE, PAPER_TRADE_ENABLED, STATE_DB_PATH, IBKR_SYNC_ENABLED, ALERT_METHOD
    FORWARD_TEST = True
    PAPER_BROKER = "sim"
    TRADING_MODE = "off"
    PAPER_TRADE_ENABLED = False
    DATA_SOURCE_PRIORITY[:] = ["external"]
    IBKR_SYNC_ENABLED = False        # no account sync, no network
    ALERT_METHOD = "console"
    if not os.getenv("STATE_DB_PATH"):
        STATE_DB_PATH = _repo_path("STATE_DB_PATH", "state/forward/trading.db")


if FORWARD_TEST:
    enable_forward_test()
