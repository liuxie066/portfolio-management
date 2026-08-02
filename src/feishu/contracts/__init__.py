"""Public Feishu Bitable structure contract API."""

from .models import (
    FieldContract,
    FieldEncoding,
    FieldOwnership,
    TableContract,
    TableRole,
    WriteContract,
    WriteOperation,
)
from .registry import (
    ACTIVE_REMOTE_TABLES,
    RETIRED_REMOTE_TABLES,
    TABLE_CONTRACTS,
    compare_live_schema,
    field_names_by_encoding,
    get_table_contract,
    parse_table_ref,
    validate_write_fields,
)

__all__ = [
    "ACTIVE_REMOTE_TABLES",
    "FieldContract",
    "FieldEncoding",
    "FieldOwnership",
    "RETIRED_REMOTE_TABLES",
    "TABLE_CONTRACTS",
    "TableContract",
    "TableRole",
    "WriteContract",
    "WriteOperation",
    "compare_live_schema",
    "field_names_by_encoding",
    "get_table_contract",
    "parse_table_ref",
    "validate_write_fields",
]
