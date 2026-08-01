from datetime import date
from unittest.mock import Mock

import pytest

from src.app.cash_flow_summary_service import CashFlowSummaryService
from src.domain.cash_flow_contracts import (
    CashFlowContractError,
    CompletedCashFlowFacts,
    expected_cash_flow_dedup_key_from_values,
)
from src.models import CashFlow, NAVHistory


def _completed_flow(flow_date, amount, *, currency="CNY", rate=1):
    return CompletedCashFlowFacts.build(
        flow_date=flow_date,
        account="a",
        broker="某券商",
        amount=amount,
        currency=currency,
        exchange_rate=rate,
        cny_amount=amount * rate,
        source="test",
    ).to_cash_flow()


def test_cash_flow_summary_service_summarizes_aggregate_cache():
    storage = Mock()
    storage.get_cash_flow_aggs.return_value = {
        "daily": {
            "2024-12-31": 10000,
            "2025-03-01": 20000,
            "2025-03-14": 5000,
            "bad-date": 999,
            "2025-03-15": 123,
        },
        "monthly": {"2025-03": 25000},
        "yearly": {"2024": 10000, "2025": 25000},
    }
    service = CashFlowSummaryService(storage=storage)
    last_nav = NAVHistory(date=date(2025, 3, 13), account="a", total_value=1000.0, nav=1.0, shares=1000.0)

    result = service.summarize("a", date(2025, 3, 14), 2024, last_nav=last_nav)

    assert result == {
        "daily": 5000.0,
        "monthly": 25000.0,
        "yearly": {"2024": 10000.0, "2025": 25000.0},
        "cumulative": 35000.0,
        "gap": 5000.0,
    }
    storage.preload_cash_flow_aggs.assert_called_with("a")


def test_cash_flow_summary_service_period_and_point_queries():
    storage = Mock()
    storage.get_cash_flow_aggs.return_value = {
        "daily": {"2025-03-01": 100, "2025-03-14": 50},
        "monthly": {"2025-03": 150},
        "yearly": {"2025": 150},
    }
    service = CashFlowSummaryService(storage=storage)

    assert service.daily("a", date(2025, 3, 14)) == 50.0
    assert service.monthly("a", 2025, 3) == 150.0
    assert service.yearly("a", "2025") == 150.0
    assert service.period("a", date(2025, 3, 2), date(2025, 3, 14)) == 50.0


def test_cash_flow_summary_service_sums_cash_flow_objects():
    flows = [
        _completed_flow(date(2025, 3, 1), 100),
        _completed_flow(date(2025, 3, 2), 50),
        _completed_flow(date(2025, 3, 3), -20),
    ]

    assert CashFlowSummaryService.sum_cash_flows(flows) == 130.0


def test_cash_flow_summary_service_rejects_foreign_flow_without_cny_amount():
    flow = CashFlow(
        record_id="cf_usd",
        flow_date=date(2025, 3, 1),
        account="a",
        broker="某券商",
        amount=10,
        currency="USD",
        cny_amount=None,
        exchange_rate=None,
        flow_type="DEPOSIT",
        dedup_key=expected_cash_flow_dedup_key_from_values(
            flow_date=date(2025, 3, 1),
            account="a",
            broker="某券商",
            amount=10,
            currency="USD",
            flow_type="DEPOSIT",
        ),
        source="test",
    )

    with pytest.raises(CashFlowContractError) as exc_info:
        CashFlowSummaryService.sum_cash_flows([flow])

    assert {issue.reason_code for issue in exc_info.value.issues} >= {
        "EXCHANGE_RATE_MISSING",
        "CNY_AMOUNT_MISSING",
    }


def test_cash_flow_summary_service_missing_date_blocks_without_partial_aggregate():
    missing_date = CashFlow(
        record_id="cf_missing_date",
        flow_date=None,
        account="a",
        broker="某券商",
        amount=100,
        currency="CNY",
        cny_amount=100,
        exchange_rate=1,
        flow_type="DEPOSIT",
        dedup_key="untrusted-without-date",
        source="test",
    )
    storage = Mock()
    storage.get_cash_flows.return_value = [
        _completed_flow(date(2025, 3, 1), 50),
        missing_date,
    ]
    service = CashFlowSummaryService(storage=storage)

    with pytest.raises(CashFlowContractError) as exc_info:
        service._build_aggs_from_flows("a")

    assert "FLOW_DATE_MISSING" in {
        issue.reason_code for issue in exc_info.value.issues
    }
