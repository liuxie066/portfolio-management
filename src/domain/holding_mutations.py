"""Canonical holding mutation contracts.

The transport model intentionally remains permissive for historical reads.  Write
paths use the contracts in this module so identity, field ownership, and
missing/null/value semantics are explicit before a Feishu request is built.
"""
from __future__ import annotations

from dataclasses import dataclass, fields as dataclass_fields
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from hashlib import sha256
import json
from math import isfinite
from types import MappingProxyType
from typing import Any, Mapping, Optional

from src.domain.holdings import RawHoldingRecord
from src.feishu.contracts import get_table_contract
from src.models import Holding


_HOLDING_TABLE_CONTRACT = get_table_contract("holdings")
IDENTITY_FIELDS = frozenset(_HOLDING_TABLE_CONTRACT.business_key)
HOLDING_VALUE_FIELDS = frozenset({
    "asset_name",
    "asset_type",
    "quantity",
    "avg_cost",
    "currency",
    "asset_class",
    "industry",
    "tag",
})
_HOLDING_SYSTEM_FIELDS = frozenset({"created_at", "updated_at"})
_HOLDING_REGISTRY_FIELDS = frozenset(_HOLDING_TABLE_CONTRACT.fields_by_name)
_UNMAPPED_HOLDING_FIELDS = _HOLDING_REGISTRY_FIELDS - (
    IDENTITY_FIELDS | HOLDING_VALUE_FIELDS | _HOLDING_SYSTEM_FIELDS
)
_UNKNOWN_HOLDING_FIELDS = (
    IDENTITY_FIELDS | HOLDING_VALUE_FIELDS | _HOLDING_SYSTEM_FIELDS
) - _HOLDING_REGISTRY_FIELDS
if _UNMAPPED_HOLDING_FIELDS or _UNKNOWN_HOLDING_FIELDS:
    raise RuntimeError(
        "holding domain projection disagrees with registry; "
        f"unmapped={sorted(_UNMAPPED_HOLDING_FIELDS)}, "
        f"unknown={sorted(_UNKNOWN_HOLDING_FIELDS)}"
    )
_HOLDING_CREATE_CONTRACT = _HOLDING_TABLE_CONTRACT.write_contract("create")
if _HOLDING_CREATE_CONTRACT is None:
    raise RuntimeError("holdings create contract is required")
_UNMAPPED_CREATE_FIELDS = (
    _HOLDING_CREATE_CONTRACT.required_fields
    - IDENTITY_FIELDS
    - HOLDING_VALUE_FIELDS
)
if _UNMAPPED_CREATE_FIELDS:
    raise RuntimeError(
        "holdings create contract has unmapped required fields: "
        + ", ".join(sorted(_UNMAPPED_CREATE_FIELDS))
    )
HOLDING_REQUIRED_VALUE_FIELDS = frozenset(
    _HOLDING_CREATE_CONTRACT.required_fields - IDENTITY_FIELDS
)
# A JSON empty list is the explicit empty tag value.  Null is deliberately not
# a tag operation because a model default must never acquire clear authority.
HOLDING_NULL_CLEARABLE_FIELDS = frozenset(
    field.name
    for field in _HOLDING_TABLE_CONTRACT.fields
    if field.clearable
)
HOLDING_REPAIRABLE_FIELDS = frozenset({
    "asset_name",
    "asset_type",
    "currency",
    "asset_class",
})


class _UnsetType:
    __slots__ = ()

    def __repr__(self) -> str:
        return "UNSET"

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self


UNSET = _UnsetType()


class AmbiguousHoldingIdentityError(ValueError):
    """A compatibility lookup omitted broker and matched multiple rows."""


class HoldingMutationConflictError(RuntimeError):
    """The fresh base no longer matches the mutation's bound base state."""


class HoldingMutationProofError(RuntimeError):
    """Fresh remote readback did not prove the requested owned fields."""


def _required_text(value: Any, *, field_name: str) -> str:
    resolved = str(value or "").strip()
    if not resolved:
        raise ValueError(f"{field_name} is required")
    return resolved


@dataclass(frozen=True, order=True)
class HoldingIdentity:
    """The only writable holding business identity."""

    asset_id: str
    account: str
    broker: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "asset_id",
            _required_text(self.asset_id, field_name="asset_id"),
        )
        object.__setattr__(
            self,
            "account",
            _required_text(self.account, field_name="account"),
        )
        object.__setattr__(
            self,
            "broker",
            _required_text(self.broker, field_name="broker"),
        )

    @classmethod
    def from_holding(cls, holding: Holding) -> "HoldingIdentity":
        return cls(holding.asset_id, holding.account, holding.broker)

    def as_dict(self) -> dict[str, str]:
        return {
            "asset_id": self.asset_id,
            "account": self.account,
            "broker": self.broker,
        }

    def cache_key(self) -> str:
        # JSON avoids delimiter collisions while remaining stable for the local
        # persistent index.
        return json.dumps(
            [self.asset_id, self.account, self.broker],
            ensure_ascii=False,
            separators=(",", ":"),
        )


_HOLDING_IDENTITY_MODEL_FIELDS = frozenset(
    item.name for item in dataclass_fields(HoldingIdentity)
)
if _HOLDING_IDENTITY_MODEL_FIELDS != IDENTITY_FIELDS:
    raise RuntimeError(
        "HoldingIdentity disagrees with holdings business_key; "
        f"model={sorted(_HOLDING_IDENTITY_MODEL_FIELDS)}, "
        f"registry={sorted(IDENTITY_FIELDS)}"
    )


def _enum_value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def _finite_number(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"invalid {field_name}: {value!r}")
    try:
        parsed = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, AttributeError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field_name}: {value!r}") from exc
    if not parsed.is_finite():
        raise ValueError(f"invalid {field_name}: {value!r}")
    try:
        result = float(parsed)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"invalid {field_name}: {value!r}") from exc
    if not isfinite(result):
        raise ValueError(f"invalid {field_name}: {value!r}")
    return result


def canonical_holding_value(field_name: str, value: Any) -> Any:
    """Canonicalize one persisted holding value without manufacturing defaults."""

    if field_name not in HOLDING_VALUE_FIELDS:
        raise ValueError(f"unsupported holding value field: {field_name}")
    if value is None:
        if field_name in HOLDING_NULL_CLEARABLE_FIELDS:
            return None
        raise ValueError(f"holding field {field_name} cannot be null")
    if field_name == "asset_name":
        return _required_text(value, field_name=field_name)
    if field_name == "asset_type":
        candidate = str(_enum_value(value)).strip().lower()
        if not candidate:
            raise ValueError("asset_type is required")
        return candidate
    if field_name in {"quantity", "avg_cost"}:
        return _finite_number(value, field_name=field_name)
    if field_name == "currency":
        candidate = str(value).strip().upper()
        if (
            not candidate
            or not candidate.isascii()
            or not candidate.isalpha()
            or not 3 <= len(candidate) <= 5
        ):
            raise ValueError(f"invalid currency: {value!r}")
        return candidate
    if field_name in {"asset_class", "industry"}:
        candidate = str(_enum_value(value)).strip()
        if not candidate:
            raise ValueError(f"holding field {field_name} cannot be blank")
        return candidate
    if field_name == "tag":
        if not isinstance(value, (list, tuple)) or not all(
            isinstance(item, str) for item in value
        ):
            raise ValueError("tag must be a list of strings")
        return tuple(value)
    raise AssertionError(field_name)


def holding_values(holding: Holding) -> dict[str, Any]:
    """Return a complete canonical business-value snapshot for one holding."""

    raw = {
        "asset_name": holding.asset_name,
        "asset_type": holding.asset_type,
        "quantity": holding.quantity,
        "avg_cost": holding.avg_cost,
        "currency": holding.currency,
        "asset_class": holding.asset_class,
        "industry": holding.industry,
        "tag": holding.tag,
    }
    values: dict[str, Any] = {}
    for field_name in HOLDING_VALUE_FIELDS:
        value = raw[field_name]
        if value is None and field_name in HOLDING_NULL_CLEARABLE_FIELDS:
            values[field_name] = None
        else:
            values[field_name] = canonical_holding_value(field_name, value)
    return values


def _json_value(value: Any) -> Any:
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, set, frozenset)):
        return [_json_value(item) for item in value]
    return _enum_value(value)


def _digest(payload: Any) -> str:
    encoded = json.dumps(
        _json_value(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


EMPTY_HOLDING_DIGEST = _digest({"holding": None})


def holding_state_digest(holding: Optional[Holding]) -> str:
    if holding is None:
        return EMPTY_HOLDING_DIGEST
    identity = HoldingIdentity.from_holding(holding)
    return _digest({
        "record_id": str(holding.record_id or "").strip() or None,
        "identity": identity.as_dict(),
        "values": holding_values(holding),
        "created_at": holding.created_at,
        "updated_at": holding.updated_at,
    })


def raw_holding_state_digest(
    record_id: Any,
    raw_fields: Mapping[str, Any],
) -> str:
    """Bind a repair mutation to the exact raw row the operator confirmed."""

    resolved_record_id = _required_text(record_id, field_name="record_id")
    if not isinstance(raw_fields, Mapping):
        raise ValueError("raw holding fields must be an object")
    return _digest({
        "record_id": resolved_record_id,
        "raw_fields": dict(raw_fields),
    })


def _validate_registry_selects(values: Mapping[str, Any]) -> None:
    table = get_table_contract("holdings")
    for field_name in ("asset_type", "asset_class", "industry"):
        value = values.get(field_name)
        if value is None:
            continue
        allowed = table.fields_by_name[field_name].select_options
        if value not in allowed:
            raise ValueError(
                f"unsupported select value for {field_name}: {value!r}; "
                f"allowed={list(allowed)!r}"
            )


def canonical_holding(holding: Holding) -> Holding:
    """Build the exact canonical model that a mutation may send/cache/return."""

    identity = HoldingIdentity.from_holding(holding)
    values = holding_values(holding)
    _validate_registry_selects(values)
    return Holding(
        record_id=str(holding.record_id or "").strip() or None,
        **identity.as_dict(),
        asset_name=values["asset_name"],
        asset_type=values["asset_type"],
        quantity=values["quantity"],
        avg_cost=values["avg_cost"],
        currency=values["currency"],
        asset_class=values["asset_class"],
        industry=values["industry"],
        tag=list(values["tag"]),
        created_at=holding.created_at,
        updated_at=holding.updated_at,
    )


@dataclass(frozen=True)
class HoldingPatch:
    """A tri-state patch bound to one fresh base row.

    UNSET means missing/do not modify.  None is an explicit clear and is valid
    only for the three domain-authorized nullable fields.
    """

    identity: HoldingIdentity
    base_record_id: str
    base_digest: str
    asset_name: Any = UNSET
    asset_type: Any = UNSET
    quantity: Any = UNSET
    avg_cost: Any = UNSET
    currency: Any = UNSET
    asset_class: Any = UNSET
    industry: Any = UNSET
    tag: Any = UNSET

    def __post_init__(self) -> None:
        record_id = _required_text(self.base_record_id, field_name="base_record_id")
        digest = _required_text(self.base_digest, field_name="base_digest")
        object.__setattr__(self, "base_record_id", record_id)
        object.__setattr__(self, "base_digest", digest)
        supplied = self.values
        if not supplied:
            raise ValueError("holding patch requires at least one field")
        for field_name, value in supplied.items():
            object.__setattr__(
                self,
                field_name,
                canonical_holding_value(field_name, value),
            )
        _validate_registry_selects(self.values)

    @property
    def values(self) -> dict[str, Any]:
        return {
            item.name: getattr(self, item.name)
            for item in dataclass_fields(self)
            if item.name in HOLDING_VALUE_FIELDS and getattr(self, item.name) is not UNSET
        }

    @property
    def owned_fields(self) -> frozenset[str]:
        return frozenset(self.values)

    @classmethod
    def from_base(cls, base: Holding, **changes: Any) -> "HoldingPatch":
        canonical_base = canonical_holding(base)
        return cls(
            identity=HoldingIdentity.from_holding(canonical_base),
            base_record_id=_required_text(
                canonical_base.record_id,
                field_name="base holding record_id",
            ),
            base_digest=holding_state_digest(canonical_base),
            **changes,
        )


@dataclass(frozen=True)
class HoldingRepairPatch:
    """Restricted patch for a raw row that cannot yet become a typed Holding."""

    identity: HoldingIdentity
    record_id: str
    base_digest: str
    values: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.identity, HoldingIdentity):
            raise TypeError("holding repair patch requires HoldingIdentity")
        record_id = _required_text(self.record_id, field_name="record_id")
        base_digest = _required_text(self.base_digest, field_name="base_digest")
        raw_values = dict(self.values)
        if not raw_values:
            raise ValueError("holding repair patch requires at least one field")
        unsupported = sorted(set(raw_values) - HOLDING_REPAIRABLE_FIELDS)
        if unsupported:
            raise ValueError(
                "unsupported holdings repair fields: "
                + ", ".join(unsupported)
            )
        canonical_values = {
            field_name: canonical_holding_value(field_name, value)
            for field_name, value in raw_values.items()
        }
        _validate_registry_selects(canonical_values)
        object.__setattr__(self, "record_id", record_id)
        object.__setattr__(self, "base_digest", base_digest)
        object.__setattr__(
            self,
            "values",
            MappingProxyType(canonical_values),
        )

    @classmethod
    def from_raw(
        cls,
        raw: RawHoldingRecord,
        values: Mapping[str, Any],
    ) -> "HoldingRepairPatch":
        if not isinstance(raw, RawHoldingRecord):
            raise TypeError("holding repair patch requires RawHoldingRecord")
        identity = HoldingIdentity(
            raw.raw_fields.get("asset_id"),
            raw.raw_fields.get("account"),
            raw.raw_fields.get("broker"),
        )
        return cls(
            identity=identity,
            record_id=raw.record_id,
            base_digest=raw_holding_state_digest(
                raw.record_id,
                raw.raw_fields,
            ),
            values=values,
        )


@dataclass(frozen=True)
class HoldingTarget:
    """A complete absolute target with explicit field ownership and base proof."""

    identity: HoldingIdentity
    values: Mapping[str, Any]
    owned_fields: frozenset[str]
    base_record_id: Optional[str]
    base_digest: str

    def __post_init__(self) -> None:
        raw_values = dict(self.values)
        missing = sorted(HOLDING_VALUE_FIELDS - set(raw_values))
        extra = sorted(set(raw_values) - HOLDING_VALUE_FIELDS)
        if missing or extra:
            raise ValueError(
                "holding target values must be complete; "
                f"missing={missing}, extra={extra}"
            )
        canonical_values: dict[str, Any] = {}
        for field_name, value in raw_values.items():
            if value is None and field_name in HOLDING_NULL_CLEARABLE_FIELDS:
                canonical_values[field_name] = None
            else:
                canonical_values[field_name] = canonical_holding_value(
                    field_name,
                    value,
                )
        _validate_registry_selects(canonical_values)
        owned = frozenset(str(item) for item in self.owned_fields)
        unsupported = sorted(owned - HOLDING_VALUE_FIELDS)
        if unsupported:
            raise ValueError(f"unsupported holding owned_fields: {unsupported}")
        if not owned:
            raise ValueError("holding target owned_fields must not be empty")
        for field_name in owned:
            if (
                canonical_values[field_name] is None
                and field_name not in HOLDING_NULL_CLEARABLE_FIELDS
            ):
                raise ValueError(f"holding field {field_name} cannot be cleared")
        base_record_id = str(self.base_record_id or "").strip() or None
        base_digest = _required_text(self.base_digest, field_name="base_digest")
        if base_record_id is None and base_digest != EMPTY_HOLDING_DIGEST:
            raise ValueError("create target must use the empty holding base digest")
        if base_record_id is None and not HOLDING_REQUIRED_VALUE_FIELDS.issubset(owned):
            missing_owned = sorted(HOLDING_REQUIRED_VALUE_FIELDS - owned)
            raise ValueError(
                "create target must own required values: " + ", ".join(missing_owned)
            )
        if base_record_id is None:
            nonneutral_unowned = sorted(
                field_name
                for field_name in HOLDING_VALUE_FIELDS - owned
                if canonical_values[field_name] is not None
                and not (
                    field_name == "tag"
                    and canonical_values[field_name] == ()
                )
            )
            if nonneutral_unowned:
                raise ValueError(
                    "create target has non-neutral unowned values: "
                    + ", ".join(nonneutral_unowned)
                )
        object.__setattr__(self, "values", MappingProxyType(canonical_values))
        object.__setattr__(self, "owned_fields", owned)
        object.__setattr__(self, "base_record_id", base_record_id)
        object.__setattr__(self, "base_digest", base_digest)

    @classmethod
    def from_holdings(
        cls,
        *,
        base: Optional[Holding],
        target: Holding,
        owned_fields: set[str] | frozenset[str],
    ) -> "HoldingTarget":
        canonical_target = canonical_holding(target)
        identity = HoldingIdentity.from_holding(canonical_target)
        target_values = holding_values(canonical_target)
        if base is None:
            return cls(
                identity=identity,
                values=target_values,
                owned_fields=frozenset(owned_fields),
                base_record_id=None,
                base_digest=EMPTY_HOLDING_DIGEST,
            )
        canonical_base = canonical_holding(base)
        base_identity = HoldingIdentity.from_holding(canonical_base)
        if identity != base_identity:
            raise ValueError(
                f"holding target identity changed: base={base_identity}, target={identity}"
            )
        base_values = holding_values(canonical_base)
        owned = frozenset(owned_fields)
        changed_without_authority = sorted(
            field_name
            for field_name in HOLDING_VALUE_FIELDS - owned
            if target_values[field_name] != base_values[field_name]
        )
        if changed_without_authority:
            raise ValueError(
                "holding target changes unowned fields: "
                + ", ".join(changed_without_authority)
            )
        return cls(
            identity=identity,
            values=target_values,
            owned_fields=owned,
            base_record_id=_required_text(
                canonical_base.record_id,
                field_name="base holding record_id",
            ),
            base_digest=holding_state_digest(canonical_base),
        )

    def to_holding(
        self,
        *,
        record_id: Optional[str] = None,
        created_at: Optional[datetime] = None,
        updated_at: Optional[datetime] = None,
    ) -> Holding:
        return Holding(
            record_id=str(record_id or self.base_record_id or "").strip() or None,
            **self.identity.as_dict(),
            asset_name=self.values["asset_name"],
            asset_type=self.values["asset_type"],
            quantity=self.values["quantity"],
            avg_cost=self.values["avg_cost"],
            currency=self.values["currency"],
            asset_class=self.values["asset_class"],
            industry=self.values["industry"],
            tag=list(self.values["tag"]),
            created_at=created_at,
            updated_at=updated_at,
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "identity": self.identity.as_dict(),
            "values": _json_value(self.values),
            "owned_fields": sorted(self.owned_fields),
            "base_record_id": self.base_record_id,
            "base_digest": self.base_digest,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "HoldingTarget":
        identity = payload.get("identity")
        if not isinstance(identity, Mapping):
            raise ValueError("holding target payload identity must be an object")
        values = payload.get("values")
        if not isinstance(values, Mapping):
            raise ValueError("holding target payload values must be an object")
        owned_fields = payload.get("owned_fields")
        if not isinstance(owned_fields, list):
            raise ValueError("holding target payload owned_fields must be a list")
        return cls(
            identity=HoldingIdentity(
                identity.get("asset_id"),
                identity.get("account"),
                identity.get("broker"),
            ),
            values=values,
            owned_fields=frozenset(str(item) for item in owned_fields),
            base_record_id=payload.get("base_record_id"),
            base_digest=payload.get("base_digest"),
        )


def explicit_holding_owned_fields(holding: Holding) -> frozenset[str]:
    """Project only caller-supplied Pydantic fields, never model defaults."""

    return frozenset(holding.model_fields_set) & HOLDING_VALUE_FIELDS


def holding_field_value(holding: Holding, field_name: str) -> Any:
    return holding_values(holding)[field_name]


def holding_owned_fields_match(
    holding: Holding,
    target: HoldingTarget,
) -> bool:
    try:
        canonical = canonical_holding(holding)
    except (TypeError, ValueError):
        return False
    if HoldingIdentity.from_holding(canonical) != target.identity:
        return False
    actual = holding_values(canonical)
    return all(actual[field_name] == target.values[field_name] for field_name in target.owned_fields)


__all__ = [
    "AmbiguousHoldingIdentityError",
    "EMPTY_HOLDING_DIGEST",
    "HOLDING_NULL_CLEARABLE_FIELDS",
    "HOLDING_REPAIRABLE_FIELDS",
    "HOLDING_REQUIRED_VALUE_FIELDS",
    "HOLDING_VALUE_FIELDS",
    "HoldingIdentity",
    "HoldingMutationConflictError",
    "HoldingMutationProofError",
    "HoldingPatch",
    "HoldingRepairPatch",
    "HoldingTarget",
    "UNSET",
    "canonical_holding",
    "canonical_holding_value",
    "explicit_holding_owned_fields",
    "holding_field_value",
    "holding_owned_fields_match",
    "raw_holding_state_digest",
    "holding_state_digest",
    "holding_values",
]
