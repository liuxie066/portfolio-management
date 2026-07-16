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
    required: set[str]
    optional: set[str]
    role: str = "core"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect documented or live Feishu schema.")
    parser.add_argument(
        "command",
        nargs="?",
        choices=["expectations", "check-live"],
        default="expectations",
        help="schema action (default: expectations)",
    )
    parser.add_argument("--strict", action="store_true", help="Exit non-zero when required live fields are missing.")
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
            "numeric_fields": numeric_fields.get(table_name, []),
        }
        for table_name, spec in specs.items()
    }
    return {"success": True, "tables": expects}


def parse_docs_schema(path: Path = DOCS_SCHEMA) -> dict[str, TableSpec]:
    """Parse docs/schema.md into expected Feishu field names."""
    text = path.read_text(encoding="utf-8")
    tables: dict[str, TableSpec] = {}
    cur_table: str | None = None
    mode: str | None = None
    heading_re = re.compile(r"^###\s+([a-zA-Z0-9_]+)\s*$")

    for line in text.splitlines():
        heading = heading_re.match(line.strip())
        if heading:
            cur_table = heading.group(1)
            tables[cur_table] = TableSpec(name=cur_table, required=set(), optional=set())
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

        field = re.match(r"^-\s+`([^`]+)`", stripped)
        if field and mode in ("required", "optional"):
            field_name = field.group(1).strip()
            target = tables[cur_table].required if mode == "required" else tables[cur_table].optional
            target.add(field_name)

    return tables


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

        endpoint = f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields"
        data = client._request("GET", endpoint, params={"page_size": 200})
        items = data.get("items", [])
        live_fields = {item.get("field_name") for item in items if item.get("field_name")}

        missing_required = sorted(spec.required - live_fields)
        extra_fields = sorted(live_fields - (spec.required | spec.optional))
        ok = len(missing_required) == 0

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
