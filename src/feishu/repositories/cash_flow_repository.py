"""Repository for the Feishu cash_flow table."""
import logging
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, List, Optional

from ...domain.cash_flow_contracts import (
    CASH_FLOW_MONEY_QUANT,
    CashFlowManualDatasetAudit,
    CompletedCashFlowFacts,
    ManualCashFlowFacts,
    RawCashFlowRecord,
    cash_flow_generated_fingerprint,
    normalize_cash_flow_rate_source,
)
from ...models import CashFlow, DATETIME_FORMAT
from ...process_lock import process_lock
from ..contracts import get_table_contract


class CashFlowRepository:
    """Cash flow table operations + aggregation cache."""

    def __init__(self, storage):
        self.storage = storage

    def __getattr__(self, name: str):
        return getattr(self.storage, name)

    CASH_FLOW_PROJECTION_FIELDS: List[str] = list(
        get_table_contract("cash_flow").fields_by_name
    )
    CASH_FLOW_RECONCILE_FIELDS: List[str] = list(CASH_FLOW_PROJECTION_FIELDS)

    def add_cash_flow(self, facts: CompletedCashFlowFacts) -> CashFlow:
        """Add one cash-flow row with same-host atomic content deduplication."""
        if not isinstance(facts, CompletedCashFlowFacts):
            raise TypeError("add_cash_flow requires CompletedCashFlowFacts")
        facts = CompletedCashFlowFacts.require(RawCashFlowRecord(
            record_id=facts.record_id,
            raw_fields={
                'flow_date': facts.flow_date,
                'account': facts.account,
                'broker': facts.broker,
                'amount': facts.amount,
                'currency': facts.currency,
                'flow_type': facts.flow_type,
                'cny_amount': facts.cny_amount,
                'dedup_key': facts.dedup_key,
                'exchange_rate': facts.exchange_rate,
                'source': facts.source,
                'remark': facts.remark,
                'updated_at': facts.updated_at,
            },
            source='application-write',
        ))

        lock_key = f"cash_flow:{facts.account}:{facts.dedup_key}"
        with process_lock(lock_key):
            dedup_cache_key = f"cash_flow:{facts.dedup_key}"
            cached_record_id = self._dedup_key_cache.get(dedup_cache_key)
            existing_record_id = self._find_by_dedup_key(
                'cash_flow',
                facts.dedup_key,
            )
            if existing_record_id:
                existing_facts = self._matching_cash_flow_replay(
                    existing_record_id,
                    expected_dedup_key=facts.dedup_key,
                )
                if existing_facts is None:
                    if cached_record_id != existing_record_id:
                        raise RuntimeError(
                            "cash_flow dedup lookup returned a mismatched record: "
                            f"record_id={existing_record_id}, "
                            f"expected_dedup_key={facts.dedup_key}"
                        )
                    self._dedup_key_cache.pop(dedup_cache_key, None)
                    existing_record_id = self._find_by_dedup_key(
                        'cash_flow',
                        facts.dedup_key,
                    )
                    if existing_record_id:
                        existing_facts = self._matching_cash_flow_replay(
                            existing_record_id,
                            expected_dedup_key=facts.dedup_key,
                        )
                        if existing_facts is None:
                            raise RuntimeError(
                                "cash_flow fresh dedup lookup returned a mismatched record: "
                                f"record_id={existing_record_id}, "
                                f"expected_dedup_key={facts.dedup_key}"
                            )
                if existing_facts is not None:
                    logging.getLogger(__name__).info(
                        "[防重保护] 发现相同内容出入金"
                        f"(dedup_key={facts.dedup_key})，跳过创建"
                    )
                    return existing_facts.with_record_id(
                        existing_record_id,
                        replayed=True,
                    ).to_cash_flow()

            fields = facts.to_fields()
            feishu_fields = self._to_feishu_fields(fields, 'cash_flow')
            try:
                result = self.client.create_record('cash_flow', feishu_fields)
            except Exception as exc:
                if self._is_missing_field_error(exc):
                    raise ValueError("Feishu cash_flow 表缺少 dedup_key 等防重字段，已拒绝降级写入；请先补齐表字段") from exc
                raise
            record_id = str(result.get('record_id') or '').strip()
            if not record_id:
                raise RuntimeError("cash_flow create returned no record_id")
            created = facts.with_record_id(record_id)
            self._dedup_key_cache[f"cash_flow:{facts.dedup_key}"] = record_id

            if facts.account in self._cash_flow_agg_loaded_accounts:
                self._append_completed_cash_flow_cache(
                    facts,
                    record_id=record_id,
                )
            else:
                # A disk cache may exist even when this process has not loaded
                # the account. It cannot be incremented safely without knowing
                # whether it is complete, so force the next reader to refresh.
                self._invalidate_cash_flow_agg_cache({facts.account})

            return created.to_cash_flow()

    def _matching_cash_flow_replay(
        self,
        record_id: str,
        *,
        expected_dedup_key: str,
    ) -> Optional[CompletedCashFlowFacts]:
        """Return a completed replay only when the exact row still owns the key."""

        existing = self.get_cash_flow(record_id)
        if existing is None or existing.dedup_key != expected_dedup_key:
            return None
        return CompletedCashFlowFacts.require(
            RawCashFlowRecord.from_cash_flow(existing)
        )

    def _append_completed_cash_flow_cache(
        self,
        facts: CompletedCashFlowFacts,
        *,
        record_id: str,
    ) -> None:
        """Publish one validated row using the same Decimal aggregate rules."""

        from ...time_utils import bj_now_naive

        account = facts.account
        base = self._cash_flow_agg_mem_cache.get(account)
        if not isinstance(base, dict) or 'cumulative' not in base:
            base = self._local_cash_flow_agg_cache.get_account(account)
        if not isinstance(base, dict) or 'cumulative' not in base:
            self._invalidate_cash_flow_agg_cache({account})
            return

        payload = dict(base)
        daily = dict(payload.get('daily') or {})
        monthly = dict(payload.get('monthly') or {})
        yearly = dict(payload.get('yearly') or {})
        ds = facts.flow_date.strftime('%Y-%m-%d')
        ym = facts.flow_date.strftime('%Y-%m')
        yy = facts.flow_date.strftime('%Y')

        def add_amount(current: Any) -> float:
            current_dec = Decimal(str(current))
            if not current_dec.is_finite():
                raise ValueError(
                    "cash_flow aggregate cache contains a non-finite value"
                )
            return float(
                (current_dec + facts.cny_amount).quantize(
                    CASH_FLOW_MONEY_QUANT,
                    rounding=ROUND_HALF_UP,
                )
            )

        try:
            daily[ds] = add_amount(daily.get(ds, 0))
            monthly[ym] = add_amount(monthly.get(ym, 0))
            yearly[yy] = add_amount(yearly.get(yy, 0))
            cumulative = add_amount(payload.get('cumulative'))
            flow_count = int(payload.get('flow_count', 0) or 0) + 1
        except (ArithmeticError, TypeError, ValueError):
            self._invalidate_cash_flow_agg_cache({account})
            return

        updated_at = bj_now_naive().strftime(DATETIME_FORMAT)
        new_row = {
            'date': ds,
            'record_id': record_id,
            'cny_amount': float(facts.cny_amount),
            'updated_at': updated_at,
        }
        flows = [
            dict(row)
            for row in (payload.get('flows') or [])
            if isinstance(row, dict)
        ]
        flows.append(new_row)
        flows.sort(key=lambda row: (
            str(row.get('date') or ''),
            str(row.get('record_id') or ''),
        ))
        last_candidates = list(flows)
        if isinstance(payload.get('last_record'), dict):
            last_candidates.append(dict(payload['last_record']))
        last_record = max(
            last_candidates,
            key=lambda row: (
                str(row.get('date') or ''),
                str(row.get('record_id') or ''),
            ),
        )

        payload.update({
            'account': account,
            'daily': daily,
            'monthly': monthly,
            'yearly': yearly,
            'cumulative': cumulative,
            'flow_count': flow_count,
            'flows': flows,
            'last_record': dict(last_record),
            'latest_updated_at': last_record.get('updated_at'),
        })
        self._local_cash_flow_agg_cache.set_account(account, payload)
        self._cash_flow_agg_mem_cache[account] = (
            self._local_cash_flow_agg_cache.get_account(account)
        )

    def get_cash_flow(self, record_id: str) -> Optional[CashFlow]:
        """获取单条出入金记录，保留远端缺失状态和实际 dedup_key。"""
        records = self.get_raw_cash_flows(record_id=record_id)
        if not records:
            return None
        raw = records[0]
        return self._dict_to_cash_flow({
            **raw.canonical_fields(),
            'record_id': raw.record_id,
        })

    def get_raw_cash_flows(
        self,
        *,
        account: Optional[str] = None,
        record_id: Optional[str] = None,
    ) -> List[RawCashFlowRecord]:
        """Read complete, untyped rows without applying model defaults."""

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
            record = self._read_record('cash_flow', requested_record_id)
            if record is None:
                return []
            records = [record]
        else:
            filter_str = (
                f'CurrentValue.[account] = "{self._escape_filter_value(requested_account)}"'
                if requested_account is not None
                else None
            )
            records = self._list_cash_flow_records(filter_str=filter_str)

        raw_records: List[RawCashFlowRecord] = []
        for record in records:
            resolved_record_id = str((record or {}).get('record_id') or '').strip()
            fields = (record or {}).get('fields')
            if not resolved_record_id or not isinstance(fields, dict):
                raise RuntimeError("cash_flow source returned an incomplete record")
            if requested_record_id is not None and resolved_record_id != requested_record_id:
                raise RuntimeError(
                    "cash_flow source returned a different record: "
                    f"requested={requested_record_id}, returned={resolved_record_id}"
                )
            if requested_account is not None:
                returned_account = str(fields.get('account') or '').strip()
                if returned_account != requested_account:
                    raise RuntimeError(
                        "cash_flow source returned a record outside the requested account: "
                        f"record_id={resolved_record_id}, requested={requested_account}, "
                        f"returned={returned_account or '<missing>'}"
                    )
            raw_records.append(RawCashFlowRecord(
                record_id=resolved_record_id,
                raw_fields=dict(fields),
                source='feishu',
                fetched_at=fetched_at,
            ))
        return raw_records

    def _list_cash_flow_records(
        self,
        *,
        filter_str: Optional[str],
    ) -> List[Dict[str, Any]]:
        """Request the registry projection, tolerating only absent optional updated_at."""

        try:
            return self.client.list_records(
                'cash_flow',
                filter_str=filter_str,
                field_names=self.CASH_FLOW_PROJECTION_FIELDS,
            )
        except Exception as exc:
            if 'FieldNameNotFound' not in str(exc):
                raise
            fallback_fields = [
                field_name
                for field_name in self.CASH_FLOW_PROJECTION_FIELDS
                if field_name != 'updated_at'
            ]
            return self.client.list_records(
                'cash_flow',
                filter_str=filter_str,
                field_names=fallback_fields,
            )

    def preload_cash_flow_aggs(self, account: str, force_refresh: bool = False) -> Dict[str, Any]:
        """预加载并缓存 cash_flow 月度/年度聚合。"""
        if (not force_refresh) and (account in self._cash_flow_agg_loaded_accounts):
            cached = self._cash_flow_agg_mem_cache.get(account) or {}
            return {
                'account': account,
                'loaded': int(cached.get('flow_count', 0) or 0),
                'source': 'memory',
                'invalidated': False,
            }

        cached_local = self._local_cash_flow_agg_cache.get_account(account)

        records = self.get_raw_cash_flows(account=account)

        flows: List[Dict[str, Any]] = []
        daily: Dict[str, float] = {}
        monthly: Dict[str, float] = {}
        yearly: Dict[str, float] = {}
        cumulative = Decimal('0')

        for record in records:
            facts = CompletedCashFlowFacts.require(record)
            amount_dec = facts.cny_amount
            amount_float = float(amount_dec)

            ds = facts.flow_date.strftime('%Y-%m-%d')
            ym = facts.flow_date.strftime('%Y-%m')
            yy = facts.flow_date.strftime('%Y')
            daily[ds] = float(self._to_decimal(daily.get(ds, 0.0)) + amount_dec)
            monthly[ym] = float(self._to_decimal(monthly.get(ym, 0.0)) + amount_dec)
            yearly[yy] = float(self._to_decimal(yearly.get(yy, 0.0)) + amount_dec)
            cumulative += amount_dec

            flows.append({
                'date': self._safe_date_str(facts.flow_date),
                'record_id': record.record_id,
                'cny_amount': amount_float,
                'updated_at': self._extract_updated_at_str(record.canonical_fields()),
            })

        flows.sort(key=lambda x: x.get('date') or '')
        last_record = dict(flows[-1]) if flows else None

        invalidated = False
        if cached_local:
            old_fp = {r.get('date'): (r.get('record_id'), r.get('updated_at')) for r in (cached_local.get('flows') or [])}
            new_fp = {r.get('date'): (r.get('record_id'), r.get('updated_at')) for r in flows}
            if old_fp != new_fp:
                invalidated = True

        payload = {
            'account': account,
            'daily': daily,
            'monthly': monthly,
            'yearly': yearly,
            'cumulative': float(cumulative),
            'flow_count': len(flows),
            'flows': flows,
            'last_record': last_record,
            'latest_updated_at': (last_record or {}).get('updated_at') if last_record else None,
        }

        self._cash_flow_agg_mem_cache[account] = payload
        self._cash_flow_agg_loaded_accounts.add(account)
        self._local_cash_flow_agg_cache.set_account(account, payload)

        return {
            'account': account,
            'loaded': len(flows),
            'source': 'feishu',
            'invalidated': invalidated,
        }

    def _ensure_cash_flow_aggs_loaded(self, account: str):
        if account in self._cash_flow_agg_loaded_accounts:
            return
        cached = self._local_cash_flow_agg_cache.get_account(account)
        if cached:
            self._cash_flow_agg_mem_cache[account] = cached
            self._cash_flow_agg_loaded_accounts.add(account)
            return
        self.preload_cash_flow_aggs(account)

    def get_cash_flow_aggs(self, account: str) -> Dict[str, Any]:
        self._ensure_cash_flow_aggs_loaded(account)
        return self._cash_flow_agg_mem_cache.get(account) or {}

    def get_cash_flows(self, account: Optional[str] = None,
                      start_date: Optional[date] = None,
                      end_date: Optional[date] = None) -> List[CashFlow]:
        """获取完整投影的出入金输运对象。"""
        records = self.get_raw_cash_flows(account=account)

        cash_flows = []
        for record in records:
            cf = self._dict_to_cash_flow({
                **record.canonical_fields(),
                'record_id': record.record_id,
            })
            if start_date and cf.flow_date and cf.flow_date < start_date:
                continue
            if end_date and cf.flow_date and cf.flow_date > end_date:
                continue
            cash_flows.append(cf)

        cash_flows.sort(key=lambda c: c.flow_date or date.min, reverse=True)
        return cash_flows

    def get_total_cash_flow_cny(self, account: str) -> float:
        """获取账户累计出入金总额(人民币)（优先聚合缓存）"""
        self._ensure_cash_flow_aggs_loaded(account)
        aggs = self._cash_flow_agg_mem_cache.get(account)
        if aggs and 'cumulative' in aggs:
            return float(aggs['cumulative'])

        raise RuntimeError(f"cash_flow aggregate cache is incomplete for account={account}")

    def _cash_flow_cny_amount_or_raise(self, cf: CashFlow) -> float:
        facts = CompletedCashFlowFacts.require(
            RawCashFlowRecord.from_cash_flow(cf)
        )
        return float(facts.cny_amount)

    def _cash_flow_cny_amount_from_fields(self, fields: Dict[str, Any], record_id: Optional[str]) -> float:
        facts = CompletedCashFlowFacts.require(RawCashFlowRecord(
            record_id=str(record_id or ''),
            raw_fields=dict(fields),
        ))
        return float(facts.cny_amount)

    def reconcile_cash_flows(
        self,
        account: Optional[str] = None,
        *,
        dry_run: bool = True,
        fx_rates: Optional[Dict[str, Any]] = None,
        record_id: Optional[str] = None,
        manual_exchange_rate: Optional[float] = None,
        rate_date: Optional[date] = None,
        rate_source: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Reconcile from one fresh account scan and prove writes by readback."""
        if not dry_run and fx_rates:
            raise ValueError(
                "provider FX apply requires the application confirmation workflow; "
                "repository-level apply is refused"
            )
        requested_account = str(account).strip() if account is not None else None
        requested_record_id = (
            str(record_id).strip() if record_id is not None else None
        )
        if account is not None and not requested_account:
            raise ValueError("account must not be blank")
        if record_id is not None and not requested_record_id:
            raise ValueError("record_id must not be blank")
        manual_rate, resolved_rate_date, resolved_rate_source = (
            self._validate_manual_cash_flow_fx_evidence(
                record_id=requested_record_id,
                manual_exchange_rate=manual_exchange_rate,
                rate_date=rate_date,
                rate_source=rate_source,
            )
        )

        records = self.get_raw_cash_flows(account=requested_account)
        initial = self._build_cash_flow_reconcile_plan(
            records,
            account=requested_account,
            record_id=requested_record_id,
            fx_rates=dict(fx_rates or {}),
            manual_exchange_rate=manual_rate,
            rate_date=resolved_rate_date,
            rate_source=resolved_rate_source,
        )
        if dry_run:
            return self._public_cash_flow_reconcile_result(
                initial,
                dry_run=True,
                change_count=len(initial["update_payloads"]),
                updated_count=0,
            )

        update_payloads = list(initial["update_payloads"])
        updated_count = 0
        if update_payloads:
            write_error: Optional[Exception] = None
            updated_records: List[Dict[str, Any]] = []
            try:
                updated_records = self.client.batch_update_records(
                    'cash_flow',
                    update_payloads,
                )
            except Exception as exc:
                write_error = exc
            finally:
                self._invalidate_cash_flow_agg_cache(
                    set(initial["affected_accounts"])
                )
            if write_error is not None:
                failed = self._public_cash_flow_reconcile_result(
                    initial,
                    dry_run=False,
                    change_count=len(update_payloads),
                    updated_count=0,
                )
                return {
                    **failed,
                    "success": False,
                    "reason_code": "cash_flow_batch_update_failed",
                    "error": str(write_error),
                    "partial_write_possible": True,
                    "readback_verified": False,
                }
            updated_count = len(updated_records)
            if updated_count != len(update_payloads):
                failed = self._public_cash_flow_reconcile_result(
                    initial,
                    dry_run=False,
                    change_count=len(update_payloads),
                    updated_count=updated_count,
                )
                return {
                    **failed,
                    "success": False,
                    "reason_code": "cash_flow_batch_update_count_mismatch",
                    "error": (
                        "cash_flow batch update count mismatch: "
                        f"expected={len(update_payloads)}, actual={updated_count}"
                    ),
                    "partial_write_possible": True,
                    "readback_verified": False,
                }

        try:
            readback_records = self.get_raw_cash_flows(account=requested_account)
            readback = self._build_cash_flow_reconcile_plan(
                readback_records,
                account=requested_account,
                record_id=requested_record_id,
                fx_rates={},
                manual_exchange_rate=manual_rate,
                rate_date=resolved_rate_date,
                rate_source=resolved_rate_source,
            )
        except Exception as exc:
            failed = self._public_cash_flow_reconcile_result(
                initial,
                dry_run=False,
                change_count=len(update_payloads),
                updated_count=updated_count,
            )
            return {
                **failed,
                "success": False,
                "reason_code": "cash_flow_readback_failed",
                "error": str(exc),
                "partial_write_possible": bool(updated_count),
                "readback_verified": False,
            }
        initial_rows = {
            str(row.get("record_id") or ""): row
            for row in initial["rows"]
        }
        readback_rows = {
            str(row.get("record_id") or ""): row
            for row in readback["rows"]
        }
        for expected_record_id, initial_row in initial_rows.items():
            if expected_record_id not in readback_rows:
                readback["rows"].append({
                    "record_id": expected_record_id,
                    "status": "error",
                    "reason_code": "cash_flow_readback_missing",
                    "error": "cash_flow row missing from fresh post-write readback",
                    "updates": {},
                    "readback_verified": False,
                })
                continue
            if initial_row.get("updates"):
                readback_rows[expected_record_id]["applied_updates"] = dict(
                    initial_row["updates"]
                )

        return self._public_cash_flow_reconcile_result(
            readback,
            dry_run=False,
            change_count=len(update_payloads),
            updated_count=updated_count,
        )

    def audit_cash_flow_duplicates(
        self,
        *,
        account: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Fresh read-only audit grouped only by canonical manual identity."""

        requested_account = str(account).strip() if account is not None else None
        if account is not None and not requested_account:
            raise ValueError("account must not be blank")
        records = self.get_raw_cash_flows(account=requested_account)
        audit = CashFlowManualDatasetAudit.build(records)
        invalid_rows = []
        for record in records:
            issues = audit.issues_for(record.record_id)
            if issues:
                invalid_rows.append({
                    "record_id": record.record_id,
                    "issues": [issue.as_dict() for issue in issues],
                })
        groups = [group.as_dict() for group in audit.duplicate_groups]
        return {
            "success": True,
            "read_only": True,
            "account": requested_account,
            "scanned": len(records),
            "duplicate_group_count": len(groups),
            "duplicate_record_count": sum(
                int(group["record_count"]) for group in groups
            ),
            "duplicate_groups": groups,
            "invalid_count": len(invalid_rows),
            "invalid_rows": invalid_rows,
        }

    def _build_cash_flow_reconcile_plan(
        self,
        records: List[RawCashFlowRecord],
        *,
        account: Optional[str],
        record_id: Optional[str],
        fx_rates: Dict[str, Any],
        manual_exchange_rate: Optional[Decimal],
        rate_date: Optional[date],
        rate_source: Optional[str],
    ) -> Dict[str, Any]:
        audit = CashFlowManualDatasetAudit.build(records)
        target_records = [
            record
            for record in records
            if record_id is None or record.record_id == record_id
        ]
        rows: List[Dict[str, Any]] = []
        update_payloads: List[Dict[str, Any]] = []
        affected_accounts: set[str] = set()

        for record in target_records:
            manual = audit.valid_by_record_id.get(record.record_id)
            if manual is None:
                issues = audit.issues_for(record.record_id)
                rows.append({
                    "record_id": record.record_id,
                    "status": "error",
                    "reason_code": "cash_flow_manual_validation_failed",
                    "error": "; ".join(
                        f"{issue.field}:{issue.reason_code}" for issue in issues
                    ),
                    "issues": [issue.as_dict() for issue in issues],
                    "fields": record.canonical_fields(),
                    "updates": {},
                    "readback_verified": False,
                })
                continue
            duplicate_group = audit.duplicate_by_record_id.get(record.record_id)
            if duplicate_group is not None:
                rows.append({
                    "record_id": record.record_id,
                    "account": manual.account,
                    "broker": manual.broker,
                    "flow_date": manual.flow_date.isoformat(),
                    "currency": manual.currency,
                    "amount": float(manual.amount),
                    "status": "error",
                    "reason_code": "cash_flow_expected_dedup_duplicate",
                    "error": (
                        "multiple cash_flow rows share the canonical manual identity"
                    ),
                    "duplicate_group": duplicate_group.as_dict(),
                    "updates": {},
                    "readback_verified": False,
                })
                continue
            try:
                row = self._plan_cash_flow_reconcile_row(
                    record,
                    manual=manual,
                    fx_rates=fx_rates,
                    manual_exchange_rate=(
                        manual_exchange_rate
                        if record_id == record.record_id
                        else None
                    ),
                    rate_date=rate_date,
                    rate_source=rate_source,
                )
            except (ArithmeticError, TypeError, ValueError) as exc:
                rows.append({
                    "record_id": record.record_id,
                    "account": manual.account,
                    "broker": manual.broker,
                    "flow_date": manual.flow_date.isoformat(),
                    "currency": manual.currency,
                    "amount": float(manual.amount),
                    "status": "error",
                    "reason_code": "cash_flow_reconcile_evidence_invalid",
                    "error": str(exc),
                    "updates": {},
                    "readback_verified": False,
                })
                continue
            rows.append(row)
            if row["updates"]:
                update_payloads.append({
                    "record_id": record.record_id,
                    "fields": self._to_feishu_fields(
                        row["updates"],
                        "cash_flow",
                    ),
                })
                affected_accounts.add(manual.account)

        return {
            "account": account,
            "record_id": record_id,
            "source_scanned": len(records),
            "scanned": len(target_records),
            "rows": rows,
            "update_payloads": update_payloads,
            "affected_accounts": affected_accounts,
            "duplicate_groups": [
                group.as_dict() for group in audit.duplicate_groups
            ],
        }

    def _plan_cash_flow_reconcile_row(
        self,
        record: RawCashFlowRecord,
        *,
        manual: ManualCashFlowFacts,
        fx_rates: Dict[str, Any],
        manual_exchange_rate: Optional[Decimal],
        rate_date: Optional[date],
        rate_source: Optional[str],
    ) -> Dict[str, Any]:
        fields = record.canonical_fields()
        updates: Dict[str, Any] = {}
        warnings: List[str] = []
        expected_flow_type = manual.expected_flow_type
        current_flow_type = fields.get("flow_type")
        if not isinstance(current_flow_type, str) or not current_flow_type.strip():
            updates["flow_type"] = expected_flow_type
        elif current_flow_type.strip().upper() != expected_flow_type:
            updates["flow_type"] = expected_flow_type
            warnings.append(
                f"flow_type={current_flow_type} differs from amount sign; "
                f"expected {expected_flow_type}"
            )

        fx_evidence: Optional[Dict[str, str]] = None
        if manual.currency == "CNY":
            if manual_exchange_rate is not None:
                raise ValueError("manual FX evidence is not valid for CNY cash_flow")
            exchange_rate = Decimal("1")
        elif manual_exchange_rate is not None:
            if rate_date != manual.flow_date:
                raise ValueError(
                    "exchange_rate_date must equal cash_flow flow_date: "
                    f"rate_date={rate_date}, flow_date={manual.flow_date}"
                )
            exchange_rate = manual_exchange_rate
            fx_evidence = {
                "exchange_rate_date": manual.flow_date.isoformat(),
                "exchange_rate_source": str(rate_source),
                "exchange_rate_evidence_type": "manual_supplement",
            }
        elif fields.get("exchange_rate") not in (None, ""):
            exchange_rate = self._require_positive_cash_flow_rate(
                fields.get("exchange_rate")
            )
        else:
            key = f"{manual.currency}CNY"
            evidence = fx_rates.get(key)
            if isinstance(evidence, dict):
                exchange_rate = self._require_positive_cash_flow_rate(
                    evidence.get("rate")
                )
                evidence_date = self._cash_flow_evidence_date(
                    evidence.get("date") or manual.flow_date
                )
                evidence_source = normalize_cash_flow_rate_source(
                    evidence.get("source")
                )
            elif evidence is not None:
                exchange_rate = self._require_positive_cash_flow_rate(evidence)
                evidence_date = manual.flow_date
                evidence_source = normalize_cash_flow_rate_source(
                    "injected_historical_rate"
                )
            else:
                raise ValueError(
                    "historical FX evidence required for "
                    f"{manual.currency} on {manual.flow_date.isoformat()}"
                )
            if evidence_date != manual.flow_date:
                raise ValueError(
                    "exchange_rate_date must equal cash_flow flow_date: "
                    f"rate_date={evidence_date}, flow_date={manual.flow_date}"
                )
            fx_evidence = {
                "exchange_rate_date": evidence_date.isoformat(),
                "exchange_rate_source": evidence_source,
                "exchange_rate_evidence_type": "provider",
            }
        exchange_rate = self._require_positive_cash_flow_rate(exchange_rate)

        if not self._cash_flow_decimal_matches(
            fields.get("exchange_rate"),
            exchange_rate,
        ):
            updates["exchange_rate"] = float(exchange_rate)
        expected_cny_amount = (manual.amount * exchange_rate).quantize(
            CASH_FLOW_MONEY_QUANT,
            rounding=ROUND_HALF_UP,
        )
        if not self._cash_flow_decimal_matches(
            fields.get("cny_amount"),
            expected_cny_amount,
            quant=CASH_FLOW_MONEY_QUANT,
        ):
            updates["cny_amount"] = float(expected_cny_amount)

        expected_dedup_key = manual.expected_dedup_key
        if fields.get("dedup_key") != expected_dedup_key:
            updates["dedup_key"] = expected_dedup_key
        current_source = fields.get("source")
        if not isinstance(current_source, str) or not current_source.strip():
            updates["source"] = "manual"

        completed_fields = {
            **fields,
            **updates,
            "flow_date": manual.flow_date,
            "account": manual.account,
            "broker": manual.broker,
            "amount": manual.amount,
            "currency": manual.currency,
            "flow_type": expected_flow_type,
            "exchange_rate": exchange_rate,
            "cny_amount": expected_cny_amount,
            "dedup_key": expected_dedup_key,
            "source": updates.get("source", fields.get("source")),
        }
        completed = CompletedCashFlowFacts.require(RawCashFlowRecord(
            record_id=record.record_id,
            raw_fields=completed_fields,
            source="reconcile-plan",
        ))
        fingerprint = cash_flow_generated_fingerprint(completed)
        observed = not updates
        row = {
            "record_id": record.record_id,
            "account": manual.account,
            "broker": manual.broker,
            "flow_date": manual.flow_date.isoformat(),
            "currency": manual.currency,
            "amount": float(manual.amount),
            "flow_type": expected_flow_type,
            "exchange_rate": float(exchange_rate),
            "cny_amount": float(expected_cny_amount),
            "expected_dedup_key": expected_dedup_key,
            "expected_generated_fingerprint": fingerprint,
            "generated_fingerprint": fingerprint if observed else None,
            "source_hash": fingerprint if observed else None,
            "requires_fx_confirmation": manual.currency != "CNY",
            "status": "ok" if observed else "pending",
            "completion_state": "completed" if observed else "proposed",
            "readback_verified": observed,
            "updates": updates,
        }
        if fx_evidence is not None:
            row["fx_evidence"] = fx_evidence
        if warnings:
            row["warnings"] = warnings
        return row

    @staticmethod
    def _validate_manual_cash_flow_fx_evidence(
        *,
        record_id: Optional[str],
        manual_exchange_rate: Any,
        rate_date: Optional[date],
        rate_source: Optional[str],
    ) -> tuple[Optional[Decimal], Optional[date], Optional[str]]:
        values = (manual_exchange_rate, rate_date, rate_source)
        if not any(value is not None for value in values):
            return None, None, None
        if not record_id:
            raise ValueError("manual FX evidence requires record_id")
        if any(value in (None, "") for value in values):
            raise ValueError(
                "manual FX evidence requires exchange_rate, rate_date, and rate_source"
            )
        if not isinstance(rate_date, date) or isinstance(rate_date, datetime):
            raise ValueError("manual FX evidence rate_date must be a date")
        resolved_source = normalize_cash_flow_rate_source(rate_source)
        resolved_rate = CashFlowRepository._require_positive_cash_flow_rate(
            manual_exchange_rate
        )
        return resolved_rate, rate_date, resolved_source

    @staticmethod
    def _require_positive_cash_flow_rate(raw: Any) -> Decimal:
        if isinstance(raw, bool) or raw in (None, ""):
            raise ValueError("exchange_rate must be a finite positive Decimal")
        try:
            value = Decimal(str(raw).strip())
        except (InvalidOperation, AttributeError, TypeError, ValueError) as exc:
            raise ValueError(
                "exchange_rate must be a finite positive Decimal"
            ) from exc
        if not value.is_finite() or value <= 0:
            raise ValueError("exchange_rate must be a finite positive Decimal")
        return value

    def _cash_flow_evidence_date(self, raw: Any) -> date:
        if isinstance(raw, datetime):
            return (
                raw.astimezone(self.FEISHU_DATE_TZ).date()
                if raw.tzinfo
                else raw.date()
            )
        if isinstance(raw, date):
            return raw
        if isinstance(raw, bool):
            raise ValueError("exchange_rate_date is invalid")
        if isinstance(raw, (int, float, Decimal)):
            value = Decimal(str(raw))
            if not value.is_finite():
                raise ValueError("exchange_rate_date is invalid")
            return datetime.fromtimestamp(
                float(value) / 1000,
                tz=self.FEISHU_DATE_TZ,
            ).date()
        if isinstance(raw, str):
            return date.fromisoformat(raw.strip()[:10])
        raise ValueError("exchange_rate_date is invalid")

    @staticmethod
    def _cash_flow_decimal_matches(
        raw: Any,
        expected: Decimal,
        *,
        quant: Optional[Decimal] = None,
    ) -> bool:
        if isinstance(raw, bool) or raw in (None, ""):
            return False
        try:
            actual = Decimal(str(raw).strip())
            if not actual.is_finite():
                return False
            if quant is not None:
                actual = actual.quantize(quant, rounding=ROUND_HALF_UP)
                expected = expected.quantize(quant, rounding=ROUND_HALF_UP)
            return actual == expected
        except (InvalidOperation, AttributeError, TypeError, ValueError):
            return False

    @staticmethod
    def _public_cash_flow_reconcile_result(
        plan: Dict[str, Any],
        *,
        dry_run: bool,
        change_count: int,
        updated_count: int,
    ) -> Dict[str, Any]:
        rows = list(plan["rows"])
        error_count = sum(1 for row in rows if row.get("status") == "error")
        completed_count = sum(
            1 for row in rows if row.get("completion_state") == "completed"
        )
        readback_verified = all(
            bool(row.get("readback_verified")) for row in rows
        ) and (bool(rows) or plan.get("record_id") is None)
        success = bool(dry_run or readback_verified)
        result = {
            "success": success,
            "dry_run": dry_run,
            "account": plan.get("account"),
            "record_id": plan.get("record_id"),
            "source_scanned": int(plan.get("source_scanned") or 0),
            "scanned": int(plan.get("scanned") or 0),
            "change_count": int(change_count),
            "updated_count": int(updated_count),
            "completed_count": completed_count,
            "error_count": error_count,
            "readback_verified": readback_verified,
            "partial_write_possible": bool(
                not dry_run and updated_count and not readback_verified
            ),
            "duplicate_groups": list(plan.get("duplicate_groups") or ()),
            "rows": rows,
        }
        if not success:
            result.update({
                "reason_code": "cash_flow_readback_not_verified",
                "error": (
                    "cash_flow apply did not produce a unique completed fresh readback"
                ),
            })
        return result

    def _parse_cash_flow_manual_fields(self, fields: Dict[str, Any]) -> Dict[str, Any]:
        manual, issues = ManualCashFlowFacts.validate(RawCashFlowRecord(
            record_id='',
            raw_fields=dict(fields),
            source='reconcile',
        ))
        if manual is None:
            return {
                'error': '; '.join(
                    f"{issue.field}:{issue.reason_code}" for issue in issues
                ),
                'issues': [issue.as_dict() for issue in issues],
                'fields': fields,
            }
        return {
            'manual': manual,
            'flow_date': manual.flow_date,
            'account': manual.account,
            'broker': manual.broker,
            'amount': float(manual.amount),
            'currency': manual.currency,
        }

    def _resolve_cash_flow_exchange_rate(
        self,
        *,
        currency: str,
        amount: float,
        cny_amount: Optional[float],
        rate_cache: Dict[str, float],
    ) -> float:
        if str(currency or '').strip().upper() == 'CNY':
            return 1.0
        raise ValueError(
            "legacy cash_flow FX resolver cannot prove dated evidence; "
            "use reconcile_cash_flows with flow_date-bound evidence"
        )

    def _invalidate_cash_flow_agg_cache(self, accounts: set[str]):
        for account in accounts:
            self._cash_flow_agg_loaded_accounts.discard(account)
            self._cash_flow_agg_mem_cache.pop(account, None)
            set_account = getattr(self._local_cash_flow_agg_cache, 'set_account', None)
            if callable(set_account):
                set_account(account, {}, _flush=True)

    def _cash_flow_to_dict(self, facts: CompletedCashFlowFacts) -> Dict[str, Any]:
        """Serialize only completed cash-flow facts for a write."""
        if not isinstance(facts, CompletedCashFlowFacts):
            raise TypeError("cash_flow writes require CompletedCashFlowFacts")
        return facts.to_fields()

    def _dict_to_cash_flow(self, data: Dict) -> CashFlow:
        """Convert a source row without manufacturing missing business facts."""
        flow_date = data.get('flow_date')
        if isinstance(flow_date, datetime):
            flow_date = (
                flow_date.astimezone(self.FEISHU_DATE_TZ).date()
                if flow_date.tzinfo
                else flow_date.date()
            )
        if isinstance(flow_date, (int, float)) and not isinstance(flow_date, bool):
            flow_date = datetime.fromtimestamp(flow_date / 1000, tz=self.FEISHU_DATE_TZ).date()
        elif isinstance(flow_date, str):
            candidate = flow_date.strip()
            if candidate:
                try:
                    flow_date = date.fromisoformat(candidate)
                except ValueError:
                    parsed = datetime.fromisoformat(candidate.replace('Z', '+00:00'))
                    flow_date = (
                        parsed.astimezone(self.FEISHU_DATE_TZ).date()
                        if parsed.tzinfo
                        else parsed.date()
                    )
            else:
                flow_date = None
        return CashFlow(
            record_id=data.get('record_id'),
            flow_date=flow_date,
            account=data.get('account'),
            broker=data.get('broker'),
            amount=data.get('amount'),
            currency=data.get('currency'),
            cny_amount=data.get('cny_amount'),
            exchange_rate=data.get('exchange_rate'),
            flow_type=(
                data['flow_type'].strip().upper()
                if isinstance(data.get('flow_type'), str)
                else data.get('flow_type')
            ),
            dedup_key=data.get('dedup_key'),
            source=data.get('source'),
            remark=data.get('remark'),
            updated_at=data.get('updated_at'),
        )

    def delete_cash_flow_by_record_id(self, record_id: str) -> bool:
        """通过记录ID删除出入金"""
        raw_rows = self.get_raw_cash_flows(record_id=record_id)
        old_account = None
        if raw_rows:
            observed_account = raw_rows[0].raw_fields.get('account')
            if isinstance(observed_account, str) and observed_account.strip():
                old_account = observed_account.strip()
        ok = self.client.delete_record('cash_flow', record_id)
        if ok:
            accounts = (
                {old_account}
                if old_account is not None
                else set(self._cash_flow_agg_loaded_accounts)
            )
            self._invalidate_cash_flow_agg_cache(accounts)
            for key, cached_record_id in list(self._dedup_key_cache.items()):
                if cached_record_id == record_id:
                    self._dedup_key_cache.pop(key, None)
        return ok
