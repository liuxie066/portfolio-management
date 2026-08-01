"""Holdings snapshot persistence service."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from src import config
from src.domain.snapshot_contracts import (
    SNAPSHOT_DIGEST_VERSION,
    NormalizedValuationSnapshot,
    snapshot_digest,
    snapshot_row_payload as snapshot_row_payload,
)
from src.snapshot_models import HoldingSnapshot


class SnapshotService:
    """Persist NAV-time holdings snapshots for auditability and replay."""

    def __init__(self, storage: Any, data_dir: Optional[Path] = None):
        self.storage = storage
        self.data_dir = data_dir or config.get_data_dir()

    def build_holdings_snapshots(
        self,
        *,
        account: str,
        as_of: str,
        normalized_valuation: NormalizedValuationSnapshot,
    ) -> list[HoldingSnapshot]:
        if not isinstance(normalized_valuation, NormalizedValuationSnapshot):
            raise TypeError(
                "snapshot persistence requires NormalizedValuationSnapshot"
            )
        if normalized_valuation.account != account:
            raise ValueError(
                "normalized valuation account mismatch for holdings snapshot"
            )
        return list(normalized_valuation.to_snapshot_rows(as_of=as_of))

    def persist_holdings_snapshot(
        self,
        *,
        account: str,
        today,
        normalized_valuation: NormalizedValuationSnapshot,
        dry_run: bool = False,
    ) -> list[HoldingSnapshot]:
        """Persist holdings_snapshot rows and write a best-effort local copy.

        Feishu write failures are allowed to bubble up because snapshots are part
        of the NAV auditability contract. Local file write failures remain
        best-effort and should not block NAV recording.
        """
        as_of = today.strftime("%Y-%m-%d")
        snapshots = self.build_holdings_snapshots(
            account=account,
            as_of=as_of,
            normalized_valuation=normalized_valuation,
        )

        dry_preview = self.storage.batch_upsert_holding_snapshots(snapshots, dry_run=True)
        should_write_snapshot = bool(dry_preview.get("to_create") or dry_preview.get("to_update"))
        if should_write_snapshot:
            self.storage.batch_upsert_holding_snapshots(snapshots, dry_run=dry_run)

        if not dry_run:
            self._write_local_snapshot(account=account, as_of=as_of, snapshots=snapshots)
        return snapshots

    def _write_local_snapshot(self, *, account: str, as_of: str, snapshots: list[HoldingSnapshot]) -> None:
        try:
            out_dir = self.data_dir / "holdings_snapshot" / account
            out_dir.mkdir(parents=True, exist_ok=True)
            out_file = out_dir / f"{as_of}.json"
            payload = {
                "as_of": as_of,
                "account": account,
                "count": len(snapshots),
                "digest_version": SNAPSHOT_DIGEST_VERSION,
                "digest": snapshot_digest(snapshots),
                "snapshots": [snapshot.model_dump() for snapshot in snapshots],
            }
            out_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("_write_local_snapshot failed for %s/%s: %s", account, as_of, exc)
