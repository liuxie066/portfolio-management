"""Exact-target normalization for Feishu cash-flow record-change events."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Dict, Optional

from src import config

from .bitable_event_contract import (
    BITABLE_FILE_TYPE,
    BITABLE_RECORD_CHANGED_EVENT_TYPE,
    BITABLE_SUBSCRIPTION_EVENT_TYPE,
    MAX_EVENT_ACTIONS,
    MAX_EVENT_IDENTIFIER_LENGTH,
    MAX_EVENT_PAYLOAD_BYTES,
)


CASH_FLOW_EVENT_TYPE = BITABLE_RECORD_CHANGED_EVENT_TYPE
CASH_FLOW_SUBSCRIPTION_EVENT_TYPE = BITABLE_SUBSCRIPTION_EVENT_TYPE
CASH_FLOW_FILE_TYPE = BITABLE_FILE_TYPE
ACTIONABLE_CASH_FLOW_ACTIONS = frozenset({"record_added", "record_edited"})
IGNORED_CASH_FLOW_ACTIONS = frozenset({"record_deleted"})


class CashFlowEventTargetMismatch(ValueError):
    """A valid event belongs to another configured resource."""


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


@dataclass(frozen=True)
class CashFlowEventTarget:
    app_id: str
    file_token: str
    table_id: str
    event_type: str = CASH_FLOW_EVENT_TYPE

    @classmethod
    def from_config(cls) -> "CashFlowEventTarget":
        app_id = str(config.get("feishu.listener.app_id") or "").strip()
        if not app_id:
            raise ValueError("missing feishu.listener.app_id for cash flow event listener")
        file_token, table_id = config.get_feishu_table_ref("cash_flow")
        return cls(app_id=app_id, file_token=file_token, table_id=table_id)

    def as_dict(self) -> Dict[str, str]:
        return {
            "app_id": self.app_id,
            "file_token": self.file_token,
            "table_id": self.table_id,
            "event_type": self.event_type,
        }


@dataclass(frozen=True)
class NormalizedCashFlowEvent:
    event_id: str
    event_type: str
    file_token: str
    table_id: str
    revision: Optional[str]
    action_list: tuple[Dict[str, str], ...]
    payload_digest: str
    create_time: Optional[str]


def normalize_cash_flow_event(
    payload: Dict[str, Any],
    *,
    target: CashFlowEventTarget,
) -> NormalizedCashFlowEvent:
    """Validate exact routing and freeze trigger-only action metadata."""

    if not isinstance(payload, dict):
        raise ValueError("cash flow event payload must be an object")
    canonical_payload = _canonical_json(payload)
    if len(canonical_payload.encode("utf-8")) > MAX_EVENT_PAYLOAD_BYTES:
        raise ValueError("cash flow event payload exceeds the receiver limit")
    if str(payload.get("schema") or "").strip() != "2.0":
        raise ValueError("cash flow event must use Feishu event schema 2.0")
    header = payload.get("header")
    event = payload.get("event")
    if not isinstance(header, dict) or not isinstance(event, dict):
        raise ValueError("cash flow event lacks header or event object")

    event_id = str(header.get("event_id") or "").strip()
    event_type = str(header.get("event_type") or "").strip()
    app_id = str(header.get("app_id") or "").strip()
    file_token = str(event.get("file_token") or "").strip()
    file_type = str(event.get("file_type") or "").strip()
    table_id = str(event.get("table_id") or "").strip()
    if not event_id:
        raise ValueError("cash flow event lacks header.event_id")
    identifiers = (event_id, event_type, app_id, file_token, file_type, table_id)
    if any(len(item) > MAX_EVENT_IDENTIFIER_LENGTH for item in identifiers):
        raise ValueError("cash flow event identifier exceeds the receiver limit")
    actual_target = (app_id, event_type, file_token, file_type, table_id)
    expected_target = (
        target.app_id,
        target.event_type,
        target.file_token,
        CASH_FLOW_FILE_TYPE,
        target.table_id,
    )
    if actual_target != expected_target:
        raise CashFlowEventTargetMismatch(
            "cash flow event target does not match configured app/base/table"
        )

    raw_actions = event.get("action_list")
    if not isinstance(raw_actions, list) or not raw_actions:
        raise ValueError("cash flow event action_list must be a nonempty list")
    if len(raw_actions) > MAX_EVENT_ACTIONS:
        raise ValueError("cash flow event action_list exceeds the receiver limit")
    frozen_actions: list[Dict[str, str]] = []
    for raw_action in raw_actions:
        if not isinstance(raw_action, dict):
            raise ValueError("cash flow event action must be an object")
        action = str(raw_action.get("action") or "").strip()
        record_id = str(raw_action.get("record_id") or "").strip()
        if not action or not record_id:
            raise ValueError("cash flow event action lacks action or record_id")
        if max(len(action), len(record_id)) > MAX_EVENT_IDENTIFIER_LENGTH:
            raise ValueError("cash flow event action identifier exceeds the receiver limit")
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
    return NormalizedCashFlowEvent(
        event_id=event_id,
        event_type=event_type,
        file_token=file_token,
        table_id=table_id,
        revision=revision,
        action_list=action_list,
        payload_digest=hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest(),
        create_time=(str(header.get("create_time") or "").strip() or None),
    )


__all__ = [
    "ACTIONABLE_CASH_FLOW_ACTIONS",
    "CASH_FLOW_EVENT_TYPE",
    "CASH_FLOW_FILE_TYPE",
    "CASH_FLOW_SUBSCRIPTION_EVENT_TYPE",
    "CashFlowEventTarget",
    "CashFlowEventTargetMismatch",
    "NormalizedCashFlowEvent",
    "normalize_cash_flow_event",
]
