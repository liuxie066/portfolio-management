from datetime import date
from types import SimpleNamespace

import pytest

from src.domain.nav_calculator import ClosedNavTarget, NavCalculator
from src.models import NAVHistory, PortfolioValuation


def test_nav_calculator_period_metrics_and_yearly_mutation():
    prev_year = NAVHistory(date=date(2024, 12, 31), account="a", total_value=1000.0, nav=1.0, shares=1000.0)
    prev_month = NAVHistory(date=date(2025, 2, 28), account="a", total_value=1100.0, nav=1.1, shares=1000.0)
    yesterday = NAVHistory(date=date(2025, 3, 13), account="a", total_value=1200.0, nav=1.2, shares=1000.0)
    yearly_data = {
        "2025": {
            "prev_end": prev_year,
            "end": NAVHistory(date=date(2025, 12, 31), account="a", total_value=1300.0, nav=1.3),
            "cash_flow": 50.0,
        }
    }

    result = NavCalculator.calc_nav_metrics(
        today=date(2025, 3, 14),
        total_value=1320.0,
        yesterday_nav=yesterday,
        prev_year_end_nav=prev_year,
        prev_month_end_nav=prev_month,
        last_nav=yesterday,
        yearly_data=yearly_data,
        daily_cash_flow=120.0,
        monthly_cash_flow=120.0,
        yearly_cash_flow=120.0,
        cumulative_cash_flow=120.0,
        start_year=2025,
        initial_value=1000.0,
        gap_cash_flow=120.0,
    )

    assert result["shares"] == 1100.0
    assert result["shares_change"] == 100.0
    assert result["nav"] == 1.2
    assert result["daily_appreciation"] == 0.0
    assert result["month_appreciation"] == 100.0
    assert result["year_appreciation"] == 200.0
    assert yearly_data["2025"]["nav_change"] == pytest.approx(0.3)
    assert yearly_data["2025"]["appreciation"] == 250.0


def test_nav_calculator_build_and_validate_nav_record():
    valuation = PortfolioValuation(
        account="a",
        total_value_cny=1100.0,
        cash_value_cny=100.0,
        stock_value_cny=1000.0,
    )
    nav = NavCalculator.build_nav_record(
        today=date(2025, 3, 14),
        account="a",
        valuation=valuation,
        stock_value=1000.0,
        cash_value=100.0,
        total_value=1100.0,
        stock_ratio=1000 / 1100,
        cash_ratio=100 / 1100,
        daily_cash_flow=100.0,
        monthly_cash_flow=100.0,
        yearly_cash_flow=100.0,
        yearly_data={"2025": {"nav_change": 0.0, "appreciation": 0.0, "cash_flow": 100.0}},
        cumulative_cash_flow=100.0,
        start_year=2025,
        shares=1100.0,
        shares_change=100.0,
        nav=1.0,
        month_nav_change=0.0,
        year_nav_change=0.0,
        cumulative_nav_change=0.0,
        daily_appreciation=0.0,
        month_appreciation=0.0,
        year_appreciation=0.0,
        cumulative_appreciation=0.0,
        initial_value=1000.0,
        first_year_data=None,
        cagr=0.0,
    )

    NavCalculator.validate_nav_record(
        nav_record=nav,
        last_nav=NAVHistory(date=date(2025, 3, 13), account="a", total_value=1000.0, nav=1.0, shares=1000.0),
        prev_month_end_nav=NAVHistory(date=date(2025, 2, 28), account="a", total_value=1000.0, nav=1.0),
        prev_year_end_nav=NAVHistory(date=date(2024, 12, 31), account="a", total_value=1000.0, nav=1.0),
        daily_cash_flow=100.0,
        monthly_cash_flow=100.0,
        yearly_cash_flow=100.0,
        gap_cash_flow=100.0,
        initial_value=1000.0,
        cumulative_cash_flow=100.0,
    )


def test_nav_calculator_validate_rejects_inconsistent_total():
    nav = NAVHistory(
        date=date(2025, 3, 14),
        account="a",
        total_value=1200.0,
        cash_value=100.0,
        stock_value=1000.0,
        stock_weight=0.9,
        cash_weight=0.1,
        shares=1200.0,
        nav=1.0,
        cash_flow=0.0,
        share_change=0.0,
    )

    with pytest.raises(ValueError, match="total_value 不等于 stock_value \\+ cash_value"):
        NavCalculator.validate_nav_record(nav_record=nav)


def test_nav_valuation_projection_has_one_non_cash_definition():
    projection = NavCalculator.project_valuation(
        PortfolioValuation(
            account="a",
            total_value_cny=1000.0,
            cash_value_cny=200.0,
            stock_value_cny=700.0,
            fund_value_cny=100.0,
            cn_asset_value=500.0,
            us_asset_value=300.0,
        )
    )

    assert float(projection.non_cash_value) == 800.0
    assert float(projection.fund_value) == 100.0
    assert float(projection.total_value) == 1000.0
    assert float(projection.stock_weight) == 0.8
    assert float(projection.cash_weight) == 0.2


def test_nav_valuation_projection_rejects_runtime_total_drift():
    with pytest.raises(ValueError, match="valuation decomposition mismatch"):
        NavCalculator.project_valuation(
            PortfolioValuation(
                account="a",
                total_value_cny=999.0,
                cash_value_cny=200.0,
                stock_value_cny=700.0,
                fund_value_cny=100.0,
            )
        )


def test_nav_valuation_projection_decomposes_at_persisted_precision():
    projection = NavCalculator.project_valuation(
        SimpleNamespace(
            total_value_cny="0.02",
            cash_value_cny="0.005",
            stock_value_cny="0.005",
            fund_value_cny="0",
            cn_asset_value="0",
            us_asset_value="0",
            hk_asset_value="0",
        )
    )

    assert projection.total_value == projection.cash_value + projection.non_cash_value
    assert float(projection.total_value) == 0.02


def test_nav_final_invariants_validate_full_dataset_fingerprint_and_finality():
    dataset = SimpleNamespace(
        details=lambda: {
            "contract_version": "pm.cash_flow.dataset.v1",
            "financial_fingerprint": "financial",
            "full_fingerprint": "full",
        }
    )
    nav = NAVHistory(
        date=date(2026, 3, 19),
        account="a",
        total_value=100.0,
        cash_value=20.0,
        stock_value=80.0,
        stock_weight=0.8,
        cash_weight=0.2,
        shares=100.0,
        nav=1.0,
        cash_flow=0.0,
        share_change=0.0,
        details={
            "cash_flow_basis": NavCalculator.build_cash_flow_basis(
                nav_date=date(2026, 3, 19),
                last_nav=None,
                daily_cash_flow=0,
                gap_cash_flow=0,
                cash_flow_dataset=dataset,
            ),
            "finality": {
                "version": 1,
                "status": "manual",
                "nav_date": "2026-03-19",
                "valuation_as_of": None,
                "writer": "nav-record",
                "write_reason": "test",
            },
        },
    )

    NavCalculator.assert_nav_invariants(
        nav_record=nav,
        cash_flow_dataset=dataset,
        require_finality=True,
    )

    nav.details["cash_flow_basis"]["dataset_full_fingerprint"] = "drift"
    with pytest.raises(ValueError, match="full_fingerprint"):
        NavCalculator.assert_nav_invariants(
            nav_record=nav,
            cash_flow_dataset=dataset,
            require_finality=True,
        )

    nav.details["cash_flow_basis"]["dataset_full_fingerprint"] = "full"
    nav.details["finality"]["writer"] = "daily-nav-job"
    with pytest.raises(ValueError, match="writer/status"):
        NavCalculator.assert_nav_invariants(
            nav_record=nav,
            cash_flow_dataset=dataset,
            require_finality=True,
        )

    nav.details["finality"]["writer"] = "nav-record"
    nav.details["finality"]["valuation_as_of"] = "not-an-iso-datetime"
    with pytest.raises(ValueError, match="invalid_valuation_as_of"):
        NavCalculator.assert_nav_invariants(
            nav_record=nav,
            cash_flow_dataset=dataset,
            require_finality=True,
        )


@pytest.mark.parametrize(
    "values,error",
    [
        (("NaN", 1, 0), "finite"),
        ((100, 40, 50), "decomposition mismatch"),
        (("100.005", "50.004", "50.001"), "persisted money precision"),
    ],
)
def test_closed_nav_target_rejects_nonfinite_or_unstable_decomposition(values, error):
    with pytest.raises(ValueError, match=error):
        ClosedNavTarget.build(
            total_value=values[0],
            cash_value=values[1],
            non_cash_value=values[2],
        )


def test_closed_nav_target_owns_fixed_share_and_nav_values():
    target = ClosedNavTarget.build(
        total_value=100,
        cash_value=40,
        non_cash_value=60,
    )

    assert target.shares == 0
    assert target.nav == 1


def test_nav_metrics_rejects_incomplete_previous_nav_evidence():
    previous = NAVHistory(
        date=date(2025, 3, 13),
        account="a",
        total_value=1000.0,
        nav=1.0,
        shares=None,
    )

    with pytest.raises(ValueError, match="previous NAV requires nav and shares"):
        NavCalculator.calc_nav_metrics(
            today=date(2025, 3, 14),
            total_value=1000.0,
            yesterday_nav=previous,
            prev_year_end_nav=None,
            prev_month_end_nav=None,
            last_nav=previous,
            yearly_data={},
            daily_cash_flow=0.0,
            monthly_cash_flow=0.0,
            yearly_cash_flow=0.0,
            cumulative_cash_flow=0.0,
            start_year=2025,
            initial_value=1000.0,
        )
