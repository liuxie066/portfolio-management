"""Holding Case operations mixin for OperationStateStore."""
from __future__ import annotations

from ..holding_case_contract import (
    LEGACY_PRECONDITION_CONTRACT_VERSION,
    PRECONDITION_CONTRACT_VERSION,
    PRECONDITION_EXACT,
    PRECONDITION_LEGACY_MIGRATABLE,
    classify_precondition_transition,
    confirmation_scope,
)
from typing import Any
from typing import Dict
from typing import Optional
from pathlib import Path
import json
import sqlite3
from ._base import _canonical_json

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



class HoldingCaseMixin:
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

    def materialize_holding_cases(
        self,
        *,
        cases: list[Dict[str, Any]],
        discovery_receipts: list[Dict[str, Any]],
        trigger: Optional[Dict[str, Any]] = None,
        enqueue_receipts: bool = True,
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
                enqueue_receipts=enqueue_receipts,
            )

    def _materialize_holding_cases_tx(
        self,
        conn: sqlite3.Connection,
        *,
        cases: list[Dict[str, Any]],
        discovery_receipts: list[Dict[str, Any]],
        trigger: Optional[Dict[str, Any]],
        now: str,
        enqueue_receipts: bool = True,
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
                if enqueue_receipts and self._insert_repeatable_closure_receipt_tx(
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
            if enqueue_receipts and self._insert_operation_receipt_tx(
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
        enqueue_receipts: bool = True,
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
                enqueue_receipts=enqueue_receipts,
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
        enqueue_receipts: bool = True,
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
            if enqueue_receipts and self._insert_repeatable_closure_receipt_tx(
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
