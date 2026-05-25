"""Compatibility facade for Feishu cash_flow operations."""

from datetime import date
from typing import Any, Dict, List, Optional

from ..models import CashFlow
from .repositories.cash_flow_repository import CashFlowRepository


class CashFlowMixin:
    """Expose the historical FeishuStorage cash_flow API via a repository."""

    CASH_FLOW_PROJECTION_FIELDS = CashFlowRepository.CASH_FLOW_PROJECTION_FIELDS
    CASH_FLOW_RECONCILE_FIELDS = CashFlowRepository.CASH_FLOW_RECONCILE_FIELDS

    @property
    def cash_flow(self) -> CashFlowRepository:
        repo = getattr(self, "_cash_flow_repository", None)
        if repo is None:
            repo = CashFlowRepository(self)
            self._cash_flow_repository = repo
        return repo

    def add_cash_flow(self, cf: CashFlow) -> CashFlow:
        return self.cash_flow.add_cash_flow(cf)

    def get_cash_flow(self, record_id: str) -> Optional[CashFlow]:
        return self.cash_flow.get_cash_flow(record_id)

    def preload_cash_flow_aggs(
        self,
        account: str,
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        return self.cash_flow.preload_cash_flow_aggs(
            account,
            force_refresh=force_refresh,
        )

    def _ensure_cash_flow_aggs_loaded(self, account: str):
        return self.cash_flow._ensure_cash_flow_aggs_loaded(account)

    def get_cash_flow_aggs(self, account: str) -> Dict[str, Any]:
        return self.cash_flow.get_cash_flow_aggs(account)

    def get_cash_flows(
        self,
        account: Optional[str] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> List[CashFlow]:
        return self.cash_flow.get_cash_flows(
            account=account,
            start_date=start_date,
            end_date=end_date,
        )

    def get_total_cash_flow_cny(self, account: str) -> float:
        return self.cash_flow.get_total_cash_flow_cny(account)

    def _cash_flow_cny_amount_or_raise(self, cf: CashFlow) -> float:
        return self.cash_flow._cash_flow_cny_amount_or_raise(cf)

    def _cash_flow_cny_amount_from_fields(
        self,
        fields: Dict[str, Any],
        record_id: Optional[str],
    ) -> float:
        return self.cash_flow._cash_flow_cny_amount_from_fields(fields, record_id)

    def reconcile_cash_flows(
        self,
        account: Optional[str] = None,
        *,
        dry_run: bool = True,
        fx_rates: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        return self.cash_flow.reconcile_cash_flows(
            account=account,
            dry_run=dry_run,
            fx_rates=fx_rates,
        )

    def _parse_cash_flow_manual_fields(self, fields: Dict[str, Any]) -> Dict[str, Any]:
        return self.cash_flow._parse_cash_flow_manual_fields(fields)

    def _resolve_cash_flow_exchange_rate(
        self,
        *,
        currency: str,
        amount: float,
        cny_amount: Optional[float],
        rate_cache: Dict[str, float],
    ) -> float:
        return self.cash_flow._resolve_cash_flow_exchange_rate(
            currency=currency,
            amount=amount,
            cny_amount=cny_amount,
            rate_cache=rate_cache,
        )

    def _invalidate_cash_flow_agg_cache(self, accounts: set[str]):
        return self.cash_flow._invalidate_cash_flow_agg_cache(accounts)

    def _cash_flow_to_dict(self, cf: CashFlow) -> Dict[str, Any]:
        return self.cash_flow._cash_flow_to_dict(cf)

    def _dict_to_cash_flow(self, data: Dict[str, Any]) -> CashFlow:
        return self.cash_flow._dict_to_cash_flow(data)

    def delete_cash_flow_by_record_id(self, record_id: str) -> bool:
        return self.cash_flow.delete_cash_flow_by_record_id(record_id)
