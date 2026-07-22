"""测试价格获取模块"""
import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, MagicMock
from zoneinfo import ZoneInfo

from src.market_time import MarketTimeUtil
from src.price_fetcher import PriceFetcher
from src.asset_utils import detect_market_type
from src.models import AssetType, PriceCache
from src.pricing.cache import market_type_from_asset_type
from src.pricing.classifier import get_exchange_prefix, is_etf, is_otc_fund
from src.pricing.fixed import get_cash_price
from src.pricing.providers.cn import CNStockProvider
from src.pricing.providers.hk import HKStockProvider
from src.pricing.providers.us import USStockProvider


class TestMarketTimeUtil:
    """测试市场交易时间工具"""

    def test_is_cn_market_open_weekend(self):
        """测试A股周末休市"""
        saturday = datetime(2025, 3, 15, 10, 0, 0, tzinfo=ZoneInfo('Asia/Shanghai'))
        assert MarketTimeUtil.is_cn_market_open(saturday) == False

        sunday = datetime(2025, 3, 16, 10, 0, 0, tzinfo=ZoneInfo('Asia/Shanghai'))
        assert MarketTimeUtil.is_cn_market_open(sunday) == False

    def test_is_cn_market_open_trading_hours(self):
        """测试A股交易时间"""
        tz = ZoneInfo('Asia/Shanghai')

        # 上午开盘时间 9:30
        morning_open = datetime(2025, 3, 14, 9, 30, 0, tzinfo=tz)
        assert MarketTimeUtil.is_cn_market_open(morning_open) == True

        # 上午收盘时间 11:30
        morning_close = datetime(2025, 3, 14, 11, 30, 0, tzinfo=tz)
        assert MarketTimeUtil.is_cn_market_open(morning_close) == True

        # 午休时间 12:00
        lunch = datetime(2025, 3, 14, 12, 0, 0, tzinfo=tz)
        assert MarketTimeUtil.is_cn_market_open(lunch) == False

        # 下午开盘时间 13:00
        afternoon_open = datetime(2025, 3, 14, 13, 0, 0, tzinfo=tz)
        assert MarketTimeUtil.is_cn_market_open(afternoon_open) == True

        # 下午收盘时间 15:00
        afternoon_close = datetime(2025, 3, 14, 15, 0, 0, tzinfo=tz)
        assert MarketTimeUtil.is_cn_market_open(afternoon_close) == True

        # 收盘后 15:30
        after_close = datetime(2025, 3, 14, 15, 30, 0, tzinfo=tz)
        assert MarketTimeUtil.is_cn_market_open(after_close) == False

    def test_is_hk_market_open_trading_hours(self):
        """测试港股交易时间"""
        tz = ZoneInfo('Asia/Shanghai')

        # 上午开盘时间 9:30
        morning_open = datetime(2025, 3, 14, 9, 30, 0, tzinfo=tz)
        assert MarketTimeUtil.is_hk_market_open(morning_open) == True

        # 午休时间 12:30
        lunch = datetime(2025, 3, 14, 12, 30, 0, tzinfo=tz)
        assert MarketTimeUtil.is_hk_market_open(lunch) == False

        # 下午交易到 16:00
        afternoon = datetime(2025, 3, 14, 15, 30, 0, tzinfo=tz)
        assert MarketTimeUtil.is_hk_market_open(afternoon) == True

    def test_is_us_market_open_weekend(self):
        """测试美股周末休市"""
        tz = ZoneInfo('America/New_York')
        saturday = datetime(2025, 3, 15, 10, 0, 0, tzinfo=tz)
        assert MarketTimeUtil.is_us_market_open(saturday) == False

    def test_is_us_market_open_trading_hours(self):
        """测试美股交易时间（北京时间判断）"""
        tz_sh = ZoneInfo('Asia/Shanghai')

        # 夏令时: 北京时间 21:30-04:00
        # 周一 21:30 开盘（夏令时7月）
        market_open = datetime(2025, 7, 14, 21, 30, 0, tzinfo=tz_sh)
        assert MarketTimeUtil.is_us_market_open(market_open) == True

        # 北京时间 23:00（盘中）
        mid_session = datetime(2025, 7, 14, 23, 0, 0, tzinfo=tz_sh)
        assert MarketTimeUtil.is_us_market_open(mid_session) == True

        # 北京时间 次日凌晨 3:00（盘中）- 周二凌晨
        early_morning = datetime(2025, 7, 15, 3, 0, 0, tzinfo=tz_sh)
        assert MarketTimeUtil.is_us_market_open(early_morning) == True

        # 北京时间 次日 5:00（已收盘）
        after_close = datetime(2025, 7, 15, 5, 0, 0, tzinfo=tz_sh)
        assert MarketTimeUtil.is_us_market_open(after_close) == False


class TestPriceCache:
    """测试价格缓存模型"""

    def test_cache_creation(self):
        """测试创建价格缓存"""
        pc = PriceCache(
            asset_id="000001",
            asset_name="平安银行",
            asset_type=AssetType.A_STOCK,
            price=10.5,
            currency="CNY",
            cny_price=10.5
        )
        assert pc.asset_id == "000001"
        assert pc.price == 10.5
        assert pc.cny_price == 10.5

    def test_cache_with_exchange_rate(self):
        """测试带汇率的价格缓存"""
        pc = PriceCache(
            asset_id="AAPL",
            asset_name="Apple Inc",
            asset_type=AssetType.US_STOCK,
            price=175.0,
            currency="USD",
            cny_price=1260.0,
            exchange_rate=7.2,
            change=2.5,
            change_pct=0.0145
        )
        assert pc.currency == "USD"
        assert pc.exchange_rate == 7.2
        assert pc.change == 2.5

    def test_cache_optional_fields(self):
        """测试缓存可选字段"""
        pc = PriceCache(
            asset_id="000001",
            asset_type=AssetType.A_STOCK,
            price=10.5,
            currency="CNY",
            cny_price=10.5,
            data_source="tencent",
        )
        assert pc.data_source == "tencent"


class TestPriceFetcher:
    """测试价格获取器"""

    @patch.object(CNStockProvider, 'fetch_from_tencent')
    def test_fetch_a_stock_success(self, mock_tencent):
        """测试获取A股价格成功"""
        mock_tencent.return_value = {
            "code": "000001",
            "name": "平安银行",
            "price": 10.5,
            "change": 0.25,
            "change_pct": 0.0244,
            "currency": "CNY"
        }

        fetcher = PriceFetcher()
        result = CNStockProvider(fetcher).fetch_a_stock("000001")

        assert result is not None
        assert result["price"] == 10.5
        assert result["name"] == "平安银行"
        assert result["change"] == 0.25
        assert result["change_pct"] == 0.0244
        assert result["currency"] == "CNY"

    @patch.object(CNStockProvider, 'fetch_from_tencent')
    def test_fetch_a_stock_failure(self, mock_tencent):
        """测试获取A股价格失败"""
        mock_tencent.return_value = None

        fetcher = PriceFetcher()
        result = CNStockProvider(fetcher).fetch_a_stock("000001")

        assert result is None

    @patch.object(HKStockProvider, 'fetch_from_tencent')
    def test_fetch_hk_stock_success(self, mock_tencent):
        """测试获取港股价格成功"""
        mock_tencent.return_value = {
            "code": "00700",
            "name": "腾讯控股",
            "price": 400.0,
            "change": 5.0,
            "change_pct": 0.0127,
            "currency": "HKD"
        }

        fetcher = PriceFetcher()
        result = HKStockProvider(fetcher).fetch_hk_stock("00700")

        assert result is not None
        assert result["price"] == 400.0
        assert result["name"] == "腾讯控股"
        assert result["currency"] == "HKD"

    @patch.object(PriceFetcher, '_retry_with_backoff')
    def test_fetch_us_stock_success(self, mock_retry):
        """测试获取美股价格成功"""
        mock_retry.return_value = {
            "code": "AAPL",
            "name": "Apple Inc",
            "price": 175.0,
            "change": 2.5,
            "change_pct": 0.0145,
            "currency": "USD",
            "source": "sina_us"
        }

        fetcher = PriceFetcher()
        result = USStockProvider(fetcher).fetch_us_stock("AAPL")

        assert result is not None
        assert result["price"] == 175.0
        assert result["currency"] == "USD"
        assert result["source"] == "sina_us"

    def test_get_cash_price(self):
        """测试获取现金价格"""
        result = get_cash_price("CNY-CASH")
        assert result is not None
        assert result["price"] == 1.0
        assert result["cny_price"] == 1.0
        assert result["currency"] == "CNY"

        result = get_cash_price("USD-CASH", lambda: {"USDCNY": 7.2})
        assert result is not None
        assert result["price"] == 1.0
        assert result["currency"] == "USD"

    def test_detect_market_type(self):
        """测试市场类型检测（已迁移到 asset_utils 模块）"""
        assert detect_market_type("000001") == "cn"
        assert detect_market_type("600000") == "cn"
        assert detect_market_type("688981") == "cn"  # 科创板
        assert detect_market_type("301039") == "cn"  # 创业板注册制
        assert detect_market_type("00700") == "hk"
        assert detect_market_type("09988") == "hk"
        assert detect_market_type("AAPL") == "us"
        assert detect_market_type("TSLA") == "us"
        assert detect_market_type("110022") == "fund"
        assert detect_market_type("510300") == "cn"
        assert detect_market_type("159915") == "cn"

    def test_is_etf(self):
        """测试ETF识别"""
        # 场内ETF（股票代码形式）
        assert is_etf("510300") == True  # 300ETF
        assert is_etf("159915") == True  # 创业板ETF
        # 非ETF
        assert is_etf("000001") == False

    def test_asset_type_market_routing_for_split_funds(self):
        assert market_type_from_asset_type("510300", AssetType.EXCHANGE_FUND, "cn") == "cn"
        assert market_type_from_asset_type("110022", AssetType.OTC_FUND, "fund") == "fund"
        assert market_type_from_asset_type("510300", AssetType.FUND, "fund") == "cn"
        assert market_type_from_asset_type("SPY", AssetType.EXCHANGE_FUND, "us") == "us"

    def test_is_otc_fund(self):
        """测试场外基金识别"""
        # 明确的场外基金代码（不与A股重叠）
        assert is_otc_fund("004001") == True  # 004开头
        assert is_otc_fund("010001") == True  # 01开头
        assert is_otc_fund("160106") == True  # 16开头
        assert is_otc_fund("270042") == True  # 27开头
        # 与A股重叠的代码返回False，需依赖name_hints判断
        assert is_otc_fund("000001") == False  # 000开头，与A股重叠
        assert is_otc_fund("001001") == False  # 001开头，与A股重叠
        assert is_otc_fund("002001") == False  # 002开头，与A股重叠
        # 明确非场外基金
        assert is_otc_fund("600519") == False  # 沪市A股
        assert is_otc_fund("300750") == False  # 创业板
        assert is_otc_fund("688981") == False  # 科创板
        assert is_otc_fund("301039") == False  # 创业板注册制
        assert is_otc_fund("510300") == False  # 场内ETF

    def test_get_exchange_prefix(self):
        """测试交易所前缀"""
        assert get_exchange_prefix("600000") == "sh"
        assert get_exchange_prefix("000001") == "sz"
        assert get_exchange_prefix("300750") == "sz"

    def test_fetch_with_storage_cache(self):
        """测试带存储的缓存获取"""
        mock_storage = Mock()
        mock_cached = Mock(
            asset_id="000001",
            asset_name="平安银行",
            price=10.5,
            cny_price=10.5,
            currency="CNY",
            change=0.25,
            change_pct=0.0244,
            exchange_rate=None,
            data_source="tencent",
            expires_at=None
        )
        mock_storage.get_price.return_value = mock_cached

        fetcher = PriceFetcher(storage=mock_storage)
        result = fetcher.fetch("000001")

        assert result is not None
        assert result["price"] == 10.5
        assert result["code"] == "000001"

    def test_fetch_batch_cache_only_uses_stale_fallback_policy(self):
        """批量路径应复用统一缓存策略，cache-only 不触发实时请求。"""
        mock_storage = Mock()
        mock_storage.get_price.return_value = PriceCache(
            asset_id="000001",
            asset_name="平安银行",
            asset_type=AssetType.A_STOCK,
            price=10.5,
            cny_price=10.5,
            currency="CNY",
            data_source="tencent",
            expires_at=datetime.now() - timedelta(hours=1),
        )

        fetcher = PriceFetcher(storage=mock_storage, use_cache=True)
        fetcher._fetch_realtime = Mock(return_value=None)

        result = fetcher.fetch_batch(
            ["000001"],
            accept_stale_when_closed=True,
            max_stale_after_expiry_sec=7200,
            use_cache_only=True,
        )

        assert result["000001"]["source"] == "cache_fallback"
        assert result["000001"]["cache_status"] == "stale_fallback"
        assert result["000001"]["is_stale"] is True
        fetcher._fetch_realtime.assert_not_called()


def test_us_market_uses_new_york_local_weekday_across_beijing_date_boundary():
    beijing = ZoneInfo("Asia/Shanghai")

    # Saturday in Beijing is still Friday 15:00 in New York.
    assert MarketTimeUtil.is_us_market_open(
        datetime(2026, 7, 18, 3, 0, tzinfo=beijing)
    ) is True
    assert MarketTimeUtil.is_us_market_open(
        datetime(2026, 7, 18, 4, 0, tzinfo=beijing)
    ) is False


def test_us_next_open_elapsed_handles_dst_change():
    new_york = ZoneInfo("America/New_York")
    friday_after_close = datetime(2026, 3, 6, 16, 30, tzinfo=new_york)

    # DST starts on Sunday: 64 absolute hours to Monday 09:30, not 65 wall hours.
    assert MarketTimeUtil._seconds_until_next_us_open(friday_after_close) == 64 * 3600


def test_fx_invalid_file_cache_never_becomes_fresh(tmp_path, monkeypatch):
    import json

    from src.pricing.fx import FxRateService
    from src.time_utils import bj_now_naive

    cache_file = tmp_path / "rate_cache.json"
    cache_file.write_text(
        json.dumps({"rates": {"USDCNY": 7.1}, "timestamp": bj_now_naive().isoformat()}),
        encoding="utf-8",
    )
    service = FxRateService(Mock(), cache_file=cache_file)

    def fail(_currency, *, deadline=None):
        raise RuntimeError("offline")

    for name in (
        "_fetch_from_open_er_api",
        "_fetch_from_exchangerate_api",
        "_fetch_from_chinamoney",
        "_fetch_from_exchangerate_host",
    ):
        monkeypatch.setattr(service, name, fail)

    with pytest.raises(RuntimeError, match="没有可用缓存"):
        service.fetch_exchange_rates(max_retries=1)

    assert service._rate_cache == {}
    assert service._rate_cache_time is None


def test_fx_retry_backoff_stops_at_deadline(tmp_path, monkeypatch):
    import time

    from src.pricing.fx import FxRateService

    service = FxRateService(Mock(), cache_file=tmp_path / "missing.json")
    calls = []

    def fail(currency, *, deadline=None):
        calls.append(currency)
        raise RuntimeError("offline")

    for name in (
        "_fetch_from_open_er_api",
        "_fetch_from_exchangerate_api",
        "_fetch_from_chinamoney",
        "_fetch_from_exchangerate_host",
    ):
        monkeypatch.setattr(service, name, fail)

    started = time.monotonic()
    with pytest.raises(RuntimeError, match="没有可用缓存"):
        service.fetch_exchange_rates(
            max_retries=3,
            deadline=time.monotonic() + 0.02,
        )

    assert time.monotonic() - started < 0.1
    assert calls == ["USD"]
