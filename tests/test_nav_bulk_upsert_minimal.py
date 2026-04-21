"""Minimal (no pytest) tests for nav_history bulk upsert behavior."""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from src.feishu_storage import FeishuStorage
from src.models import NAVHistory


class StubLocalNavIndexCache:
    def __init__(self):
        self.accounts: Dict[str, Dict[str, Any]] = {}
        self.upsert_calls = 0

    def get_account(self, account: str) -> Dict[str, Any]:
        return dict(self.accounts.get(account) or {})

    def set_account(self, account: str, payload: Dict[str, Any], _flush: bool = False):
        self.accounts[account] = dict(payload)

    def upsert_nav_records(self, account: str, records: List[Dict[str, Any]], _flush: bool = False):
        self.upsert_calls += 1
        base = dict(self.accounts.get(account) or {})
        navs = list(base.get('nav_history') or [])

        by_date: Dict[str, Dict[str, Any]] = {}
        for r in navs:
            ds = str((r or {}).get('date') or '')
            if ds:
                by_date[ds] = dict(r)

        for r in records:
            ds = str((r or {}).get('date') or '')
            if ds:
                by_date[ds] = dict(r)

        merged = [by_date[k] for k in sorted(by_date.keys())]
        month_end = {}
        year_end = {}
        for r in merged:
            ds = r.get('date') or ''
            ym = ds[:7]
            yy = ds[:4]
            if ym:
                month_end[ym] = dict(r)
            if yy:
                year_end[yy] = dict(r)

        base.update(
            {
                'account': account,
                'nav_history': merged,
                'record_count': len(merged),
                'month_end_base': month_end,
                'year_end_base': year_end,
                'inception_base': dict(merged[0]) if merged else None,
                'last_record': dict(merged[-1]) if merged else None,
                'latest_updated_at': (merged[-1].get('updated_at') if merged else None),
            }
        )
        self.accounts[account] = base


class StubNavBulkClient:
    def __init__(self, initial_records: Optional[List[Dict[str, Any]]] = None):
        self._records = list(initial_records or [])
        self.list_records_calls: List[Dict[str, Any]] = []
        self.batch_update_records_calls: List[List[Dict[str, Any]]] = []
        self.batch_create_records_calls: List[List[Dict[str, Any]]] = []

    def list_records(self, table_name: str, filter_str: str = None, field_names: List[str] = None, page_size: int = 500):
        assert table_name == 'nav_history'
        self.list_records_calls.append(
            {
                'table_name': table_name,
                'filter_str': filter_str,
                'field_names': list(field_names or []),
                'page_size': page_size,
            }
        )

        account = None
        if filter_str and 'CurrentValue.[account] = "' in filter_str:
            account = filter_str.split('CurrentValue.[account] = "', 1)[1].split('"', 1)[0]

        out = []
        for r in self._records:
            if account and (r.get('fields') or {}).get('account') != account:
                continue
            out.append({'record_id': r['record_id'], 'fields': dict(r.get('fields') or {})})
        return out

    def batch_update_records(self, table_name: str, records: List[Dict]):
        assert table_name == 'nav_history'
        self.batch_update_records_calls.append([{'record_id': x['record_id'], 'fields': dict(x['fields'])} for x in records])
        by_id = {r['record_id']: r for r in self._records}
        for rec in records:
            rid = rec['record_id']
            if rid in by_id:
                by_id[rid].setdefault('fields', {}).update(rec.get('fields') or {})
        return [{'record_id': r['record_id'], 'fields': dict(r.get('fields') or {})} for r in records]

    def batch_create_records(self, table_name: str, records: List[Dict[str, Any]]):
        assert table_name == 'nav_history'
        self.batch_create_records_calls.append([{'fields': dict((x.get('fields') or {}))} for x in records])
        result = []
        for i, rec in enumerate(records, start=1):
            new_id = f"rec_nav_new_{len(self._records) + i}"
            fields = dict(rec.get('fields') or {})
            self._records.append({'record_id': new_id, 'fields': fields})
            result.append({'record_id': new_id, 'fields': fields})
        return result


class StubNavSingleWriteClient:
    def __init__(self, initial_records: Optional[List[Dict[str, Any]]] = None):
        self._records = list(initial_records or [])
        self.list_records_calls: List[Dict[str, Any]] = []
        self.update_record_calls: List[Dict[str, Any]] = []
        self.create_record_calls: List[Dict[str, Any]] = []

    def list_records(self, table_name: str, filter_str: str = None, field_names: List[str] = None, page_size: int = 500):
        assert table_name == 'nav_history'
        self.list_records_calls.append(
            {
                'table_name': table_name,
                'filter_str': filter_str,
                'field_names': list(field_names or []),
                'page_size': page_size,
            }
        )

        account = None
        if filter_str and 'CurrentValue.[account] = "' in filter_str:
            account = filter_str.split('CurrentValue.[account] = "', 1)[1].split('"', 1)[0]

        out = []
        for r in self._records:
            if account and (r.get('fields') or {}).get('account') != account:
                continue
            out.append({'record_id': r['record_id'], 'fields': dict(r.get('fields') or {})})
        return out

    def update_record(self, table_name: str, record_id: str, fields: Dict[str, Any]):
        assert table_name == 'nav_history'
        self.update_record_calls.append({'record_id': record_id, 'fields': dict(fields)})
        for rec in self._records:
            if rec.get('record_id') == record_id:
                rec.setdefault('fields', {}).update(dict(fields))
                break
        return {'record_id': record_id, 'fields': dict(fields)}

    def create_record(self, table_name: str, fields: Dict[str, Any]):
        assert table_name == 'nav_history'
        self.create_record_calls.append({'fields': dict(fields)})
        record_id = f"rec_nav_new_{len(self._records) + 1}"
        self._records.append({'record_id': record_id, 'fields': dict(fields)})
        return {'record_id': record_id, 'fields': dict(fields)}


def test_nav_bulk_upsert_uses_single_preload_and_batch_ops_for_n_le_500():
    client = StubNavBulkClient(
        initial_records=[
            {
                'record_id': 'rec_nav_1',
                'fields': {
                    'date': '2026-03-01',
                    'account': 'lx',
                    'total_value': 1000,
                    'shares': 1000,
                    'nav': 1.0,
                    'cash_flow': 0,
                    'pnl': 0,
                    'mtd_nav_change': 0,
                    'ytd_nav_change': 0,
                    'mtd_pnl': 0,
                    'ytd_pnl': 0,
                },
            }
        ]
    )
    nav_idx_cache = StubLocalNavIndexCache()
    storage = FeishuStorage(client=client, local_nav_index_cache=nav_idx_cache)

    payload = [
        NAVHistory(
            date=date(2026, 3, 1),
            account='lx',
            total_value=1100.0,
            shares=1000.0,
            nav=1.1,
            cash_flow=0.0,
            pnl=100.0,
            mtd_nav_change=0.1,
            ytd_nav_change=0.1,
            mtd_pnl=100.0,
            ytd_pnl=100.0,
        ),
        NAVHistory(
            date=date(2026, 3, 2),
            account='lx',
            total_value=1110.0,
            shares=1000.0,
            nav=1.11,
            cash_flow=0.0,
            pnl=10.0,
            mtd_nav_change=0.11,
            ytd_nav_change=0.11,
            mtd_pnl=110.0,
            ytd_pnl=110.0,
        ),
    ]

    result = storage.write_nav_records(payload, mode='replace', dry_run=False)

    # ≤500 records under one account: at most one preload + one batch_update + one batch_create
    assert len(client.list_records_calls) == 1
    assert len(client.batch_update_records_calls) == 1
    assert len(client.batch_update_records_calls[0]) == 1
    assert len(client.batch_create_records_calls) == 1
    assert len(client.batch_create_records_calls[0]) == 1

    assert result['updated'] == 1
    assert result['created'] == 1


def test_nav_bulk_upsert_upsert_mode_keeps_existing_cache_values_for_none_fields():
    client = StubNavBulkClient(
        initial_records=[
            {
                'record_id': 'rec_nav_1',
                'fields': {
                    'date': '2026-03-01',
                    'account': 'lx',
                    'total_value': 1000,
                    'shares': 1000,
                    'nav': 1.0,
                    'cash_flow': 0,
                    'pnl': 0,
                    'mtd_nav_change': 0,
                    'ytd_nav_change': 0,
                    'mtd_pnl': 12.34,
                    'ytd_pnl': 56.78,
                },
            }
        ]
    )
    nav_idx_cache = StubLocalNavIndexCache()
    storage = FeishuStorage(client=client, local_nav_index_cache=nav_idx_cache)

    # mode=upsert: None 字段不应覆盖/清空已有值
    payload = [
        NAVHistory(
            date=date(2026, 3, 1),
            account='lx',
            total_value=1010.0,
            shares=1000.0,
            nav=1.01,
            # mtd_pnl/ytd_pnl omitted -> None
        )
    ]
    storage.write_nav_records(payload, mode='upsert', dry_run=False)

    idx = storage.get_nav_index('lx')
    rows = idx.get('nav_history') or []
    by_date = {r.get('date'): r for r in rows}
    row = by_date['2026-03-01']
    assert row.get('total_value') == 1010.0
    # 关键：缓存应保留旧值，避免与 upsert（不清空）语义冲突
    assert row.get('mtd_pnl') == 12.34
    assert row.get('ytd_pnl') == 56.78


def test_save_nav_refreshes_remote_once_before_create_to_avoid_same_day_duplicates():
    client = StubNavSingleWriteClient(
        initial_records=[
            {
                'record_id': 'rec_nav_1',
                'fields': {
                    'date': '2026-03-01',
                    'account': 'lx',
                    'total_value': 1000,
                    'shares': 1000,
                    'nav': 1.0,
                },
            }
        ]
    )
    nav_idx_cache = StubLocalNavIndexCache()
    nav_idx_cache.set_account(
        'lx',
        {
            'account': 'lx',
            'nav_history': [],
            'record_count': 0,
            'month_end_base': {},
            'year_end_base': {},
            'inception_base': None,
            'last_record': None,
            'latest_updated_at': None,
        },
    )
    storage = FeishuStorage(client=client, local_nav_index_cache=nav_idx_cache)

    nav = NAVHistory(
        date=date(2026, 3, 1),
        account='lx',
        total_value=1100.0,
        shares=1000.0,
        nav=1.1,
    )

    storage.write_nav_record(nav)

    assert len(client.list_records_calls) == 1
    assert len(client.update_record_calls) == 1
    assert len(client.create_record_calls) == 0
    assert nav.record_id == 'rec_nav_1'


def test_save_nav_respects_overwrite_existing_false_before_write():
    client = StubNavSingleWriteClient(
        initial_records=[
            {
                'record_id': 'rec_nav_1',
                'fields': {
                    'date': '2026-03-01',
                    'account': 'lx',
                    'total_value': 1000,
                    'shares': 1000,
                    'nav': 1.0,
                },
            }
        ]
    )
    storage = FeishuStorage(client=client, local_nav_index_cache=StubLocalNavIndexCache())

    nav = NAVHistory(
        date=date(2026, 3, 1),
        account='lx',
        total_value=1100.0,
        shares=1000.0,
        nav=1.1,
    )

    try:
        storage.write_nav_record(nav, overwrite_existing=False)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "已存在同日记录" in str(exc)

    assert len(client.update_record_calls) == 0
    assert len(client.create_record_calls) == 0


def test_write_nav_record_alias_matches_single_full_write_behavior():
    client = StubNavSingleWriteClient()
    storage = FeishuStorage(client=client, local_nav_index_cache=StubLocalNavIndexCache())

    nav = NAVHistory(
        date=date(2026, 3, 2),
        account='lx',
        total_value=1200.0,
        shares=1000.0,
        nav=1.2,
    )

    storage.write_nav_record(nav, dry_run=False)

    assert len(client.create_record_calls) == 1
    assert nav.record_id == 'rec_nav_new_1'


def test_nav_bulk_upsert_updates_nav_index_cache_incrementally():
    client = StubNavBulkClient(
        initial_records=[
            {
                'record_id': 'rec_nav_1',
                'fields': {
                    'date': '2026-03-01',
                    'account': 'lx',
                    'total_value': 1000,
                    'shares': 1000,
                    'nav': 1.0,
                    'cash_flow': 0,
                    'pnl': 0,
                    'mtd_nav_change': 0,
                    'ytd_nav_change': 0,
                    'mtd_pnl': 0,
                    'ytd_pnl': 0,
                },
            }
        ]
    )
    nav_idx_cache = StubLocalNavIndexCache()
    storage = FeishuStorage(client=client, local_nav_index_cache=nav_idx_cache)

    payload = [
        NAVHistory(date=date(2026, 3, 1), account='lx', total_value=1200.0, shares=1000.0, nav=1.2),
        NAVHistory(date=date(2026, 3, 3), account='lx', total_value=1210.0, shares=1000.0, nav=1.21),
    ]
    storage.write_nav_records(payload, mode='replace', dry_run=False)

    # Local cache got an incremental upsert call
    assert nav_idx_cache.upsert_calls >= 1
    acc = nav_idx_cache.accounts.get('lx') or {}
    assert acc.get('record_count') == 2
    dates = [x.get('date') for x in (acc.get('nav_history') or [])]
    assert dates == ['2026-03-01', '2026-03-03']

    # get_nav_index should be served from local cache after invalidation (no extra list_records)
    before = len(client.list_records_calls)
    idx = storage.get_nav_index('lx')
    after = len(client.list_records_calls)
    assert after == before

    rows = idx.get('nav_history') or []
    by_date = {r.get('date'): r for r in rows}
    assert by_date['2026-03-01']['record_id'] == 'rec_nav_1'
    assert str(by_date['2026-03-03']['record_id']).startswith('rec_nav_new_')


def test_patch_nav_derived_fields_rejects_non_derived_fields():
    storage = FeishuStorage(client=StubNavSingleWriteClient(), local_nav_index_cache=StubLocalNavIndexCache())

    try:
        storage.patch_nav_derived_fields('rec_nav_1', {'total_value': 123.45}, dry_run=True)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "illegal field" in str(exc)
