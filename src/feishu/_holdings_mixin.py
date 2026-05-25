"""Compatibility facade for Feishu holdings operations."""

from typing import Any, Dict, List, Optional

from ..models import Holding
from .repositories.holdings_repository import HoldingsRepository


class HoldingsMixin:
    """Expose the historical FeishuStorage holdings API via a repository."""

    HOLDING_PROJECTION_FIELDS = HoldingsRepository.HOLDING_PROJECTION_FIELDS

    @property
    def holdings(self) -> HoldingsRepository:
        repo = getattr(self, "_holdings_repository", None)
        if repo is None:
            repo = HoldingsRepository(self)
            self._holdings_repository = repo
        return repo

    def _get_holding_cache_key(self, asset_id: str, account: str, broker: Optional[str]) -> str:
        return self.holdings._get_holding_cache_key(asset_id, account, broker)

    def _snapshot_for_persistent_cache(self, holding: Holding) -> Dict[str, Any]:
        return self.holdings._snapshot_for_persistent_cache(holding)

    def _load_persistent_holdings_index(self):
        return self.holdings._load_persistent_holdings_index()

    def _flush_persistent_holdings_index(self):
        return self.holdings._flush_persistent_holdings_index()

    def _invalidate_holding_cache_by_record_id(
        self,
        record_id: str,
        *,
        flush_persistent: bool = False,
    ):
        return self.holdings._invalidate_holding_cache_by_record_id(
            record_id,
            flush_persistent=flush_persistent,
        )

    def _invalidate_holding_cache(
        self,
        asset_id: str,
        account: str,
        broker: Optional[str],
        *,
        flush_persistent: bool = False,
    ):
        return self.holdings._invalidate_holding_cache(
            asset_id,
            account,
            broker,
            flush_persistent=flush_persistent,
        )

    def _put_holding_cache(self, holding: Holding, *, flush_persistent: bool = False):
        return self.holdings._put_holding_cache(
            holding,
            flush_persistent=flush_persistent,
        )

    def _get_holding_from_cache(
        self,
        asset_id: str,
        account: str,
        broker: Optional[str],
    ) -> Optional[Holding]:
        return self.holdings._get_holding_from_cache(asset_id, account, broker)

    def _get_holding_from_cache_any_market(self, asset_id: str, account: str) -> Optional[Holding]:
        return self.holdings._get_holding_from_cache_any_market(asset_id, account)

    def preload_holdings_index(self, account: Optional[str] = None) -> Dict[str, Any]:
        return self.holdings.preload_holdings_index(account=account)

    def get_holding(
        self,
        asset_id: str,
        account: str,
        broker: Optional[str] = None,
    ) -> Optional[Holding]:
        return self.holdings.get_holding(asset_id, account, broker)

    def get_holdings(
        self,
        account: Optional[str] = None,
        asset_type: Optional[str] = None,
        include_empty: bool = False,
    ) -> List[Holding]:
        return self.holdings.get_holdings(
            account=account,
            asset_type=asset_type,
            include_empty=include_empty,
        )

    def upsert_holding(self, holding: Holding) -> Holding:
        return self.holdings.upsert_holding(holding)

    def replace_holding(self, holding: Holding) -> Holding:
        return self.holdings.replace_holding(holding)

    def upsert_holdings_bulk(
        self,
        holdings: List[Holding],
        mode: str = "additive",
    ) -> Dict[str, Any]:
        return self.holdings.upsert_holdings_bulk(holdings, mode=mode)

    def update_holding_quantity(
        self,
        asset_id: str,
        account: str,
        quantity_change: float,
        broker: Optional[str] = None,
    ):
        return self.holdings.update_holding_quantity(
            asset_id,
            account,
            quantity_change,
            broker,
        )

    def delete_holding_if_zero(
        self,
        asset_id: str,
        account: str,
        broker: Optional[str] = None,
    ):
        return self.holdings.delete_holding_if_zero(asset_id, account, broker)

    def delete_holding_by_record_id(self, record_id: str) -> bool:
        return self.holdings.delete_holding_by_record_id(record_id)

    def _holding_to_dict(self, holding: Holding) -> Dict[str, Any]:
        return self.holdings._holding_to_dict(holding)

    def _dict_to_holding(self, data: Dict[str, Any]) -> Holding:
        return self.holdings._dict_to_holding(data)
