#!/usr/bin/env python3
"""Minimal test runner (no pytest dependency).

Usage:
  . .venv/bin/activate
  python tests/run_tests.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure repo root is on sys.path so `import src.*` works when running `python tests/run_tests.py`.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def test_currency_from_us_ticker_suffix():
    from src.broker_message_parser import parse_futu_fill_message
    msg = "成交提醒: 【成交提醒】成功买入20股$富途控股 (FUTU.US)$，成交价格：147，此笔订单委托已全部成交，2026/03/12 21:59:45 (香港)。【富途证券(香港)】"
    p = parse_futu_fill_message(msg)
    assert p.ok
    assert p.currency == "USD"
    assert "currency_reason=ticker_suffix:.US" in p.raw


def test_currency_from_hk_ticker_suffix():
    from src.broker_message_parser import parse_futu_fill_message
    msg = "成交提醒: 【成交提醒】成功卖出200股$腾讯控股 (00700.HK)$，成交价格：610，此笔订单委托已全部成交，2025/11/27 14:42:11 (香港)。【富途证券(香港)】"
    p = parse_futu_fill_message(msg)
    assert p.ok
    assert p.currency == "HKD"
    assert "currency_reason=ticker_suffix:.HK" in p.raw


def test_currency_fallback_venue_hint():
    from src.broker_message_parser import parse_futu_fill_message
    msg = "成交提醒: 【成交提醒】成功买入10股$某未知标的$，成交价格：10，此笔订单委托已全部成交，2026/03/12 21:59:45 (香港)。【富途证券(香港)】"
    p = parse_futu_fill_message(msg)
    assert p.ok
    assert p.currency == "HKD"
    assert "currency_reason=venue_hint:HK" in p.raw


def main() -> None:
    from tests.test_asset_utils_market_suffix import (
        test_validate_code_strips_market_suffix_and_normalizes_hk,
        test_detect_market_type_respects_suffix,
    )
    from tests.test_price_fetcher_single_fetch_cache_only import (
        test_single_fetch_cache_only_and_stale_fallback,
    )
    from tests.test_pricing_service import (
        test_batch_planner_fetch_non_us_uses_provider_batch,
        test_fetch_batch_wraps_optimized_payloads,
        test_fetch_batch_does_not_fail_when_payload_key_is_normalized,
        test_fetch_quote_falls_back_to_stale_cache_when_realtime_fails,
        test_fetch_quote_returns_structured_failure_without_cache_or_realtime,
        test_fetch_quote_returns_valid_cache_without_realtime_call,
        test_fetch_quote_saves_realtime_payload_and_returns_market_type,
    )
    from tests.test_pricing_providers import (
        test_tencent_batch_provider_fetches_cn_quote,
        test_us_batch_provider_falls_back_to_stale_cache,
        test_yahoo_chart_empty_quote_does_not_fetch_exchange_rate,
        test_yahoo_chart_single_and_batch_share_normalized_payload,
    )
    from tests.test_holdings_preload_minimal import (
        test_preload_builds_index_and_projection_and_avoids_refetch,
        test_upsert_uses_preloaded_cache_for_batch_updates,
        test_upsert_create_after_preload_missing_key_without_refetch,
    )
    from tests.test_holdings_bulk_upsert_minimal import (
        test_bulk_upsert_additive_preloads_once_per_account_and_batches_updates,
        test_bulk_upsert_replace_mixed_update_create_updates_caches,
    )
    from tests.test_nav_cashflow_perf_minimal import (
        test_nav_base_cache_month_boundary_and_invalidation_flag,
        test_cash_flow_agg_cache_updates_on_new_record,
        test_record_nav_avoids_get_nav_history_full_scan_when_preloaded,
    )
    from tests.test_nav_record_service import (
        test_nav_record_service_persists_run_id_in_details,
        test_nav_record_service_rejects_real_write_on_unreliable_valuation,
    )
    from tests.test_nav_bulk_upsert_minimal import (
        test_nav_bulk_upsert_uses_single_preload_and_batch_ops_for_n_le_500,
        test_nav_bulk_upsert_upsert_mode_keeps_existing_cache_values_for_none_fields,
        test_nav_bulk_upsert_updates_nav_index_cache_incrementally,
    )
    from tests.test_daily_top_holdings_merge_minimal import (
        test_full_report_top_holdings_merge_duplicates_and_cash_mmf_bucket,
    )
    from tests.test_report_query_service import (
        test_full_report_prefers_recorded_today_nav_over_synthetic,
        test_full_report_synthetic_nav_reuses_core_nav_calculation,
    )
    from tests.test_audit_fixes import (
        test_round_none_guard_in_nav_record_fields,
        test_zero_is_not_none_in_truthiness_check,
        test_sort_with_none_dates_uses_date_min,
        test_deduct_cash_prevalidates_insufficient_funds,
        test_deduct_cash_succeeds_when_sufficient,
        test_nav_calculator_warns_when_shares_zero_but_value_positive,
        test_name_update_compares_content_not_length,
        test_del_does_not_raise,
        test_singleton_lock_exists,
        test_rate_limiter_has_lock,
        test_prev_close_not_overwritten_when_valid,
        test_escape_filter_value_handles_quotes,
    )
    from tests.test_cash_service import (
        test_cash_service_get_cash_formats_cash_and_mmf_holdings,
    )
    from tests.test_feishu_efficiency import (
        test_get_holdings_uses_cache_when_loaded,
        test_get_holdings_includes_empty_when_requested,
        test_get_holdings_falls_through_when_cache_not_loaded,
        test_get_holdings_with_asset_type_bypasses_cache,
        test_get_transactions_pushes_date_filter_to_server,
        test_get_total_cash_flow_cny_uses_agg_cache,
    )
    from tests.test_pm_cli import (
        test_pm_report_requires_preview_flag,
        test_pm_report_preview_marks_noncanonical_output,
        test_pm_cash_passes_account,
        test_pm_json_suppresses_internal_stdout_by_default,
        test_pm_failure_payload_returns_nonzero_exit_code,
        test_pm_accounts_lists_discovered_accounts,
        test_pm_overview_passes_accounts_and_timeout,
        test_pm_cash_prefers_service_when_available,
        test_pm_init_nav_passes_account_and_write_flags,
        test_pm_init_nav_write_requires_confirm,
        test_pm_nav_record_passes_account_and_write_flags,
        test_pm_nav_record_write_requires_confirm,
        test_pm_daily_runs_nav_record_and_distribution,
        test_pm_daily_write_requires_confirm,
        test_pm_daily_failure_payload_returns_nonzero_exit_code,
        test_pm_positions_distribution_prefers_service_when_available,
        test_pm_nav_record_prefers_service_when_available,
        test_pm_daily_prefers_service_for_nav_and_distribution,
    )
    from tests.test_multi_account import (
        test_list_accounts_discovers_accounts_across_read_models,
        test_multi_account_overview_aggregates_successful_accounts,
    )
    from tests.test_service_application import (
        test_portfolio_service_generate_report_uses_direct_app_service,
        test_portfolio_service_get_cash_uses_direct_cash_service,
        test_portfolio_service_get_distribution_uses_direct_read_service,
        test_portfolio_service_full_report_uses_direct_app_service,
        test_portfolio_service_get_holdings_uses_direct_read_service,
        test_portfolio_service_get_nav_uses_direct_storage_path,
        test_portfolio_service_init_nav_history_uses_direct_app_service,
        test_portfolio_service_list_accounts_uses_direct_account_service,
        test_portfolio_service_multi_account_overview_uses_direct_account_service,
        test_portfolio_service_daily_report_bundle_reuses_one_snapshot,
        test_portfolio_service_record_nav_uses_direct_portfolio_path,
    )
    from tests.test_portfolio_read_service import (
        test_build_snapshot_passes_price_timeout_to_valuation,
    )
    from tests.test_service_client import (
        test_service_client_builds_local_request_urls,
        test_service_client_posts_daily_report_bundle_payload,
        test_service_client_posts_nav_record_payload,
        test_service_client_marks_unavailable_on_connection_error,
    )
    from tests.test_service_http import (
        test_http_service_routes_delegate_to_portfolio_service,
        test_http_service_rejects_unknown_report_type,
    )
    from tests.test_daily_report_entrypoints import (
        test_generate_daily_report_html_is_renderer_only,
        test_publish_daily_report_direct_path_uses_application_bundle_service,
        test_publish_daily_report_build_report_data_passes_account,
        test_publish_daily_report_futu_sync_defaults_to_dry_run,
        test_publish_daily_report_main_prints_result_while_suppressing_internal_stdout,
        test_publish_daily_report_main_quiet_suppresses_success_output,
        test_publish_daily_report_prefers_service_bundle,
        test_publish_daily_report_parse_args_uses_config_defaults_and_cli_overrides,
        test_publish_report_returns_local_artifact_without_public_url,
    )
    from tests.test_futu_balance_sync_service import (
        test_futu_openapi_provider_reads_defaults_from_config_file,
    )
    from tests.test_config import (
        test_config_typed_getters_use_yaml_file_then_env_overrides,
    )

    tests = [
        test_currency_from_us_ticker_suffix,
        test_currency_from_hk_ticker_suffix,
        test_currency_fallback_venue_hint,
        test_validate_code_strips_market_suffix_and_normalizes_hk,
        test_detect_market_type_respects_suffix,
        test_single_fetch_cache_only_and_stale_fallback,
        test_fetch_quote_returns_valid_cache_without_realtime_call,
        test_fetch_quote_falls_back_to_stale_cache_when_realtime_fails,
        test_fetch_quote_saves_realtime_payload_and_returns_market_type,
        test_fetch_quote_returns_structured_failure_without_cache_or_realtime,
        test_fetch_batch_wraps_optimized_payloads,
        test_fetch_batch_does_not_fail_when_payload_key_is_normalized,
        test_batch_planner_fetch_non_us_uses_provider_batch,
        test_tencent_batch_provider_fetches_cn_quote,
        test_us_batch_provider_falls_back_to_stale_cache,
        test_yahoo_chart_empty_quote_does_not_fetch_exchange_rate,
        test_yahoo_chart_single_and_batch_share_normalized_payload,
        test_preload_builds_index_and_projection_and_avoids_refetch,
        test_upsert_uses_preloaded_cache_for_batch_updates,
        test_upsert_create_after_preload_missing_key_without_refetch,
        test_bulk_upsert_additive_preloads_once_per_account_and_batches_updates,
        test_bulk_upsert_replace_mixed_update_create_updates_caches,
        test_nav_base_cache_month_boundary_and_invalidation_flag,
        test_cash_flow_agg_cache_updates_on_new_record,
        test_record_nav_avoids_get_nav_history_full_scan_when_preloaded,
        test_nav_record_service_persists_run_id_in_details,
        test_nav_record_service_rejects_real_write_on_unreliable_valuation,
        test_nav_bulk_upsert_uses_single_preload_and_batch_ops_for_n_le_500,
        test_nav_bulk_upsert_upsert_mode_keeps_existing_cache_values_for_none_fields,
        test_nav_bulk_upsert_updates_nav_index_cache_incrementally,
        test_full_report_top_holdings_merge_duplicates_and_cash_mmf_bucket,
        test_full_report_prefers_recorded_today_nav_over_synthetic,
        test_full_report_synthetic_nav_reuses_core_nav_calculation,
        # audit fix regression tests
        test_round_none_guard_in_nav_record_fields,
        test_zero_is_not_none_in_truthiness_check,
        test_sort_with_none_dates_uses_date_min,
        test_deduct_cash_prevalidates_insufficient_funds,
        test_deduct_cash_succeeds_when_sufficient,
        test_nav_calculator_warns_when_shares_zero_but_value_positive,
        test_name_update_compares_content_not_length,
        test_del_does_not_raise,
        test_singleton_lock_exists,
        test_rate_limiter_has_lock,
        test_prev_close_not_overwritten_when_valid,
        test_escape_filter_value_handles_quotes,
        test_cash_service_get_cash_formats_cash_and_mmf_holdings,
        # feishu efficiency tests
        test_get_holdings_uses_cache_when_loaded,
        test_get_holdings_includes_empty_when_requested,
        test_get_holdings_falls_through_when_cache_not_loaded,
        test_get_holdings_with_asset_type_bypasses_cache,
        test_get_transactions_pushes_date_filter_to_server,
        test_get_total_cash_flow_cny_uses_agg_cache,
        # CLI / entrypoint account coverage
        test_pm_report_requires_preview_flag,
        test_pm_report_preview_marks_noncanonical_output,
        test_pm_cash_passes_account,
        test_pm_json_suppresses_internal_stdout_by_default,
        test_pm_failure_payload_returns_nonzero_exit_code,
        test_pm_accounts_lists_discovered_accounts,
        test_pm_overview_passes_accounts_and_timeout,
        test_pm_cash_prefers_service_when_available,
        test_pm_init_nav_passes_account_and_write_flags,
        test_pm_init_nav_write_requires_confirm,
        test_pm_nav_record_passes_account_and_write_flags,
        test_pm_nav_record_write_requires_confirm,
        test_pm_daily_runs_nav_record_and_distribution,
        test_pm_daily_write_requires_confirm,
        test_pm_daily_failure_payload_returns_nonzero_exit_code,
        test_pm_positions_distribution_prefers_service_when_available,
        test_pm_nav_record_prefers_service_when_available,
        test_pm_daily_prefers_service_for_nav_and_distribution,
        test_list_accounts_discovers_accounts_across_read_models,
        test_multi_account_overview_aggregates_successful_accounts,
        test_portfolio_service_generate_report_uses_direct_app_service,
        test_portfolio_service_get_cash_uses_direct_cash_service,
        test_portfolio_service_get_distribution_uses_direct_read_service,
        test_portfolio_service_full_report_uses_direct_app_service,
        test_portfolio_service_get_holdings_uses_direct_read_service,
        test_portfolio_service_get_nav_uses_direct_storage_path,
        test_portfolio_service_init_nav_history_uses_direct_app_service,
        test_portfolio_service_list_accounts_uses_direct_account_service,
        test_portfolio_service_multi_account_overview_uses_direct_account_service,
        test_portfolio_service_daily_report_bundle_reuses_one_snapshot,
        test_portfolio_service_record_nav_uses_direct_portfolio_path,
        test_build_snapshot_passes_price_timeout_to_valuation,
        test_service_client_builds_local_request_urls,
        test_service_client_posts_daily_report_bundle_payload,
        test_service_client_posts_nav_record_payload,
        test_service_client_marks_unavailable_on_connection_error,
        test_http_service_routes_delegate_to_portfolio_service,
        test_http_service_rejects_unknown_report_type,
        test_generate_daily_report_html_is_renderer_only,
        test_publish_daily_report_direct_path_uses_application_bundle_service,
        test_publish_daily_report_build_report_data_passes_account,
        test_publish_daily_report_futu_sync_defaults_to_dry_run,
        test_publish_daily_report_main_prints_result_while_suppressing_internal_stdout,
        test_publish_daily_report_main_quiet_suppresses_success_output,
        test_publish_daily_report_prefers_service_bundle,
        test_publish_daily_report_parse_args_uses_config_defaults_and_cli_overrides,
        test_publish_report_returns_local_artifact_without_public_url,
        test_futu_openapi_provider_reads_defaults_from_config_file,
        test_config_typed_getters_use_yaml_file_then_env_overrides,
    ]
    for t in tests:
        t()
    print(f"OK ({len(tests)} tests)")


if __name__ == "__main__":
    main()
