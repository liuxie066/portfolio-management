"""Operation state SQLite schema: DDL, migrations, and version bookkeeping."""

import sqlite3

SCHEMA_VERSION = "2"
HOLDINGS_WORKFLOW_SCHEMA_VERSION = "1"
CASH_FLOW_EVENT_SCHEMA_VERSION = "1"


def initialize_schema(conn: sqlite3.Connection) -> None:
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

