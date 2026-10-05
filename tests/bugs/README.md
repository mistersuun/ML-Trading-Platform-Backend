# Bug index

Every known bug has at least one test in this directory. The table below is the index; `tests/test_bug_index.py` keeps it
honest (every bug test must be listed, every row must exist, and `xfail` status must match the presence of an xfail marker).

- **xfail**: strict `xfail` pinning the bug. The test asserts the CORRECT behaviour and currently fails with `AssertionError`.
- **fixed**: the pinning test now passes (its xfail marker was removed by the workstream that fixed it).

When a workstream fixes a bug: remove the `xfail` marker from the test, then change its status here to `fixed`.
When adding a bug test, name it `test_<PREFIX>_<N>[letter]_<description>` and add a row.
Notes: `SIG-1` (check_recent_signal rewrite, also changes alert direction) was fixed early under WS0.4, ahead of WS1.5; ML order confidence now comes from the row of the latest non-zero signal. `API-4` also covers `SIG-2` (workstream WS1.3 for the signal-logging half). `ORD-2` currently passes only because of
the broker-state dedupe added in WS0.4; the per-bar dedupe arrives with WS1.3.

| Bug ID | Test | Status | Fixing workstream |
|---|---|---|---|
| ORD-1 | `test_orders_bugs.py::test_ORD_1_qty_bounded_when_profit_factor_huge` | fixed | WS1.3 / WS0.4 |
| ORD-2 | `test_orders_bugs.py::test_ORD_2_one_order_per_symbol_bar` | fixed | WS1.3 / WS0.4 |
| ORD-2b | `test_orders_bugs.py::test_ORD_2b_no_rebuy_on_same_bar_after_position_closed` | xfail | WS1.3 |
| ORD-3 | `test_orders_bugs.py::test_ORD_3_risk_manager_blocks_orders_when_halted` | xfail | WS1.3 / WS0.4 |
| ORD-4 | `test_orders_bugs.py::test_ORD_4_no_non_equity_symbol_reaches_broker` | fixed | WS1.3 / WS0.4 |
| ORD-5 | `test_orders_bugs.py::test_ORD_5_sell_without_position_rejected` | fixed | WS1.3 / WS0.4 |
| ORD-6 | `test_orders_bugs.py::test_ORD_6_ml_sell_confidence_at_least_half` | fixed | WS1.3 / WS0.4 |
| ORD-7 | `test_orders_bugs.py::test_ORD_7_trading_client_is_always_paper` | fixed | WS1.3 / WS0.4 |
| SIG-1 | `test_orders_bugs.py::test_SIG_1_latest_nonzero_signal_wins` | fixed | WS1.3 / WS0.4 |
| BT-1 | `test_backtest_bugs.py::test_BT_1_entry_on_next_bar_open` | xfail | WS2.2 / WS2.3 |
| BT-2 | `test_backtest_bugs.py::test_BT_2_stop_gap_through_fills_at_open` | xfail | WS2.2 / WS2.3 |
| BT-3 | `test_backtest_bugs.py::test_BT_3_equity_marked_to_market_every_bar` | xfail | WS2.2 / WS2.3 |
| BT-4 | `test_backtest_bugs.py::test_BT_4_bars_held_is_exit_minus_entry_index` | xfail | WS2.2 / WS2.3 |
| BT-5 | `test_backtest_bugs.py::test_BT_5_random_walk_sharpe_near_zero` | xfail | WS2.2 / WS2.3 |
| BT-1a | `test_backtest_bugs.py::test_BT_1a_canary_peeking_signal_is_profitable_today` | fixed | n/a (characterization of BT-1) |
| BT-1b | `test_backtest_bugs.py::test_BT_1b_peeking_signal_not_profitable_with_next_bar_entry` | xfail | WS2.2 / WS2.3 |
| BT-5b | `test_backtest_bugs.py::test_BT_5b_zero_signal_flat_curve_sharpe_is_zero` | fixed | WS2.2 / WS2.3 |
| BT-6 | `test_backtest_bugs.py::test_BT_6_profit_factor_finite_and_capped_with_no_losses` | xfail | WS2.2 / WS2.3 |
| BT-7 | `test_backtest_bugs.py::test_BT_7_equity_curve_covers_every_bar` | xfail | WS2.2 / WS2.3 |
| WF-1 | `test_backtest_bugs.py::test_WF_1_walk_forward_uses_training_window_for_warmup` | xfail | WS2.2 / WS2.3 |
| ML-1 | `test_ml_bugs.py::test_ML_1_target_up_nan_on_unresolved_rows` | xfail | WS2.5 |
| ML-2 | `test_ml_bugs.py::test_ML_2_scaler_not_fit_on_test_folds` | xfail | WS2.5 |
| ML-3 | `test_ml_bugs.py::test_ML_3_ml_route_backtests_only_out_of_sample` | xfail | WS2.5 |
| ML-3 | `test_ml_bugs.py::test_ML_3_scan_ml_backtests_only_out_of_sample` | xfail | WS2.5 |
| PR-1a | `test_pairs_stress_bugs.py::test_PR_1a_scaling_both_prices_leaves_returns_unchanged` | fixed | WS2.6 / WS2.7 |
| PR-1b | `test_pairs_stress_bugs.py::test_PR_1b_near_zero_entry_spread_gives_bounded_pnl` | xfail | WS2.6 / WS2.7 |
| PR-2 | `test_pairs_stress_bugs.py::test_PR_2_pair_signals_causal_under_truncation` | xfail | WS2.6 / WS2.7 |
| PR-3 | `test_pairs_stress_bugs.py::test_PR_3_pairs_analyze_json_valid_with_infinite_half_life` | xfail | WS2.6 / WS2.7 |
| PR-4 | `test_pairs_stress_bugs.py::test_PR_4_pairs_scan_does_not_500_on_numpy_types` | xfail | WS2.6 / WS2.7 |
| ST-1 | `test_pairs_stress_bugs.py::test_ST_1_regimes_causal` | xfail | WS2.4 |
| ST-2 | `test_pairs_stress_bugs.py::test_ST_2_regime_tests_use_contiguous_slices` | xfail | WS2.4 |
| ST-3 | `test_pairs_stress_bugs.py::test_ST_3_monte_carlo_dispersion_reflects_position_size` | xfail | WS2.4 |
| API-1 | `test_api_data_alerts_bugs.py::test_API_1_json_response_nan_inf_does_not_500` | xfail | WS2.7 |
| API-2 | `test_api_data_alerts_bugs.py::test_API_2_errors_are_not_http_200` | xfail | WS2.7 |
| API-3 | `test_api_data_alerts_bugs.py::test_API_3_scan_total_return_not_prerounded` | xfail | WS2.7 |
| API-4 | `test_api_data_alerts_bugs.py::test_API_4_scan_does_not_swallow_exceptions` | xfail | WS2.7 |
| DATA-1 | `test_api_data_alerts_bugs.py::test_DATA_1_futures_symbol_not_sent_as_equity` | xfail | WS2.1 |
| DATA-2 | `test_api_data_alerts_bugs.py::test_DATA_2_adjusted_close_consistent_with_ohl` | xfail | WS2.1 |
| DATA-3 | `test_api_data_alerts_bugs.py::test_DATA_3_in_progress_bar_dropped` | xfail | WS2.1 |
| ALR-1 | `test_api_data_alerts_bugs.py::test_ALR_1_telegram_message_with_underscore_delivered` | xfail | WS1.7 |
| ALR-2 | `test_api_data_alerts_bugs.py::test_ALR_2_bot_token_not_in_logs_on_http_error` | xfail | WS1.7 |
| LLM-1 | `test_api_data_alerts_bugs.py::test_LLM_1_model_id_configurable_via_env` | xfail | WS1.8 |
| LLM-1b | `test_api_data_alerts_bugs.py::test_LLM_1b_api_failure_is_surfaced` | xfail | WS1.8 |
