"""Project a newly verified Futu write into a frozen quantity-change receipt."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from .notification_shells import render_receipt

RECEIPT_TYPE = "futu_refresh_quantity_changed"


def quantity_change_payload(result: dict[str, Any]) -> dict[str, Any] | None:
    if (
        result.get("success") is not True
        or result.get("status") != "written"
        or result.get("dry_run") is not False
        or result.get("source") != "futu-openapi"
        or result.get("partial_write_possible")
        or result.get("receipt_persisted") is not True
        or (result.get("reconciliation") or {}).get("status") != "trusted"
    ):
        return None
    changes = [
        {
            "asset_id": item["asset_id"],
            "before": item["current_quantity"],
            "after": item["target_quantity"],
        }
        for item in result.get("positions", [])
        if item.get("security_type") in {"STOCK", "ETF"}
        and item.get("quantity_changed") is True
    ]
    if not changes:
        return None
    return {
        "account": result["account"],
        "sync_run_id": result["sync_run_id"],
        "source_snapshot_id": result["source_snapshot_id"],
        "observed_at_utc": result["source_metadata"]["observed_at_utc"],
        "changes": changes,
    }


def quantity_change_message(payload: dict[str, Any]) -> str:
    def number(value: Any) -> str:
        quantity = Decimal(str(value))
        return str(int(quantity)) if quantity == quantity.to_integral_value() else format(quantity, "f")

    changes = "；".join(
        f"{item['asset_id']}：数量 {number(item['before'])}→{number(item['after'])}"
        for item in payload["changes"]
    )
    return render_receipt(
        title=payload["account"],
        receipt_type="富途持仓同步",
        status="✅ 成功",
        fields=[("持仓变化", changes)],
    )
