"""Tests for MarketTimeUtil.has_market_session_between (semantic stale window)."""
from datetime import datetime

from src.market_time import MarketTimeUtil

# Fixed week: 2026-07-17 Fri, 07-18 Sat, 07-20 Mon, 07-21 Tue (US in DST).
FRI = datetime(2026, 7, 17)
SAT = datetime(2026, 7, 18)
MON = datetime(2026, 7, 20)
TUE = datetime(2026, 7, 21)


def _at(day, hour, minute=0):
    return day.replace(hour=hour, minute=minute)


def test_cn_no_session_between_close_and_evening():
    assert MarketTimeUtil.has_market_session_between("cn", _at(MON, 15, 30), _at(MON, 20, 0)) is False


def test_cn_no_session_overnight_before_open():
    assert MarketTimeUtil.has_market_session_between("cn", _at(MON, 15, 30), _at(TUE, 8, 10)) is False


def test_cn_session_once_next_day_opens():
    assert MarketTimeUtil.has_market_session_between("cn", _at(MON, 15, 30), _at(TUE, 10, 0)) is True


def test_cn_weekend_gap_has_no_session():
    assert MarketTimeUtil.has_market_session_between("cn", _at(FRI, 15, 30), _at(MON, 8, 0)) is False
    assert MarketTimeUtil.has_market_session_between("cn", _at(FRI, 15, 30), _at(MON, 10, 0)) is True


def test_cn_lunch_start_still_catches_afternoon_session():
    assert MarketTimeUtil.has_market_session_between("cn", _at(MON, 12, 0), _at(MON, 14, 0)) is True


def test_us_session_between_saturday_and_tuesday_morning():
    # US opens Mon 21:30 Beijing time (DST), inside the window
    assert MarketTimeUtil.has_market_session_between("us", _at(SAT, 12, 0), _at(TUE, 8, 10)) is True


def test_us_no_session_before_monday_open():
    assert MarketTimeUtil.has_market_session_between("us", _at(SAT, 12, 0), _at(MON, 20, 0)) is False


def test_us_spanning_session_detected_at_window_start():
    # Mon 22:10 Beijing is inside the US regular session
    assert MarketTimeUtil.has_market_session_between("us", _at(MON, 22, 10), _at(TUE, 8, 10)) is True


def test_fund_and_unknown_markets_fail_closed():
    assert MarketTimeUtil.has_market_session_between("fund", _at(MON, 15, 30), _at(TUE, 8, 10)) is True
    assert MarketTimeUtil.has_market_session_between(None, _at(MON, 15, 30), _at(TUE, 8, 10)) is True


def test_hk_session_detection():
    # HK opens 09:30 Beijing time on weekdays
    assert MarketTimeUtil.has_market_session_between("hk", _at(MON, 16, 30), _at(MON, 20, 0)) is False
    assert MarketTimeUtil.has_market_session_between("hk", _at(MON, 16, 30), _at(TUE, 8, 10)) is False
    assert MarketTimeUtil.has_market_session_between("hk", _at(MON, 16, 30), _at(TUE, 10, 0)) is True


def test_aware_expires_at_is_normalized():
    from datetime import timezone
    from src.pricing.cache import _parse_expires_at

    # 2026-07-20T08:00:00Z == 2026-07-20 16:00 Beijing
    parsed = _parse_expires_at("2026-07-20T08:00:00Z")
    assert parsed is not None
    assert parsed.tzinfo is None
    assert (parsed.year, parsed.month, parsed.day, parsed.hour) == (2026, 7, 20, 16)

    aware = datetime(2026, 7, 20, 8, 0, tzinfo=timezone.utc)
    parsed2 = _parse_expires_at(aware)
    assert parsed2 is not None
    assert parsed2.tzinfo is None
    assert (parsed2.day, parsed2.hour) == (20, 16)


def test_empty_window_is_false():
    assert MarketTimeUtil.has_market_session_between("cn", _at(MON, 15, 30), _at(MON, 15, 30)) is False


def test_none_bounds_fail_closed():
    assert MarketTimeUtil.has_market_session_between("cn", None, _at(MON, 15, 30)) is True
    assert MarketTimeUtil.has_market_session_between("cn", _at(MON, 15, 30), None) is True
