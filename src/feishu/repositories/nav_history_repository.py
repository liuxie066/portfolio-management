"""Repository for the Feishu nav_history table."""
import logging
import json
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from ...feishu_client import FeishuBatchWriteError
from ...models import NAVHistory
from ...process_lock import nav_history_lock_key, process_lock
from ..contracts import get_table_contract


class NavHistoryReadIntegrityError(ValueError):
    """A canonical NAV source row could not be reconstructed losslessly."""

    def __init__(
        self,
        *,
        source: str,
        account: str,
        record_id: Optional[str],
        row_index: int,
        cause: Exception,
    ):
        self.source = source
        self.account = account
        self.record_id = record_id
        self.row_index = row_index
        self.reason = str(cause) or cause.__class__.__name__
        super().__init__(
            'nav_history canonical read integrity failed: '
            f'source={source}, account={account}, record_id={record_id or "<missing>"}, '
            f'row_index={row_index}: {self.reason}'
        )


class NavHistoryRepository:
    """NAV history table operations + nav index cache."""

    def __init__(self, storage):
        self.storage = storage

    def __getattr__(self, name: str):
        return getattr(self.storage, name)

    NAV_CACHE_FORMAT_VERSION = 2
    NAV_CANONICAL_PROJECTION_FIELDS: List[str] = [
        field.name for field in get_table_contract('nav_history').fields
    ]
    # Compatibility alias for callers that historically inspected this name.
    # The projection is now the complete canonical row, not a lossy index row.
    NAV_INDEX_PROJECTION_FIELDS = NAV_CANONICAL_PROJECTION_FIELDS

    NAV_DERIVED_PATCH_FIELDS = {
        'stock_weight',
        'cash_weight',
        'shares',
        'nav',
        'cash_flow',
        'share_change',
        'pnl',
        'mtd_nav_change',
        'ytd_nav_change',
        'mtd_pnl',
        'ytd_pnl',
    }
    NAV_MAINTENANCE_PATCH_FIELDS = NAV_DERIVED_PATCH_FIELDS | {'details'}

    def _nav_to_cache_row(
        self,
        nav: NAVHistory,
        *,
        updated_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Serialize one complete canonical NAV row for memory/disk replay."""
        row = self._nav_to_dict(nav)
        row['date'] = self._safe_date_str(nav.date)
        row['record_id'] = nav.record_id
        row['updated_at'] = updated_at
        return row

    @staticmethod
    def _nav_identity_row(row: Dict[str, Any]) -> Dict[str, Any]:
        """Project the lightweight date/identity facts used by internal indexes."""
        return {
            'date': row.get('date'),
            'account': row.get('account'),
            'record_id': row.get('record_id'),
            'updated_at': row.get('updated_at'),
        }

    @staticmethod
    def _validate_nav_source_identity(
        row: Dict[str, Any],
        *,
        scoped_account: str,
    ) -> None:
        """Require observed record/account identity without manufacturing it."""
        record_id = row.get('record_id')
        if not isinstance(record_id, str) or not record_id.strip():
            raise ValueError('record_id is required')

        source_account = row.get('account')
        if not isinstance(source_account, str) or not source_account.strip():
            raise ValueError('account is required')
        if scoped_account and source_account != scoped_account:
            raise ValueError(
                'account scope mismatch: '
                f'expected={scoped_account}, actual={source_account}'
            )

    def _build_nav_payload_from_cache_rows(
        self,
        account: str,
        rows: List[Dict[str, Any]],
        *,
        source: str = 'versioned_cache',
    ) -> Dict[str, Any]:
        navs: List[NAVHistory] = []
        nav_records: List[Dict[str, Any]] = []

        for row_index, raw_row in enumerate(rows):
            record_id = raw_row.get('record_id') if isinstance(raw_row, dict) else None
            try:
                if not isinstance(raw_row, dict):
                    raise TypeError('canonical row must be an object')
                row = dict(raw_row)
                self._validate_nav_source_identity(
                    row,
                    scoped_account=account,
                )
                nav = self._dict_to_nav(row)
            except (AttributeError, TypeError, ValueError) as exc:
                raise NavHistoryReadIntegrityError(
                    source=source,
                    account=account,
                    record_id=record_id,
                    row_index=row_index,
                    cause=exc,
                ) from exc
            if not nav.date:
                raise NavHistoryReadIntegrityError(
                    source=source,
                    account=account,
                    record_id=record_id,
                    row_index=row_index,
                    cause=ValueError('date is required'),
                )
            navs.append(nav)
            nav_records.append(
                self._nav_to_cache_row(
                    nav,
                    updated_at=self._extract_updated_at_str(row),
                )
            )

        nav_records.sort(key=lambda item: item.get('date') or '')
        navs.sort(key=lambda item: item.date)

        month_end_base: Dict[str, Dict[str, Any]] = {}
        year_end_base: Dict[str, Dict[str, Any]] = {}
        date_identity_index: Dict[str, List[Dict[str, Any]]] = {}
        identity_rows: List[Dict[str, Any]] = []
        for row in nav_records:
            ds = row.get('date')
            if not ds:
                continue
            identity = self._nav_identity_row(row)
            identity_rows.append(identity)
            date_identity_index.setdefault(ds, []).append(dict(identity))
            d = datetime.strptime(ds, '%Y-%m-%d').date()
            month_end_base[d.strftime('%Y-%m')] = dict(identity)
            year_end_base[str(d.year)] = dict(identity)

        inception_base = dict(identity_rows[0]) if identity_rows else None
        last_record = dict(identity_rows[-1]) if identity_rows else None

        return {
            'account': account,
            'cache_format_version': self.NAV_CACHE_FORMAT_VERSION,
            'record_count': len(nav_records),
            # Public reads replay these complete rows.
            'nav_history': nav_records,
            # Internal lookup structures intentionally carry identity only.
            'date_identity_index': date_identity_index,
            'month_end_base': month_end_base,
            'year_end_base': year_end_base,
            'inception_base': inception_base,
            'last_record': last_record,
            'latest_updated_at': (last_record or {}).get('updated_at') if last_record else None,
            '_nav_objects': navs,
        }

    def _build_nav_index_payload(
        self,
        account: str,
        records: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        canonical_rows: List[Dict[str, Any]] = []
        for row_index, record in enumerate(records):
            record_id = record.get('record_id') if isinstance(record, dict) else None
            try:
                if not isinstance(record, dict):
                    raise TypeError('remote record must be an object')
                raw_fields = record.get('fields') or {}
                if not isinstance(raw_fields, dict):
                    raise TypeError('remote record fields must be an object')
                fields = self._from_feishu_fields(raw_fields, 'nav_history')
                fields['record_id'] = record_id
                self._validate_nav_source_identity(
                    fields,
                    scoped_account=account,
                )
                nav = self._dict_to_nav(fields)
            except (AttributeError, TypeError, ValueError) as exc:
                raise NavHistoryReadIntegrityError(
                    source='feishu',
                    account=account,
                    record_id=record_id,
                    row_index=row_index,
                    cause=exc,
                ) from exc
            if not nav.date:
                raise NavHistoryReadIntegrityError(
                    source='feishu',
                    account=account,
                    record_id=record_id,
                    row_index=row_index,
                    cause=ValueError('date is required'),
                )
            canonical_rows.append(
                self._nav_to_cache_row(
                    nav,
                    updated_at=self._extract_updated_at_str(raw_fields),
                )
            )
        return self._build_nav_payload_from_cache_rows(
            account,
            canonical_rows,
            source='feishu_canonical_row',
        )

    @staticmethod
    def _nav_index_fingerprint(payload: Dict[str, any]) -> Dict[str, tuple]:
        fp: Dict[str, tuple] = {}
        for row in payload.get('nav_history') or []:
            ds = row.get('date')
            if not ds:
                continue
            fp[ds] = (row.get('record_id'), row.get('updated_at'))
        return fp

    def _nav_duplicate_groups_from_rows(
        self,
        rows: List[Dict[str, Any]],
        *,
        default_account: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        grouped: Dict[tuple, List[Dict[str, Any]]] = {}
        for row in rows or []:
            raw_fields = row.get('fields') if 'fields' in row else row
            fields = self._from_feishu_fields(raw_fields or {}, 'nav_history')
            nav_date = fields.get('date')
            if isinstance(nav_date, (int, float)):
                nav_date = datetime.fromtimestamp(nav_date / 1000, tz=self.FEISHU_DATE_TZ).date()
            elif isinstance(nav_date, datetime):
                nav_date = nav_date.date()
            elif isinstance(nav_date, str):
                try:
                    nav_date = datetime.strptime(nav_date[:10], '%Y-%m-%d').date()
                except ValueError:
                    nav_date = None
            account = fields.get('account') or default_account
            if not account or not nav_date:
                continue
            key = (str(account), self._safe_date_str(nav_date))
            grouped.setdefault(key, []).append({
                'record_id': row.get('record_id') or fields.get('record_id'),
                'total_value': fields.get('total_value'),
                'nav': fields.get('nav'),
                'shares': fields.get('shares'),
                'updated_at': self._extract_updated_at_str(raw_fields or {}),
            })

        duplicates = []
        for (account, date_str), items in sorted(grouped.items(), key=lambda x: (x[0][0], x[0][1])):
            if len(items) <= 1:
                continue
            duplicates.append({
                'account': account,
                'date': date_str,
                'count': len(items),
                'record_ids': [item.get('record_id') for item in items if item.get('record_id')],
                'records': items,
            })
        return duplicates

    def _duplicate_error_message(self, duplicates: List[Dict[str, Any]]) -> str:
        sample = duplicates[:5]
        parts = [
            f"account={item.get('account')}, date={item.get('date')}, record_ids={item.get('record_ids')}"
            for item in sample
        ]
        suffix = "" if len(duplicates) <= len(sample) else f"; ... +{len(duplicates) - len(sample)} more"
        return "nav_history duplicate account/date records exist; repair before NAV write: " + "; ".join(parts) + suffix

    def _store_nav_index_payload(self, account: str, payload: Dict[str, Any]) -> None:
        """Publish one freshly built NAV index to memory and the local cache."""
        self._nav_index_mem_cache[account] = payload
        self._nav_index_loaded_accounts.add(account)
        persist_payload = dict(payload)
        persist_payload.pop('_nav_objects', None)
        self._local_nav_index_cache.set_account(account, persist_payload)

    def audit_nav_history_duplicates(self, account: Optional[str] = None) -> Dict[str, Any]:
        """Read-only audit for duplicate nav_history rows by business key."""
        filter_str = None
        if account:
            filter_str = f'CurrentValue.[account] = "{self._escape_filter_value(account)}"'

        try:
            records = self.client.list_records(
                'nav_history',
                filter_str=filter_str,
                field_names=self.NAV_INDEX_PROJECTION_FIELDS,
            )
        except Exception as e:
            if 'FieldNameNotFound' in str(e):
                fallback_fields = [f for f in self.NAV_INDEX_PROJECTION_FIELDS if f != 'updated_at']
                records = self.client.list_records(
                    'nav_history',
                    filter_str=filter_str,
                    field_names=fallback_fields,
                )
            else:
                raise

        try:
            payload = self._build_nav_index_payload(account or '', records)
        except NavHistoryReadIntegrityError:
            if account:
                self._clear_nav_index_authority(account)
            raise
        duplicates = self._nav_duplicate_groups_from_rows(records, default_account=account)
        if account:
            self._store_nav_index_payload(account, payload)
        return {
            'success': True,
            'account': account,
            'record_count': len(records or []),
            'duplicate_group_count': len(duplicates),
            'duplicate_record_count': sum(item.get('count', 0) for item in duplicates),
            'duplicates': duplicates,
        }

    def preload_nav_index(self, account: str, force_refresh: bool = False) -> Dict[str, any]:
        """预加载并缓存 nav_history 索引（含 month/year/inception bases）。"""
        if (not force_refresh) and (account in self._nav_index_loaded_accounts):
            cached = self._nav_index_mem_cache.get(account) or {}
            return {
                'account': account,
                'loaded': int(cached.get('record_count', 0) or 0),
                'source': 'memory',
                'invalidated': False,
            }

        cached_local = self._local_nav_index_cache.get_account(account)

        filter_str = f'CurrentValue.[account] = "{self._escape_filter_value(account)}"'
        try:
            records = self.client.list_records(
                'nav_history',
                filter_str=filter_str,
                field_names=self.NAV_INDEX_PROJECTION_FIELDS,
            )
        except Exception as e:
            if 'FieldNameNotFound' in str(e):
                fallback_fields = [f for f in self.NAV_INDEX_PROJECTION_FIELDS if f != 'updated_at']
                records = self.client.list_records(
                    'nav_history',
                    filter_str=filter_str,
                    field_names=fallback_fields,
                )
            else:
                raise

        try:
            payload = self._build_nav_index_payload(account, records)
        except NavHistoryReadIntegrityError:
            self._clear_nav_index_authority(account)
            raise
        invalidated = False

        if cached_local:
            missing_base = not cached_local.get('inception_base') or not cached_local.get('month_end_base') or not cached_local.get('year_end_base')
            if missing_base:
                invalidated = True
            else:
                old_fp = self._nav_index_fingerprint(cached_local)
                new_fp = self._nav_index_fingerprint(payload)
                if old_fp != new_fp:
                    invalidated = True

        self._store_nav_index_payload(account, payload)

        return {
            'account': account,
            'loaded': len(payload.get('nav_history') or []),
            'source': 'feishu',
            'invalidated': invalidated,
        }

    def _ensure_nav_index_loaded(self, account: str):
        if account in self._nav_index_loaded_accounts:
            return

        cached_local = self._local_nav_index_cache.get_account(account)
        if (
            cached_local
            and cached_local.get('cache_format_version') == self.NAV_CACHE_FORMAT_VERSION
        ):
            try:
                payload = self._build_nav_payload_from_cache_rows(
                    account,
                    list(cached_local.get('nav_history') or []),
                )
            except NavHistoryReadIntegrityError:
                self._clear_nav_index_authority(account)
                raise
            self._nav_index_mem_cache[account] = payload
            self._nav_index_loaded_accounts.add(account)
            return

        # Older cache payloads contain only a partial NAV projection and cannot
        # serve a public read safely. Replace them from the remote canonical row.
        self.preload_nav_index(account, force_refresh=True)

    def get_nav_index(self, account: str) -> Dict[str, any]:
        self._ensure_nav_index_loaded(account)
        return self._nav_index_mem_cache.get(account) or {}

    def _get_indexed_navs(self, account: str) -> List[NAVHistory]:
        idx = self.get_nav_index(account)
        navs: List[NAVHistory] = list(idx.get('_nav_objects') or [])
        if navs:
            return navs

        self.preload_nav_index(account, force_refresh=True)
        idx = self.get_nav_index(account)
        return list(idx.get('_nav_objects') or [])

    def _invalidate_nav_index(self, account: str):
        self._nav_index_loaded_accounts.discard(account)
        self._nav_index_mem_cache.pop(account, None)

    def _clear_nav_index_authority(self, account: str) -> None:
        """Discard memory/disk authority before rebuilding after a write fault."""
        self._invalidate_nav_index(account)
        self._local_nav_index_cache.set_account(account, {}, _flush=True)

    def _normalize_nav_date(self, nav_date) -> date:
        if isinstance(nav_date, datetime):
            return nav_date.date()
        if isinstance(nav_date, str):
            return datetime.strptime(nav_date[:10], '%Y-%m-%d').date()
        return nav_date

    def _nav_to_index_row(self, nav: NAVHistory, updated_at: Optional[str] = None) -> Dict[str, any]:
        """Compatibility name for a complete row used by incremental cache updates."""
        return self._nav_to_cache_row(nav, updated_at=updated_at)

    def _apply_nav_rows_to_local_cache(self, account: str, rows: List[Dict[str, any]]):
        """增量更新本地 NAV 索引缓存，并失效内存镜像。"""
        if not rows:
            return
        self._local_nav_index_cache.upsert_nav_records(account, rows, _flush=True)
        cached = self._local_nav_index_cache.get_account(account)
        rebuilt = self._build_nav_payload_from_cache_rows(
            account,
            list(cached.get('nav_history') or []),
        )
        persist_payload = dict(rebuilt)
        persist_payload.pop('_nav_objects', None)
        self._local_nav_index_cache.set_account(
            account,
            persist_payload,
            _flush=True,
        )
        self._invalidate_nav_index(account)

    def _validate_nav_write(self, nav: NAVHistory):
        """Validate a full nav record write before persisting."""
        nav.date = self._normalize_nav_date(nav.date)

        if not getattr(nav, 'account', None):
            raise ValueError('nav_history write validation failed: account is required')
        if not getattr(nav, 'date', None):
            raise ValueError('nav_history write validation failed: date is required')

        if nav.total_value is None:
            raise ValueError('nav_history write validation failed: total_value is required')
        try:
            tv = float(nav.total_value)
        except Exception:
            raise ValueError('nav_history write validation failed: total_value must be a number')
        if tv <= 0:
            raise ValueError('nav_history write validation failed: total_value must be > 0')

        details = getattr(nav, 'details', None)
        status = None
        if isinstance(details, dict):
            status = (details.get('status') or '').upper()

        if status == 'CLOSED':
            if nav.shares is None:
                raise ValueError('nav_history write validation failed: shares is required when status=CLOSED')
            try:
                if float(nav.shares) != 0.0:
                    raise ValueError('nav_history write validation failed: shares must be 0 when status=CLOSED')
            except ValueError:
                raise
            except Exception:
                raise ValueError('nav_history write validation failed: shares must be a number when status=CLOSED')
            return

        if nav.shares is None:
            raise ValueError('nav_history write validation failed: shares is required')
        if nav.nav is None:
            raise ValueError('nav_history write validation failed: nav is required')
        try:
            if float(nav.shares) <= 0:
                raise ValueError('nav_history write validation failed: shares must be > 0')
        except ValueError:
            raise
        except Exception:
            raise ValueError('nav_history write validation failed: shares must be a number')
        try:
            if float(nav.nav) <= 0:
                raise ValueError('nav_history write validation failed: nav must be > 0')
        except ValueError:
            raise
        except Exception:
            raise ValueError('nav_history write validation failed: nav must be a number')

    @staticmethod
    def _fields_contain_finality(fields: Dict[str, Any]) -> bool:
        """Return whether dropping the details field would lose finality provenance."""
        details = (fields or {}).get('details')
        if isinstance(details, str):
            try:
                details = json.loads(details)
            except (TypeError, ValueError):
                return False
        return isinstance(details, dict) and 'finality' in details

    def _fail_closed_if_finality_would_be_dropped(
        self,
        payloads: List[Dict[str, Any]],
        *,
        operation: str,
        cause: Exception,
    ) -> None:
        fields_list = [(payload or {}).get('fields') or {} for payload in payloads]
        if not any(self._fields_contain_finality(fields) for fields in fields_list):
            return
        raise RuntimeError(
            'nav_history authoritative write failed closed: Feishu returned FieldNameNotFound; '
            f'refusing {operation} retry without required details.finality'
        ) from cause

    @staticmethod
    def _batch_scope_row(
        operation: str,
        cache_row: Dict[str, Any],
        *,
        record_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return {
            'operation': operation,
            'date': cache_row.get('date'),
            'record_id': record_id or cache_row.get('record_id'),
        }

    def _stage_aware_batch_error(
        self,
        *,
        account: str,
        failed_operation: str,
        unconfirmed_outcome: str,
        cause: Exception,
        update_rows: List[Dict[str, Any]],
        create_rows: List[Dict[str, Any]],
        confirmed_update_results: Optional[List[Dict[str, Any]]] = None,
        confirmed_create_results: Optional[List[Dict[str, Any]]] = None,
    ) -> FeishuBatchWriteError:
        """Preserve cross-stage facts and replace optimistic cache authority."""
        if unconfirmed_outcome not in {'failed', 'unknown'}:
            raise ValueError(
                "unconfirmed_outcome must be either 'failed' or 'unknown'"
            )
        update_results = list(confirmed_update_results or [])
        create_results = list(confirmed_create_results or [])

        update_row_by_id = {
            str(row.get('record_id')): row
            for row in update_rows
            if row.get('record_id')
        }
        confirmed_update_scopes: List[Dict[str, Any]] = []
        confirmed_update_ids = set()
        for result in update_results:
            record_id = str((result or {}).get('record_id') or '').strip()
            if not record_id:
                continue
            confirmed_update_ids.add(record_id)
            confirmed_update_scopes.append(
                self._batch_scope_row(
                    'update',
                    update_row_by_id.get(record_id) or {'record_id': record_id},
                    record_id=record_id,
                )
            )

        confirmed_create_scopes: List[Dict[str, Any]] = []
        for index, result in enumerate(create_results):
            row = create_rows[index] if index < len(create_rows) else {}
            record_id = str((result or {}).get('record_id') or '').strip() or None
            confirmed_create_scopes.append(
                self._batch_scope_row('create', row, record_id=record_id)
            )

        unconfirmed_update_scopes = [
            self._batch_scope_row('update', row)
            for row in update_rows
            if str(row.get('record_id') or '') not in confirmed_update_ids
        ]
        unconfirmed_create_scopes = [
            self._batch_scope_row('create', row)
            for row in create_rows[len(confirmed_create_scopes):]
        ]
        if unconfirmed_outcome == 'unknown':
            unknown_update_scopes = unconfirmed_update_scopes
            unknown_create_scopes = unconfirmed_create_scopes
            failed_update_scopes: List[Dict[str, Any]] = []
            failed_create_scopes: List[Dict[str, Any]] = []
        else:
            unknown_update_scopes = []
            unknown_create_scopes = []
            failed_update_scopes = unconfirmed_update_scopes
            failed_create_scopes = unconfirmed_create_scopes

        chunk_offset = int(getattr(cause, 'chunk_offset', 0) or 0)
        reason = str(cause) or cause.__class__.__name__
        error = FeishuBatchWriteError(
            operation='nav_history_full_write',
            table_name='nav_history',
            chunk_offset=chunk_offset,
            reason=(
                f'account={account}, failed_operation={failed_operation}, '
                f'confirmed_update={len(confirmed_update_scopes)}, '
                f'confirmed_create={len(confirmed_create_scopes)}, '
                f'failed_update={len(failed_update_scopes)}, '
                f'failed_create={len(failed_create_scopes)}, '
                f'unknown_update={len(unknown_update_scopes)}, '
                f'unknown_create={len(unknown_create_scopes)}: {reason}'
            ),
            confirmed_results=[*update_results, *create_results],
        )
        error.account = account
        error.confirmed_scopes = {
            'update': confirmed_update_scopes,
            'create': confirmed_create_scopes,
        }
        error.unknown_scopes = {
            'update': unknown_update_scopes,
            'create': unknown_create_scopes,
        }
        error.failed_scopes = {
            'update': failed_update_scopes,
            'create': failed_create_scopes,
        }
        error.failure_stage = {
            'operation': failed_operation,
            'chunk_offset': chunk_offset,
            'reason': reason,
        }
        error.partial_write_possible = bool(
            confirmed_update_scopes
            or confirmed_create_scopes
            or unknown_update_scopes
            or unknown_create_scopes
        )

        cache_rebuild: Dict[str, Any]
        try:
            self._clear_nav_index_authority(account)
            rebuilt = self.preload_nav_index(account, force_refresh=True)
            cache_rebuild = {
                'status': 'rebuilt_from_fresh_read',
                'loaded': int(rebuilt.get('loaded', 0) or 0),
            }
        except Exception as rebuild_error:
            self._invalidate_nav_index(account)
            cache_rebuild = {
                'status': 'failed',
                'error': str(rebuild_error) or rebuild_error.__class__.__name__,
            }
        error.cache_rebuild = cache_rebuild
        return error

    def _execute_single_nav_write(self, nav: NAVHistory, existing_row: Optional[Dict[str, Any]], preserve_none_for_update: bool, dry_run: bool = False) -> Dict[str, Any]:
        """Execute one full nav write with the same semantics as bulk replace/upsert."""
        existing_record_id = (existing_row or {}).get('record_id')
        fields = self._nav_to_dict(nav)
        feishu_fields = self._to_feishu_fields(
            fields,
            'nav_history',
            preserve_none=bool(existing_record_id and preserve_none_for_update),
        )

        if dry_run:
            return {
                'existing': bool(existing_record_id),
                'record_id': existing_record_id,
                'fields': feishu_fields,
                'cache_row': self._nav_to_index_row(nav, updated_at=feishu_fields.get('updated_at')),
            }

        used_fields = feishu_fields
        compatibility_dropped_details = False
        try:
            if existing_record_id:
                self.client.update_record('nav_history', existing_record_id, feishu_fields)
                nav.record_id = existing_record_id
            else:
                result = self.client.create_record('nav_history', feishu_fields)
                nav.record_id = result['record_id']
        except Exception as e:
            msg = str(e)
            if 'FieldNameNotFound' not in msg:
                raise

            operation = 'update' if existing_record_id else 'create'
            self._fail_closed_if_finality_would_be_dropped(
                [{'record_id': existing_record_id, 'fields': feishu_fields}],
                operation=operation,
                cause=e,
            )

            fallback_fields = dict(feishu_fields)
            fallback_fields.pop('details', None)
            used_fields = fallback_fields
            compatibility_dropped_details = True

            if existing_record_id:
                self.client.update_record('nav_history', existing_record_id, fallback_fields)
                nav.record_id = existing_record_id
            else:
                result = self.client.create_record('nav_history', fallback_fields)
                nav.record_id = result['record_id']

        cache_row = self._nav_to_index_row(nav, updated_at=used_fields.get('updated_at'))
        if compatibility_dropped_details:
            cache_row['details'] = (
                (existing_row or {}).get('details')
                if existing_record_id
                else None
            )
        if existing_record_id and (not preserve_none_for_update):
            existing_cache_row = dict(existing_row or {})
            merged_row = dict(existing_cache_row)
            merged_row.update(cache_row)
            for k, v in list(merged_row.items()):
                if v is None and k in existing_cache_row:
                    merged_row[k] = existing_cache_row.get(k)
            cache_row = merged_row
        return {
            'existing': bool(existing_record_id),
            'record_id': nav.record_id,
            'fields': used_fields,
            'cache_row': cache_row,
        }

    def _write_nav_full_records(
        self,
        nav_list: List[NAVHistory],
        *,
        mode: str = 'replace',
        allow_partial: bool = False,
        dry_run: bool = False,
        use_batch_api: bool = True,
    ) -> Dict[str, Any]:
        """Unified full-record nav writer used by both single and bulk APIs."""
        if mode not in ('replace', 'upsert'):
            raise ValueError("mode must be 'replace' or 'upsert'")

        if not nav_list:
            return {
                'mode': mode, 'total': 0, 'updated': 0, 'created': 0,
                'preloaded_accounts': [], 'accounts': {}, 'errors': [], 'dry_run': dry_run,
            }

        grouped: Dict[str, List[NAVHistory]] = {}
        for nav in nav_list:
            if not nav:
                continue
            self._validate_nav_write(nav)
            grouped.setdefault(nav.account, []).append(nav)

        total_updated = 0
        total_created = 0
        preloaded_accounts: List[str] = []
        errors: List[Dict[str, Any]] = []
        account_results: Dict[str, Dict[str, Any]] = {}
        previews: List[Dict[str, Any]] = []

        for account in sorted(grouped.keys()):
            navs_raw = grouped.get(account) or []
            by_date_nav: Dict[str, NAVHistory] = {}
            for n in navs_raw:
                by_date_nav[self._safe_date_str(n.date)] = n
            navs = [by_date_nav[d] for d in sorted(by_date_nav.keys())]

            try:
                self.preload_nav_index(account, force_refresh=True)
                preloaded_accounts.append(account)
                idx = self.get_nav_index(account)
                existing_rows = list(idx.get('nav_history') or [])
                duplicates = self._nav_duplicate_groups_from_rows(existing_rows, default_account=account)
                if duplicates:
                    raise ValueError(self._duplicate_error_message(duplicates))

                existing_row_by_date: Dict[str, Dict[str, Any]] = {}
                for row in existing_rows:
                    ds = str((row or {}).get('date') or '')
                    if ds:
                        existing_row_by_date[ds] = dict(row or {})

                preserve_none_for_update = (mode == 'replace')

                if use_batch_api:
                    update_payloads: List[Dict[str, Any]] = []
                    update_rows_for_cache: List[Dict[str, Any]] = []
                    create_payloads: List[Dict[str, Any]] = []
                    create_rows_for_cache: List[Dict[str, Any]] = []
                    created_navs: List[NAVHistory] = []

                    for nav in sorted(navs, key=lambda x: x.date):
                        ds = self._safe_date_str(nav.date)
                        existing_row = existing_row_by_date.get(ds)
                        rid = (existing_row or {}).get('record_id')
                        fields = self._nav_to_dict(nav)
                        if rid:
                            feishu_fields = self._to_feishu_fields(fields, 'nav_history', preserve_none=preserve_none_for_update)
                            update_payloads.append({'record_id': rid, 'fields': feishu_fields})
                            nav.record_id = rid

                            merged_row = dict(existing_row or {})
                            merged_row.update(self._nav_to_index_row(nav, updated_at=feishu_fields.get('updated_at')))
                            if not preserve_none_for_update:
                                for k, v in list(merged_row.items()):
                                    if v is None and k in (existing_row or {}):
                                        merged_row[k] = existing_row.get(k)
                            update_rows_for_cache.append(merged_row)
                            if dry_run:
                                previews.append({'account': account, 'date': ds, 'existing': True, 'fields': feishu_fields})
                        else:
                            feishu_fields = self._to_feishu_fields(fields, 'nav_history', preserve_none=False)
                            create_payloads.append({'fields': feishu_fields})
                            create_rows_for_cache.append(self._nav_to_index_row(nav, updated_at=feishu_fields.get('updated_at')))
                            created_navs.append(nav)
                            if dry_run:
                                previews.append({'account': account, 'date': ds, 'existing': False, 'fields': feishu_fields})

                    if not dry_run:
                        updated_records: List[Dict[str, Any]] = []
                        created_records: List[Dict[str, Any]] = []
                        if update_payloads:
                            try:
                                updated_records = self.client.batch_update_records('nav_history', update_payloads)
                            except Exception as e:
                                msg = str(e)
                                confirmed_updates = list(getattr(e, 'confirmed_results', None) or [])
                                if 'FieldNameNotFound' in msg and not confirmed_updates:
                                    self._fail_closed_if_finality_would_be_dropped(
                                        update_payloads,
                                        operation='batch update',
                                        cause=e,
                                    )
                                    fallback_updates = []
                                    fallback_rows = []
                                    for p, row in zip(update_payloads, update_rows_for_cache):
                                        f = dict(p.get('fields') or {})
                                        f.pop('details', None)
                                        fallback_updates.append({'record_id': p['record_id'], 'fields': f})
                                        r = dict(row)
                                        r['updated_at'] = f.get('updated_at')
                                        existing_for_date = existing_row_by_date.get(
                                            str(r.get('date') or '')
                                        ) or {}
                                        r['details'] = existing_for_date.get('details')
                                        fallback_rows.append(r)
                                    try:
                                        updated_records = self.client.batch_update_records(
                                            'nav_history',
                                            fallback_updates,
                                        )
                                    except Exception as fallback_error:
                                        raise self._stage_aware_batch_error(
                                            account=account,
                                            failed_operation='update',
                                            unconfirmed_outcome=(
                                                'failed'
                                                if 'FieldNameNotFound' in str(fallback_error)
                                                else 'unknown'
                                            ),
                                            cause=fallback_error,
                                            update_rows=fallback_rows,
                                            create_rows=[],
                                            confirmed_update_results=list(
                                                getattr(fallback_error, 'confirmed_results', None) or []
                                            ),
                                        ) from fallback_error
                                    update_rows_for_cache = fallback_rows
                                else:
                                    raise self._stage_aware_batch_error(
                                        account=account,
                                        failed_operation='update',
                                        unconfirmed_outcome=(
                                            'failed' if 'FieldNameNotFound' in msg else 'unknown'
                                        ),
                                        cause=e,
                                        update_rows=update_rows_for_cache,
                                        create_rows=[],
                                        confirmed_update_results=confirmed_updates,
                                    ) from e

                        if create_payloads:
                            try:
                                created_records = self.client.batch_create_records('nav_history', create_payloads)
                            except Exception as e:
                                msg = str(e)
                                confirmed_creates = list(getattr(e, 'confirmed_results', None) or [])
                                if 'FieldNameNotFound' in msg and not confirmed_creates:
                                    try:
                                        self._fail_closed_if_finality_would_be_dropped(
                                            create_payloads,
                                            operation='batch create',
                                            cause=e,
                                        )
                                    except RuntimeError as finality_error:
                                        if updated_records:
                                            raise self._stage_aware_batch_error(
                                                account=account,
                                                failed_operation='create',
                                                unconfirmed_outcome='failed',
                                                cause=finality_error,
                                                update_rows=update_rows_for_cache,
                                                create_rows=create_rows_for_cache,
                                                confirmed_update_results=updated_records,
                                            ) from finality_error
                                        raise
                                    fallback_creates = []
                                    for p in create_payloads:
                                        f = dict((p.get('fields') or {}))
                                        f.pop('details', None)
                                        fallback_creates.append({'fields': f})
                                    try:
                                        created_records = self.client.batch_create_records(
                                            'nav_history',
                                            fallback_creates,
                                        )
                                    except Exception as fallback_error:
                                        raise self._stage_aware_batch_error(
                                            account=account,
                                            failed_operation='create',
                                            unconfirmed_outcome=(
                                                'failed'
                                                if 'FieldNameNotFound' in str(fallback_error)
                                                else 'unknown'
                                            ),
                                            cause=fallback_error,
                                            update_rows=update_rows_for_cache,
                                            create_rows=create_rows_for_cache,
                                            confirmed_update_results=updated_records,
                                            confirmed_create_results=list(
                                                getattr(fallback_error, 'confirmed_results', None) or []
                                            ),
                                        ) from fallback_error
                                    for i, p in enumerate(fallback_creates):
                                        if i < len(create_rows_for_cache):
                                            create_rows_for_cache[i]['updated_at'] = (p.get('fields') or {}).get('updated_at')
                                            create_rows_for_cache[i]['details'] = None
                                else:
                                    raise self._stage_aware_batch_error(
                                        account=account,
                                        failed_operation='create',
                                        unconfirmed_outcome=(
                                            'failed' if 'FieldNameNotFound' in msg else 'unknown'
                                        ),
                                        cause=e,
                                        update_rows=update_rows_for_cache,
                                        create_rows=create_rows_for_cache,
                                        confirmed_update_results=updated_records,
                                        confirmed_create_results=confirmed_creates,
                                    ) from e

                            for rec, nav, cache_row in zip(created_records, created_navs, create_rows_for_cache):
                                nav.record_id = rec['record_id']
                                cache_row['record_id'] = rec['record_id']

                        all_rows = []
                        all_rows.extend(update_rows_for_cache)
                        all_rows.extend(create_rows_for_cache)
                        if all_rows:
                            self._apply_nav_rows_to_local_cache(account, all_rows)

                        updated_n = len(updated_records)
                        created_n = len(created_records)
                    else:
                        updated_n = len(update_payloads)
                        created_n = len(create_payloads)
                else:
                    account_rows_for_cache: List[Dict[str, Any]] = []
                    updated_n = 0
                    created_n = 0
                    for nav in sorted(navs, key=lambda x: x.date):
                        ds = self._safe_date_str(nav.date)
                        existing_row = existing_row_by_date.get(ds)
                        outcome = self._execute_single_nav_write(nav, existing_row, preserve_none_for_update, dry_run=dry_run)
                        previews.append({
                            'account': account,
                            'date': ds,
                            'existing': outcome['existing'],
                            'fields': outcome['fields'],
                            'existing_row': dict(existing_row or {}),
                        })
                        if outcome['existing']:
                            updated_n += 1
                        else:
                            created_n += 1
                        if not dry_run:
                            account_rows_for_cache.append(outcome['cache_row'])
                    if account_rows_for_cache:
                        self._apply_nav_rows_to_local_cache(account, account_rows_for_cache)

                total_updated += updated_n
                total_created += created_n
                account_results[account] = {
                    'updated': updated_n,
                    'created': created_n,
                    'total': len(navs),
                }
            except Exception as e:
                err = {'account': account, 'error': str(e), 'count': len(navs)}
                errors.append(err)
                if (
                    not allow_partial
                    or (
                        isinstance(e, FeishuBatchWriteError)
                        and hasattr(e, 'confirmed_scopes')
                    )
                ):
                    raise
                account_results[account] = {
                    'updated': 0, 'created': 0, 'total': len(navs), 'error': str(e),
                }

        return {
            'mode': mode,
            'total': len(nav_list),
            'updated': total_updated,
            'created': total_created,
            'preloaded_accounts': preloaded_accounts,
            'accounts': account_results,
            'errors': errors,
            'dry_run': dry_run,
            'previews': previews,
        }

    def _write_one_nav_record(self, nav: NAVHistory, overwrite_existing: bool = False, dry_run: bool = False):
        preview_result = self._write_nav_full_records(
            [nav],
            mode='replace',
            allow_partial=False,
            dry_run=True,
            use_batch_api=False,
        )
        preview = (preview_result.get('previews') or [{}])[0]
        if preview.get('existing') and not overwrite_existing:
            raise ValueError(f"nav_history 已存在同日记录，拒绝覆盖: account={nav.account}, date={nav.date}")
        if dry_run:
            return {"existing": bool(preview.get('existing')), "fields": preview.get('fields')}
        outcome = self._execute_single_nav_write(
            nav,
            preview.get('existing_row') or None,
            preserve_none_for_update=True,
            dry_run=False,
        )
        self._apply_nav_rows_to_local_cache(nav.account, [outcome['cache_row']])
        return

    def write_nav_record(self, nav: NAVHistory, overwrite_existing: bool = False, dry_run: bool = False):
        """Write one full NAV row while serializing preview and mutation."""
        with process_lock(nav_history_lock_key()):
            return self._write_one_nav_record(
                nav,
                overwrite_existing=overwrite_existing,
                dry_run=dry_run,
            )

    def write_nav_records(
        self,
        nav_list: List[NAVHistory],
        mode: str = 'replace',
        allow_partial: bool = False,
        dry_run: bool = False,
    ) -> Dict[str, any]:
        """Write full NAV rows in bulk under the repository mutation lock."""
        with process_lock(nav_history_lock_key()):
            result = self._write_nav_full_records(
                nav_list,
                mode=mode,
                allow_partial=allow_partial,
                dry_run=dry_run,
                use_batch_api=not dry_run,
            )
            if not dry_run:
                result.pop('previews', None)
                result.pop('dry_run', None)
            return result

    def get_nav_history(self, account: str, days: int = 365) -> List[NAVHistory]:
        """获取净值历史（优先本地预加载索引）。"""
        from datetime import timedelta
        from ...time_utils import bj_today
        start_date = bj_today() - timedelta(days=days)

        idx = self.get_nav_index(account)
        navs: List[NAVHistory] = list(idx.get('_nav_objects') or [])
        if not navs:
            self.preload_nav_index(account, force_refresh=True)
            idx = self.get_nav_index(account)
            navs = list(idx.get('_nav_objects') or [])

        filtered = [n for n in navs if n.date and n.date >= start_date]
        filtered.sort(key=lambda n: n.date)
        return filtered

    def get_latest_nav(self, account: str) -> Optional[NAVHistory]:
        """获取最新净值记录（优先索引）。"""
        navs = self._get_indexed_navs(account)
        return navs[-1] if navs else None

    def get_nav_on_date(self, account: str, nav_date: date) -> Optional[NAVHistory]:
        """获取指定日期的净值记录。"""
        if isinstance(nav_date, datetime):
            nav_date = nav_date.date()
        elif isinstance(nav_date, str):
            nav_date = datetime.strptime(nav_date[:10], '%Y-%m-%d').date()

        navs = self._get_indexed_navs(account)
        matches = [n for n in navs if n.date == nav_date]

        if len(matches) > 1:
            logging.getLogger(__name__).warning(f"[警告] nav_history 存在重复日期记录: account={account}, date={nav_date}, count={len(matches)}")

        return matches[0] if matches else None

    def read_nav_maintenance_rows(self, account: str) -> List[Dict[str, Any]]:
        """Fresh-read complete NAV rows while retaining Missing/Null/Value state."""

        filter_str = f'CurrentValue.[account] = "{self._escape_filter_value(account)}"'
        try:
            records = self.client.list_records(
                'nav_history',
                filter_str=filter_str,
                field_names=self.NAV_CANONICAL_PROJECTION_FIELDS,
            )
        except Exception as exc:
            if 'FieldNameNotFound' not in str(exc):
                raise
            records = self.client.list_records(
                'nav_history',
                filter_str=filter_str,
                field_names=[
                    field
                    for field in self.NAV_CANONICAL_PROJECTION_FIELDS
                    if field != 'updated_at'
                ],
            )

        try:
            payload = self._build_nav_index_payload(account, records)
        except NavHistoryReadIntegrityError:
            self._clear_nav_index_authority(account)
            raise
        result: List[Dict[str, Any]] = []
        failure_record_id: Optional[str] = None
        failure_row_index = -1
        try:
            for row_index, record in enumerate(records):
                failure_row_index = row_index
                failure_record_id = record.get('record_id')
                raw_fields = record.get('fields') or {}
                fields = self._from_feishu_fields(raw_fields, 'nav_history')
                fields['record_id'] = record.get('record_id')
                nav = self._dict_to_nav(fields)
                states: Dict[str, Dict[str, Any]] = {}
                for field in self.NAV_CANONICAL_PROJECTION_FIELDS:
                    if field not in raw_fields:
                        states[field] = {'state': 'missing'}
                    elif raw_fields.get(field) in (None, ''):
                        states[field] = {'state': 'null'}
                    elif fields.get(field) is None:
                        raise ValueError(
                            f'non-null field failed canonical parsing: {field}'
                        )
                    else:
                        states[field] = {
                            'state': 'value',
                            'value': fields.get(field),
                        }
                result.append({
                    'nav': nav,
                    'record_id': nav.record_id,
                    'date': nav.date,
                    'field_states': states,
                })
        except (TypeError, ValueError) as exc:
            self._clear_nav_index_authority(account)
            raise NavHistoryReadIntegrityError(
                source='feishu_maintenance',
                account=account,
                record_id=failure_record_id,
                row_index=failure_row_index,
                cause=exc,
            ) from exc
        result.sort(key=lambda item: item['date'])
        self._store_nav_index_payload(account, payload)
        return result

    def _patch_nav_fields(
        self,
        record_id: str,
        fields: Dict[str, any],
        dry_run: bool = False,
        allowed_fields: Optional[set] = None,
    ):
        if allowed_fields is not None:
            illegal = [k for k in fields.keys() if k not in allowed_fields]
            if illegal:
                raise ValueError(f"update_nav_fields: illegal field(s): {illegal}. allowed={sorted(list(allowed_fields))}")

        normalized = {}
        for k, v in fields.items():
            if k in ('nav', 'mtd_nav_change', 'ytd_nav_change') and v is not None:
                normalized[k] = self._quantize_nav(v)
            elif k in ('shares', 'mtd_pnl', 'ytd_pnl', 'pnl', 'cash_flow', 'share_change') and v is not None:
                normalized[k] = self._quantize_money(v)
            elif k in ('stock_weight', 'cash_weight') and v is not None:
                normalized[k] = self._quantize_weight(v)
            else:
                normalized[k] = v

        feishu_fields = self._to_feishu_fields(normalized, 'nav_history', preserve_none=True)
        if dry_run:
            return {"record_id": record_id, "fields": feishu_fields}
        self.client.update_record('nav_history', record_id, feishu_fields)
        self._nav_index_loaded_accounts.clear()
        self._nav_index_mem_cache.clear()
        return {"record_id": record_id, "fields": feishu_fields}

    def patch_nav_derived_fields(self, record_id: str, fields: Dict[str, any], dry_run: bool = False):
        """Patch only derived NAV fields under the repository mutation lock."""
        with process_lock(nav_history_lock_key()):
            return self._patch_nav_fields(
                record_id,
                fields,
                dry_run=dry_run,
                allowed_fields=self.NAV_DERIVED_PATCH_FIELDS,
            )

    def patch_nav_maintenance_fields(
        self,
        record_id: str,
        field_states: Dict[str, Dict[str, Any]],
        dry_run: bool = False,
    ):
        """Apply one derived/details-only maintenance patch.

        The explicit state envelope keeps an absent field distinct from a
        present null in the journal and CAS preflight.  Both missing and null
        are sent as a clear operation because Feishu has no separate update
        instruction for physical key removal.
        """

        illegal = sorted(set(field_states) - self.NAV_MAINTENANCE_PATCH_FIELDS)
        if illegal:
            raise ValueError(
                f"nav maintenance patch contains non-derived fields: {illegal}"
            )
        fields: Dict[str, Any] = {}
        for field, envelope in field_states.items():
            if not isinstance(envelope, dict):
                raise TypeError(f"nav maintenance field state must be an object: {field}")
            state = envelope.get('state')
            if state not in {'missing', 'null', 'value'}:
                raise ValueError(f"invalid nav maintenance field state: {field}={state}")
            if state == 'value' and 'value' not in envelope:
                raise ValueError(f"nav maintenance value state has no value: {field}")
            value = envelope.get('value') if state == 'value' else None
            if field == 'details' and isinstance(value, dict):
                evidence_version = str(value.get('evidence_version') or '').strip().lower()
                snapshot_evidence = value.get('snapshot_evidence')
                snapshot_version = (
                    str((snapshot_evidence or {}).get('version') or '').strip().lower()
                    if isinstance(snapshot_evidence, dict)
                    else ''
                )
                snapshot_status = (
                    str((snapshot_evidence or {}).get('status') or '').strip().lower()
                    if isinstance(snapshot_evidence, dict)
                    else ''
                )
                if evidence_version in {'2', 'v2'} or (
                    snapshot_version in {'2', 'v2'} and snapshot_status == 'complete'
                ):
                    raise ValueError(
                        'derived-only NAV maintenance cannot claim snapshot v2 complete'
                    )
            fields[field] = value

        with process_lock(nav_history_lock_key()):
            return self._patch_nav_fields(
                record_id,
                fields,
                dry_run=dry_run,
                allowed_fields=self.NAV_MAINTENANCE_PATCH_FIELDS,
            )

    def patch_nav_details(self, record_id: str, details: Dict[str, any], dry_run: bool = False):
        """Patch only the recovery/status details object under the mutation lock."""
        with process_lock(nav_history_lock_key()):
            return self._patch_nav_fields(
                record_id,
                {"details": dict(details or {})},
                dry_run=dry_run,
                allowed_fields={"details"},
            )

    def get_latest_nav_before(self, account: str, before_date: date) -> Optional[NAVHistory]:
        """获取指定日期之前的最新净值记录（优先索引）。"""
        navs = self._get_indexed_navs(account)
        candidates = [n for n in navs if n.date and n.date < before_date]
        candidates.sort(key=lambda n: n.date, reverse=True)
        return candidates[0] if candidates else None

    def get_total_shares(self, account: str) -> float:
        """获取账户总份额"""
        latest = self.get_latest_nav(account)
        return latest.shares if latest else 0.0

    def _nav_to_dict(self, nav: NAVHistory) -> Dict:
        """NAVHistory 转字典"""
        return {
            'date': nav.date,
            'account': nav.account,
            'total_value': nav.total_value,
            'cash_value': nav.cash_value,
            'stock_value': nav.stock_value,
            'fund_value': nav.fund_value,
            'cn_stock_value': nav.cn_stock_value,
            'us_stock_value': nav.us_stock_value,
            'hk_stock_value': nav.hk_stock_value,
            'stock_weight': nav.stock_weight,
            'cash_weight': nav.cash_weight,
            'shares': nav.shares,
            'nav': nav.nav,
            'cash_flow': nav.cash_flow,
            'share_change': nav.share_change,
            'mtd_nav_change': nav.mtd_nav_change,
            'ytd_nav_change': nav.ytd_nav_change,
            'pnl': nav.pnl,
            'mtd_pnl': nav.mtd_pnl,
            'ytd_pnl': nav.ytd_pnl,
            'details': nav.details,
        }

    def _dict_to_nav(self, data: Dict) -> NAVHistory:
        """字典转 NAVHistory"""
        nav_date = data.get('date')
        if isinstance(nav_date, (int, float)):
            nav_date = datetime.fromtimestamp(nav_date / 1000, tz=self.FEISHU_DATE_TZ).date()
        elif isinstance(nav_date, str):
            nav_date = datetime.strptime(nav_date[:10], '%Y-%m-%d').date()

        def _opt_float(key):
            v = data.get(key)
            if v is None:
                return None
            return self._parse_float(v)

        return NAVHistory(
            date=nav_date,
            record_id=data.get('record_id'),
            account=data.get('account', ''),
            total_value=self._parse_float(data.get('total_value')) or 0.0,
            cash_value=_opt_float('cash_value'),
            stock_value=_opt_float('stock_value'),
            fund_value=_opt_float('fund_value'),
            cn_stock_value=_opt_float('cn_stock_value'),
            us_stock_value=_opt_float('us_stock_value'),
            hk_stock_value=_opt_float('hk_stock_value'),
            stock_weight=_opt_float('stock_weight'),
            cash_weight=_opt_float('cash_weight'),
            shares=_opt_float('shares'),
            nav=_opt_float('nav'),
            cash_flow=_opt_float('cash_flow'),
            share_change=_opt_float('share_change'),
            mtd_nav_change=_opt_float('mtd_nav_change'),
            ytd_nav_change=_opt_float('ytd_nav_change'),
            pnl=_opt_float('pnl'),
            mtd_pnl=_opt_float('mtd_pnl'),
            ytd_pnl=_opt_float('ytd_pnl'),
            details=data.get('details')
        )

    def delete_nav_by_record_id(self, record_id: str) -> bool:
        """Delete one NAV row under the repository mutation lock."""
        with process_lock(nav_history_lock_key()):
            ok = self.client.delete_record('nav_history', record_id)
            if ok:
                self._nav_index_loaded_accounts.clear()
                self._nav_index_mem_cache.clear()
            return ok
