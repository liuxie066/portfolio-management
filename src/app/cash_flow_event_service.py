"""Exact-target normalization for Feishu cash-flow record-change events."""

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


CASH_FLOW_EVENT_TYPE = BITABLE_RECORD_CHANGED_EVENT_TYPE
CASH_FLOW_SUBSCRIPTION_EVENT_TYPE = BITABLE_SUBSCRIPTION_EVENT_TYPE
CASH_FLOW_FILE_TYPE = BITABLE_FILE_TYPE
ACTIONABLE_CASH_FLOW_ACTIONS = frozenset({"record_added", "record_edited"})
IGNORED_CASH_FLOW_ACTIONS = frozenset({"record_deleted"})


class CashFlowEventTargetMismatch(ValueError):
    """A valid event belongs to another configured resource."""


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

    return NormalizedCashFlowEvent(
        **_normalize_bitable_record_changed_event(
            payload,
            target=target,
            event_label="cash flow event",
            target_mismatch_error=CashFlowEventTargetMismatch,
        )
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
