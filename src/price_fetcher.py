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
from typing import Dict, Optional, List, Tuple, Any

from .market_time import MarketTimeUtil  # re-exported for legacy tests/callers
from .pricing import PriceRequest, PriceService
from .pricing.cache import market_type_from_asset_type as _market_type_from_asset_type
from .pricing.cache import price_cache_to_payload
from .pricing.fixed import get_cash_price, get_cash_price_with_rates, get_mmf_price
from .pricing.fx import FxRateService
from .pricing.classifier import (
    get_exchange_prefix,
    get_type_hints_from_name,
    is_etf,
    is_otc_fund,
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
        self.price_service = PriceService.for_legacy_fetcher(self)
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

    def _fetch_concurrent(self, codes: List[str], name_map: Dict[str, str],
                          max_workers: int = 5, _nested: bool = False,
                          asset_type_map: Dict[str, Any] = None) -> Dict[str, Dict]:
        """Compatibility adapter for non-US batch quotes."""
        from .pricing.batch import BatchPricePlanner

        return BatchPricePlanner(self).fetch_non_us(
            codes,
            name_map,
            max_workers=max_workers,
            _nested=_nested,
            asset_type_map=asset_type_map,
        )

    def _fetch_tencent_quotes_batch(
        self,
        codes: List[str],
        name_map: Dict[str, str] = None,
        asset_type_map: Dict[str, Any] = None,
    ) -> Tuple[Dict[str, Dict], List[str]]:
        """Compatibility adapter for Tencent batch quotes."""
        from .pricing.providers.tencent_batch import fetch_tencent_quotes_batch

        return fetch_tencent_quotes_batch(self, codes, name_map=name_map, asset_type_map=asset_type_map)

    def _fetch_us_batch(self, codes: List[str], name_map: Dict[str, str],
                        expired_cache: Dict[str, Dict], max_workers: int = 3,
                        _nested: bool = False) -> Dict[str, Dict]:
        """Compatibility adapter for fast US batch quotes."""
        from .pricing.providers.us_batch import fetch_us_batch

        return fetch_us_batch(
            self,
            codes,
            name_map,
            expired_cache,
            max_workers=max_workers,
            _nested=_nested,
        )

    def _price_cache_to_dict(self, cached) -> Dict:
        """将PriceCache对象转为字典"""
        return price_cache_to_payload(cached)

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

    def _get_cash_price_with_rates(self, code: str, rates: Dict[str, float]) -> Dict:
        """获取现金价格（使用外部传入的汇率，避免重复请求）"""
        return get_cash_price_with_rates(code, rates)

    def _get_cash_price(self, code: str) -> Dict:
        """获取现金价格"""
        return get_cash_price(code, self._fetch_exchange_rates)

    def _get_mmf_price(self, code: str) -> Dict:
        """获取货币基金价格"""
        return get_mmf_price(code)

    def _fetch_realtime(self, code: str, asset_name: str, asset_type: Any = None) -> Optional[Dict]:
        """获取实时价格 (内部方法)"""
        # 根据名称辅助判断类型
        name_hints = self._get_type_hints_from_name(asset_name)

        # 根据名称辅助判断并补全代码前缀
        normalized_code = self._normalize_code_with_name(code, asset_name)
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

    def _normalize_code_with_name(self, code: str, name: str) -> str:
        """根据资产名称给代码添加交易所前缀"""
        return normalize_code_with_name(code, name)

    def _get_type_hints_from_name(self, name: str) -> Dict:
        """从资产名称中提取类型提示"""
        return get_type_hints_from_name(name)

    def _is_etf(self, code: str) -> bool:
        """检测是否为ETF/场内基金"""
        return is_etf(code)

    def _is_otc_fund(self, code: str) -> bool:
        """检测是否为场外基金代码

        注意: 000/002/003 开头的代码与A股重叠（如 000001 既是平安银行也是华夏成长），
        无法仅凭代码区分。此方法仅识别不含歧义的场外基金代码。
        歧义代码需依赖 name_hints 在 _fetch_realtime 中判断。
        """
        return is_otc_fund(code)

    def _get_exchange_prefix(self, code: str) -> str:
        """获取交易所前缀"""
        return get_exchange_prefix(code)

    def _load_rate_cache_from_file(self) -> Optional[Dict]:
        """从 JSON 文件加载汇率缓存"""
        return self.fx_service.load_cache_from_file()

    def _save_rate_cache_to_file(self, rates: Dict[str, float]):
        """保存汇率缓存到 JSON 文件"""
        self.fx_service.save_cache_to_file(rates)

    def _fetch_exchange_rates(self, max_retries: int = 3) -> Dict[str, float]:
        """获取汇率 (带24小时缓存，并发请求+重试机制)

        Args:
            max_retries: 最大重试次数

        Returns:
            汇率字典。获取失败时，如果有过期缓存则使用缓存并打印警告；
            完全没有缓存时抛出 RuntimeError。
        """
        return self.fx_service.fetch_exchange_rates(max_retries=max_retries)

    # ========== Provider compatibility adapters ==========

    def _fetch_a_stock(self, code: str) -> Optional[Dict]:
        """兼容旧调用：A 股实时价格实现已迁移到 CNStockProvider。"""
        from .pricing.providers.cn import CNStockProvider

        return CNStockProvider(self).fetch_a_stock(code)

    def _fetch_a_stock_from_tencent(self, code: str) -> Optional[Dict]:
        """兼容旧调用：腾讯 A 股源已迁移到 CNStockProvider。"""
        from .pricing.providers.cn import CNStockProvider

        return CNStockProvider(self).fetch_from_tencent(code)

    def _fetch_hk_stock(self, code: str) -> Optional[Dict]:
        """兼容旧调用：港股实时价格实现已迁移到 HKStockProvider。"""
        from .pricing.providers.hk import HKStockProvider

        return HKStockProvider(self).fetch_hk_stock(code)

    def _fetch_hk_stock_from_tencent(self, code: str) -> Optional[Dict]:
        """兼容旧调用：腾讯港股源已迁移到 HKStockProvider。"""
        from .pricing.providers.hk import HKStockProvider

        return HKStockProvider(self).fetch_from_tencent(code)

    def _fetch_us_stock(self, code: str) -> Optional[Dict]:
        """兼容旧调用：美股实时价格实现已迁移到 USStockProvider。"""
        from .pricing.providers.us import USStockProvider

        return USStockProvider(self).fetch_us_stock(code)

    def _fetch_us_stock_finnhub(self, code: str, api_key: str) -> Optional[Dict]:
        """兼容旧调用：Finnhub 源已迁移到 USStockProvider。"""
        from .pricing.providers.us import USStockProvider

        return USStockProvider(self).fetch_finnhub(code, api_key)

    def _fetch_us_stock_yahoo_chart(self, code: str) -> Optional[Dict]:
        """兼容旧调用：Yahoo Chart 源已迁移到 USStockProvider。"""
        from .pricing.providers.us import USStockProvider

        return USStockProvider(self).fetch_yahoo_chart(code)

    def _fetch_etf(self, code: str) -> Optional[Dict]:
        """兼容旧调用：ETF 实时价格实现已迁移到 ETFProvider。"""
        from .pricing.providers.etf import ETFProvider

        return ETFProvider(self).fetch_etf(code)

    def _fetch_fund(self, code: str) -> Optional[Dict]:
        """兼容旧调用：场外基金净值实现已迁移到 FundProvider。"""
        from .pricing.providers.fund import FundProvider

        return FundProvider(self).fetch_fund(code)

    def _fetch_fund_from_tencent(self, code: str) -> Optional[Dict]:
        """兼容旧调用：腾讯基金源已迁移到 FundProvider。"""
        from .pricing.providers.fund import FundProvider

        return FundProvider(self).fetch_from_tencent(code)

    def _fetch_fund_from_eastmoney(self, code: str) -> Optional[Dict]:
        """兼容旧调用：东方财富基金源已迁移到 FundProvider。"""
        from .pricing.providers.fund import FundProvider

        return FundProvider(self).fetch_from_eastmoney(code)
