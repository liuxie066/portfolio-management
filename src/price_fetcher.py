#!/usr/bin/env python3
"""
价格获取模块 (带缓存和交易时间优化)
整合A股(腾讯)、港股(腾讯)、美股(Finnhub/Yahoo Chart)、基金(腾讯/东方财富)价格查询

优化特性:
1. 自动缓存价格，减少API调用
2. 根据交易时间智能调整缓存有效期
3. 非交易时间延长缓存，交易时间缩短缓存
4. 美股多数据源备选，防止限流
"""
import requests
import time
import random
from typing import Dict, Optional, List, Any

from .pricing import PriceRequest, PriceService
from .pricing.fx import FxRateService
from .pricing.classifier import (
    get_type_hints_from_name,
    normalize_code_with_name,
)
from .pricing.payload import (
    MONEY_QUANT,
    PCT_QUANT,
    RATE_QUANT,
    normalize_price_payload,
    quantize_money,
    quantize_pct,
    quantize_rate,
    to_decimal,
)


class PriceFetcher:
    """统一价格获取器 (带缓存优化，支持飞书多维表)

    Harness-friendly conventions:
    - Prefer batch APIs when available (Tencent quotes) to reduce latency and failure surface.
    - Keep per-asset payloads self-describing (source/is_from_cache/is_stale/expires_at).
    - Provide scripts/diagnose_pricing.py for quick observability.
    """

    MONEY_QUANT = MONEY_QUANT
    RATE_QUANT = RATE_QUANT
    PCT_QUANT = PCT_QUANT

    # 关键词列表（用于名称辅助判断资产类型）
    STOCK_KEYWORDS = [
        '股票', '股份', '集团', '银行', '科技', '医药', '能源',
        '保险', '证券', '茅台', '格力', '美的', '五粮液', '平安',
        '招行', '兴业', '浦发', '华夏', '民生', '中信', '光大',
        '海油', '石油', '石化', '神华', '中免', '恒瑞', '药明',
        '宁德', '比亚迪', '隆基', '通威', '海康',
        '腾讯', '美团', '阿里', '京东', '百度', '小米',
    ]
    FUND_KEYWORDS = [
        'etf', '联接', '基金', '混合', '债券', '指数', 'qdii', 'fof',
        '货币', '理财', '分级', 'lof', '保本', '定增',
        '天弘', '易方达', '广发', '华夏', '汇添富', '南方', '嘉实',
        '博时', '工银', '华宝', '华安', '国泰', '招商', '鹏华',
    ]
    CASH_KEYWORDS = ['现金', '货币', 'mmf', 'cash', '余额宝']

    @staticmethod
    def _to_decimal(value):
        return to_decimal(value)

    @classmethod
    def _quantize_money(cls, value) -> float:
        return quantize_money(value)

    @classmethod
    def _quantize_rate(cls, value) -> float:
        return quantize_rate(value)

    @classmethod
    def _quantize_pct(cls, value) -> float:
        return quantize_pct(value)

    @classmethod
    def _normalize_price_payload(cls, payload: Dict) -> Dict:
        """统一价格输出口径。

        约定：
        - 所有金额字段按 MONEY_QUANT 量化
        - change_pct / exchange_rate 量化
        - 自动补充 fetched_at（北京时间 naive ISO 字符串），便于诊断“是否刷新/是否走缓存”
        """
        return normalize_price_payload(payload)

    def __init__(self, storage=None, use_cache: bool = True):
        """
        Args:
            storage: FeishuStorage 实例（可选，用于价格缓存）
            use_cache: 是否启用缓存
        """
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        })
        self.storage = storage
        self.use_cache = use_cache and storage is not None
        self.fx_service = FxRateService(self.session)
        # last-batch meta for observability
        self._last_tencent_batch_meta = None
        self.price_service = PriceService.for_price_fetcher(self)
        self._last_price_service_diagnostics = []

    def fetch(
        self,
        code: str,
        asset_name: str = None,
        force_refresh: bool = False,
        *,
        asset_type_map: Dict[str, Any] = None,
        market_closed_ttl_multiplier: float = 1.0,
        accept_stale_when_closed: bool = False,
        max_stale_after_expiry_sec: int = 0,
        use_cache_only: bool = False,
    ) -> Optional[Dict]:
        """获取单个资产价格（带缓存）。

        约定：
        - 未过期缓存：直接返回（不触发 realtime 请求）
        - 过期缓存：默认不返回；若 accept_stale_when_closed=True，则在 max_stale_after_expiry_sec 窗口内可作为 fallback
        - use_cache_only=True：仅使用缓存（包括允许窗口内的过期缓存），不触发 realtime

        Args:
            code: 资产代码
            asset_name: 资产名称（用于辅助判断）
            force_refresh: True 时跳过缓存，强制 realtime
            asset_type_map: 可选 {code -> AssetType}，用于更准确的 market_type/TTL 计算
            market_closed_ttl_multiplier: TTL 乘数（如非交易时段延长）
            accept_stale_when_closed: 是否允许在缓存过期后仍读取（通常用于市场关闭时的“稳定优先”）
            max_stale_after_expiry_sec: 允许过期后最多多少秒仍可返回
            use_cache_only: 仅使用缓存，不请求实时价格（用于超时 fallback）
        """
        return self.price_service.fetch(
            code,
            asset_name or "",
            force_refresh=force_refresh,
            asset_type_map=asset_type_map,
            market_closed_ttl_multiplier=market_closed_ttl_multiplier,
            accept_stale_when_closed=accept_stale_when_closed,
            max_stale_after_expiry_sec=max_stale_after_expiry_sec,
            use_cache_only=use_cache_only,
        )

    def fetch_batch(self, codes: List[str], name_map: Dict[str, str] = None,
                    asset_type_map: Dict[str, Any] = None,
                    market_closed_ttl_multiplier: float = 1.0,
                    accept_stale_when_closed: bool = False,
                    max_stale_after_expiry_sec: int = 0,
                    force_refresh: bool = False, use_concurrent: bool = True,
                    skip_us: bool = False, use_cache_only: bool = False) -> Dict[str, Dict]:
        """批量获取价格 (智能缓存 + 并发查询)

        Args:
            codes: 资产代码列表
            name_map: 代码到名称的映射
            force_refresh: 强制刷新缓存
            use_concurrent: 是否使用并发查询
            skip_us: 是否跳过美股查询（用于快速获取）
            use_cache_only: 仅使用缓存，不请求实时价格（超时时使用）

        Returns:
            代码到价格数据的映射
        """
        return self.price_service.fetch_batch(
            codes,
            name_map=name_map,
            asset_type_map=asset_type_map,
            market_closed_ttl_multiplier=market_closed_ttl_multiplier,
            accept_stale_when_closed=accept_stale_when_closed,
            max_stale_after_expiry_sec=max_stale_after_expiry_sec,
            force_refresh=force_refresh,
            use_concurrent=use_concurrent,
            skip_us=skip_us,
            use_cache_only=use_cache_only,
        ).payloads()

    def _retry_with_backoff(self, func, max_retries: int = 3, base_delay: float = 1.0):
        """带指数退避的重试机制

        Args:
            func: 要执行的函数
            max_retries: 最大重试次数
            base_delay: 基础延迟秒数

        Returns:
            func的返回值

        Raises:
            最后一次重试的异常
        """
        last_exception = None
        for attempt in range(max_retries):
            try:
                return func()
            except Exception as e:
                last_exception = e
                error_msg = str(e).lower()

                # 判断是否可重试的错误
                is_retryable = (
                    'rate' in error_msg or
                    'limit' in error_msg or
                    '429' in error_msg or
                    'timeout' in error_msg or
                    'connection' in error_msg or
                    '503' in error_msg or
                    '502' in error_msg or
                    'too many requests' in error_msg
                )

                if not is_retryable:
                    raise

                if attempt < max_retries - 1:
                    # 指数退避 + 随机抖动
                    delay = base_delay * (2 ** attempt) + random.uniform(0, 0.5)
                    print(f"  请求限流，{delay:.1f}秒后重试 ({attempt + 1}/{max_retries - 1})...")
                    time.sleep(delay)

        raise last_exception

    def _fetch_realtime(self, code: str, asset_name: str, asset_type: Any = None) -> Optional[Dict]:
        """获取实时价格 (内部方法)"""
        # 根据名称辅助判断类型
        name_hints = get_type_hints_from_name(asset_name)

        # 根据名称辅助判断并补全代码前缀
        normalized_code = normalize_code_with_name(code, asset_name)
        request = PriceRequest(
            code=code,
            asset_name=asset_name or "",
            asset_type=asset_type,
            normalized_code=normalized_code,
            hints=name_hints,
        )
        result = self.price_service.fetch_realtime(request)
        self._last_price_service_diagnostics = list(self.price_service.last_diagnostics)
        return result

    def _fetch_exchange_rates(self, max_retries: int = 3) -> Dict[str, float]:
        """获取汇率 (带24小时缓存，并发请求+重试机制)

        Args:
            max_retries: 最大重试次数

        Returns:
            汇率字典。获取失败时，如果有过期缓存则使用缓存并打印警告；
            完全没有缓存时抛出 RuntimeError。
        """
        return self.fx_service.fetch_exchange_rates(max_retries=max_retries)
