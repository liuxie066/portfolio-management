"""Canonical cash-flow row contracts and validation rules."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
from types import MappingProxyType
from typing import Any, Mapping, Optional

from src.models import Currency


CASH_FLOW_CONTRACT_VERSION = "pm.cash_flow.row.v1"
CASH_FLOW_MONEY_QUANT = Decimal("0.01")
CASH_FLOW_TYPES = frozenset({"DEPOSIT", "WITHDRAW"})
BEIJING_TZ = timezone(timedelta(hours=8))


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
            MappingProxyType({str(key): value for key, value in self.raw_fields.items()}),
        )

    def canonical_fields(self) -> dict[str, Any]:
        return dict(self.raw_fields)

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
            expected_cash_flow_dedup_key(manual, flow_type)
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
        flow_type = "DEPOSIT" if manual.amount > 0 else "WITHDRAW"
        dedup_key = expected_cash_flow_dedup_key(manual, flow_type)
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
    expected = "DEPOSIT" if manual.amount > 0 else "WITHDRAW"
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


__all__ = [
    "CASH_FLOW_CONTRACT_VERSION",
    "CASH_FLOW_MONEY_QUANT",
    "CASH_FLOW_TYPES",
    "CashFlowContractError",
    "CashFlowValidationIssue",
    "CompletedCashFlowFacts",
    "ManualCashFlowFacts",
    "RawCashFlowRecord",
    "expected_cash_flow_dedup_key",
    "expected_cash_flow_dedup_key_from_values",
]
