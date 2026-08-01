"""Repository for the Feishu holdings table."""
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
import json
from math import isfinite
from typing import Dict, List, Optional

from ...domain.holding_dates import format_holding_date, parse_holding_date
from ...domain.holdings import RawHoldingRecord
from ...models import (
    Holding, AssetType, AssetClass, Industry,
)


class HoldingsIntegrityError(ValueError):
    """Fresh holdings rows could not be converted without inventing facts."""

    def __init__(self, errors: List[Dict[str, str]]):
        self.errors = list(errors)
        record_ids = ", ".join(sorted({item["record_id"] for item in errors}))
        super().__init__(f"invalid holdings records: {record_ids}")


class HoldingsRepository:
    """Holdings table operations + in-memory / persistent cache."""

    def __init__(self, storage):
        self.storage = storage

    def __getattr__(self, name: str):
        return getattr(self.storage, name)

    def _get_holding_cache_key(self, asset_id: str, account: str, broker: Optional[str]) -> str:
        """生成持仓缓存 key"""
        return f"{asset_id}:{account}:{broker or ''}"

    HOLDING_PROJECTION_FIELDS: List[str] = [
        'asset_id', 'asset_name', 'asset_type', 'account', 'broker',
        'quantity', 'avg_cost', 'currency', 'asset_class', 'industry', 'tag',
        'created_at', 'updated_at'
    ]

    def _snapshot_for_persistent_cache(self, holding: Holding) -> Dict[str, any]:
        return {
            'validation_policy_version': 'holdings-validation.v1',
            'record_id': holding.record_id,
            'asset_id': holding.asset_id,
            'asset_name': holding.asset_name,
            'asset_type': holding.asset_type.value if holding.asset_type else None,
            'broker': holding.broker or '',
            'account': holding.account,
            'quantity': holding.quantity,
            'avg_cost': holding.avg_cost,
            'currency': holding.currency,
            'asset_class': holding.asset_class.value if holding.asset_class else None,
            'industry': holding.industry.value if holding.industry else None,
            'tag': holding.tag,
            'created_at': format_holding_date(holding.created_at) if holding.created_at else None,
            'updated_at': format_holding_date(holding.updated_at) if holding.updated_at else None,
        }

    def _load_persistent_holdings_index(self):
        """启动时从本地缓存恢复持仓索引到内存。"""
        entries = self._local_holdings_index_cache.load_all()
        if not entries:
            return
        for bk, fields in entries.items():
            if not fields or not fields.get('record_id'):
                continue
            if fields.get('validation_policy_version') != 'holdings-validation.v1':
                continue
            try:
                holding = self._dict_to_holding(fields)
            except (TypeError, ValueError):
                continue
            asset_id = fields.get('asset_id', '')
            account = fields.get('account', '')
            broker = fields.get('broker') or ''
            cache_key = self._get_holding_cache_key(asset_id, account, broker or None)
            self._holding_id_cache[cache_key] = holding.record_id
            self._holding_fields_cache[cache_key] = dict(fields)

    def _flush_persistent_holdings_index(self):
        """将内存持仓索引刷写到本地缓存。"""
        self._local_holdings_index_cache.flush()

    def _invalidate_holding_cache_by_record_id(self, record_id: str, *, flush_persistent: bool = False):
        """通过 record_id 失效持仓缓存。"""
        keys_to_delete = [k for k, rid in self._holding_id_cache.items() if rid == record_id]
        for k in keys_to_delete:
            self._holding_id_cache.pop(k, None)
            self._holding_fields_cache.pop(k, None)
            self._local_holdings_index_cache.delete(k)
        if flush_persistent:
            self._flush_persistent_holdings_index()

    def _invalidate_holding_cache(self, asset_id: str, account: str, broker: Optional[str], *, flush_persistent: bool = False):
        cache_key = self._get_holding_cache_key(asset_id, account, broker)
        self._holding_id_cache.pop(cache_key, None)
        self._holding_fields_cache.pop(cache_key, None)
        self._local_holdings_index_cache.delete(cache_key)
        if flush_persistent:
            self._flush_persistent_holdings_index()

    def _put_holding_cache(self, holding: Holding, *, flush_persistent: bool = False):
        """Store holding into all cache layers (memory + persistent)."""
        self._validate_writable_holding(holding)
        if not holding.record_id:
            return

        cache_key = self._get_holding_cache_key(holding.asset_id, holding.account, holding.broker)
        self._holding_id_cache[cache_key] = holding.record_id
        self._holding_fields_cache[cache_key] = {
            'record_id': holding.record_id,
            'asset_id': holding.asset_id,
            'asset_name': holding.asset_name,
            'asset_type': holding.asset_type.value if holding.asset_type else None,
            'broker': holding.broker or '',
            'account': holding.account,
            'quantity': holding.quantity,
            'avg_cost': holding.avg_cost,
            'currency': holding.currency,
            'asset_class': holding.asset_class.value if holding.asset_class else None,
            'industry': holding.industry.value if holding.industry else None,
            'tag': holding.tag,
            'created_at': format_holding_date(holding.created_at) if holding.created_at else None,
            'updated_at': format_holding_date(holding.updated_at) if holding.updated_at else None,
        }

        self._local_holdings_index_cache.upsert(
            cache_key,
            self._snapshot_for_persistent_cache(holding),
            _flush=flush_persistent,
        )

    def _get_holding_from_cache(self, asset_id: str, account: str, broker: Optional[str]) -> Optional[Holding]:
        cache_key = self._get_holding_cache_key(asset_id, account, broker)
        fields = self._holding_fields_cache.get(cache_key)
        if not fields:
            return None
        return self._dict_to_holding(fields)

    def _get_holding_from_cache_any_market(self, asset_id: str, account: str) -> Optional[Holding]:
        prefix = f"{asset_id}:{account}:"
        best = None
        for k, fields in self._holding_fields_cache.items():
            if k.startswith(prefix):
                h = self._dict_to_holding(fields)
                if best is None:
                    best = h
                elif not (h.broker or ''):
                    best = h
                    break
        return best

    def preload_holdings_index(self, account: Optional[str] = None) -> Dict[str, any]:
        """预加载持仓索引到内存和本地缓存。"""
        records = self.get_raw_holdings(account=account)
        converted = self._convert_raw_holdings(records)

        keys_to_remove = [
            key
            for key, fields in self._holding_fields_cache.items()
            if account is None or fields.get('account') == account
        ]
        for key in keys_to_remove:
            self._holding_id_cache.pop(key, None)
            self._holding_fields_cache.pop(key, None)
            self._local_holdings_index_cache.delete(key)
        for holding in converted:
            self._put_holding_cache(holding)
        self._flush_persistent_holdings_index()

        if account:
            self._holdings_index_loaded_accounts.add(account)
        else:
            self._holdings_index_loaded_all = True

        return {
            'account': account or 'all',
            'loaded': len(converted),
            'source': 'feishu',
        }

    def _convert_raw_holdings(
        self,
        records: List[RawHoldingRecord],
    ) -> List[Holding]:
        """Convert a complete raw slice or report every integrity error."""

        converted: List[Holding] = []
        errors: List[Dict[str, str]] = []
        identities: Dict[tuple[str, str, str], List[str]] = {}
        for record in records:
            raw_identity = tuple(
                str(record.raw_fields.get(field) or '').strip()
                for field in ('asset_id', 'account', 'broker')
            )
            if all(raw_identity):
                identities.setdefault(raw_identity, []).append(record.record_id)
            fields = dict(record.raw_fields)
            fields['record_id'] = record.record_id
            try:
                converted.append(self._dict_to_holding(fields))
            except (TypeError, ValueError) as exc:
                errors.append({"record_id": record.record_id, "error": str(exc)})
        for identity, record_ids in identities.items():
            if len(record_ids) < 2:
                continue
            message = (
                "duplicate holding identity: "
                f"asset_id={identity[0]}, account={identity[1]}, broker={identity[2]}"
            )
            for duplicate_record_id in record_ids:
                errors.append({"record_id": duplicate_record_id, "error": message})
        if errors:
            raise HoldingsIntegrityError(errors)
        return converted

    def get_raw_holdings(
        self,
        *,
        account: Optional[str] = None,
        record_id: Optional[str] = None,
    ) -> List[RawHoldingRecord]:
        """Read complete untyped Feishu rows without applying query defaults."""

        requested_account = str(account).strip() if account is not None else None
        requested_record_id = str(record_id).strip() if record_id is not None else None
        if account is not None and not requested_account:
            raise ValueError("account must not be blank")
        if record_id is not None and not requested_record_id:
            raise ValueError("record_id must not be blank")
        if requested_account is not None and requested_record_id is not None:
            raise ValueError("account and record_id are mutually exclusive")
        fetched_at = datetime.now(UTC)
        if requested_record_id is not None:
            record = self.client.get_record_strict('holdings', requested_record_id)
            records = [record]
        else:
            filter_str = (
                f'CurrentValue.[account] = "{self._escape_filter_value(requested_account)}"'
                if requested_account is not None
                else None
            )
            records = self.client.list_records(
                'holdings',
                filter_str=filter_str,
                field_names=self.HOLDING_PROJECTION_FIELDS,
            )
        raw_records: List[RawHoldingRecord] = []
        for record in records:
            resolved_record_id = str((record or {}).get('record_id') or '').strip()
            fields = (record or {}).get('fields')
            if not resolved_record_id or not isinstance(fields, dict):
                raise RuntimeError("holdings source returned an incomplete record")
            if requested_record_id is not None and resolved_record_id != requested_record_id:
                raise RuntimeError(
                    "holdings source returned a different record: "
                    f"requested={requested_record_id}, returned={resolved_record_id}"
                )
            if requested_account is not None:
                returned_account = str(fields.get('account') or '').strip()
                if returned_account != requested_account:
                    raise RuntimeError(
                        "holdings source returned a record outside the requested account: "
                        f"record_id={resolved_record_id}, requested={requested_account}, "
                        f"returned={returned_account or '<missing>'}"
                    )
            raw_records.append(
                RawHoldingRecord(
                    record_id=resolved_record_id,
                    raw_fields=dict(fields),
                    source='feishu',
                    fetched_at=fetched_at,
                )
            )
        return raw_records

    def patch_holding_record(
        self,
        *,
        record_id: str,
        fields: Dict[str, object],
    ) -> RawHoldingRecord:
        """Narrow absolute patch used only by confirmed reconciliation flows."""

        from ...time_utils import bj_now_naive

        resolved_record_id = str(record_id or '').strip()
        if not resolved_record_id:
            raise ValueError("record_id is required")
        allowed = {'asset_name', 'asset_type', 'currency', 'asset_class'}
        supplied = {str(key): value for key, value in dict(fields).items()}
        if not supplied:
            raise ValueError("holding patch requires at least one target field")
        unsupported = sorted(set(supplied) - allowed)
        if unsupported:
            raise ValueError(
                "unsupported holdings reconciliation fields: "
                + ", ".join(unsupported)
            )
        if any(value is None or (isinstance(value, str) and not value.strip()) for value in supplied.values()):
            raise ValueError("holdings reconciliation patch cannot write blank values")
        update_fields = {
            **supplied,
            'updated_at': format_holding_date(bj_now_naive()),
        }
        feishu_fields = self._to_feishu_fields(update_fields, 'holdings')
        try:
            self.client.update_record('holdings', resolved_record_id, feishu_fields)
        finally:
            self._invalidate_holding_cache_by_record_id(
                resolved_record_id,
                flush_persistent=True,
            )
        records = self.get_raw_holdings(record_id=resolved_record_id)
        if len(records) != 1:
            raise RuntimeError(
                f"holding patch readback did not return one record: {resolved_record_id}"
            )
        return records[0]

    # ========== holdings CRUD ==========

    def get_holding(self, asset_id: str, account: str, broker: Optional[str] = None) -> Optional[Holding]:
        """获取单个持仓（优先使用内存索引与快照）"""
        if not str(asset_id or '').strip() or not str(account or '').strip():
            raise ValueError("asset_id and account are required")
        cached_holding = self._get_holding_from_cache(asset_id, account, broker)
        if not cached_holding and broker is None:
            cached_holding = self._get_holding_from_cache_any_market(asset_id, account)
        if cached_holding:
            return cached_holding

        if account and (not self._holdings_index_loaded_all) and (account not in self._holdings_index_loaded_accounts):
            self.preload_holdings_index(account=account)
            cached_holding = self._get_holding_from_cache(asset_id, account, broker)
            if not cached_holding and broker is None:
                cached_holding = self._get_holding_from_cache_any_market(asset_id, account)
            if cached_holding:
                return cached_holding

        if self._holdings_index_loaded_all or (account in self._holdings_index_loaded_accounts):
            return None
        return None

    def get_holding_fresh(self, asset_id: str, account: str, broker: str) -> Optional[Holding]:
        """Read one exact holding identity from Feishu, bypassing every cache.

        Cash-flow confirmation uses this method so a preview can never be
        applied against a stale local holding snapshot.
        """
        if not broker:
            raise ValueError("broker is required for an exact fresh holding read")
        filter_str = (
            f'CurrentValue.[asset_id] = "{self._escape_filter_value(asset_id)}" '
            f'AND CurrentValue.[account] = "{self._escape_filter_value(account)}" '
            f'AND CurrentValue.[broker] = "{self._escape_filter_value(broker)}"'
        )
        records = self.client.list_records(
            'holdings',
            filter_str=filter_str,
            field_names=self.HOLDING_PROJECTION_FIELDS,
        )
        if len(records) > 1:
            raise RuntimeError(
                f"duplicate holding identity: asset_id={asset_id}, account={account}, broker={broker}"
            )
        self._invalidate_holding_cache(asset_id, account, broker)
        if not records:
            return None

        raw = records[0]
        raw_fields = raw.get('fields')
        resolved_record_id = str(raw.get('record_id') or '').strip()
        if not resolved_record_id or not isinstance(raw_fields, dict) or any(
            str(raw_fields.get(field_name) or '').strip() != expected
            for field_name, expected in (
                ('asset_id', str(asset_id).strip()),
                ('account', str(account).strip()),
                ('broker', str(broker).strip()),
            )
        ):
            raise RuntimeError("exact holding read returned an identity mismatch")
        holding = self._convert_raw_holdings(
            [
                RawHoldingRecord(
                    record_id=resolved_record_id,
                    raw_fields=dict(raw_fields),
                    source='feishu',
                    fetched_at=datetime.now(UTC),
                )
            ]
        )[0]
        self._put_holding_cache(holding)
        return holding

    def get_holdings_fresh(
        self,
        *,
        account: Optional[str] = None,
        asset_type: Optional[str] = None,
        include_empty: bool = True,
    ) -> List[Holding]:
        """Read a complete holdings slice directly from Feishu."""
        converted = self._convert_raw_holdings(self.get_raw_holdings(account=account))
        for holding in converted:
            identity = (holding.asset_id, holding.account, holding.broker)
            self._invalidate_holding_cache(*identity)
            self._put_holding_cache(holding)
        holdings: List[Holding] = []
        for holding in converted:
            if asset_type and holding.asset_type.value != str(asset_type).strip().lower():
                continue
            if not include_empty and holding.quantity == 0:
                continue
            if (
                not include_empty
                and holding.quantity < 0
                and holding.asset_type != AssetType.CASH
            ):
                continue
            holdings.append(holding)
        holdings.sort(key=lambda item: (item.account, item.broker, item.asset_id))
        return holdings

    def get_holdings(self, account: Optional[str] = None, asset_type: Optional[str] = None, include_empty: bool = False) -> List[Holding]:
        """获取持仓列表（优先使用内存缓存索引）"""
        loaded = self._holdings_index_loaded_all or (
            account is not None and account in self._holdings_index_loaded_accounts
        )
        if not loaded:
            self.preload_holdings_index(account=account)
        holdings = []
        for fields in self._holding_fields_cache.values():
            if account and fields.get('account') != account:
                continue
            holding = self._dict_to_holding(fields)
            if asset_type and holding.asset_type.value != str(asset_type).strip().lower():
                continue
            if not include_empty and holding.quantity == 0:
                continue
            if not include_empty and holding.quantity < 0 and holding.asset_type != AssetType.CASH:
                continue
            holdings.append(holding)

        holdings.sort(key=lambda h: (h.asset_type.value if h.asset_type else '', h.asset_id))
        return holdings

    def upsert_holding(self, holding: Holding) -> Holding:
        """插入或更新持仓（优先使用预加载索引与内存快照）"""
        from ...time_utils import bj_now_naive

        self._validate_writable_holding(holding)
        now = bj_now_naive()
        now_text = format_holding_date(now)
        existing = self.get_holding(holding.asset_id, holding.account, holding.broker)

        if existing and existing.record_id:
            is_cash_like = (existing.asset_type and existing.asset_type.value in ('cash', 'mmf'))
            new_quantity = (
                self._quantize_money(existing.quantity + holding.quantity)
                if is_cash_like else (existing.quantity + holding.quantity)
            )
            update_fields = {
                'quantity': new_quantity,
                'updated_at': now_text,
            }

            new_name = holding.asset_name or existing.asset_name
            if new_name and new_name != (existing.asset_name or ''):
                update_fields['asset_name'] = new_name
                print(f"[持仓名称更新] {existing.asset_name} -> {new_name}")

            feishu_update_fields = self._to_feishu_fields(update_fields, 'holdings')
            try:
                self.client.update_record('holdings', existing.record_id, feishu_update_fields)
            except Exception:
                self._invalidate_holding_cache(holding.asset_id, holding.account, holding.broker, flush_persistent=True)
                raise

            existing.quantity = new_quantity
            existing.updated_at = now
            if 'asset_name' in update_fields:
                existing.asset_name = update_fields['asset_name']

            holding.record_id = existing.record_id
            holding.updated_at = now
            self._put_holding_cache(existing)
            return holding

        holding.created_at = now
        holding.updated_at = now
        fields = self._holding_to_dict(holding)
        feishu_fields = self._to_feishu_fields(fields, 'holdings')
        result = self.client.create_record('holdings', feishu_fields)
        holding.record_id = result['record_id']
        self._put_holding_cache(holding)
        return holding

    def replace_holding(self, holding: Holding) -> Holding:
        """Replace one holding row by business key.

        Unlike ``upsert_holding`` this treats ``quantity`` as the absolute
        target value and refreshes the canonical descriptor fields. CASH
        callers must enforce their confirmed effect boundary before invoking it.
        """
        from ...time_utils import bj_now_naive

        self._validate_writable_holding(holding)
        now = bj_now_naive()
        existing = self.get_holding(holding.asset_id, holding.account, holding.broker)

        if existing and existing.record_id:
            replacement = Holding(**holding.model_dump())
            replacement.record_id = existing.record_id
            replacement.created_at = existing.created_at
            replacement.updated_at = now
            fields = self._holding_to_dict(replacement)
            feishu_fields = self._to_feishu_fields(fields, 'holdings')
            try:
                self.client.update_record('holdings', existing.record_id, feishu_fields)
            except Exception:
                self._invalidate_holding_cache(holding.asset_id, holding.account, holding.broker, flush_persistent=True)
                raise
            self._put_holding_cache(replacement)
            return replacement

        new_holding = Holding(**holding.model_dump())
        new_holding.created_at = now
        new_holding.updated_at = now
        fields = self._holding_to_dict(new_holding)
        feishu_fields = self._to_feishu_fields(fields, 'holdings')
        result = self.client.create_record('holdings', feishu_fields)
        new_holding.record_id = result['record_id']
        self._put_holding_cache(new_holding)
        return new_holding

    def upsert_holdings_bulk(self, holdings: List[Holding], mode: str = 'additive') -> Dict[str, any]:
        """批量 upsert 持仓，减少 HTTP 调用。"""
        from ...time_utils import bj_now_naive

        if mode not in ('additive', 'replace'):
            raise ValueError(f"unsupported mode={mode}, expected 'additive' or 'replace'")

        if not holdings:
            return {'mode': mode, 'updated': 0, 'created': 0, 'preloaded_accounts': []}

        for holding in holdings:
            self._validate_writable_holding(holding)

        preloaded_accounts: List[str] = []
        if mode == 'additive':
            accounts_to_preload = set()
            for h in holdings:
                cache_key = self._get_holding_cache_key(h.asset_id, h.account, h.broker)
                has_cache = cache_key in self._holding_fields_cache
                if (not has_cache) and h.account and (not self._holdings_index_loaded_all) and (h.account not in self._holdings_index_loaded_accounts):
                    accounts_to_preload.add(h.account)
            for account in sorted(accounts_to_preload):
                self.preload_holdings_index(account=account)
                preloaded_accounts.append(account)

        now = bj_now_naive()
        now_text = format_holding_date(now)
        update_payloads: List[Dict[str, any]] = []
        update_targets: List[Holding] = []
        create_payloads: List[Dict[str, any]] = []
        create_targets: List[Holding] = []

        working_existing: Dict[str, Holding] = {}

        for incoming in holdings:
            cache_key = self._get_holding_cache_key(incoming.asset_id, incoming.account, incoming.broker)
            existing = working_existing.get(cache_key)
            if existing is None:
                existing = self.get_holding(incoming.asset_id, incoming.account, incoming.broker)
                if existing:
                    working_existing[cache_key] = Holding(**existing.model_dump())
                    existing = working_existing[cache_key]

            if existing and existing.record_id:
                if mode == 'replace':
                    new_quantity = incoming.quantity
                else:
                    is_cash_like = (existing.asset_type and existing.asset_type.value in ('cash', 'mmf'))
                    new_quantity = (
                        self._quantize_money(existing.quantity + incoming.quantity)
                        if is_cash_like else (existing.quantity + incoming.quantity)
                    )

                update_fields = {
                    'quantity': new_quantity,
                    'updated_at': now_text,
                }
                if mode == 'replace':
                    # Broker snapshots replace the current quantity and average
                    # cost together. None explicitly clears cost after exit.
                    update_fields['avg_cost'] = incoming.avg_cost
                new_name = incoming.asset_name or existing.asset_name
                if new_name and new_name != (existing.asset_name or ''):
                    update_fields['asset_name'] = new_name

                update_payloads.append({
                    'record_id': existing.record_id,
                    'fields': self._to_feishu_fields(
                        update_fields,
                        'holdings',
                        preserve_none=True,
                    ),
                })

                existing.quantity = new_quantity
                existing.updated_at = now
                if mode == 'replace':
                    existing.avg_cost = incoming.avg_cost
                if 'asset_name' in update_fields:
                    existing.asset_name = update_fields['asset_name']
                update_targets.append(Holding(**existing.model_dump()))
            else:
                new_holding = Holding(**incoming.model_dump())
                new_holding.created_at = now
                new_holding.updated_at = now
                fields = self._holding_to_dict(new_holding)
                feishu_fields = self._to_feishu_fields(fields, 'holdings')
                create_payloads.append({'fields': feishu_fields})
                create_targets.append(new_holding)

        updated_records: List[Dict[str, any]] = []
        created_records: List[Dict[str, any]] = []

        if update_payloads:
            try:
                updated_records = self.client.batch_update_records('holdings', update_payloads)
            except Exception:
                for h in update_targets:
                    self._invalidate_holding_cache(h.asset_id, h.account, h.broker)
                self._flush_persistent_holdings_index()
                raise
            for h in update_targets:
                self._put_holding_cache(h)

        if create_payloads:
            created_records = self.client.batch_create_records('holdings', create_payloads)
            for rec, h in zip(created_records, create_targets):
                h.record_id = rec['record_id']
                self._put_holding_cache(h)

        if update_payloads or create_payloads:
            self._flush_persistent_holdings_index()

        return {
            'mode': mode,
            'updated': len(updated_records),
            'created': len(created_records),
            'preloaded_accounts': preloaded_accounts,
        }

    def update_holding_quantity(self, asset_id: str, account: str, quantity_change: float, broker: Optional[str] = None):
        """更新持仓数量（优先使用预加载索引与内存快照）"""
        from ...time_utils import bj_now_naive

        holding = self.get_holding(asset_id, account, broker)
        if not holding or not holding.record_id:
            raise ValueError(f"holding not found: asset_id={asset_id}, account={account}, broker={broker or ''}")

        is_cash_like = (holding.asset_type and holding.asset_type.value in ('cash', 'mmf'))
        new_quantity = self._quantize_money(holding.quantity + quantity_change) if is_cash_like else (holding.quantity + quantity_change)
        if new_quantity < -1e-8:
            raise ValueError(
                f"holding quantity would become negative: asset_id={asset_id}, current={holding.quantity}, change={quantity_change}"
            )
        if abs(new_quantity) <= 1e-8:
            new_quantity = 0.0
        now = bj_now_naive()
        now_text = format_holding_date(now)
        update_fields = {
            'quantity': new_quantity,
            'updated_at': now_text,
        }
        feishu_update_fields = self._to_feishu_fields(update_fields, 'holdings')
        try:
            self.client.update_record('holdings', holding.record_id, feishu_update_fields)
        except Exception:
            self._invalidate_holding_cache(asset_id, account, holding.broker, flush_persistent=True)
            raise

        holding.quantity = new_quantity
        holding.updated_at = now
        self._put_holding_cache(holding)
        return holding

    def delete_holding_if_zero(self, asset_id: str, account: str, broker: Optional[str] = None):
        """如果持仓为0则删除（容忍极小浮点残值）"""
        holding = self.get_holding(asset_id, account, broker)
        if holding and holding.record_id and abs(holding.quantity) <= 1e-8:
            if not self.client.delete_record('holdings', holding.record_id):
                raise RuntimeError(f"Feishu holding delete was not confirmed: record_id={holding.record_id}")
            self._invalidate_holding_cache(asset_id, account, holding.broker, flush_persistent=True)

    def delete_holding_by_record_id(self, record_id: str) -> bool:
        """通过记录ID删除持仓"""
        ok = self.client.delete_record('holdings', record_id)
        if ok:
            self._invalidate_holding_cache_by_record_id(record_id, flush_persistent=True)
        return ok

    def _holding_to_dict(self, holding: Holding) -> Dict:
        """Holding 转字典"""
        result = {
            'asset_id': holding.asset_id,
            'asset_name': holding.asset_name,
            'asset_type': holding.asset_type,
            'broker': holding.broker or '',
            'account': holding.account,
            'quantity': holding.quantity,
            'avg_cost': holding.avg_cost,
            'currency': holding.currency,
            'asset_class': holding.asset_class,
            'industry': holding.industry,
            'tag': holding.tag,
        }

        if holding.created_at:
            result['created_at'] = format_holding_date(holding.created_at)
        if holding.updated_at:
            result['updated_at'] = format_holding_date(holding.updated_at)

        return result

    @staticmethod
    def _validate_writable_holding(holding: Holding) -> None:
        missing = [
            field_name
            for field_name in ('asset_id', 'account', 'broker', 'currency')
            if not str(getattr(holding, field_name, '') or '').strip()
        ]
        if missing:
            raise ValueError(
                "missing required holdings fields: " + ", ".join(sorted(missing))
            )
        currency = str(holding.currency).strip().upper()
        if not currency.isascii() or not currency.isalpha() or not 3 <= len(currency) <= 5:
            raise ValueError(f"invalid currency: {holding.currency}")
        if not isfinite(float(holding.quantity)):
            raise ValueError(f"invalid quantity: {holding.quantity}")
        if holding.avg_cost is not None and not isfinite(float(holding.avg_cost)):
            raise ValueError(f"invalid avg_cost: {holding.avg_cost}")

    def _dict_to_holding(self, data: Dict) -> Holding:
        """Convert already validated fields without manufacturing defaults."""
        required_text = ('asset_id', 'asset_type', 'account', 'broker', 'currency')
        missing = [
            field
            for field in required_text
            if data.get(field) is None or not str(data.get(field)).strip()
        ]
        if data.get('quantity') is None or (
            isinstance(data.get('quantity'), str) and not data.get('quantity').strip()
        ):
            missing.append('quantity')
        if missing:
            raise ValueError("missing required holdings fields: " + ", ".join(sorted(missing)))

        try:
            asset_type = AssetType(str(data['asset_type']).strip().lower())
        except ValueError as exc:
            raise ValueError(f"invalid asset_type: {data.get('asset_type')}") from exc
        quantity = self._strict_holding_number(data['quantity'], field_name='quantity')
        currency = str(data['currency']).strip().upper()
        if not currency.isascii() or not currency.isalpha() or not 3 <= len(currency) <= 5:
            raise ValueError(f"invalid currency: {data.get('currency')}")
        avg_cost = (
            self._strict_holding_number(data.get('avg_cost'), field_name='avg_cost')
            if data.get('avg_cost') not in (None, '')
            else None
        )
        tag = self._strict_holding_tag(data.get('tag'))
        created_at = self._strict_holding_timestamp(
            data.get('created_at'), field_name='created_at'
        )
        updated_at = self._strict_holding_timestamp(
            data.get('updated_at'), field_name='updated_at'
        )

        return Holding(
            record_id=data.get('record_id'),
            asset_id=str(data['asset_id']).strip(),
            asset_name=str(data.get('asset_name') or ''),
            asset_type=asset_type,
            broker=str(data['broker']).strip(),
            account=str(data['account']).strip(),
            quantity=quantity,
            avg_cost=avg_cost,
            currency=currency,
            asset_class=AssetClass(data.get('asset_class')) if data.get('asset_class') else None,
            industry=Industry(data.get('industry')) if data.get('industry') else None,
            tag=tag,
            created_at=created_at,
            updated_at=updated_at
        )

    @staticmethod
    def _strict_holding_number(value, *, field_name: str) -> float:
        if isinstance(value, bool):
            raise ValueError(f"invalid {field_name}: {value}")
        try:
            parsed = Decimal(str(value).replace(',', '').strip())
        except (InvalidOperation, AttributeError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid {field_name}: {value}") from exc
        if not parsed.is_finite():
            raise ValueError(f"invalid {field_name}: {value}")
        try:
            result = float(parsed)
        except (OverflowError, ValueError) as exc:
            raise ValueError(f"invalid {field_name}: {value}") from exc
        if not isfinite(result):
            raise ValueError(f"invalid {field_name}: {value}")
        return result

    @staticmethod
    def _strict_holding_tag(value) -> List[str]:
        if value in (None, ''):
            return []
        candidate = value
        if isinstance(candidate, str):
            try:
                candidate = json.loads(candidate)
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                raise ValueError("invalid tag") from exc
        if not isinstance(candidate, list) or not all(
            isinstance(item, str) for item in candidate
        ):
            raise ValueError("invalid tag")
        return list(candidate)

    @staticmethod
    def _strict_holding_timestamp(value, *, field_name: str) -> Optional[datetime]:
        return parse_holding_date(value, field_name=field_name)
