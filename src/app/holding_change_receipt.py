"""Frozen receipts describing table observations, never inferred trades."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from .notification_shells import render_receipt

RECEIPT_TYPE = "non_futu_holdings_changed"
LABELS = {
    "first_observed": "新增观察持仓", "quantity_changed": "数量变化",
    "quantity_zero": "数量归零", "quantity_restored": "恢复持仓",
    "record_deleted": "记录已删除", "identity_changed": "持仓身份变更",
    "measure_changed": "类型／计量信息变更", "out_of_scope": "移出监控范围",
    "back_in_scope": "重新纳入监控", "record_replaced": "记录重建／替换",
}


def receipts(scope_key: str, epoch: str, seq: int, changes: list[dict]) -> list[dict]:
    groups = defaultdict(list)
    for change in changes:
        snap = change.get("after") or change.get("before") or {}
        account, broker = snap.get("account"), snap.get("broker")
        separate = change["record_id"] if change["kind"] in {"identity_changed", "out_of_scope", "measure_changed"} else ""
        groups[(account or "未归属账户", broker or "未知券商", separate)].append(change)
    result = []
    for group_idx, ((account, broker, _), items) in enumerate(sorted(groups.items())):
        parts = (len(items) + 9) // 10
        for offset in range(0, len(items), 10):
            part = offset // 10 + 1
            result.append({
                "receipt_key": f"holdings:changes:{scope_key}:{epoch}:{seq}:{group_idx}:{part}",
                "receipt_type": RECEIPT_TYPE,
                "payload": {"account": account, "broker": broker, "changes": items[offset:offset + 10],
                            "part": part, "parts": parts, "observation_seq": seq},
            })
    return result


def message(payload: dict[str, Any]) -> str:
    lines = []
    for item in payload["changes"]:
        before, after = item.get("before") or {}, item.get("after") or {}
        label = LABELS[item["kind"]]
        asset = after.get("asset_id") or before.get("asset_id") or item["record_id"]
        if item["kind"] in {"identity_changed", "measure_changed", "out_of_scope", "back_in_scope", "record_replaced"}:
            def describe(snapshot):
                return "/".join(str(snapshot.get(key) or "-") for key in ("account", "broker", "asset_id", "asset_type", "currency")) + f" 数量 {snapshot.get('quantity', '不存在')}"
            body = f"{describe(before)} → {describe(after)}"
        else:
            body = f"数量 {before.get('quantity', '未观察')} → {after.get('quantity', '不存在')}"
        gap = "（间断后观察）" if item.get("after_gap") else ""
        lines.append(f"{label}{gap}｜{asset}：{body}；观测 {item.get('previous_observed_at') or '首次'} → {item['observed_at']}")
    return render_receipt(
        title=payload["account"], receipt_type="Holdings 持仓变动", status="已观察并记录",
        fields=[("券商", payload["broker"]), ("变化", "；".join(lines)),
                ("分片", f"{payload['part']}/{payload['parts']}"),
                ("依据", "飞书 holdings；表内变化，不代表成交确认")],
    )
