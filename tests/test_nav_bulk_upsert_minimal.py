"""Minimal (no pytest) tests for nav_history bulk upsert behavior."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from threading import Event, Lock, Thread

import pytest
from typing import Any, Dict, List, Optional

from src import config
from src.feishu_storage import FeishuStorage
from src.feishu_client import FeishuBatchWriteError
from src.feishu.repositories.nav_history_repository import NavHistoryReadIntegrityError
from src.app.nav_finality import evaluate_nav_finality
from src.models import NAVHistory


@pytest.fixture(autouse=True)
def _use_temporary_nav_lock_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "get_data_dir", lambda: tmp_path)


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
        self.delete_record_calls: List[str] = []

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

    def delete_record(self, table_name: str, record_id: str):
        assert table_name == 'nav_history'
        self.delete_record_calls.append(record_id)
        self._records = [row for row in self._records if row.get('record_id') != record_id]
        return True


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


def test_nav_batch_fallback_does_not_replay_confirmed_partial_chunk():
    class PartialClient(StubNavBulkClient):
        def batch_update_records(self, table_name: str, records: List[Dict]):
            self.batch_update_records_calls.append(records)
            raise FeishuBatchWriteError(
                operation='update',
                table_name=table_name,
                chunk_offset=500,
                reason='FieldNameNotFound',
                confirmed_results=[{'record_id': records[0]['record_id']}],
            )

    client = PartialClient(initial_records=[{
        'record_id': 'rec_nav_1',
        'fields': {
            'date': '2026-03-01',
            'account': 'lx',
            'total_value': 1000,
            'shares': 1000,
            'nav': 1.0,
        },
    }])
    storage = FeishuStorage(client=client, local_nav_index_cache=StubLocalNavIndexCache())

    try:
        storage.write_nav_records([
            NAVHistory(date=date(2026, 3, 1), account='lx', total_value=1100, shares=1000, nav=1.1)
        ], mode='replace', dry_run=False)
        assert False, 'expected FeishuBatchWriteError'
    except FeishuBatchWriteError as exc:
        assert len(exc.confirmed_results) == 1

    assert len(client.batch_update_records_calls) == 1


def _finality_details(nav_date: str) -> Dict[str, Any]:
    return {
        'finality': {
            'version': 1,
            'status': 'final',
            'nav_date': nav_date,
            'valuation_as_of': f'{nav_date}T08:00:00+08:00',
            'writer': 'daily-nav-job',
            'write_reason': 'canonical_daily_nav_job',
            'run_id': f'run-{nav_date}',
        }
    }


def test_single_nav_create_fails_closed_when_feishu_rejects_required_finality_details():
    class MissingDetailsClient(StubNavSingleWriteClient):
        def create_record(self, table_name: str, fields: Dict[str, Any]):
            assert table_name == 'nav_history'
            self.create_record_calls.append({'fields': dict(fields)})
            raise Exception('FieldNameNotFound: details')

    client = MissingDetailsClient()
    local_cache = StubLocalNavIndexCache()
    storage = FeishuStorage(client=client, local_nav_index_cache=local_cache)
    nav = NAVHistory(
        date=date(2026, 3, 9),
        account='lx',
        total_value=1000.0,
        shares=1000.0,
        nav=1.0,
        details=_finality_details('2026-03-09'),
    )

    with pytest.raises(RuntimeError, match=r'refusing create retry without required details\.finality'):
        storage.write_nav_record(nav)

    assert len(client.create_record_calls) == 1
    assert 'details' in client.create_record_calls[0]['fields']
    assert nav.record_id is None
    assert local_cache.upsert_calls == 0


def test_single_legacy_nav_create_still_retries_without_optional_details():
    class MissingDetailsOnceClient(StubNavSingleWriteClient):
        def create_record(self, table_name: str, fields: Dict[str, Any]):
            assert table_name == 'nav_history'
            self.create_record_calls.append({'fields': dict(fields)})
            if len(self.create_record_calls) == 1:
                raise Exception('FieldNameNotFound: details')
            record_id = 'rec_nav_new_legacy'
            self._records.append({'record_id': record_id, 'fields': dict(fields)})
            return {'record_id': record_id, 'fields': dict(fields)}

    client = MissingDetailsOnceClient()
    local_cache = StubLocalNavIndexCache()
    storage = FeishuStorage(client=client, local_nav_index_cache=local_cache)
    nav = NAVHistory(
        date=date(2026, 3, 9),
        account='lx',
        total_value=1000.0,
        shares=1000.0,
        nav=1.0,
        details={'source': 'legacy-low-level-writer'},
    )

    storage.write_nav_record(nav)

    assert len(client.create_record_calls) == 2
    assert 'details' in client.create_record_calls[0]['fields']
    assert 'details' not in client.create_record_calls[1]['fields']
    assert nav.record_id == 'rec_nav_new_legacy'
    assert local_cache.upsert_calls == 1
    assert local_cache.get_account('lx')['nav_history'][0]['details'] is None


def test_single_nav_update_fails_closed_when_feishu_rejects_required_finality_details():
    class MissingDetailsClient(StubNavSingleWriteClient):
        def update_record(self, table_name: str, record_id: str, fields: Dict[str, Any]):
            assert table_name == 'nav_history'
            self.update_record_calls.append({'record_id': record_id, 'fields': dict(fields)})
            raise Exception('FieldNameNotFound: details')

    initial_fields = {
        'date': '2026-03-09',
        'account': 'lx',
        'total_value': 900.0,
        'shares': 1000.0,
        'nav': 0.9,
    }
    client = MissingDetailsClient(initial_records=[{'record_id': 'rec_nav_1', 'fields': initial_fields}])
    local_cache = StubLocalNavIndexCache()
    storage = FeishuStorage(client=client, local_nav_index_cache=local_cache)
    nav = NAVHistory(
        date=date(2026, 3, 9),
        account='lx',
        total_value=1000.0,
        shares=1000.0,
        nav=1.0,
        details=_finality_details('2026-03-09'),
    )

    with pytest.raises(RuntimeError, match=r'refusing update retry without required details\.finality'):
        storage.write_nav_record(nav, overwrite_existing=True)

    assert len(client.update_record_calls) == 1
    assert 'details' in client.update_record_calls[0]['fields']
    assert client._records[0]['fields'] == initial_fields
    assert local_cache.upsert_calls == 0


def test_bulk_nav_create_fails_closed_when_feishu_rejects_required_finality_details():
    class MissingDetailsClient(StubNavBulkClient):
        def batch_create_records(self, table_name: str, records: List[Dict[str, Any]]):
            assert table_name == 'nav_history'
            self.batch_create_records_calls.append([
                {'fields': dict((record.get('fields') or {}))} for record in records
            ])
            raise Exception('FieldNameNotFound: details')

    client = MissingDetailsClient()
    local_cache = StubLocalNavIndexCache()
    storage = FeishuStorage(client=client, local_nav_index_cache=local_cache)
    nav = NAVHistory(
        date=date(2026, 3, 10),
        account='lx',
        total_value=1000.0,
        shares=1000.0,
        nav=1.0,
        details=_finality_details('2026-03-10'),
    )

    with pytest.raises(RuntimeError, match=r'refusing batch create retry without required details\.finality'):
        storage.write_nav_records([nav], mode='replace')

    assert len(client.batch_create_records_calls) == 1
    assert 'details' in client.batch_create_records_calls[0][0]['fields']
    assert nav.record_id is None
    assert local_cache.upsert_calls == 0


def test_bulk_nav_update_fails_closed_when_feishu_rejects_required_finality_details():
    class MissingDetailsClient(StubNavBulkClient):
        def batch_update_records(self, table_name: str, records: List[Dict[str, Any]]):
            assert table_name == 'nav_history'
            self.batch_update_records_calls.append([
                {'record_id': record['record_id'], 'fields': dict(record['fields'])}
                for record in records
            ])
            raise Exception('FieldNameNotFound: details')

    initial_fields = {
        'date': '2026-03-10',
        'account': 'lx',
        'total_value': 900.0,
        'shares': 1000.0,
        'nav': 0.9,
    }
    client = MissingDetailsClient(initial_records=[{'record_id': 'rec_nav_1', 'fields': initial_fields}])
    local_cache = StubLocalNavIndexCache()
    storage = FeishuStorage(client=client, local_nav_index_cache=local_cache)
    nav = NAVHistory(
        date=date(2026, 3, 10),
        account='lx',
        total_value=1000.0,
        shares=1000.0,
        nav=1.0,
        details=_finality_details('2026-03-10'),
    )

    with pytest.raises(RuntimeError, match=r'refusing batch update retry without required details\.finality'):
        storage.write_nav_records([nav], mode='replace')

    assert len(client.batch_update_records_calls) == 1
    assert 'details' in client.batch_update_records_calls[0][0]['fields']
    assert client._records[0]['fields'] == initial_fields
    assert local_cache.upsert_calls == 0


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


def test_write_nav_record_refreshes_remote_once_before_create_to_avoid_same_day_duplicates():
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

    storage.write_nav_record(nav, overwrite_existing=True)

    assert len(client.list_records_calls) == 1
    assert len(client.update_record_calls) == 1
    assert len(client.create_record_calls) == 0
    assert nav.record_id == 'rec_nav_1'


def test_write_nav_record_respects_overwrite_existing_false_before_write():
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
        storage.write_nav_record(nav)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "已存在同日记录" in str(exc)

    assert len(client.update_record_calls) == 0
    assert len(client.create_record_calls) == 0


def test_audit_nav_history_duplicates_groups_by_account_date():
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
            },
            {
                'record_id': 'rec_nav_2',
                'fields': {
                    'date': '2026-03-01',
                    'account': 'lx',
                    'total_value': 1100,
                    'shares': 1000,
                    'nav': 1.1,
                },
            },
            {
                'record_id': 'rec_nav_3',
                'fields': {
                    'date': '2026-03-02',
                    'account': 'lx',
                    'total_value': 1200,
                    'shares': 1000,
                    'nav': 1.2,
                },
            },
        ]
    )
    storage = FeishuStorage(client=client, local_nav_index_cache=StubLocalNavIndexCache())

    result = storage.audit_nav_history_duplicates(account='lx')

    assert result['success'] is True
    assert result['record_count'] == 3
    assert result['duplicate_group_count'] == 1
    assert result['duplicate_record_count'] == 2
    assert result['duplicates'][0]['account'] == 'lx'
    assert result['duplicates'][0]['date'] == '2026-03-01'
    assert result['duplicates'][0]['record_ids'] == ['rec_nav_1', 'rec_nav_2']


def test_write_nav_record_refuses_existing_duplicate_date_records():
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
            },
            {
                'record_id': 'rec_nav_2',
                'fields': {
                    'date': '2026-03-01',
                    'account': 'lx',
                    'total_value': 1100,
                    'shares': 1000,
                    'nav': 1.1,
                },
            },
        ]
    )
    storage = FeishuStorage(client=client, local_nav_index_cache=StubLocalNavIndexCache())
    nav = NAVHistory(
        date=date(2026, 3, 2),
        account='lx',
        total_value=1200.0,
        shares=1000.0,
        nav=1.2,
    )

    try:
        storage.write_nav_record(nav, dry_run=False)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "duplicate account/date" in str(exc)
        assert "rec_nav_1" in str(exc)
        assert "rec_nav_2" in str(exc)

    assert len(client.update_record_calls) == 0
    assert len(client.create_record_calls) == 0


def test_write_nav_record_matches_single_full_write_behavior():
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



def test_nav_repository_public_mutations_share_one_lock_key(monkeypatch):
    import src.feishu.repositories.nav_history_repository as repository_module

    lock_keys = []
    active_depth = 0
    max_depth = 0

    @contextmanager
    def fake_process_lock(key):
        nonlocal active_depth, max_depth
        lock_keys.append(key)
        active_depth += 1
        max_depth = max(max_depth, active_depth)
        try:
            yield
        finally:
            active_depth -= 1

    monkeypatch.setattr(repository_module, 'process_lock', fake_process_lock)
    client = StubNavSingleWriteClient()
    storage = FeishuStorage(client=client, local_nav_index_cache=StubLocalNavIndexCache())
    nav = NAVHistory(date=date(2026, 3, 4), account='lx', total_value=1000.0, shares=1000.0, nav=1.0)

    storage.write_nav_record(nav, dry_run=True)
    storage.write_nav_records([nav], dry_run=True)
    storage.patch_nav_derived_fields('rec_nav_1', {'pnl': 1.0}, dry_run=True)
    storage.patch_nav_details('rec_nav_1', {'snapshot_status': 'failed'}, dry_run=True)
    storage.delete_nav_by_record_id('rec_nav_1')

    assert lock_keys == ['nav-history-write'] * 5
    assert max_depth == 1


def test_single_nav_existence_check_and_create_are_inside_one_lock(monkeypatch):
    import src.feishu.repositories.nav_history_repository as repository_module

    active = False

    @contextmanager
    def fake_process_lock(key):
        nonlocal active
        assert key == 'nav-history-write'
        assert active is False
        active = True
        try:
            yield
        finally:
            active = False

    class LockCheckingClient(StubNavSingleWriteClient):
        def list_records(self, *args, **kwargs):
            assert active is True
            return super().list_records(*args, **kwargs)

        def create_record(self, *args, **kwargs):
            assert active is True
            return super().create_record(*args, **kwargs)

    monkeypatch.setattr(repository_module, 'process_lock', fake_process_lock)
    client = LockCheckingClient()
    storage = FeishuStorage(client=client, local_nav_index_cache=StubLocalNavIndexCache())
    nav = NAVHistory(date=date(2026, 3, 5), account='lx', total_value=1000.0, shares=1000.0, nav=1.0)

    storage.write_nav_record(nav)

    assert active is False
    assert len(client.list_records_calls) == 1
    assert len(client.create_record_calls) == 1


def test_concurrent_same_date_single_writes_create_only_once(monkeypatch):
    import src.feishu.repositories.nav_history_repository as repository_module

    mutex = Lock()

    @contextmanager
    def fake_process_lock(key):
        assert key == 'nav-history-write'
        with mutex:
            yield

    monkeypatch.setattr(repository_module, 'process_lock', fake_process_lock)
    client = StubNavSingleWriteClient()
    storage = FeishuStorage(client=client, local_nav_index_cache=StubLocalNavIndexCache())
    start = Event()
    outcomes = []

    def write_once(value):
        nav = NAVHistory(
            date=date(2026, 3, 6),
            account='lx',
            total_value=value,
            shares=1000.0,
            nav=value / 1000.0,
        )
        start.wait()
        try:
            storage.write_nav_record(nav)
            outcomes.append('created')
        except ValueError as exc:
            assert '已存在同日记录' in str(exc)
            outcomes.append('blocked')

    threads = [Thread(target=write_once, args=(1000.0,)), Thread(target=write_once, args=(1100.0,))]
    for thread in threads:
        thread.start()
    start.set()
    for thread in threads:
        thread.join(timeout=2)
        assert thread.is_alive() is False

    assert sorted(outcomes) == ['blocked', 'created']
    assert len(client.create_record_calls) == 1
    assert len(client.update_record_calls) == 0



def test_account_duplicate_audit_refreshes_finality_from_remote_into_local_index():
    finality = {
        'version': 1,
        'status': 'final',
        'nav_date': '2026-03-07',
        'valuation_as_of': '2026-03-07T08:00:00+08:00',
        'writer': 'daily-nav-job',
        'write_reason': 'canonical_daily_nav_job',
        'run_id': 'run-final-1',
    }
    client = StubNavSingleWriteClient(initial_records=[{
        'record_id': 'rec_nav_final',
        'fields': {
            'date': '2026-03-07',
            'account': 'lx',
            'total_value': 1000.0,
            'shares': 1000.0,
            'nav': 1.0,
            'details': {'finality': finality},
        },
    }])
    local_cache = StubLocalNavIndexCache()
    local_cache.set_account('lx', {
        'account': 'lx',
        'nav_history': [{
            'date': '2026-03-07',
            'record_id': 'rec_nav_final',
            'total_value': 1000.0,
            'shares': 1000.0,
            'nav': 1.0,
        }],
        'record_count': 1,
        'month_end_base': {'2026-03': {'date': '2026-03-07'}},
        'year_end_base': {'2026': {'date': '2026-03-07'}},
        'inception_base': {'date': '2026-03-07'},
        'last_record': {'date': '2026-03-07'},
    })
    storage = FeishuStorage(client=client, local_nav_index_cache=local_cache)

    audit = storage.audit_nav_history_duplicates(account='lx')
    existing = storage.get_nav_on_date('lx', date(2026, 3, 7))
    decision = evaluate_nav_finality(existing.details, target_date=date(2026, 3, 7))

    assert audit['duplicate_group_count'] == 0
    assert existing.details == {'finality': finality}
    assert decision.eligible is True
    assert local_cache.get_account('lx')['nav_history'][0]['details'] == {'finality': finality}


def test_incremental_nav_cache_roundtrip_preserves_details():
    client = StubNavSingleWriteClient()
    local_cache = StubLocalNavIndexCache()
    storage = FeishuStorage(client=client, local_nav_index_cache=local_cache)
    details = {
        'finality': {
            'version': 1,
            'status': 'manual',
            'nav_date': '2026-03-08',
            'valuation_as_of': None,
            'writer': 'nav-record',
            'write_reason': 'direct_nav_record',
        }
    }
    nav = NAVHistory(
        date=date(2026, 3, 8),
        account='lx',
        total_value=1000.0,
        shares=1000.0,
        nav=1.0,
        details=details,
    )

    storage.write_nav_record(nav)
    restarted_storage = FeishuStorage(client=client, local_nav_index_cache=local_cache)
    existing = restarted_storage.get_nav_on_date('lx', date(2026, 3, 8))

    assert existing.details == details


def test_public_nav_reads_preserve_complete_canonical_rows_across_cache_layers():
    details = {
        'finality': {
            'version': 1,
            'status': 'final',
            'nav_date': '2026-03-11',
            'valuation_as_of': '2026-03-11T08:00:00+08:00',
            'writer': 'daily-nav-job',
            'write_reason': 'canonical_daily_nav_job',
            'run_id': 'run-complete-row',
        }
    }
    fields = {
        'date': '2026-03-11',
        'account': 'lx',
        'total_value': 1000.0,
        'cash_value': 200.0,
        # Persisted compatibility semantics: fund is a subset of stock_value.
        'stock_value': 800.0,
        'fund_value': 100.0,
        'cn_stock_value': 450.0,
        'us_stock_value': 250.0,
        'hk_stock_value': 100.0,
        'stock_weight': 0.8,
        'cash_weight': 0.2,
        'shares': 1000.0,
        'nav': 1.0,
        'cash_flow': 25.0,
        'share_change': 25.0,
        'mtd_nav_change': 0.03,
        'ytd_nav_change': 0.05,
        'pnl': 12.0,
        'mtd_pnl': 30.0,
        'ytd_pnl': 50.0,
        'details': details,
        'updated_at': '2026-03-11 08:01:00',
    }
    client = StubNavSingleWriteClient(initial_records=[{
        'record_id': 'rec_nav_complete',
        'fields': fields,
    }])
    local_cache = StubLocalNavIndexCache()
    storage = FeishuStorage(client=client, local_nav_index_cache=local_cache)

    fresh = storage.get_nav_history('lx', days=9999)[0]
    memory = storage.get_latest_nav('lx')
    first_read_count = len(client.list_records_calls)

    restarted = FeishuStorage(client=client, local_nav_index_cache=local_cache)
    disk = restarted.get_nav_on_date('lx', date(2026, 3, 11))
    public_history = restarted.get_nav_history('lx', days=9999)

    canonical_attributes = (
        'total_value', 'cash_value', 'stock_value', 'fund_value',
        'cn_stock_value', 'us_stock_value', 'hk_stock_value',
        'stock_weight', 'cash_weight', 'shares', 'nav', 'cash_flow',
        'share_change', 'mtd_nav_change', 'ytd_nav_change', 'pnl',
        'mtd_pnl', 'ytd_pnl', 'details',
    )
    for candidate in (fresh, memory, disk, public_history[0]):
        assert candidate.record_id == 'rec_nav_complete'
        assert candidate.date == date(2026, 3, 11)
        assert candidate.account == 'lx'
        for attribute in canonical_attributes:
            assert getattr(candidate, attribute) == getattr(fresh, attribute)

    # A valid versioned disk row serves public reads without a second remote read.
    assert len(client.list_records_calls) == first_read_count == 1
    requested_fields = set(client.list_records_calls[0]['field_names'])
    assert requested_fields == set(storage.nav_history.NAV_CANONICAL_PROJECTION_FIELDS)

    cached = local_cache.get_account('lx')
    assert cached['cache_format_version'] == storage.nav_history.NAV_CACHE_FORMAT_VERSION
    assert cached['nav_history'][0]['cash_value'] == 200.0
    assert cached['nav_history'][0]['share_change'] == 25.0
    assert set(cached['date_identity_index']['2026-03-11'][0]) == {
        'date', 'account', 'record_id', 'updated_at',
    }


@pytest.mark.parametrize('bad_date', [None, 'not-a-date'])
def test_remote_nav_with_missing_or_invalid_date_fails_closed_without_cache_publication(
    bad_date,
):
    fields = {
        'account': 'lx',
        'total_value': 1000.0,
        'shares': 1000.0,
        'nav': 1.0,
    }
    if bad_date is not None:
        fields['date'] = bad_date
    client = StubNavBulkClient(initial_records=[{
        'record_id': 'rec_nav_invalid',
        'fields': fields,
    }])
    local_cache = StubLocalNavIndexCache()
    storage = FeishuStorage(client=client, local_nav_index_cache=local_cache)

    with pytest.raises(
        NavHistoryReadIntegrityError,
        match=(
            r'nav_history canonical read integrity failed: source=feishu, '
            r'account=lx, record_id=rec_nav_invalid, row_index=0'
        ),
    ) as exc_info:
        storage.preload_nav_index('lx', force_refresh=True)

    assert exc_info.value.source == 'feishu'
    assert exc_info.value.record_id == 'rec_nav_invalid'
    assert local_cache.get_account('lx') == {}
    assert 'lx' not in storage._nav_index_loaded_accounts
    assert 'lx' not in storage._nav_index_mem_cache


@pytest.mark.parametrize('audit_account', ['lx', None])
def test_nav_duplicate_audit_rejects_invalid_remote_row_instead_of_reporting_success(
    audit_account,
):
    client = StubNavBulkClient(initial_records=[{
        'record_id': 'rec_nav_invalid',
        'fields': {
            'date': '2026-99-99',
            'account': 'lx',
            'total_value': 1000.0,
            'shares': 1000.0,
            'nav': 1.0,
        },
    }])
    local_cache = StubLocalNavIndexCache()
    storage = FeishuStorage(client=client, local_nav_index_cache=local_cache)

    with pytest.raises(NavHistoryReadIntegrityError):
        storage.audit_nav_history_duplicates(account=audit_account)

    assert local_cache.get_account('lx') == {}


@pytest.mark.parametrize(
    ('record_id', 'source_account', 'reason'),
    [
        ('', 'lx', 'record_id is required'),
        ('rec_nav_missing_account', None, 'account is required'),
        ('rec_nav_other_account', 'other', 'account scope mismatch'),
    ],
)
def test_scoped_remote_nav_rejects_missing_or_cross_account_identity(
    record_id,
    source_account,
    reason,
):
    class UnfilteredClient(StubNavBulkClient):
        def list_records(
            self,
            table_name: str,
            filter_str: str = None,
            field_names: List[str] = None,
            page_size: int = 500,
        ):
            assert table_name == 'nav_history'
            self.list_records_calls.append({
                'table_name': table_name,
                'filter_str': filter_str,
                'field_names': list(field_names or []),
                'page_size': page_size,
            })
            return [
                {
                    'record_id': row.get('record_id'),
                    'fields': dict(row.get('fields') or {}),
                }
                for row in self._records
            ]

    fields = {
        'date': '2026-03-14',
        'total_value': 1000.0,
        'shares': 1000.0,
        'nav': 1.0,
    }
    if source_account is not None:
        fields['account'] = source_account
    client = UnfilteredClient(initial_records=[{
        'record_id': record_id,
        'fields': fields,
    }])
    local_cache = StubLocalNavIndexCache()
    storage = FeishuStorage(client=client, local_nav_index_cache=local_cache)

    with pytest.raises(NavHistoryReadIntegrityError, match=reason):
        storage.preload_nav_index('lx', force_refresh=True)

    assert local_cache.get_account('lx') == {}
    assert 'lx' not in storage._nav_index_loaded_accounts


@pytest.mark.parametrize(
    ('record_id', 'source_account', 'reason'),
    [
        ('', 'lx', 'record_id is required'),
        ('rec_nav_missing_account', None, 'account is required'),
        ('rec_nav_other_account', 'other', 'account scope mismatch'),
    ],
)
def test_versioned_nav_cache_rejects_missing_or_cross_account_identity(
    record_id,
    source_account,
    reason,
):
    row = {
        'record_id': record_id,
        'date': '2026-03-14',
        'total_value': 1000.0,
        'shares': 1000.0,
        'nav': 1.0,
    }
    if source_account is not None:
        row['account'] = source_account
    client = StubNavBulkClient()
    local_cache = StubLocalNavIndexCache()
    local_cache.set_account('lx', {
        'account': 'lx',
        'cache_format_version': 2,
        'nav_history': [row],
    })
    storage = FeishuStorage(client=client, local_nav_index_cache=local_cache)

    with pytest.raises(NavHistoryReadIntegrityError, match=reason):
        storage.get_nav_on_date('lx', date(2026, 3, 14))

    assert client.list_records_calls == []
    assert local_cache.get_account('lx') == {}


@pytest.mark.parametrize(
    ('record_id', 'source_account'),
    [('', 'lx'), ('rec_nav_missing_account', None)],
)
def test_global_duplicate_audit_rejects_missing_canonical_identity(
    record_id,
    source_account,
):
    fields = {
        'date': '2026-03-14',
        'total_value': 1000.0,
        'shares': 1000.0,
        'nav': 1.0,
    }
    if source_account is not None:
        fields['account'] = source_account
    client = StubNavBulkClient(initial_records=[{
        'record_id': record_id,
        'fields': fields,
    }])
    storage = FeishuStorage(
        client=client,
        local_nav_index_cache=StubLocalNavIndexCache(),
    )

    with pytest.raises(NavHistoryReadIntegrityError):
        storage.audit_nav_history_duplicates(account=None)


def test_nav_write_rejects_invalid_remote_row_before_any_mutation_request():
    client = StubNavBulkClient(initial_records=[{
        'record_id': 'rec_nav_invalid',
        'fields': {
            'account': 'lx',
            'total_value': 1000.0,
            'shares': 1000.0,
            'nav': 1.0,
        },
    }])
    local_cache = StubLocalNavIndexCache()
    storage = FeishuStorage(client=client, local_nav_index_cache=local_cache)

    with pytest.raises(NavHistoryReadIntegrityError):
        storage.write_nav_records([
            NAVHistory(
                date=date(2026, 3, 14),
                account='lx',
                total_value=1000.0,
                shares=1000.0,
                nav=1.0,
            )
        ])

    assert client.batch_update_records_calls == []
    assert client.batch_create_records_calls == []
    assert local_cache.get_account('lx') == {}


def test_nav_write_rejects_missing_remote_record_id_before_any_mutation_request():
    client = StubNavBulkClient(initial_records=[{
        'record_id': '',
        'fields': {
            'date': '2026-03-14',
            'account': 'lx',
            'total_value': 1000.0,
            'shares': 1000.0,
            'nav': 1.0,
        },
    }])
    local_cache = StubLocalNavIndexCache()
    storage = FeishuStorage(client=client, local_nav_index_cache=local_cache)

    with pytest.raises(NavHistoryReadIntegrityError, match='record_id is required'):
        storage.write_nav_records([
            NAVHistory(
                date=date(2026, 3, 15),
                account='lx',
                total_value=1010.0,
                shares=1000.0,
                nav=1.01,
            )
        ])

    assert client.batch_update_records_calls == []
    assert client.batch_create_records_calls == []
    assert local_cache.get_account('lx') == {}


def test_corrupt_versioned_nav_cache_row_fails_closed_and_is_discarded():
    client = StubNavBulkClient()
    local_cache = StubLocalNavIndexCache()
    local_cache.set_account('lx', {
        'account': 'lx',
        'cache_format_version': 2,
        'nav_history': [{
            'record_id': 'rec_nav_corrupt_cache',
            'date': 'broken-date',
            'account': 'lx',
            'total_value': 1000.0,
            'shares': 1000.0,
            'nav': 1.0,
        }],
    })
    storage = FeishuStorage(client=client, local_nav_index_cache=local_cache)

    with pytest.raises(
        NavHistoryReadIntegrityError,
        match=r'source=versioned_cache, account=lx, record_id=rec_nav_corrupt_cache',
    ):
        storage.get_nav_on_date('lx', date(2026, 3, 14))

    assert client.list_records_calls == []
    assert local_cache.get_account('lx') == {}
    assert 'lx' not in storage._nav_index_loaded_accounts


def test_update_success_create_failure_preserves_scopes_and_rebuilds_cache():
    class UpdateThenCreateFailureClient(StubNavBulkClient):
        def batch_create_records(self, table_name: str, records: List[Dict[str, Any]]):
            assert table_name == 'nav_history'
            self.batch_create_records_calls.append([
                {'fields': dict((record.get('fields') or {}))} for record in records
            ])
            raise FeishuBatchWriteError(
                operation='create',
                table_name=table_name,
                chunk_offset=0,
                reason='transport outcome unknown',
                confirmed_results=[],
            )

    client = UpdateThenCreateFailureClient(initial_records=[{
        'record_id': 'rec_nav_existing',
        'fields': {
            'date': '2026-03-12',
            'account': 'lx',
            'total_value': 1000.0,
            'cash_value': 200.0,
            'stock_value': 800.0,
            'fund_value': 100.0,
            'shares': 1000.0,
            'nav': 1.0,
        },
    }])
    local_cache = StubLocalNavIndexCache()
    storage = FeishuStorage(client=client, local_nav_index_cache=local_cache)
    existing = NAVHistory(
        date=date(2026, 3, 12),
        account='lx',
        total_value=1100.0,
        cash_value=200.0,
        stock_value=900.0,
        fund_value=100.0,
        shares=1000.0,
        nav=1.1,
    )
    new = NAVHistory(
        date=date(2026, 3, 13),
        account='lx',
        total_value=1110.0,
        cash_value=200.0,
        stock_value=910.0,
        fund_value=100.0,
        shares=1000.0,
        nav=1.11,
    )

    with pytest.raises(FeishuBatchWriteError) as exc_info:
        storage.write_nav_records(
            [existing, new],
            mode='replace',
            allow_partial=True,
        )

    error = exc_info.value
    assert error.confirmed_scopes == {
        'update': [{
            'operation': 'update',
            'date': '2026-03-12',
            'record_id': 'rec_nav_existing',
        }],
        'create': [],
    }
    assert error.unknown_scopes == {
        'update': [],
        'create': [{
            'operation': 'create',
            'date': '2026-03-13',
            'record_id': None,
        }],
    }
    assert error.failed_scopes == {'update': [], 'create': []}
    assert error.failure_stage == {
        'operation': 'create',
        'chunk_offset': 0,
        'reason': (
            'Feishu batch create failed: table=nav_history, chunk_offset=0, '
            'confirmed=0: transport outcome unknown'
        ),
    }
    assert error.partial_write_possible is True
    assert error.cache_rebuild == {
        'status': 'rebuilt_from_fresh_read',
        'loaded': 1,
    }
    assert [row['record_id'] for row in error.confirmed_results] == ['rec_nav_existing']

    # The repository publishes only the fresh remote readback, never the
    # optimistic create row that failed to complete.
    assert local_cache.upsert_calls == 0
    cached_rows = local_cache.get_account('lx')['nav_history']
    assert [(row['date'], row['total_value']) for row in cached_rows] == [
        ('2026-03-12', 1100.0),
    ]
    assert len(client.list_records_calls) == 2


def test_update_success_create_schema_rejection_marks_target_failed_not_unknown():
    class UpdateThenCreateSchemaRejectionClient(StubNavBulkClient):
        def batch_create_records(self, table_name: str, records: List[Dict[str, Any]]):
            assert table_name == 'nav_history'
            self.batch_create_records_calls.append([
                {'fields': dict((record.get('fields') or {}))} for record in records
            ])
            raise Exception('FieldNameNotFound: details')

    client = UpdateThenCreateSchemaRejectionClient(initial_records=[{
        'record_id': 'rec_nav_existing',
        'fields': {
            'date': '2026-03-15',
            'account': 'lx',
            'total_value': 1000.0,
            'shares': 1000.0,
            'nav': 1.0,
        },
    }])
    local_cache = StubLocalNavIndexCache()
    storage = FeishuStorage(client=client, local_nav_index_cache=local_cache)
    existing = NAVHistory(
        date=date(2026, 3, 15),
        account='lx',
        total_value=1010.0,
        shares=1000.0,
        nav=1.01,
    )
    rejected_create = NAVHistory(
        date=date(2026, 3, 16),
        account='lx',
        total_value=1020.0,
        shares=1000.0,
        nav=1.02,
        details=_finality_details('2026-03-16'),
    )

    with pytest.raises(FeishuBatchWriteError) as exc_info:
        storage.write_nav_records(
            [existing, rejected_create],
            mode='replace',
            allow_partial=True,
        )

    error = exc_info.value
    assert error.confirmed_scopes == {
        'update': [{
            'operation': 'update',
            'date': '2026-03-15',
            'record_id': 'rec_nav_existing',
        }],
        'create': [],
    }
    assert error.unknown_scopes == {'update': [], 'create': []}
    assert error.failed_scopes == {
        'update': [],
        'create': [{
            'operation': 'create',
            'date': '2026-03-16',
            'record_id': None,
        }],
    }
    assert error.failure_stage['operation'] == 'create'
    assert 'FieldNameNotFound' in error.failure_stage['reason']
    assert error.partial_write_possible is True
    assert error.cache_rebuild == {
        'status': 'rebuilt_from_fresh_read',
        'loaded': 1,
    }
    assert len(client.batch_create_records_calls) == 1
    cached_rows = local_cache.get_account('lx')['nav_history']
    assert [(row['date'], row['total_value']) for row in cached_rows] == [
        ('2026-03-15', 1010.0),
    ]
