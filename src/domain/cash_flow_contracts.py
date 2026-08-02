"""Canonical cash-flow row contracts and validation rules."""
from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import Enum
import hashlib
import json
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Optional

from src.models import Currency


CASH_FLOW_CONTRACT_VERSION = "pm.cash_flow.row.v1"
CASH_FLOW_GENERATED_FINGERPRINT_VERSION = "pm.cash_flow.generated.v1"
CASH_FLOW_DATASET_CONTRACT_VERSION = "pm.cash_flow.dataset.v1"
CASH_FLOW_MONEY_QUANT = Decimal("0.01")


class CashFlowType(str, Enum):
    """Domain-owned values derived from the sign of a cash-flow amount."""

    DEPOSIT = "DEPOSIT"
    WITHDRAW = "WITHDRAW"


CASH_FLOW_TYPE_VALUES = tuple(item.value for item in CashFlowType)
CASH_FLOW_TYPES = frozenset(CASH_FLOW_TYPE_VALUES)
CASH_FLOW_AMBIGUOUS_RATE_SOURCES = frozenset({
    "-",
    "manual",
    "n/a",
    "na",
    "none",
    "null",
    "placeholder",
    "tbd",
    "todo",
    "unknown",
})
BEIJING_TZ = timezone(timedelta(hours=8))
CASH_FLOW_MANUAL_REQUIRED_FIELDS = (
    "flow_date",
    "account",
    "broker",
    "amount",
    "currency",
)
CASH_FLOW_FINANCIAL_FIELDS = (
    *CASH_FLOW_MANUAL_REQUIRED_FIELDS,
    "flow_type",
    "cny_amount",
    "dedup_key",
    "exchange_rate",
    "source",
)
CASH_FLOW_CANONICAL_FIELDS = (
    *CASH_FLOW_FINANCIAL_FIELDS,
    "remark",
    "updated_at",
)


@dataclass(frozen=True)
class CashFlowValidationIssue:
    """One field-level reason a raw row cannot become a financial fact."""

    record_id: str
    field: str
    reason_code: str
    message: str
    raw_value: Any = None
    blocks_aggregate: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "field": self.field,
            "reason_code": self.reason_code,
            "message": self.message,
            "raw_value": self.raw_value,
            "blocks_aggregate": self.blocks_aggregate,
        }


class CashFlowContractError(ValueError):
    """A raw row failed manual or completed cash-flow validation."""

    def __init__(self, issues: tuple[CashFlowValidationIssue, ...]):
        self.issues = issues
        detail = "; ".join(
            f"{item.record_id or '(new)'}:{item.field}:{item.reason_code}"
            for item in issues
        )
        super().__init__(f"cash_flow validation blockers: {detail}")


@dataclass(frozen=True)
class RawCashFlowRecord:
    """One complete untyped source row before model defaults."""

    record_id: str
    raw_fields: Mapping[str, Any]
    source: str = "feishu"
    fetched_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "record_id", str(self.record_id or "").strip())
        object.__setattr__(
            self,
            "raw_fields",
            _freeze_mapping({
                str(key): value
                for key, value in self.raw_fields.items()
            }),
        )

    def canonical_fields(self) -> dict[str, Any]:
        return _thaw_value(self.raw_fields)

    @classmethod
    def from_cash_flow(cls, flow: Any) -> "RawCashFlowRecord":
        return cls(
            record_id=str(getattr(flow, "record_id", None) or ""),
            raw_fields={
                "flow_date": getattr(flow, "flow_date", None),
                "account": getattr(flow, "account", None),
                "broker": getattr(flow, "broker", None),
                "amount": getattr(flow, "amount", None),
                "currency": getattr(flow, "currency", None),
                "flow_type": getattr(flow, "flow_type", None),
                "cny_amount": getattr(flow, "cny_amount", None),
                "dedup_key": getattr(flow, "dedup_key", None),
                "exchange_rate": getattr(flow, "exchange_rate", None),
                "source": getattr(flow, "source", None),
                "remark": getattr(flow, "remark", None),
                "updated_at": getattr(flow, "updated_at", None),
            },
            source="model",
        )


@dataclass(frozen=True)
class ManualCashFlowFacts:
    """Validated operator-owned facts."""

    record_id: str
    flow_date: date
    account: str
    broker: str
    amount: Decimal
    currency: str
    remark: Optional[str] = None

    @property
    def expected_flow_type(self) -> str:
        return expected_cash_flow_type(self)

    @property
    def expected_dedup_key(self) -> str:
        return expected_cash_flow_dedup_key(self, self.expected_flow_type)

    @classmethod
    def validate(
        cls,
        record: RawCashFlowRecord,
    ) -> tuple[Optional["ManualCashFlowFacts"], tuple[CashFlowValidationIssue, ...]]:
        fields = record.raw_fields
        issues: list[CashFlowValidationIssue] = []
        flow_date = _parse_flow_date(
            fields.get("flow_date"),
            record_id=record.record_id,
            issues=issues,
        )
        account = _required_text(
            fields.get("account"),
            field="account",
            record_id=record.record_id,
            issues=issues,
        )
        broker = _required_text(
            fields.get("broker"),
            field="broker",
            record_id=record.record_id,
            issues=issues,
        )
        amount = _finite_decimal(
            fields.get("amount"),
            field="amount",
            record_id=record.record_id,
            issues=issues,
            nonzero=True,
        )
        currency = _supported_currency(
            fields.get("currency"),
            record_id=record.record_id,
            issues=issues,
        )
        quantized_amount: Optional[Decimal] = None
        if amount is not None:
            try:
                quantized_amount = _money(amount)
            except InvalidOperation:
                issues.append(_issue(
                    record.record_id,
                    "amount",
                    "AMOUNT_INVALID",
                    "amount cannot be represented at cash-flow money precision",
                    fields.get("amount"),
                ))
            else:
                if quantized_amount == 0:
                    issues.append(_issue(
                        record.record_id,
                        "amount",
                        "AMOUNT_ZERO",
                        "amount must remain nonzero at cash-flow money precision",
                        fields.get("amount"),
                    ))
        if issues:
            return None, tuple(issues)
        return cls(
            record_id=record.record_id,
            flow_date=flow_date,
            account=account,
            broker=broker,
            amount=quantized_amount,
            currency=currency,
            remark=_optional_text(fields.get("remark")),
        ), ()

    @classmethod
    def require(cls, record: RawCashFlowRecord) -> "ManualCashFlowFacts":
        facts, issues = cls.validate(record)
        if facts is None:
            raise CashFlowContractError(issues)
        return facts


@dataclass(frozen=True)
class CompletedCashFlowFacts:
    """Validated manual and system fields safe for writes and aggregation."""

    manual: ManualCashFlowFacts
    flow_type: str
    cny_amount: Decimal
    exchange_rate: Decimal
    dedup_key: str
    source: str
    updated_at: Any = None
    replayed: bool = False

    @property
    def record_id(self) -> str:
        return self.manual.record_id

    @property
    def flow_date(self) -> date:
        return self.manual.flow_date

    @property
    def account(self) -> str:
        return self.manual.account

    @property
    def broker(self) -> str:
        return self.manual.broker

    @property
    def amount(self) -> Decimal:
        return self.manual.amount

    @property
    def currency(self) -> str:
        return self.manual.currency

    @property
    def remark(self) -> Optional[str]:
        return self.manual.remark

    @classmethod
    def validate(
        cls,
        record: RawCashFlowRecord,
        *,
        manual: Optional[ManualCashFlowFacts] = None,
    ) -> tuple[Optional["CompletedCashFlowFacts"], tuple[CashFlowValidationIssue, ...]]:
        if manual is None:
            manual, manual_issues = ManualCashFlowFacts.validate(record)
            if manual is None:
                return None, manual_issues
        fields = record.raw_fields
        issues: list[CashFlowValidationIssue] = []
        flow_type = _flow_type(
            fields.get("flow_type"),
            manual=manual,
            record_id=record.record_id,
            issues=issues,
        )
        exchange_rate = _finite_decimal(
            fields.get("exchange_rate"),
            field="exchange_rate",
            record_id=record.record_id,
            issues=issues,
            positive=True,
        )
        cny_amount = _finite_decimal(
            fields.get("cny_amount"),
            field="cny_amount",
            record_id=record.record_id,
            issues=issues,
        )
        if exchange_rate is not None and manual.currency == Currency.CNY.value:
            if exchange_rate != Decimal("1"):
                issues.append(_issue(
                    record.record_id,
                    "exchange_rate",
                    "CNY_RATE_NOT_ONE",
                    "CNY exchange_rate must equal 1",
                    fields.get("exchange_rate"),
                ))
        quantized_cny_amount: Optional[Decimal] = None
        if cny_amount is not None:
            try:
                quantized_cny_amount = _money(cny_amount)
            except InvalidOperation:
                issues.append(_issue(
                    record.record_id,
                    "cny_amount",
                    "CNY_AMOUNT_INVALID",
                    "cny_amount cannot be represented at cash-flow money precision",
                    fields.get("cny_amount"),
                ))
        if exchange_rate is not None and quantized_cny_amount is not None:
            try:
                expected_cny = _money(manual.amount * exchange_rate)
            except InvalidOperation:
                issues.append(_issue(
                    record.record_id,
                    "exchange_rate",
                    "EXCHANGE_RATE_INVALID",
                    "amount * exchange_rate cannot be represented at cash-flow money precision",
                    fields.get("exchange_rate"),
                ))
            else:
                if quantized_cny_amount != expected_cny:
                    issues.append(_issue(
                        record.record_id,
                        "cny_amount",
                        "CNY_AMOUNT_MISMATCH",
                        f"cny_amount must equal amount * exchange_rate ({expected_cny})",
                        fields.get("cny_amount"),
                    ))
        expected_dedup = (
            manual.expected_dedup_key
            if flow_type in CASH_FLOW_TYPES
            else None
        )
        dedup_key = _required_text(
            fields.get("dedup_key"),
            field="dedup_key",
            record_id=record.record_id,
            issues=issues,
        )
        if dedup_key and expected_dedup and dedup_key != expected_dedup:
            issues.append(_issue(
                record.record_id,
                "dedup_key",
                "DEDUP_KEY_MISMATCH",
                "dedup_key disagrees with canonical manual facts",
                fields.get("dedup_key"),
            ))
        source = _required_text(
            fields.get("source"),
            field="source",
            record_id=record.record_id,
            issues=issues,
        )
        if issues:
            return None, tuple(issues)
        return cls(
            manual=manual,
            flow_type=flow_type,
            cny_amount=quantized_cny_amount,
            exchange_rate=exchange_rate,
            dedup_key=dedup_key,
            source=source,
            updated_at=fields.get("updated_at"),
        ), ()

    @classmethod
    def require(cls, record: RawCashFlowRecord) -> "CompletedCashFlowFacts":
        facts, issues = cls.validate(record)
        if facts is None:
            raise CashFlowContractError(issues)
        return facts

    @classmethod
    def build(
        cls,
        *,
        flow_date: date,
        account: str,
        broker: str,
        amount: Any,
        currency: str,
        cny_amount: Any = None,
        exchange_rate: Any = None,
        source: str = "manual",
        remark: Optional[str] = None,
        record_id: str = "",
        updated_at: Any = None,
    ) -> "CompletedCashFlowFacts":
        manual_record = RawCashFlowRecord(
            record_id=record_id,
            raw_fields={
                "flow_date": flow_date,
                "account": account,
                "broker": broker,
                "amount": amount,
                "currency": currency,
                "remark": remark,
            },
            source="application",
        )
        manual = ManualCashFlowFacts.require(manual_record)
        resolved_rate = (
            Decimal("1")
            if manual.currency == Currency.CNY.value and exchange_rate is None
            else exchange_rate
        )
        resolved_cny = (
            manual.amount
            if manual.currency == Currency.CNY.value and cny_amount is None
            else cny_amount
        )
        flow_type = manual.expected_flow_type
        dedup_key = manual.expected_dedup_key
        record = RawCashFlowRecord(
            record_id=record_id,
            raw_fields={
                **manual_record.canonical_fields(),
                "flow_type": flow_type,
                "cny_amount": resolved_cny,
                "exchange_rate": resolved_rate,
                "dedup_key": dedup_key,
                "source": source,
                "updated_at": updated_at,
            },
            source="application",
        )
        return cls.require(record)

    def with_record_id(self, record_id: str, *, replayed: bool = False) -> "CompletedCashFlowFacts":
        manual = replace(self.manual, record_id=str(record_id or "").strip())
        return replace(self, manual=manual, replayed=replayed)

    def to_fields(self) -> dict[str, Any]:
        return {
            "flow_date": self.flow_date,
            "account": self.account,
            "broker": self.broker,
            "amount": float(self.amount),
            "currency": self.currency,
            "flow_type": self.flow_type,
            "cny_amount": float(self.cny_amount),
            "dedup_key": self.dedup_key,
            "exchange_rate": float(self.exchange_rate),
            "source": self.source,
            "remark": self.remark,
        }

    def to_cash_flow(self) -> Any:
        from src.models import CashFlow

        result = CashFlow(
            record_id=self.record_id or None,
            flow_date=self.flow_date,
            account=self.account,
            broker=self.broker,
            amount=float(self.amount),
            currency=self.currency,
            cny_amount=float(self.cny_amount),
            exchange_rate=float(self.exchange_rate),
            flow_type=self.flow_type,
            dedup_key=self.dedup_key,
            source=self.source,
            remark=self.remark,
            updated_at=self.updated_at,
        )
        if self.replayed:
            result.mark_replayed()
        return result


@dataclass(frozen=True)
class CashFlowDuplicateGroup:
    """Distinct source rows that resolve to one canonical manual identity."""

    expected_dedup_key: str
    record_ids: tuple[str, ...]
    account: str
    broker: str
    flow_date: date
    amount: Decimal
    currency: str
    flow_type: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "expected_dedup_key": self.expected_dedup_key,
            "record_ids": list(self.record_ids),
            "record_count": len(self.record_ids),
            "account": self.account,
            "broker": self.broker,
            "flow_date": self.flow_date.isoformat(),
            "amount": _decimal_text(self.amount),
            "currency": self.currency,
            "flow_type": self.flow_type,
        }


@dataclass(frozen=True)
class CashFlowManualDatasetAudit:
    """Single authority for manual validation and expected-key duplicates."""

    valid_facts: tuple[ManualCashFlowFacts, ...]
    issues: tuple[CashFlowValidationIssue, ...]
    duplicate_groups: tuple[CashFlowDuplicateGroup, ...]
    valid_by_record_id: Mapping[str, ManualCashFlowFacts]
    duplicate_by_record_id: Mapping[str, CashFlowDuplicateGroup]

    @classmethod
    def build(
        cls,
        records: Iterable[RawCashFlowRecord],
    ) -> "CashFlowManualDatasetAudit":
        valid_facts: list[ManualCashFlowFacts] = []
        issues: list[CashFlowValidationIssue] = []
        facts_by_key: dict[str, list[ManualCashFlowFacts]] = {}
        valid_by_record_id: dict[str, ManualCashFlowFacts] = {}

        for record in records:
            facts, row_issues = ManualCashFlowFacts.validate(record)
            if facts is None:
                issues.extend(row_issues)
                continue
            valid_facts.append(facts)
            valid_by_record_id[facts.record_id] = facts
            facts_by_key.setdefault(facts.expected_dedup_key, []).append(facts)

        duplicate_groups: list[CashFlowDuplicateGroup] = []
        duplicate_by_record_id: dict[str, CashFlowDuplicateGroup] = {}
        for expected_key, grouped_facts in sorted(facts_by_key.items()):
            record_ids = tuple(sorted({facts.record_id for facts in grouped_facts}))
            if len(record_ids) < 2:
                continue
            representative = grouped_facts[0]
            group = CashFlowDuplicateGroup(
                expected_dedup_key=expected_key,
                record_ids=record_ids,
                account=representative.account,
                broker=representative.broker,
                flow_date=representative.flow_date,
                amount=representative.amount,
                currency=representative.currency,
                flow_type=representative.expected_flow_type,
            )
            duplicate_groups.append(group)
            for record_id in record_ids:
                duplicate_by_record_id[record_id] = group

        return cls(
            valid_facts=tuple(valid_facts),
            issues=tuple(issues),
            duplicate_groups=tuple(duplicate_groups),
            valid_by_record_id=MappingProxyType(valid_by_record_id),
            duplicate_by_record_id=MappingProxyType(duplicate_by_record_id),
        )

    def issues_for(self, record_id: str) -> tuple[CashFlowValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.record_id == record_id)


@dataclass(frozen=True)
class CashFlowDatasetBlocker:
    """One immutable reason a dataset cannot authorize an official NAV."""

    reason_code: str
    message: str
    record_id: str = ""
    field: Optional[str] = None
    details: Mapping[str, Any] = dataclass_field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "reason_code", str(self.reason_code or "").strip())
        object.__setattr__(self, "message", str(self.message or "").strip())
        object.__setattr__(self, "record_id", str(self.record_id or "").strip())
        object.__setattr__(
            self,
            "details",
            _freeze_mapping(self.details),
        )
        if not self.reason_code or not self.message:
            raise ValueError("cash-flow dataset blocker requires reason_code and message")

    @classmethod
    def from_issue(cls, issue: CashFlowValidationIssue) -> "CashFlowDatasetBlocker":
        return cls(
            reason_code=issue.reason_code,
            message=issue.message,
            record_id=issue.record_id,
            field=issue.field,
            details={"raw_value": _json_value(issue.raw_value)},
        )

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "reason_code": self.reason_code,
            "message": self.message,
            "record_id": self.record_id,
            "field": self.field,
        }
        if self.details:
            payload["details"] = _thaw_value(self.details)
        return payload


@dataclass(frozen=True)
class CashFlowDatasetRowDerivation:
    """Canonical raw-row validation result for one account dataset."""

    completed_rows: tuple[CompletedCashFlowFacts, ...]
    blockers: tuple[CashFlowDatasetBlocker, ...]
    duplicate_groups: tuple[CashFlowDuplicateGroup, ...]


def derive_cash_flow_dataset_rows(
    records: Iterable[RawCashFlowRecord],
    *,
    account: str,
) -> CashFlowDatasetRowDerivation:
    """Derive validated completed rows from one complete raw account scan."""

    raw_rows = tuple(records)
    requested_account = str(account or "").strip()
    if not requested_account:
        raise ValueError("cash-flow dataset account is required")
    if not all(isinstance(item, RawCashFlowRecord) for item in raw_rows):
        raise TypeError("cash-flow dataset rows must be RawCashFlowRecord values")

    audit = CashFlowManualDatasetAudit.build(raw_rows)
    blockers = [
        CashFlowDatasetBlocker.from_issue(issue)
        for issue in audit.issues
    ]
    record_id_counts: dict[str, int] = {}
    for raw in raw_rows:
        if raw.record_id:
            record_id_counts[raw.record_id] = record_id_counts.get(raw.record_id, 0) + 1
    repeated_record_ids = {
        record_id
        for record_id, count in record_id_counts.items()
        if count > 1
    }
    for record_id in sorted(repeated_record_ids):
        blockers.append(CashFlowDatasetBlocker(
            reason_code="RECORD_ID_DUPLICATE",
            message="cash-flow source scan contains a repeated record_id",
            record_id=record_id,
            details={"occurrences": record_id_counts[record_id]},
        ))

    duplicate_record_ids = set(audit.duplicate_by_record_id)
    for group in audit.duplicate_groups:
        blockers.append(CashFlowDatasetBlocker(
            reason_code="EXPECTED_DEDUP_KEY_DUPLICATE",
            message="multiple cash-flow rows share one canonical manual identity",
            details=group.as_dict(),
        ))

    completed_rows: list[CompletedCashFlowFacts] = []
    for raw in raw_rows:
        if not raw.record_id:
            blockers.append(CashFlowDatasetBlocker(
                reason_code="RECORD_ID_MISSING",
                message="cash-flow source row has no stable record_id",
            ))
            continue
        if raw.record_id in repeated_record_ids:
            continue
        manual = audit.valid_by_record_id.get(raw.record_id)
        if manual is None or raw.record_id in duplicate_record_ids:
            continue
        if manual.account != requested_account:
            blockers.append(CashFlowDatasetBlocker(
                reason_code="ACCOUNT_SCOPE_MISMATCH",
                message="cash-flow row account disagrees with requested dataset scope",
                record_id=raw.record_id,
                field="account",
                details={
                    "expected": requested_account,
                    "actual": manual.account,
                },
            ))
            continue
        completed, issues = CompletedCashFlowFacts.validate(
            raw,
            manual=manual,
        )
        if completed is None:
            blockers.extend(
                CashFlowDatasetBlocker.from_issue(issue)
                for issue in issues
            )
            continue
        completed_rows.append(completed)

    return CashFlowDatasetRowDerivation(
        completed_rows=tuple(completed_rows),
        blockers=tuple(blockers),
        duplicate_groups=audit.duplicate_groups,
    )


@dataclass(frozen=True)
class CashFlowDatasetSnapshot:
    """Run-scoped immutable cash-flow facts used by one official NAV write."""

    account: str
    nav_date: date
    run_id: str
    fetched_at: datetime
    window_start: date
    window_end: date
    raw_rows: tuple[RawCashFlowRecord, ...]
    completed_rows: tuple[CompletedCashFlowFacts, ...]
    blockers: tuple[CashFlowDatasetBlocker, ...]
    duplicate_groups: tuple[CashFlowDuplicateGroup, ...]
    audit_only_record_ids: tuple[str, ...]
    daily: Mapping[str, Decimal]
    monthly: Mapping[str, Decimal]
    yearly: Mapping[str, Decimal]
    cumulative: Decimal
    financial_fingerprint: str
    full_fingerprint: str
    fx_confirmation_identities: tuple[Mapping[str, Any], ...]
    fx_confirmation_fingerprint: str
    effect_store_revision: Optional[str]
    effect_gate: Mapping[str, Any]
    contract_version: str = CASH_FLOW_DATASET_CONTRACT_VERSION

    def __post_init__(self) -> None:
        account = str(self.account or "").strip()
        run_id = str(self.run_id or "").strip()
        if not account:
            raise ValueError("cash-flow dataset account is required")
        if not run_id:
            raise ValueError("cash-flow dataset run_id is required")
        if not isinstance(self.nav_date, date) or isinstance(self.nav_date, datetime):
            raise TypeError("cash-flow dataset nav_date must be a date")
        if not isinstance(self.window_start, date) or isinstance(self.window_start, datetime):
            raise TypeError("cash-flow dataset window_start must be a date")
        if not isinstance(self.window_end, date) or isinstance(self.window_end, datetime):
            raise TypeError("cash-flow dataset window_end must be a date")
        if self.window_end != self.nav_date:
            raise ValueError("cash-flow dataset window_end must equal nav_date")
        if self.window_start > self.window_end:
            raise ValueError("cash-flow dataset window is invalid")
        if not isinstance(self.fetched_at, datetime):
            raise TypeError("cash-flow dataset fetched_at must be a datetime")
        if self.fetched_at.utcoffset() is None:
            raise ValueError("cash-flow dataset fetched_at must be timezone-aware")
        object.__setattr__(self, "account", account)
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "raw_rows", tuple(self.raw_rows))
        object.__setattr__(self, "completed_rows", tuple(self.completed_rows))
        object.__setattr__(self, "blockers", tuple(self.blockers))
        object.__setattr__(self, "duplicate_groups", tuple(self.duplicate_groups))
        object.__setattr__(
            self,
            "audit_only_record_ids",
            tuple(sorted(str(item or "").strip() for item in self.audit_only_record_ids)),
        )
        object.__setattr__(self, "daily", _freeze_decimal_mapping(self.daily))
        object.__setattr__(self, "monthly", _freeze_decimal_mapping(self.monthly))
        object.__setattr__(self, "yearly", _freeze_decimal_mapping(self.yearly))
        object.__setattr__(self, "cumulative", Decimal(self.cumulative))
        object.__setattr__(
            self,
            "fx_confirmation_identities",
            tuple(_freeze_mapping(item) for item in self.fx_confirmation_identities),
        )
        revision = str(self.effect_store_revision or "").strip() or None
        object.__setattr__(self, "effect_store_revision", revision)
        object.__setattr__(self, "effect_gate", _freeze_mapping(self.effect_gate))

    @property
    def complete(self) -> bool:
        return (
            not self.blockers
            and self.effect_gate.get("success") is True
            and bool(self.effect_store_revision)
        )

    def summary(self, *, last_nav: Any = None) -> dict[str, Any]:
        daily = self.daily.get(self.nav_date.isoformat(), Decimal("0"))
        monthly = self.monthly.get(self.nav_date.strftime("%Y-%m"), Decimal("0"))
        yearly = {
            str(year): float(self.yearly.get(str(year), Decimal("0")))
            for year in range(self.window_start.year, self.nav_date.year + 1)
        }
        previous_date = getattr(last_nav, "date", None) if last_nav is not None else None
        if isinstance(previous_date, datetime):
            previous_date = previous_date.date()
        gap = Decimal("0")
        for day_text, amount in self.daily.items():
            flow_date = date.fromisoformat(day_text)
            if previous_date is None:
                if flow_date == self.nav_date:
                    gap += amount
            elif previous_date < flow_date <= self.nav_date:
                gap += amount
        return {
            "daily": float(daily),
            "monthly": float(monthly),
            "yearly": yearly,
            "cumulative": float(self.cumulative),
            "gap": float(gap),
        }

    def details(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "financial_fingerprint": self.financial_fingerprint,
            "full_fingerprint": self.full_fingerprint,
            "fetched_at": self.fetched_at.isoformat(),
            "window": {
                "start": self.window_start.isoformat(),
                "end": self.window_end.isoformat(),
                "start_inclusive": True,
                "end_inclusive": True,
            },
            "fx_confirmation_fingerprint": self.fx_confirmation_fingerprint,
            "effect_store_revision": self.effect_store_revision,
            "run_id": self.run_id,
            "source_record_count": len(self.raw_rows),
            "completed_record_count": len(self.completed_rows),
        }

    def assert_official_scope(
        self,
        *,
        account: str,
        nav_date: date,
        run_id: str,
        start_year: int,
    ) -> None:
        expected_account = str(account or "").strip()
        expected_run_id = str(run_id or "").strip()
        expected_start = date(int(start_year), 1, 1)
        mismatches: list[str] = []
        if self.contract_version != CASH_FLOW_DATASET_CONTRACT_VERSION:
            mismatches.append("contract_version")
        if self.account != expected_account:
            mismatches.append("account")
        if self.nav_date != nav_date or self.window_end != nav_date:
            mismatches.append("nav_date")
        if self.run_id != expected_run_id:
            mismatches.append("run_id")
        if self.window_start != expected_start:
            mismatches.append("window")
        if cash_flow_dataset_fingerprint(self.raw_rows, financial_only=True) != self.financial_fingerprint:
            mismatches.append("financial_fingerprint")
        if cash_flow_dataset_fingerprint(self.raw_rows, financial_only=False) != self.full_fingerprint:
            mismatches.append("full_fingerprint")
        derivation = derive_cash_flow_dataset_rows(
            self.raw_rows,
            account=self.account,
        )
        if self.completed_rows != derivation.completed_rows:
            mismatches.append("completed_rows")
        if self.duplicate_groups != derivation.duplicate_groups:
            mismatches.append("duplicate_groups")
        source_blockers = {
            json.dumps(
                item.as_dict(),
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
            for item in derivation.blockers
        }
        embedded_blockers = {
            json.dumps(
                item.as_dict(),
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
            for item in self.blockers
        }
        if not source_blockers.issubset(embedded_blockers):
            mismatches.append("source_blockers")
        expected_audit_only = tuple(sorted(
            facts.record_id
            for facts in derivation.completed_rows
            if not self.window_start <= facts.flow_date <= self.window_end
        ))
        if self.audit_only_record_ids != expected_audit_only:
            mismatches.append("audit_only_record_ids")
        expected_fx_fingerprint = cash_flow_fx_evidence_fingerprint(
            self.fx_confirmation_identities
        )
        if expected_fx_fingerprint != self.fx_confirmation_fingerprint:
            mismatches.append("fx_confirmation_fingerprint")
        expected_daily, expected_monthly, expected_yearly, expected_cumulative = (
            aggregate_completed_cash_flows(
                self.completed_rows,
                start_date=self.window_start,
                end_date=self.window_end,
            )
        )
        if dict(self.daily) != expected_daily:
            mismatches.append("daily")
        if dict(self.monthly) != expected_monthly:
            mismatches.append("monthly")
        if dict(self.yearly) != expected_yearly:
            mismatches.append("yearly")
        if self.cumulative != expected_cumulative:
            mismatches.append("cumulative")
        gate_revision = (
            self.effect_gate.get("effect_store_revision")
            or self.effect_gate.get("scan_run_id")
        )
        if str(gate_revision or "").strip() != str(self.effect_store_revision or ""):
            mismatches.append("effect_store_revision")
        if self.effect_gate.get("success") is True:
            if self.effect_gate.get("account") != self.account:
                mismatches.append("effect_gate_account")
            if self.effect_gate.get("nav_date") != self.nav_date.isoformat():
                mismatches.append("effect_gate_nav_date")
            if (
                self.effect_gate.get("cash_flow_financial_fingerprint")
                != self.financial_fingerprint
            ):
                mismatches.append("effect_gate_financial_fingerprint")
        if mismatches:
            raise ValueError(
                "NAV 写入拒绝：cash-flow dataset scope/integrity mismatch: "
                + ", ".join(sorted(set(mismatches)))
            )
        if self.blockers:
            raise ValueError(
                "NAV 写入拒绝：cash-flow dataset has blockers: "
                + json.dumps(
                    [item.as_dict() for item in self.blockers],
                    ensure_ascii=False,
                    default=str,
                )
            )
        if not self.complete:
            raise ValueError(
                "NAV 写入拒绝：cash-flow dataset effect gate is incomplete"
            )


def cash_flow_dataset_fingerprint(
    records: Iterable[RawCashFlowRecord],
    *,
    financial_only: bool,
) -> str:
    """Hash a complete raw row set with stable Missing/Null/Value states."""

    field_names = (
        CASH_FLOW_FINANCIAL_FIELDS
        if financial_only
        else CASH_FLOW_CANONICAL_FIELDS
    )
    rows = []
    for record in records:
        fields = record.raw_fields
        row = {
            "record_id": _field_state(record.record_id, present=True),
            "fields": {
                field: _field_state(fields.get(field), present=field in fields)
                for field in field_names
            },
        }
        if not financial_only:
            extra_fields = sorted(set(fields) - set(CASH_FLOW_CANONICAL_FIELDS))
            row["extra_fields"] = {
                field: _field_state(fields.get(field), present=True)
                for field in extra_fields
            }
        rows.append(row)
    rows.sort(
        key=lambda row: (
            str((row["record_id"].get("value") or "")),
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        )
    )
    payload = {
        "contract_version": CASH_FLOW_DATASET_CONTRACT_VERSION,
        "scope": "financial" if financial_only else "full",
        "rows": rows,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def cash_flow_fx_evidence_fingerprint(
    identities: Iterable[Mapping[str, Any]],
) -> str:
    normalized = [_json_value(dict(item)) for item in identities]
    normalized.sort(
        key=lambda item: json.dumps(
            item,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    payload = {
        "contract_version": CASH_FLOW_DATASET_CONTRACT_VERSION,
        "fx_confirmations": normalized,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def aggregate_completed_cash_flows(
    completed_rows: Iterable[CompletedCashFlowFacts],
    *,
    start_date: date,
    end_date: date,
) -> tuple[dict[str, Decimal], dict[str, Decimal], dict[str, Decimal], Decimal]:
    daily: dict[str, Decimal] = {}
    monthly: dict[str, Decimal] = {}
    yearly: dict[str, Decimal] = {}
    cumulative = Decimal("0")
    for facts in completed_rows:
        if not start_date <= facts.flow_date <= end_date:
            continue
        amount = facts.cny_amount
        day_key = facts.flow_date.isoformat()
        month_key = facts.flow_date.strftime("%Y-%m")
        year_key = facts.flow_date.strftime("%Y")
        daily[day_key] = daily.get(day_key, Decimal("0")) + amount
        monthly[month_key] = monthly.get(month_key, Decimal("0")) + amount
        yearly[year_key] = yearly.get(year_key, Decimal("0")) + amount
        cumulative += amount
    return daily, monthly, yearly, cumulative


def _field_state(value: Any, *, present: bool) -> dict[str, Any]:
    if not present:
        return {"state": "missing"}
    if value is None:
        return {"state": "null"}
    return {"state": "value", "value": _json_value(value)}


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return _json_value(value.value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return _canonical_decimal_text(value)
    if isinstance(value, Mapping):
        return {
            str(key): _json_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _freeze_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _freeze_mapping(value)
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(_freeze_value(item) for item in value)
    return value


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType({
        str(key): _freeze_value(item)
        for key, item in value.items()
    })


def _thaw_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_value(item) for item in value]
    return value


def _freeze_decimal_mapping(value: Mapping[str, Any]) -> Mapping[str, Decimal]:
    return MappingProxyType({
        str(key): Decimal(item)
        for key, item in value.items()
    })


def expected_cash_flow_type(manual: ManualCashFlowFacts) -> str:
    """Derive the system-owned direction from the signed manual amount."""

    if not isinstance(manual, ManualCashFlowFacts):
        raise TypeError("expected_cash_flow_type requires ManualCashFlowFacts")
    return (
        CashFlowType.DEPOSIT.value
        if manual.amount > 0
        else CashFlowType.WITHDRAW.value
    )


def cash_flow_generated_fingerprint(facts: CompletedCashFlowFacts) -> str:
    """Fingerprint the validated generated fields observed for FX binding."""

    if not isinstance(facts, CompletedCashFlowFacts):
        raise TypeError(
            "cash_flow_generated_fingerprint requires CompletedCashFlowFacts"
        )
    facts = CompletedCashFlowFacts.require(RawCashFlowRecord.from_cash_flow(facts))
    payload = {
        "contract_version": CASH_FLOW_GENERATED_FINGERPRINT_VERSION,
        "flow_type": facts.flow_type,
        "exchange_rate": _canonical_decimal_text(facts.exchange_rate),
        "cny_amount": format(_money(facts.cny_amount), ".2f"),
        "dedup_key": facts.dedup_key,
        "source": facts.source.strip(),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def normalize_cash_flow_rate_source(raw: Any) -> str:
    """Require one non-placeholder source usable for later investigation."""

    if not isinstance(raw, str):
        raise ValueError("exchange_rate_source must be traceable text")
    source = raw.strip()
    if not source or source.casefold() in CASH_FLOW_AMBIGUOUS_RATE_SOURCES:
        raise ValueError("exchange_rate_source must be traceable text")
    return source


def expected_cash_flow_dedup_key(
    manual: ManualCashFlowFacts,
    flow_type: str,
) -> str:
    resolved_flow_type = str(flow_type or "").strip().upper()
    if resolved_flow_type not in CASH_FLOW_TYPES:
        raise ValueError(f"unsupported cash_flow flow_type: {resolved_flow_type}")
    raw = "|".join((
        manual.account,
        manual.broker,
        manual.flow_date.isoformat(),
        resolved_flow_type,
        _decimal_text(manual.amount),
        manual.currency,
    ))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def expected_cash_flow_dedup_key_from_values(
    *,
    flow_date: Any,
    account: Any,
    broker: Any,
    amount: Any,
    currency: Any,
    flow_type: Any,
) -> str:
    record = RawCashFlowRecord(
        record_id="",
        raw_fields={
            "flow_date": flow_date,
            "account": account,
            "broker": broker,
            "amount": amount,
            "currency": currency,
        },
        source="dedup",
    )
    manual = ManualCashFlowFacts.require(record)
    return expected_cash_flow_dedup_key(manual, str(flow_type or ""))


def _issue(
    record_id: str,
    field: str,
    reason_code: str,
    message: str,
    raw_value: Any,
) -> CashFlowValidationIssue:
    return CashFlowValidationIssue(
        record_id=record_id,
        field=field,
        reason_code=reason_code,
        message=message,
        raw_value=raw_value,
    )


def _required_text(
    raw: Any,
    *,
    field: str,
    record_id: str,
    issues: list[CashFlowValidationIssue],
) -> Optional[str]:
    if not isinstance(raw, str) or not raw.strip():
        issues.append(_issue(
            record_id,
            field,
            f"{field.upper()}_MISSING",
            f"{field} must be nonblank text",
            raw,
        ))
        return None
    return raw.strip()


def _optional_text(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    return str(raw)


def _supported_currency(
    raw: Any,
    *,
    record_id: str,
    issues: list[CashFlowValidationIssue],
) -> Optional[str]:
    if not isinstance(raw, str) or not raw.strip():
        issues.append(_issue(
            record_id,
            "currency",
            "CURRENCY_MISSING",
            "currency is required",
            raw,
        ))
        return None
    candidate = raw.strip().upper()
    try:
        return Currency(candidate).value
    except ValueError:
        issues.append(_issue(
            record_id,
            "currency",
            "CURRENCY_UNSUPPORTED",
            f"unsupported currency: {candidate}",
            raw,
        ))
        return None


def _parse_flow_date(
    raw: Any,
    *,
    record_id: str,
    issues: list[CashFlowValidationIssue],
) -> Optional[date]:
    if raw is None or raw == "":
        issues.append(_issue(
            record_id,
            "flow_date",
            "FLOW_DATE_MISSING",
            "flow_date is required",
            raw,
        ))
        return None
    try:
        if isinstance(raw, bool):
            raise ValueError("boolean is not a date")
        if isinstance(raw, datetime):
            return raw.astimezone(BEIJING_TZ).date() if raw.tzinfo else raw.date()
        if isinstance(raw, date):
            return raw
        if isinstance(raw, (int, float, Decimal)):
            timestamp = Decimal(str(raw))
            if not timestamp.is_finite():
                raise ValueError("non-finite timestamp")
            return datetime.fromtimestamp(float(timestamp) / 1000, tz=BEIJING_TZ).date()
        if isinstance(raw, str):
            candidate = raw.strip()
            if len(candidate) == 10:
                return date.fromisoformat(candidate)
            parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
            return parsed.astimezone(BEIJING_TZ).date() if parsed.tzinfo else parsed.date()
        raise ValueError(f"unsupported date type: {type(raw).__name__}")
    except (ArithmeticError, OSError, OverflowError, TypeError, ValueError) as exc:
        issues.append(_issue(
            record_id,
            "flow_date",
            "FLOW_DATE_INVALID",
            f"invalid flow_date: {exc}",
            raw,
        ))
        return None


def _finite_decimal(
    raw: Any,
    *,
    field: str,
    record_id: str,
    issues: list[CashFlowValidationIssue],
    nonzero: bool = False,
    positive: bool = False,
) -> Optional[Decimal]:
    if raw is None or raw == "":
        issues.append(_issue(
            record_id,
            field,
            f"{field.upper()}_MISSING",
            f"{field} is required",
            raw,
        ))
        return None
    if isinstance(raw, bool):
        issues.append(_issue(
            record_id,
            field,
            f"{field.upper()}_INVALID",
            f"{field} must be a finite Decimal",
            raw,
        ))
        return None
    try:
        result = Decimal(str(raw).strip())
    except (InvalidOperation, AttributeError, TypeError, ValueError):
        result = Decimal("NaN")
    if not result.is_finite():
        issues.append(_issue(
            record_id,
            field,
            f"{field.upper()}_INVALID",
            f"{field} must be a finite Decimal",
            raw,
        ))
        return None
    if nonzero and result == 0:
        issues.append(_issue(
            record_id,
            field,
            f"{field.upper()}_ZERO",
            f"{field} must be nonzero",
            raw,
        ))
        return None
    if positive and result <= 0:
        issues.append(_issue(
            record_id,
            field,
            f"{field.upper()}_NOT_POSITIVE",
            f"{field} must be positive",
            raw,
        ))
        return None
    return result


def _flow_type(
    raw: Any,
    *,
    manual: ManualCashFlowFacts,
    record_id: str,
    issues: list[CashFlowValidationIssue],
) -> Optional[str]:
    if not isinstance(raw, str) or not raw.strip():
        issues.append(_issue(
            record_id,
            "flow_type",
            "FLOW_TYPE_MISSING",
            "flow_type is required",
            raw,
        ))
        return None
    candidate = raw.strip().upper()
    if candidate not in CASH_FLOW_TYPES:
        issues.append(_issue(
            record_id,
            "flow_type",
            "FLOW_TYPE_UNSUPPORTED",
            f"unsupported flow_type: {candidate}",
            raw,
        ))
        return None
    expected = manual.expected_flow_type
    if candidate != expected:
        issues.append(_issue(
            record_id,
            "flow_type",
            "FLOW_TYPE_SIGN_MISMATCH",
            f"flow_type must be {expected} for amount={manual.amount}",
            raw,
        ))
        return None
    return candidate


def _money(value: Decimal) -> Decimal:
    return value.quantize(CASH_FLOW_MONEY_QUANT, rounding=ROUND_HALF_UP)


def _decimal_text(value: Decimal) -> str:
    resolved = _money(value)
    # Persisted v0 keys hashed ``str(CashFlow.amount)`` after the Pydantic
    # cent-quantizer had converted the value to float. Keep that text contract
    # exactly, including Python's scientific notation boundary, while all
    # validation and financial arithmetic remain Decimal-based.
    return str(float(resolved))


def _canonical_decimal_text(value: Decimal) -> str:
    normalized = value.normalize()
    if normalized == 0:
        return "0"
    return format(normalized, "f")


__all__ = [
    "CASH_FLOW_CONTRACT_VERSION",
    "CASH_FLOW_AMBIGUOUS_RATE_SOURCES",
    "CASH_FLOW_GENERATED_FINGERPRINT_VERSION",
    "CASH_FLOW_FINANCIAL_FIELDS",
    "CASH_FLOW_MANUAL_REQUIRED_FIELDS",
    "CASH_FLOW_MONEY_QUANT",
    "CASH_FLOW_TYPE_VALUES",
    "CASH_FLOW_TYPES",
    "CashFlowType",
    "CashFlowDuplicateGroup",
    "CashFlowManualDatasetAudit",
    "CashFlowContractError",
    "CashFlowValidationIssue",
    "CompletedCashFlowFacts",
    "ManualCashFlowFacts",
    "RawCashFlowRecord",
    "cash_flow_generated_fingerprint",
    "expected_cash_flow_dedup_key",
    "expected_cash_flow_dedup_key_from_values",
    "expected_cash_flow_type",
    "normalize_cash_flow_rate_source",
]
