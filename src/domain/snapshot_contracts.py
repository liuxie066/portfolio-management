"""Immutable valuation and holdings-snapshot contracts.

The normalized valuation is the financial source of truth for one valuation
run.  ``PortfolioValuation`` remains a compatibility view only; persisted
holding rows and official NAV totals are projected from this object.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
from typing import Any, Iterable, Mapping, Optional

from src.snapshot_models import HoldingSnapshot


NORMALIZED_VALUATION_VERSION = "pm.normalized_valuation.v2"
SNAPSHOT_DIGEST_VERSION = "pm.holdings_snapshot.v2"
SNAPSHOT_EXACT_SET_VERSION = "pm.holdings_snapshot.exact_set.v1"
SNAPSHOT_WRITE_AUTHORITY_VERSION = "pm.holdings_snapshot.write_authority.v1"
SNAPSHOT_BUSINESS_KEY_FIELDS = ("as_of", "account", "asset_id", "broker")
QUANTITY_QUANT = Decimal("0.00000001")
MONEY_QUANT = Decimal("0.01")
NAV_QUANT = Decimal("0.000001")
WEIGHT_QUANT = Decimal("0.000001")
_OFFICIAL_ISSUER = object()


def finite_decimal(value: Any, *, field: str) -> Decimal:
    """Parse one finite Decimal without applying money precision."""

    if value is None:
        raise ValueError(f"{field} is required")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite number") from exc
    if not result.is_finite():
        raise ValueError(f"{field} must be a finite number")
    return result


def normalize_quantity(value: Any, *, field: str = "quantity") -> Decimal:
    """Normalize quantity exactly once at the persisted quantity precision."""

    return finite_decimal(value, field=field).quantize(
        QUANTITY_QUANT,
        rounding=ROUND_HALF_UP,
    )


def quantize_money(value: Any) -> Decimal:
    return finite_decimal(value, field="market_value_cny").quantize(
        MONEY_QUANT,
        rounding=ROUND_HALF_UP,
    )


def canonical_decimal(value: Optional[Decimal]) -> Optional[str]:
    if value is None:
        return None
    if value == 0:
        return "0"
    if value == value.to_integral():
        return str(value.quantize(Decimal("1")))
    return format(value.normalize(), "f")


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def digest_payload(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _optional_snapshot_number(value: Any, *, field: str) -> Optional[str]:
    if value is None:
        return None
    return canonical_decimal(finite_decimal(value, field=field))


def snapshot_row_payload(snapshot: HoldingSnapshot) -> dict[str, Any]:
    """Canonical full persisted row used by all v2 snapshot digests."""

    if not isinstance(snapshot, HoldingSnapshot):
        raise TypeError("snapshot_row_payload requires HoldingSnapshot")
    payload = snapshot.model_dump(mode="python", exclude={"record_id"})
    for field_name in (
        "quantity",
        "price",
        "cny_price",
        "market_value_cny",
        "avg_cost",
    ):
        payload[field_name] = _optional_snapshot_number(
            payload[field_name],
            field=field_name,
        )
    return payload


def snapshot_digest(snapshots: Iterable[Any]) -> str:
    """Compute the stable v2 digest over every persisted row field."""

    items = [snapshot_row_payload(snapshot) for snapshot in snapshots]
    items.sort(
        key=lambda item: tuple(
            str(item[field_name]) for field_name in SNAPSHOT_BUSINESS_KEY_FIELDS
        )
    )
    return digest_payload({"version": SNAPSHOT_DIGEST_VERSION, "rows": items})


def snapshot_business_key(snapshot: Any) -> tuple[str, str, str, str]:
    """Return the canonical persisted snapshot business key."""

    return tuple(
        str(getattr(snapshot, field_name))
        for field_name in SNAPSHOT_BUSINESS_KEY_FIELDS
    )


def snapshot_dedup_key(
    *,
    account: Any,
    as_of: Any,
    broker: Any,
    asset_id: Any,
) -> str:
    """Return the stable persisted dedup token for one snapshot business key."""

    values = {
        "account": str(account or "").strip(),
        "as_of": str(as_of or "").strip(),
        "broker": str(broker or "").strip(),
        "asset_id": str(asset_id or "").strip(),
    }
    missing = sorted(name for name, value in values.items() if not value)
    if missing:
        raise ValueError(
            "snapshot dedup key requires nonblank fields: " + ", ".join(missing)
        )
    # Preserve the deployed opaque token format while owning the formula once.
    return ":".join(
        (values["account"], values["as_of"], values["broker"], values["asset_id"])
    )


def _assert_snapshot_scope(snapshot: Any, *, account: str, as_of: str) -> None:
    if snapshot.account != account or snapshot.as_of != as_of:
        raise ValueError(
            "holdings snapshot row scope mismatch: "
            f"expected={account}/{as_of} actual={snapshot.account}/{snapshot.as_of}"
        )
    expected_dedup_key = snapshot_dedup_key(
        account=account,
        as_of=as_of,
        broker=snapshot.broker,
        asset_id=snapshot.asset_id,
    )
    if snapshot.dedup_key != expected_dedup_key:
        raise ValueError(
            "holdings snapshot dedup_key mismatch: "
            f"expected={expected_dedup_key} actual={snapshot.dedup_key}"
        )


def _snapshot_with_record_payload(snapshot: Any) -> dict[str, Any]:
    return {
        "record_id": str(snapshot.record_id or "") or None,
        **snapshot_row_payload(snapshot),
    }


def _snapshot_from_payload(payload: Mapping[str, Any]) -> Any:
    return HoldingSnapshot(**dict(payload))


class SnapshotSetConflictError(ValueError):
    """The remote slice is neither the bound before set nor a safe partial target."""


@dataclass(frozen=True)
class SnapshotSetActions:
    """Residual mutations required to reach one exact snapshot target set."""

    creates: tuple[Any, ...]
    updates: tuple[tuple[str, Any], ...]
    deletes: tuple[str, ...]
    unchanged: int = 0

    @property
    def mutation_count(self) -> int:
        return len(self.creates) + len(self.updates) + len(self.deletes)

    def summary(self) -> dict[str, int]:
        return {
            "create": len(self.creates),
            "update": len(self.updates),
            "delete": len(self.deletes),
            "unchanged": self.unchanged,
        }


@dataclass(frozen=True)
class SnapshotExactSetPlan:
    """Immutable before/target snapshot set bound to one account and date."""

    account: str
    as_of: str
    target_digest: str
    before: tuple[Any, ...]
    desired: tuple[Any, ...]
    contract_version: str = SNAPSHOT_EXACT_SET_VERSION

    def __post_init__(self) -> None:
        _nonblank(self.account, field="snapshot_plan.account")
        try:
            date.fromisoformat(self.as_of)
        except (TypeError, ValueError) as exc:
            raise ValueError("snapshot_plan.as_of must be YYYY-MM-DD") from exc
        if self.contract_version != SNAPSHOT_EXACT_SET_VERSION:
            raise ValueError("unsupported snapshot exact-set contract version")
        if len(str(self.target_digest)) != 64:
            raise ValueError("snapshot plan target_digest must be a sha256 digest")
        if not isinstance(self.before, tuple) or not isinstance(self.desired, tuple):
            raise TypeError("snapshot plan before/desired sets must be immutable tuples")
        self._validate_rows(self.before, require_record_id=True, label="before")
        self._validate_rows(self.desired, require_record_id=False, label="desired")

    def _validate_rows(
        self,
        rows: tuple[Any, ...],
        *,
        require_record_id: bool,
        label: str,
    ) -> None:
        keys: list[tuple[str, str, str, str]] = []
        record_ids: list[str] = []
        for row in rows:
            _assert_snapshot_scope(row, account=self.account, as_of=self.as_of)
            keys.append(snapshot_business_key(row))
            record_id = str(getattr(row, "record_id", None) or "")
            if require_record_id and not record_id:
                raise ValueError(f"snapshot plan {label} row requires record_id")
            if record_id:
                record_ids.append(record_id)
        if len(keys) != len(set(keys)):
            raise SnapshotSetConflictError(
                f"snapshot plan {label} set contains duplicate business keys"
            )
        if len(record_ids) != len(set(record_ids)):
            raise SnapshotSetConflictError(
                f"snapshot plan {label} set contains duplicate record_ids"
            )

    @classmethod
    def build(
        cls,
        *,
        account: str,
        as_of: str,
        target_digest: str,
        before: Iterable[Any],
        desired: Iterable[Any],
    ) -> "SnapshotExactSetPlan":
        def sort_key(row: Any) -> tuple[str, str, str, str, str]:
            return snapshot_business_key(row) + (str(row.record_id or ""),)

        return cls(
            account=str(account),
            as_of=str(as_of),
            target_digest=str(target_digest),
            before=tuple(sorted(before, key=sort_key)),
            desired=tuple(sorted(desired, key=sort_key)),
        )

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "SnapshotExactSetPlan":
        if not isinstance(payload, Mapping):
            raise TypeError("snapshot exact-set plan payload must be an object")
        plan = cls.build(
            account=str(payload.get("account") or ""),
            as_of=str(payload.get("as_of") or ""),
            target_digest=str(payload.get("target_digest") or ""),
            before=(
                _snapshot_from_payload(row)
                for row in (payload.get("before") or [])
            ),
            desired=(
                _snapshot_from_payload(row)
                for row in (payload.get("desired") or [])
            ),
        )
        if payload.get("contract_version") != plan.contract_version:
            raise ValueError("snapshot exact-set plan contract_version mismatch")
        if payload.get("plan_digest") != plan.plan_digest:
            raise ValueError("snapshot exact-set plan digest mismatch")
        return plan

    @property
    def row_digest(self) -> str:
        return snapshot_digest(self.desired)

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "account": self.account,
            "as_of": self.as_of,
            "target_digest": self.target_digest,
            "row_digest": self.row_digest,
            "before": [_snapshot_with_record_payload(row) for row in self.before],
            "desired": [snapshot_row_payload(row) for row in self.desired],
        }

    @property
    def plan_digest(self) -> str:
        return digest_payload(self.canonical_payload())

    def to_payload(self) -> dict[str, Any]:
        return {**self.canonical_payload(), "plan_digest": self.plan_digest}

    @staticmethod
    def _rows_by_key(rows: Iterable[Any]) -> dict[tuple[str, str, str, str], Any]:
        result: dict[tuple[str, str, str, str], Any] = {}
        for row in rows:
            key = snapshot_business_key(row)
            if key in result:
                raise SnapshotSetConflictError(
                    f"duplicate remote holdings_snapshot business key: {key}"
                )
            result[key] = row
        return result

    def residual_actions(self, current: Iterable[Any]) -> SnapshotSetActions:
        """Validate a safe replay state and return the remaining deterministic actions."""

        current_rows = tuple(current)
        self._validate_rows(current_rows, require_record_id=True, label="current")
        before_by_key = self._rows_by_key(self.before)
        desired_by_key = self._rows_by_key(self.desired)
        current_by_key = self._rows_by_key(current_rows)
        allowed_keys = set(before_by_key) | set(desired_by_key)
        unknown_keys = sorted(set(current_by_key) - allowed_keys)
        if unknown_keys:
            raise SnapshotSetConflictError(
                f"snapshot remote set contains unbound keys: {unknown_keys}"
            )

        creates: list[Any] = []
        updates: list[tuple[str, Any]] = []
        deletes: list[str] = []
        unchanged = 0
        for key in sorted(desired_by_key):
            desired = desired_by_key[key]
            current_row = current_by_key.get(key)
            before_row = before_by_key.get(key)
            if current_row is None:
                if before_row is not None:
                    raise SnapshotSetConflictError(
                        f"bound snapshot row disappeared before target completion: {key}"
                    )
                creates.append(desired)
                continue
            current_payload = snapshot_row_payload(current_row)
            desired_payload = snapshot_row_payload(desired)
            if before_row is not None:
                if current_row.record_id != before_row.record_id:
                    raise SnapshotSetConflictError(
                        f"bound snapshot record_id changed: {key}"
                    )
                before_payload = snapshot_row_payload(before_row)
                if current_payload not in (before_payload, desired_payload):
                    raise SnapshotSetConflictError(
                        f"bound snapshot row matches neither before nor target: {key}"
                    )
            elif current_payload != desired_payload:
                raise SnapshotSetConflictError(
                    f"new snapshot row does not match the bound target: {key}"
                )
            if current_payload == desired_payload:
                unchanged += 1
            else:
                updates.append((str(current_row.record_id), desired))

        for key in sorted(set(before_by_key) - set(desired_by_key)):
            before_row = before_by_key[key]
            current_row = current_by_key.get(key)
            if current_row is None:
                unchanged += 1
                continue
            if (
                current_row.record_id != before_row.record_id
                or snapshot_row_payload(current_row) != snapshot_row_payload(before_row)
            ):
                raise SnapshotSetConflictError(
                    f"obsolete snapshot row changed before deletion: {key}"
                )
            deletes.append(str(current_row.record_id))

        return SnapshotSetActions(
            creates=tuple(creates),
            updates=tuple(updates),
            deletes=tuple(deletes),
            unchanged=unchanged,
        )


@dataclass(frozen=True)
class BoundSnapshotWriteAuthority:
    """A confirmed write capability cryptographically bound to one exact-set plan."""

    account: str
    as_of: str
    run_id: str
    issuer: str
    overwrite_existing: bool
    confirmed: bool
    target_digest: str
    plan_digest: str
    authority_digest: str
    contract_version: str = SNAPSHOT_WRITE_AUTHORITY_VERSION

    def __post_init__(self) -> None:
        for name in ("account", "as_of", "run_id", "issuer"):
            _nonblank(getattr(self, name), field=f"snapshot_authority.{name}")
        if len(self.target_digest) != 64 or len(self.plan_digest) != 64:
            raise ValueError("snapshot authority digests must be sha256 digests")
        if self.contract_version != SNAPSHOT_WRITE_AUTHORITY_VERSION:
            raise ValueError("unsupported snapshot write authority version")
        expected_authority_digest = digest_payload({
            "contract_version": self.contract_version,
            "account": self.account,
            "as_of": self.as_of,
            "run_id": self.run_id,
            "issuer": self.issuer,
            "overwrite_existing": self.overwrite_existing,
            "confirmed": self.confirmed,
            "target_digest": self.target_digest,
        })
        if self.authority_digest != expected_authority_digest:
            raise ValueError("snapshot authority digest mismatch")

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "account": self.account,
            "as_of": self.as_of,
            "run_id": self.run_id,
            "issuer": self.issuer,
            "overwrite_existing": self.overwrite_existing,
            "confirmed": self.confirmed,
            "target_digest": self.target_digest,
            "plan_digest": self.plan_digest,
            "authority_digest": self.authority_digest,
        }

    @property
    def digest(self) -> str:
        return digest_payload(self.canonical_payload())

    def to_payload(self) -> dict[str, Any]:
        return {**self.canonical_payload(), "bound_digest": self.digest}

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "BoundSnapshotWriteAuthority":
        if not isinstance(payload, Mapping):
            raise TypeError("bound snapshot authority payload must be an object")
        if not isinstance(payload.get("overwrite_existing"), bool):
            raise ValueError("snapshot authority overwrite_existing must be boolean")
        if not isinstance(payload.get("confirmed"), bool):
            raise ValueError("snapshot authority confirmed must be boolean")
        bound = cls(
            account=str(payload.get("account") or ""),
            as_of=str(payload.get("as_of") or ""),
            run_id=str(payload.get("run_id") or ""),
            issuer=str(payload.get("issuer") or ""),
            overwrite_existing=payload["overwrite_existing"],
            confirmed=payload["confirmed"],
            target_digest=str(payload.get("target_digest") or ""),
            plan_digest=str(payload.get("plan_digest") or ""),
            authority_digest=str(payload.get("authority_digest") or ""),
            contract_version=str(payload.get("contract_version") or ""),
        )
        if payload.get("bound_digest") != bound.digest:
            raise ValueError("bound snapshot authority digest mismatch")
        return bound

    def assert_matches(self, plan: SnapshotExactSetPlan) -> None:
        if (
            self.account != plan.account
            or self.as_of != plan.as_of
            or self.target_digest != plan.target_digest
            or self.plan_digest != plan.plan_digest
        ):
            raise SnapshotSetConflictError(
                "snapshot authority scope/digest does not match the prepared plan"
            )


@dataclass(frozen=True)
class SnapshotWriteAuthority:
    """Top-level caller intent before it is bound to a fresh exact-set plan."""

    account: str
    as_of: str
    run_id: str
    issuer: str
    overwrite_existing: bool
    confirmed: bool
    target_digest: str
    contract_version: str = SNAPSHOT_WRITE_AUTHORITY_VERSION

    def __post_init__(self) -> None:
        for name in ("account", "as_of", "run_id", "issuer"):
            _nonblank(getattr(self, name), field=f"snapshot_authority.{name}")
        try:
            date.fromisoformat(self.as_of)
        except (TypeError, ValueError) as exc:
            raise ValueError("snapshot_authority.as_of must be YYYY-MM-DD") from exc
        if not isinstance(self.overwrite_existing, bool) or not isinstance(
            self.confirmed, bool
        ):
            raise TypeError("snapshot authority flags must be boolean")
        if len(str(self.target_digest)) != 64:
            raise ValueError("snapshot authority target_digest must be a sha256 digest")
        if self.contract_version != SNAPSHOT_WRITE_AUTHORITY_VERSION:
            raise ValueError("unsupported snapshot write authority version")

    @property
    def digest(self) -> str:
        return digest_payload({
            "contract_version": self.contract_version,
            "account": self.account,
            "as_of": self.as_of,
            "run_id": self.run_id,
            "issuer": self.issuer,
            "overwrite_existing": self.overwrite_existing,
            "confirmed": self.confirmed,
            "target_digest": self.target_digest,
        })

    def bind(
        self,
        plan: SnapshotExactSetPlan,
        *,
        require_confirm: bool,
    ) -> BoundSnapshotWriteAuthority:
        if (
            self.account != plan.account
            or self.as_of != plan.as_of
            or self.target_digest != plan.target_digest
        ):
            raise SnapshotSetConflictError(
                "snapshot write authority scope/digest mismatch"
            )
        if require_confirm and not self.confirmed:
            raise PermissionError("snapshot write requires confirmed authority")
        if plan.before and not self.overwrite_existing:
            raise PermissionError(
                "holdings_snapshot slice already exists; overwrite_existing=True required"
            )
        return BoundSnapshotWriteAuthority(
            account=self.account,
            as_of=self.as_of,
            run_id=self.run_id,
            issuer=self.issuer,
            overwrite_existing=self.overwrite_existing,
            confirmed=self.confirmed,
            target_digest=self.target_digest,
            plan_digest=plan.plan_digest,
            authority_digest=self.digest,
        )


def _enum_value(value: Any) -> Optional[str]:
    if value is None:
        return None
    return str(getattr(value, "value", value))


def _nonblank(value: Any, *, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field} must be nonblank")
    return result


def _freeze_json(value: Any) -> str:
    return canonical_json(value if value is not None else {})


def _thaw_json(value: str) -> Any:
    return json.loads(value)


@dataclass(frozen=True)
class NormalizedValuationRow:
    """One immutable, normalized holding row used by NAV and persistence."""

    record_id: Optional[str]
    account: str
    asset_id: str
    asset_name: str
    asset_type: str
    normalized_type: str
    broker: str
    quantity: Decimal
    avg_cost: Optional[Decimal]
    currency: str
    asset_class: Optional[str]
    industry: Optional[str]
    tags: tuple[str, ...]
    price: Optional[Decimal]
    cny_price: Optional[Decimal]
    market_value_cny: Optional[Decimal]
    source: str

    def __post_init__(self) -> None:
        _nonblank(self.account, field="account")
        _nonblank(self.asset_id, field="asset_id")
        _nonblank(self.asset_type, field="asset_type")
        _nonblank(self.normalized_type, field="normalized_type")
        _nonblank(self.currency, field="currency")
        if not isinstance(self.tags, tuple):
            raise TypeError("normalized valuation row tags must be an immutable tuple")
        quantity = normalize_quantity(self.quantity)
        if quantity != self.quantity:
            raise ValueError("normalized valuation row quantity is not canonical")
        if quantity == 0:
            raise ValueError("zero quantity rows must be excluded before normalization")
        price_fields = (self.price, self.cny_price, self.market_value_cny)
        if any(value is None for value in price_fields):
            if not all(value is None for value in price_fields):
                raise ValueError(
                    "price, cny_price, and market_value_cny must be all present or all absent"
                )
            return
        price = finite_decimal(self.price, field="price")
        cny_price = finite_decimal(self.cny_price, field="cny_price")
        market_value = finite_decimal(
            self.market_value_cny,
            field="market_value_cny",
        )
        if price <= 0 or cny_price <= 0:
            raise ValueError("price and cny_price must be positive finite numbers")
        expected = (quantity * cny_price).quantize(
            MONEY_QUANT,
            rounding=ROUND_HALF_UP,
        )
        if market_value != expected:
            raise ValueError(
                "normalized valuation row replay mismatch: "
                f"{quantity} * {cny_price} -> {expected}, got {market_value}"
            )

    @classmethod
    def from_holding(
        cls,
        holding: Any,
        *,
        account: str,
        normalized_type: str,
        price: Any = None,
        cny_price: Any = None,
        source: str = "record_nav",
    ) -> "NormalizedValuationRow":
        row_account = _nonblank(
            getattr(holding, "account", None) or account,
            field="account",
        )
        if row_account != account:
            raise ValueError(
                f"normalized holding account mismatch: {row_account} != {account}"
            )
        quantity = normalize_quantity(getattr(holding, "quantity", None))
        if quantity == 0:
            raise ValueError("zero quantity rows must be excluded before normalization")

        price_decimal: Optional[Decimal]
        cny_price_decimal: Optional[Decimal]
        market_value: Optional[Decimal]
        if price is None or cny_price is None:
            if price is not None or cny_price is not None:
                raise ValueError("price and cny_price must be supplied together")
            price_decimal = None
            cny_price_decimal = None
            market_value = None
        else:
            price_decimal = finite_decimal(price, field="price")
            cny_price_decimal = finite_decimal(cny_price, field="cny_price")
            market_value = (quantity * cny_price_decimal).quantize(
                MONEY_QUANT,
                rounding=ROUND_HALF_UP,
            )

        avg_cost_raw = getattr(holding, "avg_cost", None)
        avg_cost = (
            finite_decimal(avg_cost_raw, field="avg_cost")
            if avg_cost_raw is not None
            else None
        )
        return cls(
            record_id=(
                str(getattr(holding, "record_id", "") or "").strip() or None
            ),
            account=row_account,
            asset_id=_nonblank(getattr(holding, "asset_id", None), field="asset_id"),
            asset_name=str(getattr(holding, "asset_name", "") or ""),
            asset_type=_nonblank(
                _enum_value(getattr(holding, "asset_type", None)),
                field="asset_type",
            ),
            normalized_type=_nonblank(normalized_type, field="normalized_type"),
            broker=str(getattr(holding, "broker", "") or "").strip(),
            quantity=quantity,
            avg_cost=avg_cost,
            currency=_nonblank(
                getattr(holding, "currency", None),
                field="currency",
            ).upper(),
            asset_class=_enum_value(getattr(holding, "asset_class", None)),
            industry=_enum_value(getattr(holding, "industry", None)),
            tags=tuple(str(item) for item in (getattr(holding, "tag", None) or [])),
            price=price_decimal,
            cny_price=cny_price_decimal,
            market_value_cny=market_value,
            source=_nonblank(source, field="source"),
        )

    @property
    def business_key(self) -> tuple[str, str, str]:
        return (self.account, self.broker, self.asset_id)

    @property
    def is_priced(self) -> bool:
        return self.price is not None

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "account": self.account,
            "asset_id": self.asset_id,
            "asset_name": self.asset_name,
            "asset_type": self.asset_type,
            "normalized_type": self.normalized_type,
            "broker": self.broker,
            "quantity": canonical_decimal(self.quantity),
            "avg_cost": canonical_decimal(self.avg_cost),
            "currency": self.currency,
            "asset_class": self.asset_class,
            "industry": self.industry,
            "tags": list(self.tags),
            "price": canonical_decimal(self.price),
            "cny_price": canonical_decimal(self.cny_price),
            "market_value_cny": canonical_decimal(self.market_value_cny),
            "source": self.source,
        }

    def to_holding(self, *, weight: Optional[Decimal]) -> Any:
        from src.models import Holding

        return Holding(
            record_id=self.record_id,
            asset_id=self.asset_id,
            asset_name=self.asset_name,
            asset_type=self.asset_type,
            account=self.account,
            broker=self.broker,
            quantity=float(self.quantity),
            avg_cost=float(self.avg_cost) if self.avg_cost is not None else None,
            currency=self.currency,
            asset_class=self.asset_class,
            industry=self.industry,
            tag=list(self.tags),
            current_price=float(self.price) if self.price is not None else None,
            cny_price=float(self.cny_price) if self.cny_price is not None else None,
            market_value_cny=(
                float(self.market_value_cny)
                if self.market_value_cny is not None
                else None
            ),
            weight=float(weight) if weight is not None else None,
        )

    def to_snapshot_row(self, *, as_of: str) -> Any:
        if not self.broker:
            raise ValueError(
                f"holding snapshot broker must be nonblank: {self.asset_id}"
            )
        if not self.is_priced:
            raise ValueError(
                f"holding snapshot price fields are required: {self.asset_id}"
            )
        dedup_key = snapshot_dedup_key(
            account=self.account,
            as_of=as_of,
            broker=self.broker,
            asset_id=self.asset_id,
        )
        return HoldingSnapshot(
            as_of=as_of,
            account=self.account,
            asset_id=self.asset_id,
            broker=self.broker,
            quantity=float(self.quantity),
            currency=self.currency,
            price=float(self.price),
            cny_price=float(self.cny_price),
            market_value_cny=float(self.market_value_cny),
            dedup_key=dedup_key,
            asset_name=self.asset_name or None,
            avg_cost=float(self.avg_cost) if self.avg_cost is not None else None,
            source="record_nav",
        )


@dataclass(frozen=True)
class ValuationComponent:
    """A declared value not represented by a persisted holding row."""

    name: str
    category: str
    value_cny: Decimal
    source: str
    provenance_json: str

    def __post_init__(self) -> None:
        _nonblank(self.name, field="component.name")
        if self.category not in {"cash", "equity", "fund"}:
            raise ValueError(f"unsupported valuation component category: {self.category}")
        value = finite_decimal(self.value_cny, field=f"component.{self.name}")
        if value != value.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP):
            raise ValueError(f"component {self.name} is not at money precision")
        _nonblank(self.source, field="component.source")
        _thaw_json(self.provenance_json)

    @classmethod
    def build(
        cls,
        *,
        name: str,
        category: str,
        value_cny: Any,
        source: str,
        provenance: Mapping[str, Any],
    ) -> "ValuationComponent":
        return cls(
            name=name,
            category=category,
            value_cny=finite_decimal(
                value_cny,
                field=f"component.{name}",
            ).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP),
            source=source,
            provenance_json=_freeze_json(dict(provenance)),
        )

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "category": self.category,
            "value_cny": canonical_decimal(self.value_cny),
            "source": self.source,
            "provenance": _thaw_json(self.provenance_json),
        }


@dataclass(frozen=True)
class NormalizedValuationSnapshot:
    """Complete immutable valuation transmission for one account and run."""

    account: str
    rows: tuple[NormalizedValuationRow, ...]
    components: tuple[ValuationComponent, ...]
    shares: Optional[Decimal]
    nav_override: Optional[Decimal]
    price_evidence_json: str
    holdings_provenance_json: str
    warnings: tuple[str, ...]
    excluded_zero_count: int
    excluded_zero_key_digest: str
    source: str
    source_provenance_json: str
    contract_version: str = NORMALIZED_VALUATION_VERSION
    _official_issuer: object = field(
        default=None,
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        _nonblank(self.account, field="account")
        if self.contract_version != NORMALIZED_VALUATION_VERSION:
            raise ValueError("unsupported normalized valuation contract version")
        if not isinstance(self.rows, tuple) or not isinstance(self.components, tuple):
            raise TypeError("normalized valuation rows/components must be immutable tuples")
        if not isinstance(self.warnings, tuple):
            raise TypeError("normalized valuation warnings must be an immutable tuple")
        if self.excluded_zero_count < 0:
            raise ValueError("excluded_zero_count must be nonnegative")
        if len(str(self.excluded_zero_key_digest)) != 64:
            raise ValueError("excluded_zero_key_digest must be a sha256 digest")
        _nonblank(self.source, field="source")
        _thaw_json(self.price_evidence_json)
        _thaw_json(self.holdings_provenance_json)
        _thaw_json(self.source_provenance_json)
        if any(row.account != self.account for row in self.rows):
            raise ValueError("normalized valuation row account mismatch")
        keys = [row.business_key for row in self.rows]
        if len(keys) != len(set(keys)):
            raise ValueError("normalized valuation contains duplicate holding keys")
        component_names = [component.name for component in self.components]
        if len(component_names) != len(set(component_names)):
            raise ValueError("normalized valuation contains duplicate component names")
        if self.shares is not None:
            shares = finite_decimal(self.shares, field="shares")
            if shares < 0:
                raise ValueError("shares must be nonnegative")
        if self.nav_override is not None:
            finite_decimal(self.nav_override, field="nav_override")

    @classmethod
    def build(
        cls,
        *,
        account: str,
        rows: Iterable[NormalizedValuationRow],
        shares: Any,
        price_evidence: Optional[Mapping[str, Any]] = None,
        holdings_provenance: Optional[Mapping[str, Any]] = None,
        warnings: Iterable[str] = (),
        excluded_zero_keys: Iterable[str] = (),
        source: str = "normalized_builder",
        source_provenance: Optional[Mapping[str, Any]] = None,
        components: Iterable[ValuationComponent] = (),
        nav_override: Any = None,
    ) -> "NormalizedValuationSnapshot":
        """Build a nonofficial normalized object for reporting and tests."""

        return cls._assemble(
            account=account,
            rows=rows,
            shares=shares,
            price_evidence=price_evidence,
            holdings_provenance=holdings_provenance,
            warnings=warnings,
            excluded_zero_keys=excluded_zero_keys,
            source=source,
            source_provenance=source_provenance,
            components=components,
            nav_override=nav_override,
            issue_official=False,
        )

    @classmethod
    def _from_valuation_service(
        cls,
        *,
        account: str,
        rows: Iterable[NormalizedValuationRow],
        shares: Any,
        price_evidence: Optional[Mapping[str, Any]] = None,
        holdings_provenance: Optional[Mapping[str, Any]] = None,
        warnings: Iterable[str] = (),
        excluded_zero_keys: Iterable[str] = (),
        source_provenance: Optional[Mapping[str, Any]] = None,
    ) -> "NormalizedValuationSnapshot":
        """Issue the official normal-valuation capability to ValuationService."""

        return cls._assemble(
            account=account,
            rows=rows,
            shares=shares,
            price_evidence=price_evidence,
            holdings_provenance=holdings_provenance,
            warnings=warnings,
            excluded_zero_keys=excluded_zero_keys,
            source="valuation_service",
            source_provenance=source_provenance,
            components=(),
            nav_override=None,
            issue_official=True,
        )

    @classmethod
    def _assemble(
        cls,
        *,
        account: str,
        rows: Iterable[NormalizedValuationRow],
        shares: Any,
        price_evidence: Optional[Mapping[str, Any]],
        holdings_provenance: Optional[Mapping[str, Any]],
        warnings: Iterable[str],
        excluded_zero_keys: Iterable[str],
        source: str,
        source_provenance: Optional[Mapping[str, Any]],
        components: Iterable[ValuationComponent],
        nav_override: Any,
        issue_official: bool,
    ) -> "NormalizedValuationSnapshot":
        excluded_keys = tuple(sorted(str(key) for key in excluded_zero_keys))
        normalized_shares = (
            finite_decimal(shares, field="shares").quantize(
                MONEY_QUANT,
                rounding=ROUND_HALF_UP,
            )
            if shares is not None
            else None
        )
        normalized_nav_override = (
            finite_decimal(nav_override, field="nav_override").quantize(
                NAV_QUANT,
                rounding=ROUND_HALF_UP,
            )
            if nav_override is not None
            else None
        )
        sorted_rows = tuple(
            sorted(
                rows,
                key=lambda row: (
                    row.account,
                    row.broker,
                    row.asset_id,
                    row.record_id or "",
                ),
            )
        )
        sorted_components = tuple(sorted(components, key=lambda item: item.name))
        snapshot = cls(
            account=_nonblank(account, field="account"),
            rows=sorted_rows,
            components=sorted_components,
            shares=normalized_shares,
            nav_override=normalized_nav_override,
            price_evidence_json=_freeze_json(dict(price_evidence or {})),
            holdings_provenance_json=_freeze_json(dict(holdings_provenance or {})),
            warnings=tuple(
                dict.fromkeys(str(item) for item in warnings if str(item).strip())
            ),
            excluded_zero_count=len(excluded_keys),
            excluded_zero_key_digest=digest_payload(list(excluded_keys)),
            source=_nonblank(source, field="source"),
            source_provenance_json=_freeze_json(dict(source_provenance or {})),
        )
        if issue_official:
            object.__setattr__(snapshot, "_official_issuer", _OFFICIAL_ISSUER)
        return snapshot

    @classmethod
    def from_closed_input(
        cls,
        target: Any,
        *,
        account: str,
        source_provenance: Optional[Mapping[str, Any]] = None,
    ) -> "NormalizedValuationSnapshot":
        """Build the sole CLOSED valuation from the validated input target."""

        from src.domain.nav_calculator import ClosedNavTarget

        if not isinstance(target, ClosedNavTarget):
            raise TypeError("CLOSED valuation requires a ClosedNavTarget")
        total = finite_decimal(target.total_value, field="closed.total_value")
        cash = finite_decimal(target.cash_value, field="closed.cash_value")
        non_cash = finite_decimal(
            target.non_cash_value,
            field="closed.non_cash_value",
        )
        if total != cash + non_cash:
            raise ValueError("CLOSED normalized component decomposition mismatch")
        provenance = {
            "mode": "closed",
            "authority": "explicit_user_input",
            **dict(source_provenance or {}),
        }
        components = (
            ValuationComponent.build(
                name="manual_cash_value",
                category="cash",
                value_cny=cash,
                source="user_input",
                provenance={"input_field": "cash_value"},
            ),
            ValuationComponent.build(
                name="manual_non_cash_value",
                category="equity",
                value_cny=non_cash,
                source="user_input",
                provenance={"input_field": "stock_value"},
            ),
        )
        return cls._assemble(
            account=account,
            rows=(),
            components=components,
            shares=target.shares,
            nav_override=target.nav,
            price_evidence=None,
            holdings_provenance=None,
            warnings=(),
            excluded_zero_keys=(),
            source="closed_input",
            source_provenance=provenance,
            issue_official=True,
        )

    def with_runtime_context(
        self,
        *,
        holdings_provenance: Optional[Mapping[str, Any]] = None,
        warnings: Iterable[str] = (),
    ) -> "NormalizedValuationSnapshot":
        """Return a new immutable transmission with application provenance."""

        resolved_provenance = (
            dict(holdings_provenance)
            if holdings_provenance is not None
            else _thaw_json(self.holdings_provenance_json)
        )
        resolved_warnings = tuple(
            dict.fromkeys(
                [
                    *(str(item) for item in warnings if str(item).strip()),
                    *self.warnings,
                ]
            )
        )
        updated = NormalizedValuationSnapshot(
            account=self.account,
            rows=self.rows,
            components=self.components,
            shares=self.shares,
            nav_override=self.nav_override,
            price_evidence_json=self.price_evidence_json,
            holdings_provenance_json=_freeze_json(resolved_provenance),
            warnings=resolved_warnings,
            excluded_zero_count=self.excluded_zero_count,
            excluded_zero_key_digest=self.excluded_zero_key_digest,
            source=self.source,
            source_provenance_json=self.source_provenance_json,
            contract_version=self.contract_version,
        )
        object.__setattr__(updated, "_official_issuer", self._official_issuer)
        return updated

    @classmethod
    def from_compatibility_projection(
        cls,
        valuation: Any,
        *,
        source: str = "compatibility_projection",
        account_override: Optional[str] = None,
    ) -> "NormalizedValuationSnapshot":
        """Compatibility adapter for report/test callers that lack transmission.

        This adapter is reporting-only and can never be promoted to an
        official NAV source. Official entrypoints pass the
        ValuationService-owned object explicitly instead.
        """

        account = _nonblank(
            getattr(valuation, "account", None) or account_override,
            field="account",
        )
        rows: list[NormalizedValuationRow] = []
        excluded: list[str] = []
        for holding in list(getattr(valuation, "holdings", None) or []):
            quantity = normalize_quantity(getattr(holding, "quantity", None))
            key = ":".join(
                (
                    account,
                    str(getattr(holding, "broker", "") or ""),
                    str(getattr(holding, "asset_id", "") or ""),
                )
            )
            if quantity == 0:
                excluded.append(key)
                continue
            raw_type = _enum_value(getattr(holding, "asset_type", None)) or "other"
            normalized_type = (
                "cash"
                if raw_type in {"cash", "mmf"}
                else "fund"
                if "fund" in raw_type
                else "equity"
            )
            rows.append(
                NormalizedValuationRow.from_holding(
                    holding,
                    account=account,
                    normalized_type=normalized_type,
                    price=getattr(holding, "current_price", None),
                    cny_price=getattr(holding, "cny_price", None),
                    source="compatibility_projection",
                )
            )
        row_cash = sum(
            (row.market_value_cny or Decimal("0"))
            for row in rows
            if row.normalized_type == "cash"
        )
        row_fund = sum(
            (row.market_value_cny or Decimal("0"))
            for row in rows
            if row.normalized_type == "fund"
        )
        row_equity = sum(
            (row.market_value_cny or Decimal("0"))
            for row in rows
            if row.normalized_type not in {"cash", "fund"}
        )
        declared = {
            "cash": finite_decimal(
                getattr(valuation, "cash_value_cny", 0),
                field="cash_value_cny",
            ).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP),
            "equity": finite_decimal(
                getattr(valuation, "stock_value_cny", 0),
                field="stock_value_cny",
            ).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP),
            "fund": finite_decimal(
                getattr(valuation, "fund_value_cny", 0),
                field="fund_value_cny",
            ).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP),
        }
        row_values = {"cash": row_cash, "equity": row_equity, "fund": row_fund}
        components = tuple(
            ValuationComponent.build(
                name=f"compatibility_{category}_remainder",
                category=category,
                value_cny=value - row_values[category],
                source="compatibility_projection",
                provenance={"field": f"{category}_value_cny"},
            )
            for category, value in declared.items()
            if value != row_values[category]
        )
        return cls.build(
            account=account,
            rows=rows,
            components=components,
            shares=getattr(valuation, "shares", None),
            nav_override=getattr(valuation, "nav", None),
            price_evidence=getattr(valuation, "price_evidence", None) or {},
            holdings_provenance=(
                getattr(valuation, "holdings_provenance", None) or {}
            ),
            warnings=getattr(valuation, "warnings", None) or (),
            excluded_zero_keys=excluded,
            source=source,
            source_provenance={"adapter": "PortfolioValuation"},
        )

    @property
    def row_cash_value(self) -> Decimal:
        return sum(
            (row.market_value_cny or Decimal("0"))
            for row in self.rows
            if row.normalized_type == "cash"
        )

    @property
    def row_fund_value(self) -> Decimal:
        return sum(
            (row.market_value_cny or Decimal("0"))
            for row in self.rows
            if row.normalized_type == "fund"
        )

    @property
    def row_equity_value(self) -> Decimal:
        return sum(
            (row.market_value_cny or Decimal("0"))
            for row in self.rows
            if row.normalized_type not in {"cash", "fund"}
        )

    def _component_value(self, category: str) -> Decimal:
        return sum(
            component.value_cny
            for component in self.components
            if component.category == category
        )

    @property
    def cash_value(self) -> Decimal:
        return self.row_cash_value + self._component_value("cash")

    @property
    def fund_value(self) -> Decimal:
        return self.row_fund_value + self._component_value("fund")

    @property
    def equity_value(self) -> Decimal:
        return self.row_equity_value + self._component_value("equity")

    @property
    def non_cash_value(self) -> Decimal:
        return self.equity_value + self.fund_value

    @property
    def total_value(self) -> Decimal:
        return self.cash_value + self.non_cash_value

    @property
    def nav(self) -> Optional[Decimal]:
        if self.nav_override is not None:
            return self.nav_override
        if self.shares is None or self.shares <= 0:
            return None
        return (self.total_value / self.shares).quantize(
            NAV_QUANT,
            rounding=ROUND_HALF_UP,
        )

    def _region_value(self, asset_class: str) -> Decimal:
        return sum(
            (row.market_value_cny or Decimal("0"))
            for row in self.rows
            if row.asset_class == asset_class
        )

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "account": self.account,
            "rows": [row.canonical_payload() for row in self.rows],
            "components": [
                component.canonical_payload() for component in self.components
            ],
            "shares": canonical_decimal(self.shares),
            "nav_override": canonical_decimal(self.nav_override),
            "price_evidence": _thaw_json(self.price_evidence_json),
            "holdings_provenance": _thaw_json(self.holdings_provenance_json),
            "warnings": list(self.warnings),
            "excluded_zero_count": self.excluded_zero_count,
            "excluded_zero_key_digest": self.excluded_zero_key_digest,
            "source": self.source,
            "source_provenance": _thaw_json(self.source_provenance_json),
            "official_eligible": self.official_eligible,
        }

    @property
    def digest(self) -> str:
        return digest_payload(self.canonical_payload())

    @property
    def official_eligible(self) -> bool:
        return self._official_issuer is _OFFICIAL_ISSUER

    def to_portfolio_valuation(self) -> Any:
        from src.models import PortfolioValuation

        total = self.total_value
        holdings = []
        for row in self.rows:
            weight = (
                (row.market_value_cny / total).quantize(
                    WEIGHT_QUANT,
                    rounding=ROUND_HALF_UP,
                )
                if total > 0 and row.market_value_cny is not None
                else None
            )
            holdings.append(row.to_holding(weight=weight))
        valuation = PortfolioValuation(
            account=self.account,
            total_value_cny=float(total),
            cash_value_cny=float(self.cash_value),
            stock_value_cny=float(self.equity_value),
            fund_value_cny=float(self.fund_value),
            cn_asset_value=float(self._region_value("中国资产")),
            us_asset_value=float(self._region_value("美国资产")),
            hk_asset_value=float(self._region_value("港股资产")),
            shares=float(self.shares) if self.shares is not None else None,
            nav=float(self.nav) if self.nav is not None else None,
            holdings=holdings,
            price_evidence=_thaw_json(self.price_evidence_json),
            holdings_provenance=_thaw_json(self.holdings_provenance_json) or None,
            warnings=list(self.warnings),
        )
        object.__setattr__(valuation, "_normalized_valuation", self)
        object.__setattr__(valuation, "_normalized_valuation_digest", self.digest)
        return valuation

    @staticmethod
    def _compatibility_payload(valuation: Any) -> dict[str, Any]:
        holdings = []
        for holding in list(getattr(valuation, "holdings", None) or []):
            holdings.append({
                "record_id": getattr(holding, "record_id", None),
                "account": str(getattr(holding, "account", "") or ""),
                "asset_id": str(getattr(holding, "asset_id", "") or ""),
                "asset_name": str(getattr(holding, "asset_name", "") or ""),
                "asset_type": _enum_value(getattr(holding, "asset_type", None)),
                "broker": str(getattr(holding, "broker", "") or ""),
                "quantity": canonical_decimal(
                    finite_decimal(getattr(holding, "quantity", None), field="quantity")
                ),
                "avg_cost": (
                    canonical_decimal(
                        finite_decimal(
                            getattr(holding, "avg_cost", None),
                            field="avg_cost",
                        )
                    )
                    if getattr(holding, "avg_cost", None) is not None
                    else None
                ),
                "currency": str(getattr(holding, "currency", "") or ""),
                "asset_class": _enum_value(getattr(holding, "asset_class", None)),
                "industry": _enum_value(getattr(holding, "industry", None)),
                "tags": [str(item) for item in (getattr(holding, "tag", None) or [])],
                "price": (
                    canonical_decimal(
                        finite_decimal(
                            getattr(holding, "current_price", None),
                            field="price",
                        )
                    )
                    if getattr(holding, "current_price", None) is not None
                    else None
                ),
                "cny_price": (
                    canonical_decimal(
                        finite_decimal(
                            getattr(holding, "cny_price", None),
                            field="cny_price",
                        )
                    )
                    if getattr(holding, "cny_price", None) is not None
                    else None
                ),
                "market_value_cny": (
                    canonical_decimal(
                        finite_decimal(
                            getattr(holding, "market_value_cny", None),
                            field="market_value_cny",
                        )
                    )
                    if getattr(holding, "market_value_cny", None) is not None
                    else None
                ),
                "weight": (
                    canonical_decimal(
                        finite_decimal(
                            getattr(holding, "weight", None),
                            field="weight",
                        )
                    )
                    if getattr(holding, "weight", None) is not None
                    else None
                ),
            })
        holdings.sort(
            key=lambda row: (
                row["account"],
                row["broker"],
                row["asset_id"],
                row["record_id"] or "",
            )
        )

        def number(name: str) -> Optional[str]:
            value = getattr(valuation, name, None)
            return (
                canonical_decimal(finite_decimal(value, field=name))
                if value is not None
                else None
            )

        return {
            "account": str(getattr(valuation, "account", "") or ""),
            "total_value_cny": number("total_value_cny"),
            "cash_value_cny": number("cash_value_cny"),
            "stock_value_cny": number("stock_value_cny"),
            "fund_value_cny": number("fund_value_cny"),
            "cn_asset_value": number("cn_asset_value"),
            "us_asset_value": number("us_asset_value"),
            "hk_asset_value": number("hk_asset_value"),
            "shares": number("shares"),
            "nav": number("nav"),
            "holdings": holdings,
            "price_evidence": dict(
                getattr(valuation, "price_evidence", None) or {}
            ),
            "holdings_provenance": dict(
                getattr(valuation, "holdings_provenance", None) or {}
            ),
            "warnings": list(getattr(valuation, "warnings", None) or []),
        }

    @property
    def compatibility_digest(self) -> str:
        return digest_payload(
            self._compatibility_payload(self.to_portfolio_valuation())
        )

    def assert_compatible(self, valuation: Any) -> None:
        actual = digest_payload(self._compatibility_payload(valuation))
        expected = self.compatibility_digest
        if actual != expected:
            raise ValueError(
                "valuation compatibility projection does not match "
                f"normalized valuation digest: expected={expected} actual={actual}"
            )

    def assert_official_eligible(
        self,
        *,
        expected_source: Optional[str] = None,
    ) -> None:
        if not self.official_eligible:
            raise ValueError(
                "official NAV requires a ValuationService-owned "
                "NormalizedValuationSnapshot"
            )
        if expected_source is not None and self.source != expected_source:
            raise ValueError(
                "official normalized valuation source mismatch: "
                f"expected={expected_source} actual={self.source}"
            )

    def to_snapshot_rows(self, *, as_of: str) -> tuple[Any, ...]:
        return tuple(row.to_snapshot_row(as_of=as_of) for row in self.rows)

    def target_digest(self, *, as_of: str) -> str:
        rows = [
            snapshot_row_payload(row)
            for row in self.to_snapshot_rows(as_of=as_of)
        ]
        return digest_payload({
            "version": SNAPSHOT_DIGEST_VERSION,
            "account": self.account,
            "as_of": as_of,
            "rows": rows,
            "components": [
                component.canonical_payload() for component in self.components
            ],
            "excluded_zero_count": self.excluded_zero_count,
            "excluded_zero_key_digest": self.excluded_zero_key_digest,
            "source": self.source,
            "source_provenance": _thaw_json(self.source_provenance_json),
        })

    def evidence(self, *, as_of: str, status: str = "planned") -> dict[str, Any]:
        rows = self.to_snapshot_rows(as_of=as_of)

        return {
            "version": "v2",
            "contract_version": self.contract_version,
            "status": status,
            "normalized_digest": self.digest,
            "target_digest": self.target_digest(as_of=as_of),
            "row_digest": snapshot_digest(rows),
            "row_count": len(rows),
            "components": [
                component.canonical_payload() for component in self.components
            ],
            "excluded_zero_count": self.excluded_zero_count,
            "excluded_zero_key_digest": self.excluded_zero_key_digest,
            "source": self.source,
            "source_provenance": _thaw_json(self.source_provenance_json),
        }


def attached_normalized_valuation(valuation: Any) -> Optional[NormalizedValuationSnapshot]:
    candidate = getattr(valuation, "_normalized_valuation", None)
    return candidate if isinstance(candidate, NormalizedValuationSnapshot) else None
