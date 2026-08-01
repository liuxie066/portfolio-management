#!/usr/bin/env python3
"""Inspect the canonical or live Feishu Bitable structure contract."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.feishu.contracts import (  # noqa: E402
    RETIRED_REMOTE_TABLES,
    TABLE_CONTRACTS,
    FieldEncoding,
    TableRole,
    compare_live_schema,
)
from src.feishu_client import FeishuClient  # noqa: E402


DOCS_SCHEMA = REPO_ROOT / "docs" / "schema.md"


@dataclass(frozen=True)
class TableSpec:
    """Compatibility projection of one registry table.

    The historical type is retained for callers that imported
    ``parse_docs_schema``. Its values are registry-owned and markdown is never
    parsed as runtime input.
    """

    name: str
    required: Any
    optional: Any
    forbidden: frozenset[str]
    role: str


def parse_docs_schema(path: Path = DOCS_SCHEMA) -> dict[str, TableSpec]:
    """Return registry-backed specs without reading the legacy markdown path."""

    del path
    return {
        table_name: TableSpec(
            name=table_name,
            required=MappingProxyType({
                field.name: field
                for field in table.fields
                if field.schema_required
            }),
            optional=MappingProxyType({
                field.name: field
                for field in table.fields
                if not field.schema_required
            }),
            forbidden=table.forbidden_fields,
            role=table.role.value,
        )
        for table_name, table in TABLE_CONTRACTS.items()
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect canonical or live Feishu schema.")
    parser.add_argument(
        "command",
        nargs="?",
        choices=["expectations", "check-live", "cash-flow-effects"],
        default="expectations",
        help="schema action (default: expectations)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero when a configured table differs from the registry",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="create missing cash-flow effect fields; default is dry-run",
    )
    parser.add_argument("--confirm", action="store_true", help="required with --apply")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "check-live":
        result = run_schema_check(strict=args.strict)
    elif args.command == "cash-flow-effects":
        if args.apply and not args.confirm:
            raise SystemExit("cash-flow-effects --apply requires --confirm")
        result = migrate_cash_flow_effect_fields(
            apply=bool(args.apply),
            confirm=bool(args.confirm),
        )
    else:
        result = schema_expectations()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok", result.get("success", True)) else 1


_CASH_FLOW_BROKER = TABLE_CONTRACTS["cash_flow"].fields_by_name["broker"]
CASH_FLOW_EFFECT_FIELD_DEFINITIONS = {
    "broker": {"field_name": "broker", "type": _CASH_FLOW_BROKER.type_id},
}


def migrate_cash_flow_effect_fields(
    *,
    apply: bool = False,
    confirm: bool = False,
    client: Any = None,
) -> dict[str, Any]:
    """Create only missing fields; incompatible existing fields always block."""
    if apply and not confirm:
        raise ValueError("cash-flow-effects apply requires confirm=True")
    client = client or FeishuClient()
    app_token, table_id = client._get_table_config("cash_flow")
    live = _list_live_fields(client, app_token, table_id)
    by_name = {
        str(item.get("field_name") or ""): item
        for item in live
        if item.get("field_name")
    }
    missing = [
        name for name in CASH_FLOW_EFFECT_FIELD_DEFINITIONS if name not in by_name
    ]
    incompatible = []
    for name, definition in CASH_FLOW_EFFECT_FIELD_DEFINITIONS.items():
        if name not in by_name:
            continue
        try:
            live_type = int(by_name[name].get("type"))
        except (TypeError, ValueError):
            live_type = None
        if live_type != int(definition["type"]):
            incompatible.append({
                "field_name": name,
                "expected_type": definition["type"],
                "live_type": live_type,
            })
    if incompatible:
        return {
            "success": False,
            "dry_run": not apply,
            "missing": missing,
            "incompatible": incompatible,
            "created": [],
            "error": "incompatible live fields must be repaired explicitly",
        }
    created = []
    if apply:
        endpoint = f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields"
        for name in missing:
            response = client._request(
                "POST",
                endpoint,
                json=CASH_FLOW_EFFECT_FIELD_DEFINITIONS[name],
            )
            created.append({
                "field_name": name,
                "field_id": response.get("field_id"),
            })
    return {
        "success": True,
        "dry_run": not apply,
        "missing": missing,
        "incompatible": [],
        "created": created,
        "manual_view_action_required": (
            "add flow_date/account/broker/amount/currency/remark to the operator view "
            "and hide generated fields"
        ),
    }


def schema_expectations() -> dict[str, Any]:
    """Serialize the typed registry for operators and deterministic tests."""
    tables: dict[str, Any] = {}
    for table_name, table in TABLE_CONTRACTS.items():
        required = [field.name for field in table.fields if field.schema_required]
        optional = [field.name for field in table.fields if not field.schema_required]
        tables[table_name] = {
            "role": table.role.value,
            "business_key": list(table.business_key),
            "required": sorted(required),
            "optional": sorted(optional),
            "forbidden": sorted(table.forbidden_fields),
            "numeric_fields": sorted(
                field.name
                for field in table.fields
                if field.encoding is FieldEncoding.NUMBER
            ),
            "fields": {
                field.name: {
                    "type": field.type_id,
                    "ui_type": field.ui_type,
                    "encoding": field.encoding.value,
                    "ownership": field.ownership.value,
                    "schema_required": field.schema_required,
                    "clearable": field.clearable,
                    "select_options": list(field.select_options),
                }
                for field in table.fields
            },
            "write_operations": {
                contract.operation.value: {
                    "required_fields": sorted(contract.required_fields),
                    "allowed_fields": sorted(contract.allowed_fields),
                }
                for contract in table.write_contracts
            },
        }
    return {
        "success": True,
        "source": "src.feishu.contracts.TABLE_CONTRACTS",
        "retired_remote_tables": sorted(RETIRED_REMOTE_TABLES),
        "tables": tables,
    }


def _list_live_fields(
    client: FeishuClient,
    app_token: str,
    table_id: str,
) -> list[dict[str, Any]]:
    endpoint = f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields"
    items: list[dict[str, Any]] = []
    page_token: str | None = None
    seen_tokens: set[str] = set()
    while True:
        params: dict[str, Any] = {"page_size": 200}
        if page_token:
            params["page_token"] = page_token
        data = client._request("GET", endpoint, params=params)
        page_items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(page_items, list):
            raise ValueError(f"invalid fields response: items={page_items!r}")
        items.extend(item for item in page_items if isinstance(item, dict))
        if not data.get("has_more"):
            return items
        next_token = str(data.get("page_token") or "").strip()
        if not next_token or next_token in seen_tokens:
            raise ValueError("invalid fields pagination: has_more without a new page_token")
        seen_tokens.add(next_token)
        page_token = next_token


def run_schema_check(strict: bool = False) -> dict[str, Any]:
    """Read live field metadata and compare configured tables with the registry."""
    if not DOCS_SCHEMA.exists():
        raise SystemExit(f"docs/schema.md not found: {DOCS_SCHEMA}")

    client = FeishuClient()
    report: dict[str, Any] = {
        "schema_doc": str(DOCS_SCHEMA),
        "contract_source": "src.feishu.contracts.TABLE_CONTRACTS",
        "tables": {},
        "configured_ok": True,
        "core_ok": True,
        "complete": True,
        "all_ok": True,
        "ok": True,
    }

    for table_name, table in TABLE_CONTRACTS.items():
        blocking = table.role is TableRole.CORE
        try:
            app_token, table_id = client._get_table_config(table_name)
        except ValueError as exc:
            if table.role is TableRole.OPTIONAL:
                report["tables"][table_name] = {
                    "configured": False,
                    "role": table.role.value,
                    "blocking": False,
                    "status": "skipped_unconfigured",
                    "error": str(exc),
                    "ok": None,
                }
                report["complete"] = False
                report["all_ok"] = False
                continue
            report["tables"][table_name] = {
                "configured": False,
                "role": table.role.value,
                "blocking": True,
                "status": "failed",
                "error": str(exc),
                "ok": False,
            }
            report["configured_ok"] = False
            report["core_ok"] = False
            report["all_ok"] = False
            continue

        items = _list_live_fields(client, app_token, table_id)
        comparison = compare_live_schema(table, items)
        status = "passed" if comparison["ok"] else "failed"
        report["tables"][table_name] = {
            "app_token": app_token,
            "table_id": table_id,
            "configured": True,
            "role": table.role.value,
            "blocking": blocking,
            "status": status,
            "live_fields": sorted(
                str(item.get("field_name"))
                for item in items
                if item.get("field_name")
            ),
            **comparison,
        }
        if not comparison["ok"]:
            report["configured_ok"] = False
            report["all_ok"] = False
            if blocking:
                report["core_ok"] = False

    report["ok"] = report["configured_ok"]
    report["all_ok"] = bool(report["all_ok"] and report["complete"])
    if strict and not report["ok"]:
        raise SystemExit(2)
    return report


if __name__ == "__main__":
    raise SystemExit(main())
