"""Fx Confirmation operations mixin for OperationStateStore."""
from __future__ import annotations

from typing import Any
from typing import Dict
from typing import Optional
from pathlib import Path
import json
import sqlite3
from .._json import canonical_json as _canonical_json



class FxConfirmationMixin:
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
