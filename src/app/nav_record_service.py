"""NAV record orchestration service."""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Optional

from src import config
from src.app.compensation_service import PartialWriteError
from src.app.nav_finality import NavWriteContext
from src.app.quality.evidence import valuation_quality_evidence
from src.app.quality.policy import assert_official_nav_write_allowed
from src.app.snapshot_service import snapshot_digest
from src.domain.nav_calculator import ClosedNavTarget, NavCalculator
from src.domain.snapshot_contracts import (
    NormalizedValuationSnapshot,
    attached_normalized_valuation,
)
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
        operation_state_store: Any = None,
    ):
        self.manager = manager
        self.storage = storage
        self._cash_flow_effect_service = cash_flow_effect_service
        self._operation_state_store = operation_state_store

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

    def cash_flow_dataset_dependencies(self) -> dict[str, Any]:
        """Expose builder-only dependencies without allowing downstream scans."""

        return {
            "cash_flow_effect_service": self._configured_effect_service(),
            "operation_state_store": self._operation_state_store,
        }

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
        cash_flow_dataset: Any = None,
        nav_history_snapshot: Optional[tuple[NAVHistory, ...]] = None,
        normalized_valuation: Optional[NormalizedValuationSnapshot] = None,
    ) -> NAVHistory:
        today_value = nav_date or bj_today()
        today = today_value.date() if isinstance(today_value, datetime) else today_value
        effective_run_id = str(
            run_id or getattr(nav_write_context, "run_id", None) or ""
        ).strip()
        if nav_write_context is not None and nav_write_context.nav_date != today:
            raise ValueError(
                f"NAV finality nav_date {nav_write_context.nav_date} "
                f"does not match record date {today}"
            )
        start_year = config.get_start_year()
        maintenance_write = bool(
            nav_write_context is not None
            and getattr(nav_write_context, "writer", None) == "nav-repair"
        )
        if nav_history_snapshot is not None:
            if persist or not maintenance_write:
                raise ValueError(
                    "explicit NAV history snapshot is restricted to "
                    "non-persisting nav-repair calculations"
                )
            if not isinstance(nav_history_snapshot, tuple):
                raise TypeError("NAV history snapshot must be an immutable tuple")
        if cash_flow_dataset is not None or persist or maintenance_write:
            from src.domain.cash_flow_contracts import CashFlowDatasetSnapshot

            if not isinstance(cash_flow_dataset, CashFlowDatasetSnapshot):
                raise ValueError(
                    "NAV 写入拒绝：official/maintenance calculation requires "
                    "CashFlowDatasetSnapshot"
                )
            cash_flow_dataset.assert_official_scope(
                account=account,
                nav_date=today,
                run_id=effective_run_id,
                start_year=start_year,
            )
        if valuation is None:
            valuation = self.manager.calculate_valuation(account)
        if valuation.account != account:
            raise ValueError(
                f"valuation account mismatch: {valuation.account} != {account}"
            )
        attached = attached_normalized_valuation(valuation)
        if normalized_valuation is None:
            normalized_valuation = attached
        elif not isinstance(
            normalized_valuation,
            NormalizedValuationSnapshot,
        ):
            raise TypeError(
                "normalized_valuation must be a NormalizedValuationSnapshot"
            )
        if persist and normalized_valuation is None:
            raise ValueError(
                "official NAV persistence requires a ValuationService-owned "
                "NormalizedValuationSnapshot"
            )
        if normalized_valuation is not None:
            if attached is not None and attached.digest != normalized_valuation.digest:
                raise ValueError(
                    "normalized valuation digest does not match the exact "
                    "object attached to the compatibility projection"
                )
            if persist and attached is None:
                raise ValueError(
                    "official NAV persistence requires a compatibility "
                    "projection derived from NormalizedValuationSnapshot"
                )
            normalized_valuation.assert_official_eligible(
                expected_source="valuation_service"
            )
            if normalized_valuation.account != account:
                raise ValueError("normalized valuation account mismatch")
            normalized_valuation.assert_compatible(valuation)
            canonical_valuation = normalized_valuation.to_portfolio_valuation()
        else:
            canonical_valuation = valuation

        valuation_quality = valuation_quality_evidence(canonical_valuation)
        if persist and not dry_run:
            self._assert_valuation_reliable_for_write(canonical_valuation)
            assert_official_nav_write_allowed(
                account=account,
                valuation_quality=valuation_quality,
            )

        current_year = today.strftime("%Y")

        valuation_projection = NavCalculator.project_valuation(
            canonical_valuation
        )
        canonical_valuation = canonical_valuation.model_copy(update={
            "total_value_cny": float(valuation_projection.total_value),
            "cash_value_cny": float(valuation_projection.cash_value),
            "stock_value_cny": float(
                valuation_projection.non_cash_value
                - valuation_projection.fund_value
            ),
            "fund_value_cny": float(valuation_projection.fund_value),
            "cn_asset_value": float(valuation_projection.cn_exposure_value),
            "us_asset_value": float(valuation_projection.us_exposure_value),
            "hk_asset_value": float(valuation_projection.hk_exposure_value),
        })
        stock_value = float(valuation_projection.non_cash_value)
        cash_value = float(valuation_projection.cash_value)
        total_value = float(valuation_projection.total_value)
        stock_ratio = float(valuation_projection.stock_weight)
        cash_ratio = float(valuation_projection.cash_weight)

        if nav_history_snapshot is None:
            all_navs = self._load_navs(account)
        else:
            all_navs = [nav.model_copy(deep=True) for nav in nav_history_snapshot]
            if any(nav.account != account for nav in all_navs):
                raise ValueError("NAV history snapshot account mismatch")
            history_dates = [nav.date for nav in all_navs]
            if len(set(history_dates)) != len(history_dates):
                raise ValueError("NAV history snapshot contains duplicate dates")
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

        if cash_flow_dataset is not None:
            cash_flow_summary = cash_flow_dataset.summary(last_nav=last_nav)
        else:
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
            valuation=canonical_valuation,
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
            run_id=effective_run_id or None,
        )
        if resolved_context.nav_date != today:
            raise ValueError(
                f"NAV finality nav_date {resolved_context.nav_date} does not match record date {today}"
            )
        resolved_context = resolved_context.with_runtime(
            run_id=effective_run_id or None
        )
        details = dict(nav_record.details or {})
        details["finality"] = resolved_context.to_details()
        details["valuation_quality"] = valuation_quality
        details["cash_flow_basis"] = NavCalculator.build_cash_flow_basis(
            nav_date=today,
            last_nav=last_nav,
            daily_cash_flow=daily_cash_flow,
            gap_cash_flow=gap_cash_flow,
            cash_flow_dataset=cash_flow_dataset,
        )
        if cash_flow_dataset is not None:
            details["cash_flow_dataset"] = cash_flow_dataset.details()
        if canonical_valuation.holdings_provenance:
            details["holdings_snapshot"] = dict(
                canonical_valuation.holdings_provenance
            )
        if resolved_context.run_id:
            details["run_id"] = resolved_context.run_id

        snapshot_rows = []
        if persist:
            if normalized_valuation is None:  # pragma: no cover - guarded above
                raise ValueError(
                    "snapshot persistence requires NormalizedValuationSnapshot"
                )
            snapshot_rows = self.manager.snapshot_service.build_holdings_snapshots(
                account=account,
                as_of=today.isoformat(),
                normalized_valuation=normalized_valuation,
            )
            details["snapshot_evidence"] = normalized_valuation.evidence(
                as_of=today.isoformat(),
                status="planned",
            )
        nav_record.details = details

        NavCalculator.assert_nav_invariants(
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
            cash_flow_dataset=cash_flow_dataset,
            require_finality=True,
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
                    normalized_valuation=normalized_valuation,
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

    def record_closed_nav(
        self,
        *,
        account: str,
        nav_date: date,
        total_value: Any,
        cash_value: Any,
        stock_value: Any,
        cash_flow_dataset: Any,
        run_id: str,
        overwrite_existing: bool = False,
        dry_run: bool = True,
        nav_write_context: Optional[NavWriteContext] = None,
    ) -> NAVHistory:
        """Compatibility CLOSED writer; S8 owns the final calculation invariant."""

        from src.domain.cash_flow_contracts import CashFlowDatasetSnapshot

        if not isinstance(cash_flow_dataset, CashFlowDatasetSnapshot):
            raise ValueError(
                "CLOSED NAV 写入拒绝：CashFlowDatasetSnapshot is required"
            )
        cash_flow_dataset.assert_official_scope(
            account=account,
            nav_date=nav_date,
            run_id=run_id,
            start_year=config.get_start_year(),
        )
        target = ClosedNavTarget.build(
            total_value=total_value,
            cash_value=cash_value,
            non_cash_value=stock_value,
        )
        normalized_valuation = NormalizedValuationSnapshot.from_closed_input(
            target,
            account=account,
            source_provenance={"run_id": run_id},
        )
        normalized_valuation.assert_official_eligible(
            expected_source="closed_input"
        )
        compatibility_valuation = normalized_valuation.to_portfolio_valuation()
        normalized_valuation.assert_compatible(compatibility_valuation)
        valuation_projection = NavCalculator.project_valuation(
            compatibility_valuation
        )

        context = nav_write_context or NavWriteContext(
            status="closed",
            writer="close-nav",
            write_reason="account_closed",
            nav_date=nav_date,
            run_id=run_id,
        )
        if context.nav_date != nav_date:
            raise ValueError(
                f"NAV finality nav_date {context.nav_date} does not match record date {nav_date}"
            )
        context = context.with_runtime(run_id=run_id)
        all_navs = self._load_navs(account)
        nav_index = self.manager._build_nav_lookup(all_navs)
        last_nav = self.manager._find_latest_nav_before(
            all_navs,
            nav_date,
            nav_index=nav_index,
        )
        cash_flow_summary = cash_flow_dataset.summary(last_nav=last_nav)
        daily_cash_flow = cash_flow_summary["daily"]
        gap_cash_flow = cash_flow_summary["gap"]
        nav_record = NAVHistory(
            date=nav_date,
            account=account,
            total_value=float(valuation_projection.total_value),
            cash_value=float(valuation_projection.cash_value),
            stock_value=float(valuation_projection.non_cash_value),
            fund_value=float(valuation_projection.fund_value),
            cn_stock_value=float(valuation_projection.cn_exposure_value),
            us_stock_value=float(valuation_projection.us_exposure_value),
            hk_stock_value=float(valuation_projection.hk_exposure_value),
            stock_weight=float(valuation_projection.stock_weight),
            cash_weight=float(valuation_projection.cash_weight),
            shares=compatibility_valuation.shares,
            nav=compatibility_valuation.nav,
            cash_flow=float(NavCalculator.quantize_money(daily_cash_flow)),
            share_change=0.0,
            details={
                "status": "CLOSED",
                "finality": context.to_details(),
                "run_id": run_id,
                "snapshot_evidence": normalized_valuation.evidence(
                    as_of=nav_date.isoformat(),
                    status="planned",
                ),
                "cash_flow_dataset": cash_flow_dataset.details(),
                "cash_flow_basis": NavCalculator.build_cash_flow_basis(
                    nav_date=nav_date,
                    last_nav=last_nav,
                    daily_cash_flow=daily_cash_flow,
                    gap_cash_flow=gap_cash_flow,
                    cash_flow_dataset=cash_flow_dataset,
                ),
            },
        )
        NavCalculator.assert_nav_invariants(
            nav_record=nav_record,
            last_nav=last_nav,
            daily_cash_flow=daily_cash_flow,
            gap_cash_flow=gap_cash_flow,
            cash_flow_dataset=cash_flow_dataset,
            require_finality=True,
        )
        write_record = getattr(self.storage, "write_nav_record", None)
        if not callable(write_record):
            raise AttributeError("storage does not support NAV writes")
        write_record(
            nav_record,
            overwrite_existing=overwrite_existing,
            dry_run=dry_run,
        )
        return nav_record
