"""Compatibility facade for Feishu holdings_snapshot operations."""

from typing import Any, Dict, Iterable, List

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

    def list_holding_snapshots_fresh(
        self,
        *,
        account: str,
        as_of: str,
    ) -> List[HoldingSnapshot]:
        return self.snapshots.list_holding_snapshots_fresh(
            account=account,
            as_of=as_of,
        )

    def apply_holding_snapshot_actions(
        self,
        *,
        actions: Any,
        current: Iterable[HoldingSnapshot],
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        return self.snapshots.apply_holding_snapshot_actions(
            actions=actions,
            current=current,
            dry_run=dry_run,
        )
