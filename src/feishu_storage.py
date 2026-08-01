"""
飞书多维表存储层
作为唯一存储后端（已移除 SQLite 后端）

职责拆分为 mixin 模块（src/feishu/），本文件作为组合入口。
"""
import json
import re
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Optional, Dict, Any

from .models import (
    Holding, Transaction, CashFlow, NAVHistory, PriceCache,
    AssetType, TransactionType, AssetClass, Industry,
    make_tx_dedup_key, make_cf_dedup_key, make_request_id, DATETIME_FORMAT
)
from .snapshot_models import HoldingSnapshot
from .feishu_client import FeishuClient
from .feishu.contracts import FieldEncoding, field_names_by_encoding
from .feishu.errors import FeishuRecordNotFoundError
from .local_cache import (
    LocalPriceCache,
    LocalHoldingsIndexCache,
    LocalNavIndexCache,
    LocalCashFlowAggCache,
)
from .feishu._price_mixin import PriceMixin
from .feishu._transactions_mixin import TransactionsMixin
from .feishu._cash_flow_mixin import CashFlowMixin
from .feishu._holdings_mixin import HoldingsMixin
from .feishu._snapshots_mixin import SnapshotsMixin
from .feishu._nav_mixin import NavMixin


_LOCAL_PRICE_CACHE_NUMBER_FIELDS = frozenset({
    'price', 'cny_price', 'change', 'change_pct', 'exchange_rate',
})


def _wire_fields_by_encoding(table: str, encoding: FieldEncoding) -> frozenset[str]:
    """Resolve remote wire types from the registry; price_cache is local-only."""
    if table == 'price_cache':
        return (
            _LOCAL_PRICE_CACHE_NUMBER_FIELDS
            if encoding is FieldEncoding.NUMBER
            else frozenset()
        )
    return field_names_by_encoding(table, encoding)


class _MemoryHoldingsIndexCache:
    def __init__(self):
        self._cache: Dict[str, Dict[str, Any]] = {}

    def load_all(self) -> Dict[str, Dict[str, Any]]:
        return {k: dict(v) for k, v in self._cache.items()}

    def upsert(self, cache_key: str, payload: Dict[str, Any], *, _flush: bool = False):
        self._cache[cache_key] = dict(payload)

    def delete(self, cache_key: str, *, _flush: bool = False):
        self._cache.pop(cache_key, None)

    def flush(self):
        return None


class _MemoryAccountCache:
    def __init__(self):
        self._cache: Dict[str, Dict[str, Any]] = {}

    def get_account(self, account: str) -> Dict[str, Any]:
        return json.loads(json.dumps(self._cache.get(account) or {}))

    def set_account(self, account: str, payload: Dict[str, Any], *, _flush: bool = False):
        self._cache[account] = json.loads(json.dumps(payload))

    def upsert_nav_records(self, account: str, records: List[Dict[str, Any]], *, _flush: bool = False):
        base = dict(self._cache.get(account) or {})
        navs = list(base.get('nav_history') or [])
        by_date = {str((row or {}).get('date') or ''): dict(row) for row in navs if (row or {}).get('date')}
        for record in records:
            ds = str((record or {}).get('date') or '')
            if ds:
                by_date[ds] = dict(record)
        base['nav_history'] = sorted(by_date.values(), key=lambda row: row.get('date') or '')
        base['record_count'] = len(base['nav_history'])
        self._cache[account] = base

    def append_flow(self, account: str, flow_date: date, cny_amount: float, record_id: Optional[str], updated_at: Optional[str], *, _flush: bool = False):
        base = dict(self._cache.get(account) or {})
        daily = dict(base.get('daily') or {})
        monthly = dict(base.get('monthly') or {})
        yearly = dict(base.get('yearly') or {})
        ds = flow_date.strftime('%Y-%m-%d')
        ym = flow_date.strftime('%Y-%m')
        yy = flow_date.strftime('%Y')
        daily[ds] = float(daily.get(ds, 0.0) + (cny_amount or 0.0))
        monthly[ym] = float(monthly.get(ym, 0.0) + (cny_amount or 0.0))
        yearly[yy] = float(yearly.get(yy, 0.0) + (cny_amount or 0.0))
        base['daily'] = daily
        base['monthly'] = monthly
        base['yearly'] = yearly
        base['cumulative'] = float(base.get('cumulative', 0.0) + (cny_amount or 0.0))
        base['flow_count'] = int(base.get('flow_count', 0) or 0) + 1
        base['last_record'] = {
            'date': ds,
            'record_id': record_id,
            'updated_at': updated_at,
            'cny_amount': cny_amount,
        }
        self._cache[account] = base


class FeishuStorage(
    HoldingsMixin,
    TransactionsMixin,
    CashFlowMixin,
    SnapshotsMixin,
    NavMixin,
    PriceMixin,
):
    """飞书多维表存储层 (带内存缓存优化)"""

    FEISHU_DATE_TZ = timezone(timedelta(hours=8))
    MONEY_QUANT = Decimal('0.01')
    NAV_QUANT = Decimal('0.000001')
    WEIGHT_QUANT = Decimal('0.000001')

    def __init__(
        self,
        client: FeishuClient = None,
        local_price_cache: Optional[LocalPriceCache] = None,
        local_holdings_index_cache: Optional[LocalHoldingsIndexCache] = None,
        local_nav_index_cache: Optional[LocalNavIndexCache] = None,
        local_cash_flow_agg_cache: Optional[LocalCashFlowAggCache] = None,
    ):
        """
        初始化飞书存储层

        Args:
            client: FeishuClient 实例，如果不传则自动创建
            local_price_cache: 本地价格缓存实例（可注入用于测试）
            local_holdings_index_cache: 本地持仓索引缓存实例（可注入用于测试）
            local_nav_index_cache: 本地净值索引缓存实例（可注入用于测试）
            local_cash_flow_agg_cache: 本地现金流聚合缓存实例（可注入用于测试）
        """
        self.client = client or FeishuClient()
        use_memory_indexes = client is not None and not isinstance(client, FeishuClient)

        # 内存缓存：减少 API 调用次数
        # key: "asset_id:account:broker" -> value: record_id
        self._holding_id_cache: Dict[str, str] = {}
        # key: "asset_id:account:broker" -> value: holding fields snapshot（含 record_id）
        self._holding_fields_cache: Dict[str, Dict[str, Any]] = {}
        # 持仓索引预加载状态
        self._holdings_index_loaded_all: bool = False
        self._holdings_index_loaded_accounts: set[str] = set()

        # 防重缓存：本地 Set 预检，避免重复 API 查询
        # key: request_id/dedup_key -> value: record_id (或 True 表示已存在)
        self._request_id_cache: Dict[str, str] = {}  # transactions 表
        self._dedup_key_cache: Dict[str, str] = {}   # transactions 和 cash_flow 表

        # 本地文件价格缓存（替代飞书多维表）
        self._local_price_cache = local_price_cache or LocalPriceCache()

        # 本地持仓索引缓存（business_key -> fields）
        self._local_holdings_index_cache = local_holdings_index_cache or (
            _MemoryHoldingsIndexCache() if use_memory_indexes else LocalHoldingsIndexCache()
        )

        # 本地 NAV 索引缓存（account -> nav index + bases）
        self._local_nav_index_cache = local_nav_index_cache or (
            _MemoryAccountCache() if use_memory_indexes else LocalNavIndexCache()
        )
        self._nav_index_loaded_accounts: set[str] = set()
        self._nav_index_mem_cache: Dict[str, Dict[str, Any]] = {}

        # 本地 cash_flow 聚合缓存（account -> monthly/yearly/cumulative）
        self._local_cash_flow_agg_cache = local_cash_flow_agg_cache or (
            _MemoryAccountCache() if use_memory_indexes else LocalCashFlowAggCache()
        )
        self._cash_flow_agg_loaded_accounts: set[str] = set()
        self._cash_flow_agg_mem_cache: Dict[str, Dict[str, Any]] = {}

        self._load_persistent_holdings_index()

    # ========== 字段转换工具（所有 mixin 共用） ==========

    @staticmethod
    def _to_decimal(v: Any) -> Decimal:
        if v is None:
            return Decimal('0')
        if isinstance(v, Decimal):
            return v
        return Decimal(str(v))

    @classmethod
    def _quantize_money(cls, v: Any) -> float:
        return float(cls._to_decimal(v).quantize(cls.MONEY_QUANT, rounding=ROUND_HALF_UP))

    @classmethod
    def _quantize_nav(cls, v: Any) -> float:
        return float(cls._to_decimal(v).quantize(cls.NAV_QUANT, rounding=ROUND_HALF_UP))

    @classmethod
    def _quantize_weight(cls, v: Any) -> float:
        return float(cls._to_decimal(v).quantize(cls.WEIGHT_QUANT, rounding=ROUND_HALF_UP))

    @classmethod
    def _normalize_numeric_field(cls, table: str, key: str, value: Any) -> Any:
        if value is None:
            return None

        money_fields = {
            'holdings': {'avg_cost'},
            'transactions': {'price', 'amount', 'fee', 'tax'},
            'cash_flow': {'amount', 'cny_amount'},
            'nav_history': {
                'total_value', 'cash_value', 'stock_value', 'fund_value',
                'cn_stock_value', 'us_stock_value', 'hk_stock_value',
                'shares', 'cash_flow', 'share_change', 'pnl', 'mtd_pnl', 'ytd_pnl'
            },
            'price_cache': {'price', 'cny_price', 'change', 'change_pct', 'exchange_rate'},
        }
        nav_fields = {
            'nav_history': {'nav', 'mtd_nav_change', 'ytd_nav_change'},
        }
        weight_fields = {
            'nav_history': {'stock_weight', 'cash_weight'},
        }

        if key in money_fields.get(table, set()):
            return cls._quantize_money(value)
        if key in nav_fields.get(table, set()):
            return cls._quantize_nav(value)
        if key in weight_fields.get(table, set()):
            return cls._quantize_weight(value)
        return value

    # ========== 字段转换工具 ==========

    def _to_feishu_fields(self, data: Dict, table: str, preserve_none: bool = False) -> Dict[str, Any]:
        """
        将 Python 字典转换为飞书多维表字段格式

        飞书字段类型：
        - 文本：直接传字符串
        - 数字：直接传数字
        - 日期：传整数时间戳（毫秒）或字符串 "2025-03-12"
        - 复选框：传布尔值

        Args:
            preserve_none: 是否保留 None 值（用于 update 时显式清空字段）
        """
        result = {}

        number_fields = _wire_fields_by_encoding(table, FieldEncoding.NUMBER)
        json_text_fields = _wire_fields_by_encoding(table, FieldEncoding.JSON_TEXT)

        for key, value in data.items():
            if value is None:
                if preserve_none:
                    result[key] = None
                continue

            # asset_id 特殊处理：强制转为字符串，确保前导零不丢失
            if key == 'asset_id' and value:
                result[key] = str(value)
                continue

            # 日期转换：飞书日期字段使用 Unix 时间戳（毫秒）。
            if isinstance(value, datetime):
                result[key] = int(value.timestamp() * 1000)
            elif isinstance(value, date):
                # Interpret date as business date in FEISHU_DATE_TZ (Beijing) to avoid cross-day drift.
                dt = datetime.combine(value, datetime.min.time(), tzinfo=self.FEISHU_DATE_TZ)
                result[key] = int(dt.timestamp() * 1000)
            # 枚举转换
            elif isinstance(value, (AssetType, TransactionType, AssetClass, Industry)):
                result[key] = value.value
            # JSON 字段
            elif key in json_text_fields and isinstance(value, (list, dict)):
                result[key] = json.dumps(value, ensure_ascii=False)
            # 数字字段类型处理
            elif key in number_fields:
                result[key] = self._normalize_numeric_field(table, key, value)
            # 其他直接传
            else:
                result[key] = value

        return result

    def _from_feishu_fields(self, fields: Dict, table: str) -> Dict[str, Any]:
        """将飞书字段格式转换为 Python 字典"""
        result = {}
        number_fields = _wire_fields_by_encoding(table, FieldEncoding.NUMBER)
        json_text_fields = _wire_fields_by_encoding(table, FieldEncoding.JSON_TEXT)

        for key, value in fields.items():
            if value is None:
                result[key] = None
                continue

            # asset_id 特殊处理：强制转为字符串，保留前导零
            if key == 'asset_id' and value:
                # 飞书可能返回数字类型，需要转为字符串并保持格式
                asset_id_str = str(value)
                # 如果原值是数字类型被转为字符串且长度小于6位，可能是前导零丢失的A股/基金代码
                # 但这里无法确定原始长度，所以只能保留飞书实际存储的值
                result[key] = asset_id_str
                continue

            if key in number_fields and value != '':
                parsed = self._parse_float(value)
                result[key] = (
                    self._normalize_numeric_field(table, key, parsed)
                    if parsed is not None
                    else None
                )
                continue

            if key in json_text_fields and value:
                try:
                    result[key] = json.loads(value) if isinstance(value, str) else value
                except (json.JSONDecodeError, TypeError, ValueError):
                    result[key] = [] if table == 'holdings' and key == 'tag' else None
                continue

            result[key] = value

        return result

    # ========== 安全辅助方法 ==========

    @staticmethod
    def _parse_float(value) -> Optional[float]:
        """解析飞书返回的数字字段，支持逗号分隔符、货币符号、括号负数

        Examples:
            '3,000.00' -> 3000.0
            '¥ 50,000.00' -> 50000.0
            '¥ (209,965.97)' -> -209965.97
            1234.5 -> 1234.5
        """
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if not isinstance(value, str):
            return None
        s = value.strip()
        if not s:
            return None
        # 检测括号负数格式
        negative = bool(re.search(r'\(.*\)', s))
        # 移除货币符号、空格、括号
        s = re.sub(r'[¥$€£\s()]', '', s)
        # 移除逗号
        s = s.replace(',', '')
        if not s:
            return None
        try:
            result = float(s)
            return -result if negative else result
        except ValueError:
            return None

    @staticmethod
    def _escape_filter_value(value: str) -> str:
        r"""
        转义飞书 filter 字符串中的特殊字符，防止注入攻击

        飞书 filter 使用双引号包裹字符串值，需要转义:
        - 双引号 " -> \"
        - 反斜杠 \ -> \\
        """
        if not isinstance(value, str):
            value = str(value)
        return value.replace('\\', '\\\\').replace('"', '\\"')

    @staticmethod
    def _safe_date_str(d: Optional[date]) -> Optional[str]:
        if not d:
            return None
        return d.strftime('%Y-%m-%d')

    def _extract_updated_at_str(self, fields: Dict[str, Any]) -> Optional[str]:
        raw = fields.get('updated_at')
        if raw is None:
            return None
        if isinstance(raw, (int, float)):
            dt = datetime.fromtimestamp(raw / 1000, tz=self.FEISHU_DATE_TZ)
            return dt.replace(tzinfo=None).strftime(DATETIME_FORMAT)
        if isinstance(raw, str):
            return raw
        return None

    def _read_record(self, table_name: str, record_id: str) -> Optional[Dict[str, Any]]:
        """Read one record through the strict client API."""
        strict = getattr(self.client, 'get_record_strict', None)
        if callable(strict):
            try:
                record = strict(table_name, record_id)
                if isinstance(record, dict):
                    return record
            except FeishuRecordNotFoundError:
                return None
        return None


    # ========== compensation_tasks ==========

    def add_compensation_task(self, task) -> Any:
        """persist compensation task (optional table).

        If Feishu table is not configured, CompensationService
        falls back to local JSONL queue.
        """
        if is_dataclass(task):
            fields = asdict(task)
        elif hasattr(task, "model_dump"):
            fields = task.model_dump(mode="json")
        else:
            fields = dict(task)

        payload = dict(fields)
        if isinstance(payload.get("payload"), (dict, list)):
            payload["payload"] = json.dumps(payload["payload"], ensure_ascii=False, sort_keys=True)
        payload = {key: value for key, value in payload.items() if value is not None}
        result = self.client.create_record("compensation_tasks", payload)
        record_id = result.get("record_id")
        if isinstance(task, dict):
            task["record_id"] = record_id
        else:
            setattr(task, "record_id", record_id)
        return task
