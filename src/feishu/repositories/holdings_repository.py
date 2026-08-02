"""Repository for the Feishu holdings table."""
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
import json
from math import isfinite
from typing import Any, Dict, Iterable, List, Optional

from ...domain.holding_dates import format_holding_date, parse_holding_date
from ...domain.holding_mutations import (
    AmbiguousHoldingIdentityError,
    HOLDING_REQUIRED_VALUE_FIELDS,
    HOLDING_VALUE_FIELDS,
    HoldingIdentity,
    HoldingMutationConflictError,
    HoldingMutationProofError,
    HoldingPatch,
    HoldingRepairPatch,
    HoldingTarget,
    canonical_holding,
    canonical_holding_value,
    explicit_holding_owned_fields,
    holding_owned_fields_match,
    raw_holding_state_digest,
    holding_state_digest,
    holding_values,
)
from ...domain.holdings import RawHoldingRecord
from ..contracts import get_table_contract
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

    def _get_holding_cache_key(self, asset_id: str, account: str, broker: str) -> str:
        """生成持仓缓存 key"""
        return HoldingIdentity(asset_id, account, broker).cache_key()

    HOLDING_PROJECTION_FIELDS: List[str] = list(
        get_table_contract("holdings").fields_by_name
    )

    def _snapshot_for_persistent_cache(self, holding: Holding) -> Dict[str, any]:
        holding = canonical_holding(holding)
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
        migrated = False
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
            try:
                cache_key = self._get_holding_cache_key(asset_id, account, broker)
            except ValueError:
                continue
            canonical_snapshot = self._snapshot_for_persistent_cache(holding)
            self._holding_id_cache[cache_key] = holding.record_id
            self._holding_fields_cache[cache_key] = dict(canonical_snapshot)
            if bk != cache_key or fields != canonical_snapshot:
                self._local_holdings_index_cache.delete(bk)
                self._local_holdings_index_cache.upsert(
                    cache_key,
                    canonical_snapshot,
                )
                migrated = True
        if migrated:
            self._flush_persistent_holdings_index()

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

    def _invalidate_holding_cache(self, asset_id: str, account: str, broker: str, *, flush_persistent: bool = False):
        cache_key = self._get_holding_cache_key(asset_id, account, broker)
        self._holding_id_cache.pop(cache_key, None)
        self._holding_fields_cache.pop(cache_key, None)
        self._local_holdings_index_cache.delete(cache_key)
        if flush_persistent:
            self._flush_persistent_holdings_index()

    def _put_holding_cache(self, holding: Holding, *, flush_persistent: bool = False):
        """Store holding into all cache layers (memory + persistent)."""
        canonical = canonical_holding(holding)
        if not canonical.record_id:
            return

        cache_key = self._get_holding_cache_key(
            canonical.asset_id,
            canonical.account,
            canonical.broker,
        )
        self._holding_id_cache[cache_key] = canonical.record_id
        self._holding_fields_cache[cache_key] = {
            'record_id': canonical.record_id,
            'asset_id': canonical.asset_id,
            'asset_name': canonical.asset_name,
            'asset_type': canonical.asset_type.value if canonical.asset_type else None,
            'broker': canonical.broker,
            'account': canonical.account,
            'quantity': canonical.quantity,
            'avg_cost': canonical.avg_cost,
            'currency': canonical.currency,
            'asset_class': canonical.asset_class.value if canonical.asset_class else None,
            'industry': canonical.industry.value if canonical.industry else None,
            'tag': canonical.tag,
            'created_at': format_holding_date(canonical.created_at) if canonical.created_at else None,
            'updated_at': format_holding_date(canonical.updated_at) if canonical.updated_at else None,
        }

        self._local_holdings_index_cache.upsert(
            cache_key,
            self._snapshot_for_persistent_cache(canonical),
            _flush=flush_persistent,
        )

    def _get_holding_from_cache(self, asset_id: str, account: str, broker: str) -> Optional[Holding]:
        cache_key = self._get_holding_cache_key(asset_id, account, broker)
        fields = self._holding_fields_cache.get(cache_key)
        if not fields:
            return None
        return self._dict_to_holding(fields)

    def _get_holding_from_cache_any_market(self, asset_id: str, account: str) -> Optional[Holding]:
        requested_asset_id = str(asset_id or '').strip()
        requested_account = str(account or '').strip()
        if not requested_asset_id or not requested_account:
            raise ValueError("asset_id and account are required")
        candidates = [
            self._dict_to_holding(fields)
            for fields in self._holding_fields_cache.values()
            if str(fields.get('asset_id') or '').strip() == requested_asset_id
            and str(fields.get('account') or '').strip() == requested_account
        ]
        if len(candidates) > 1:
            brokers = sorted({item.broker for item in candidates})
            raise AmbiguousHoldingIdentityError(
                "holding lookup requires broker; "
                f"asset_id={requested_asset_id}, account={requested_account}, "
                f"brokers={brokers}"
            )
        return candidates[0] if candidates else None

    def _invalidate_holding_account_cache(
        self,
        account: str,
        *,
        flush_persistent: bool = False,
    ) -> None:
        requested_account = str(account or '').strip()
        if not requested_account:
            raise ValueError("account is required")
        persistent_entries = self._local_holdings_index_cache.load_all()
        keys_to_remove = {
            key
            for key, fields in self._holding_fields_cache.items()
            if str(fields.get('account') or '').strip() == requested_account
        }
        keys_to_remove.update(
            key
            for key, fields in persistent_entries.items()
            if str((fields or {}).get('account') or '').strip() == requested_account
        )
        for key in keys_to_remove:
            self._holding_id_cache.pop(key, None)
            self._holding_fields_cache.pop(key, None)
            self._local_holdings_index_cache.delete(key)
        self._holdings_index_loaded_accounts.discard(requested_account)
        self._holdings_index_loaded_all = False
        if flush_persistent:
            self._flush_persistent_holdings_index()

    def _publish_holding_account_slice(
        self,
        account: str,
        holdings: Iterable[Holding],
    ) -> None:
        requested_account = str(account or '').strip()
        rows = [canonical_holding(item) for item in holdings]
        if any(item.account != requested_account for item in rows):
            raise RuntimeError("cannot publish an out-of-scope holdings account slice")
        self._invalidate_holding_account_cache(requested_account)
        for holding in rows:
            self._put_holding_cache(holding)
        self._flush_persistent_holdings_index()
        self._holdings_index_loaded_accounts.add(requested_account)

    def _publish_all_holding_slices(self, holdings: Iterable[Holding]) -> None:
        rows = [canonical_holding(item) for item in holdings]
        keys_to_remove = set(self._holding_fields_cache)
        keys_to_remove.update(self._local_holdings_index_cache.load_all())
        for key in keys_to_remove:
            self._holding_id_cache.pop(key, None)
            self._holding_fields_cache.pop(key, None)
            self._local_holdings_index_cache.delete(key)
        self._holdings_index_loaded_accounts.clear()
        for holding in rows:
            self._put_holding_cache(holding)
        self._flush_persistent_holdings_index()
        self._holdings_index_loaded_accounts.update(
            item.account for item in rows
        )
        self._holdings_index_loaded_all = True

    def _read_fresh_holding_account_slice(self, account: str) -> List[Holding]:
        requested_account = str(account or '').strip()
        if not requested_account:
            raise ValueError("account is required")
        return self._convert_raw_holdings(
            self.get_raw_holdings(account=requested_account)
        )

    @staticmethod
    def _find_holding_identity(
        holdings: Iterable[Holding],
        identity: HoldingIdentity,
    ) -> Optional[Holding]:
        matches = [
            item
            for item in holdings
            if HoldingIdentity.from_holding(item) == identity
        ]
        if len(matches) > 1:
            raise RuntimeError(f"duplicate holding identity: {identity}")
        return matches[0] if matches else None

    def _fresh_base_for_target(
        self,
        target: HoldingTarget,
    ) -> tuple[List[Holding], Optional[Holding]]:
        try:
            fresh = self._read_fresh_holding_account_slice(
                target.identity.account,
            )
            current = self._find_holding_identity(fresh, target.identity)
            if target.base_record_id is None:
                if current is not None:
                    raise HoldingMutationConflictError(
                        "holding create base is no longer empty: "
                        f"{target.identity}"
                    )
                return fresh, None
            if current is None:
                raise HoldingMutationConflictError(
                    f"holding base disappeared: {target.identity}"
                )
            if current.record_id != target.base_record_id:
                raise HoldingMutationConflictError(
                    "holding base record changed: "
                    f"expected={target.base_record_id}, actual={current.record_id}"
                )
            if holding_state_digest(current) != target.base_digest:
                raise HoldingMutationConflictError(
                    f"holding fresh base digest changed: {target.identity}"
                )
            return fresh, current
        except Exception:
            self._invalidate_holding_account_cache(
                target.identity.account,
                flush_persistent=True,
            )
            raise

    def _prove_holding_targets_and_publish(
        self,
        account: str,
        targets: Iterable[tuple[HoldingTarget, Optional[str]]],
    ) -> Dict[HoldingIdentity, Holding]:
        expected = list(targets)
        try:
            fresh = self._read_fresh_holding_account_slice(account)
            proven: Dict[HoldingIdentity, Holding] = {}
            for target, expected_record_id in expected:
                actual = self._find_holding_identity(fresh, target.identity)
                if actual is None:
                    raise HoldingMutationProofError(
                        f"holding fresh readback is missing: {target.identity}"
                    )
                if expected_record_id and actual.record_id != expected_record_id:
                    raise HoldingMutationProofError(
                        "holding fresh readback record changed: "
                        f"expected={expected_record_id}, actual={actual.record_id}"
                    )
                if not holding_owned_fields_match(actual, target):
                    raise HoldingMutationProofError(
                        "holding fresh readback disagrees with owned fields: "
                        f"identity={target.identity}, owned={sorted(target.owned_fields)}"
                    )
                proven[target.identity] = canonical_holding(actual)
        except Exception:
            self._invalidate_holding_account_cache(
                account,
                flush_persistent=True,
            )
            raise
        self._publish_holding_account_slice(account, fresh)
        return proven

    def _prove_holding_deleted_and_publish(
        self,
        identity: HoldingIdentity,
    ) -> None:
        try:
            fresh = self._read_fresh_holding_account_slice(identity.account)
            if self._find_holding_identity(fresh, identity) is not None:
                raise HoldingMutationProofError(
                    f"holding fresh readback still contains deleted identity: {identity}"
                )
        except Exception:
            self._invalidate_holding_account_cache(
                identity.account,
                flush_persistent=True,
            )
            raise
        self._publish_holding_account_slice(identity.account, fresh)

    def preload_holdings_index(self, account: Optional[str] = None) -> Dict[str, any]:
        """预加载持仓索引到内存和本地缓存。"""
        records = self.get_raw_holdings(account=account)
        converted = self._convert_raw_holdings(records)

        if account:
            self._publish_holding_account_slice(account, converted)
        else:
            self._publish_all_holding_slices(converted)

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
        patch: HoldingRepairPatch,
    ) -> RawHoldingRecord:
        """Narrow absolute patch used only by confirmed reconciliation flows."""

        from ...time_utils import bj_now_naive

        if not isinstance(patch, HoldingRepairPatch):
            raise TypeError("patch_holding_record requires HoldingRepairPatch")
        identity = patch.identity
        resolved_record_id = patch.record_id
        current_identity: Optional[HoldingIdentity] = None
        try:
            base_records = self.get_raw_holdings(record_id=resolved_record_id)
            if len(base_records) != 1:
                raise RuntimeError(
                    "holding patch lookup did not return one record: "
                    f"{resolved_record_id}"
                )
            base_fields = base_records[0].canonical_fields()
            current_identity = HoldingIdentity(
                base_fields.get('asset_id'),
                base_fields.get('account'),
                base_fields.get('broker'),
            )
            if (
                current_identity != identity
                or raw_holding_state_digest(
                    resolved_record_id,
                    base_fields,
                ) != patch.base_digest
            ):
                raise HoldingMutationConflictError(
                    f"holding repair base changed: {identity}"
                )
        except Exception:
            accounts = {identity.account}
            if current_identity is not None:
                accounts.add(current_identity.account)
            for account in accounts:
                self._invalidate_holding_account_cache(
                    account,
                    flush_persistent=True,
                )
            raise
        canonical_supplied = dict(patch.values)
        update_fields = {
            **canonical_supplied,
            'updated_at': format_holding_date(bj_now_naive()),
        }
        feishu_fields = self._to_feishu_fields(
            update_fields,
            'holdings',
            preserve_none=True,
        )
        readback_identity: Optional[HoldingIdentity] = None
        try:
            self.client.update_record('holdings', resolved_record_id, feishu_fields)
            records = self.get_raw_holdings(record_id=resolved_record_id)
            if len(records) != 1:
                raise RuntimeError(
                    "holding patch readback did not return one record: "
                    f"{resolved_record_id}"
                )
            readback_fields = records[0].canonical_fields()
            readback_identity = HoldingIdentity(
                readback_fields.get('asset_id'),
                readback_fields.get('account'),
                readback_fields.get('broker'),
            )
            if (
                readback_identity != identity
                or any(
                    readback_fields.get(field_name) != value
                    for field_name, value in canonical_supplied.items()
                )
            ):
                raise HoldingMutationProofError(
                    "holding patch readback disagrees with requested fields: "
                    f"{identity}"
                )
            fresh = self._read_fresh_holding_account_slice(identity.account)
            self._publish_holding_account_slice(identity.account, fresh)
        except Exception:
            accounts = {identity.account}
            if readback_identity is not None:
                accounts.add(readback_identity.account)
            for account in accounts:
                self._invalidate_holding_account_cache(
                    account,
                    flush_persistent=True,
                )
            raise
        return records[0]

    # ========== holdings CRUD ==========

    def get_holding(self, asset_id: str, account: str, broker: Optional[str] = None) -> Optional[Holding]:
        """Read one cached holding; omitted broker is allowed only if unique."""
        requested_asset_id = str(asset_id or '').strip()
        requested_account = str(account or '').strip()
        if not requested_asset_id or not requested_account:
            raise ValueError("asset_id and account are required")
        requested_broker = str(broker or '').strip() if broker is not None else None
        if broker is not None and not requested_broker:
            raise ValueError("broker must not be blank")

        # A broker-less compatibility lookup is a uniqueness decision, so it
        # may only inspect a complete account slice.  A restored persistent
        # cache is an acceleration hint, not proof that no second broker row
        # exists remotely.
        if (
            requested_broker is None
            and not self._holdings_index_loaded_all
            and requested_account not in self._holdings_index_loaded_accounts
        ):
            self.preload_holdings_index(account=requested_account)

        cached_holding = (
            self._get_holding_from_cache(
                requested_asset_id,
                requested_account,
                requested_broker,
            )
            if requested_broker is not None
            else self._get_holding_from_cache_any_market(
                requested_asset_id,
                requested_account,
            )
        )
        if cached_holding:
            return cached_holding

        if (
            not self._holdings_index_loaded_all
            and requested_account not in self._holdings_index_loaded_accounts
        ):
            self.preload_holdings_index(account=requested_account)
            cached_holding = (
                self._get_holding_from_cache(
                    requested_asset_id,
                    requested_account,
                    requested_broker,
                )
                if requested_broker is not None
                else self._get_holding_from_cache_any_market(
                    requested_asset_id,
                    requested_account,
                )
            )
            if cached_holding:
                return cached_holding
        return None

    def get_holding_fresh(self, asset_id: str, account: str, broker: str) -> Optional[Holding]:
        """Read and publish one complete fresh account slice, then select exactly."""
        identity = HoldingIdentity(asset_id, account, broker)
        fresh = self._read_fresh_holding_account_slice(identity.account)
        holding = self._find_holding_identity(fresh, identity)
        self._publish_holding_account_slice(identity.account, fresh)
        return canonical_holding(holding) if holding is not None else None

    def get_holdings_fresh(
        self,
        *,
        account: Optional[str] = None,
        asset_type: Optional[str] = None,
        include_empty: bool = True,
    ) -> List[Holding]:
        """Read a complete holdings slice directly from Feishu."""
        converted = self._convert_raw_holdings(self.get_raw_holdings(account=account))
        if account is not None:
            self._publish_holding_account_slice(str(account).strip(), converted)
        else:
            self._publish_all_holding_slices(converted)
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
        requested_account = str(account).strip() if account is not None else None
        if account is not None and not requested_account:
            raise ValueError("account must not be blank")
        loaded = self._holdings_index_loaded_all or (
            requested_account is not None
            and requested_account in self._holdings_index_loaded_accounts
        )
        if not loaded:
            self.preload_holdings_index(account=requested_account)
        holdings = []
        for fields in self._holding_fields_cache.values():
            if requested_account is not None and fields.get('account') != requested_account:
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

    @staticmethod
    def _wire_mutation_values(values: Dict[str, Any]) -> Dict[str, Any]:
        normalized = dict(values)
        if isinstance(normalized.get('tag'), tuple):
            normalized['tag'] = list(normalized['tag'])
        return normalized

    def _apply_holding_target(self, target: HoldingTarget) -> Holding:
        """Apply one canonical target and return only fresh remote proof."""
        from ...time_utils import bj_now_naive

        if not isinstance(target, HoldingTarget):
            raise TypeError("replace_holding requires HoldingTarget")
        _fresh, current = self._fresh_base_for_target(target)
        now = bj_now_naive()
        if current is not None:
            raw_update = {
                field_name: target.values[field_name]
                for field_name in target.owned_fields
            }
            raw_update['updated_at'] = format_holding_date(now)
            payload = self._to_feishu_fields(
                self._wire_mutation_values(raw_update),
                'holdings',
                preserve_none=True,
            )
            try:
                self.client.update_record(
                    'holdings',
                    target.base_record_id,
                    payload,
                )
            except Exception:
                self._invalidate_holding_account_cache(
                    target.identity.account,
                    flush_persistent=True,
                )
                raise
            expected_record_id = target.base_record_id
        else:
            raw_create: Dict[str, Any] = {
                **target.identity.as_dict(),
                **{
                    field_name: target.values[field_name]
                    for field_name in target.owned_fields
                    if target.values[field_name] is not None
                },
                'created_at': format_holding_date(now),
                'updated_at': format_holding_date(now),
            }
            payload = self._to_feishu_fields(
                self._wire_mutation_values(raw_create),
                'holdings',
            )
            try:
                result = self.client.create_record('holdings', payload)
            except Exception:
                self._invalidate_holding_account_cache(
                    target.identity.account,
                    flush_persistent=True,
                )
                raise
            expected_record_id = str(result.get('record_id') or '').strip()
            if not expected_record_id:
                self._invalidate_holding_account_cache(
                    target.identity.account,
                    flush_persistent=True,
                )
                raise RuntimeError("holding create response lacks record_id")
        return self._prove_holding_targets_and_publish(
            target.identity.account,
            [(target, expected_record_id)],
        )[target.identity]

    def apply_holding_patch(self, patch: HoldingPatch) -> Holding:
        """Apply only explicitly set patch fields against the bound fresh base."""
        if not isinstance(patch, HoldingPatch):
            raise TypeError("apply_holding_patch requires HoldingPatch")
        try:
            fresh = self._read_fresh_holding_account_slice(
                patch.identity.account,
            )
            current = self._find_holding_identity(fresh, patch.identity)
            if current is None or current.record_id != patch.base_record_id:
                raise HoldingMutationConflictError(
                    f"holding patch base is missing or changed: {patch.identity}"
                )
            if holding_state_digest(current) != patch.base_digest:
                raise HoldingMutationConflictError(
                    f"holding patch fresh base digest changed: {patch.identity}"
                )
        except Exception:
            self._invalidate_holding_account_cache(
                patch.identity.account,
                flush_persistent=True,
            )
            raise
        desired = canonical_holding(current)
        for field_name, value in patch.values.items():
            setattr(
                desired,
                field_name,
                list(value) if field_name == 'tag' else value,
            )
        target = HoldingTarget.from_holdings(
            base=current,
            target=desired,
            owned_fields=patch.owned_fields,
        )
        return self._apply_holding_target(target)

    def _legacy_replace_target(self, holding: Holding) -> HoldingTarget:
        """Safely adapt an old Holding payload without granting defaults authority."""
        canonical = canonical_holding(holding)
        identity = HoldingIdentity.from_holding(canonical)
        fresh = self._read_fresh_holding_account_slice(identity.account)
        base = self._find_holding_identity(fresh, identity)
        explicit = explicit_holding_owned_fields(holding)
        owned = set(explicit & {
            'asset_name', 'asset_type', 'quantity', 'currency',
        })
        for optional_field in ('avg_cost', 'asset_class', 'industry'):
            if optional_field in explicit:
                owned.add(optional_field)
        if 'tag' in explicit:
            owned.add('tag')
        if base is None:
            owned.update(HOLDING_REQUIRED_VALUE_FIELDS)
            for optional_field in ('avg_cost', 'asset_class', 'industry'):
                if getattr(canonical, optional_field) is not None:
                    owned.add(optional_field)
            if canonical.tag:
                owned.add('tag')
            desired = canonical
        else:
            desired = canonical_holding(base)
            incoming_values = holding_values(canonical)
            for field_name in owned:
                setattr(
                    desired,
                    field_name,
                    list(incoming_values[field_name])
                    if field_name == 'tag'
                    else incoming_values[field_name],
                )
        return HoldingTarget.from_holdings(
            base=base,
            target=desired,
            owned_fields=owned,
        )

    def upsert_holding(self, holding: Holding) -> Holding:
        """Compatibility additive upsert with fresh base and fresh proof."""
        canonical = canonical_holding(holding)
        identity = HoldingIdentity.from_holding(canonical)
        fresh = self._read_fresh_holding_account_slice(identity.account)
        base = self._find_holding_identity(fresh, identity)
        if base is None:
            owned = set(HOLDING_REQUIRED_VALUE_FIELDS)
            for optional_field in ('avg_cost', 'asset_class', 'industry'):
                if getattr(canonical, optional_field) is not None:
                    owned.add(optional_field)
            if canonical.tag:
                owned.add('tag')
            return self._apply_holding_target(HoldingTarget.from_holdings(
                base=None,
                target=canonical,
                owned_fields=owned,
            ))
        is_cash_like = base.asset_type in {AssetType.CASH, AssetType.MMF}
        quantity = (
            self._quantize_money(base.quantity + canonical.quantity)
            if is_cash_like
            else base.quantity + canonical.quantity
        )
        desired = canonical_holding(base)
        desired.quantity = quantity
        owned = {'quantity'}
        if canonical.asset_name and canonical.asset_name != base.asset_name:
            desired.asset_name = canonical.asset_name
            owned.add('asset_name')
        return self._apply_holding_target(HoldingTarget.from_holdings(
            base=base,
            target=desired,
            owned_fields=owned,
        ))

    def replace_holding(self, target: HoldingTarget | Holding) -> Holding:
        """Replace an absolute target; Holding is a narrow compatibility input."""
        mutation = (
            target
            if isinstance(target, HoldingTarget)
            else self._legacy_replace_target(target)
            if isinstance(target, Holding)
            else None
        )
        if mutation is None:
            raise TypeError("replace_holding requires HoldingTarget or Holding")
        return self._apply_holding_target(mutation)

    def upsert_holdings_bulk(self, holdings: List[Holding], mode: str = 'additive') -> Dict[str, Any]:
        """Plan one mutation per identity, batch it, then prove full account slices."""
        from ...time_utils import bj_now_naive

        if mode not in ('additive', 'replace'):
            raise ValueError(f"unsupported mode={mode}, expected 'additive' or 'replace'")
        if not holdings:
            return {'mode': mode, 'updated': 0, 'created': 0, 'preloaded_accounts': []}

        incoming_rows = [
            (item, canonical_holding(item))
            for item in holdings
        ]
        accounts = sorted({item.account for _, item in incoming_rows})
        try:
            bases_by_account = {
                account: self._read_fresh_holding_account_slice(account)
                for account in accounts
            }
        except Exception:
            for account in accounts:
                self._invalidate_holding_account_cache(
                    account,
                    flush_persistent=True,
                )
            raise
        original_by_identity: Dict[HoldingIdentity, Holding] = {}
        working_by_identity: Dict[HoldingIdentity, Holding] = {}
        owned_by_identity: Dict[HoldingIdentity, set[str]] = {}
        for account, rows in bases_by_account.items():
            for row in rows:
                identity = HoldingIdentity.from_holding(row)
                original_by_identity[identity] = canonical_holding(row)
                working_by_identity[identity] = canonical_holding(row)

        for original_input, incoming in incoming_rows:
            identity = HoldingIdentity.from_holding(incoming)
            working = working_by_identity.get(identity)
            if working is None:
                working_by_identity[identity] = canonical_holding(incoming)
                owned = set(HOLDING_REQUIRED_VALUE_FIELDS)
                for optional_field in ('avg_cost', 'asset_class', 'industry'):
                    if getattr(incoming, optional_field) is not None:
                        owned.add(optional_field)
                if incoming.tag:
                    owned.add('tag')
                owned_by_identity[identity] = owned
                continue

            explicit = explicit_holding_owned_fields(original_input)
            owned = owned_by_identity.setdefault(identity, set())
            if mode == 'replace':
                if 'quantity' in explicit:
                    working.quantity = incoming.quantity
                    owned.add('quantity')
                if 'avg_cost' in explicit:
                    working.avg_cost = incoming.avg_cost
                    owned.add('avg_cost')
            else:
                if 'quantity' in explicit:
                    is_cash_like = working.asset_type in {AssetType.CASH, AssetType.MMF}
                    working.quantity = (
                        self._quantize_money(working.quantity + incoming.quantity)
                        if is_cash_like
                        else working.quantity + incoming.quantity
                    )
                    owned.add('quantity')
            if (
                'asset_name' in explicit
                and incoming.asset_name
                and incoming.asset_name != working.asset_name
            ):
                working.asset_name = incoming.asset_name
                owned.add('asset_name')

        targets: List[HoldingTarget] = []
        for identity, desired in working_by_identity.items():
            if not owned_by_identity.get(identity):
                continue
            targets.append(HoldingTarget.from_holdings(
                base=original_by_identity.get(identity),
                target=desired,
                owned_fields=owned_by_identity[identity],
            ))
        targets.sort(key=lambda item: item.identity)

        # Bind the batch to a second fresh base check immediately before the
        # first transport mutation.  No cache participates in this decision.
        try:
            for account in accounts:
                latest = self._read_fresh_holding_account_slice(account)
                for target in [
                    item
                    for item in targets
                    if item.identity.account == account
                ]:
                    current = self._find_holding_identity(
                        latest,
                        target.identity,
                    )
                    if target.base_record_id is None:
                        if current is not None:
                            raise HoldingMutationConflictError(
                                "holding bulk create base changed: "
                                f"{target.identity}"
                            )
                    elif (
                        current is None
                        or current.record_id != target.base_record_id
                        or holding_state_digest(current) != target.base_digest
                    ):
                        raise HoldingMutationConflictError(
                            f"holding bulk base changed: {target.identity}"
                        )
        except Exception:
            for account in accounts:
                self._invalidate_holding_account_cache(
                    account,
                    flush_persistent=True,
                )
            raise

        now_text = format_holding_date(bj_now_naive())
        update_targets = [item for item in targets if item.base_record_id is not None]
        create_targets = [item for item in targets if item.base_record_id is None]
        update_payloads = [
            {
                'record_id': target.base_record_id,
                'fields': self._to_feishu_fields(
                    self._wire_mutation_values({
                        **{
                            field_name: target.values[field_name]
                            for field_name in target.owned_fields
                        },
                        'updated_at': now_text,
                    }),
                    'holdings',
                    preserve_none=True,
                ),
            }
            for target in update_targets
        ]
        create_payloads = [
            {
                'fields': self._to_feishu_fields(
                    self._wire_mutation_values({
                        **target.identity.as_dict(),
                        **{
                            field_name: target.values[field_name]
                            for field_name in target.owned_fields
                            if target.values[field_name] is not None
                        },
                        'created_at': now_text,
                        'updated_at': now_text,
                    }),
                    'holdings',
                )
            }
            for target in create_targets
        ]
        updated_records: List[Dict[str, Any]] = []
        created_records: List[Dict[str, Any]] = []
        try:
            if update_payloads:
                updated_records = self.client.batch_update_records(
                    'holdings',
                    update_payloads,
                )
            if create_payloads:
                created_records = self.client.batch_create_records(
                    'holdings',
                    create_payloads,
                )
        except Exception:
            for account in accounts:
                self._invalidate_holding_account_cache(
                    account,
                    flush_persistent=True,
                )
            raise
        if len(updated_records) != len(update_targets) or len(created_records) != len(create_targets):
            for account in accounts:
                self._invalidate_holding_account_cache(
                    account,
                    flush_persistent=True,
                )
            raise RuntimeError("holdings batch response count mismatch")

        expected_record_ids: Dict[HoldingIdentity, str] = {
            target.identity: str(target.base_record_id)
            for target in update_targets
        }
        for target, record in zip(create_targets, created_records):
            record_id = str(record.get('record_id') or '').strip()
            if not record_id:
                for account in accounts:
                    self._invalidate_holding_account_cache(
                        account,
                        flush_persistent=True,
                    )
                raise RuntimeError("holding batch create response lacks record_id")
            expected_record_ids[target.identity] = record_id
        for account in accounts:
            self._prove_holding_targets_and_publish(
                account,
                [
                    (target, expected_record_ids[target.identity])
                    for target in targets
                    if target.identity.account == account
                ],
            )
        return {
            'mode': mode,
            'updated': len(updated_records),
            'created': len(created_records),
            'preloaded_accounts': accounts,
        }

    def update_holding_quantity(
        self,
        asset_id: str,
        account: str,
        quantity_change: float,
        broker: str,
    ) -> Holding:
        """Update one exact identity through a fresh-base HoldingPatch."""
        identity = HoldingIdentity(asset_id, account, broker)
        fresh = self._read_fresh_holding_account_slice(identity.account)
        holding = self._find_holding_identity(fresh, identity)
        if not holding or not holding.record_id:
            self._publish_holding_account_slice(identity.account, fresh)
            raise ValueError(f"holding not found: {identity}")
        change = self._strict_holding_number(
            quantity_change,
            field_name='quantity_change',
        )
        is_cash_like = holding.asset_type in {AssetType.CASH, AssetType.MMF}
        new_quantity = (
            self._quantize_money(holding.quantity + change)
            if is_cash_like
            else holding.quantity + change
        )
        if new_quantity < -1e-8:
            raise ValueError(
                "holding quantity would become negative: "
                f"asset_id={identity.asset_id}, current={holding.quantity}, change={change}"
            )
        if abs(new_quantity) <= 1e-8:
            new_quantity = 0.0
        return self.apply_holding_patch(HoldingPatch.from_base(
            holding,
            quantity=new_quantity,
        ))

    def delete_holding_if_zero(
        self,
        asset_id: str,
        account: str,
        broker: str,
    ) -> bool:
        """Delete an exact zero identity and prove absence with a fresh slice."""
        identity = HoldingIdentity(asset_id, account, broker)
        fresh = self._read_fresh_holding_account_slice(identity.account)
        holding = self._find_holding_identity(fresh, identity)
        if holding is None or abs(holding.quantity) > 1e-8:
            self._publish_holding_account_slice(identity.account, fresh)
            return False
        if not holding.record_id:
            raise RuntimeError(f"holding delete target lacks record_id: {identity}")
        target = HoldingTarget.from_holdings(
            base=holding,
            target=holding,
            owned_fields={'quantity'},
        )
        return self.delete_holding_target_if_zero(target)

    def delete_holding_target_if_zero(self, target: HoldingTarget) -> bool:
        """Delete only the zero row bound to a canonical fresh-base target."""

        if not isinstance(target, HoldingTarget):
            raise TypeError("delete_holding_target_if_zero requires HoldingTarget")
        if target.base_record_id is None:
            raise ValueError("holding zero-delete target requires an existing base")
        if abs(float(target.values['quantity'])) > 1e-8:
            raise ValueError("holding zero-delete target quantity must be zero")
        _fresh, holding = self._fresh_base_for_target(target)
        if holding is None:
            raise HoldingMutationConflictError(
                f"holding zero-delete base disappeared: {target.identity}"
            )
        if abs(holding.quantity) > 1e-8:
            raise HoldingMutationConflictError(
                f"holding zero-delete base is not zero: {target.identity}"
            )
        try:
            confirmed = self.client.delete_record(
                'holdings',
                target.base_record_id,
            )
        except Exception:
            self._invalidate_holding_account_cache(
                target.identity.account,
                flush_persistent=True,
            )
            raise
        if not confirmed:
            self._invalidate_holding_account_cache(
                target.identity.account,
                flush_persistent=True,
            )
            raise RuntimeError(
                "Feishu holding delete was not confirmed: "
                f"record_id={target.base_record_id}"
            )
        self._prove_holding_deleted_and_publish(target.identity)
        return True

    def delete_holding_by_record_id(self, record_id: str) -> bool:
        """Compatibility delete that derives and verifies the complete identity."""
        resolved_record_id = str(record_id or '').strip()
        if not resolved_record_id:
            raise ValueError("record_id is required")
        raw = self.get_raw_holdings(record_id=resolved_record_id)
        if len(raw) != 1:
            raise RuntimeError(
                f"holding delete lookup did not return one record: {resolved_record_id}"
            )
        holding = self._convert_raw_holdings(raw)[0]
        identity = HoldingIdentity.from_holding(holding)
        try:
            fresh = self._read_fresh_holding_account_slice(identity.account)
            current = self._find_holding_identity(fresh, identity)
            if current is None or current.record_id != resolved_record_id:
                raise HoldingMutationConflictError(
                    f"holding delete base changed: {identity}"
                )
        except Exception:
            self._invalidate_holding_account_cache(
                identity.account,
                flush_persistent=True,
            )
            raise
        try:
            confirmed = self.client.delete_record('holdings', resolved_record_id)
        except Exception:
            self._invalidate_holding_account_cache(
                identity.account,
                flush_persistent=True,
            )
            raise
        if not confirmed:
            self._invalidate_holding_account_cache(
                identity.account,
                flush_persistent=True,
            )
            raise RuntimeError(
                f"Feishu holding delete was not confirmed: record_id={resolved_record_id}"
            )
        self._prove_holding_deleted_and_publish(identity)
        return True

    def _holding_to_dict(self, holding: Holding) -> Dict:
        """Holding 转字典"""
        holding = canonical_holding(holding)
        result = {
            'asset_id': holding.asset_id,
            'asset_name': holding.asset_name,
            'asset_type': holding.asset_type,
            'broker': holding.broker,
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

    def _dict_to_holding(self, data: Dict) -> Holding:
        """Convert already validated fields without manufacturing defaults."""
        required_text = (
            'asset_id',
            'asset_name',
            'asset_type',
            'account',
            'broker',
            'currency',
        )
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
            asset_name=str(data['asset_name']).strip(),
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
