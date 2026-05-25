"""Compatibility facade for Feishu holdings_snapshot operations."""

from typing import Dict, List

from ..snapshot_models import HoldingSnapshot
from .repositories.snapshots_repository import SnapshotsRepository


class SnapshotsMixin:
    """Expose the historical FeishuStorage holdings_snapshot API via a repository."""

    @property
    def snapshots(self) -> SnapshotsRepository:
        repo = getattr(self, "_snapshots_repository", None)
        if repo is None:
            repo = SnapshotsRepository(self)
            self._snapshots_repository = repo
        return repo

    def batch_upsert_holding_snapshots(
        self,
        snapshots: List[HoldingSnapshot],
        dry_run: bool = False,
    ) -> Dict[str, any]:
        return self.snapshots.batch_upsert_holding_snapshots(
            snapshots,
            dry_run=dry_run,
        )
