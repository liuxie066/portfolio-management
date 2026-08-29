"""Protocol contract shared by exact Feishu Bitable event handlers."""

from __future__ import annotations

import hashlib
from typing import Any

from ._json import canonical_json as _canonical_json

BITABLE_RECORD_CHANGED_EVENT_TYPE = "drive.file.bitable_record_changed_v1"
BITABLE_SUBSCRIPTION_EVENT_TYPE = "bitable_record_changed_v1"
BITABLE_FILE_TYPE = "bitable"
MAX_EVENT_PAYLOAD_BYTES = 1_000_000
MAX_EVENT_ACTIONS = 500
MAX_EVENT_IDENTIFIER_LENGTH = 256


def _normalize_bitable_record_changed_event(
    payload: dict[str, Any],
    *,
    target: Any,
    event_label: str,
    target_mismatch_error: type[ValueError],
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError(f"{event_label} payload must be an object")
    canonical_payload = _canonical_json(payload)
    if len(canonical_payload.encode("utf-8")) > MAX_EVENT_PAYLOAD_BYTES:
        raise ValueError(f"{event_label} payload exceeds the receiver limit")
    if str(payload.get("schema") or "").strip() != "2.0":
        raise ValueError(f"{event_label} must use Feishu event schema 2.0")
    header = payload.get("header")
    event = payload.get("event")
    if not isinstance(header, dict) or not isinstance(event, dict):
        raise ValueError(f"{event_label} lacks header or event object")

    event_id = str(header.get("event_id") or "").strip()
    event_type = str(header.get("event_type") or "").strip()
    app_id = str(header.get("app_id") or "").strip()
    file_token = str(event.get("file_token") or "").strip()
    file_type = str(event.get("file_type") or "").strip()
    table_id = str(event.get("table_id") or "").strip()
    if not event_id:
        raise ValueError(f"{event_label} lacks header.event_id")
    identifiers = (event_id, event_type, app_id, file_token, file_type, table_id)
    if any(len(item) > MAX_EVENT_IDENTIFIER_LENGTH for item in identifiers):
        raise ValueError(f"{event_label} identifier exceeds the receiver limit")
    actual_target = (app_id, event_type, file_token, file_type, table_id)
    expected_target = (
        target.app_id,
        target.event_type,
        target.file_token,
        BITABLE_FILE_TYPE,
        target.table_id,
    )
    if actual_target != expected_target:
        raise target_mismatch_error(
            f"{event_label} target does not match configured app/base/table"
        )

    raw_actions = event.get("action_list")
    if not isinstance(raw_actions, list) or not raw_actions:
        raise ValueError(f"{event_label} action_list must be a nonempty list")
    if len(raw_actions) > MAX_EVENT_ACTIONS:
        raise ValueError(f"{event_label} action_list exceeds the receiver limit")
    frozen_actions: list[dict[str, str]] = []
    for raw_action in raw_actions:
        if not isinstance(raw_action, dict):
            raise ValueError(f"{event_label} action must be an object")
        action = str(raw_action.get("action") or "").strip()
        record_id = str(raw_action.get("record_id") or "").strip()
        if not action or not record_id:
            raise ValueError(f"{event_label} action lacks action or record_id")
        if max(len(action), len(record_id)) > MAX_EVENT_IDENTIFIER_LENGTH:
            raise ValueError(
                f"{event_label} action identifier exceeds the receiver limit"
            )
        frozen_actions.append({"action": action, "record_id": record_id})
    action_list = tuple(
        sorted(
            {
                (item["action"], item["record_id"]): item
                for item in frozen_actions
            }.values(),
            key=lambda item: (item["record_id"], item["action"]),
        )
    )
    revision_value = event.get("revision")
    revision = (
        str(revision_value).strip()
        if revision_value not in (None, "")
        else None
    )
    return {
        "event_id": event_id,
        "event_type": event_type,
        "file_token": file_token,
        "table_id": table_id,
        "revision": revision,
        "action_list": action_list,
        "payload_digest": hashlib.sha256(
            canonical_payload.encode("utf-8")
        ).hexdigest(),
        "create_time": str(header.get("create_time") or "").strip() or None,
    }


__all__ = [
    "BITABLE_FILE_TYPE",
    "BITABLE_RECORD_CHANGED_EVENT_TYPE",
    "BITABLE_SUBSCRIPTION_EVENT_TYPE",
    "MAX_EVENT_ACTIONS",
    "MAX_EVENT_IDENTIFIER_LENGTH",
    "MAX_EVENT_PAYLOAD_BYTES",
]
