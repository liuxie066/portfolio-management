"""Compatibility facade for Feishu nav_history operations."""

from typing import Any, Dict, List, Optional

from ..models import NAVHistory
from .repositories.nav_history_repository import NavHistoryRepository


class NavMixin:
    """Expose the historical FeishuStorage nav_history API via a repository."""

    NAV_INDEX_PROJECTION_FIELDS = NavHistoryRepository.NAV_INDEX_PROJECTION_FIELDS
    NAV_DERIVED_PATCH_FIELDS = NavHistoryRepository.NAV_DERIVED_PATCH_FIELDS

    @property
    def nav_history(self) -> NavHistoryRepository:
        repo = getattr(self, "_nav_history_repository", None)
        if repo is None:
            repo = NavHistoryRepository(self)
            self._nav_history_repository = repo
        return repo

    def audit_nav_history_duplicates(self, account: Optional[str] = None) -> Dict[str, Any]:
        return self.nav_history.audit_nav_history_duplicates(account=account)

    def preload_nav_index(self, account: str, force_refresh: bool = False) -> Dict[str, Any]:
        return self.nav_history.preload_nav_index(account, force_refresh=force_refresh)

    def get_nav_index(self, account: str) -> Dict[str, Any]:
        return self.nav_history.get_nav_index(account)

    def write_nav_record(
        self,
        nav: NAVHistory,
        overwrite_existing: bool = True,
        dry_run: bool = False,
    ):
        return self.nav_history.write_nav_record(
            nav,
            overwrite_existing=overwrite_existing,
            dry_run=dry_run,
        )

    def write_nav_records(
        self,
        nav_list: List[NAVHistory],
        mode: str = "replace",
        allow_partial: bool = False,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        return self.nav_history.write_nav_records(
            nav_list,
            mode=mode,
            allow_partial=allow_partial,
            dry_run=dry_run,
        )

    def get_nav_history(self, account: str, days: int = 365) -> List[NAVHistory]:
        return self.nav_history.get_nav_history(account, days=days)

    def get_latest_nav(self, account: str) -> Optional[NAVHistory]:
        return self.nav_history.get_latest_nav(account)

    def get_nav_on_date(self, account: str, nav_date) -> Optional[NAVHistory]:
        return self.nav_history.get_nav_on_date(account, nav_date)

    def patch_nav_derived_fields(
        self,
        record_id: str,
        fields: Dict[str, Any],
        dry_run: bool = False,
    ):
        return self.nav_history.patch_nav_derived_fields(
            record_id,
            fields,
            dry_run=dry_run,
        )

    def get_latest_nav_before(self, account: str, before_date) -> Optional[NAVHistory]:
        return self.nav_history.get_latest_nav_before(account, before_date)

    def get_total_shares(self, account: str) -> float:
        return self.nav_history.get_total_shares(account)

    def delete_nav_by_record_id(self, record_id: str) -> bool:
        return self.nav_history.delete_nav_by_record_id(record_id)
