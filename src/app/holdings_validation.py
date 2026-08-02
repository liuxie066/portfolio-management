"""Pure validation and evidence-backed completion proposals for raw holdings."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from typing import Any, Iterable, Mapping, Optional

from src.models import AssetClass, AssetType, Holding, Industry
from src.domain.holdings import (
    RawHoldingRecord,
    asset_class_for_economic_exposure,
)
from src.domain.holding_dates import parse_holding_date
from src.feishu.contracts import get_table_contract


VALIDATION_POLICY_VERSION = "holdings-validation.v1"
CURRENCY_POLICY_VERSION = "holdings-currency.v1"
ASSET_CLASS_POLICY_VERSION = "holdings-asset-class.v2"

_HOLDINGS_TABLE_CONTRACT = get_table_contract("holdings")
_HOLDINGS_CREATE_CONTRACT = _HOLDINGS_TABLE_CONTRACT.write_contract("create")
if _HOLDINGS_CREATE_CONTRACT is None:
    raise RuntimeError("holdings create contract is required for validation")
REQUIRED_FIELDS = _HOLDINGS_CREATE_CONTRACT.required_fields

_CURRENCY_RE = re.compile(r"^[A-Z]{3,5}$")
_CASH_ASSET_ID_RE = re.compile(r"^([A-Z]{3,5})-(CASH|MMF)$")
_MARKET_SUFFIX_RE = re.compile(r"\.([A-Z]{2})$")
_FUTU_MARKETS = {"US", "HK", "SH", "SZ", "CN"}
VALIDATION_RELEVANT_FIELDS = tuple(_HOLDINGS_TABLE_CONTRACT.fields_by_name)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, (str, int, float, Decimal)):
        return None
    normalized = str(value).strip()
    return normalized or None


def _decimal(value: Any) -> Optional[Decimal]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        result = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, AttributeError, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _canonical_number(value: Decimal) -> str:
    if value == 0:
        return "0"
    if value == value.to_integral():
        return str(value.quantize(Decimal("1")))
    return format(value.normalize(), "f")


def _futu_asset_identity(value: str) -> tuple[str, Optional[str]]:
    """Return a symbol plus an optional, preserved market qualifier."""

    normalized = str(value or "").strip().upper()
    if not normalized:
        return "", None
    if "." in normalized:
        first, remainder = normalized.split(".", 1)
        if first in _FUTU_MARKETS and remainder:
            return remainder, first
        remainder, last = normalized.rsplit(".", 1)
        if last in _FUTU_MARKETS and remainder:
            return remainder, last
    return normalized, None


def canonical_record_payload(raw_fields: Mapping[str, Any]) -> dict[str, Any]:
    """Canonicalize validation facts for stable workflow identities."""

    payload: dict[str, Any] = {}
    for field_name in VALIDATION_RELEVANT_FIELDS:
        value = raw_fields.get(field_name)
        if field_name in {"quantity", "avg_cost"}:
            parsed = _decimal(value)
            payload[field_name] = (
                _canonical_number(parsed)
                if parsed is not None
                else _text(value)
            )
        elif field_name == "asset_type":
            normalized = _text(value)
            payload[field_name] = normalized.lower() if normalized else None
        elif field_name == "currency":
            normalized = _text(value)
            payload[field_name] = normalized.upper() if normalized else None
        elif field_name == "tag":
            candidate = value
            if isinstance(candidate, str):
                try:
                    parsed_json = json.loads(candidate)
                except (json.JSONDecodeError, TypeError, ValueError):
                    parsed_json = None
                if isinstance(parsed_json, list):
                    candidate = parsed_json
            if isinstance(candidate, list):
                payload[field_name] = [
                    item.strip() if isinstance(item, str) else item
                    for item in candidate
                ]
            else:
                payload[field_name] = _text(candidate)
        else:
            payload[field_name] = _text(value)
    return payload


def record_digest(raw_fields: Mapping[str, Any]) -> str:
    return _digest(canonical_record_payload(raw_fields))


@dataclass(frozen=True)
class FutuPositionEvidence:
    asset_id: str
    raw_code: str
    asset_name: str
    security_type: str
    market: str
    currency: Optional[str]
    currency_explicit: bool

    def matches(self, raw_asset_id: str) -> bool:
        target_symbol, target_market = _futu_asset_identity(raw_asset_id)
        evidence_symbol, evidence_market = _futu_asset_identity(
            self.raw_code or self.asset_id
        )
        if not evidence_market:
            explicit_market = str(self.market or "").strip().upper()
            evidence_market = explicit_market if explicit_market in _FUTU_MARKETS else None
        if not target_symbol or target_symbol != evidence_symbol:
            return False
        if target_market and evidence_market and target_market != evidence_market:
            return False
        return True

    def authority_identity(self) -> str:
        symbol, market = _futu_asset_identity(self.raw_code or self.asset_id)
        if not market:
            candidate = str(self.market or "").strip().upper()
            market = candidate if candidate in _FUTU_MARKETS else None
        return f"{market or 'UNQUALIFIED'}:{symbol}"


@dataclass(frozen=True)
class FutuAccountEvidence:
    account: str
    source: str
    source_snapshot_id: str
    source_as_of: str
    positions: tuple[FutuPositionEvidence, ...]
    profile_fingerprint: str = ""
    account_fingerprint: str = ""

    def exact_position(self, asset_id: str) -> tuple[Optional[FutuPositionEvidence], str]:
        matches = [position for position in self.positions if position.matches(asset_id)]
        if not matches:
            return None, "FUTU_POSITION_NOT_FOUND"
        if len(matches) > 1:
            return None, "FUTU_POSITION_AMBIGUOUS"
        return matches[0], "FUTU_POSITION_EXACT"


@dataclass(frozen=True)
class HoldingsEvidenceBundle:
    futu_by_account: Mapping[str, FutuAccountEvidence] = field(default_factory=dict)
    source_errors: Mapping[str, str] = field(default_factory=dict)

    def futu_position(
        self,
        *,
        account: Optional[str],
        broker: Optional[str],
        asset_id: Optional[str],
    ) -> tuple[Optional[FutuPositionEvidence], Optional[FutuAccountEvidence], str]:
        if not account or not asset_id or not _is_futu_broker(broker):
            return None, None, "FUTU_NOT_APPLICABLE"
        evidence = self.futu_by_account.get(account)
        if evidence is None:
            reason = "FUTU_OBSERVATION_FAILED" if account in self.source_errors else "FUTU_NOT_OBSERVED"
            return None, None, reason
        position, reason = evidence.exact_position(asset_id)
        return position, evidence, reason


@dataclass(frozen=True)
class FieldOutcome:
    field: str
    status: str
    current: Any = None
    proposed: Any = None
    authority: Optional[str] = None
    authority_id: Optional[str] = None
    reason_code: str = ""
    blocks_official_nav: bool = False
    evidence: Optional[Mapping[str, Any]] = None

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "field": self.field,
            "status": self.status,
            "current": self.current,
            "proposed": self.proposed,
            "authority": self.authority,
            "authority_id": self.authority_id,
            "reason_code": self.reason_code,
            "blocks_official_nav": self.blocks_official_nav,
        }
        if self.evidence is not None:
            payload["evidence"] = dict(self.evidence)
        return payload


@dataclass(frozen=True)
class RecordIssue:
    kind: str
    reason_code: str
    blocks_official_nav: bool
    details: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "reason_code": self.reason_code,
            "blocks_official_nav": self.blocks_official_nav,
            "details": dict(self.details),
        }


@dataclass
class RecordValidation:
    raw: RawHoldingRecord
    outcomes: list[FieldOutcome]
    issues: list[RecordIssue] = field(default_factory=list)

    @property
    def blocks_official_nav(self) -> bool:
        return any(item.blocks_official_nav and item.status != "valid" for item in self.outcomes) or any(
            issue.blocks_official_nav for issue in self.issues
        )

    @property
    def actionable(self) -> bool:
        return any(item.status in {"missing_completable", "missing_manual", "conflict", "invalid"} for item in self.outcomes) or bool(self.issues)

    @property
    def valid_for_typed_holding(self) -> bool:
        if self.issues:
            return False
        by_field = {item.field: item for item in self.outcomes}
        return all(
            by_field.get(name) is not None
            and (
                by_field[name].status == "valid"
                or (
                    by_field[name].status == "conflict"
                    and not by_field[name].blocks_official_nav
                )
            )
            for name in REQUIRED_FIELDS
        )

    def to_holding(
        self,
        *,
        confirmed_conflict_fields: Iterable[str] = (),
    ) -> Holding:
        confirmed = {str(field) for field in confirmed_conflict_fields}
        by_field = {item.field: item for item in self.outcomes}
        required_valid = all(
            by_field.get(name) is not None
            and (
                by_field[name].status == "valid"
                or (
                    by_field[name].status == "conflict"
                    and (
                        not by_field[name].blocks_official_nav
                        or name in confirmed
                    )
                )
            )
            for name in REQUIRED_FIELDS
        )
        if self.issues or not required_valid:
            raise ValueError(f"holding record is not fully valid: {self.raw.record_id}")
        fields = self.raw.canonical_fields()
        outcome_by_field = by_field
        quantity = _decimal(fields.get("quantity"))
        avg_cost = (
            _decimal(fields.get("avg_cost"))
            if outcome_by_field["avg_cost"].status == "valid"
            else None
        )
        asset_class_value = (
            _text(fields.get("asset_class"))
            if outcome_by_field["asset_class"].status in {"valid", "conflict"}
            else None
        )
        industry_value = (
            _text(fields.get("industry"))
            if outcome_by_field["industry"].status == "valid"
            else None
        )
        return Holding(
            record_id=self.raw.record_id,
            asset_id=str(_text(fields.get("asset_id")) or ""),
            asset_name=str(_text(fields.get("asset_name")) or ""),
            asset_type=AssetType(str(_text(fields.get("asset_type"))).lower()),
            account=str(_text(fields.get("account")) or ""),
            broker=str(_text(fields.get("broker")) or ""),
            quantity=float(quantity) if quantity is not None else 0.0,
            avg_cost=float(avg_cost) if avg_cost is not None else None,
            currency=str(_text(fields.get("currency")) or "").upper(),
            asset_class=(
                AssetClass(str(asset_class_value))
                if asset_class_value
                else None
            ),
            industry=(
                Industry(str(industry_value))
                if industry_value
                else None
            ),
            tag=(
                list(outcome_by_field["tag"].current or [])
                if outcome_by_field["tag"].status == "valid"
                else []
            ),
            created_at=(
                parse_holding_date(
                    fields.get("created_at"), field_name="created_at"
                )
                if outcome_by_field["created_at"].status == "valid"
                else None
            ),
            updated_at=(
                parse_holding_date(
                    fields.get("updated_at"), field_name="updated_at"
                )
                if outcome_by_field["updated_at"].status == "valid"
                else None
            ),
        )

    @property
    def record_digest(self) -> str:
        return record_digest(self.raw.raw_fields)

    def as_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.raw.record_id,
            "record_digest": self.record_digest,
            "identity": {
                "asset_id": _text(self.raw.raw_fields.get("asset_id")),
                "account": _text(self.raw.raw_fields.get("account")),
                "broker": _text(self.raw.raw_fields.get("broker")),
            },
            "blocks_official_nav": self.blocks_official_nav,
            "outcomes": [item.as_dict() for item in self.outcomes],
            "issues": [item.as_dict() for item in self.issues],
        }


@dataclass(frozen=True)
class HoldingsValidationReport:
    records: tuple[RecordValidation, ...]
    evidence_errors: Mapping[str, str]
    policy_version: str = VALIDATION_POLICY_VERSION
    currency_policy_version: str = CURRENCY_POLICY_VERSION
    asset_class_policy_version: str = ASSET_CLASS_POLICY_VERSION

    @property
    def blocking_count(self) -> int:
        return sum(item.blocks_official_nav for item in self.records)

    @property
    def actionable_count(self) -> int:
        return sum(item.actionable for item in self.records)

    def as_dict(self) -> dict[str, Any]:
        outcome_counts: dict[str, int] = {}
        issue_counts: dict[str, int] = {}
        for record in self.records:
            for outcome in record.outcomes:
                outcome_counts[outcome.status] = outcome_counts.get(outcome.status, 0) + 1
            for issue in record.issues:
                issue_counts[issue.kind] = issue_counts.get(issue.kind, 0) + 1
        source_complete = not self.evidence_errors
        return {
            "success": self.blocking_count == 0 and source_complete,
            "status": (
                "holdings_evidence_unavailable"
                if not source_complete
                else "valid"
                if self.blocking_count == 0
                else "holdings_attention_required"
            ),
            "read_only": True,
            "policy_version": self.policy_version,
            "currency_policy_version": self.currency_policy_version,
            "asset_class_policy_version": self.asset_class_policy_version,
            "record_count": len(self.records),
            "blocking_record_count": self.blocking_count,
            "actionable_record_count": self.actionable_count,
            "outcome_counts": outcome_counts,
            "issue_counts": issue_counts,
            "evidence_errors": dict(self.evidence_errors),
            "records": [item.as_dict() for item in self.records],
        }


def _is_futu_broker(value: Optional[str]) -> bool:
    normalized = str(value or "").strip().lower()
    return normalized in {"futu", "moomoo", "富途"}


class HoldingsValidator:
    """Pure validator. It performs no source read or state mutation."""

    def validate(
        self,
        records: Iterable[RawHoldingRecord],
        *,
        evidence: Optional[HoldingsEvidenceBundle] = None,
    ) -> HoldingsValidationReport:
        bundle = evidence or HoldingsEvidenceBundle()
        validated = [self._validate_record(record, bundle) for record in records]
        self._mark_duplicates(validated)
        return HoldingsValidationReport(tuple(validated), bundle.source_errors)

    def _validate_record(
        self,
        record: RawHoldingRecord,
        evidence: HoldingsEvidenceBundle,
    ) -> RecordValidation:
        fields = record.raw_fields
        asset_id = _text(fields.get("asset_id"))
        account = _text(fields.get("account"))
        broker = _text(fields.get("broker"))
        position, futu_evidence, futu_reason = evidence.futu_position(
            account=account,
            broker=broker,
            asset_id=asset_id,
        )

        outcomes: list[FieldOutcome] = []
        outcomes.append(self._required_text("asset_id", fields.get("asset_id"), "ASSET_ID_MISSING"))
        account_outcome = self._required_text("account", fields.get("account"), "ACCOUNT_MISSING")
        outcomes.append(account_outcome)
        outcomes.append(self._required_text("broker", fields.get("broker"), "BROKER_MISSING"))
        outcomes.append(self._quantity(fields.get("quantity")))

        type_outcome, effective_type = self._asset_type(fields.get("asset_type"), asset_id, position, futu_evidence)
        outcomes.append(type_outcome)
        outcomes.append(
            self._asset_name(fields.get("asset_name"), position, futu_evidence)
        )
        outcomes.append(
            self._currency(
                fields.get("currency"),
                asset_id=asset_id,
                asset_type=effective_type,
                asset_type_conflicted=type_outcome.status in {"conflict", "invalid", "missing_manual"},
                position=position,
                futu_evidence=futu_evidence,
                futu_reason=futu_reason,
            )
        )
        outcomes.append(self._asset_class(fields.get("asset_class"), effective_type))
        outcomes.append(self._optional_decimal("avg_cost", fields.get("avg_cost")))
        outcomes.append(self._optional_enum("industry", fields.get("industry"), Industry))
        outcomes.append(self._optional_tag(fields.get("tag")))
        outcomes.append(self._optional_transport_field("created_at", fields.get("created_at")))
        outcomes.append(self._optional_transport_field("updated_at", fields.get("updated_at")))

        issues: list[RecordIssue] = []
        if account_outcome.status != "valid":
            issues.append(
                RecordIssue(
                    kind="orphan",
                    reason_code="ACCOUNT_ORPHAN_GLOBAL_BLOCKER",
                    blocks_official_nav=True,
                    details={"record_id": record.record_id},
                )
            )
        return RecordValidation(record, outcomes, issues)

    @staticmethod
    def _required_text(field_name: str, value: Any, missing_reason: str) -> FieldOutcome:
        normalized = _text(value)
        if normalized is None:
            return FieldOutcome(
                field=field_name,
                status="missing_manual",
                current=value,
                reason_code=missing_reason,
                blocks_official_nav=True,
            )
        if not isinstance(value, (str, int, float, Decimal)):
            return FieldOutcome(
                field=field_name,
                status="invalid",
                current=value,
                reason_code=f"{field_name.upper()}_INVALID_TYPE",
                blocks_official_nav=True,
            )
        return FieldOutcome(
            field=field_name,
            status="valid",
            current=normalized,
            reason_code=f"{field_name.upper()}_PRESENT",
            blocks_official_nav=True,
        )

    @staticmethod
    def _quantity(value: Any) -> FieldOutcome:
        if value is None or (isinstance(value, str) and not value.strip()):
            return FieldOutcome(
                field="quantity",
                status="missing_manual",
                current=value,
                reason_code="QUANTITY_MISSING",
                blocks_official_nav=True,
            )
        parsed = _decimal(value)
        if parsed is None:
            return FieldOutcome(
                field="quantity",
                status="invalid",
                current=value,
                reason_code="QUANTITY_INVALID_OR_NONFINITE",
                blocks_official_nav=True,
            )
        return FieldOutcome(
            field="quantity",
            status="valid",
            current=_canonical_number(parsed),
            reason_code="QUANTITY_ZERO_VALID" if parsed == 0 else "QUANTITY_VALID",
            blocks_official_nav=True,
        )

    @staticmethod
    def _asset_type(
        raw_value: Any,
        asset_id: Optional[str],
        position: Optional[FutuPositionEvidence],
        futu_evidence: Optional[FutuAccountEvidence],
    ) -> tuple[FieldOutcome, Optional[AssetType]]:
        normalized = _text(raw_value)
        current_type: Optional[AssetType] = None
        if normalized is not None:
            try:
                current_type = AssetType(normalized.lower())
            except ValueError:
                return (
                    FieldOutcome(
                        field="asset_type",
                        status="invalid",
                        current=raw_value,
                        reason_code="ASSET_TYPE_INVALID",
                        blocks_official_nav=True,
                    ),
                    None,
                )

        proposed: Optional[AssetType] = None
        authority: Optional[str] = None
        authority_id: Optional[str] = None
        evidence_payload: Optional[dict[str, Any]] = None
        cash_match = _CASH_ASSET_ID_RE.match(str(asset_id or "").upper())
        if cash_match:
            proposed = AssetType.CASH if cash_match.group(2) == "CASH" else AssetType.MMF
            authority = "asset_id_explicit"
            authority_id = f"asset_id:{str(asset_id).upper()}"
        elif position is not None and futu_evidence is not None:
            market = position.market.upper()
            if position.security_type == "ETF":
                proposed = AssetType.EXCHANGE_FUND
            elif market == "HK":
                proposed = AssetType.HK_STOCK
            elif market == "US":
                proposed = AssetType.US_STOCK
            elif market in {"SH", "SZ", "CN"}:
                proposed = AssetType.A_STOCK
            if proposed is not None:
                authority = "futu_explicit"
                authority_id = _futu_authority_id(futu_evidence, position, "type")
                evidence_payload = {
                    "source": futu_evidence.source,
                    "source_snapshot_id": futu_evidence.source_snapshot_id,
                    "source_as_of": futu_evidence.source_as_of,
                    "profile_fingerprint": futu_evidence.profile_fingerprint,
                    "account_fingerprint": futu_evidence.account_fingerprint,
                    "security_type": position.security_type,
                    "market": position.market,
                }

        if current_type is None:
            if proposed is None:
                return (
                    FieldOutcome(
                        field="asset_type",
                        status="missing_manual",
                        current=raw_value,
                        reason_code="ASSET_TYPE_AUTHORITY_UNAVAILABLE",
                        blocks_official_nav=True,
                    ),
                    None,
                )
            return (
                FieldOutcome(
                    field="asset_type",
                    status="missing_completable",
                    current=raw_value,
                    proposed=proposed.value,
                    authority=authority,
                    authority_id=authority_id,
                    reason_code="ASSET_TYPE_COMPLETION_AVAILABLE",
                    blocks_official_nav=True,
                    evidence=evidence_payload,
                ),
                proposed,
            )
        if proposed is not None and current_type != proposed:
            return (
                FieldOutcome(
                    field="asset_type",
                    status="conflict",
                    current=current_type.value,
                    proposed=proposed.value,
                    authority=authority,
                    authority_id=authority_id,
                    reason_code="ASSET_TYPE_AUTHORITY_CONFLICT",
                    blocks_official_nav=True,
                    evidence=evidence_payload,
                ),
                None,
            )
        return (
            FieldOutcome(
                field="asset_type",
                status="valid",
                current=current_type.value,
                proposed=proposed.value if proposed else None,
                authority=authority or "manual_raw_unverified",
                authority_id=authority_id,
                reason_code="ASSET_TYPE_VALID",
                blocks_official_nav=True,
                evidence=evidence_payload,
            ),
            current_type,
        )

    @staticmethod
    def _asset_name(
        raw_value: Any,
        position: Optional[FutuPositionEvidence],
        futu_evidence: Optional[FutuAccountEvidence],
    ) -> FieldOutcome:
        current = _text(raw_value)
        proposed = _text(position.asset_name) if position is not None else None
        evidence_payload = None
        authority_id = None
        if proposed and futu_evidence and position:
            authority_id = _futu_authority_id(futu_evidence, position, "name")
            evidence_payload = {
                "source": futu_evidence.source,
                "source_snapshot_id": futu_evidence.source_snapshot_id,
                "source_as_of": futu_evidence.source_as_of,
                "profile_fingerprint": futu_evidence.profile_fingerprint,
                "account_fingerprint": futu_evidence.account_fingerprint,
            }
        if current is None and proposed:
            return FieldOutcome(
                field="asset_name",
                status="missing_completable",
                current=raw_value,
                proposed=proposed,
                authority="futu_explicit",
                authority_id=authority_id,
                reason_code="ASSET_NAME_COMPLETION_AVAILABLE",
                blocks_official_nav=True,
                evidence=evidence_payload,
            )
        if current is None:
            return FieldOutcome(
                field="asset_name",
                status="missing_manual",
                current=raw_value,
                reason_code="ASSET_NAME_MISSING",
                blocks_official_nav=True,
            )
        if proposed and current != proposed:
            return FieldOutcome(
                field="asset_name",
                status="conflict",
                current=current,
                proposed=proposed,
                authority="futu_explicit",
                authority_id=authority_id,
                reason_code="ASSET_NAME_AUTHORITY_CONFLICT",
                blocks_official_nav=False,
                evidence=evidence_payload,
            )
        return FieldOutcome(
            field="asset_name",
            status="valid",
            current=current,
            proposed=proposed,
            authority="futu_explicit" if proposed else "manual_raw_unverified",
            authority_id=authority_id,
            reason_code="ASSET_NAME_VALID",
            blocks_official_nav=False,
            evidence=evidence_payload,
        )

    @staticmethod
    def _currency(
        raw_value: Any,
        *,
        asset_id: Optional[str],
        asset_type: Optional[AssetType],
        asset_type_conflicted: bool,
        position: Optional[FutuPositionEvidence],
        futu_evidence: Optional[FutuAccountEvidence],
        futu_reason: str,
    ) -> FieldOutcome:
        raw_text = _text(raw_value)
        current = raw_text.upper() if raw_text else None
        if current is not None and not _CURRENCY_RE.fullmatch(current):
            return FieldOutcome(
                field="currency",
                status="invalid",
                current=raw_value,
                reason_code="CURRENCY_INVALID",
                blocks_official_nav=True,
            )

        proposed: Optional[str] = None
        authority: Optional[str] = None
        authority_id: Optional[str] = None
        evidence_payload: Optional[dict[str, Any]] = None

        if not asset_type_conflicted and position is not None and position.currency_explicit and position.currency:
            proposed = position.currency.upper()
            authority = "futu_explicit"
            if futu_evidence is not None:
                authority_id = _futu_authority_id(futu_evidence, position, "currency")
                evidence_payload = {
                    "source": futu_evidence.source,
                    "source_snapshot_id": futu_evidence.source_snapshot_id,
                    "source_as_of": futu_evidence.source_as_of,
                    "profile_fingerprint": futu_evidence.profile_fingerprint,
                    "account_fingerprint": futu_evidence.account_fingerprint,
                    "raw_code": position.raw_code,
                    "market": position.market,
                }
        if proposed is None and not asset_type_conflicted:
            cash_match = _CASH_ASSET_ID_RE.match(str(asset_id or "").upper())
            if asset_type in {AssetType.CASH, AssetType.MMF} and cash_match:
                proposed = cash_match.group(1)
                authority = "asset_id_explicit"
                authority_id = f"asset_id:{str(asset_id).upper()}:currency"
            elif asset_type in {AssetType.A_STOCK, AssetType.CN_FUND, AssetType.OTC_FUND}:
                proposed = "CNY"
                authority = "asset_type_policy"
                authority_id = f"asset_type:{asset_type.value}"
            elif asset_type in {AssetType.US_STOCK, AssetType.US_FUND}:
                proposed = "USD"
                authority = "asset_type_policy"
                authority_id = f"asset_type:{asset_type.value}"
            elif asset_type in {AssetType.EXCHANGE_FUND, AssetType.FUND}:
                suffix_match = _MARKET_SUFFIX_RE.search(str(asset_id or "").upper())
                suffix = suffix_match.group(1) if suffix_match else None
                if suffix == "US":
                    proposed = "USD"
                elif suffix in {"SH", "SZ"}:
                    proposed = "CNY"
                if proposed:
                    authority = "asset_id_explicit"
                    authority_id = f"asset_id:{str(asset_id).upper()}:market"

        if current is None:
            if proposed is not None:
                return FieldOutcome(
                    field="currency",
                    status="missing_completable",
                    current=raw_value,
                    proposed=proposed,
                    authority=authority,
                    authority_id=authority_id,
                    reason_code="CURRENCY_COMPLETION_AVAILABLE",
                    blocks_official_nav=True,
                    evidence=evidence_payload,
                )
            reason = (
                "CURRENCY_ASSET_TYPE_UNRESOLVED"
                if asset_type_conflicted or asset_type is None
                else "CURRENCY_EXPLICIT_EVIDENCE_REQUIRED"
            )
            if futu_reason == "FUTU_POSITION_AMBIGUOUS":
                reason = futu_reason
            return FieldOutcome(
                field="currency",
                status="missing_manual",
                current=raw_value,
                reason_code=reason,
                blocks_official_nav=True,
            )
        if proposed is not None and current != proposed:
            return FieldOutcome(
                field="currency",
                status="conflict",
                current=current,
                proposed=proposed,
                authority=authority,
                authority_id=authority_id,
                reason_code="CURRENCY_AUTHORITY_CONFLICT",
                blocks_official_nav=True,
                evidence=evidence_payload,
            )
        return FieldOutcome(
            field="currency",
            status="valid",
            current=current,
            proposed=proposed,
            authority=authority or "manual_raw_unverified",
            authority_id=authority_id,
            reason_code=("CURRENCY_AUTHORITY_MATCH" if proposed else "CURRENCY_MANUAL_RAW_VALID"),
            blocks_official_nav=True,
            evidence=evidence_payload,
        )

    @staticmethod
    def _asset_class(raw_value: Any, asset_type: Optional[AssetType]) -> FieldOutcome:
        current_text = _text(raw_value)
        current = None
        if current_text is not None:
            try:
                current = AssetClass(current_text)
            except ValueError:
                return FieldOutcome(
                    field="asset_class",
                    status="invalid",
                    current=raw_value,
                    reason_code="ASSET_CLASS_INVALID",
                    blocks_official_nav=False,
                )
        proposed = asset_class_for_economic_exposure(asset_type)
        if current is None and proposed is not None:
            return FieldOutcome(
                field="asset_class",
                status="missing_completable",
                current=raw_value,
                proposed=proposed.value,
                authority="asset_type_policy",
                authority_id=f"asset_type:{asset_type.value}:asset_class",
                reason_code="ASSET_CLASS_COMPLETION_AVAILABLE",
                blocks_official_nav=False,
            )
        if current is None:
            return FieldOutcome(
                field="asset_class",
                status="optional_missing",
                current=raw_value,
                reason_code="ASSET_CLASS_OPTIONAL_MISSING",
                blocks_official_nav=False,
            )
        if proposed is not None and current != proposed:
            return FieldOutcome(
                field="asset_class",
                status="conflict",
                current=current.value,
                proposed=proposed.value,
                authority="asset_type_policy",
                authority_id=f"asset_type:{asset_type.value}:asset_class",
                reason_code="ASSET_CLASS_AUTHORITY_CONFLICT",
                blocks_official_nav=False,
            )
        return FieldOutcome(
            field="asset_class",
            status="valid",
            current=current.value,
            proposed=proposed.value if proposed else None,
            authority="asset_type_policy" if proposed else "manual_raw_unverified",
            reason_code="ASSET_CLASS_VALID",
            blocks_official_nav=False,
        )

    @staticmethod
    def _optional_decimal(field_name: str, value: Any) -> FieldOutcome:
        if value is None or (isinstance(value, str) and not value.strip()):
            return FieldOutcome(
                field=field_name,
                status="optional_missing",
                current=value,
                reason_code=f"{field_name.upper()}_OPTIONAL_MISSING",
                blocks_official_nav=False,
            )
        parsed = _decimal(value)
        if parsed is None:
            return FieldOutcome(
                field=field_name,
                status="invalid",
                current=value,
                reason_code=f"{field_name.upper()}_INVALID_OR_NONFINITE",
                blocks_official_nav=False,
            )
        return FieldOutcome(
            field=field_name,
            status="valid",
            current=_canonical_number(parsed),
            reason_code=f"{field_name.upper()}_VALID",
            blocks_official_nav=False,
        )

    @staticmethod
    def _optional_enum(field_name: str, value: Any, enum_type: type) -> FieldOutcome:
        normalized = _text(value)
        if normalized is None:
            return FieldOutcome(
                field=field_name,
                status="optional_missing",
                current=value,
                reason_code=f"{field_name.upper()}_OPTIONAL_MISSING",
                blocks_official_nav=False,
            )
        try:
            parsed = enum_type(normalized)
        except ValueError:
            return FieldOutcome(
                field=field_name,
                status="invalid",
                current=value,
                reason_code=f"{field_name.upper()}_INVALID",
                blocks_official_nav=False,
            )
        return FieldOutcome(
            field=field_name,
            status="valid",
            current=parsed.value,
            reason_code=f"{field_name.upper()}_VALID",
            blocks_official_nav=False,
        )

    @staticmethod
    def _optional_tag(value: Any) -> FieldOutcome:
        if value is None or value == "":
            return FieldOutcome(
                field="tag",
                status="optional_missing",
                current=value,
                reason_code="TAG_OPTIONAL_MISSING",
                blocks_official_nav=False,
            )
        candidate = value
        if isinstance(candidate, str):
            try:
                candidate = json.loads(candidate)
            except (json.JSONDecodeError, TypeError, ValueError):
                candidate = None
        if not isinstance(candidate, list) or not all(
            isinstance(item, str) for item in candidate
        ):
            return FieldOutcome(
                field="tag",
                status="invalid",
                current=value,
                reason_code="TAG_INVALID",
                blocks_official_nav=False,
            )
        if not candidate:
            return FieldOutcome(
                field="tag",
                status="optional_missing",
                current=[],
                reason_code="TAG_OPTIONAL_MISSING",
                blocks_official_nav=False,
            )
        return FieldOutcome(
            field="tag",
            status="valid",
            current=list(candidate),
            reason_code="TAG_VALID",
            blocks_official_nav=False,
        )

    @staticmethod
    def _optional_transport_field(field_name: str, value: Any) -> FieldOutcome:
        normalized = _text(value)
        if normalized is not None:
            try:
                parse_holding_date(normalized, field_name=field_name)
            except (TypeError, ValueError):
                return FieldOutcome(
                    field=field_name,
                    status="invalid",
                    current=value,
                    reason_code=f"{field_name.upper()}_INVALID",
                    blocks_official_nav=False,
                )
        return FieldOutcome(
            field=field_name,
            status="valid" if normalized is not None else "optional_missing",
            current=normalized,
            authority="manual_raw_unverified" if normalized is not None else None,
            reason_code=(
                f"{field_name.upper()}_PRESENT"
                if normalized is not None
                else f"{field_name.upper()}_OPTIONAL_MISSING"
            ),
            blocks_official_nav=False,
        )

    @staticmethod
    def _mark_duplicates(records: list[RecordValidation]) -> None:
        grouped: dict[tuple[str, str, str], list[RecordValidation]] = {}
        for item in records:
            fields = item.raw.raw_fields
            asset_id = _text(fields.get("asset_id"))
            account = _text(fields.get("account"))
            broker = _text(fields.get("broker"))
            if not asset_id or not account or not broker:
                continue
            grouped.setdefault((asset_id, account, broker), []).append(item)
        for identity, duplicates in grouped.items():
            if len(duplicates) < 2:
                continue
            record_ids = sorted(item.raw.record_id for item in duplicates)
            for item in duplicates:
                item.issues.append(
                    RecordIssue(
                        kind="duplicate_identity",
                        reason_code="DUPLICATE_HOLDING_IDENTITY",
                        blocks_official_nav=True,
                        details={
                            "identity": {
                                "asset_id": identity[0],
                                "account": identity[1],
                                "broker": identity[2],
                            },
                            "record_ids": record_ids,
                        },
                    )
                )


def _futu_authority_id(
    account_evidence: FutuAccountEvidence,
    position: FutuPositionEvidence,
    field_name: str,
) -> str:
    profile = account_evidence.profile_fingerprint or "profile-unavailable"
    account_fingerprint = (
        account_evidence.account_fingerprint or "account-unavailable"
    )
    return (
        f"futu:{account_evidence.account}:{profile}:{account_fingerprint}:"
        f"{position.authority_identity()}:{field_name}"
    )
