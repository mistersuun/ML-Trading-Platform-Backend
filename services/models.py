"""Pydantic response / domain models returned by the services (fractions; Optional[float] for non-finite).

Build them from ``common.clean(...)``-ed data: NaN / inf are mapped to None BEFORE validation. There is
deliberately no FiniteFloat on outputs (a bad number becomes null, it never fails the request).
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, RootModel

OptF = Optional[float]


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")

    def dump(self) -> dict:
        return self.model_dump(mode="json")


# ---------------------------------------------------------------- market data
class OhlcvBar(_Base):
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int


class OhlcvResponse(_Base):
    symbol: str
    count: int
    data: list[OhlcvBar]


class WatchlistFetchResponse(_Base):
    count: int
    symbols: list[str]


class SymbolStatus(_Base):
    """What the data layer can report about one symbol today (the bar-store worker fills in the rest)."""
    symbol: str
    source: Optional[str] = None
    adjusted: Optional[bool] = None
    calendar: Optional[str] = None
    last_session: Optional[str] = None
    n_bars: Optional[int] = None
    coverage: OptF = None
    stale_sessions: Optional[int] = None
    fetched_at: Optional[str] = None
    quality_flags: list[str] = Field(default_factory=list)
    status: str = "ok"                       # ok | no_data | quality_failed | error
    error: Optional[str] = None
    cache_age_seconds: OptF = None           # None until the persistent bar store exists


# ---------------------------------------------------------------- patterns / scan
class PricePoint(_Base):
    date: str
    price: float


class LatestSignal(_Base):
    date: str
    signal: str
    price: float


class PatternDetectResponse(_Base):
    symbol: str
    pattern: str
    total_signals: int
    buys: list[PricePoint]
    sells: list[PricePoint]
    latest_signal: Optional[LatestSignal] = None


class ScanSignal(_Base):
    """One (symbol, pattern) signal with its IN-SAMPLE display metrics (same numbers for CLI and API)."""
    symbol: str
    pattern: str
    signal: str                              # BUY | SELL
    signal_date: str
    days_ago: int
    price: float
    win_rate: OptF = None
    profit_factor: OptF = None
    sharpe: OptF = None
    total_return: OptF = None
    total_return_pct: OptF = None            # legacy name, same fraction; kept for one release
    max_drawdown: OptF = None
    total_trades: Optional[int] = None
    is_valid: Optional[bool] = None          # in-sample display heuristic, NOT an eligibility gate
    validation_status: str = "unvalidated"


class TechnicalCandidate(ScanSignal):
    """A CLI scan candidate: the ScanSignal plus its out-of-sample evidence and validation status."""
    signal_value: int = 0
    signal_bar_date: Optional[str] = None
    orderable_direction: int = 0             # 0 unless the signal is on the LAST bar (D12)
    oos: Optional[dict[str, Any]] = None
    holdout: Optional[dict[str, Any]] = None
    n_oos_trades: int = 0
    oos_psr: OptF = None
    bh_adjusted_p: OptF = None
    n_trials: int = 0
    null_p: OptF = None
    rejected_reasons: list[str] = Field(default_factory=list)


class ScanFailure(_Base):
    symbol: str
    pattern: Optional[str] = None
    error: str
    message: Optional[str] = None


class PatternScanResponse(_Base):
    count: int
    signals: list[ScanSignal]
    failed: list[ScanFailure]


# ---------------------------------------------------------------- backtest
class HoldoutInfo(_Base):
    start: str
    bars: int
    share: float
    note: str


class EquityPoint(_Base):
    date: str
    value: float


class TradeOut(_Base):
    entry_date: str
    exit_date: Optional[str] = None
    direction: str
    entry_price: float
    exit_price: float
    pnl_pct: OptF = None
    exit_reason: Optional[str] = None
    bars_held: Optional[int] = None
    in_holdout: bool = False


class BacktestResponse(_Base):
    symbol: str
    pattern: str
    metrics: dict[str, Any]
    metrics_display: dict[str, Any]
    equity_curve: list[EquityPoint]
    trades: list[TradeOut]
    is_valid: bool
    validation_status: str = "unvalidated"
    holdout: HoldoutInfo


class WalkForwardFold(_Base):
    fold: int
    metrics: dict[str, Any]
    metrics_display: dict[str, Any]


class WalkForwardResponse(_Base):
    symbol: str
    pattern: str
    n_folds: int
    folds: list[WalkForwardFold]
    fold_scheme: str


# ---------------------------------------------------------------- pairs
class PairsConfiguredResponse(_Base):
    pairs: list[dict[str, str]]


class PairAnalysisResponse(_Base):
    symbol_a: str
    symbol_b: str
    is_cointegrated: Optional[bool] = None
    is_valid: Optional[bool] = None
    coint_pvalue: OptF = None
    hedge_ratio: OptF = None
    half_life: OptF = None
    correlation: OptF = None
    current_zscore: OptF = None
    n_obs: Optional[int] = None
    bars_per_year: OptF = None
    non_executable: Optional[bool] = None
    non_executable_reason: Optional[str] = None
    spread_data: list[dict[str, Any]]
    prices_a: list[PricePoint]
    prices_b: list[PricePoint]
    backtest: dict[str, Any]


class PairsScanResponse(_Base):
    count: int
    pairs: list[dict[str, Any]]
    n_pairs_configured: int
    failed: list[dict[str, Any]]


# ---------------------------------------------------------------- ML
class FeatureImportance(_Base):
    feature: str
    importance: OptF = None


class MLSignal(_Base):
    date: str
    signal: str
    price: float
    confidence: OptF = None
    p_up: OptF = None


class MLPredictResponse(_Base):
    symbol: str
    model_type: Optional[str] = None
    feature_importance: list[FeatureImportance]
    signals: list[MLSignal]
    total_signals: int
    metrics: dict[str, Any]
    metrics_display: dict[str, Any]
    equity_curve: list[EquityPoint]
    is_valid: bool
    oos_start: Optional[str] = None
    n_oos_bars: int
    oos_metrics: Optional[dict[str, Any]] = None
    importance_basis: str
    holdout: HoldoutInfo


# ---------------------------------------------------------------- stress
class StressReport(RootModel[dict[str, Any]]):
    """full_stress_test's report, passed through as one dict (its existing keys are the contract)."""

    def dump(self) -> dict:
        return self.model_dump(mode="json")


class RegimesResponse(_Base):
    symbol: str
    regimes: dict[str, dict[str, int]]


class SensitivityResponse(_Base):
    data: list[dict[str, Any]]
    meta: dict[str, Any]


# ---------------------------------------------------------------- misc responses the frontend calls
class PatternListResponse(_Base):
    patterns: list[str]
    count: int


class PairRef(_Base):
    a: str
    b: str


class ConfigBacktest(_Base):
    lookback_days: int
    initial_capital: float
    commission_pct: float
    slippage_pct: float
    stop_loss_pct: float
    take_profit_pct: float


class ConfigValidation(_Base):
    min_win_rate: float
    min_profit_factor: float
    min_sharpe: float
    min_trades: int
    signal_recency_days: int


class ConfigRisk(_Base):
    max_position_pct: float
    max_portfolio_risk_pct: float
    max_correlated_positions: int
    max_drawdown_halt_pct: float


class ConfigML(_Base):
    train_test_split: float
    n_estimators: int
    lookback_bars: int
    min_confidence: float


class ConfigResponse(_Base):
    watchlist: dict[str, Any]
    pairs: list[PairRef]
    backtest: ConfigBacktest
    validation: ConfigValidation
    risk: ConfigRisk
    ml: ConfigML


class WatchlistSymbolsResponse(RootModel[dict[str, list[str]]]):
    """Configured symbols grouped by market."""

    def dump(self) -> dict:
        return self.model_dump(mode="json")


# ---------------------------------------------------------------- nightly results (read-only files)
class _LatestResult(_Base):
    kind: str
    generated_at: str
    age_hours: float
    stale: bool


class LatestTechnicalResult(_LatestResult):
    """Latest nightly technical scan: the validated / OOS-positive candidates (a superset of ScanSignal)."""
    payload: list[TechnicalCandidate]


class LatestPairsResult(_LatestResult):
    payload: list[dict[str, Any]]


class LatestMLResult(_LatestResult):
    payload: list[dict[str, Any]]
