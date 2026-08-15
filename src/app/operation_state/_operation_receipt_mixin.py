"""Operation Receipt operations mixin for OperationStateStore."""
from __future__ import annotations

from typing import Any
from typing import Dict
from typing import Optional
import json
import sqlite3
from datetime import timedelta
from uuid import uuid4
from ._base import _CLAIM_LEASE_MINUTES, _RETRY_MINUTES
from .._json import canonical_json as _canonical_json



class OperationReceiptMixin:
    @staticmethod
    def _insert_operation_receipt_tx(
        conn: sqlite3.Connection,
        *,
        receipt_key: str,
        receipt_type: str,
        payload: Dict[str, Any],
        now: str,
    ) -> bool:
        if receipt_type not in {
            "cash_flow_reconcile_attention_required",
            "holding_case_discovered",
            "holding_case_closed",
            "holding_case_attention_required",
        }:
            raise ValueError(f"unsupported operation receipt type: {receipt_type}")
        payload_json = _canonical_json(payload)
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO operation_receipt_outbox(
                receipt_key, receipt_type, payload_json, status,
                next_attempt_at, created_at, updated_at
            ) VALUES (?, ?, ?, 'pending', ?, ?, ?)
            """,
            (receipt_key, receipt_type, payload_json, now, now, now),
        )
        if cursor.rowcount == 0:
            existing = conn.execute(
                """
                SELECT receipt_type, payload_json FROM operation_receipt_outbox
                WHERE receipt_key = ?
                """,
                (receipt_key,),
            ).fetchone()
            if (
                not existing
                or existing["receipt_type"] != receipt_type
                or existing["payload_json"] != payload_json
            ):
                raise ValueError(
                    "operation receipt key collision with different payload: "
                    f"{receipt_key}"
                )
        return cursor.rowcount == 1

    @classmethod
    def _insert_repeatable_closure_receipt_tx(
        cls,
        conn: sqlite3.Connection,
        *,
        receipt_key: str,
        payload: Dict[str, Any],
        now: str,
    ) -> bool:
        """Keep the first frozen payload when a reopened lifecycle closes identically."""

        existing = conn.execute(
            "SELECT receipt_type FROM operation_receipt_outbox WHERE receipt_key = ?",
            (receipt_key,),
        ).fetchone()
        if existing:
            if existing["receipt_type"] != "holding_case_closed":
                raise ValueError(
                    "operation receipt key collision with different type: "
                    f"{receipt_key}"
                )
            return False
        return cls._insert_operation_receipt_tx(
            conn,
            receipt_key=receipt_key,
            receipt_type="holding_case_closed",
            payload=payload,
            now=now,
        )

    def enqueue_operation_receipt(
        self,
        *,
        receipt_key: str,
        receipt_type: str,
        payload: Dict[str, Any],
    ) -> bool:
        now = self.now_factory().isoformat()
        with self._connect() as conn:
            return self._insert_operation_receipt_tx(
                conn,
                receipt_key=receipt_key,
                receipt_type=receipt_type,
                payload=payload,
                now=now,
            )

    @staticmethod
    def _decode_operation_receipt(row: sqlite3.Row | Dict[str, Any]) -> Dict[str, Any]:
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json"))
        return item

    def claim_due_operation_receipts(
        self,
        *,
        limit: int = 100,
        receipt_key: Optional[str] = None,
        claim_id: Optional[str] = None,
    ) -> list[Dict[str, Any]]:
        now_dt = self.now_factory()
        now = now_dt.isoformat()
        lease_cutoff = (now_dt - timedelta(minutes=_CLAIM_LEASE_MINUTES)).isoformat()
        resolved_claim_id = claim_id or uuid4().hex
        query = """
            SELECT * FROM operation_receipt_outbox
            WHERE status IN ('pending', 'failed') AND next_attempt_at <= ?
        """
        params: list[Any] = [now]
        if receipt_key:
            query += " AND receipt_key = ?"
            params.append(receipt_key)
        query += " ORDER BY created_at LIMIT ?"
        params.append(int(limit))
        claimed: list[sqlite3.Row] = []
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                UPDATE operation_receipt_outbox
                SET status = 'failed', claim_id = NULL, claimed_at = NULL,
                    last_error = COALESCE(last_error, 'dispatcher claim expired'),
                    next_attempt_at = ?, updated_at = ?
                WHERE status = 'claimed' AND claimed_at <= ?
                """,
                (now, now, lease_cutoff),
            )
            conn.execute(
                """
                UPDATE operation_receipt_outbox
                SET status = 'unknown', claim_id = NULL,
                    last_error = COALESCE(last_error, 'sending lease expired'),
                    updated_at = ?
                WHERE status = 'sending' AND send_started_at <= ?
                """,
                (now, lease_cutoff),
            )
            rows = conn.execute(query, params).fetchall()
            for row in rows:
                cursor = conn.execute(
                    """
                    UPDATE operation_receipt_outbox
                    SET status = 'claimed', claim_id = ?, claimed_at = ?, updated_at = ?
                    WHERE receipt_key = ?
                      AND status IN ('pending', 'failed')
                      AND next_attempt_at <= ?
                    """,
                    (resolved_claim_id, now, now, row["receipt_key"], now),
                )
                if cursor.rowcount == 1:
                    claimed.append(row)
        result = []
        for row in claimed:
            item = self._decode_operation_receipt(row)
            item.update(
                {"status": "claimed", "claim_id": resolved_claim_id, "claimed_at": now}
            )
            result.append(item)
        return result

    def start_operation_receipt_send(self, *, receipt_key: str, claim_id: str) -> None:
        now = self.now_factory().isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE operation_receipt_outbox
                SET status = 'sending', send_started_at = ?, updated_at = ?
                WHERE receipt_key = ? AND status = 'claimed' AND claim_id = ?
                """,
                (now, now, receipt_key, claim_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(
                    f"operation receipt claim not found: {receipt_key} claim_id={claim_id}"
                )

    def mark_operation_receipt(
        self,
        *,
        receipt_key: str,
        claim_id: str,
        outcome: str,
        message_id: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        if outcome not in {"sent", "failed", "unknown"}:
            raise ValueError(f"unsupported receipt outcome: {outcome}")
        now_dt = self.now_factory()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT attempt_count FROM operation_receipt_outbox
                WHERE receipt_key = ? AND status = 'sending' AND claim_id = ?
                """,
                (receipt_key, claim_id),
            ).fetchone()
            if not row:
                raise KeyError(
                    f"operation receipt send not found: {receipt_key} claim_id={claim_id}"
                )
            attempt = int(row[0]) + 1
            retry_index = min(attempt - 1, len(_RETRY_MINUTES) - 1)
            next_attempt = now_dt + timedelta(minutes=_RETRY_MINUTES[retry_index])
            conn.execute(
                """
                UPDATE operation_receipt_outbox
                SET status = ?, attempt_count = ?, next_attempt_at = ?,
                    claim_id = NULL, claimed_at = NULL, message_id = ?,
                    last_error = ?, updated_at = ?
                WHERE receipt_key = ? AND status = 'sending' AND claim_id = ?
                """,
                (
                    outcome,
                    attempt,
                    next_attempt.isoformat(),
                    message_id,
                    error,
                    now_dt.isoformat(),
                    receipt_key,
                    claim_id,
                ),
            )

    def resolve_operation_receipt(
        self,
        *,
        receipt_key: str,
        decision: str,
        operator_context: Dict[str, Any],
    ) -> None:
        if decision not in {"retry", "mark-sent"}:
            raise ValueError(f"unsupported receipt resolution: {decision}")
        now = self.now_factory().isoformat()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT status FROM operation_receipt_outbox WHERE receipt_key = ?",
                (receipt_key,),
            ).fetchone()
            if not row:
                raise KeyError(f"operation receipt not found: {receipt_key}")
            if row["status"] != "unknown":
                raise ValueError(
                    f"operation receipt is not unknown: {receipt_key}: {row['status']}"
                )
            conn.execute(
                """
                UPDATE operation_receipt_outbox
                SET status = ?, next_attempt_at = ?, last_error = ?, updated_at = ?
                WHERE receipt_key = ? AND status = 'unknown'
                """,
                (
                    "failed" if decision == "retry" else "sent",
                    now,
                    _canonical_json(
                        {"decision": decision, "operator_context": operator_context}
                    ),
                    now,
                    receipt_key,
                ),
            )

    def get_operation_receipt(self, receipt_key: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM operation_receipt_outbox WHERE receipt_key = ?",
                (receipt_key,),
            ).fetchone()
        return self._decode_operation_receipt(row) if row else None
