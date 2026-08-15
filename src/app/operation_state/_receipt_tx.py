"""Transactional helpers for the typed operation receipt outbox.

These are module-level because they operate only on the passed ``conn`` — they
are shared across several operation-state mixins and carry no ``self``/``cls``
state. Importing them explicitly makes that cross-mixin dependency visible
instead of relying on method resolution order.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Dict

from .._json import canonical_json as _canonical_json


def insert_operation_receipt_tx(
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


def insert_repeatable_closure_receipt_tx(
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
    return insert_operation_receipt_tx(
        conn,
        receipt_key=receipt_key,
        receipt_type="holding_case_closed",
        payload=payload,
        now=now,
    )
