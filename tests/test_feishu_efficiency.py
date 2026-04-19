"""Tests for Feishu API efficiency optimizations."""
from datetime import date
from unittest.mock import Mock, patch, MagicMock

from src.models import AssetType, Holding


def test_get_holdings_uses_cache_when_loaded():
    """get_holdings should serve from cache when holdings index is loaded."""
    from src.feishu._holdings_mixin import HoldingsMixin

    # Create a minimal instance with mixin
    mixin = HoldingsMixin.__new__(HoldingsMixin)
    mixin._holdings_index_loaded_all = True
    mixin._holdings_index_loaded_accounts = set()
    mixin._holding_fields_cache = {
        ('000001', 'a', None): {
            'asset_id': '000001',
            'asset_name': '平安银行',
            'asset_type': 'a_stock',
            'account': 'a',
            'quantity': 100.0,
            'currency': 'CNY',
            'record_id': 'rec1',
        },
        ('000002', 'a', None): {
            'asset_id': '000002',
            'asset_name': '万科A',
            'asset_type': 'a_stock',
            'account': 'a',
            'quantity': 0.0,  # empty holding
            'currency': 'CNY',
            'record_id': 'rec2',
        },
    }
    mixin._holding_id_cache = {}
    mixin.client = Mock()

    holdings = mixin.get_holdings(account='a')

    # Should NOT call API
    mixin.client.list_records.assert_not_called()
    # Should return only non-empty holdings
    assert len(holdings) == 1
    assert holdings[0].asset_id == '000001'


def test_get_holdings_includes_empty_when_requested():
    """get_holdings with include_empty=True should include zero-quantity holdings."""
    from src.feishu._holdings_mixin import HoldingsMixin

    mixin = HoldingsMixin.__new__(HoldingsMixin)
    mixin._holdings_index_loaded_all = True
    mixin._holdings_index_loaded_accounts = set()
    mixin._holding_fields_cache = {
        ('000001', 'a', None): {
            'asset_id': '000001', 'asset_name': '平安银行', 'asset_type': 'a_stock',
            'account': 'a', 'quantity': 100.0, 'currency': 'CNY', 'record_id': 'rec1',
        },
        ('000002', 'a', None): {
            'asset_id': '000002', 'asset_name': '万科A', 'asset_type': 'a_stock',
            'account': 'a', 'quantity': 0.0, 'currency': 'CNY', 'record_id': 'rec2',
        },
    }
    mixin._holding_id_cache = {}
    mixin.client = Mock()

    holdings = mixin.get_holdings(account='a', include_empty=True)
    assert len(holdings) == 2
    mixin.client.list_records.assert_not_called()


def test_get_holdings_falls_through_when_cache_not_loaded():
    """get_holdings should call API when cache is not loaded."""
    from src.feishu._holdings_mixin import HoldingsMixin

    mixin = HoldingsMixin.__new__(HoldingsMixin)
    mixin._holdings_index_loaded_all = False
    mixin._holdings_index_loaded_accounts = set()
    mixin._holding_fields_cache = {}
    mixin._holding_id_cache = {}
    mixin.client = Mock()
    mixin.client.list_records.return_value = []
    mixin.HOLDING_PROJECTION_FIELDS = ['asset_id', 'asset_name']
    mixin._escape_filter_value = lambda v: v

    holdings = mixin.get_holdings(account='a')

    mixin.client.list_records.assert_called_once()
    assert holdings == []
    # After call, account 'a' should be marked as loaded
    assert 'a' in mixin._holdings_index_loaded_accounts


def test_get_holdings_with_asset_type_bypasses_cache():
    """get_holdings with asset_type filter should always call API."""
    from src.feishu._holdings_mixin import HoldingsMixin

    mixin = HoldingsMixin.__new__(HoldingsMixin)
    mixin._holdings_index_loaded_all = True
    mixin._holdings_index_loaded_accounts = set()
    mixin._holding_fields_cache = {}
    mixin._holding_id_cache = {}
    mixin.client = Mock()
    mixin.client.list_records.return_value = []
    mixin.HOLDING_PROJECTION_FIELDS = ['asset_id']
    mixin._escape_filter_value = lambda v: v

    holdings = mixin.get_holdings(asset_type='a_stock')

    # asset_type filter forces API call even when cache is loaded
    mixin.client.list_records.assert_called_once()


def test_get_transactions_pushes_date_filter_to_server():
    """get_transactions should include date conditions in the Feishu filter."""
    from src.feishu._transactions_mixin import TransactionsMixin

    mixin = TransactionsMixin.__new__(TransactionsMixin)
    mixin.client = Mock()
    mixin.client.list_records.return_value = []
    mixin._escape_filter_value = lambda v: v

    mixin.get_transactions(
        account='a',
        start_date=date(2025, 1, 1),
        end_date=date(2025, 3, 31),
    )

    call_args = mixin.client.list_records.call_args
    filter_str = call_args[1].get('filter_str') or call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get('filter_str')

    assert '2025-01-01' in filter_str
    assert '2025-03-31' in filter_str
    assert 'tx_date' in filter_str


def test_get_total_cash_flow_cny_uses_agg_cache():
    """get_total_cash_flow_cny should use aggregation cache when available."""
    from src.feishu._cash_flow_mixin import CashFlowMixin

    mixin = CashFlowMixin.__new__(CashFlowMixin)
    mixin._cash_flow_agg_mem_cache = {'a': {'cumulative': 50000.0}}
    mixin._cash_flow_agg_loaded_accounts = {'a'}
    mixin.client = Mock()

    # Mock _ensure_cash_flow_aggs_loaded to do nothing (already loaded)
    mixin._ensure_cash_flow_aggs_loaded = Mock()

    result = mixin.get_total_cash_flow_cny('a')

    assert result == 50000.0
    mixin.client.list_records.assert_not_called()
