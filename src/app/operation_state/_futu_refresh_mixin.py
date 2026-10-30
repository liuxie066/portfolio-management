"""Durable OM refresh admission; execution belongs to the exclusive worker."""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from .._json import canonical_json
from ._receipt_tx import insert_operation_receipt_tx


class FutuRefreshMixin:
    def accept_futu_refresh(self, *, account: str, request_id: str) -> None:
        now = self.now_factory().isoformat()
        with self._connect_inbox_accept() as conn:
            conn.execute(
                """
                INSERT INTO futu_refresh_inbox(
                    account, request_id, state, next_attempt_at, created_at, updated_at
                ) VALUES (?, ?, 'pending', ?, ?, ?)
                ON CONFLICT(account, request_id) DO NOTHING
                """,
                (account, request_id, now, now, now),
            )

    def recover_futu_refreshes(self) -> None:
        """Only call under worker lifetime ownership, with no sync in flight."""
        now = self.now_factory().isoformat()
        with self._connect_inbox_accept() as conn:
            conn.execute(
                """
                UPDATE futu_refresh_inbox SET state = 'pending',
                    next_attempt_at = ?, updated_at = ? WHERE state = 'running'
                """,
                (now, now),
            )

    def claim_futu_refresh(self) -> dict[str, Any] | None:
        now = self.now_factory().isoformat()
        with self._connect_inbox_accept() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT * FROM futu_refresh_inbox
                WHERE state = 'pending' AND next_attempt_at <= ?
                ORDER BY next_attempt_at, account, request_id LIMIT 1
                """,
                (now,),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                """
                UPDATE futu_refresh_inbox SET state = 'running',
                    attempt_count = attempt_count + 1, updated_at = ?
                WHERE account = ? AND request_id = ? AND state = 'pending'
                """,
                (now, row["account"], row["request_id"]),
            )
        return dict(row)

    def settle_futu_refresh(
        self, *, account: str, request_id: str, result: dict[str, Any],
        receipt_payload: dict[str, Any] | None = None,
    ) -> None:
        now = self.now_factory()
        success = result.get("success") is True
        with self._connect_inbox_accept() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT attempt_count, state, result_json FROM futu_refresh_inbox
                WHERE account = ? AND request_id = ?
                """,
                (account, request_id),
            ).fetchone()
            if row is not None and row["state"] == "completed":
                # A committed settlement may have lost its acknowledgement.
                if row["result_json"] == canonical_json(result):
                    return
                raise ValueError("completed Futu refresh has a different result")
            if row is None or row["state"] != "running":
                raise KeyError(f"running Futu refresh not found: {account}/{request_id}")
            if success and receipt_payload is not None:
                if receipt_payload["account"] != account:
                    raise ValueError("Futu receipt account does not match refresh")
                insert_operation_receipt_tx(
                    conn,
                    receipt_key=f"futu-refresh-quantity:{account}:{request_id}",
                    receipt_type="futu_refresh_quantity_changed",
                    payload=receipt_payload,
                    now=now.isoformat(),
                )
            delay = (1, 5, 15, 60)[min(int(row[0]) - 1, 3)]
            conn.execute(
                """
                UPDATE futu_refresh_inbox SET state = ?, result_json = ?,
                    last_error = ?, next_attempt_at = ?, updated_at = ?
                WHERE account = ? AND request_id = ? AND state = 'running'
                """,
                (
                    "completed" if success else "pending", canonical_json(result),
                    None if success else str(result.get("error") or "sync unsuccessful"),
                    (now + timedelta(minutes=delay)).isoformat(), now.isoformat(),
                    account, request_id,
                ),
            )

    def get_futu_refresh(self, *, account: str, request_id: str) -> dict[str, Any] | None:
        with self._connect_inbox_accept() as conn:
            row = conn.execute(
                "SELECT * FROM futu_refresh_inbox WHERE account = ? AND request_id = ?",
                (account, request_id),
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        raw = item.pop("result_json")
        item["result"] = json.loads(raw) if raw is not None else None
        return item
