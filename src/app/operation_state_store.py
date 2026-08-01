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

from .holding_case_contract import (
    LEGACY_PRECONDITION_CONTRACT_VERSION,
    PRECONDITION_CONTRACT_VERSION,
    PRECONDITION_EXACT,
    PRECONDITION_LEGACY_MIGRATABLE,
    classify_precondition_transition,
    confirmation_scope,
)


SCHEMA_VERSION = "2"
HOLDINGS_WORKFLOW_SCHEMA_VERSION = "1"
CASH_FLOW_EVENT_SCHEMA_VERSION = "1"
_RETRY_MINUTES = (1, 5, 15, 60)
_CLAIM_LEASE_MINUTES = 5

_OPEN_HOLDING_CASE_STATES = (
    "pending_apply",
    "pending_confirmation",
    "pending_manual_edit",
    "applying",
    "failed_retryable",
    "apply_outcome_unknown",
)
_SUPERSEDEABLE_HOLDING_CASE_STATES = (
    *_OPEN_HOLDING_CASE_STATES,
    "resolved_keep",
)
_REOPENABLE_HOLDING_CASE_STATES = (
    "resolved_accept",
    "resolved_external",
    "superseded",
)


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
        return OperationStateStore.resolve_db_path_read_only(db_path)

    @staticmethod
    def resolve_db_path_read_only(db_path: Optional[str | Path] = None) -> Path:
        """Resolve the operation DB path without creating its parent directory."""

        if db_path:
            path = Path(db_path).expanduser()
            if path.is_absolute():
                return path
            configured = config.get("data.dir")
            data_dir = (
                Path(str(configured)).expanduser()
                if configured
                else Path(__file__).resolve().parents[2] / ".data"
            )
            if not data_dir.is_absolute():
                data_dir = Path(__file__).resolve().parents[2] / data_dir
            return data_dir / path
        configured = config.get("data.dir")
        data_dir = (
            Path(str(configured)).expanduser()
            if configured
            else Path(__file__).resolve().parents[2] / ".data"
        )
        if not data_dir.is_absolute():
            data_dir = Path(__file__).resolve().parents[2] / data_dir
        return data_dir / "pm_operation_state.sqlite3"

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 10000")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def _connect_inbox_accept(self) -> sqlite3.Connection:
        """Open the pre-initialized inbox with a bounded receiver lock wait."""

        conn = sqlite3.connect(self.db_path, timeout=1)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 1000")
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            meta_exists = conn.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = 'operation_meta'
                """
            ).fetchone()
            if meta_exists:
                for key, supported, label in (
                    (
                        "holdings_workflow_schema_version",
                        HOLDINGS_WORKFLOW_SCHEMA_VERSION,
                        "holdings workflow",
                    ),
                    (
                        "cash_flow_event_schema_version",
                        CASH_FLOW_EVENT_SCHEMA_VERSION,
                        "cash flow event",
                    ),
                ):
                    feature_row = conn.execute(
                        "SELECT value FROM operation_meta WHERE key = ?",
                        (key,),
                    ).fetchone()
                    if feature_row is None:
                        continue
                    try:
                        feature_version = int(feature_row[0])
                    except (TypeError, ValueError) as exc:
                        raise RuntimeError(
                            f"invalid {label} schema version: {feature_row[0]}"
                        ) from exc
                    if feature_version > int(supported):
                        raise RuntimeError(
                            f"unsupported newer {label} schema version: "
                            f"{feature_version}"
                        )
            conn.executescript(
                """
                BEGIN IMMEDIATE;
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
                CREATE TABLE IF NOT EXISTS holding_reconciliation_cases (
                    case_key TEXT PRIMARY KEY,
                    record_id TEXT NOT NULL,
                    account TEXT,
                    identity_json TEXT NOT NULL DEFAULT '{}',
                    field TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    blocks_official_nav INTEGER NOT NULL,
                    policy_version TEXT NOT NULL,
                    authority_id TEXT,
                    current_json TEXT NOT NULL,
                    proposed_json TEXT NOT NULL,
                    record_digest TEXT NOT NULL,
                    case_precondition_digest TEXT NOT NULL,
                    latest_evidence_instance_id TEXT,
                    evidence_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    resolution_json TEXT,
                    target_json TEXT,
                    before_json TEXT,
                    apply_attempt_id TEXT,
                    remote_attempt_started_at TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_holding_cases_scope
                ON holding_reconciliation_cases(account, state, record_id);
                CREATE INDEX IF NOT EXISTS idx_holding_cases_record_field
                ON holding_reconciliation_cases(record_id, field, kind, state);
                CREATE TABLE IF NOT EXISTS holding_reconciliation_events (
                    event_seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    case_key TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(case_key) REFERENCES holding_reconciliation_cases(case_key)
                );
                CREATE INDEX IF NOT EXISTS idx_holding_case_events
                ON holding_reconciliation_events(case_key, event_seq);
                CREATE TABLE IF NOT EXISTS holding_event_inbox (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    file_token TEXT NOT NULL,
                    table_id TEXT NOT NULL,
                    revision TEXT,
                    action_list_json TEXT NOT NULL,
                    payload_digest TEXT NOT NULL,
                    state TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at TEXT NOT NULL,
                    claim_id TEXT,
                    claimed_at TEXT,
                    outcome_json TEXT,
                    last_error TEXT,
                    received_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_holding_event_inbox_due
                ON holding_event_inbox(state, next_attempt_at);
                CREATE TABLE IF NOT EXISTS cash_flow_event_inbox (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    file_token TEXT NOT NULL,
                    table_id TEXT NOT NULL,
                    revision TEXT,
                    action_list_json TEXT NOT NULL,
                    payload_digest TEXT NOT NULL,
                    state TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at TEXT NOT NULL,
                    claim_id TEXT,
                    claimed_at TEXT,
                    outcome_json TEXT,
                    last_error TEXT,
                    received_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_cash_flow_event_inbox_due
                ON cash_flow_event_inbox(state, next_attempt_at);
                CREATE TABLE IF NOT EXISTS operation_receipt_outbox (
                    receipt_key TEXT PRIMARY KEY,
                    receipt_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at TEXT NOT NULL,
                    claim_id TEXT,
                    claimed_at TEXT,
                    send_started_at TEXT,
                    message_id TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_operation_receipt_due
                ON operation_receipt_outbox(status, next_attempt_at);
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
            holding_case_columns = {
                str(row[1])
                for row in conn.execute(
                    "PRAGMA table_info(holding_reconciliation_cases)"
                ).fetchall()
            }
            if "identity_json" not in holding_case_columns:
                conn.execute(
                    """
                    ALTER TABLE holding_reconciliation_cases
                    ADD COLUMN identity_json TEXT NOT NULL DEFAULT '{}'
                    """
                )
            conn.execute(
                """
                INSERT INTO operation_meta(key, value) VALUES ('schema_version', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (SCHEMA_VERSION,),
            )
            conn.execute(
                """
                INSERT INTO operation_meta(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                ("holdings_workflow_schema_version", HOLDINGS_WORKFLOW_SCHEMA_VERSION),
            )
            conn.execute(
                """
                INSERT INTO operation_meta(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                ("cash_flow_event_schema_version", CASH_FLOW_EVENT_SCHEMA_VERSION),
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
                feature_row = conn.execute(
                    "SELECT value FROM operation_meta WHERE key = ?",
                    ("holdings_workflow_schema_version",),
                ).fetchone()
                if (
                    not feature_row
                    or feature_row[0] != HOLDINGS_WORKFLOW_SCHEMA_VERSION
                ):
                    raise RuntimeError(
                        "unsupported holdings workflow schema version: "
                        f"{feature_row[0] if feature_row else None}"
                    )
                cash_flow_event_row = conn.execute(
                    "SELECT value FROM operation_meta WHERE key = ?",
                    ("cash_flow_event_schema_version",),
                ).fetchone()
                if (
                    not cash_flow_event_row
                    or cash_flow_event_row[0] != CASH_FLOW_EVENT_SCHEMA_VERSION
                ):
                    raise RuntimeError(
                        "unsupported cash flow event schema version: "
                        f"{cash_flow_event_row[0] if cash_flow_event_row else None}"
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

    # ---------- Holdings reconciliation workflow ----------

    @staticmethod
    def _decode_case_row(row: sqlite3.Row | Dict[str, Any]) -> Dict[str, Any]:
        item = dict(row)
        for source, target in (
            ("identity_json", "identity"),
            ("current_json", "current"),
            ("proposed_json", "proposed"),
            ("evidence_json", "evidence"),
            ("resolution_json", "resolution"),
            ("target_json", "target"),
            ("before_json", "before"),
        ):
            raw = item.pop(source, None)
            item[target] = json.loads(raw) if raw is not None else None
        item["blocks_official_nav"] = bool(item["blocks_official_nav"])
        return item

    @staticmethod
    def _insert_case_event_tx(
        conn: sqlite3.Connection,
        *,
        case_key: str,
        event_type: str,
        payload: Dict[str, Any],
        created_at: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO holding_reconciliation_events(
                case_key, event_type, payload_json, created_at
            ) VALUES (?, ?, ?, ?)
            """,
            (case_key, event_type, _canonical_json(payload), created_at),
        )

    def _migrate_case_precondition_tx(
        self,
        conn: sqlite3.Connection,
        *,
        existing: sqlite3.Row,
        candidate: Dict[str, Any],
        trigger: Optional[Dict[str, Any]],
        now: str,
    ) -> bool:
        stored = self._decode_case_row(existing)
        transition = classify_precondition_transition(stored, candidate)
        if transition == PRECONDITION_EXACT:
            return False
        if transition != PRECONDITION_LEGACY_MIGRATABLE:
            raise ValueError(
                "holding case key collision with different semantics: "
                f"{candidate.get('case_key')}"
            )

        resolution = stored.get("resolution")
        if stored.get("state") == "resolved_keep":
            resolution = dict(resolution or {})
            resolution["confirmation_scope"] = confirmation_scope(candidate)
        cursor = conn.execute(
            """
            UPDATE holding_reconciliation_cases
            SET case_precondition_digest = ?, resolution_json = ?, updated_at = ?
            WHERE case_key = ? AND case_precondition_digest = ?
            """,
            (
                candidate["case_precondition_digest"],
                _canonical_json(resolution) if resolution is not None else None,
                now,
                candidate["case_key"],
                stored["case_precondition_digest"],
            ),
        )
        if cursor.rowcount != 1:
            raise ValueError(
                "holding case precondition changed during migration: "
                f"{candidate.get('case_key')}"
            )
        self._insert_case_event_tx(
            conn,
            case_key=str(candidate["case_key"]),
            event_type="precondition_contract_migrated",
            payload={
                "from_contract": LEGACY_PRECONDITION_CONTRACT_VERSION,
                "to_contract": PRECONDITION_CONTRACT_VERSION,
                "from_digest": stored["case_precondition_digest"],
                "to_digest": candidate["case_precondition_digest"],
                "state": stored.get("state"),
                "trigger": dict(trigger or {}),
            },
            created_at=now,
        )
        return True

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

    def materialize_holding_cases(
        self,
        *,
        cases: list[Dict[str, Any]],
        discovery_receipts: list[Dict[str, Any]],
        trigger: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Atomically store semantic cases/events and first-discovery receipts."""

        now = self.now_factory().isoformat()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            return self._materialize_holding_cases_tx(
                conn,
                cases=cases,
                discovery_receipts=discovery_receipts,
                trigger=trigger,
                now=now,
            )

    def _materialize_holding_cases_tx(
        self,
        conn: sqlite3.Connection,
        *,
        cases: list[Dict[str, Any]],
        discovery_receipts: list[Dict[str, Any]],
        trigger: Optional[Dict[str, Any]],
        now: str,
    ) -> Dict[str, Any]:
        receipt_by_case = {
            str(item.get("case_key") or ""): dict(item)
            for item in discovery_receipts
        }
        created: list[str] = []
        refreshed: list[str] = []
        reopened: list[str] = []
        superseded: list[str] = []
        receipt_keys: list[str] = []
        for candidate in cases:
            case_key = str(candidate.get("case_key") or "").strip()
            record_id = str(candidate.get("record_id") or "").strip()
            field = str(candidate.get("field") or "").strip()
            kind = str(candidate.get("kind") or "").strip()
            state = str(candidate.get("state") or "").strip()
            policy_version = str(candidate.get("policy_version") or "").strip()
            precondition = str(
                candidate.get("case_precondition_digest") or ""
            ).strip()
            record_digest = str(candidate.get("record_digest") or "").strip()
            if not all(
                (
                    case_key,
                    record_id,
                    field,
                    kind,
                    state,
                    policy_version,
                    precondition,
                    record_digest,
                )
            ):
                raise ValueError("holding case is missing a required identity field")
            old_rows = conn.execute(
                """
                SELECT * FROM holding_reconciliation_cases
                WHERE record_id = ? AND field = ? AND case_key != ?
                  AND state IN ({})
                """.format(",".join("?" for _ in _SUPERSEDEABLE_HOLDING_CASE_STATES)),
                (record_id, field, case_key, *_SUPERSEDEABLE_HOLDING_CASE_STATES),
            ).fetchall()
            for old in old_rows:
                resolution = {
                    "reason": "semantic_case_changed",
                    "replacement_case_key": case_key,
                    "trigger": dict(trigger or {}),
                }
                if old["resolution_json"]:
                    resolution["previous_resolution"] = json.loads(
                        old["resolution_json"]
                    )
                conn.execute(
                    """
                    UPDATE holding_reconciliation_cases
                    SET state = 'superseded', resolution_json = ?, updated_at = ?
                    WHERE case_key = ? AND state IN ({})
                    """.format(",".join("?" for _ in _SUPERSEDEABLE_HOLDING_CASE_STATES)),
                    (
                        _canonical_json(resolution),
                        now,
                        old["case_key"],
                        *_SUPERSEDEABLE_HOLDING_CASE_STATES,
                    ),
                )
                self._insert_case_event_tx(
                    conn,
                    case_key=old["case_key"],
                    event_type="superseded",
                    payload=resolution,
                    created_at=now,
                )
                closure_key = (
                    f"holdings:case:closed:{old['case_key']}:superseded:"
                    f"{record_digest}"
                )
                if self._insert_repeatable_closure_receipt_tx(
                    conn,
                    receipt_key=closure_key,
                    payload={
                        "case_key": old["case_key"],
                        "record_id": old["record_id"],
                        "field": old["field"],
                        "terminal_state": "superseded",
                        **resolution,
                    },
                    now=now,
                ):
                    receipt_keys.append(closure_key)
                superseded.append(old["case_key"])

            existing = conn.execute(
                "SELECT * FROM holding_reconciliation_cases WHERE case_key = ?",
                (case_key,),
            ).fetchone()
            immutable = {
                "record_id": record_id,
                "identity_json": _canonical_json(candidate.get("identity") or {}),
                "field": field,
                "kind": kind,
                "policy_version": policy_version,
                "authority_id": candidate.get("authority_id"),
                "current_json": _canonical_json(candidate.get("current")),
                "proposed_json": _canonical_json(candidate.get("proposed")),
                "case_precondition_digest": precondition,
            }
            if existing:
                if existing["identity_json"] == "{}":
                    conn.execute(
                        """
                        UPDATE holding_reconciliation_cases
                        SET identity_json = ?, updated_at = ? WHERE case_key = ?
                        """,
                        (immutable["identity_json"], now, case_key),
                    )
                elif existing["identity_json"] != immutable["identity_json"]:
                    raise ValueError(
                        "holding case key collision with different identity: "
                        f"{case_key}"
                    )
                existing = conn.execute(
                    "SELECT * FROM holding_reconciliation_cases WHERE case_key = ?",
                    (case_key,),
                ).fetchone()
                self._migrate_case_precondition_tx(
                    conn,
                    existing=existing,
                    candidate=candidate,
                    trigger=trigger,
                    now=now,
                )
                existing = conn.execute(
                    "SELECT * FROM holding_reconciliation_cases WHERE case_key = ?",
                    (case_key,),
                ).fetchone()
                if any(
                    existing[key] != value
                    for key, value in immutable.items()
                    if key != "identity_json"
                ):
                    raise ValueError(
                        "holding case key collision with different semantics: "
                        f"{case_key}"
                    )
                evidence_id = candidate.get("latest_evidence_instance_id")
                evidence_json = _canonical_json(candidate.get("evidence") or {})
                if existing["state"] in _REOPENABLE_HOLDING_CASE_STATES:
                    conn.execute(
                        """
                        UPDATE holding_reconciliation_cases
                        SET state = ?, identity_json = ?,
                            latest_evidence_instance_id = ?, evidence_json = ?,
                            record_digest = ?, resolution_json = NULL,
                            target_json = NULL, before_json = NULL,
                            apply_attempt_id = NULL,
                            remote_attempt_started_at = NULL,
                            last_error = NULL, updated_at = ?
                        WHERE case_key = ?
                        """,
                        (
                            state,
                            immutable["identity_json"],
                            evidence_id,
                            evidence_json,
                            record_digest,
                            now,
                            case_key,
                        ),
                    )
                    self._insert_case_event_tx(
                        conn,
                        case_key=case_key,
                        event_type="reopened",
                        payload={
                            "from_state": existing["state"],
                            "to_state": state,
                            "record_digest": record_digest,
                            "trigger": dict(trigger or {}),
                        },
                        created_at=now,
                    )
                    reopened.append(case_key)
                    continue
                if (
                    existing["latest_evidence_instance_id"] != evidence_id
                    or existing["evidence_json"] != evidence_json
                    or existing["record_digest"] != record_digest
                ):
                    conn.execute(
                        """
                        UPDATE holding_reconciliation_cases
                        SET latest_evidence_instance_id = ?, evidence_json = ?,
                            record_digest = ?, updated_at = ?
                        WHERE case_key = ?
                        """,
                        (evidence_id, evidence_json, record_digest, now, case_key),
                    )
                    self._insert_case_event_tx(
                        conn,
                        case_key=case_key,
                        event_type=(
                            "evidence_refreshed"
                            if (
                                existing["latest_evidence_instance_id"] != evidence_id
                                or existing["evidence_json"] != evidence_json
                            )
                            else "record_refreshed"
                        ),
                        payload={
                            "evidence_instance_id": evidence_id,
                            "record_digest": record_digest,
                            "trigger": dict(trigger or {}),
                        },
                        created_at=now,
                    )
                    refreshed.append(case_key)
                continue

            conn.execute(
                """
                INSERT INTO holding_reconciliation_cases(
                    case_key, record_id, account, identity_json, field, kind,
                    blocks_official_nav, policy_version, authority_id,
                    current_json, proposed_json, record_digest,
                    case_precondition_digest, latest_evidence_instance_id,
                    evidence_json, state, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    case_key,
                    record_id,
                    candidate.get("account"),
                    immutable["identity_json"],
                    field,
                    kind,
                    int(bool(candidate.get("blocks_official_nav"))),
                    policy_version,
                    candidate.get("authority_id"),
                    immutable["current_json"],
                    immutable["proposed_json"],
                    record_digest,
                    precondition,
                    candidate.get("latest_evidence_instance_id"),
                    _canonical_json(candidate.get("evidence") or {}),
                    state,
                    now,
                    now,
                ),
            )
            self._insert_case_event_tx(
                conn,
                case_key=case_key,
                event_type="discovered",
                payload={
                    "state": state,
                    "record_digest": record_digest,
                    "trigger": dict(trigger or {}),
                },
                created_at=now,
            )
            receipt = receipt_by_case.get(case_key)
            if receipt is None:
                raise ValueError(f"new holding case lacks discovery receipt: {case_key}")
            if self._insert_operation_receipt_tx(
                conn,
                receipt_key=str(receipt["receipt_key"]),
                receipt_type=str(receipt["receipt_type"]),
                payload=dict(receipt["payload"]),
                now=now,
            ):
                receipt_keys.append(str(receipt["receipt_key"]))
            created.append(case_key)
        return {
            "created_case_keys": created,
            "refreshed_case_keys": refreshed,
            "reopened_case_keys": reopened,
            "superseded_case_keys": superseded,
            "enqueued_receipt_keys": receipt_keys,
        }

    def get_holding_case(self, case_key: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM holding_reconciliation_cases WHERE case_key = ?",
                (case_key,),
            ).fetchone()
        return self._decode_case_row(row) if row else None

    @classmethod
    def get_holding_cases_read_only(
        cls,
        case_keys: list[str],
        *,
        db_path: Optional[str | Path] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """Read existing cases without initializing or migrating local state."""

        keys = [str(key) for key in dict.fromkeys(case_keys) if str(key).strip()]
        if not keys:
            return {}
        path = cls.resolve_db_path_read_only(db_path)
        if not path.exists():
            return {}
        uri = f"{path.resolve().as_uri()}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            table = conn.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = 'holding_reconciliation_cases'
                """
            ).fetchone()
            if table is None:
                return {}
            rows = conn.execute(
                """
                SELECT * FROM holding_reconciliation_cases
                WHERE case_key IN ({})
                """.format(",".join("?" for _ in keys)),
                keys,
            ).fetchall()
        return {
            str(row["case_key"]): cls._decode_case_row(row)
            for row in rows
        }

    @classmethod
    def list_holding_cases_read_only(
        cls,
        *,
        account: Optional[str] = None,
        state: Optional[str] = None,
        db_path: Optional[str | Path] = None,
    ) -> list[Dict[str, Any]]:
        """List existing cases without creating or migrating local state."""

        path = cls.resolve_db_path_read_only(db_path)
        if not path.exists():
            return []
        uri = f"{path.resolve().as_uri()}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            table = conn.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = 'holding_reconciliation_cases'
                """
            ).fetchone()
            if table is None:
                return []
            query = "SELECT * FROM holding_reconciliation_cases WHERE 1 = 1"
            params: list[Any] = []
            if account is not None:
                query += " AND account = ?"
                params.append(account)
            if state is not None:
                query += " AND state = ?"
                params.append(state)
            query += " ORDER BY created_at DESC, case_key"
            rows = conn.execute(query, params).fetchall()
        return [cls._decode_case_row(row) for row in rows]

    def list_holding_cases(
        self,
        *,
        account: Optional[str] = None,
        state: Optional[str] = None,
        limit: int = 500,
    ) -> list[Dict[str, Any]]:
        query = "SELECT * FROM holding_reconciliation_cases WHERE 1 = 1"
        params: list[Any] = []
        if account is not None:
            query += " AND account = ?"
            params.append(account)
        if state is not None:
            query += " AND state = ?"
            params.append(state)
        query += " ORDER BY created_at DESC, case_key LIMIT ?"
        params.append(int(limit))
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._decode_case_row(row) for row in rows]

    def list_holding_case_events(self, case_key: str) -> list[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM holding_reconciliation_events
                WHERE case_key = ? ORDER BY event_seq
                """,
                (case_key,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            result.append(item)
        return result

    def resolve_holding_cases_external(
        self,
        *,
        record_id: str,
        active_case_keys: list[str],
        record_digest: str,
        current_identity: Dict[str, Any],
        trigger: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, list[str]]:
        """Close previously open cases only after a fresh scan proves repair."""

        now = self.now_factory().isoformat()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            return self._resolve_absent_holding_cases_tx(
                conn,
                record_id=record_id,
                active_case_keys=active_case_keys,
                record_digest=record_digest,
                current_identity=current_identity,
                trigger=trigger,
                now=now,
            )

    def _resolve_absent_holding_cases_tx(
        self,
        conn: sqlite3.Connection,
        *,
        record_id: str,
        active_case_keys: list[str],
        record_digest: str,
        current_identity: Dict[str, Any],
        trigger: Optional[Dict[str, Any]],
        now: str,
    ) -> Dict[str, list[str]]:
        eligible_states = (
            "pending_apply",
            "pending_confirmation",
            "pending_manual_edit",
            "failed_retryable",
        )
        query = """
            SELECT * FROM holding_reconciliation_cases
            WHERE record_id = ? AND state IN ({})
        """.format(",".join("?" for _ in eligible_states))
        params: list[Any] = [record_id, *eligible_states]
        if active_case_keys:
            query += " AND case_key NOT IN ({})".format(
                ",".join("?" for _ in active_case_keys)
            )
            params.extend(active_case_keys)
        closed: list[str] = []
        superseded: list[str] = []
        receipt_keys: list[str] = []
        rows = conn.execute(query, params).fetchall()
        for row in rows:
            stored_identity = json.loads(row["identity_json"] or "{}")
            identity_matches = stored_identity == dict(current_identity)
            terminal_state = (
                "resolved_external" if identity_matches else "superseded"
            )
            resolution = {
                "decision": (
                    "fresh_scan_proved_repair"
                    if identity_matches
                    else "record_identity_changed"
                ),
                "readback_digest": record_digest,
                "expected_identity": stored_identity,
                "observed_identity": dict(current_identity),
                "trigger": dict(trigger or {}),
            }
            conn.execute(
                """
                UPDATE holding_reconciliation_cases
                SET state = ?, resolution_json = ?,
                    last_error = NULL, updated_at = ?
                WHERE case_key = ? AND state IN ({})
                """.format(",".join("?" for _ in eligible_states)),
                (
                    terminal_state,
                    _canonical_json(resolution),
                    now,
                    row["case_key"],
                    *eligible_states,
                ),
            )
            self._insert_case_event_tx(
                conn,
                case_key=row["case_key"],
                event_type=terminal_state,
                payload=resolution,
                created_at=now,
            )
            receipt_key = (
                f"holdings:case:closed:{row['case_key']}:{terminal_state}:"
                f"{record_digest}"
            )
            if self._insert_repeatable_closure_receipt_tx(
                conn,
                receipt_key=receipt_key,
                payload={
                    "case_key": row["case_key"],
                    "record_id": row["record_id"],
                    "account": row["account"],
                    "field": row["field"],
                    "terminal_state": terminal_state,
                    **resolution,
                },
                now=now,
            ):
                receipt_keys.append(receipt_key)
            if identity_matches:
                closed.append(row["case_key"])
            else:
                superseded.append(row["case_key"])
        return {
            "closed_case_keys": closed,
            "superseded_case_keys": superseded,
            "enqueued_receipt_keys": receipt_keys,
        }

    def prepare_holding_apply(
        self,
        *,
        cases: list[Dict[str, Any]],
        apply_attempt_id: str,
        operator_context: Dict[str, Any],
    ) -> None:
        if not cases:
            raise ValueError("holding apply requires at least one case")
        now = self.now_factory().isoformat()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for candidate in cases:
                case_key = str(candidate["case_key"])
                row = conn.execute(
                    "SELECT * FROM holding_reconciliation_cases WHERE case_key = ?",
                    (case_key,),
                ).fetchone()
                allowed_states = tuple(candidate.get("allowed_states") or ())
                if not row or row["state"] not in allowed_states:
                    raise ValueError(
                        f"holding case is not applicable: {case_key}: "
                        f"{row['state'] if row else 'missing'}"
                    )
                if row["case_precondition_digest"] != candidate["case_precondition_digest"]:
                    raise ValueError(f"holding case precondition changed: {case_key}")
                resolution = {
                    "operator_context": dict(operator_context),
                    "decision": candidate.get("decision"),
                    "reason": candidate.get("reason"),
                    "confirmation_scope": candidate.get("confirmation_scope"),
                }
                conn.execute(
                    """
                    UPDATE holding_reconciliation_cases
                    SET state = 'applying', target_json = ?, before_json = ?,
                        apply_attempt_id = ?, resolution_json = ?,
                        remote_attempt_started_at = NULL, last_error = NULL,
                        updated_at = ?
                    WHERE case_key = ?
                    """,
                    (
                        _canonical_json(candidate.get("target")),
                        _canonical_json(candidate.get("before")),
                        apply_attempt_id,
                        _canonical_json(resolution),
                        now,
                        case_key,
                    ),
                )
                self._insert_case_event_tx(
                    conn,
                    case_key=case_key,
                    event_type="apply_prepared",
                    payload={
                        "apply_attempt_id": apply_attempt_id,
                        "target": candidate.get("target"),
                        "before": candidate.get("before"),
                        **resolution,
                    },
                    created_at=now,
                )

    def materialize_and_prepare_holding_apply(
        self,
        *,
        observed_cases: list[Dict[str, Any]],
        discovery_receipts: list[Dict[str, Any]],
        apply_cases: list[Dict[str, Any]],
        apply_attempt_id: str,
        operator_context: Dict[str, Any],
        trigger: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, list[str]]:
        """Create/refresh cases and commit the selected subset to applying atomically."""

        if not apply_cases:
            raise ValueError("holding apply requires at least one case")
        now = self.now_factory().isoformat()
        receipt_by_case = {
            str(item.get("case_key") or ""): dict(item)
            for item in discovery_receipts
        }
        created: list[str] = []
        refreshed: list[str] = []
        reopened: list[str] = []
        closed: list[str] = []
        superseded: list[str] = []
        receipt_keys: list[str] = []
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for candidate in observed_cases:
                case_key = str(candidate.get("case_key") or "").strip()
                record_id = str(candidate.get("record_id") or "").strip()
                field = str(candidate.get("field") or "").strip()
                kind = str(candidate.get("kind") or "").strip()
                state = str(candidate.get("state") or "").strip()
                policy_version = str(candidate.get("policy_version") or "").strip()
                precondition = str(
                    candidate.get("case_precondition_digest") or ""
                ).strip()
                current_json = _canonical_json(candidate.get("current"))
                proposed_json = _canonical_json(candidate.get("proposed"))
                evidence_json = _canonical_json(candidate.get("evidence") or {})
                if not all(
                    (
                        case_key,
                        record_id,
                        field,
                        kind,
                        state,
                        policy_version,
                        precondition,
                        candidate.get("record_digest"),
                    )
                ):
                    raise ValueError("holding case is missing a required identity field")
                old_rows = conn.execute(
                    """
                    SELECT * FROM holding_reconciliation_cases
                    WHERE record_id = ? AND field = ? AND case_key != ?
                      AND state IN ({})
                    """.format(",".join("?" for _ in _SUPERSEDEABLE_HOLDING_CASE_STATES)),
                    (record_id, field, case_key, *_SUPERSEDEABLE_HOLDING_CASE_STATES),
                ).fetchall()
                for old in old_rows:
                    resolution = {
                        "reason": "semantic_case_changed",
                        "replacement_case_key": case_key,
                        "trigger": dict(trigger or {}),
                    }
                    if old["resolution_json"]:
                        resolution["previous_resolution"] = json.loads(
                            old["resolution_json"]
                        )
                    conn.execute(
                        """
                        UPDATE holding_reconciliation_cases
                        SET state = 'superseded', resolution_json = ?, updated_at = ?
                        WHERE case_key = ? AND state IN ({})
                        """.format(",".join("?" for _ in _SUPERSEDEABLE_HOLDING_CASE_STATES)),
                        (
                            _canonical_json(resolution),
                            now,
                            old["case_key"],
                            *_SUPERSEDEABLE_HOLDING_CASE_STATES,
                        ),
                    )
                    self._insert_case_event_tx(
                        conn,
                        case_key=old["case_key"],
                        event_type="superseded",
                        payload=resolution,
                        created_at=now,
                    )
                    closure_key = (
                        f"holdings:case:closed:{old['case_key']}:superseded:"
                        f"{candidate['record_digest']}"
                    )
                    if self._insert_repeatable_closure_receipt_tx(
                        conn,
                        receipt_key=closure_key,
                        payload={
                            "case_key": old["case_key"],
                            "record_id": old["record_id"],
                            "field": old["field"],
                            "terminal_state": "superseded",
                            **resolution,
                        },
                        now=now,
                    ):
                        receipt_keys.append(closure_key)
                    superseded.append(old["case_key"])

                existing = conn.execute(
                    "SELECT * FROM holding_reconciliation_cases WHERE case_key = ?",
                    (case_key,),
                ).fetchone()
                if existing:
                    immutable = {
                        "record_id": record_id,
                        "identity_json": _canonical_json(candidate.get("identity") or {}),
                        "field": field,
                        "kind": kind,
                        "policy_version": policy_version,
                        "authority_id": candidate.get("authority_id"),
                        "current_json": current_json,
                        "proposed_json": proposed_json,
                        "case_precondition_digest": precondition,
                    }
                    if existing["identity_json"] == "{}":
                        conn.execute(
                            """
                            UPDATE holding_reconciliation_cases
                            SET identity_json = ?, updated_at = ? WHERE case_key = ?
                            """,
                            (immutable["identity_json"], now, case_key),
                        )
                    elif existing["identity_json"] != immutable["identity_json"]:
                        raise ValueError(
                            "holding case key collision with different identity: "
                            f"{case_key}"
                        )
                    existing = conn.execute(
                        "SELECT * FROM holding_reconciliation_cases WHERE case_key = ?",
                        (case_key,),
                    ).fetchone()
                    self._migrate_case_precondition_tx(
                        conn,
                        existing=existing,
                        candidate=candidate,
                        trigger=trigger,
                        now=now,
                    )
                    existing = conn.execute(
                        "SELECT * FROM holding_reconciliation_cases WHERE case_key = ?",
                        (case_key,),
                    ).fetchone()
                    if any(
                        existing[key] != value
                        for key, value in immutable.items()
                        if key != "identity_json"
                    ):
                        raise ValueError(
                            "holding case key collision with different semantics: "
                            f"{case_key}"
                        )
                    if existing["state"] in _REOPENABLE_HOLDING_CASE_STATES:
                        conn.execute(
                            """
                            UPDATE holding_reconciliation_cases
                            SET state = ?, identity_json = ?,
                                latest_evidence_instance_id = ?, evidence_json = ?,
                                record_digest = ?, resolution_json = NULL,
                                target_json = NULL, before_json = NULL,
                                apply_attempt_id = NULL,
                                remote_attempt_started_at = NULL,
                                last_error = NULL, updated_at = ?
                            WHERE case_key = ?
                            """,
                            (
                                state,
                                immutable["identity_json"],
                                candidate.get("latest_evidence_instance_id"),
                                evidence_json,
                                candidate["record_digest"],
                                now,
                                case_key,
                            ),
                        )
                        self._insert_case_event_tx(
                            conn,
                            case_key=case_key,
                            event_type="reopened",
                            payload={
                                "from_state": existing["state"],
                                "to_state": state,
                                "record_digest": candidate["record_digest"],
                                "trigger": dict(trigger or {}),
                            },
                            created_at=now,
                        )
                        reopened.append(case_key)
                    elif (
                        existing["latest_evidence_instance_id"]
                        != candidate.get("latest_evidence_instance_id")
                        or existing["evidence_json"] != evidence_json
                        or existing["record_digest"] != candidate["record_digest"]
                    ):
                        conn.execute(
                            """
                            UPDATE holding_reconciliation_cases
                            SET latest_evidence_instance_id = ?, evidence_json = ?,
                                record_digest = ?, updated_at = ?
                            WHERE case_key = ?
                            """,
                            (
                                candidate.get("latest_evidence_instance_id"),
                                evidence_json,
                                candidate["record_digest"],
                                now,
                                case_key,
                            ),
                        )
                        self._insert_case_event_tx(
                            conn,
                            case_key=case_key,
                            event_type="evidence_refreshed",
                            payload={
                                "evidence_instance_id": candidate.get(
                                    "latest_evidence_instance_id"
                                ),
                                "record_digest": candidate["record_digest"],
                            },
                            created_at=now,
                        )
                        refreshed.append(case_key)
                else:
                    conn.execute(
                        """
                        INSERT INTO holding_reconciliation_cases(
                            case_key, record_id, account, identity_json, field, kind,
                            blocks_official_nav, policy_version, authority_id,
                            current_json, proposed_json, record_digest,
                            case_precondition_digest, latest_evidence_instance_id,
                            evidence_json, state, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            case_key,
                            record_id,
                            candidate.get("account"),
                            _canonical_json(candidate.get("identity") or {}),
                            field,
                            kind,
                            int(bool(candidate.get("blocks_official_nav"))),
                            policy_version,
                            candidate.get("authority_id"),
                            current_json,
                            proposed_json,
                            candidate["record_digest"],
                            precondition,
                            candidate.get("latest_evidence_instance_id"),
                            evidence_json,
                            state,
                            now,
                            now,
                        ),
                    )
                    self._insert_case_event_tx(
                        conn,
                        case_key=case_key,
                        event_type="discovered",
                        payload={"state": state, "trigger": dict(trigger or {})},
                        created_at=now,
                    )
                    receipt = receipt_by_case.get(case_key)
                    if receipt is None:
                        raise ValueError(
                            f"new holding case lacks discovery receipt: {case_key}"
                        )
                    if self._insert_operation_receipt_tx(
                        conn,
                        receipt_key=str(receipt["receipt_key"]),
                        receipt_type=str(receipt["receipt_type"]),
                        payload=dict(receipt["payload"]),
                        now=now,
                    ):
                        receipt_keys.append(str(receipt["receipt_key"]))
                    created.append(case_key)

            if not observed_cases:
                raise ValueError("holding apply lacks a fresh observed case set")
            first_observed = observed_cases[0]
            record_ids = {str(item.get("record_id") or "") for item in observed_cases}
            identities = {
                _canonical_json(item.get("identity") or {}) for item in observed_cases
            }
            record_digests = {
                str(item.get("record_digest") or "") for item in observed_cases
            }
            if len(record_ids) != 1 or len(identities) != 1 or len(record_digests) != 1:
                raise ValueError("holding apply observed cases span multiple record scopes")
            absent = self._resolve_absent_holding_cases_tx(
                conn,
                record_id=str(first_observed["record_id"]),
                active_case_keys=[str(item["case_key"]) for item in observed_cases],
                record_digest=str(first_observed["record_digest"]),
                current_identity=dict(first_observed.get("identity") or {}),
                trigger=trigger,
                now=now,
            )
            closed.extend(absent["closed_case_keys"])
            superseded.extend(absent["superseded_case_keys"])
            receipt_keys.extend(absent["enqueued_receipt_keys"])

            for candidate in apply_cases:
                case_key = str(candidate["case_key"])
                row = conn.execute(
                    "SELECT * FROM holding_reconciliation_cases WHERE case_key = ?",
                    (case_key,),
                ).fetchone()
                allowed_states = tuple(candidate.get("allowed_states") or ())
                if not row or row["state"] not in allowed_states:
                    raise ValueError(
                        f"holding case is not applicable: {case_key}: "
                        f"{row['state'] if row else 'missing'}"
                    )
                if row["case_precondition_digest"] != candidate["case_precondition_digest"]:
                    raise ValueError(f"holding case precondition changed: {case_key}")
                resolution = {
                    "operator_context": dict(operator_context),
                    "decision": candidate.get("decision"),
                    "reason": candidate.get("reason"),
                    "confirmation_scope": candidate.get("confirmation_scope"),
                }
                conn.execute(
                    """
                    UPDATE holding_reconciliation_cases
                    SET state = 'applying', target_json = ?, before_json = ?,
                        apply_attempt_id = ?, resolution_json = ?,
                        remote_attempt_started_at = NULL, last_error = NULL,
                        updated_at = ?
                    WHERE case_key = ?
                    """,
                    (
                        _canonical_json(candidate.get("target")),
                        _canonical_json(candidate.get("before")),
                        apply_attempt_id,
                        _canonical_json(resolution),
                        now,
                        case_key,
                    ),
                )
                self._insert_case_event_tx(
                    conn,
                    case_key=case_key,
                    event_type="apply_prepared",
                    payload={
                        "apply_attempt_id": apply_attempt_id,
                        "target": candidate.get("target"),
                        "before": candidate.get("before"),
                        **resolution,
                    },
                    created_at=now,
                )
        return {
            "created_case_keys": created,
            "refreshed_case_keys": refreshed,
            "reopened_case_keys": reopened,
            "closed_case_keys": closed,
            "superseded_case_keys": superseded,
            "enqueued_receipt_keys": receipt_keys,
        }

    def mark_holding_remote_attempt(
        self,
        *,
        case_keys: list[str],
        apply_attempt_id: str,
    ) -> None:
        now = self.now_factory().isoformat()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for case_key in case_keys:
                cursor = conn.execute(
                    """
                    UPDATE holding_reconciliation_cases
                    SET remote_attempt_started_at = ?, updated_at = ?
                    WHERE case_key = ? AND state = 'applying'
                      AND apply_attempt_id = ? AND remote_attempt_started_at IS NULL
                    """,
                    (now, now, case_key, apply_attempt_id),
                )
                if cursor.rowcount != 1:
                    raise ValueError(
                        f"holding case remote attempt cannot start: {case_key}"
                    )
                self._insert_case_event_tx(
                    conn,
                    case_key=case_key,
                    event_type="remote_attempt_started",
                    payload={"apply_attempt_id": apply_attempt_id},
                    created_at=now,
                )

    def finalize_holding_cases(
        self,
        *,
        outcomes: list[Dict[str, Any]],
        receipts: list[Dict[str, Any]],
    ) -> None:
        now = self.now_factory().isoformat()
        allowed_terminal = {
            "resolved_accept",
            "resolved_keep",
            "resolved_external",
            "superseded",
            "failed_retryable",
            "apply_outcome_unknown",
        }
        receipts_by_case = {
            str(item.get("case_key") or ""): dict(item) for item in receipts
        }
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for outcome in outcomes:
                state = str(outcome.get("state") or "")
                case_key = str(outcome.get("case_key") or "")
                if state not in allowed_terminal:
                    raise ValueError(f"invalid holding case terminal state: {state}")
                row = conn.execute(
                    "SELECT * FROM holding_reconciliation_cases WHERE case_key = ?",
                    (case_key,),
                ).fetchone()
                if not row:
                    raise KeyError(f"holding case not found: {case_key}")
                expected_attempt = outcome.get("apply_attempt_id")
                if expected_attempt and row["apply_attempt_id"] != expected_attempt:
                    raise ValueError(f"holding apply attempt changed: {case_key}")
                resolution = dict(outcome.get("resolution") or {})
                conn.execute(
                    """
                    UPDATE holding_reconciliation_cases
                    SET state = ?, resolution_json = ?, last_error = ?, updated_at = ?
                    WHERE case_key = ?
                    """,
                    (
                        state,
                        _canonical_json(resolution),
                        outcome.get("last_error"),
                        now,
                        case_key,
                    ),
                )
                self._insert_case_event_tx(
                    conn,
                    case_key=case_key,
                    event_type=str(outcome.get("event_type") or "resolved"),
                    payload={"state": state, **resolution},
                    created_at=now,
                )
                receipt = receipts_by_case.get(case_key)
                if receipt:
                    self._insert_operation_receipt_tx(
                        conn,
                        receipt_key=str(receipt["receipt_key"]),
                        receipt_type=str(receipt["receipt_type"]),
                        payload=dict(receipt["payload"]),
                        now=now,
                    )

    # ---------- Holdings event inbox ----------

    def complete_holding_event(
        self,
        *,
        event_id: str,
        claim_id: str,
        materializations: list[Dict[str, Any]],
        outcome: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Commit event cases, receipts, closures, and processed state together."""

        now = self.now_factory().isoformat()
        combined: Dict[str, list[str]] = {
            "created_case_keys": [],
            "refreshed_case_keys": [],
            "reopened_case_keys": [],
            "superseded_case_keys": [],
            "closed_case_keys": [],
            "enqueued_receipt_keys": [],
        }
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            claimed = conn.execute(
                """
                SELECT 1 FROM holding_event_inbox
                WHERE event_id = ? AND state = 'claimed' AND claim_id = ?
                """,
                (event_id, claim_id),
            ).fetchone()
            if not claimed:
                raise KeyError(f"holding event claim not found: {event_id}")
            for item in materializations:
                trigger = dict(item.get("trigger") or {})
                stored = self._materialize_holding_cases_tx(
                    conn,
                    cases=list(item.get("cases") or ()),
                    discovery_receipts=list(item.get("discovery_receipts") or ()),
                    trigger=trigger,
                    now=now,
                )
                for key, values in stored.items():
                    combined.setdefault(key, []).extend(values)
                if bool(item.get("prove_external")):
                    closed = self._resolve_absent_holding_cases_tx(
                        conn,
                        record_id=str(item["record_id"]),
                        active_case_keys=list(item.get("active_case_keys") or ()),
                        record_digest=str(item["record_digest"]),
                        current_identity=dict(item.get("current_identity") or {}),
                        trigger=trigger,
                        now=now,
                    )
                    for key, values in closed.items():
                        combined.setdefault(key, []).extend(values)
            persisted_outcome = dict(outcome)
            persisted_outcome["workflow"] = combined
            cursor = conn.execute(
                """
                UPDATE holding_event_inbox
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
                raise KeyError(f"holding event claim not found: {event_id}")
        return persisted_outcome

    def accept_holding_event(
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
                INSERT OR IGNORE INTO holding_event_inbox(
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
                FROM holding_event_inbox WHERE event_id = ?
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
                    f"holding event id collision with different payload: {event_id}"
                )
        return cursor.rowcount == 1

    def claim_holding_events(
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
                UPDATE holding_event_inbox
                SET state = 'failed_retryable', claim_id = NULL, claimed_at = NULL,
                    last_error = COALESCE(last_error, 'worker claim expired'),
                    next_attempt_at = ?, updated_at = ?
                WHERE state = 'claimed' AND claimed_at <= ?
                """,
                (now, now, lease_cutoff),
            )
            rows = conn.execute(
                """
                SELECT * FROM holding_event_inbox
                WHERE state IN ('pending', 'failed_retryable')
                  AND next_attempt_at <= ?
                ORDER BY received_at LIMIT ?
                """,
                (now, int(limit)),
            ).fetchall()
            for row in rows:
                cursor = conn.execute(
                    """
                    UPDATE holding_event_inbox
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

    def mark_holding_event_processed(
        self,
        *,
        event_id: str,
        claim_id: str,
        outcome: Dict[str, Any],
    ) -> None:
        now = self.now_factory().isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE holding_event_inbox
                SET state = 'processed', outcome_json = ?, claim_id = NULL,
                    claimed_at = NULL, last_error = NULL, updated_at = ?
                WHERE event_id = ? AND state = 'claimed' AND claim_id = ?
                """,
                (_canonical_json(outcome), now, event_id, claim_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"holding event claim not found: {event_id}")

    def mark_holding_event_failed(
        self,
        *,
        event_id: str,
        claim_id: str,
        error: str,
    ) -> None:
        now_dt = self.now_factory()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT attempt_count FROM holding_event_inbox
                WHERE event_id = ? AND state = 'claimed' AND claim_id = ?
                """,
                (event_id, claim_id),
            ).fetchone()
            if not row:
                raise KeyError(f"holding event claim not found: {event_id}")
            attempt = int(row[0]) + 1
            retry_index = min(attempt - 1, len(_RETRY_MINUTES) - 1)
            next_attempt = now_dt + timedelta(minutes=_RETRY_MINUTES[retry_index])
            conn.execute(
                """
                UPDATE holding_event_inbox
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

    def get_holding_event(self, event_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM holding_event_inbox WHERE event_id = ?",
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
    def inspect_holding_event_status(
        cls,
        db_path: Optional[str | Path] = None,
    ) -> Dict[str, Any]:
        """Read inbox evidence without creating or migrating local state."""

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
                WHERE type = 'table' AND name = 'holding_event_inbox'
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
                FROM holding_event_inbox GROUP BY state ORDER BY state
                """
            ).fetchall()
            latest = conn.execute(
                """
                SELECT event_id, state, received_at, updated_at, last_error
                FROM holding_event_inbox
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
                f"holding event inbox status read failed: {path}: {exc}"
            ) from exc
        finally:
            if "conn" in locals():
                conn.close()

    def holding_event_status(self) -> Dict[str, Any]:
        """Return local inbox evidence without asserting remote health."""

        return self.inspect_holding_event_status(self.db_path)

    # ---------- Cash-flow event inbox ----------

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

    # ---------- Typed operation receipt outbox ----------

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
