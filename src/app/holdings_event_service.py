"""Exact-target normalization for Feishu holdings record-change events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from src import config

from .bitable_event_contract import (
    BITABLE_FILE_TYPE,
    BITABLE_RECORD_CHANGED_EVENT_TYPE,
    BITABLE_SUBSCRIPTION_EVENT_TYPE,
    MAX_EVENT_ACTIONS as MAX_EVENT_ACTIONS,
    MAX_EVENT_IDENTIFIER_LENGTH as MAX_EVENT_IDENTIFIER_LENGTH,
    MAX_EVENT_PAYLOAD_BYTES as MAX_EVENT_PAYLOAD_BYTES,
    _normalize_bitable_record_changed_event,
)

HOLDINGS_EVENT_TYPE = BITABLE_RECORD_CHANGED_EVENT_TYPE
HOLDINGS_SUBSCRIPTION_EVENT_TYPE = BITABLE_SUBSCRIPTION_EVENT_TYPE
HOLDINGS_FILE_TYPE = BITABLE_FILE_TYPE
ACTIONABLE_HOLDING_ACTIONS = frozenset({"record_added", "record_edited"})
IGNORED_HOLDING_ACTIONS = frozenset({"record_deleted"})


class HoldingEventTargetMismatch(ValueError):
    """A valid event belongs to another configured resource."""


@dataclass(frozen=True)
class HoldingsEventTarget:
    app_id: str
    file_token: str
    table_id: str
    event_type: str = HOLDINGS_EVENT_TYPE

    @classmethod
    def from_config(cls) -> "HoldingsEventTarget":
        app_id = str(config.get("feishu.listener.app_id") or "").strip()
        if not app_id:
            raise ValueError("missing feishu.listener.app_id for holdings event listener")
        file_token, table_id = config.get_feishu_table_ref("holdings")
        return cls(app_id=app_id, file_token=file_token, table_id=table_id)

    def as_dict(self) -> Dict[str, str]:
        return {
            "app_id": self.app_id,
            "file_token": self.file_token,
            "table_id": self.table_id,
            "event_type": self.event_type,
        }


@dataclass(frozen=True)
class NormalizedHoldingEvent:
    event_id: str
    event_type: str
    file_token: str
    table_id: str
    revision: Optional[str]
    action_list: tuple[Dict[str, str], ...]
    payload_digest: str
    create_time: Optional[str]


def normalize_holding_event(
    payload: Dict[str, Any],
    *,
    target: HoldingsEventTarget,
) -> NormalizedHoldingEvent:
    """Validate exact routing and freeze trigger-only action metadata."""

    return NormalizedHoldingEvent(
        **_normalize_bitable_record_changed_event(
            payload,
            target=target,
            event_label="holding event",
            target_mismatch_error=HoldingEventTargetMismatch,
        )
    )
