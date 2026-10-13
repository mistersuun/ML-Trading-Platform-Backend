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
    dsr: OptF = None                         # Deflated Sharpe (probability) with N = every trial of the run
    dsr_p: OptF = None                       # 1 - dsr; the order gate is dsr_p < DSR_P_MAX
    pbo: OptF = None                         # CSCV probability of backtest overfitting: ADVISORY, never gated
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
    equity_curve: list[EquityPoint]
    trades: list[TradeOut]
    is_valid: bool
    validation_status: str = "unvalidated"
    holdout: HoldoutInfo


class WalkForwardFold(_Base):
    fold: int
    metrics: dict[str, Any]


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
    calibrated: Optional[bool] = None        # p_up is sigmoid-calibrated on a time-ordered validation block
    model_selected: Optional[str] = None
    abstain_reasons: dict[str, int] = Field(default_factory=dict)   # OOS rows with no signal, by reason
    last_abstain_reason: Optional[str] = None                      # reason on the latest bar ('' / None = none)
    metrics: dict[str, Any]
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


class Funnel(_Base):
    """How many candidates of the nightly run survive each gate in turn (every stage is a subset of the last)."""
    tested: int
    min_trades: int
    oos_positive: int
    psr: int
    bh: int
    dsr: Optional[int] = None                # survives Deflated Sharpe (None for results stored before Phase 4)
    orders: int
    n_trials: Optional[int] = None           # N: every evaluation of the run, valid or not
    pbo: OptF = None                         # advisory CSCV PBO of the run
    sharpe_var: OptF = None                  # cross-trial variance of per-period Sharpe used by the DSR
    run_id: Optional[str] = None


class LatestTechnicalResult(_LatestResult):
    """Latest nightly technical scan: the validated / OOS-positive candidates (a superset of ScanSignal)."""
    payload: list[TechnicalCandidate]
    funnel: Optional[Funnel] = None          # None for results stored before the funnel existed


# ---------------------------------------------------------------- pooled validation, shadow only (D18)
class _PooledBase(BaseModel):
    """Pooled blocks tolerate extra keys (newer stored payloads) and are all-optional where a run can omit them."""
    model_config = ConfigDict(extra="ignore")


class PooledUniverse(_PooledBase):
    hash: str
    symbols: list[str]
    k: int
    missing: list[str] = Field(default_factory=list)
    missing_reasons: dict[str, str] = Field(default_factory=dict)
    complete: bool
    partial: bool
    flags: list[str] = Field(default_factory=list)


class PooledOosWindow(_PooledBase):
    days: int
    start: str
    end: str
    bars_min: int
    bars_max: int


class PooledInterval(_PooledBase):
    lo: float
    hi: float
    mean: float
    draws: int


class PooledConcentration(_PooledBase):
    max_symbol_share: OptF = None
    top_symbol: Optional[str] = None
    loso_min_mean_r: OptF = None
    loso_worst_symbol: Optional[str] = None
    loco_min_mean_r: OptF = None
    loco_worst_cluster: Optional[str] = None
    lofo_min_mean_r: OptF = None
    lofo_worst_fold: Optional[int] = None
    share_ok: bool = False
    loso_ok: bool = False
    loco_ok: bool = False
    lofo_ok: bool = False
    ok: bool = False


class PooledCapped(_PooledBase):
    admitted: int = 0
    skipped_by_caps: int = 0
    excluded_trades: int = 0
    pooled_return: OptF = None                # fraction of the sleeve (sum of the daily capped contributions)
    sharpe: OptF = None
    sharpe_ratio_to_uncapped: OptF = None
    max_concurrent_uncapped: int = 0
    ok: bool = False


class PooledSymbolRow(_PooledBase):
    symbol: str
    n_trades: int
    mean_r: OptF = None
    pnl_r: float = 0.0
    share: float = 0.0
    t_stat: OptF = None
    excluded: bool = False


class PooledHoldout(_PooledBase):
    start: str
    n_bars: int
    n_trades: int
    n_symbols: Optional[int] = None
    total_return: OptF = None
    sharpe: OptF = None
    reread_p: OptF = None
    bonferroni_k: Optional[int] = None


class PooledPattern(_PooledBase):
    """One pattern's pooled verdict: status, every gate, the symbol contribution table and the version hashes."""
    pattern: str
    status: str                               # unvalidated | pooled_oos_validated | pooled_deflated_validated
    order_eligible: bool = False              # always False: pooled is shadow only
    reasons: list[str] = Field(default_factory=list)
    gates: dict[str, bool] = Field(default_factory=dict)
    n_trades: int = 0
    n_clusters: Optional[int] = None
    breadth: Optional[int] = None
    breadth_needed: Optional[int] = None
    mean_r: OptF = None
    pooled_return: OptF = None
    sharpe: OptF = None
    psr: OptF = None
    t_eff: OptF = None
    q: Optional[int] = None
    null_p: OptF = None
    null_draws: Optional[int] = None
    null_observed: OptF = None
    null_clusters: Optional[int] = None
    null_unfit: Optional[int] = None
    null_failed: Optional[bool] = None
    bh_adjusted_p: OptF = None
    dsr: OptF = None
    dsr_p: OptF = None
    n_trials: Optional[int] = None
    sharpe_var_used: OptF = None
    deflation_reason: Optional[str] = None
    cost_return: OptF = None
    delay_return: OptF = None
    concentration: Optional[PooledConcentration] = None
    capped: Optional[PooledCapped] = None
    excluded_symbols: list[str] = Field(default_factory=list)
    symbols: list[PooledSymbolRow] = Field(default_factory=list)
    bootstrap_sharpe: Optional[PooledInterval] = None
    bootstrap_mean_r: Optional[PooledInterval] = None
    max_hold_bars: Optional[int] = None
    holdout: Optional[PooledHoldout] = None
    holdout_state: Optional[str] = None
    holdout_frozen: Optional[bool] = None
    holdout_asof: Optional[str] = None
    holdout_reads_of_pattern: Optional[int] = None
    trial_version: Optional[str] = None
    pooled_version: Optional[str] = None
    duplicate_of: Optional[str] = None
    failed_symbols: list[str] = Field(default_factory=list)


class PooledFunnel(_PooledBase):
    """patterns_tested -> pooled_min_trades -> ... -> holdout; every stage is a subset of the last."""
    unit: str = "pooled"
    patterns_tested: int
    pooled_min_trades: int
    breadth: int
    oos_positive: int
    psr: int
    null_bh: int
    dsr: int
    concentration: int
    cost_delay: int
    capped_replay: int
    holdout: int
    pooled_oos_validated: int
    pooled_validated: int
    n_trials: int
    sharpe_var: OptF = None
    pbo: OptF = None                          # advisory time CSCV
    pbo_xs: OptF = None                       # advisory cross-sectional CSCV
    run_id: Optional[str] = None
    universe_hash: Optional[str] = None
    orders: int = 0                           # always 0: shadow only
    firing_tonight: int = 0


class PooledRetired(_PooledBase):
    pattern: str
    duplicate_of: Optional[str] = None
    reason: str


class PooledShadowSignal(_PooledBase):
    kind: str = "pooled_pattern"
    symbol: str
    pattern: str
    signal_bar_date: str
    pooled_status: str
    stale: bool = False
    excluded: bool = False
    order_eligible: bool = False


class PooledLastVerdictPattern(_PooledBase):
    pattern: Optional[str] = None
    status: Optional[str] = None


class PooledLastVerdict(_PooledBase):
    """Display-only fallback shown when the universe is incomplete: never an eligibility input."""
    generated_at: str
    age_hours: float
    display_only: bool = True
    order_eligible: bool = False
    universe_hash: Optional[str] = None
    patterns: list[PooledLastVerdictPattern] = Field(default_factory=list)


class PooledShadowHealth(_PooledBase):
    """D18(a) shadow-period counters: complete = >= 20 distinct bar-date sessions AND >= 28 calendar days."""
    ok_nights: int = 0
    consecutive_failed_nights: int = 0
    last_counted_date: Optional[str] = None
    ok_dates: list[str] = Field(default_factory=list)
    ok_sessions: int = 0
    ok_session_dates: list[str] = Field(default_factory=list)
    first_ok_date: Optional[str] = None
    calendar_days: Optional[int] = None
    required_ok_sessions: int = 20
    required_calendar_days: int = 28
    complete: bool = False


class PooledPayload(_PooledBase):
    unit: str = "pooled"
    mode: str
    shadow_only: bool = True
    order_eligible: bool = False
    status: str                               # ok | pooled_universe_incomplete | pooled_trials_unavailable | ...
    message: Optional[str] = None
    run_id: Optional[str] = None
    generated_at: Optional[str] = None
    method_version: Optional[str] = None
    holdout_start: Optional[str] = None
    warmup_bars: Optional[int] = None
    asof: Optional[str] = None
    disclosure: Optional[str] = None
    universe: PooledUniverse
    oos: Optional[PooledOosWindow] = None
    narrowed: bool = False
    n_trials: Optional[int] = None
    n_trials_floor: Optional[int] = None
    n_trials_ledger: Optional[int] = None
    sharpe_var: OptF = None
    sharpe_var_empirical: OptF = None
    no_nightly_variance: bool = False
    pbo: OptF = None
    pbo_xs: OptF = None
    funnel: Optional[PooledFunnel] = None
    patterns: list[PooledPattern] = Field(default_factory=list)
    retired: list[PooledRetired] = Field(default_factory=list)
    shadow_signals: list[PooledShadowSignal] = Field(default_factory=list)
    last_verdict: Optional[PooledLastVerdict] = None
    shadow_health: Optional[PooledShadowHealth] = None
    elapsed_s: OptF = None


class LatestPooledResult(_LatestResult):
    """Latest nightly POOLED validation (D18): shadow only, never order-eligible."""
    payload: PooledPayload


class LatestPairsResult(_LatestResult):
    payload: list[dict[str, Any]]


class LatestMLResult(_LatestResult):
    payload: list[dict[str, Any]]


# ---------------------------------------------------------------- owner portfolio overview (GET /api/portfolio/overview)
class AccountValue(_Base):
    key: str                                 # core | trend | cash | other
    label: str
    value: float
    share: OptF = None                       # fraction of total portfolio value


class PaperSleeve(_Base):
    """The platform's own paper signal sleeve: NOT real money and not part of the portfolio total."""
    label: str = "paper"
    value: OptF = None
    peak: OptF = None
    drawdown: OptF = None                    # negative fraction below peak
    as_of: Optional[str] = None
    note: str = ""


class SeriesPoint(_Base):
    date: str
    portfolio: float                         # growth of 100
    benchmark: OptF = None                   # growth of 100
    drawdown: OptF = None                    # portfolio drawdown from its running peak, <= 0


class GroupWeight(_Base):
    key: str
    label: str
    target: float                            # fraction
    now: float                               # fraction
    drift: float                             # now - target
    after: OptF = None                       # allocation proposal only
    value: OptF = None                       # overview only: this group's value in base currency


class AccountBlock(_Base):
    """The owner's broker account (read-only IBKR snapshot) or the holdings.csv fallback, in base currency.

    margin_loan is the absolute value of negative cash; leverage is gross positions / net liquidation;
    margin_headroom is the broker's excess liquidity (None without a broker snapshot)."""
    available: bool = True
    source: Optional[str] = None             # ibkr | holdings_csv
    as_of: Optional[str] = None
    age_hours: OptF = None
    base_currency: Optional[str] = None
    net_worth: OptF = None
    positions_value: OptF = None
    cash: OptF = None                        # negative = margin loan
    margin_loan: OptF = None
    leverage: OptF = None
    max_leverage_warn: OptF = None
    margin_headroom: OptF = None             # excess liquidity
    maint_margin: OptF = None
    buying_power: OptF = None
    positions: Optional[int] = None
    warnings: list[str] = Field(default_factory=list)
    note: Optional[str] = None


class OverviewResponse(_Base):
    as_of: str
    range: str
    total_value: float
    day_change: OptF = None
    day_change_pct: OptF = None
    cash: float
    accounts: list[AccountValue]
    paper_sleeve: PaperSleeve
    period_return: OptF = None
    benchmark_return: OptF = None
    max_drawdown: OptF = None
    series: list[SeriesPoint]
    method: str
    benchmark_label: str
    allocation: list[GroupWeight]
    allocation_basis: str
    unpriced: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    base_currency: str = "CAD"
    source: str = "holdings_csv"             # ibkr | holdings_csv
    net_worth: OptF = None                   # net liquidation (broker) or positions + cash (negative cash included)
    positions_value: OptF = None             # holdings priced from the bar store, base currency
    margin_loan: OptF = None                 # abs(negative cash)
    leverage: OptF = None                    # gross positions / net worth
    margin_headroom: OptF = None             # IBKR excess liquidity
    account: Optional[AccountBlock] = None
    other_value: OptF = None                 # snapshot only: net worth - (positions + cash): stale closes, accruals, unvalued items
    unclassified_value: OptF = None          # value of priced holdings that are in no asset group, base currency


# ---------------------------------------------------------------- allocation proposal (GET /api/allocation/proposal)
class ProposalRow(_Base):
    symbol: str
    name: str
    group: str
    price: float
    shares: float
    value: float
    target: float
    current: float
    drift: float                             # current - target (fraction)
    band: float                              # allowed |drift| (fraction): max(5 pts, 25% of target)
    outside: bool
    action: str                              # buy | sell | hold
    trade_shares: int                        # + buy / - sell
    trade_value: float


class ProposalTrade(_Base):
    symbol: str
    action: str                              # buy | sell
    shares: int
    amount: float


class TrendVote(_Base):
    lookback: int
    above: bool
    average: OptF = None


class TrendMonth(_Base):
    month: str                               # YYYY-MM
    close: float
    average: OptF = None                     # 10-month average at that month-end


class TrendAsset(_Base):
    symbol: str
    name: str
    months: list[TrendMonth]
    votes: list[TrendVote]
    score: float                             # share of 8/10/12 votes above their average
    state: str                               # held | partial | tbills | unknown
    weight: float                            # share of the whole portfolio held in the asset
    data_available: bool = True


class ProposalResponse(_Base):
    as_of: str
    signal_month: Optional[str] = None
    total_value: float
    cash: float
    contribution: float
    cash_after: float
    rows: list[ProposalRow]
    trades: list[ProposalTrade]
    groups: list[GroupWeight]
    groups_basis: str
    trend: list[TrendAsset]
    unmanaged: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    advisory: str
    profile: str = "us"
    margin_loan: OptF = None
    leverage: OptF = None
    base_currency: str = "CAD"               # every amount in this response (prices, values, trades) is in it
    managed_value: OptF = None               # managed positions + max(cash, 0): what the targets apply to
    contribution_to_loan: OptF = None        # part of the contribution that repays the margin loan first
    margin_loan_after: OptF = None           # the loan after that repayment
    unmanaged_value: OptF = None             # holdings outside the target list, held fixed


# ---------------------------------------------------------------- risk status (GET /api/risk/status)
class LadderLevel(_Base):
    kind: str                                # cut | halt
    drawdown: float                          # fraction below peak that triggers it
    risk_multiplier: float                   # 0 for the halt
    label: str
    threshold_equity: OptF = None
    room: OptF = None                        # dollars of sleeve value left before the level (<= 0: reached)


class LimitUse(_Base):
    key: str
    label: str
    used: OptF = None                        # None: not measurable from the records
    maximum: float
    unit: str                                # fraction | count
    note: Optional[str] = None


class EquityHistoryPoint(_Base):
    ts: str
    sleeve_equity: float
    peak: OptF = None


class ReasonCount(_Base):
    reason: str
    count: int


class DecisionRow(_Base):
    ts: str
    symbol: Optional[str] = None
    side: Optional[str] = None
    status: str
    reasons: list[str]
    source: str                              # ledger | scan_run


class ReconcileStatus(_Base):
    broker_checked: bool
    status: str                              # clean | mismatch
    mismatches: list[str]
    note: str


class RiskStatusResponse(_Base):
    mode: str
    paper_trade_enabled: bool
    halted: bool
    halt_reason: Optional[str] = None
    halted_at: Optional[str] = None
    kill_switch: bool
    reconcile: ReconcileStatus
    sleeve_value: OptF = None
    sleeve_as_of: Optional[str] = None
    configured_equity: float
    peak: OptF = None
    drawdown: OptF = None                    # negative fraction below peak
    ladder: list[LadderLevel]
    open_positions: Optional[int] = None
    max_positions: int
    limits: list[LimitUse]
    equity_history: list[EquityHistoryPoint]
    decision_window_days: int
    decision_total: int
    decision_reasons: list[ReasonCount]
    decisions: list[DecisionRow]
    account: Optional[AccountBlock] = None


# ---------------------------------------------------------------- scanner candidate (GET /api/scanner/candidate)
class CurvePoint(_Base):
    date: str
    value: float                             # cumulative return, fraction


class Gate(_Base):
    key: str
    name: str
    value: OptF = None
    threshold: OptF = None
    comparator: str                          # ">=" | ">" | "<="
    unit: str                                # count | fraction | probability
    status: str                              # pass | fail | recorded | unavailable
    gating: bool
    note: Optional[str] = None


class BarOut(_Base):
    date: str
    open: float
    high: float
    low: float
    close: float


class TradeMarker(_Base):
    entry_date: str
    entry_price: float
    exit_date: Optional[str] = None
    exit_price: OptF = None
    pnl_pct: OptF = None
    exit_reason: Optional[str] = None
    in_holdout: bool = False


class NightlyRef(_Base):
    in_last_scan: bool
    generated_at: Optional[str] = None
    bh_adjusted_p: OptF = None
    validation_status: Optional[str] = None
    tested: Optional[int] = None
    dsr_p: OptF = None
    n_trials: Optional[int] = None
    pbo: OptF = None
    label: str


class CandidateResponse(_Base):
    symbol: str
    pattern: str
    variant: str
    last_price: OptF = None
    day_change: OptF = None
    day_change_pct: OptF = None
    as_of: Optional[str] = None
    holdout_start: str
    holdout_frozen: bool = False
    oos_curve: list[CurvePoint]
    in_sample_curve: list[CurvePoint]
    in_sample_method: str
    oos_return: OptF = None
    in_sample_return: OptF = None
    holdout_return: OptF = None
    oos_trade_returns: list[float]
    n_oos_trades: int
    win_rate: OptF = None
    win_rate_ci_low: OptF = None
    win_rate_ci_high: OptF = None
    avg_win: OptF = None
    avg_loss: OptF = None
    gates: list[Gate]
    gates_failed: int
    verdict: str                             # validated | alert_only
    nightly: NightlyRef
    bars: list[BarOut]
    trades: list[TradeMarker]
    rejected_reasons: list[str]
    note: str


# ---------------------------------------------------------------- nightly briefing (GET /api/briefing)
class BriefingBody(_Base):
    headline: str
    observations: list[str]
    risks: list[str]
    what_changed: list[str]


class BriefingUsage(_Base):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


class BriefingBudget(_Base):
    spent_today_usd: float
    daily_limit_usd: float
    spent_month_usd: float
    monthly_limit_usd: float


class BriefingAttempt(_Base):
    """A later skipped/failed run that did not replace the displayed briefing."""
    status: str
    reason: Optional[str] = None
    error: Optional[str] = None
    at: Optional[str] = None


class BriefingResponse(_Base):
    """The latest Claude briefing. Advisory only. status: none (never generated) | ok | skipped (reason: budget,
    no_api_key, disabled, ledger) | error (error holds the message).
    last_attempt: a newer skipped/failed run when the briefing shown is an older ok one."""
    status: str
    generated_at: Optional[str] = None
    model: Optional[str] = None
    reason: Optional[str] = None
    error: Optional[str] = None
    briefing: Optional[BriefingBody] = None
    usage: Optional[BriefingUsage] = None
    cost_usd: OptF = None
    cost_estimated: bool = False
    request_id: Optional[str] = None
    last_attempt: Optional[BriefingAttempt] = None
    budget: BriefingBudget
    advisory: str
