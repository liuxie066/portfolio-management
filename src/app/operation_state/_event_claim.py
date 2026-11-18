"""Shared claim transaction for the two Bitable event inboxes."""
from __future__ import annotations

from datetime import timedelta
import json
from typing import Any, Literal
from uuid import uuid4

from ._base import _CLAIM_LEASE_MINUTES, OperationStateBase


def claim_due_inbox_events(
    store: OperationStateBase,
    table: Literal["holding_event_inbox", "cash_flow_event_inbox"],
    *,
    limit: int,
    claim_id: str | None,
) -> list[dict[str, Any]]:
    """Reclaim expired work and claim available events in one transaction."""
    if table not in ("holding_event_inbox", "cash_flow_event_inbox"):
        raise ValueError("unsupported event inbox")

    now_dt = store.now_factory()
    now = now_dt.isoformat()
    lease_cutoff = (now_dt - timedelta(minutes=_CLAIM_LEASE_MINUTES)).isoformat()
    resolved_claim_id = claim_id or uuid4().hex
    claimed = []
    with store._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            f"""
            UPDATE {table}
            SET state = 'failed_retryable', claim_id = NULL, claimed_at = NULL,
                last_error = COALESCE(last_error, 'worker claim expired'),
                next_attempt_at = ?, updated_at = ?
            WHERE state = 'claimed' AND claimed_at <= ?
            """,
            (now, now, lease_cutoff),
        )
        rows = conn.execute(
            f"""
            SELECT * FROM {table}
            WHERE state IN ('pending', 'failed_retryable')
              AND next_attempt_at <= ?
            ORDER BY received_at LIMIT ?
            """,
            (now, int(limit)),
        ).fetchall()
        for row in rows:
            cursor = conn.execute(
                f"""
                UPDATE {table}
                SET state = 'claimed', claim_id = ?, claimed_at = ?, updated_at = ?
                WHERE event_id = ? AND state IN ('pending', 'failed_retryable')
                """,
                (resolved_claim_id, now, now, row["event_id"]),
            )
            if cursor.rowcount == 1:
                claimed.append(row)

    result = []
    for row in claimed:
        item = dict(row)
        item["action_list"] = json.loads(item.pop("action_list_json"))
        item["outcome"] = (
            json.loads(item.pop("outcome_json"))
            if item.get("outcome_json") is not None
            else None
        )
        item["state"] = "claimed"
        item["claim_id"] = resolved_claim_id
        item["claimed_at"] = now
        result.append(item)
    return result
