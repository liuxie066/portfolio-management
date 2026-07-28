"""Always-on local technical state for FX evidence and notification delivery."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from uuid import uuid4

from src import config
from src.time_utils import bj_now_naive


SCHEMA_VERSION = "2"
_RETRY_MINUTES = (1, 5, 15, 60)
_CLAIM_LEASE_MINUTES = 5


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


class OperationStateStore:
    """SQLite state independent from Feishu facts and cash-flow effect cutover."""

    def __init__(
        self,
        db_path: Optional[str | Path] = None,
        *,
        now_factory: Optional[Callable[[], datetime]] = None,
    ):
        self.db_path = self.resolve_db_path(db_path)
        self.now_factory = now_factory or bj_now_naive
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
        self._validate()
        self.db_path.chmod(0o600)

    @staticmethod
    def resolve_db_path(db_path: Optional[str | Path] = None) -> Path:
        if db_path:
            path = Path(db_path).expanduser()
            return path if path.is_absolute() else config.get_data_dir() / path
        return config.get_data_dir() / "pm_operation_state.sqlite3"

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 10000")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS operation_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS cash_flow_fx_confirmations (
                    confirmation_id TEXT PRIMARY KEY,
                    record_id TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    exchange_rate TEXT NOT NULL,
                    exchange_rate_date TEXT NOT NULL,
                    exchange_rate_source TEXT NOT NULL,
                    exchange_rate_evidence_type TEXT NOT NULL,
                    cny_amount TEXT NOT NULL,
                    confirmation_json TEXT NOT NULL,
                    confirmed_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_operation_fx_confirmation_record
                ON cash_flow_fx_confirmations(record_id, confirmed_at DESC);
                CREATE TABLE IF NOT EXISTS nav_receipt_outbox (
                    receipt_key TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at TEXT NOT NULL,
                    claim_id TEXT,
                    claimed_at TEXT,
                    message_id TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_nav_receipt_outbox_due
                ON nav_receipt_outbox(status, next_attempt_at);
                """
            )
            receipt_columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(nav_receipt_outbox)").fetchall()
            }
            if "claim_id" not in receipt_columns:
                conn.execute("ALTER TABLE nav_receipt_outbox ADD COLUMN claim_id TEXT")
            if "claimed_at" not in receipt_columns:
                conn.execute("ALTER TABLE nav_receipt_outbox ADD COLUMN claimed_at TEXT")
            conn.execute(
                """
                INSERT INTO operation_meta(key, value) VALUES ('schema_version', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (SCHEMA_VERSION,),
            )

    def _validate(self) -> None:
        try:
            with self._connect() as conn:
                check = conn.execute("PRAGMA quick_check").fetchone()
                if not check or check[0] != "ok":
                    raise RuntimeError(f"operation state integrity check failed: {check}")
                row = conn.execute(
                    "SELECT value FROM operation_meta WHERE key = 'schema_version'"
                ).fetchone()
                if not row or row[0] != SCHEMA_VERSION:
                    raise RuntimeError(
                        f"unsupported operation state schema version: {row[0] if row else None}"
                    )
        except sqlite3.DatabaseError as exc:
            raise RuntimeError(
                f"operation state database is corrupt: {self.db_path}: {exc}"
            ) from exc

    def record_fx_confirmation(
        self,
        *,
        confirmation_id: str,
        record_id: str,
        source_hash: str,
        exchange_rate: str,
        exchange_rate_date: str,
        exchange_rate_source: str,
        exchange_rate_evidence_type: str,
        cny_amount: str,
        confirmation: Dict[str, Any],
    ) -> str:
        now = self.now_factory().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO cash_flow_fx_confirmations(
                    confirmation_id, record_id, source_hash, exchange_rate,
                    exchange_rate_date, exchange_rate_source,
                    exchange_rate_evidence_type, cny_amount,
                    confirmation_json, confirmed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    confirmation_id,
                    record_id,
                    source_hash,
                    exchange_rate,
                    exchange_rate_date,
                    exchange_rate_source,
                    exchange_rate_evidence_type,
                    cny_amount,
                    _canonical_json(confirmation),
                    now,
                ),
            )
        return confirmation_id

    def latest_fx_confirmation(self, record_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM cash_flow_fx_confirmations
                WHERE record_id = ? ORDER BY confirmed_at DESC LIMIT 1
                """,
                (record_id,),
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["confirmation"] = json.loads(result.pop("confirmation_json"))
        return result

    def import_legacy_fx_confirmations(
        self,
        legacy_db_path: str | Path,
    ) -> Dict[str, int]:
        """Idempotently import confirmations from an initialized effects DB."""
        legacy_path = Path(legacy_db_path).expanduser()
        if not legacy_path.exists():
            raise FileNotFoundError(f"legacy effect database not found: {legacy_path}")
        source = sqlite3.connect(
            f"file:{legacy_path}?mode=ro",
            uri=True,
            timeout=10,
        )
        source.row_factory = sqlite3.Row
        try:
            table = source.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = 'cash_flow_fx_confirmations'
                """
            ).fetchone()
            if not table:
                return {"scanned": 0, "imported": 0}
            rows = source.execute(
                "SELECT * FROM cash_flow_fx_confirmations"
            ).fetchall()
        finally:
            source.close()

        imported = 0
        with self._connect() as conn:
            for row in rows:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO cash_flow_fx_confirmations(
                        confirmation_id, record_id, source_hash, exchange_rate,
                        exchange_rate_date, exchange_rate_source,
                        exchange_rate_evidence_type, cny_amount,
                        confirmation_json, confirmed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    tuple(row[key] for key in (
                        "confirmation_id",
                        "record_id",
                        "source_hash",
                        "exchange_rate",
                        "exchange_rate_date",
                        "exchange_rate_source",
                        "exchange_rate_evidence_type",
                        "cny_amount",
                        "confirmation_json",
                        "confirmed_at",
                    )),
                )
                imported += int(cursor.rowcount == 1)
        return {"scanned": len(rows), "imported": imported}

    def import_default_legacy_fx_confirmations(self) -> Dict[str, int]:
        """Carry forward the previous default FX authority during NAV preflight."""
        if self.db_path != self.resolve_db_path():
            return {"scanned": 0, "imported": 0}
        from src.app.cash_flow_effect_store import CashFlowEffectStore

        legacy_path = CashFlowEffectStore.resolve_db_path()
        if legacy_path == self.db_path or not legacy_path.exists():
            return {"scanned": 0, "imported": 0}
        return self.import_legacy_fx_confirmations(legacy_path)

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
