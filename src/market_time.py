"""
市场交易时间工具模块

从 price_fetcher.py 提取，零外部依赖（标准库 zoneinfo）。
提供各市场开盘判断和智能缓存 TTL 计算。
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .models import MarketType


class MarketTimeUtil:
    """市场交易时间工具"""

    # 时区定义
    TZ_SHANGHAI = ZoneInfo('Asia/Shanghai')
    TZ_NEW_YORK = ZoneInfo('America/New_York')
    TZ_UTC = ZoneInfo('UTC')

    @classmethod
    def is_cn_market_open(cls, dt: datetime = None) -> bool:
        """判断A股市场是否开盘 (北京时间)
        交易时间: 9:30-11:30, 13:00-15:00 (周一至周五)
        """
        if dt is None:
            dt = datetime.now(cls.TZ_SHANGHAI)

        # 周末休市
        if dt.weekday() >= 5:
            return False

        hour, minute = dt.hour, dt.minute
        time_val = hour * 100 + minute

        # 上午 9:30-11:30, 下午 13:00-15:00
        return (930 <= time_val <= 1130) or (1300 <= time_val <= 1500)

    @classmethod
    def is_hk_market_open(cls, dt: datetime = None) -> bool:
        """判断港股市场是否开盘 (北京时间)
        交易时间: 9:30-12:00, 13:00-16:00 (周一至周五)
        """
        if dt is None:
            dt = datetime.now(cls.TZ_SHANGHAI)

        if dt.weekday() >= 5:
            return False

        hour, minute = dt.hour, dt.minute
        time_val = hour * 100 + minute

        return (930 <= time_val <= 1200) or (1300 <= time_val <= 1600)

    @classmethod
    def _as_aware(cls, dt: datetime | None, default_tz: ZoneInfo) -> datetime:
        if dt is None:
            return datetime.now(default_tz)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=default_tz)
        return dt

    @classmethod
    def is_dst_in_new_york(cls, dt: datetime = None) -> bool:
        """Return whether the supplied instant is in New York daylight time."""
        instant = cls._as_aware(dt, cls.TZ_NEW_YORK)
        return bool(instant.astimezone(cls.TZ_NEW_YORK).dst())

    @classmethod
    def is_us_market_open(cls, dt: datetime = None) -> bool:
        """Evaluate the supplied instant in New York local market time.

        Trading hours are Monday-Friday 09:30-16:00. Exchange holidays remain
        outside the current calendar contract.
        """
        instant = cls._as_aware(dt, cls.TZ_SHANGHAI)
        local = instant.astimezone(cls.TZ_NEW_YORK)
        time_value = local.hour * 100 + local.minute
        return local.weekday() < 5 and 930 <= time_value < 1600

    @classmethod
    def get_us_market_hours(cls, dt: datetime = None) -> tuple:
        """Return regular-session hours in Beijing time for compatibility."""
        return (2130, 400) if cls.is_dst_in_new_york(dt) else (2230, 500)

    @classmethod
    def _seconds_until_next_cn_open(cls, now: datetime) -> int:
        """计算到A股下次开盘的秒数"""
        current_weekday = now.weekday()

        if current_weekday >= 5:
            days_until_monday = 7 - current_weekday
            next_open = now + timedelta(days=days_until_monday)
            next_open = next_open.replace(hour=9, minute=30, second=0, microsecond=0)
            return int((next_open - now).total_seconds())

        hour, minute = now.hour, now.minute
        time_val = hour * 100 + minute

        if time_val < 930:
            next_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
            return int((next_open - now).total_seconds())

        if time_val >= 1500:
            if current_weekday == 4:
                next_open = now + timedelta(days=3)
            else:
                next_open = now + timedelta(days=1)
            next_open = next_open.replace(hour=9, minute=30, second=0, microsecond=0)
            return int((next_open - now).total_seconds())

        # 午间休市 (11:30-13:00)
        if 1130 <= time_val < 1300:
            next_open = now.replace(hour=13, minute=0, second=0, microsecond=0)
            return int((next_open - now).total_seconds())

        # 交易时间内，返回10分钟
        return 600

    @classmethod
    def _seconds_until_next_hk_open(cls, now: datetime) -> int:
        """计算到港股下次开盘的秒数"""
        current_weekday = now.weekday()

        if current_weekday >= 5:
            days_until_monday = 7 - current_weekday
            next_open = now + timedelta(days=days_until_monday)
            next_open = next_open.replace(hour=9, minute=30, second=0, microsecond=0)
            return int((next_open - now).total_seconds())

        hour, minute = now.hour, now.minute
        time_val = hour * 100 + minute

        if time_val < 930:
            next_open = now.replace(hour=9, minute=30, second=0, microsecond=0)
            return int((next_open - now).total_seconds())

        if time_val >= 1600:  # 港股收盘16:00
            if current_weekday == 4:
                next_open = now + timedelta(days=3)
            else:
                next_open = now + timedelta(days=1)
            next_open = next_open.replace(hour=9, minute=30, second=0, microsecond=0)
            return int((next_open - now).total_seconds())

        # 午间休市 (12:00-13:00)
        if 1200 <= time_val < 1300:
            next_open = now.replace(hour=13, minute=0, second=0, microsecond=0)
            return int((next_open - now).total_seconds())

        return 600

    @classmethod
    def _seconds_until_next_us_open(cls, now: datetime) -> int:
        """Calculate elapsed seconds to the next New York-local regular open."""
        instant = cls._as_aware(now, cls.TZ_SHANGHAI)
        local = instant.astimezone(cls.TZ_NEW_YORK)
        time_value = local.hour * 100 + local.minute

        if local.weekday() < 5 and 930 <= time_value < 1600:
            return 600

        if local.weekday() < 5 and time_value < 930:
            next_open_local = local.replace(hour=9, minute=30, second=0, microsecond=0)
        else:
            next_open_local = (local + timedelta(days=1)).replace(
                hour=9, minute=30, second=0, microsecond=0
            )
            while next_open_local.weekday() >= 5:
                next_open_local += timedelta(days=1)

        return max(
            0,
            int(
                (
                    next_open_local.astimezone(cls.TZ_UTC)
                    - instant.astimezone(cls.TZ_UTC)
                ).total_seconds()
            ),
        )

    @classmethod
    def _seconds_until_next_fund_update(cls, now: datetime) -> int:
        """计算到基金下次净值更新时间的秒数

        基金净值每天晚上7-9点更新一次：
        - 周中(周一到周四): 缓存到次日19:00
        - 周五到周日: 缓存到下周一19:00
        """
        current_weekday = now.weekday()

        if current_weekday <= 3:  # 周一到周四
            next_update = now + timedelta(days=1)
        elif current_weekday == 4:  # 周五
            next_update = now + timedelta(days=3)
        elif current_weekday == 5:  # 周六
            next_update = now + timedelta(days=2)
        else:  # 周日
            next_update = now + timedelta(days=1)

        next_update = next_update.replace(hour=19, minute=0, second=0, microsecond=0)
        return int((next_update - now).total_seconds())

    @classmethod
    def get_cache_ttl(cls, market_type) -> int:
        """根据市场类型和当前时间获取缓存有效期(秒)

        策略：
        - 交易时间：缓存30分钟
        - 非交易时间：缓存到下次开盘前

        Args:
            market_type: MarketType 枚举或字符串 'cn' (A股), 'hk' (港股), 'us' (美股), 'fund' (基金)

        Returns:
            缓存有效期秒数
        """
        now = datetime.now(cls.TZ_SHANGHAI)

        # 支持 MarketType 枚举和字符串（向后兼容）
        market_key = market_type.value if isinstance(market_type, MarketType) else market_type

        if market_key == MarketType.CN:
            if cls.is_cn_market_open(now):
                return 1800
            else:
                return cls._seconds_until_next_cn_open(now)

        elif market_key == MarketType.HK:
            if cls.is_hk_market_open(now):
                return 1800
            else:
                return cls._seconds_until_next_hk_open(now)

        elif market_key == MarketType.US:
            if cls.is_us_market_open(now):
                return 1800
            else:
                return cls._seconds_until_next_us_open(now)

        elif market_key == MarketType.FUND:
            return cls._seconds_until_next_fund_update(now)

        else:
            # 未知市场类型，默认缓存 1 小时
            return 3600
