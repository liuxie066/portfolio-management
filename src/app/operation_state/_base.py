"""Shared SQLite connection, schema, and migration for operation state."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
import sqlite3
from typing import Callable
from typing import Iterator
from typing import Optional

from src import config
from src.time_utils import bj_now_naive

from ._schema import (
    CASH_FLOW_EVENT_SCHEMA_VERSION,
    HOLDINGS_WORKFLOW_SCHEMA_VERSION,
    SCHEMA_VERSION,
    initialize_schema,
)

_RETRY_MINUTES = (1, 5, 15, 60)
_CLAIM_LEASE_MINUTES = 5


class OperationStateBase:
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

    @classmethod
    def resolve_db_path(cls, db_path: Optional[str | Path] = None) -> Path:
        return cls.resolve_db_path_read_only(db_path)

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
                else Path(__file__).resolve().parents[3] / ".data"
            )
            if not data_dir.is_absolute():
                data_dir = Path(__file__).resolve().parents[3] / data_dir
            return data_dir / path
        configured = config.get("data.dir")
        data_dir = (
            Path(str(configured)).expanduser()
            if configured
            else Path(__file__).resolve().parents[3] / ".data"
        )
        if not data_dir.is_absolute():
            data_dir = Path(__file__).resolve().parents[3] / data_dir
        return data_dir / "pm_operation_state.sqlite3"

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=10)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout = 10000")
            conn.execute("PRAGMA journal_mode = WAL")
            with conn:
                yield conn
        finally:
            conn.close()

    @contextmanager
    def _connect_inbox_accept(self) -> Iterator[sqlite3.Connection]:
        """Open the pre-initialized inbox with a bounded receiver lock wait."""

        conn = sqlite3.connect(self.db_path, timeout=1)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout = 1000")
            with conn:
                yield conn
        finally:
            conn.close()

    def _initialize(self) -> None:
        with self._connect() as conn:
            initialize_schema(conn)

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
