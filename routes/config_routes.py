"""Configuration endpoints."""

from fastapi import APIRouter
import config

router = APIRouter()


@router.get("/")
def get_config():
    """Get current configuration."""
    return {
        "watchlist": config.WATCHLIST,
        "pairs": [{"a": a, "b": b} for a, b in config.PAIRS],
        "backtest": {
            "lookback_days": config.LOOKBACK_DAYS,
            "initial_capital": config.BACKTEST_INITIAL_CAPITAL,
            "commission_pct": config.COMMISSION_PCT,
            "slippage_pct": config.SLIPPAGE_PCT,
            "stop_loss_pct": config.STOP_LOSS_PCT,
            "take_profit_pct": config.TAKE_PROFIT_PCT,
        },
        "validation": {
            "min_win_rate": config.MIN_WIN_RATE,
            "min_profit_factor": config.MIN_PROFIT_FACTOR,
            "min_sharpe": config.MIN_SHARPE,
            "min_trades": config.MIN_TRADES,
            "signal_recency_days": config.VALIDATION_WINDOW_DAYS,
        },
        "risk": {
            "max_position_pct": config.MAX_POSITION_SIZE_PCT,
            "max_portfolio_risk_pct": config.MAX_PORTFOLIO_RISK_PCT,
            "max_correlated_positions": config.MAX_CORRELATED_POSITIONS,
            "max_drawdown_halt_pct": config.MAX_DRAWDOWN_HALT_PCT,
        },
        "ml": {
            "train_test_split": config.ML_TRAIN_TEST_SPLIT,
            "n_estimators": config.ML_N_ESTIMATORS,
            "lookback_bars": config.ML_LOOKBACK_BARS,
            "min_confidence": config.ML_MIN_CONFIDENCE,
        },
    }
