#!/usr/bin/env python3
"""Validate documented Feishu schema expectations."""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.feishu_client import FeishuClient


DOCS_SCHEMA = REPO_ROOT / "docs" / "schema.md"


@dataclass
class TableSpec:
    name: str
    required: dict[str, frozenset[str]]
    optional: dict[str, frozenset[str]]
    role: str = "core"


FIELD_TYPE_IDS = {
    "text": {1},
    "number": {2},
    "select": {3, 4},
    "date": {5},
    "datetime": {5},
    "json-text": {1},
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect documented or live Feishu schema.")
    parser.add_argument(
        "command",
        nargs="?",
        choices=["expectations", "check-live"],
        default="expectations",
        help="schema action (default: expectations)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when required live fields are missing or have incompatible types.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = run_schema_check(strict=args.strict) if args.command == "check-live" else schema_expectations()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok", result.get("success", True)) else 1


def schema_expectations() -> dict:
    specs = parse_docs_schema()
    numeric_fields = {
        "holdings": ["quantity", "avg_cost"],
        "transactions": ["quantity", "price", "amount", "fee", "tax"],
        "cash_flow": ["amount", "cny_amount", "exchange_rate"],
        "nav_history": [
            "total_value", "cash_value", "stock_value", "fund_value",
            "cn_stock_value", "us_stock_value", "hk_stock_value",
            "stock_weight", "cash_weight", "shares", "nav",
            "cash_flow", "share_change", "mtd_nav_change",
            "ytd_nav_change", "pnl", "mtd_pnl", "ytd_pnl",
        ],
        "holdings_snapshot": ["quantity", "avg_cost", "price", "cny_price", "market_value_cny"],
        "compensation_tasks": ["retry_count"],
    }
    expects = {
        table_name: {
            "role": spec.role,
            "required": sorted(spec.required),
            "optional": sorted(spec.optional),
            "field_types": {
                field_name: sorted(accepted)
                for field_name, accepted in sorted({**spec.required, **spec.optional}.items())
            },
            "numeric_fields": numeric_fields.get(table_name, []),
        }
        for table_name, spec in specs.items()
    }
    return {"success": True, "tables": expects}


def _normalize_doc_type(raw_type: str) -> frozenset[str]:
    accepted = []
    for value in str(raw_type or "").lower().split("/"):
        family = value.strip()
        if family == "json":
            family = "json-text"
        if family:
            accepted.append(family)
    return frozenset(accepted)


def parse_docs_schema(path: Path = DOCS_SCHEMA) -> dict[str, TableSpec]:
    """Parse docs/schema.md into expected Feishu field names and type families."""
    text = path.read_text(encoding="utf-8")
    tables: dict[str, TableSpec] = {}
    cur_table: str | None = None
    mode: str | None = None
    heading_re = re.compile(r"^###\s+([a-zA-Z0-9_]+)\s*$")

    for line in text.splitlines():
        heading = heading_re.match(line.strip())
        if heading:
            cur_table = heading.group(1)
            tables[cur_table] = TableSpec(name=cur_table, required={}, optional={})
            mode = None
            continue

        if cur_table is None:
            continue

        lowered = line.strip().lower()
        if lowered.startswith("role:"):
            role = lowered.split(":", 1)[1].strip()
            tables[cur_table].role = "optional" if role.startswith("optional") else "core"
            mode = None
            continue
        if lowered.startswith("required fields"):
            mode = "required"
            continue
        if lowered.startswith("optional fields"):
            mode = "optional"
            continue

        stripped = line.strip()
        if stripped and not stripped.startswith("-"):
            mode = None
            continue

        field = re.match(r"^-\s+`([^`]+)`\s+\(([^)]+)\)", stripped)
        if field and mode in ("required", "optional"):
            field_name = field.group(1).strip()
            target = tables[cur_table].required if mode == "required" else tables[cur_table].optional
            target[field_name] = _normalize_doc_type(field.group(2))

    return tables


def _live_field_matches(item: dict[str, Any], accepted: frozenset[str]) -> bool:
    if not accepted:
        return True
    try:
        type_id = int(item.get("type"))
    except (TypeError, ValueError):
        return False
    ui_type = str(item.get("ui_type") or "").strip().lower()
    for family in accepted:
        if type_id not in FIELD_TYPE_IDS.get(family, set()):
            continue
        if family == "datetime" and ui_type != "datetime":
            continue
        return True
    return False


def _list_live_fields(client: FeishuClient, app_token: str, table_id: str) -> list[dict[str, Any]]:
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
    if not DOCS_SCHEMA.exists():
        raise SystemExit(f"docs/schema.md not found: {DOCS_SCHEMA}")

    specs = parse_docs_schema(DOCS_SCHEMA)
    client = FeishuClient()

    report: dict[str, Any] = {
        "schema_doc": str(DOCS_SCHEMA),
        "tables": {},
        "all_ok": True,
        "core_ok": True,
        "ok": True,
    }

    for table_name, spec in specs.items():
        blocking = spec.role == "core"
        try:
            app_token, table_id = client._get_table_config(table_name)
        except Exception as e:
            report["tables"][table_name] = {
                "configured": False,
                "role": spec.role,
                "blocking": blocking,
                "error": str(e),
                "required": sorted(spec.required),
                "optional": sorted(spec.optional),
                "ok": not blocking,
            }
            report["all_ok"] = False
            if blocking:
                report["core_ok"] = False
                report["ok"] = False
            continue

        items = _list_live_fields(client, app_token, table_id)
        live_by_name = {
            str(item.get("field_name")): item
            for item in items
            if item.get("field_name")
        }
        live_fields = set(live_by_name)

        missing_required = sorted(set(spec.required) - live_fields)
        extra_fields = sorted(live_fields - (set(spec.required) | set(spec.optional)))
        required_type_mismatches = [
            {
                "field_name": field_name,
                "expected": sorted(accepted),
                "live_type": live_by_name[field_name].get("type"),
                "live_ui_type": live_by_name[field_name].get("ui_type"),
            }
            for field_name, accepted in sorted(spec.required.items())
            if field_name in live_by_name and not _live_field_matches(live_by_name[field_name], accepted)
        ]
        optional_type_mismatches = [
            {
                "field_name": field_name,
                "expected": sorted(accepted),
                "live_type": live_by_name[field_name].get("type"),
                "live_ui_type": live_by_name[field_name].get("ui_type"),
            }
            for field_name, accepted in sorted(spec.optional.items())
            if field_name in live_by_name and not _live_field_matches(live_by_name[field_name], accepted)
        ]
        ok = not missing_required and (not strict or not required_type_mismatches)

        report["tables"][table_name] = {
            "app_token": app_token,
            "table_id": table_id,
            "role": spec.role,
            "blocking": blocking,
            "required": sorted(spec.required),
            "optional": sorted(spec.optional),
            "live_fields": sorted(live_fields),
            "missing_required": missing_required,
            "extra_fields": extra_fields,
            "required_type_mismatches": required_type_mismatches,
            "optional_type_mismatches": optional_type_mismatches,
            "ok": ok,
        }

        if not ok:
            report["all_ok"] = False
            if blocking:
                report["core_ok"] = False
                report["ok"] = False

    if strict and not report["ok"]:
        raise SystemExit(2)
    return report


if __name__ == "__main__":
    raise SystemExit(main())
