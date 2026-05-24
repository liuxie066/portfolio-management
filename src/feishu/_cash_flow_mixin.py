"""Cash flow CRUD mixin for FeishuStorage."""
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from ..models import CashFlow, make_cf_dedup_key, DATETIME_FORMAT


class CashFlowMixin:
    """Cash flow table operations + aggregation cache."""

    CASH_FLOW_RECONCILE_FIELDS: List[str] = [
        'flow_date', 'account', 'amount', 'currency', 'cny_amount',
        'exchange_rate', 'flow_type', 'dedup_key', 'source', 'remark',
        'updated_at',
    ]

    def add_cash_flow(self, cf: CashFlow) -> CashFlow:
        """添加出入金记录（自动防重）"""
        if not cf.dedup_key:
            cf.dedup_key = make_cf_dedup_key(cf)

        if cf.dedup_key:
            existing = self._find_by_dedup_key('cash_flow', cf.dedup_key)
            if existing:
                print(f"[防重保护] 发现相同内容出入金(dedup_key={cf.dedup_key})，跳过创建")
                cf.record_id = existing
                return cf

        fields = self._cash_flow_to_dict(cf)
        feishu_fields = self._to_feishu_fields(fields, 'cash_flow')

        try:
            result = self.client.create_record('cash_flow', feishu_fields)
        except Exception as e:
            if self._is_missing_field_error(e):
                raise ValueError("Feishu cash_flow 表缺少 dedup_key 等防重字段，已拒绝降级写入；请先补齐表字段") from e
            raise
        cf.record_id = result['record_id']

        if cf.dedup_key:
            self._dedup_key_cache[f"cash_flow:{cf.dedup_key}"] = cf.record_id

        # 增量更新本地 cash_flow 聚合缓存
        if cf.account in self._cash_flow_agg_loaded_accounts and cf.flow_date:
            from ..time_utils import bj_now_naive
            cny_amount = cf.cny_amount if cf.cny_amount is not None else cf.amount
            self._local_cash_flow_agg_cache.append_flow(
                cf.account,
                cf.flow_date,
                float(cny_amount or 0.0),
                cf.record_id,
                bj_now_naive().strftime(DATETIME_FORMAT),
            )
            self._cash_flow_agg_mem_cache[cf.account] = self._local_cash_flow_agg_cache.get_account(cf.account)

        return cf

    def get_cash_flow(self, record_id: str) -> Optional[CashFlow]:
        """获取单条出入金记录"""
        record = self._read_record('cash_flow', record_id)
        if not record:
            return None

        fields = self._from_feishu_fields(record['fields'], 'cash_flow')
        fields['record_id'] = record['record_id']
        return self._dict_to_cash_flow(fields)

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

        filter_str = f'CurrentValue.[account] = "{self._escape_filter_value(account)}"'
        try:
            records = self.client.list_records(
                'cash_flow',
                filter_str=filter_str,
                field_names=self.CASH_FLOW_PROJECTION_FIELDS,
            )
        except Exception as e:
            if 'FieldNameNotFound' in str(e):
                fallback_fields = [f for f in self.CASH_FLOW_PROJECTION_FIELDS if f != 'updated_at']
                records = self.client.list_records(
                    'cash_flow',
                    filter_str=filter_str,
                    field_names=fallback_fields,
                )
            else:
                raise

        flows: List[Dict[str, Any]] = []
        daily: Dict[str, float] = {}
        monthly: Dict[str, float] = {}
        yearly: Dict[str, float] = {}
        cumulative = Decimal('0')

        for record in records:
            fields = self._from_feishu_fields(record.get('fields') or {}, 'cash_flow')
            if not fields.get('flow_date'):
                amount = self._cash_flow_cny_amount_from_fields(fields, record.get('record_id'))
                cumulative += self._to_decimal(amount or 0)
                continue
            cf = self._dict_to_cash_flow({**fields, 'record_id': record.get('record_id')})
            amount = self._cash_flow_cny_amount_or_raise(cf)
            amount_dec = self._to_decimal(amount or 0)
            amount_float = float(amount_dec)

            ds = cf.flow_date.strftime('%Y-%m-%d')
            ym = cf.flow_date.strftime('%Y-%m')
            yy = cf.flow_date.strftime('%Y')
            daily[ds] = float(self._to_decimal(daily.get(ds, 0.0)) + amount_dec)
            monthly[ym] = float(self._to_decimal(monthly.get(ym, 0.0)) + amount_dec)
            yearly[yy] = float(self._to_decimal(yearly.get(yy, 0.0)) + amount_dec)
            cumulative += amount_dec

            flows.append({
                'date': self._safe_date_str(cf.flow_date),
                'record_id': record['record_id'],
                'cny_amount': amount_float,
                'updated_at': self._extract_updated_at_str(record.get('fields') or {}),
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

        return {'account': account, 'loaded': len(flows), 'source': 'feishu', 'invalidated': invalidated}

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
        """获取出入金记录列表（投影字段，降低 payload）。"""
        conditions = []

        if account:
            conditions.append(f'CurrentValue.[account] = "{self._escape_filter_value(account)}"')
        filter_str = ' AND '.join(conditions) if conditions else None
        try:
            records = self.client.list_records(
                'cash_flow',
                filter_str=filter_str,
                field_names=self.CASH_FLOW_PROJECTION_FIELDS,
            )
        except Exception as e:
            if 'FieldNameNotFound' in str(e):
                fallback_fields = [f for f in self.CASH_FLOW_PROJECTION_FIELDS if f != 'updated_at']
                records = self.client.list_records(
                    'cash_flow',
                    filter_str=filter_str,
                    field_names=fallback_fields,
                )
            else:
                raise

        cash_flows = []
        for record in records:
            fields = self._from_feishu_fields(record['fields'], 'cash_flow')
            fields['record_id'] = record['record_id']
            cf = self._dict_to_cash_flow(fields)
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

        # 兜底：缓存未就绪时直接查 API
        records = self.client.list_records(
            'cash_flow',
            filter_str=f'CurrentValue.[account] = "{self._escape_filter_value(account)}"'
        )

        total = Decimal('0')
        for record in records:
            fields = record['fields']
            parsed = self._from_feishu_fields(fields, 'cash_flow')
            cny_amount = self._cash_flow_cny_amount_from_fields(parsed, record.get('record_id'))
            if cny_amount is not None and cny_amount != '':
                total += self._to_decimal(cny_amount)

        return float(total)

    def _cash_flow_cny_amount_or_raise(self, cf: CashFlow) -> float:
        if cf.cny_amount is not None:
            return cf.cny_amount
        if (cf.currency or 'CNY').upper() == 'CNY':
            return cf.amount
        raise ValueError(
            f"cash_flow record {cf.record_id or '(unknown)'} currency={cf.currency} lacks cny_amount; "
            "run `pm cash-flow reconcile --apply --confirm` before NAV calculation"
        )

    def _cash_flow_cny_amount_from_fields(self, fields: Dict[str, Any], record_id: Optional[str]) -> float:
        if fields.get('cny_amount') is not None:
            return fields.get('cny_amount')
        currency = str(fields.get('currency') or 'CNY').upper()
        if currency == 'CNY':
            return fields.get('amount', 0)
        raise ValueError(
            f"cash_flow record {record_id or '(unknown)'} currency={currency} lacks cny_amount; "
            "run `pm cash-flow reconcile --apply --confirm` before NAV calculation"
        )

    def reconcile_cash_flows(
        self,
        account: Optional[str] = None,
        *,
        dry_run: bool = True,
        fx_rates: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        """Fill system-managed fields for manually entered cash_flow rows.

        Manual rows only need flow_date/account/amount/currency/remark.  This
        method derives blank system fields without recalculating populated CNY
        amounts, so historical FX decisions are not churned by a later run.
        """
        filter_str = None
        if account:
            filter_str = f'CurrentValue.[account] = "{self._escape_filter_value(account)}"'

        try:
            records = self.client.list_records(
                'cash_flow',
                filter_str=filter_str,
                field_names=self.CASH_FLOW_RECONCILE_FIELDS,
            )
        except Exception as e:
            if 'FieldNameNotFound' in str(e):
                fallback_fields = [f for f in self.CASH_FLOW_RECONCILE_FIELDS if f != 'updated_at']
                records = self.client.list_records(
                    'cash_flow',
                    filter_str=filter_str,
                    field_names=fallback_fields,
                )
            else:
                raise

        rows: List[Dict[str, Any]] = []
        update_payloads: List[Dict[str, Any]] = []
        affected_accounts: set[str] = set()
        rate_cache = dict(fx_rates or {})

        for record in records:
            record_id = record.get('record_id')
            raw_fields = record.get('fields') or {}
            fields = self._from_feishu_fields(raw_fields, 'cash_flow')
            parsed = self._parse_cash_flow_manual_fields(fields)
            if parsed.get('error'):
                rows.append({
                    'record_id': record_id,
                    'status': 'error',
                    'error': parsed['error'],
                    'fields': parsed.get('fields', {}),
                })
                continue

            flow_date = parsed['flow_date']
            row_account = parsed['account']
            amount = parsed['amount']
            currency = parsed['currency']
            cny_amount = fields.get('cny_amount')
            exchange_rate = fields.get('exchange_rate')
            expected_flow_type = 'DEPOSIT' if amount >= 0 else 'WITHDRAW'
            updates: Dict[str, Any] = {}
            warnings: List[str] = []

            current_flow_type = fields.get('flow_type')
            if not current_flow_type:
                updates['flow_type'] = expected_flow_type
            elif str(current_flow_type).upper() != expected_flow_type:
                updates['flow_type'] = expected_flow_type
                warnings.append(
                    f"flow_type={current_flow_type} differs from amount sign; expected {expected_flow_type}"
            )

            try:
                if currency == 'CNY' and (exchange_rate is None or float(exchange_rate) != 1.0):
                    exchange_rate = 1.0
                    updates['exchange_rate'] = exchange_rate
                elif exchange_rate is None:
                    exchange_rate = self._resolve_cash_flow_exchange_rate(
                        currency=currency,
                        amount=amount,
                        cny_amount=cny_amount,
                        rate_cache=rate_cache,
                    )
                    updates['exchange_rate'] = exchange_rate

                expected_cny_amount = self._quantize_money(Decimal(str(amount)) * Decimal(str(exchange_rate)))
                if cny_amount is None or self._quantize_money(cny_amount) != expected_cny_amount:
                    cny_amount = expected_cny_amount
                    updates['cny_amount'] = cny_amount
            except Exception as exc:
                rows.append({
                    'record_id': record_id,
                    'account': row_account,
                    'flow_date': flow_date.strftime('%Y-%m-%d'),
                    'currency': currency,
                    'amount': amount,
                    'status': 'error',
                    'error': str(exc),
                })
                continue

            cf = CashFlow(
                flow_date=flow_date,
                account=row_account,
                amount=amount,
                currency=currency,
                cny_amount=cny_amount,
                exchange_rate=exchange_rate,
                flow_type=expected_flow_type,
                source=fields.get('source'),
                remark=fields.get('remark'),
            )
            expected_dedup_key = make_cf_dedup_key(cf)
            if fields.get('dedup_key') != expected_dedup_key:
                updates['dedup_key'] = expected_dedup_key

            if not fields.get('source'):
                updates['source'] = 'manual'

            row = {
                'record_id': record_id,
                'account': row_account,
                'flow_date': flow_date.strftime('%Y-%m-%d'),
                'currency': currency,
                'amount': amount,
                'status': 'pending' if updates else 'ok',
                'updates': updates,
            }
            if warnings:
                row['warnings'] = warnings
            rows.append(row)

            if updates:
                update_payloads.append({
                    'record_id': record_id,
                    'fields': self._to_feishu_fields(updates, 'cash_flow'),
                })
                affected_accounts.add(row_account)

        updated_count = 0
        if not dry_run and update_payloads:
            self.client.batch_update_records('cash_flow', update_payloads)
            updated_count = len(update_payloads)
            self._invalidate_cash_flow_agg_cache(affected_accounts)

        return {
            'success': True,
            'dry_run': dry_run,
            'account': account,
            'scanned': len(records),
            'change_count': len(update_payloads),
            'updated_count': updated_count,
            'error_count': sum(1 for row in rows if row.get('status') == 'error'),
            'rows': rows,
        }

    def _parse_cash_flow_manual_fields(self, fields: Dict[str, Any]) -> Dict[str, Any]:
        required = ('flow_date', 'account', 'amount', 'currency')
        missing = [name for name in required if fields.get(name) in (None, '')]
        if missing:
            return {'error': f"missing manual fields: {', '.join(missing)}", 'fields': fields}

        raw_date = fields.get('flow_date')
        try:
            if isinstance(raw_date, date) and not isinstance(raw_date, datetime):
                flow_date = raw_date
            elif isinstance(raw_date, (int, float)):
                flow_date = datetime.fromtimestamp(raw_date / 1000, tz=self.FEISHU_DATE_TZ).date()
            elif isinstance(raw_date, str):
                flow_date = datetime.strptime(raw_date[:10], '%Y-%m-%d').date()
            else:
                raise ValueError(f"unsupported flow_date={raw_date!r}")
        except (TypeError, ValueError) as exc:
            return {'error': f"invalid flow_date: {exc}", 'fields': fields}

        try:
            amount = float(fields.get('amount'))
        except (TypeError, ValueError) as exc:
            return {'error': f"invalid amount: {exc}", 'fields': fields}

        return {
            'flow_date': flow_date,
            'account': str(fields.get('account')),
            'amount': amount,
            'currency': str(fields.get('currency') or 'CNY').upper(),
        }

    def _resolve_cash_flow_exchange_rate(
        self,
        *,
        currency: str,
        amount: float,
        cny_amount: Optional[float],
        rate_cache: Dict[str, float],
    ) -> float:
        if currency == 'CNY':
            return 1.0

        if cny_amount is not None and amount != 0:
            return float(Decimal(str(cny_amount)) / Decimal(str(amount)))

        key = f'{currency}CNY'
        if key not in rate_cache:
            from requests import Session
            from ..pricing.fx import FxRateService

            rate_cache.update(FxRateService(Session()).fetch_exchange_rates())

        rate = rate_cache.get(key)
        if rate is None:
            raise ValueError(f"unsupported cash_flow currency without FX rate: {currency}")
        return float(rate)

    def _invalidate_cash_flow_agg_cache(self, accounts: set[str]):
        for account in accounts:
            self._cash_flow_agg_loaded_accounts.discard(account)
            self._cash_flow_agg_mem_cache.pop(account, None)
            set_account = getattr(self._local_cash_flow_agg_cache, 'set_account', None)
            if callable(set_account):
                set_account(account, {}, _flush=True)

    def _cash_flow_to_dict(self, cf: CashFlow) -> Dict:
        """CashFlow 转字典"""
        flow_type = str(cf.flow_type).upper() if cf.flow_type is not None else None
        result = {
            'flow_date': cf.flow_date,
            'account': cf.account,
            'amount': cf.amount,
            'currency': cf.currency,
            'cny_amount': cf.cny_amount,
            'exchange_rate': cf.exchange_rate,
            'flow_type': flow_type,
            'source': cf.source,
            'remark': cf.remark,
        }
        if cf.dedup_key:
            result['dedup_key'] = cf.dedup_key
        return result

    def _dict_to_cash_flow(self, data: Dict) -> CashFlow:
        """字典转 CashFlow"""
        flow_date = data.get('flow_date')
        if isinstance(flow_date, (int, float)):
            flow_date = datetime.fromtimestamp(flow_date / 1000, tz=self.FEISHU_DATE_TZ).date()
        elif isinstance(flow_date, str):
            flow_date = datetime.strptime(flow_date, '%Y-%m-%d').date()

        return CashFlow(
            record_id=data.get('record_id'),
            flow_date=flow_date,
            account=data.get('account', ''),
            amount=float(data.get('amount', 0)),
            currency=data.get('currency', 'CNY'),
            cny_amount=float(data.get('cny_amount')) if data.get('cny_amount') is not None else None,
            exchange_rate=float(data.get('exchange_rate')) if data.get('exchange_rate') is not None else None,
            flow_type=str(data.get('flow_type', 'DEPOSIT')).upper(),
            source=data.get('source'),
            remark=data.get('remark'),
        )

    def delete_cash_flow_by_record_id(self, record_id: str) -> bool:
        """通过记录ID删除出入金"""
        ok = self.client.delete_record('cash_flow', record_id)
        if ok:
            self._cash_flow_agg_loaded_accounts.clear()
            self._cash_flow_agg_mem_cache.clear()
        return ok
