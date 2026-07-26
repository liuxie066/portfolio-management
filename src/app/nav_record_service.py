"""NAV record orchestration service."""
from __future__ import annotations

import logging
import json
from datetime import date, datetime
from typing import Any, Optional

from src import config
from src.app.compensation_service import PartialWriteError
from src.app.nav_finality import NavWriteContext
from src.app.snapshot_service import snapshot_digest
from src.models import NAVHistory, PortfolioValuation
from src.time_utils import bj_today


class NavRecordService:
    """Coordinate NAV calculation, snapshot persistence, validation, and storage.

    ``manager`` owns shared NAV helper methods and runtime services.
    """

    def __init__(
        self,
        manager: Any,
        storage: Any,
        cash_flow_effect_service: Any = None,
    ):
        self.manager = manager
        self.storage = storage
        self._cash_flow_effect_service = cash_flow_effect_service

    def _load_navs(self, account: str) -> list:
        preload = getattr(self.storage, "preload_nav_index", None)
        if callable(preload):
            try:
                preload(account)
            except Exception:
                pass

        get_index = getattr(self.storage, "get_nav_index", None)
        nav_idx_payload = get_index(account) if callable(get_index) else {}
        if not isinstance(nav_idx_payload, dict):
            nav_idx_payload = {}

        navs = nav_idx_payload.get("_nav_objects") or []
        if isinstance(navs, list) and navs:
            return list(navs)

        get_history = getattr(self.storage, "get_nav_history", None)
        if callable(get_history):
            for kwargs in ({"days": 9999}, {}):
                try:
                    history = get_history(account, **kwargs)
                except TypeError:
                    continue
                if isinstance(history, list):
                    return list(history)
        return []

    @staticmethod
    def _blocking_valuation_warnings(valuation: PortfolioValuation) -> list[str]:
        warning_texts = [str(warning) for warning in (getattr(valuation, "warnings", None) or [])]
        blocking_markers = ("无法获取汇率", "价格缺失，无法可靠估值")
        return [
            warning
            for warning in warning_texts
            if any(marker in warning for marker in blocking_markers)
        ]

    def _assert_valuation_reliable_for_write(self, valuation: PortfolioValuation) -> None:
        blocking_warnings = self._blocking_valuation_warnings(valuation)
        if not blocking_warnings:
            return
        raise ValueError("NAV 写入拒绝：估值存在阻断性告警: " + " | ".join(blocking_warnings))

    def _configured_effect_service(self) -> Any:
        if self._cash_flow_effect_service is not None:
            return self._cash_flow_effect_service
        from src.app.cash_flow_effect_store import CashFlowEffectStore

        db_path = CashFlowEffectStore.resolve_db_path()
        cutover = config.get("cash_flow.effects.cutover_date")
        if cutover in (None, "") and not db_path.exists():
            return None
        from src.app.cash_flow_effect_service import CashFlowEffectService

        store = CashFlowEffectStore(db_path)
        store.assert_cutover(cutover)
        self._cash_flow_effect_service = CashFlowEffectService(
            storage=self.storage,
            store=store,
        )
        return self._cash_flow_effect_service

    def _assert_cash_flow_ready_for_write(self, *, account: str, nav_date: date) -> None:
        result = None
        reconcile = getattr(self.storage, "reconcile_cash_flows", None)
        if callable(reconcile):
            result = reconcile(account=account, dry_run=True)
            if isinstance(result, dict):
                if result.get("success") is False or int(result.get("error_count") or 0):
                    raise ValueError(
                        "NAV 写入拒绝：cash_flow generated fields 校验失败: "
                        + str(result.get("error") or result.get("rows") or "unknown error")
                    )
                if int(result.get("change_count") or 0):
                    raise ValueError(
                        "NAV 写入拒绝：cash_flow generated fields 尚未确认；"
                        "请另行执行 `pm cash-flow reconcile --apply --confirm`"
                    )

        effect_service = self._configured_effect_service()
        result_rows = (
            result.get("rows")
            if isinstance(result, dict) and isinstance(result.get("rows"), list)
            else []
        )
        manual_rows = [
            row
            for row in result_rows
            if row.get("status") != "error"
            and row.get("exchange_rate_evidence_type") == "manual_supplement"
        ]
        if manual_rows and effect_service is None:
            raise ValueError(
                "NAV 写入拒绝：manual FX evidence 缺少 SQLite 确认记录"
            )
        if effect_service is not None:
            for row in manual_rows:
                confirmation = effect_service.store.latest_fx_confirmation(
                    str(row.get("record_id") or "")
                )
                expected = {
                    "source_hash": str(row.get("source_hash") or ""),
                    "exchange_rate": str(row.get("exchange_rate")),
                    "exchange_rate_date": str(row.get("exchange_rate_date")),
                    "exchange_rate_source": str(row.get("exchange_rate_source")),
                    "exchange_rate_evidence_type": str(
                        row.get("exchange_rate_evidence_type")
                    ),
                    "cny_amount": str(row.get("cny_amount")),
                }
                if not confirmation or any(
                    str(confirmation.get(key)) != value
                    for key, value in expected.items()
                ):
                    raise ValueError(
                        "NAV 写入拒绝：manual FX evidence 未经独立确认或已失效: "
                        f"record_id={row.get('record_id')}"
                    )
        if effect_service is None:
            return
        gate = effect_service.nav_gate(account=account, nav_date=nav_date)
        if not gate.get("success"):
            raise ValueError(
                "NAV 写入拒绝：cash-flow holding effects 未解决: "
                + json.dumps(gate, ensure_ascii=False, default=str)
            )

    def record_nav(
        self,
        account: str,
        valuation: Optional[PortfolioValuation] = None,
        nav_date: Optional[date] = None,
        persist: bool = True,
        overwrite_existing: bool = False,
        dry_run: bool = False,
        use_bulk_persist: bool = False,
        run_id: Optional[str] = None,
        nav_write_context: Optional[NavWriteContext] = None,
    ) -> NAVHistory:
        today_value = nav_date or bj_today()
        today = today_value.date() if isinstance(today_value, datetime) else today_value
        if persist and not dry_run:
            self._assert_cash_flow_ready_for_write(account=account, nav_date=today)
        if valuation is None:
            valuation = self.manager.calculate_valuation(account)
        if persist and not dry_run:
            self._assert_valuation_reliable_for_write(valuation)

        current_year = today.strftime("%Y")
        start_year = config.get_start_year()

        stock_value = valuation.stock_value_cny + valuation.fund_value_cny
        cash_value = valuation.cash_value_cny
        total_value = stock_value + cash_value
        stock_ratio = stock_value / total_value if total_value > 0 else 0
        cash_ratio = cash_value / total_value if total_value > 0 else 0

        all_navs = self._load_navs(account)
        nav_index = self.manager._build_nav_lookup(all_navs)

        yesterday_nav = self.manager._find_latest_nav_before(all_navs, today, nav_index=nav_index)
        prev_year_end_nav = self.manager._find_year_end_nav(all_navs, str(today.year - 1), nav_index=nav_index)
        prev_month_end_nav = self.manager._find_prev_month_end_nav(all_navs, today.year, today.month, nav_index=nav_index)
        mtd_return_base_nav = self.manager._find_mtd_return_base_nav(all_navs, today, nav_index=nav_index)
        ytd_return_base_nav = self.manager._find_ytd_return_base_nav(all_navs, today, nav_index=nav_index)
        last_nav = yesterday_nav

        yearly_data = {}
        for yr in range(start_year, today.year + 1):
            yr_str = str(yr)
            yearly_data[yr_str] = {
                "prev_end": self.manager._find_year_end_nav(all_navs, str(yr - 1), nav_index=nav_index),
                "end": self.manager._find_year_end_nav(all_navs, yr_str, nav_index=nav_index),
            }

        cash_flow_summary = self.manager._summarize_cash_flows(
            account=account,
            today=today,
            start_year=start_year,
            last_nav=last_nav,
        )
        daily_cash_flow = cash_flow_summary["daily"]
        monthly_cash_flow = cash_flow_summary["monthly"]
        yearly_cash_flow = cash_flow_summary["yearly"].get(current_year, 0.0)
        for yr_str, yd in yearly_data.items():
            yd["cash_flow"] = cash_flow_summary["yearly"].get(yr_str, 0.0)
        cumulative_cash_flow = cash_flow_summary["cumulative"]
        gap_cash_flow = cash_flow_summary["gap"]

        calc = self.manager._calc_nav_metrics(
            account=account,
            today=today,
            total_value=total_value,
            yesterday_nav=yesterday_nav,
            prev_year_end_nav=prev_year_end_nav,
            prev_month_end_nav=prev_month_end_nav,
            mtd_return_base_nav=mtd_return_base_nav,
            ytd_return_base_nav=ytd_return_base_nav,
            last_nav=last_nav,
            yearly_data=yearly_data,
            daily_cash_flow=daily_cash_flow,
            monthly_cash_flow=monthly_cash_flow,
            yearly_cash_flow=yearly_cash_flow,
            cumulative_cash_flow=cumulative_cash_flow,
            start_year=start_year,
            gap_cash_flow=gap_cash_flow,
            all_navs=all_navs,
        )

        nav_record = self.manager._build_nav_record(
            today=today,
            account=account,
            valuation=valuation,
            stock_value=stock_value,
            cash_value=cash_value,
            total_value=total_value,
            stock_ratio=stock_ratio,
            cash_ratio=cash_ratio,
            daily_cash_flow=daily_cash_flow,
            monthly_cash_flow=monthly_cash_flow,
            yearly_cash_flow=yearly_cash_flow,
            yearly_data=yearly_data,
            cumulative_cash_flow=cumulative_cash_flow,
            start_year=start_year,
            **calc,
        )
        resolved_context = nav_write_context or NavWriteContext(
            status="manual",
            writer="nav-record",
            write_reason="direct_nav_record",
            nav_date=today,
            run_id=run_id,
        )
        if resolved_context.nav_date != today:
            raise ValueError(
                f"NAV finality nav_date {resolved_context.nav_date} does not match record date {today}"
            )
        resolved_context = resolved_context.with_runtime(run_id=run_id)
        details = dict(nav_record.details or {})
        details["finality"] = resolved_context.to_details()
        if resolved_context.run_id:
            details["run_id"] = resolved_context.run_id
        nav_record.details = details

        if not config.get_bool("nav.disable_runtime_validation", False):
            self.manager._validate_nav_record(
                nav_record=nav_record,
                last_nav=last_nav,
                prev_month_end_nav=prev_month_end_nav,
                prev_year_end_nav=prev_year_end_nav,
                mtd_return_base_nav=mtd_return_base_nav,
                ytd_return_base_nav=ytd_return_base_nav,
                daily_cash_flow=daily_cash_flow,
                monthly_cash_flow=monthly_cash_flow,
                yearly_cash_flow=yearly_cash_flow,
                gap_cash_flow=gap_cash_flow,
                initial_value=calc.get("initial_value"),
                cumulative_cash_flow=cumulative_cash_flow,
            )

        snapshot_rows = []
        if persist:
            snapshot_rows = self.manager.snapshot_service.build_holdings_snapshots(
                account=account, as_of=today.isoformat(), valuation=valuation
            )

        if persist:
            if use_bulk_persist and (not dry_run) and overwrite_existing:
                write_records = getattr(self.storage, "write_nav_records", None)
                if callable(write_records):
                    write_records([nav_record], mode="replace", allow_partial=False, dry_run=False)
                else:
                    raise AttributeError("storage does not support bulk NAV writes")
            else:
                write_record = getattr(self.storage, "write_nav_record", None)
                if callable(write_record):
                    write_record(nav_record, overwrite_existing=overwrite_existing, dry_run=dry_run)
                else:
                    raise AttributeError("storage does not support NAV writes")

        # Snapshot after NAV record to avoid orphaned snapshots on NAV write failure.
        if persist:
            try:
                self.manager.snapshot_service.persist_holdings_snapshot(
                    account=account,
                    today=today,
                    valuation=valuation,
                    dry_run=dry_run,
                )
            except Exception as exc:
                original_details = dict(nav_record.details or {})
                task_id = self.manager.compensation.new_task_id()
                failed_details = {
                    **original_details,
                    "snapshot_persisted": False,
                    "snapshot_error": str(exc),
                    "snapshot_status": "failed",
                    "snapshot_task_id": task_id,
                    "snapshot_retry_command": f"pm compensation retry --task-id {task_id} --confirm",
                }
                complete_details = {
                    **original_details,
                    "snapshot_persisted": True,
                    "snapshot_status": "complete",
                    "snapshot_digest": snapshot_digest(snapshot_rows),
                }
                target = {
                    "type": "HOLDINGS_SNAPSHOT_TARGET_SET",
                    "account": account,
                    "as_of": today.isoformat(),
                    "nav_record_id": nav_record.record_id,
                    "before": {"one_of": [original_details, failed_details]},
                    "target": {"details": complete_details},
                    "snapshots": [row.model_dump(mode="json") for row in snapshot_rows],
                    "digest": complete_details["snapshot_digest"],
                }
                nav_record.details = failed_details
                if not dry_run:
                    try:
                        self.manager._record_compensation(
                            operation_type="NAV_HOLDINGS_SNAPSHOT_FAILED",
                            account=account,
                            payload={"targets": [target]},
                            error=exc,
                            related_record_id=nav_record.record_id,
                            task_id=task_id,
                        )
                    except Exception as compensation_error:
                        raise PartialWriteError(
                            operation="NAV_RECORD",
                            account=account,
                            related_record_id=nav_record.record_id,
                            completed_steps=["nav_record_created"],
                            failed_step="holdings_snapshot",
                            task_id=None,
                            target_count=1,
                            compensation_persisted=False,
                            original_error=f"{exc}; compensation persistence failed: {compensation_error}",
                        ) from exc

                    patch = getattr(getattr(self.storage, "nav_history", None), "patch_nav_details", None)
                    if callable(patch) and nav_record.record_id:
                        try:
                            patch(nav_record.record_id, failed_details, dry_run=False)
                        except Exception as patch_error:
                            nav_record.details = {**failed_details, "snapshot_details_patch_error": str(patch_error)}
                logging.getLogger(__name__).warning(
                    "holdings_snapshot write failed for %s (%s): %s - NAV record was saved successfully",
                    today, account, exc,
                )

        if persist and not dry_run:
            self.manager._print_nav_summary(
                today=today,
                stock_value=stock_value,
                cash_value=cash_value,
                total_value=total_value,
                stock_ratio=stock_ratio,
                cash_ratio=cash_ratio,
                current_year=current_year,
                start_year=start_year,
                yesterday_nav=yesterday_nav,
                prev_year_end_nav=prev_year_end_nav,
                prev_month_end_nav=prev_month_end_nav,
                yearly_data=yearly_data,
                daily_cash_flow=daily_cash_flow,
                cumulative_cash_flow=cumulative_cash_flow,
                **calc,
            )

        return nav_record
