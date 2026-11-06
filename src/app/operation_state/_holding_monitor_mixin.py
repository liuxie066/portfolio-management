"""Atomic technical baselines, scheduling, audit and outbox for holdings observations."""
from __future__ import annotations

import json
import sqlite3
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from .._json import canonical_json
from ._receipt_tx import insert_operation_receipt_tx


def initialize_holding_monitor_schema(conn: sqlite3.Connection) -> None:
    statements = """
        CREATE TABLE IF NOT EXISTS holding_change_monitor (
            scope_key TEXT PRIMARY KEY, file_token TEXT NOT NULL, table_id TEXT NOT NULL,
            settings_json TEXT NOT NULL, epoch TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 0,
            ready INTEGER NOT NULL DEFAULT 0, rows_json TEXT NOT NULL DEFAULT '{}',
            requested INTEGER NOT NULL DEFAULT 0, completed INTEGER NOT NULL DEFAULT 0,
            observation_seq INTEGER NOT NULL DEFAULT 0, next_due TEXT,
            failure_count INTEGER NOT NULL DEFAULT 0,
            manual_attempt_at TEXT, manual_success_at TEXT, manual_error TEXT,
            background_attempt_at TEXT, background_success_at TEXT, background_error TEXT
        );
        CREATE TABLE IF NOT EXISTS holding_change_observations (
            scope_key TEXT NOT NULL, epoch TEXT NOT NULL, observation_seq INTEGER NOT NULL,
            mode TEXT NOT NULL, observed_at TEXT NOT NULL, changes_json TEXT NOT NULL,
            PRIMARY KEY(scope_key, epoch, observation_seq)
        );
    """
    for statement in statements.split(";"):
        if statement.strip():
            conn.execute(statement)


class HoldingMonitorMixin:
    @staticmethod
    def _decode_monitor(row: Any) -> dict[str, Any] | None:
        if row is None:
            return None
        state = dict(row)
        state["settings"] = json.loads(state.pop("settings_json"))
        state["rows"] = json.loads(state.pop("rows_json"))
        state["enabled"] = bool(state["enabled"])
        state["ready"] = bool(state["ready"])
        return state

    @classmethod
    def read_holding_monitor(cls, scope_key: str, db_path: str | Path | None = None) -> dict[str, Any] | None:
        path = cls.resolve_db_path_read_only(db_path)
        if not path.exists():
            return None
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='holding_change_monitor'").fetchone()
            if not exists:
                return None
            version = conn.execute("SELECT value FROM operation_meta WHERE key='holding_change_monitor_schema_version'").fetchone()
            if not version or version[0] != "1":
                raise RuntimeError("unsupported holdings change monitor schema version")
            state = cls._decode_monitor(conn.execute("SELECT * FROM holding_change_monitor WHERE scope_key=?", (scope_key,)).fetchone())
            if state:
                prefix = f"holdings:changes:{scope_key}:"
                statuses = conn.execute("""SELECT status,COUNT(*) FROM operation_receipt_outbox
                    WHERE substr(receipt_key,1,?)=? GROUP BY status""", (len(prefix), prefix)).fetchall()
                state["receipts"] = {str(row[0]): int(row[1]) for row in statuses}
            return state
        finally:
            conn.close()

    def initialize_holding_monitor(self, *, scope_key: str, file_token: str, table_id: str,
                                   settings: dict, rows: dict, rebuild: bool = False) -> dict:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            old = conn.execute("SELECT * FROM holding_change_monitor WHERE scope_key=?", (scope_key,)).fetchone()
            if old and not rebuild:
                raise ValueError("monitor baseline already exists; preview and explicitly --rebuild")
            epoch = uuid4().hex
            conn.execute("DELETE FROM holding_change_monitor WHERE scope_key=?", (scope_key,))
            conn.execute("""INSERT INTO holding_change_monitor(
                scope_key,file_token,table_id,settings_json,epoch,ready,rows_json)
                VALUES(?,?,?,?,?,1,?)""", (scope_key, file_token, table_id, canonical_json(settings), epoch, canonical_json(rows)))
        return self.read_holding_monitor(scope_key, self.db_path)

    def configure_holding_monitor(self, scope_key: str, *, settings: dict, enabled: bool) -> bool:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT settings_json FROM holding_change_monitor WHERE scope_key=? AND ready=1", (scope_key,)).fetchone()
            valid = bool(row and row[0] == canonical_json(settings))
            conn.execute("UPDATE holding_change_monitor SET enabled=? WHERE scope_key=?", (int(enabled and valid), scope_key))
            return valid

    def request_holding_monitor(self, scope_key: str, *, seconds: float = 0) -> None:
        due = (self.now_factory() + timedelta(seconds=seconds)).isoformat()
        with self._connect() as conn:
            conn.execute("""UPDATE holding_change_monitor SET requested=requested+1,
                next_due=CASE WHEN failure_count>0 THEN next_due
                    WHEN next_due IS NULL OR next_due>? THEN ? ELSE next_due END
                WHERE scope_key=? AND ready=1 AND enabled=1""", (due, due, scope_key))

    def fail_holding_monitor(self, scope_key: str, *, mode: str, error: str) -> None:
        if mode not in {"manual", "background"}:
            raise ValueError("invalid monitor mode")
        now = self.now_factory()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT failure_count FROM holding_change_monitor WHERE scope_key=?", (scope_key,)).fetchone()
            if mode == "background" and row:
                count = int(row[0]) + 1
                delay = (2, 5, 15, 30, 60)[min(count - 1, 4)]
                conn.execute("""UPDATE holding_change_monitor SET failure_count=?,next_due=?,
                    background_attempt_at=?,background_error=? WHERE scope_key=?""",
                    (count, (now + timedelta(seconds=delay)).isoformat(), now.isoformat(), error, scope_key))
            else:
                conn.execute("UPDATE holding_change_monitor SET manual_attempt_at=?,manual_error=? WHERE scope_key=?",
                             (now.isoformat(), error, scope_key))

    def settle_holding_monitor(self, *, scope_key: str, settings: dict, epoch: str, mode: str,
                               generation: int, plan: dict, duplicate_plan: dict,
                               receipt_builder: Any, interval: float) -> dict:
        if mode not in {"manual", "background"}:
            raise ValueError("invalid monitor mode")
        now = self.now_factory()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            state = self._decode_monitor(conn.execute("SELECT * FROM holding_change_monitor WHERE scope_key=?", (scope_key,)).fetchone())
            if not state or state["epoch"] != epoch or state["settings"] != settings:
                raise ValueError("monitor scope changed; reinitialize explicitly")
            seq = state["observation_seq"] + 1
            case_result = self._reconcile_duplicate_cases_tx(conn, plan=duplicate_plan, now=now.isoformat())
            keys = list(case_result.get("enqueued_receipt_keys") or [])
            for receipt in receipt_builder(epoch, seq, plan["changes"]):
                if insert_operation_receipt_tx(conn, now=now.isoformat(), **receipt):
                    keys.append(receipt["receipt_key"])
            conn.execute("""INSERT INTO holding_change_observations VALUES(?,?,?,?,?,?)""",
                         (scope_key, epoch, seq, mode, now.isoformat(), canonical_json(plan["changes"])))
            conn.execute(f"""UPDATE holding_change_monitor SET rows_json=?,observation_seq=?,
                {mode}_attempt_at=?,{mode}_success_at=?,{mode}_error=NULL WHERE scope_key=?""",
                (canonical_json(plan["rows"]), seq, now.isoformat(), now.isoformat(), scope_key))
            if mode == "background":
                delay = 2 if plan["has_missing_candidates"] else interval
                due = now + timedelta(seconds=delay)
                if state["requested"] > generation:
                    due = min(due, now + timedelta(seconds=2))
                conn.execute("""UPDATE holding_change_monitor SET completed=?,next_due=?,failure_count=0 WHERE scope_key=?""",
                             (generation, due.isoformat(), scope_key))
        return {"observation_seq": seq, "receipt_keys": keys, "case_result": case_result}
