from src.app.account_service import report_value_breakdown


def test_persisted_nav_stock_compatibility_value_is_not_added_to_fund_again():
    result = report_value_breakdown({
        "overview": {
            "total_value": 1000.0,
            "cash_ratio": 0.0,
            "stock_ratio": 0.0,
            "fund_ratio": 0.0,
        },
        "nav": {
            "cash_value": 200.0,
            # Persisted stock_value is already the full non-cash value.
            "stock_value": 800.0,
            "fund_value": 100.0,
        },
    })

    assert result == {
        "total_value": 1000.0,
        "cash_value": 200.0,
        "stock_value": 700.0,
        "fund_value": 100.0,
        "non_cash_value": 800.0,
    }


def test_runtime_ratio_breakdown_builds_non_cash_from_disjoint_categories():
    result = report_value_breakdown({
        "overview": {
            "total_value": 1000.0,
            "cash_ratio": 0.2,
            "stock_ratio": 0.7,
            "fund_ratio": 0.1,
        },
    })

    assert result["stock_value"] == 700.0
    assert result["fund_value"] == 100.0
    assert result["non_cash_value"] == 800.0
