"""Nav Receipt operations mixin for OperationStateStore."""
from __future__ import annotations

from typing import Any
from typing import Dict
from typing import Optional
import json
from datetime import timedelta
from uuid import uuid4
from ._base import _CLAIM_LEASE_MINUTES, _RETRY_MINUTES
from .._json import canonical_json as _canonical_json



class NavReceiptMixin:
    def enqueue_nav_receipt(
        self,
        *,
        receipt_key: str,
        payload: Dict[str, Any],
    ) -> bool:
        now = self.now_factory().isoformat()
        payload_json = _canonical_json(payload)
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO nav_receipt_outbox(
                    receipt_key, payload_json, status, next_attempt_at,
                    created_at, updated_at
                ) VALUES (?, ?, 'pending', ?, ?, ?)
                """,
                (receipt_key, payload_json, now, now, now),
            )
            if cursor.rowcount == 0:
                existing = conn.execute(
                    "SELECT payload_json FROM nav_receipt_outbox WHERE receipt_key = ?",
                    (receipt_key,),
                ).fetchone()
                if not existing or existing[0] != payload_json:
                    raise ValueError(
                        "NAV receipt key collision with different payload: "
                        f"{receipt_key}"
                    )
        return cursor.rowcount == 1

    def claim_due_nav_receipts(
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
            SELECT * FROM nav_receipt_outbox
            WHERE status IN ('pending', 'failed') AND next_attempt_at <= ?
        """
        params: list[Any] = [now]
        if receipt_key:
            query += " AND receipt_key = ?"
            params.append(receipt_key)
        query += " ORDER BY created_at LIMIT ?"
        params.append(int(limit))
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                UPDATE nav_receipt_outbox
                SET status = 'failed', claim_id = NULL, claimed_at = NULL,
                    last_error = COALESCE(last_error, 'dispatcher claim expired'),
                    updated_at = ?
                WHERE status = 'sending' AND claimed_at <= ?
                """,
                (now, lease_cutoff),
            )
            rows = conn.execute(query, params).fetchall()
            claimed_rows = []
            for row in rows:
                cursor = conn.execute(
                    """
                    UPDATE nav_receipt_outbox
                    SET status = 'sending', claim_id = ?, claimed_at = ?, updated_at = ?
                    WHERE receipt_key = ?
                      AND status IN ('pending', 'failed')
                      AND next_attempt_at <= ?
                    """,
                    (resolved_claim_id, now, now, row["receipt_key"], now),
                )
                if cursor.rowcount == 1:
                    claimed_rows.append(row)
        result = []
        for row in claimed_rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            item["claim_id"] = resolved_claim_id
            item["status"] = "sending"
            item["claimed_at"] = now
            result.append(item)
        return result

    def list_due_nav_receipts(
        self,
        *,
        limit: int = 100,
        receipt_key: Optional[str] = None,
    ) -> list[Dict[str, Any]]:
        """Read due rows without claiming them; intended for diagnostics only."""
        now = self.now_factory().isoformat()
        query = """
            SELECT * FROM nav_receipt_outbox
            WHERE status IN ('pending', 'failed') AND next_attempt_at <= ?
        """
        params: list[Any] = [now]
        if receipt_key:
            query += " AND receipt_key = ?"
            params.append(receipt_key)
        query += " ORDER BY created_at LIMIT ?"
        params.append(int(limit))
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            result.append(item)
        return result

    def mark_nav_receipt(
        self,
        receipt_key: str,
        *,
        claim_id: str,
        success: bool,
        message_id: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        now_dt = self.now_factory()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT attempt_count FROM nav_receipt_outbox
                WHERE receipt_key = ? AND status = 'sending' AND claim_id = ?
                """,
                (receipt_key, claim_id),
            ).fetchone()
            if not row:
                raise KeyError(
                    f"nav receipt claim not found: {receipt_key} claim_id={claim_id}"
                )
            attempt_count = int(row[0]) + 1
            retry_index = min(max(attempt_count - 1, 0), len(_RETRY_MINUTES) - 1)
            next_attempt = now_dt + timedelta(minutes=_RETRY_MINUTES[retry_index])
            conn.execute(
                """
                UPDATE nav_receipt_outbox
                SET status = ?, attempt_count = ?, next_attempt_at = ?,
                    claim_id = NULL, claimed_at = NULL,
                    message_id = ?, last_error = ?, updated_at = ?
                WHERE receipt_key = ? AND status = 'sending' AND claim_id = ?
                """,
                (
                    "sent" if success else "failed",
                    attempt_count,
                    next_attempt.isoformat(),
                    message_id,
                    error,
                    now_dt.isoformat(),
                    receipt_key,
                    claim_id,
                ),
            )

    def get_nav_receipt(self, receipt_key: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM nav_receipt_outbox WHERE receipt_key = ?",
                (receipt_key,),
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["payload"] = json.loads(result.pop("payload_json"))
        return result
