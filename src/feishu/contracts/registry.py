"""Canonical Feishu Bitable structure registry and boundary validators."""
from __future__ import annotations

from types import MappingProxyType
from typing import Any, Iterable, Mapping, Optional

from .models import (
    FieldContract,
    FieldEncoding,
    FieldOwnership,
    TableContract,
    TableRole,
    WriteContract,
    WriteOperation,
    describe_live_field,
)


def _field(
    name: str,
    type_id: int,
    ui_type: str,
    encoding: FieldEncoding,
    ownership: FieldOwnership,
    *,
    required: bool = True,
    clearable: bool = False,
    options: Iterable[str] = (),
) -> FieldContract:
    return FieldContract(
        name=name,
        type_id=type_id,
        ui_type=ui_type,
        encoding=encoding,
        ownership=ownership,
        schema_required=required,
        clearable=clearable,
        select_options=tuple(options),
    )


def _writes(
    fields: Iterable[FieldContract],
    required: Iterable[str],
    *,
    allow_delete: bool = True,
) -> tuple[WriteContract, ...]:
    allowed = frozenset(field.name for field in fields)
    contracts = [
        WriteContract(
            operation=WriteOperation.CREATE,
            required_fields=frozenset(required),
            allowed_fields=allowed,
        ),
        WriteContract(
            operation=WriteOperation.UPDATE,
            allowed_fields=allowed,
        ),
    ]
    if allow_delete:
        contracts.append(WriteContract(operation=WriteOperation.DELETE))
    return tuple(contracts)


_HOLDING_FIELDS = (
    _field("asset_id", 1, "Text", FieldEncoding.TEXT, FieldOwnership.MANUAL),
    _field("asset_name", 1, "Text", FieldEncoding.TEXT, FieldOwnership.MANUAL),
    _field(
        "asset_type", 3, "SingleSelect", FieldEncoding.SINGLE_SELECT,
        FieldOwnership.MANUAL,
        options=(
            "a_stock", "cash", "otc_fund", "other", "hk_stock",
            "us_stock", "exchange_fund", "us_fund", "mmf", "crypto",
        ),
    ),
    _field("account", 1, "Text", FieldEncoding.TEXT, FieldOwnership.MANUAL),
    _field("broker", 1, "Text", FieldEncoding.TEXT, FieldOwnership.MANUAL),
    _field("quantity", 2, "Number", FieldEncoding.NUMBER, FieldOwnership.MANUAL),
    _field(
        "avg_cost", 2, "Number", FieldEncoding.NUMBER, FieldOwnership.MIXED,
        required=False, clearable=True,
    ),
    _field("currency", 1, "Text", FieldEncoding.TEXT, FieldOwnership.MANUAL),
    _field(
        "asset_class", 3, "SingleSelect", FieldEncoding.SINGLE_SELECT,
        FieldOwnership.MANUAL, required=False, clearable=True,
        options=("美国资产", "另类资产", "中国资产", "现金", "港股资产"),
    ),
    _field(
        "industry", 3, "SingleSelect", FieldEncoding.SINGLE_SELECT,
        FieldOwnership.MANUAL, required=False, clearable=True,
        options=(
            "金融", "AI", "中概", "非行业指数", "区块链", "能源", "消费",
            "房地产", "半导体", "现金", "科技", "其他",
        ),
    ),
    _field(
        "tag", 1, "Text", FieldEncoding.JSON_TEXT, FieldOwnership.MANUAL,
        required=False,
    ),
    _field("created_at", 1, "Text", FieldEncoding.TEXT, FieldOwnership.SYSTEM, required=False),
    _field("updated_at", 1, "Text", FieldEncoding.TEXT, FieldOwnership.SYSTEM, required=False),
)

_CASH_FLOW_FIELDS = (
    _field("flow_date", 5, "DateTime", FieldEncoding.DATETIME, FieldOwnership.MANUAL),
    _field("account", 1, "Text", FieldEncoding.TEXT, FieldOwnership.MANUAL),
    _field("broker", 1, "Text", FieldEncoding.TEXT, FieldOwnership.MANUAL),
    _field("amount", 2, "Number", FieldEncoding.NUMBER, FieldOwnership.MANUAL),
    _field("currency", 1, "Text", FieldEncoding.TEXT, FieldOwnership.MANUAL),
    _field(
        "flow_type", 3, "SingleSelect", FieldEncoding.SINGLE_SELECT,
        FieldOwnership.SYSTEM, options=("DEPOSIT", "WITHDRAW"),
    ),
    _field("cny_amount", 2, "Number", FieldEncoding.NUMBER, FieldOwnership.SYSTEM),
    _field("dedup_key", 1, "Text", FieldEncoding.TEXT, FieldOwnership.SYSTEM),
    _field(
        "exchange_rate", 2, "Number", FieldEncoding.NUMBER,
        FieldOwnership.SYSTEM, required=False, clearable=True,
    ),
    _field("source", 1, "Text", FieldEncoding.TEXT, FieldOwnership.SYSTEM, required=False),
    _field(
        "remark", 1, "Text", FieldEncoding.TEXT, FieldOwnership.MANUAL,
        required=False, clearable=True,
    ),
    _field("updated_at", 1, "Text", FieldEncoding.TEXT, FieldOwnership.SYSTEM, required=False),
)

_NAV_FIELDS = (
    _field("date", 5, "DateTime", FieldEncoding.DATETIME, FieldOwnership.SYSTEM),
    _field("account", 1, "Text", FieldEncoding.TEXT, FieldOwnership.SYSTEM),
    _field("total_value", 2, "Number", FieldEncoding.NUMBER, FieldOwnership.SYSTEM),
    _field("shares", 2, "Number", FieldEncoding.NUMBER, FieldOwnership.SYSTEM),
    _field("nav", 2, "Number", FieldEncoding.NUMBER, FieldOwnership.SYSTEM),
    *(
        _field(name, 2, "Number", FieldEncoding.NUMBER, FieldOwnership.SYSTEM, required=False)
        for name in (
            "cash_value", "stock_value", "fund_value", "cn_stock_value",
            "us_stock_value", "hk_stock_value", "stock_weight", "cash_weight",
            "cash_flow", "share_change", "mtd_nav_change", "ytd_nav_change",
            "pnl", "mtd_pnl", "ytd_pnl",
        )
    ),
    _field("details", 1, "Text", FieldEncoding.JSON_TEXT, FieldOwnership.SYSTEM, required=False),
    _field("updated_at", 1, "Text", FieldEncoding.TEXT, FieldOwnership.SYSTEM, required=False),
)

_SNAPSHOT_FIELDS = (
    _field("as_of", 1, "Text", FieldEncoding.TEXT, FieldOwnership.SYSTEM),
    _field("account", 1, "Text", FieldEncoding.TEXT, FieldOwnership.SYSTEM),
    _field("asset_id", 1, "Text", FieldEncoding.TEXT, FieldOwnership.SYSTEM),
    _field("broker", 1, "Text", FieldEncoding.TEXT, FieldOwnership.SYSTEM),
    _field("quantity", 2, "Number", FieldEncoding.NUMBER, FieldOwnership.SYSTEM),
    _field("currency", 1, "Text", FieldEncoding.TEXT, FieldOwnership.SYSTEM),
    _field("price", 2, "Number", FieldEncoding.NUMBER, FieldOwnership.SYSTEM),
    _field("cny_price", 2, "Number", FieldEncoding.NUMBER, FieldOwnership.SYSTEM),
    _field("market_value_cny", 2, "Number", FieldEncoding.NUMBER, FieldOwnership.SYSTEM),
    _field("dedup_key", 1, "Text", FieldEncoding.TEXT, FieldOwnership.SYSTEM),
    _field(
        "asset_name", 1, "Text", FieldEncoding.TEXT, FieldOwnership.SYSTEM,
        required=False, clearable=True,
    ),
    _field(
        "avg_cost", 2, "Number", FieldEncoding.NUMBER, FieldOwnership.SYSTEM,
        required=False, clearable=True,
    ),
    _field(
        "source", 1, "Text", FieldEncoding.TEXT, FieldOwnership.SYSTEM,
        required=False, clearable=True,
    ),
    _field("remark", 1, "Text", FieldEncoding.TEXT, FieldOwnership.SYSTEM, required=False, clearable=True),
)

_TRANSACTION_FIELDS = (
    _field("tx_date", 1, "Text", FieldEncoding.TEXT, FieldOwnership.MIXED),
    _field("tx_type", 1, "Text", FieldEncoding.TEXT, FieldOwnership.MANUAL),
    _field("asset_id", 1, "Text", FieldEncoding.TEXT, FieldOwnership.MANUAL),
    _field("account", 1, "Text", FieldEncoding.TEXT, FieldOwnership.MANUAL),
    _field("quantity", 2, "Number", FieldEncoding.NUMBER, FieldOwnership.MANUAL),
    _field("price", 2, "Number", FieldEncoding.NUMBER, FieldOwnership.MANUAL),
    _field("currency", 1, "Text", FieldEncoding.TEXT, FieldOwnership.MANUAL),
    _field("request_id", 1, "Text", FieldEncoding.TEXT, FieldOwnership.SYSTEM),
    _field("dedup_key", 1, "Text", FieldEncoding.TEXT, FieldOwnership.SYSTEM),
    _field("asset_name", 1, "Text", FieldEncoding.TEXT, FieldOwnership.MIXED, required=False),
    _field("asset_type", 1, "Text", FieldEncoding.TEXT, FieldOwnership.MIXED, required=False),
    _field("market", 1, "Text", FieldEncoding.TEXT, FieldOwnership.MIXED, required=False),
    _field("amount", 2, "Number", FieldEncoding.NUMBER, FieldOwnership.SYSTEM, required=False),
    _field("fee", 2, "Number", FieldEncoding.NUMBER, FieldOwnership.MANUAL, required=False),
    _field("remark", 1, "Text", FieldEncoding.TEXT, FieldOwnership.MANUAL, required=False),
)

_COMPENSATION_FIELDS = (
    _field("task_id", 1, "Text", FieldEncoding.TEXT, FieldOwnership.SYSTEM),
    _field("operation_type", 1, "Text", FieldEncoding.TEXT, FieldOwnership.SYSTEM),
    _field("account", 1, "Text", FieldEncoding.TEXT, FieldOwnership.SYSTEM),
    _field(
        "status", 3, "SingleSelect", FieldEncoding.SINGLE_SELECT,
        FieldOwnership.SYSTEM, options=("PENDING", "RUNNING", "FAILED", "RESOLVED"),
    ),
    _field("payload", 1, "Text", FieldEncoding.JSON_TEXT, FieldOwnership.SYSTEM),
    _field("error", 1, "Text", FieldEncoding.TEXT, FieldOwnership.SYSTEM),
    _field("related_record_id", 1, "Text", FieldEncoding.TEXT, FieldOwnership.SYSTEM),
    _field("retry_count", 2, "Number", FieldEncoding.NUMBER, FieldOwnership.SYSTEM),
    _field("created_at", 1, "Text", FieldEncoding.TEXT, FieldOwnership.SYSTEM),
    _field("updated_at", 1, "Text", FieldEncoding.TEXT, FieldOwnership.SYSTEM),
    _field("resolved_at", 1, "Text", FieldEncoding.TEXT, FieldOwnership.SYSTEM, required=False),
    _field("resolution", 1, "Text", FieldEncoding.TEXT, FieldOwnership.SYSTEM, required=False),
)

_SCHEMA_VERSION_FIELDS = (
    _field("migration_id", 1, "Text", FieldEncoding.TEXT, FieldOwnership.SYSTEM),
    _field("description", 1, "Text", FieldEncoding.TEXT, FieldOwnership.SYSTEM),
    _field("applied_at", 1, "Text", FieldEncoding.TEXT, FieldOwnership.SYSTEM),
    _field(
        "status", 3, "SingleSelect", FieldEncoding.SINGLE_SELECT,
        FieldOwnership.SYSTEM, options=("APPLIED", "FAILED"),
    ),
    _field("notes", 1, "Text", FieldEncoding.TEXT, FieldOwnership.MIXED, required=False),
)


_TABLES = (
    TableContract(
        name="holdings",
        role=TableRole.CORE,
        fields=_HOLDING_FIELDS,
        business_key=("asset_id", "account", "broker"),
        write_contracts=_writes(
            _HOLDING_FIELDS,
            (
                "asset_id", "asset_name", "asset_type", "account", "broker",
                "quantity", "currency",
            ),
        ),
    ),
    TableContract(
        name="cash_flow",
        role=TableRole.CORE,
        fields=_CASH_FLOW_FIELDS,
        business_key=("dedup_key",),
        write_contracts=_writes(_CASH_FLOW_FIELDS, ("flow_date", "account", "amount", "currency")),
        forbidden_fields=frozenset({
            "exchange_rate_date", "exchange_rate_source", "exchange_rate_evidence_type",
        }),
    ),
    TableContract(
        name="nav_history",
        role=TableRole.CORE,
        fields=_NAV_FIELDS,
        business_key=("account", "date"),
        write_contracts=_writes(_NAV_FIELDS, ("date", "account", "total_value", "shares", "nav")),
    ),
    TableContract(
        name="holdings_snapshot",
        role=TableRole.CORE,
        fields=_SNAPSHOT_FIELDS,
        business_key=("as_of", "account", "asset_id", "broker"),
        write_contracts=_writes(
            _SNAPSHOT_FIELDS,
            (
                "as_of", "account", "asset_id", "broker", "quantity", "currency",
                "price", "cny_price", "market_value_cny", "dedup_key",
            ),
        ),
    ),
    TableContract(
        name="transactions",
        role=TableRole.OPTIONAL,
        fields=_TRANSACTION_FIELDS,
        business_key=("request_id",),
        write_contracts=(),
    ),
    TableContract(
        name="compensation_tasks",
        role=TableRole.OPTIONAL,
        fields=_COMPENSATION_FIELDS,
        business_key=("task_id",),
        write_contracts=_writes(
            _COMPENSATION_FIELDS,
            (
                "task_id", "operation_type", "account", "status", "payload", "error",
                "retry_count", "created_at", "updated_at",
            ),
        ),
    ),
    TableContract(
        name="schema_version",
        role=TableRole.OPTIONAL,
        fields=_SCHEMA_VERSION_FIELDS,
        business_key=("migration_id",),
        write_contracts=_writes(
            _SCHEMA_VERSION_FIELDS,
            ("migration_id", "description", "applied_at", "status"),
        ),
    ),
)

TABLE_CONTRACTS: Mapping[str, TableContract] = MappingProxyType(
    {table.name: table for table in _TABLES}
)
ACTIVE_REMOTE_TABLES = tuple(TABLE_CONTRACTS)
RETIRED_REMOTE_TABLES = frozenset({"price_cache"})


def get_table_contract(table_name: str) -> TableContract:
    name = str(table_name or "").strip()
    if name in RETIRED_REMOTE_TABLES:
        raise ValueError(f"Feishu table {name} is retired; use the local cache")
    try:
        return TABLE_CONTRACTS[name]
    except KeyError as exc:
        raise ValueError(f"unknown Feishu table contract: {name or '<empty>'}") from exc


def field_names_by_encoding(
    table_name: str,
    encoding: FieldEncoding,
) -> frozenset[str]:
    """Project one wire-encoding field set from the canonical registry."""

    table = get_table_contract(table_name)
    return frozenset(
        field.name for field in table.fields if field.encoding is encoding
    )


def parse_table_ref(
    raw_value: Any,
    *,
    default_app_token: Any = None,
    table_name: str = "table",
    require_app_token: bool = True,
) -> tuple[Optional[str], str]:
    """Parse either ``table_id`` or ``app_token/table_id`` consistently."""

    name = str(table_name or "table").strip() or "table"
    value = str(raw_value or "").strip()
    if not value:
        raise ValueError(f"missing Feishu table configuration: {name}")
    parts = value.split("/")
    if len(parts) > 2 or any(not part.strip() for part in parts):
        raise ValueError(
            f"invalid Feishu table reference for {name}; expected app_token/table_id"
        )
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    app_token = str(default_app_token or "").strip() or None
    if require_app_token and app_token is None:
        raise ValueError(f"missing feishu.app_token for table configuration: {name}")
    return app_token, parts[0].strip()


def validate_write_fields(
    table_name: str,
    operation: WriteOperation | str,
    fields: Mapping[str, Any],
    *,
    row_index: int | None = None,
) -> None:
    """Validate one wire row against the declared operation contract."""

    table = get_table_contract(table_name)
    normalized_operation = (
        operation
        if isinstance(operation, WriteOperation)
        else WriteOperation(str(operation))
    )
    location = (
        f"table={table.name} operation={normalized_operation.value}"
        + (f" row_index={row_index}" if row_index is not None else "")
    )
    contract = table.write_contract(normalized_operation)
    if contract is None:
        raise ValueError(f"{location}: no write contract (read-only table)")
    if not isinstance(fields, Mapping):
        raise ValueError(f"{location}: fields must be an object")
    unknown = sorted(set(fields) - contract.allowed_fields)
    if unknown:
        raise ValueError(f"{location}: unknown fields: {', '.join(unknown)}")
    if normalized_operation is not WriteOperation.DELETE and not fields:
        raise ValueError(f"{location}: fields must not be empty")
    for field_name in sorted(contract.required_fields):
        value = fields.get(field_name)
        if (
            field_name not in fields
            or value is None
            or (isinstance(value, str) and not value.strip())
        ):
            raise ValueError(f"{location}: 缺少必填字段: {field_name}")
    fields_by_name = table.fields_by_name
    if normalized_operation is WriteOperation.CREATE:
        null_fields = sorted(name for name, value in fields.items() if value is None)
        if null_fields:
            raise ValueError(
                f"{location}: create fields cannot be null: {', '.join(null_fields)}"
            )
    if normalized_operation is WriteOperation.UPDATE:
        nonclearable_empty = sorted(
            name
            for name, value in fields.items()
            if (
                value is None
                or (isinstance(value, str) and not value.strip())
            )
            and not fields_by_name[name].clearable
        )
        if nonclearable_empty:
            raise ValueError(
                f"{location}: fields cannot be cleared: "
                + ", ".join(nonclearable_empty)
            )
    for field_name, value in fields.items():
        field = fields_by_name[field_name]
        if value in (None, "") or field.encoding is not FieldEncoding.SINGLE_SELECT:
            continue
        candidate = value.value if hasattr(value, "value") else value
        if not isinstance(candidate, str) or candidate not in field.select_options:
            raise ValueError(
                f"{location}: unsupported select value for {field_name}: {candidate!r}; "
                f"allowed={list(field.select_options)!r}"
            )


def compare_live_schema(
    table: TableContract,
    items: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Compare exact field type, UI type, select options, and field presence."""

    live_by_name = {
        str(item.get("field_name")): item
        for item in items
        if isinstance(item, Mapping) and item.get("field_name")
    }
    expected_by_name = table.fields_by_name
    missing_required = sorted(
        name
        for name, field in expected_by_name.items()
        if field.schema_required and name not in live_by_name
    )
    missing_optional = sorted(
        name
        for name, field in expected_by_name.items()
        if not field.schema_required and name not in live_by_name
    )
    forbidden_present = sorted(table.forbidden_fields & set(live_by_name))
    extra_fields = sorted(set(live_by_name) - set(expected_by_name))
    mismatches: list[dict[str, Any]] = []
    for name, field in expected_by_name.items():
        if name not in live_by_name:
            continue
        live = describe_live_field(live_by_name[name])
        expected_options = tuple(field.select_options)
        if (
            live["type"] != field.type_id
            or str(live["ui_type"] or "") != field.ui_type
            or live["select_options"] != expected_options
        ):
            mismatches.append({
                "field_name": name,
                "expected_type": field.type_id,
                "expected_ui_type": field.ui_type,
                "expected_select_options": list(expected_options),
                "live_type": live["type"],
                "live_ui_type": live["ui_type"],
                "live_select_options": list(live["select_options"]),
                "required": field.schema_required,
            })
    return {
        "missing_required": missing_required,
        "missing_optional": missing_optional,
        "forbidden_present": forbidden_present,
        "extra_fields": extra_fields,
        "field_mismatches": mismatches,
        "ok": not missing_required and not forbidden_present and not extra_fields and not mismatches,
    }
