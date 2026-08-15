"""Cash Flow Event operations mixin for OperationStateStore."""
from __future__ import annotations

from typing import Any
from typing import Dict
from typing import Optional
from pathlib import Path
import json
import sqlite3
from datetime import timedelta
from uuid import uuid4
from ._base import _CLAIM_LEASE_MINUTES, _RETRY_MINUTES
from .._json import canonical_json as _canonical_json



class CashFlowEventMixin:
    def complete_cash_flow_event(
        self,
        *,
        event_id: str,
        claim_id: str,
        outcome: Dict[str, Any],
        receipts: Optional[list[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Commit a cash-flow event outcome and its receipts together."""

        now = self.now_factory().isoformat()
        enqueued_receipt_keys: list[str] = []
        receipt_keys = [str(item["receipt_key"]) for item in list(receipts or ())]
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            claimed = conn.execute(
                """
                SELECT 1 FROM cash_flow_event_inbox
                WHERE event_id = ? AND state = 'claimed' AND claim_id = ?
                """,
                (event_id, claim_id),
            ).fetchone()
            if not claimed:
                raise KeyError(f"cash flow event claim not found: {event_id}")
            for receipt in list(receipts or ()):
                inserted = self._insert_operation_receipt_tx(
                    conn,
                    receipt_key=str(receipt["receipt_key"]),
                    receipt_type=str(receipt["receipt_type"]),
                    payload=dict(receipt["payload"]),
                    now=now,
                )
                if inserted:
                    enqueued_receipt_keys.append(str(receipt["receipt_key"]))
            persisted_outcome = dict(outcome)
            persisted_outcome["receipt_keys"] = receipt_keys
            persisted_outcome["enqueued_receipt_keys"] = enqueued_receipt_keys
            cursor = conn.execute(
                """
                UPDATE cash_flow_event_inbox
                SET state = 'processed', outcome_json = ?, claim_id = NULL,
                    claimed_at = NULL, last_error = NULL, updated_at = ?
                WHERE event_id = ? AND state = 'claimed' AND claim_id = ?
                """,
                (
                    _canonical_json(persisted_outcome),
                    now,
                    event_id,
                    claim_id,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"cash flow event claim not found: {event_id}")
        return persisted_outcome

    def accept_cash_flow_event(
        self,
        *,
        event_id: str,
        event_type: str,
        file_token: str,
        table_id: str,
        revision: Optional[str],
        action_list: list[Dict[str, Any]],
        payload_digest: str,
    ) -> bool:
        now = self.now_factory().isoformat()
        action_json = _canonical_json(action_list)
        with self._connect_inbox_accept() as conn:
            conn.execute("BEGIN IMMEDIATE")
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO cash_flow_event_inbox(
                    event_id, event_type, file_token, table_id, revision,
                    action_list_json, payload_digest, state, next_attempt_at,
                    received_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                """,
                (
                    event_id,
                    event_type,
                    file_token,
                    table_id,
                    revision,
                    action_json,
                    payload_digest,
                    now,
                    now,
                    now,
                ),
            )
            existing = conn.execute(
                """
                SELECT event_type, file_token, table_id, revision,
                       action_list_json, payload_digest
                FROM cash_flow_event_inbox WHERE event_id = ?
                """,
                (event_id,),
            ).fetchone()
            expected = (
                event_type,
                file_token,
                table_id,
                revision,
                action_json,
                payload_digest,
            )
            actual = tuple(existing) if existing else None
            if actual != expected:
                raise ValueError(
                    f"cash flow event id collision with different payload: {event_id}"
                )
        return cursor.rowcount == 1

    def claim_cash_flow_events(
        self,
        *,
        limit: int = 100,
        claim_id: Optional[str] = None,
    ) -> list[Dict[str, Any]]:
        now_dt = self.now_factory()
        now = now_dt.isoformat()
        lease_cutoff = (now_dt - timedelta(minutes=_CLAIM_LEASE_MINUTES)).isoformat()
        resolved_claim_id = claim_id or uuid4().hex
        claimed: list[sqlite3.Row] = []
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                UPDATE cash_flow_event_inbox
                SET state = 'failed_retryable', claim_id = NULL, claimed_at = NULL,
                    last_error = COALESCE(last_error, 'worker claim expired'),
                    next_attempt_at = ?, updated_at = ?
                WHERE state = 'claimed' AND claimed_at <= ?
                """,
                (now, now, lease_cutoff),
            )
            rows = conn.execute(
                """
                SELECT * FROM cash_flow_event_inbox
                WHERE state IN ('pending', 'failed_retryable')
                  AND next_attempt_at <= ?
                ORDER BY received_at LIMIT ?
                """,
                (now, int(limit)),
            ).fetchall()
            for row in rows:
                cursor = conn.execute(
                    """
                    UPDATE cash_flow_event_inbox
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

    def mark_cash_flow_event_failed(
        self,
        *,
        event_id: str,
        claim_id: str,
        error: str,
        max_attempts: int = 0,
        terminal_outcome: Optional[Dict[str, Any]] = None,
        terminal_receipts: Optional[list[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        now_dt = self.now_factory()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT attempt_count FROM cash_flow_event_inbox
                WHERE event_id = ? AND state = 'claimed' AND claim_id = ?
                """,
                (event_id, claim_id),
            ).fetchone()
            if not row:
                raise KeyError(f"cash flow event claim not found: {event_id}")
            attempt = int(row[0]) + 1
            if max_attempts and attempt >= int(max_attempts):
                receipts = list(terminal_receipts or ())
                if not receipts:
                    raise ValueError(
                        "terminal cash flow event failure requires an attention receipt"
                    )
                enqueued_receipt_keys = []
                receipt_keys = []
                now = now_dt.isoformat()
                for receipt in receipts:
                    receipt_key = str(receipt["receipt_key"])
                    receipt_keys.append(receipt_key)
                    inserted = self._insert_operation_receipt_tx(
                        conn,
                        receipt_key=receipt_key,
                        receipt_type=str(receipt["receipt_type"]),
                        payload=dict(receipt["payload"]),
                        now=now,
                    )
                    if inserted:
                        enqueued_receipt_keys.append(receipt_key)
                persisted_outcome = dict(terminal_outcome or {})
                persisted_outcome.setdefault("status", "attention_required")
                persisted_outcome["attempt_count"] = attempt
                persisted_outcome["receipt_keys"] = receipt_keys
                persisted_outcome["enqueued_receipt_keys"] = enqueued_receipt_keys
                cursor = conn.execute(
                    """
                    UPDATE cash_flow_event_inbox
                    SET state = 'processed', attempt_count = ?, outcome_json = ?,
                        claim_id = NULL, claimed_at = NULL, last_error = ?,
                        updated_at = ?
                    WHERE event_id = ? AND state = 'claimed' AND claim_id = ?
                    """,
                    (
                        attempt,
                        _canonical_json(persisted_outcome),
                        error,
                        now,
                        event_id,
                        claim_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise KeyError(f"cash flow event claim not found: {event_id}")
                return {
                    "state": "processed",
                    "attempt_count": attempt,
                    "outcome": persisted_outcome,
                }
            retry_index = min(attempt - 1, len(_RETRY_MINUTES) - 1)
            next_attempt = now_dt + timedelta(minutes=_RETRY_MINUTES[retry_index])
            conn.execute(
                """
                UPDATE cash_flow_event_inbox
                SET state = 'failed_retryable', attempt_count = ?,
                    next_attempt_at = ?, claim_id = NULL, claimed_at = NULL,
                    last_error = ?, updated_at = ?
                WHERE event_id = ? AND state = 'claimed' AND claim_id = ?
                """,
                (
                    attempt,
                    next_attempt.isoformat(),
                    error,
                    now_dt.isoformat(),
                    event_id,
                    claim_id,
                ),
            )
        return {
            "state": "failed_retryable",
            "attempt_count": attempt,
            "next_attempt_at": next_attempt.isoformat(),
        }

    def get_cash_flow_event(self, event_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM cash_flow_event_inbox WHERE event_id = ?",
                (event_id,),
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["action_list"] = json.loads(item.pop("action_list_json"))
        raw_outcome = item.pop("outcome_json")
        item["outcome"] = json.loads(raw_outcome) if raw_outcome else None
        return item

    @classmethod
    def inspect_cash_flow_event_status(
        cls,
        db_path: Optional[str | Path] = None,
    ) -> Dict[str, Any]:
        """Read cash-flow inbox evidence without creating local state."""

        path = cls.resolve_db_path_read_only(db_path)
        if not path.exists():
            return {
                "db_path": str(path),
                "initialized": False,
                "counts": {},
                "latest": None,
            }
        try:
            connection_uri = f"{path.resolve().as_uri()}?mode=ro"
            conn = sqlite3.connect(connection_uri, uri=True, timeout=1)
            conn.row_factory = sqlite3.Row
            table = conn.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = 'cash_flow_event_inbox'
                """
            ).fetchone()
            if not table:
                return {
                    "db_path": str(path),
                    "initialized": False,
                    "counts": {},
                    "latest": None,
                }
            rows = conn.execute(
                """
                SELECT state, COUNT(*) AS count
                FROM cash_flow_event_inbox GROUP BY state ORDER BY state
                """
            ).fetchall()
            latest = conn.execute(
                """
                SELECT event_id, state, received_at, updated_at, last_error
                FROM cash_flow_event_inbox
                ORDER BY received_at DESC LIMIT 1
                """
            ).fetchone()
            return {
                "db_path": str(path),
                "initialized": True,
                "counts": {str(row["state"]): int(row["count"]) for row in rows},
                "latest": dict(latest) if latest else None,
            }
        except sqlite3.DatabaseError as exc:
            raise RuntimeError(
                f"cash flow event inbox status read failed: {path}: {exc}"
            ) from exc
        finally:
            if "conn" in locals():
                conn.close()

    def cash_flow_event_status(self) -> Dict[str, Any]:
        return self.inspect_cash_flow_event_status(self.db_path)
