import inspect
from unittest.mock import Mock

from src.feishu._cash_flow_mixin import CashFlowMixin
from src.feishu._holdings_mixin import HoldingsMixin
from src.feishu._nav_mixin import NavMixin
from src.feishu._snapshots_mixin import SnapshotsMixin
from src.feishu._transactions_mixin import TransactionsMixin
from src.feishu.repositories import (
    CashFlowRepository,
    HoldingsRepository,
    NavHistoryRepository,
    SnapshotsRepository,
    TransactionsRepository,
)
from src.feishu_storage import FeishuStorage


def test_feishu_storage_exposes_stable_nav_history_repository():
    storage = FeishuStorage(client=Mock())

    assert isinstance(storage.nav_history, NavHistoryRepository)
    assert storage.nav_history is storage.nav_history


def test_nav_mixin_stays_thin_and_delegates_nav_writes(monkeypatch):
    storage = FeishuStorage(client=Mock())
    calls = []

    def fake_write_nav_records(nav_list, mode="replace", allow_partial=False, dry_run=False):
        calls.append((nav_list, mode, allow_partial, dry_run))
        return {"ok": True}

    monkeypatch.setattr(storage.nav_history, "write_nav_records", fake_write_nav_records)

    result = storage.write_nav_records([], mode="upsert", allow_partial=True, dry_run=True)

    assert result == {"ok": True}
    assert calls == [([], "upsert", True, True)]

    source = inspect.getsource(NavMixin)
    assert "list_records(" not in source
    assert "batch_create_records(" not in source
    assert "batch_update_records(" not in source


def test_feishu_storage_exposes_stable_holdings_repository():
    storage = FeishuStorage(client=Mock())

    assert isinstance(storage.holdings, HoldingsRepository)
    assert storage.holdings is storage.holdings


def test_holdings_mixin_stays_thin_and_delegates_holdings_reads(monkeypatch):
    storage = FeishuStorage(client=Mock())
    calls = []

    def fake_get_holdings(account=None, asset_type=None, include_empty=False):
        calls.append((account, asset_type, include_empty))
        return []

    monkeypatch.setattr(storage.holdings, "get_holdings", fake_get_holdings)

    result = storage.get_holdings(account="lx", asset_type="cash", include_empty=True)

    assert result == []
    assert calls == [("lx", "cash", True)]

    source = inspect.getsource(HoldingsMixin)
    assert "list_records(" not in source
    assert "create_record(" not in source
    assert "batch_create_records(" not in source
    assert "batch_update_records(" not in source


def test_feishu_storage_exposes_stable_cash_flow_repository():
    storage = FeishuStorage(client=Mock())

    assert isinstance(storage.cash_flow, CashFlowRepository)
    assert storage.cash_flow is storage.cash_flow


def test_cash_flow_mixin_stays_thin_and_delegates_reconcile(monkeypatch):
    storage = FeishuStorage(client=Mock())
    calls = []

    def fake_reconcile_cash_flows(account=None, *, dry_run=True, fx_rates=None):
        calls.append((account, dry_run, fx_rates))
        return {"success": True}

    monkeypatch.setattr(storage.cash_flow, "reconcile_cash_flows", fake_reconcile_cash_flows)

    result = storage.reconcile_cash_flows(
        account="lx",
        dry_run=False,
        fx_rates={"USDCNY": 7.1},
    )

    assert result == {"success": True}
    assert calls == [("lx", False, {"USDCNY": 7.1})]

    source = inspect.getsource(CashFlowMixin)
    assert "list_records(" not in source
    assert "create_record(" not in source
    assert "batch_update_records(" not in source


def test_feishu_storage_exposes_stable_transactions_repository():
    storage = FeishuStorage(client=Mock())

    assert isinstance(storage.transactions, TransactionsRepository)
    assert storage.transactions is storage.transactions


def test_transactions_mixin_stays_thin_and_delegates_dedup_lookup(monkeypatch):
    storage = FeishuStorage(client=Mock())
    calls = []

    def fake_find_by_dedup_key(table, dedup_key):
        calls.append((table, dedup_key))
        return "rec_1"

    monkeypatch.setattr(storage.transactions, "_find_by_dedup_key", fake_find_by_dedup_key)

    result = storage._find_by_dedup_key("cash_flow", "key_1")

    assert result == "rec_1"
    assert calls == [("cash_flow", "key_1")]

    source = inspect.getsource(TransactionsMixin)
    assert "list_records(" not in source
    assert "create_record(" not in source


def test_feishu_storage_exposes_stable_snapshots_repository():
    storage = FeishuStorage(client=Mock())

    assert isinstance(storage.snapshots, SnapshotsRepository)
    assert storage.snapshots is storage.snapshots


def test_snapshots_mixin_stays_thin_and_delegates_batch_upsert(monkeypatch):
    storage = FeishuStorage(client=Mock())
    calls = []

    def fake_batch_upsert_holding_snapshots(snapshots, dry_run=False):
        calls.append((snapshots, dry_run))
        return {"dry_run": dry_run, "created": 0, "updated": 0}

    monkeypatch.setattr(
        storage.snapshots,
        "batch_upsert_holding_snapshots",
        fake_batch_upsert_holding_snapshots,
    )

    result = storage.batch_upsert_holding_snapshots([], dry_run=True)

    assert result == {"dry_run": True, "created": 0, "updated": 0}
    assert calls == [([], True)]

    source = inspect.getsource(SnapshotsMixin)
    assert "list_records(" not in source
    assert "batch_create_records(" not in source
    assert "batch_update_records(" not in source
