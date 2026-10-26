"""Durable single-use Agent name previews; never contains authentication secrets."""
from __future__ import annotations

import json
import sqlite3


class AgentNameMixin:
    def create_agent_name_operation(self, operation: dict) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO agent_name_operations(preview_id, principal_id, record_id, grant_digest, state, expires_at, snapshot_json, result_json) VALUES(?,?,?,?,?,?,?,NULL)",
                (operation['preview_id'], operation['principal_id'], operation['record_id'],
                 operation['grant_digest'], 'previewed', operation['expires_at'],
                 json.dumps(operation, ensure_ascii=False)),
            )

    def get_agent_name_operation(self, preview_id: str) -> dict | None:
        return self.read_agent_name_operation(self.db_path, preview_id)

    @staticmethod
    def read_agent_name_operation(db_path, preview_id: str) -> dict | None:
        # Status must not initialize a database or change journal mode.
        if not db_path.exists():
            return None
        with sqlite3.connect(f'file:{db_path}?mode=ro', uri=True) as conn:
            conn.row_factory = sqlite3.Row
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_name_operations'").fetchone() is None:
                return None
            row = conn.execute('SELECT * FROM agent_name_operations WHERE preview_id=?', (preview_id,)).fetchone()
            return AgentNameMixin._agent_operation_row(row)

    @staticmethod
    def _agent_operation_row(row) -> dict | None:
        if row is None:
            return None
        result = json.loads(row['snapshot_json'])
        result['state'] = row['state']
        result['result'] = json.loads(row['result_json']) if row['result_json'] else None
        return result

    def claim_agent_name_operation(self, preview_id: str, *, principal_id: str,
                                   record_id: str, grant_digest: str, now: float) -> bool:
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            changed = conn.execute(
                "UPDATE agent_name_operations SET state='executing' WHERE preview_id=? AND principal_id=? AND record_id=? AND grant_digest=? AND state='previewed' AND expires_at>?",
                (preview_id, principal_id, record_id, grant_digest, now),
            )
            return changed.rowcount == 1

    def finish_agent_name_operation(self, preview_id: str, *, state: str, result: dict) -> None:
        if state not in {'succeeded', 'noop', 'rejected', 'unknown'}:
            raise ValueError('invalid Agent operation terminal state')
        with self._connect() as conn:
            conn.execute(
                "UPDATE agent_name_operations SET state=?, result_json=? WHERE preview_id=? AND state='executing'",
                (state, json.dumps(result, ensure_ascii=False), preview_id),
            )
