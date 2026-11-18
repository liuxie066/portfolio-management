from datetime import date
from types import SimpleNamespace

import pytest

from src.domain.nav.performance import calc_month_return, calc_year_return


def nav(day, value):
    return SimpleNamespace(date=date.fromisoformat(day), nav=value)


@pytest.mark.parametrize(("calculator", "period"), [(calc_month_return, "2026-01"), (calc_year_return, "2026")])
def test_unsorted_generator_preserves_first_duplicate_and_previous_period_anchor(calculator, period):
    rows = [nav("2026-01-30", 1.2), nav("2025-12-31", 1), nav("2026-01-30", 99),
            nav("2025-12-31", 88), nav("2026-01-02", 1.1), nav("2027-01-01", 77)]
    result = calculator(period, navs=iter(rows))
    assert result["start_nav"] == 1
    assert result["end_nav"] == 1.2
    assert result["return_pct"] == pytest.approx(20)
    assert result["start_date"] == "2025-12-31"
    assert rows[0].nav == 1.2


def test_leap_month_and_missing_previous_period_use_first_observed_row():
    result = calc_month_return("2024-02", navs=[nav("2024-02-29", 1.2), nav("2024-02-02", 1)])
    assert result["base"] == "月初"
    assert result["return_pct"] == pytest.approx(20)
    assert calc_year_return("2024", navs=[nav("2024-02-29", 1.2)])["return_pct"] == 0


@pytest.mark.parametrize("period", ["2026-1", "2026-13", "garbage", "2026-00", "2026-01-01"])
def test_noncanonical_or_empty_month_is_unavailable(period):
    assert calc_month_return(period, navs=[nav("2026-01-01", 1)])["success"] is False


@pytest.mark.parametrize("period", ["02026", " 2026", "2026-01", "garbage", "0", "10000"])
def test_noncanonical_or_empty_year_is_unavailable(period):
    assert calc_year_return(period, navs=[nav("2026-01-01", 1)])["success"] is False
