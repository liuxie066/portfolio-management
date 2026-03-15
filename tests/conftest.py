"""测试配置和 fixtures"""
import pytest
from datetime import date, datetime
from unittest.mock import Mock


@pytest.fixture
def mock_storage():
    """模拟存储层"""
    return Mock()


@pytest.fixture
def mock_price_fetcher():
    """模拟价格获取器"""
    return Mock()


@pytest.fixture
def mock_feishu_client():
    """模拟飞书客户端"""
    return Mock()


@pytest.fixture
def sample_date():
    """示例日期"""
    return date(2025, 3, 14)


@pytest.fixture
def sample_datetime():
    """示例日期时间"""
    return datetime(2025, 3, 14, 10, 30, 0)
