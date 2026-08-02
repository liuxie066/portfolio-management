"""Immutable metadata types for the Feishu Bitable wire contract."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping


class TableRole(str, Enum):
    """Whether a configured table is required by the running product."""

    CORE = "core"
    OPTIONAL = "optional"
    RETIRED = "retired"


class FieldOwnership(str, Enum):
    """Who is allowed to author a field's business value."""

    MANUAL = "manual"
    SYSTEM = "system"
    MIXED = "mixed"
    RESERVED = "reserved"


class FieldEncoding(str, Enum):
    """Application-side encoding used on the Feishu wire."""

    TEXT = "text"
    NUMBER = "number"
    DATETIME = "datetime"
    SINGLE_SELECT = "single_select"
    JSON_TEXT = "json_text"


class WriteOperation(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


@dataclass(frozen=True)
class FieldContract:
    name: str
    type_id: int
    ui_type: str
    encoding: FieldEncoding
    ownership: FieldOwnership
    schema_required: bool = True
    clearable: bool = False
    select_options: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("field contract name is required")
        if self.type_id <= 0:
            raise ValueError(f"field {self.name} type_id must be positive")
        if not self.ui_type.strip():
            raise ValueError(f"field {self.name} ui_type is required")
        if self.encoding is FieldEncoding.SINGLE_SELECT and not self.select_options:
            raise ValueError(f"single-select field {self.name} requires options")
        if self.encoding is not FieldEncoding.SINGLE_SELECT and self.select_options:
            raise ValueError(f"non-select field {self.name} cannot declare options")
        if len(set(self.select_options)) != len(self.select_options):
            raise ValueError(f"field {self.name} has duplicate select options")


@dataclass(frozen=True)
class WriteContract:
    operation: WriteOperation
    required_fields: frozenset[str] = frozenset()
    allowed_fields: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.required_fields.issubset(self.allowed_fields):
            extra = sorted(self.required_fields - self.allowed_fields)
            raise ValueError(
                f"write contract {self.operation.value} requires disallowed fields: {extra}"
            )


@dataclass(frozen=True)
class TableContract:
    name: str
    role: TableRole
    fields: tuple[FieldContract, ...]
    business_key: tuple[str, ...] = ()
    write_contracts: tuple[WriteContract, ...] = ()
    forbidden_fields: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        field_names = [field.name for field in self.fields]
        if not self.name.strip():
            raise ValueError("table contract name is required")
        if len(field_names) != len(set(field_names)):
            raise ValueError(f"table {self.name} has duplicate field contracts")
        if not set(self.business_key).issubset(field_names):
            raise ValueError(f"table {self.name} business key references unknown fields")
        operations = [contract.operation for contract in self.write_contracts]
        if len(operations) != len(set(operations)):
            raise ValueError(f"table {self.name} has duplicate write operations")
        known = set(field_names)
        for contract in self.write_contracts:
            if not contract.allowed_fields.issubset(known):
                unknown = sorted(contract.allowed_fields - known)
                raise ValueError(
                    f"table {self.name} {contract.operation.value} allows unknown fields: {unknown}"
                )
            if (
                contract.operation is WriteOperation.CREATE
                and not set(self.business_key).issubset(contract.required_fields)
            ):
                missing_key_fields = sorted(
                    set(self.business_key) - contract.required_fields
                )
                raise ValueError(
                    f"table {self.name} create contract must require business key fields: "
                    f"{missing_key_fields}"
                )
        if self.forbidden_fields & known:
            overlap = sorted(self.forbidden_fields & known)
            raise ValueError(f"table {self.name} fields are also forbidden: {overlap}")

    @property
    def fields_by_name(self) -> Mapping[str, FieldContract]:
        return MappingProxyType({field.name: field for field in self.fields})

    @property
    def write_contracts_by_operation(self) -> Mapping[WriteOperation, WriteContract]:
        return MappingProxyType(
            {contract.operation: contract for contract in self.write_contracts}
        )

    def write_contract(self, operation: WriteOperation | str) -> WriteContract | None:
        normalized = (
            operation
            if isinstance(operation, WriteOperation)
            else WriteOperation(str(operation))
        )
        return self.write_contracts_by_operation.get(normalized)


def describe_live_field(item: Mapping[str, Any]) -> dict[str, Any]:
    """Return the stable live-schema attributes used in drift reports."""

    return {
        "type": item.get("type"),
        "ui_type": item.get("ui_type"),
        "select_options": tuple(
            str(option.get("name"))
            for option in ((item.get("property") or {}).get("options") or [])
            if isinstance(option, Mapping) and option.get("name") is not None
        ),
    }
