"""Fresh cash-flow dataset builder and nonofficial summary queries."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
from typing import Any

from src.domain.cash_flow_contracts import (
    CASH_FLOW_DATASET_CONTRACT_VERSION,
    CashFlowDatasetBlocker,
    CashFlowDatasetSnapshot,
    CompletedCashFlowFacts,
    RawCashFlowRecord,
    aggregate_completed_cash_flows,
    cash_flow_dataset_fingerprint,
    cash_flow_fx_evidence_fingerprint,
    cash_flow_generated_fingerprint,
    derive_cash_flow_dataset_rows,
)


class CashFlowSummaryService:
    """Own the only storage-backed builder for run-scoped cash-flow facts."""

    def __init__(self, storage: Any):
        self.storage = storage

    @staticmethod
    def to_decimal(value: Any) -> Decimal:
        if value is None:
            return Decimal("0")
        if isinstance(value, Decimal):
            return value
        return Decimal(str(value))

    @classmethod
    def sum_cash_flows(cls, flows) -> float:
        total = Decimal("0")
        for flow in flows:
            total += cls.to_decimal(cls._cny_amount(flow))
        return float(total)

    @staticmethod
    def _cny_amount(flow) -> float:
        facts = CompletedCashFlowFacts.require(
            RawCashFlowRecord.from_cash_flow(flow)
        )
        return float(facts.cny_amount)

    def build_dataset(
        self,
        *,
        account: str,
        nav_date: date,
        run_id: str,
        start_year: int,
        cash_flow_effect_service: Any = None,
        operation_state_store: Any = None,
    ) -> CashFlowDatasetSnapshot:
        """Build one fresh, frozen source/evidence set for a target NAV run."""

        requested_account = str(account or "").strip()
        requested_run_id = str(run_id or "").strip()
        if not requested_account:
            raise ValueError("cash-flow dataset account is required")
        if not requested_run_id:
            raise ValueError("cash-flow dataset run_id is required")
        if isinstance(nav_date, datetime):
            requested_nav_date = nav_date.date()
        elif isinstance(nav_date, date):
            requested_nav_date = nav_date
        else:
            requested_nav_date = date.fromisoformat(str(nav_date)[:10])
        window_start = date(int(start_year), 1, 1)
        if window_start > requested_nav_date:
            raise ValueError("cash-flow dataset start_year is after nav_date")

        get_raw = getattr(self.storage, "get_raw_cash_flows", None)
        if not callable(get_raw):
            raise AttributeError(
                "storage does not support fresh raw cash-flow reads"
            )
        raw_rows = tuple(get_raw(account=requested_account))
        fetched_at = datetime.now(timezone.utc)
        if not all(isinstance(item, RawCashFlowRecord) for item in raw_rows):
            raise TypeError(
                "get_raw_cash_flows must return RawCashFlowRecord values"
            )

        row_derivation = derive_cash_flow_dataset_rows(
            raw_rows,
            account=requested_account,
        )
        blockers = list(row_derivation.blockers)
        completed_rows = list(row_derivation.completed_rows)
        audit_only_record_ids: list[str] = []
        foreign_in_window: list[CompletedCashFlowFacts] = []
        for completed in completed_rows:
            if not window_start <= completed.flow_date <= requested_nav_date:
                audit_only_record_ids.append(completed.record_id)
            elif completed.currency != "CNY":
                foreign_in_window.append(completed)

        fx_identities: list[dict[str, Any]] = []
        if foreign_in_window:
            from src.app.cash_flow_fx_confirmation import (
                evaluate_cash_flow_fx_confirmation,
                frozen_fx_confirmation_identity,
            )
            from src.app.operation_state_store import OperationStateStore

            operation_store = operation_state_store or OperationStateStore()
            operation_store.import_default_legacy_fx_confirmations()
            for facts in foreign_in_window:
                row = {
                    "record_id": facts.record_id,
                    "flow_date": facts.flow_date.isoformat(),
                    "generated_fingerprint": cash_flow_generated_fingerprint(facts),
                    "exchange_rate": facts.exchange_rate,
                    "cny_amount": facts.cny_amount,
                }
                confirmation = operation_store.latest_fx_confirmation(
                    facts.record_id
                )
                identity = frozen_fx_confirmation_identity(confirmation)
                fx_identities.append(identity)
                evaluation = evaluate_cash_flow_fx_confirmation(row, confirmation)
                if not evaluation.get("valid"):
                    blockers.append(CashFlowDatasetBlocker(
                        reason_code=str(
                            evaluation.get("reason_code")
                            or "fx_confirmation_invalid"
                        ),
                        message=(
                            "foreign cash-flow FX evidence is missing or stale"
                        ),
                        record_id=facts.record_id,
                        field="exchange_rate",
                        details={
                            "evaluation": evaluation,
                            "confirmation": identity,
                        },
                    ))

        daily, monthly, yearly, cumulative = aggregate_completed_cash_flows(
            completed_rows,
            start_date=window_start,
            end_date=requested_nav_date,
        )
        preliminary = CashFlowDatasetSnapshot(
            account=requested_account,
            nav_date=requested_nav_date,
            run_id=requested_run_id,
            fetched_at=fetched_at,
            window_start=window_start,
            window_end=requested_nav_date,
            raw_rows=raw_rows,
            completed_rows=tuple(completed_rows),
            blockers=tuple(blockers),
            duplicate_groups=row_derivation.duplicate_groups,
            audit_only_record_ids=tuple(audit_only_record_ids),
            daily=daily,
            monthly=monthly,
            yearly=yearly,
            cumulative=cumulative,
            financial_fingerprint=cash_flow_dataset_fingerprint(
                raw_rows,
                financial_only=True,
            ),
            full_fingerprint=cash_flow_dataset_fingerprint(
                raw_rows,
                financial_only=False,
            ),
            fx_confirmation_identities=tuple(fx_identities),
            fx_confirmation_fingerprint=cash_flow_fx_evidence_fingerprint(
                fx_identities
            ),
            effect_store_revision=None,
            effect_gate={
                "success": False,
                "status": "not_evaluated",
                "effect_store_revision": None,
            },
            contract_version=CASH_FLOW_DATASET_CONTRACT_VERSION,
        )
        if blockers:
            return preliminary

        if cash_flow_effect_service is None:
            revision = "not_activated"
            gate = {
                "success": True,
                "status": "not_activated",
                "effect_store_revision": revision,
                "account": requested_account,
                "nav_date": requested_nav_date.isoformat(),
                "cash_flow_financial_fingerprint": (
                    preliminary.financial_fingerprint
                ),
            }
            return replace(
                preliminary,
                effect_store_revision=revision,
                effect_gate=gate,
            )

        try:
            gate = cash_flow_effect_service.nav_gate(
                account=requested_account,
                nav_date=requested_nav_date,
                cash_flow_dataset=preliminary,
            )
        except Exception as exc:
            return replace(
                preliminary,
                blockers=(
                    *preliminary.blockers,
                    CashFlowDatasetBlocker(
                        reason_code="EFFECT_GATE_FAILED",
                        message="cash-flow holding effect gate failed",
                        details={"error": str(exc)},
                    ),
                ),
                effect_gate={
                    "success": False,
                    "status": "failed",
                    "error": str(exc),
                    "effect_store_revision": None,
                },
            )

        gate = dict(gate or {})
        revision = str(
            gate.get("effect_store_revision")
            or gate.get("scan_run_id")
            or ""
        ).strip() or None
        gate["effect_store_revision"] = revision
        gate_blockers = list(preliminary.blockers)
        gate_fingerprint = str(
            gate.get("cash_flow_financial_fingerprint") or ""
        ).strip()
        if not revision:
            gate_blockers.append(CashFlowDatasetBlocker(
                reason_code="EFFECT_REVISION_MISSING",
                message="cash-flow holding effect gate returned no revision",
                details={"gate": gate},
            ))
        if gate_fingerprint != preliminary.financial_fingerprint:
            gate_blockers.append(CashFlowDatasetBlocker(
                reason_code="EFFECT_SOURCE_FINGERPRINT_MISMATCH",
                message=(
                    "cash-flow holding effect gate is not bound to the "
                    "current dataset source"
                ),
                details={
                    "expected": preliminary.financial_fingerprint,
                    "actual": gate_fingerprint or None,
                },
            ))
        if gate.get("success") is not True:
            gate_blockers.append(CashFlowDatasetBlocker(
                reason_code="EFFECT_GATE_BLOCKED",
                message="cash-flow holding effects are unresolved",
                details={"gate": gate},
            ))
        return replace(
            preliminary,
            blockers=tuple(gate_blockers),
            effect_store_revision=revision,
            effect_gate=gate,
        )

    def summarize(
        self,
        account: str,
        today: date,
        start_year: int,
        last_nav=None,
    ) -> dict:
        dataset = self._build_nonofficial_dataset(
            account=account,
            nav_date=today,
            start_year=start_year,
            purpose="summary",
        )
        self._assert_queryable(dataset)
        return dataset.summary(last_nav=last_nav)

    def daily(self, account: str, flow_date: date) -> float:
        dataset = self._build_nonofficial_dataset(
            account=account,
            nav_date=flow_date,
            start_year=flow_date.year,
            purpose="daily",
        )
        self._assert_queryable(dataset)
        return float(dataset.daily.get(flow_date.isoformat(), Decimal("0")))

    def yearly(self, account: str, year: str) -> float:
        resolved_year = int(year)
        dataset = self._build_nonofficial_dataset(
            account=account,
            nav_date=date(resolved_year, 12, 31),
            start_year=resolved_year,
            purpose="yearly",
        )
        self._assert_queryable(dataset)
        return float(dataset.yearly.get(str(resolved_year), Decimal("0")))

    def monthly(self, account: str, year: int, month: int) -> float:
        month_end = date(year + (month // 12), (month % 12) + 1, 1) - timedelta(days=1)
        dataset = self._build_nonofficial_dataset(
            account=account,
            nav_date=month_end,
            start_year=year,
            purpose="monthly",
        )
        self._assert_queryable(dataset)
        return float(dataset.monthly.get(f"{year:04d}-{month:02d}", Decimal("0")))

    def period(self, account: str, start_date: date, end_date: date) -> float:
        dataset = self._build_nonofficial_dataset(
            account=account,
            nav_date=end_date,
            start_year=start_date.year,
            purpose="period",
        )
        self._assert_queryable(dataset)
        total = Decimal("0")
        for day_text, amount in dataset.daily.items():
            flow_date = date.fromisoformat(day_text)
            if start_date <= flow_date <= end_date:
                total += amount
        return float(total)

    def _build_nonofficial_dataset(
        self,
        *,
        account: str,
        nav_date: date,
        start_year: int,
        purpose: str,
    ) -> CashFlowDatasetSnapshot:
        return self.build_dataset(
            account=account,
            nav_date=nav_date,
            run_id=f"nonofficial:{purpose}:{account}:{nav_date.isoformat()}",
            start_year=start_year,
        )

    @staticmethod
    def _assert_queryable(dataset: CashFlowDatasetSnapshot) -> None:
        if dataset.blockers:
            raise ValueError(
                "cash-flow summary dataset is blocked: "
                + json.dumps(
                    [item.as_dict() for item in dataset.blockers],
                    ensure_ascii=False,
                    default=str,
                )
            )
