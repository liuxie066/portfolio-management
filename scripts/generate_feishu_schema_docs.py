#!/usr/bin/env python3
"""Generate the registry-owned field-contract projection in docs/schema.md."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.feishu.contracts import RETIRED_REMOTE_TABLES, TABLE_CONTRACTS  # noqa: E402


SCHEMA_PATH = REPO_ROOT / "docs" / "schema.md"
BEGIN_MARKER = "<!-- BEGIN GENERATED FEISHU CONTRACTS -->"
END_MARKER = "<!-- END GENERATED FEISHU CONTRACTS -->"


def _render_contract_block() -> str:
    lines = [
        BEGIN_MARKER,
        "## Generated Field Contracts",
        "",
        "Generated from `src.feishu.contracts.TABLE_CONTRACTS`; do not edit this block by hand.",
        "",
    ]
    for table in TABLE_CONTRACTS.values():
        business_key = ", ".join(f"`{name}`" for name in table.business_key) or "none"
        lines.extend([
            f"### `{table.name}` contract",
            "",
            f"- Role: `{table.role.value}`",
            f"- Business key: {business_key}",
            "",
            "| Field | Type ID | UI type | Encoding | Presence | Ownership | Clearable | Select options |",
            "|---|---:|---|---|---|---|---|---|",
        ])
        for field in table.fields:
            options = ", ".join(f"`{value}`" for value in field.select_options)
            lines.append(
                f"| `{field.name}` | {field.type_id} | `{field.ui_type}` | "
                f"`{field.encoding.value}` | "
                f"`{'required' if field.schema_required else 'optional'}` | "
                f"`{field.ownership.value}` | `{'yes' if field.clearable else 'no'}` | "
                f"{options} |"
            )
        lines.extend(["", "Write contracts:", ""])
        if table.write_contracts:
            for contract in table.write_contracts:
                required = ", ".join(
                    f"`{name}`" for name in sorted(contract.required_fields)
                ) or "none"
                lines.append(
                    f"- `{contract.operation.value}` row-required fields: {required}"
                )
        else:
            lines.append("- none (read-only table)")
        if table.forbidden_fields:
            forbidden = ", ".join(
                f"`{name}`" for name in sorted(table.forbidden_fields)
            )
            lines.extend(["", f"Forbidden fields: {forbidden}."])
        lines.append("")
    retired = ", ".join(f"`{name}`" for name in sorted(RETIRED_REMOTE_TABLES))
    lines.append(f"{retired} is retired as a remote table; its storage is local-only.")
    lines.append(END_MARKER)
    return "\n".join(lines)


def generate_document(current: str) -> str:
    block = _render_contract_block()
    if BEGIN_MARKER in current or END_MARKER in current:
        if current.count(BEGIN_MARKER) != 1 or current.count(END_MARKER) != 1:
            raise ValueError("schema document must contain exactly one generated marker pair")
        before, remainder = current.split(BEGIN_MARKER, 1)
        _old, after = remainder.split(END_MARKER, 1)
        return before.rstrip() + "\n\n" + block + "\n\n" + after.lstrip()
    anchor = "## Active Tables"
    if anchor not in current:
        raise ValueError(f"schema document is missing insertion anchor: {anchor}")
    return current.replace(anchor, block + "\n\n" + anchor, 1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    current = SCHEMA_PATH.read_text(encoding="utf-8")
    generated = generate_document(current)
    if args.check:
        if generated != current:
            print(f"generated Feishu schema docs are stale: {SCHEMA_PATH}", file=sys.stderr)
            return 1
        return 0
    SCHEMA_PATH.write_text(generated, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
