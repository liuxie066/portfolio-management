"""Durable SQLite workflow state for confirmed cash-flow holding effects.

Feishu remains authoritative for ``cash_flow`` and ``holdings`` facts.  This
store only owns discovery/version state, confirmations, fingerprints, scan
runs, and receipt delivery evidence.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from src import config
from src.time_utils import bj_now_naive


SCHEMA_VERSION = "1"
HASH_CONTRACT_VERSION = "cash-flow-effects.v1"
TERMINAL_STATES = {"applied", "record_only"}
UNRESOLVED_STATES = {
    "scheduled",
    "pending",
    "blocked",
    "previewed",
    "stale",
    "applying",
    "compensation_pending",
}
KNOWN_STATES = TERMINAL_STATES | UNRESOLVED_STATES | {
    "superseded",
    "superseded_by_cash_flow",
}
JSON_COLUMNS = {
    "source_json": "source",
    "before_json": "before",
    "targets_json": "targets",
    "warnings_json": "warnings",
    "confirmation_json": "confirmation",
}


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class CashFlowEffectStore:
    """Single-host durable store; normal construction never creates a database."""

    def __init__(self, db_path: Optional[str | Path] = None):
        self.db_path = self.resolve_db_path(db_path)
        self._transaction_state = threading.local()
        if not self.db_path.exists():
            raise RuntimeError(
                "cash-flow effect database is not initialized: "
                f"{self.db_path}; run `pm cash-flow effects init --cutover-date YYYY-MM-DD --confirm`"
            )
        self._validate_database()

    @staticmethod
    def resolve_db_path(db_path: Optional[str | Path] = None) -> Path:
        configured = db_path or config.get("cash_flow.effects.db_path")
        path = Path(configured).expanduser() if configured else (
            config.get_data_dir() / "cash_flow_effects.sqlite3"
        )
        return path if path.is_absolute() else config.get_data_dir() / path

    @classmethod
    def initialize(
        cls,
        *,
        cutover_date: str | date,
        db_path: Optional[str | Path] = None,
    ) -> "CashFlowEffectStore":
        parsed_cutover = cls._parse_date(cutover_date).isoformat()
        path = cls.resolve_db_path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with cls._connect_path(path) as conn:
                cls._create_schema(conn)
                cls._set_meta_if_absent(conn, "schema_version", SCHEMA_VERSION)
                cls._set_meta_if_absent(conn, "cutover_date", parsed_cutover)
                cls._set_meta_if_absent(conn, "database_id", uuid.uuid4().hex)
                stored = cls._get_meta(conn, "cutover_date")
                if stored != parsed_cutover:
                    raise RuntimeError(
                        "cash-flow effect cutover_date is immutable: "
                        f"database={stored}, requested={parsed_cutover}"
                    )
        except sqlite3.DatabaseError as exc:
            raise RuntimeError(f"cash-flow effect database initialization failed: {path}: {exc}") from exc
        path.chmod(0o600)
        return cls(path)

    @staticmethod
    def _connect_path(path: Path) -> sqlite3.Connection:
        conn = sqlite3.connect(path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 10000")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    @contextmanager
    def _connect(self):
        if not self.db_path.exists():
            raise RuntimeError(f"cash-flow effect database disappeared: {self.db_path}")
        active = getattr(self._transaction_state, "connection", None)
        if active is not None:
            yield active
            return
        try:
            conn = self._connect_path(self.db_path)
        except sqlite3.Error as exc:
            raise RuntimeError(
                f"cash-flow effect database unavailable: {self.db_path}: {exc}"
            ) from exc
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @contextmanager
    def transaction(self):
        """Make nested store operations one SQLite transaction."""
        if getattr(self._transaction_state, "connection", None) is not None:
            raise RuntimeError("nested cash-flow effect transactions are not supported")
        if not self.db_path.exists():
            raise RuntimeError(f"cash-flow effect database disappeared: {self.db_path}")
        conn = self._connect_path(self.db_path)
        self._transaction_state.connection = conn
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._transaction_state.connection = None
            conn.close()

    @staticmethod
    def _create_schema(conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS effect_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS cash_flow_effects (
                effect_id TEXT PRIMARY KEY,
                effect_kind TEXT NOT NULL,
                hash_contract_version TEXT NOT NULL,
                record_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                source_hash TEXT NOT NULL,
                source_json TEXT NOT NULL,
                state TEXT NOT NULL,
                mode TEXT NOT NULL,
                account TEXT NOT NULL,
                broker TEXT NOT NULL,
                currency TEXT NOT NULL,
                signed_amount TEXT NOT NULL,
                flow_date TEXT NOT NULL,
                target_source TEXT,
                before_json TEXT,
                targets_json TEXT,
                preview_hash TEXT,
                warnings_json TEXT,
                confirmation_json TEXT,
                compensation_task_id TEXT,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(effect_kind, record_id, version)
            );
            CREATE INDEX IF NOT EXISTS idx_cash_flow_effects_latest
            ON cash_flow_effects(effect_kind, record_id, version DESC);
            CREATE INDEX IF NOT EXISTS idx_cash_flow_effects_account
            ON cash_flow_effects(account, state, flow_date);
            CREATE TABLE IF NOT EXISTS cash_flow_effect_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                effect_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(effect_id) REFERENCES cash_flow_effects(effect_id)
            );
            CREATE TABLE IF NOT EXISTS cash_flow_scan_runs (
                scan_run_id TEXT PRIMARY KEY,
                scope TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                status TEXT NOT NULL,
                source_record_count INTEGER,
                source_digest TEXT,
                added_count INTEGER NOT NULL DEFAULT 0,
                changed_count INTEGER NOT NULL DEFAULT 0,
                deleted_count INTEGER NOT NULL DEFAULT 0,
                blocked_count INTEGER NOT NULL DEFAULT 0,
                error TEXT
            );
            CREATE TABLE IF NOT EXISTS cash_holding_fingerprints (
                holding_identity TEXT PRIMARY KEY,
                holding_record_id TEXT,
                last_confirmed_amount TEXT,
                last_confirmed_hash TEXT,
                last_observed_amount TEXT,
                last_observed_hash TEXT,
                confirmed_by_effect_id TEXT,
                observed_at TEXT,
                updated_at TEXT NOT NULL
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
            CREATE INDEX IF NOT EXISTS idx_cash_flow_fx_confirmations_record
            ON cash_flow_fx_confirmations(record_id, confirmed_at DESC);
            CREATE TABLE IF NOT EXISTS cash_flow_effect_receipts (
                receipt_key TEXT PRIMARY KEY,
                receipt_type TEXT NOT NULL,
                effect_id TEXT,
                scan_run_id TEXT,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                message_id TEXT,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )

    def _validate_database(self) -> None:
        try:
            with self._connect() as conn:
                integrity = conn.execute("PRAGMA quick_check").fetchone()
                if not integrity or integrity[0] != "ok":
                    raise RuntimeError(
                        f"cash-flow effect database integrity check failed: {integrity}"
                    )
                version = self._get_meta(conn, "schema_version")
                if version != SCHEMA_VERSION:
                    raise RuntimeError(
                        f"unsupported cash-flow effect schema version: {version}; "
                        f"expected {SCHEMA_VERSION}"
                    )
                if not self._get_meta(conn, "cutover_date"):
                    raise RuntimeError("cash-flow effect database lacks immutable cutover_date")
        except sqlite3.DatabaseError as exc:
            raise RuntimeError(
                f"cash-flow effect database is corrupt: {self.db_path}: {exc}"
            ) from exc

    @staticmethod
    def _parse_date(value: str | date) -> date:
        if isinstance(value, date):
            return value
        return date.fromisoformat(str(value)[:10])

    @staticmethod
    def _get_meta(conn: sqlite3.Connection, key: str) -> Optional[str]:
        row = conn.execute("SELECT value FROM effect_meta WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else None

    @staticmethod
    def _set_meta_if_absent(conn: sqlite3.Connection, key: str, value: str) -> None:
        conn.execute(
            "INSERT OR IGNORE INTO effect_meta(key, value, updated_at) VALUES (?, ?, ?)",
            (key, value, bj_now_naive().isoformat()),
        )

    @property
    def cutover_date(self) -> date:
        with self._connect() as conn:
            raw = self._get_meta(conn, "cutover_date")
        if not raw:
            raise RuntimeError("cash-flow effect database lacks cutover_date")
        return date.fromisoformat(raw)

    def assert_cutover(self, configured: str | date | None) -> date:
        if configured in (None, ""):
            raise RuntimeError(
                "cash-flow effect cutover config is missing after database activation"
            )
        requested = self._parse_date(configured)
        stored = self.cutover_date
        if requested != stored:
            raise RuntimeError(
                "cash-flow effect cutover_date is immutable: "
                f"database={stored.isoformat()}, configured={requested.isoformat()}"
            )
        return stored

    def integrity_check(self) -> Dict[str, Any]:
        self._validate_database()
        with self._connect() as conn:
            database_id = self._get_meta(conn, "database_id")
        return {
            "ok": True,
            "db_path": str(self.db_path),
            "schema_version": SCHEMA_VERSION,
            "cutover_date": self.cutover_date.isoformat(),
            "database_id": database_id,
        }

    def backup(self, destination: str | Path) -> Dict[str, Any]:
        """Create a consistent SQLite online backup; never overwrite a file."""
        target = Path(destination).expanduser()
        if not target.is_absolute():
            target = Path.cwd() / target
        if target.exists():
            raise RuntimeError(f"cash-flow effect backup already exists: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._connect() as source, sqlite3.connect(target) as output:
                source.backup(output)
            target.chmod(0o600)
            verification = CashFlowEffectStore(target).integrity_check()
        except Exception:
            # Keep a failed artifact for investigation; never hide it by deleting.
            raise
        return {
            "success": True,
            "source": str(self.db_path),
            "destination": str(target),
            "verification": verification,
        }

    def create_version(
        self,
        *,
        source: Dict[str, Any],
        source_hash: str,
        state: str,
        mode: str,
        effect_kind: str = "cash_flow",
        event_type: str = "discovered",
    ) -> Dict[str, Any]:
        if state not in KNOWN_STATES:
            raise ValueError(f"unknown cash-flow effect state: {state}")
        record_id = str(source["record_id"])
        now = bj_now_naive().isoformat()
        with self._connect() as conn:
            latest = conn.execute(
                """
                SELECT * FROM cash_flow_effects
                WHERE effect_kind = ? AND record_id = ?
                ORDER BY version DESC LIMIT 1
                """,
                (effect_kind, record_id),
            ).fetchone()
            if latest and str(latest["source_hash"]) == source_hash:
                return self._decode(latest)
            version = int(latest["version"]) + 1 if latest else 1
            effect_id = f"cfe_{uuid.uuid4().hex}"
            conn.execute(
                """
                INSERT INTO cash_flow_effects(
                    effect_id, effect_kind, hash_contract_version, record_id,
                    version, source_hash, source_json, state, mode, account,
                    broker, currency, signed_amount, flow_date, created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    effect_id,
                    effect_kind,
                    HASH_CONTRACT_VERSION,
                    record_id,
                    version,
                    source_hash,
                    canonical_json(source),
                    state,
                    mode,
                    str(source.get("account") or ""),
                    str(source.get("broker") or ""),
                    str(source.get("currency") or ""),
                    str(source.get("signed_amount") or "0"),
                    str(source.get("flow_date") or ""),
                    now,
                    now,
                ),
            )
            if latest and latest["state"] in UNRESOLVED_STATES:
                conn.execute(
                    """
                    UPDATE cash_flow_effects SET state = 'superseded', updated_at = ?
                    WHERE effect_id = ?
                    """,
                    (now, latest["effect_id"]),
                )
                self._append_event(
                    conn,
                    str(latest["effect_id"]),
                    "superseded",
                    {"superseded_by": effect_id},
                )
            self._append_event(
                conn,
                effect_id,
                event_type,
                {"source_hash": source_hash, "mode": mode, "effect_kind": effect_kind},
            )
        return self.get_effect(effect_id) or {}

    def update_effect(
        self,
        effect_id: str,
        *,
        state: Optional[str] = None,
        fields: Optional[Dict[str, Any]] = None,
        event_type: str,
        event_payload: Optional[Dict[str, Any]] = None,
        expected_states: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        if state is not None and state not in KNOWN_STATES:
            raise ValueError(f"unknown cash-flow effect state: {state}")
        values: Dict[str, Any] = dict(fields or {})
        if state is not None:
            values["state"] = state
        values["updated_at"] = bj_now_naive().isoformat()
        allowed = {
            "state",
            "target_source",
            "before_json",
            "targets_json",
            "preview_hash",
            "warnings_json",
            "confirmation_json",
            "compensation_task_id",
            "last_error",
            "updated_at",
        }
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"unsupported cash-flow effect fields: {sorted(unknown)}")
        for key in set(JSON_COLUMNS) & set(values):
            values[key] = canonical_json(values[key]) if values[key] is not None else None
        assignments = ", ".join(f"{key} = ?" for key in values)
        params = list(values.values())
        where = "effect_id = ?"
        params.append(effect_id)
        expected = tuple(expected_states or ())
        if expected:
            where += f" AND state IN ({','.join('?' for _ in expected)})"
            params.extend(expected)
        with self._connect() as conn:
            cursor = conn.execute(
                f"UPDATE cash_flow_effects SET {assignments} WHERE {where}",
                params,
            )
            if cursor.rowcount != 1:
                current = conn.execute(
                    "SELECT state FROM cash_flow_effects WHERE effect_id = ?",
                    (effect_id,),
                ).fetchone()
                raise RuntimeError(
                    f"cash-flow effect state conflict: effect_id={effect_id}, "
                    f"state={current['state'] if current else 'missing'}, "
                    f"expected={list(expected)}"
                )
            self._append_event(conn, effect_id, event_type, event_payload or {})
        return self.get_effect(effect_id) or {}

    @staticmethod
    def _append_event(
        conn: sqlite3.Connection,
        effect_id: str,
        event_type: str,
        payload: Dict[str, Any],
    ) -> None:
        conn.execute(
            """
            INSERT INTO cash_flow_effect_events(
                effect_id, event_type, payload_json, created_at
            ) VALUES (?, ?, ?, ?)
            """,
            (
                effect_id,
                event_type,
                canonical_json(payload),
                bj_now_naive().isoformat(),
            ),
        )

    def append_event(
        self,
        effect_id: str,
        event_type: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        with self._connect() as conn:
            self._append_event(conn, effect_id, event_type, payload or {})

    def get_effect(self, effect_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM cash_flow_effects WHERE effect_id = ?",
                (effect_id,),
            ).fetchone()
        return self._decode(row) if row else None

    def get_latest_for_record(
        self,
        record_id: str,
        *,
        effect_kind: str = "cash_flow",
    ) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM cash_flow_effects
                WHERE effect_kind = ? AND record_id = ?
                ORDER BY version DESC LIMIT 1
                """,
                (effect_kind, record_id),
            ).fetchone()
        return self._decode(row) if row else None

    def get_previous_applied(self, effect_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            current = conn.execute(
                """
                SELECT effect_kind, record_id, version
                FROM cash_flow_effects WHERE effect_id = ?
                """,
                (effect_id,),
            ).fetchone()
            if not current:
                return None
            row = conn.execute(
                """
                SELECT * FROM cash_flow_effects
                WHERE effect_kind = ? AND record_id = ? AND version < ?
                  AND state = 'applied'
                ORDER BY version DESC LIMIT 1
                """,
                (
                    current["effect_kind"],
                    current["record_id"],
                    current["version"],
                ),
            ).fetchone()
        return self._decode(row) if row else None

    def list_effects(
        self,
        *,
        account: Optional[str] = None,
        latest_only: bool = True,
        states: Optional[Iterable[str]] = None,
    ) -> list[Dict[str, Any]]:
        conditions: list[str] = []
        params: list[Any] = []
        if account:
            conditions.append("e.account = ?")
            params.append(account)
        selected_states = tuple(states or ())
        if selected_states:
            conditions.append(f"e.state IN ({','.join('?' for _ in selected_states)})")
            params.extend(selected_states)
        if latest_only:
            conditions.append(
                """
                e.version = (
                    SELECT MAX(x.version) FROM cash_flow_effects x
                    WHERE x.effect_kind = e.effect_kind AND x.record_id = e.record_id
                )
                """
            )
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT e.* FROM cash_flow_effects e
                {where}
                ORDER BY e.flow_date, e.effect_kind, e.record_id, e.version
                """,
                params,
            ).fetchall()
        return [self._decode(row) for row in rows]

    def list_blockers(
        self,
        *,
        account: str,
        nav_date: str | date,
    ) -> list[Dict[str, Any]]:
        target_date = self._parse_date(nav_date)
        blockers = []
        for effect in self.list_effects(account=account, latest_only=True):
            state = str(effect["state"])
            if state in TERMINAL_STATES or state.startswith("superseded"):
                continue
            flow_date = self._parse_date(effect["flow_date"])
            if state == "scheduled" and target_date < flow_date:
                continue
            blockers.append(effect)
        return blockers

    def list_events(self, effect_id: str) -> list[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT event_id, event_type, payload_json, created_at
                FROM cash_flow_effect_events
                WHERE effect_id = ? ORDER BY event_id
                """,
                (effect_id,),
            ).fetchall()
        return [
            {
                "event_id": int(row["event_id"]),
                "event_type": row["event_type"],
                "payload": json.loads(row["payload_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def begin_scan(self, *, scope: str) -> str:
        scan_run_id = f"cfs_{uuid.uuid4().hex}"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO cash_flow_scan_runs(
                    scan_run_id, scope, started_at, status
                ) VALUES (?, ?, ?, 'running')
                """,
                (scan_run_id, scope, bj_now_naive().isoformat()),
            )
        return scan_run_id

    def finish_scan(
        self,
        scan_run_id: str,
        *,
        status: str,
        source_record_count: Optional[int] = None,
        source_digest: Optional[str] = None,
        added_count: int = 0,
        changed_count: int = 0,
        deleted_count: int = 0,
        blocked_count: int = 0,
        error: Optional[str] = None,
    ) -> Dict[str, Any]:
        if status not in {"completed", "failed"}:
            raise ValueError("scan status must be completed or failed")
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE cash_flow_scan_runs
                SET completed_at = ?, status = ?, source_record_count = ?,
                    source_digest = ?, added_count = ?, changed_count = ?,
                    deleted_count = ?, blocked_count = ?, error = ?
                WHERE scan_run_id = ? AND status = 'running'
                """,
                (
                    bj_now_naive().isoformat(),
                    status,
                    source_record_count,
                    source_digest,
                    added_count,
                    changed_count,
                    deleted_count,
                    blocked_count,
                    error,
                    scan_run_id,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(f"scan run is not active: {scan_run_id}")
            row = conn.execute(
                "SELECT * FROM cash_flow_scan_runs WHERE scan_run_id = ?",
                (scan_run_id,),
            ).fetchone()
        return dict(row) if row else {}

    def latest_successful_scan(self, *, scope: Optional[str] = None) -> Optional[Dict[str, Any]]:
        condition = "AND scope = ?" if scope else ""
        params: tuple[Any, ...] = (scope,) if scope else ()
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT * FROM cash_flow_scan_runs
                WHERE status = 'completed' {condition}
                ORDER BY completed_at DESC LIMIT 1
                """,
                params,
            ).fetchone()
        return dict(row) if row else None

    def latest_scan(self, *, scope: Optional[str] = None) -> Optional[Dict[str, Any]]:
        condition = "WHERE scope = ?" if scope else ""
        params: tuple[Any, ...] = (scope,) if scope else ()
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT * FROM cash_flow_scan_runs
                {condition}
                ORDER BY started_at DESC LIMIT 1
                """,
                params,
            ).fetchone()
        return dict(row) if row else None

    def get_fingerprint(self, holding_identity: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM cash_holding_fingerprints
                WHERE holding_identity = ?
                """,
                (holding_identity,),
            ).fetchone()
        return dict(row) if row else None

    def list_fingerprints(self, *, account: Optional[str] = None) -> list[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM cash_holding_fingerprints
                ORDER BY holding_identity
                """
            ).fetchall()
        result = [dict(row) for row in rows]
        if account:
            result = [
                row for row in result
                if str(row["holding_identity"]).split("|", 2)[1] == account
            ]
        return result

    def observe_fingerprint(
        self,
        *,
        holding_identity: str,
        holding_record_id: Optional[str],
        amount: str,
        observation_hash: str,
    ) -> Dict[str, Any]:
        now = bj_now_naive().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO cash_holding_fingerprints(
                    holding_identity, holding_record_id, last_observed_amount,
                    last_observed_hash, observed_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(holding_identity) DO UPDATE SET
                    holding_record_id = excluded.holding_record_id,
                    last_observed_amount = excluded.last_observed_amount,
                    last_observed_hash = excluded.last_observed_hash,
                    observed_at = excluded.observed_at,
                    updated_at = excluded.updated_at
                """,
                (
                    holding_identity,
                    holding_record_id,
                    amount,
                    observation_hash,
                    now,
                    now,
                ),
            )
        return self.get_fingerprint(holding_identity) or {}

    def confirm_fingerprint(
        self,
        *,
        holding_identity: str,
        holding_record_id: Optional[str],
        amount: str,
        confirmation_hash: str,
        effect_id: Optional[str],
    ) -> Dict[str, Any]:
        now = bj_now_naive().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO cash_holding_fingerprints(
                    holding_identity, holding_record_id, last_confirmed_amount,
                    last_confirmed_hash, last_observed_amount,
                    last_observed_hash, confirmed_by_effect_id, observed_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(holding_identity) DO UPDATE SET
                    holding_record_id = excluded.holding_record_id,
                    last_confirmed_amount = excluded.last_confirmed_amount,
                    last_confirmed_hash = excluded.last_confirmed_hash,
                    last_observed_amount = excluded.last_observed_amount,
                    last_observed_hash = excluded.last_observed_hash,
                    confirmed_by_effect_id = excluded.confirmed_by_effect_id,
                    observed_at = excluded.observed_at,
                    updated_at = excluded.updated_at
                """,
                (
                    holding_identity,
                    holding_record_id,
                    amount,
                    confirmation_hash,
                    amount,
                    confirmation_hash,
                    effect_id,
                    now,
                    now,
                ),
            )
        return self.get_fingerprint(holding_identity) or {}

    def record_fx_confirmation(
        self,
        *,
        record_id: str,
        source_hash: str,
        exchange_rate: str,
        exchange_rate_date: str,
        exchange_rate_source: str,
        exchange_rate_evidence_type: str,
        cny_amount: str,
        confirmation: Dict[str, Any],
    ) -> str:
        confirmation_id = f"cfx_{uuid.uuid4().hex}"
        now = bj_now_naive().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO cash_flow_fx_confirmations(
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
                    canonical_json(confirmation),
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

    def enqueue_receipt(
        self,
        *,
        receipt_key: str,
        receipt_type: str,
        payload: Dict[str, Any],
        effect_id: Optional[str] = None,
        scan_run_id: Optional[str] = None,
    ) -> bool:
        now = bj_now_naive().isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO cash_flow_effect_receipts(
                    receipt_key, receipt_type, effect_id, scan_run_id,
                    payload_json, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    receipt_key,
                    receipt_type,
                    effect_id,
                    scan_run_id,
                    canonical_json(payload),
                    now,
                    now,
                ),
            )
        return cursor.rowcount == 1

    def list_pending_receipts(self, *, limit: int = 100) -> list[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM cash_flow_effect_receipts
                WHERE status IN ('pending', 'failed')
                ORDER BY created_at LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            result.append(item)
        return result

    def mark_receipt(
        self,
        receipt_key: str,
        *,
        success: bool,
        message_id: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE cash_flow_effect_receipts
                SET status = ?, attempt_count = attempt_count + 1,
                    message_id = ?, last_error = ?, updated_at = ?
                WHERE receipt_key = ?
                """,
                (
                    "sent" if success else "failed",
                    message_id,
                    error,
                    bj_now_naive().isoformat(),
                    receipt_key,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"receipt not found: {receipt_key}")

    @staticmethod
    def _decode(row: sqlite3.Row) -> Dict[str, Any]:
        result = dict(row)
        for column, output_key in JSON_COLUMNS.items():
            raw = result.pop(column, None)
            result[output_key] = json.loads(raw) if raw else None
        return result
