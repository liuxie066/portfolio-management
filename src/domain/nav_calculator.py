"""Canonical NAV calculation, projection, and invariant helpers."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Mapping, Optional

from src.domain.nav_finality_contract import (
    finality_validation_reason,
)
from src.models import NAVHistory, PortfolioValuation


@dataclass(frozen=True)
class NavValuationProjection:
    """Canonical mapping from runtime valuation facts to persisted NAV values.

    ``PortfolioValuation.stock_value_cny`` is the equity component.  The
    persisted compatibility column ``stock_value`` is the complete non-cash
    value and therefore already includes ``fund_value``.
    """

    cash_value: Decimal
    non_cash_value: Decimal
    fund_value: Decimal
    total_value: Decimal
    cn_exposure_value: Decimal
    us_exposure_value: Decimal
    hk_exposure_value: Decimal
    stock_weight: Decimal
    cash_weight: Decimal


@dataclass(frozen=True)
class ClosedNavTarget:
    """Validated, exactly decomposed target for a compatibility CLOSED row."""

    total_value: Decimal
    cash_value: Decimal
    non_cash_value: Decimal
    shares: Decimal = Decimal("0.00")
    nav: Decimal = Decimal("1.000000")

    @classmethod
    def build(
        cls,
        *,
        total_value: Any,
        cash_value: Any,
        non_cash_value: Any,
    ) -> "ClosedNavTarget":
        values = {
            "total_value": total_value,
            "cash_value": cash_value,
            "non_cash_value": non_cash_value,
        }
        parsed: dict[str, Decimal] = {}
        for field, value in values.items():
            if value is None:
                raise ValueError(f"CLOSED NAV {field} is required")
            try:
                item = Decimal(str(value))
            except Exception as exc:
                raise ValueError("CLOSED NAV values must be finite numbers") from exc
            if not item.is_finite():
                raise ValueError("CLOSED NAV values must be finite numbers")
            parsed[field] = item

        if parsed["total_value"] != parsed["cash_value"] + parsed["non_cash_value"]:
            raise ValueError(
                "CLOSED NAV decomposition mismatch: "
                "total_value must equal cash_value + non_cash_value"
            )

        quantized = {
            field: NavCalculator.quantize_money(value)
            for field, value in parsed.items()
        }
        if quantized["total_value"] != quantized["cash_value"] + quantized["non_cash_value"]:
            raise ValueError(
                "CLOSED NAV decomposition is not stable at persisted money precision"
            )
        if quantized["total_value"] <= 0:
            raise ValueError("CLOSED NAV total_value must be > 0")
        return cls(**quantized)


class NavCalculator:
    MONEY_QUANT = Decimal("0.01")
    NAV_QUANT = Decimal("0.000001")
    WEIGHT_QUANT = Decimal("0.000001")
    CASH_FLOW_BASIS_VERSION = 1

    @staticmethod
    def to_decimal(value: Any) -> Decimal:
        if value is None:
            return Decimal("0")
        if isinstance(value, Decimal):
            return value
        return Decimal(str(value))

    @classmethod
    def quantize_money(cls, value: Any) -> Decimal:
        return cls.to_decimal(value).quantize(cls.MONEY_QUANT, rounding=ROUND_HALF_UP)

    @classmethod
    def quantize_nav(cls, value: Any) -> Decimal:
        return cls.to_decimal(value).quantize(cls.NAV_QUANT, rounding=ROUND_HALF_UP)

    @classmethod
    def quantize_weight(cls, value: Any) -> Decimal:
        return cls.to_decimal(value).quantize(cls.WEIGHT_QUANT, rounding=ROUND_HALF_UP)

    @classmethod
    def _finite_decimal(cls, value: Any, *, field: str) -> Decimal:
        try:
            parsed = Decimal(str(value))
        except Exception as exc:
            raise ValueError(f"{field} must be a finite number") from exc
        if not parsed.is_finite():
            raise ValueError(f"{field} must be a finite number")
        return parsed

    @classmethod
    def project_valuation(cls, valuation: PortfolioValuation) -> NavValuationProjection:
        """Return the sole runtime-to-persisted valuation projection."""

        observed_total = cls._finite_decimal(
            valuation.total_value_cny,
            field="total_value_cny",
        )
        cash = cls._finite_decimal(valuation.cash_value_cny, field="cash_value_cny")
        equity = cls._finite_decimal(valuation.stock_value_cny, field="stock_value_cny")
        fund = cls._finite_decimal(valuation.fund_value_cny, field="fund_value_cny")
        persisted_cash = cls.quantize_money(cash)
        persisted_non_cash = cls.quantize_money(equity + fund)
        persisted_fund = cls.quantize_money(fund)
        persisted_total = persisted_cash + persisted_non_cash
        if cls.quantize_money(observed_total) != persisted_total:
            raise ValueError(
                "valuation decomposition mismatch: total_value_cny must equal "
                "cash_value_cny + stock_value_cny + fund_value_cny"
            )
        cn = cls._finite_decimal(valuation.cn_asset_value, field="cn_asset_value")
        us = cls._finite_decimal(valuation.us_asset_value, field="us_asset_value")
        hk = cls._finite_decimal(valuation.hk_asset_value, field="hk_asset_value")
        stock_weight = (
            persisted_non_cash / persisted_total
            if persisted_total > 0
            else Decimal("0")
        )
        cash_weight = (
            persisted_cash / persisted_total
            if persisted_total > 0
            else Decimal("0")
        )
        return NavValuationProjection(
            cash_value=persisted_cash,
            non_cash_value=persisted_non_cash,
            fund_value=persisted_fund,
            total_value=persisted_total,
            cn_exposure_value=cls.quantize_money(cn),
            us_exposure_value=cls.quantize_money(us),
            hk_exposure_value=cls.quantize_money(hk),
            stock_weight=cls.quantize_weight(stock_weight),
            cash_weight=cls.quantize_weight(cash_weight),
        )

    @classmethod
    def build_cash_flow_basis(
        cls,
        *,
        nav_date: date,
        last_nav: Optional[NAVHistory],
        daily_cash_flow: Any,
        gap_cash_flow: Any,
        cash_flow_dataset: Any = None,
    ) -> dict[str, Any]:
        """Describe the daily column and gap calculation from one dataset."""

        previous_date = getattr(last_nav, "date", None)
        if isinstance(previous_date, datetime):
            previous_date = previous_date.date()
        dataset_details = (
            cash_flow_dataset.details()
            if cash_flow_dataset is not None and callable(getattr(cash_flow_dataset, "details", None))
            else {}
        )
        return {
            "version": cls.CASH_FLOW_BASIS_VERSION,
            "cash_flow_column_semantics": "daily",
            "daily_cash_flow": float(cls.quantize_money(daily_cash_flow)),
            "gap_cash_flow": float(cls.quantize_money(gap_cash_flow)),
            "previous_nav_date": previous_date.isoformat() if previous_date else None,
            "gap_window": {
                "start": previous_date.isoformat() if previous_date else nav_date.isoformat(),
                "end": nav_date.isoformat(),
                "start_inclusive": previous_date is None,
                "end_inclusive": True,
            },
            "dataset_contract_version": dataset_details.get("contract_version"),
            "dataset_financial_fingerprint": dataset_details.get("financial_fingerprint"),
            "dataset_full_fingerprint": dataset_details.get("full_fingerprint"),
        }

    @classmethod
    def calc_period_return(cls, current_value: float, base_value: Optional[float]) -> float:
        if base_value is None:
            return 0.0
        current_dec = cls.to_decimal(current_value)
        base_dec = cls.to_decimal(base_value)
        if base_dec <= 0:
            return 0.0
        return float((current_dec - base_dec) / base_dec)

    @classmethod
    def calc_mtd_nav_change(cls, nav: float, prev_month_end_nav) -> Optional[float]:
        base_nav = prev_month_end_nav.nav if prev_month_end_nav else None
        if base_nav is None or base_nav <= 0:
            return None
        return cls.calc_period_return(nav, base_nav)

    @classmethod
    def calc_ytd_nav_change(cls, nav: float, prev_year_end_nav) -> Optional[float]:
        base_nav = prev_year_end_nav.nav if prev_year_end_nav else None
        if base_nav is None or base_nav <= 0:
            return None
        return cls.calc_period_return(nav, base_nav)

    @classmethod
    def calc_mtd_pnl(cls, total_value: float, prev_month_end_nav, monthly_cash_flow: float) -> Optional[float]:
        if not prev_month_end_nav:
            return None
        return float(
            cls.to_decimal(total_value)
            - cls.to_decimal(prev_month_end_nav.total_value)
            - cls.to_decimal(monthly_cash_flow)
        )

    @classmethod
    def calc_ytd_pnl(cls, total_value: float, prev_year_end_nav, yearly_cash_flow: float) -> Optional[float]:
        if not prev_year_end_nav:
            return None
        return float(
            cls.to_decimal(total_value)
            - cls.to_decimal(prev_year_end_nav.total_value)
            - cls.to_decimal(yearly_cash_flow)
        )

    @classmethod
    def calc_nav_metrics(
        cls,
        *,
        today,
        total_value,
        yesterday_nav,
        prev_year_end_nav,
        prev_month_end_nav,
        last_nav,
        yearly_data,
        daily_cash_flow,
        monthly_cash_flow,
        yearly_cash_flow,
        cumulative_cash_flow,
        start_year,
        initial_value: Optional[float],
        ytd_return_base_nav=None,
        mtd_return_base_nav=None,
        gap_cash_flow=None,
    ) -> dict:
        """Calculate shares, NAV deltas, PnL, and CAGR.

        Mutates ``yearly_data`` by filling ``nav_change`` and ``appreciation``
        to preserve the existing PortfolioManager contract.
        """
        cf_for_shares = gap_cash_flow if gap_cash_flow is not None else daily_cash_flow
        cf_for_shares_dec = cls.to_decimal(cf_for_shares)
        total_value_dec = cls.to_decimal(total_value)
        last_nav_nav_dec = None
        last_nav_shares_dec = None
        if last_nav is not None:
            if last_nav.nav is None or last_nav.shares is None:
                raise ValueError(
                    "historical_evidence_required: previous NAV requires nav and shares"
                )
            last_nav_nav_dec = cls._finite_decimal(
                last_nav.nav,
                field="previous_nav.nav",
            )
            last_nav_shares_dec = cls._finite_decimal(
                last_nav.shares,
                field="previous_nav.shares",
            )
            if last_nav_nav_dec <= 0 or last_nav_shares_dec < 0:
                raise ValueError(
                    "historical_evidence_required: previous NAV requires "
                    "nav > 0 and shares >= 0"
                )

        if last_nav is not None:
            shares_change_dec = cf_for_shares_dec / last_nav_nav_dec
            shares_dec = last_nav_shares_dec + shares_change_dec
        else:
            shares_change_dec = cf_for_shares_dec
            shares_dec = total_value_dec

        nav_dec = (total_value_dec / shares_dec) if shares_dec > 0 else Decimal("1.0")
        if shares_dec <= 0 and total_value_dec > 0:
            import logging
            logging.getLogger(__name__).warning(
                "NAV defaulted to 1.0: shares=%.2f but total_value=%.2f — possible data corruption",
                float(shares_dec), float(total_value_dec),
            )
        nav_dec = cls.quantize_nav(nav_dec)

        shares_change = float(shares_change_dec)
        shares = float(shares_dec)
        nav = float(nav_dec)

        month_base_nav = mtd_return_base_nav if mtd_return_base_nav is not None else prev_month_end_nav
        year_base_nav = ytd_return_base_nav if ytd_return_base_nav is not None else prev_year_end_nav
        month_nav_change = cls.calc_mtd_nav_change(nav, month_base_nav)
        year_nav_change = cls.calc_ytd_nav_change(nav, year_base_nav)

        for yd in yearly_data.values():
            base, e = yd["prev_end"], yd["end"]
            if e and base and base.nav is not None and base.nav > 0:
                yd["nav_change"] = cls.calc_period_return(e.nav, base.nav)
            else:
                yd["nav_change"] = None

        cumulative_nav_change = 0.0
        first_year_data = yearly_data.get(str(start_year))
        if first_year_data and first_year_data["prev_end"]:
            cumulative_nav_change = cls.calc_period_return(nav, first_year_data["prev_end"].nav)

        if yesterday_nav and yesterday_nav.date and (today - yesterday_nav.date).days == 1:
            daily_appreciation = float(total_value_dec - cls.to_decimal(yesterday_nav.total_value) - cf_for_shares_dec)
        else:
            daily_appreciation = None

        month_appreciation = cls.calc_mtd_pnl(total_value, prev_month_end_nav, monthly_cash_flow)
        year_appreciation = cls.calc_ytd_pnl(total_value, prev_year_end_nav, yearly_cash_flow)

        sorted_years = sorted(yearly_data.keys())
        for i, yr_str in enumerate(sorted_years):
            yd = yearly_data[yr_str]
            if i == 0:
                if yd["end"] and initial_value is not None:
                    yd["appreciation"] = yd["end"].total_value - initial_value - yd["cash_flow"]
                else:
                    yd["appreciation"] = None
            else:
                prev_yd = yearly_data[sorted_years[i - 1]]
                if yd["end"] and prev_yd["end"]:
                    yd["appreciation"] = yd["end"].total_value - prev_yd["end"].total_value - yd["cash_flow"]
                else:
                    yd["appreciation"] = None

        cumulative_appreciation = (total_value - initial_value - cumulative_cash_flow) if initial_value else 0.0

        cagr = 0.0
        if first_year_data and first_year_data["prev_end"] and first_year_data["prev_end"].nav > 0 and nav > 0:
            days_since_start = (today - first_year_data["prev_end"].date).days
            years_since_start = days_since_start / 365.25
            if years_since_start > 0:
                cagr = (nav / first_year_data["prev_end"].nav) ** (1 / years_since_start) - 1

        return {
            "shares": shares,
            "shares_change": shares_change,
            "nav": nav,
            "month_nav_change": month_nav_change,
            "year_nav_change": year_nav_change,
            "cumulative_nav_change": cumulative_nav_change,
            "daily_appreciation": daily_appreciation,
            "month_appreciation": month_appreciation,
            "year_appreciation": year_appreciation,
            "cumulative_appreciation": cumulative_appreciation,
            "initial_value": initial_value,
            "first_year_data": first_year_data,
            "cagr": cagr,
        }

    @classmethod
    def approx_equal(cls, a: Optional[float], b: Optional[float], tolerance: float = 1e-6) -> bool:
        if a is None or b is None:
            return a is b
        return abs(cls.to_decimal(a) - cls.to_decimal(b)) <= cls.to_decimal(tolerance)

    @classmethod
    def approx_equal_quantized(cls, a: Optional[float], b: Optional[float], quantizer, *, tolerance: float = 0.0) -> bool:
        if a is None or b is None:
            return a is b
        qa = quantizer(a)
        qb = quantizer(b)
        if tolerance and tolerance > 0:
            return cls.approx_equal(float(qa), float(qb), tolerance=tolerance)
        return qa == qb

    @classmethod
    def money_equal(cls, a: Optional[float], b: Optional[float]) -> bool:
        if a is None or b is None:
            return a is b
        return cls.quantize_money(a) == cls.quantize_money(b)

    @classmethod
    def nav_equal(cls, a: Optional[float], b: Optional[float]) -> bool:
        if a is None or b is None:
            return a is b
        return cls.quantize_nav(a) == cls.quantize_nav(b)

    @classmethod
    def assert_nav_invariants(
        cls,
        *,
        nav_record: NAVHistory,
        last_nav=None,
        prev_month_end_nav=None,
        prev_year_end_nav=None,
        mtd_return_base_nav=None,
        ytd_return_base_nav=None,
        daily_cash_flow: float = 0.0,
        monthly_cash_flow: float = 0.0,
        yearly_cash_flow: float = 0.0,
        gap_cash_flow: Optional[float] = None,
        initial_value: Optional[float] = None,
        cumulative_cash_flow: float = 0.0,
        cash_flow_dataset: Any = None,
        require_finality: Optional[bool] = None,
    ) -> None:
        """Assert the final persisted/runtime NAV contract after all mapping."""

        errors: list[str] = []
        details = nav_record.details if isinstance(nav_record.details, dict) else {}
        is_closed = str(details.get("status") or "").upper() == "CLOSED"

        def require_reference_value(
            reference: Any,
            field: str,
            label: str,
            *,
            positive: bool = False,
            nonnegative: bool = False,
        ) -> None:
            if reference is None:
                return
            value = getattr(reference, field, None)
            if value is None:
                errors.append(f"{label}.{field} 缺失")
                return
            try:
                parsed = cls._finite_decimal(value, field=f"{label}.{field}")
            except ValueError:
                errors.append(f"{label}.{field} 必须是有限数")
                return
            if positive and parsed <= 0:
                errors.append(f"{label}.{field} 必须 > 0")
            if nonnegative and parsed < 0:
                errors.append(f"{label}.{field} 必须 >= 0")

        if not is_closed:
            require_reference_value(last_nav, "total_value", "last_nav")
            require_reference_value(last_nav, "nav", "last_nav", positive=True)
            require_reference_value(
                last_nav,
                "shares",
                "last_nav",
                nonnegative=True,
            )
            require_reference_value(
                prev_month_end_nav,
                "total_value",
                "prev_month_end_nav",
            )
            require_reference_value(
                prev_year_end_nav,
                "total_value",
                "prev_year_end_nav",
            )
            require_reference_value(
                mtd_return_base_nav,
                "nav",
                "mtd_return_base_nav",
                positive=True,
            )
            require_reference_value(
                ytd_return_base_nav,
                "nav",
                "ytd_return_base_nav",
                positive=True,
            )

        finite_fields = (
            "total_value", "cash_value", "stock_value", "fund_value",
            "cn_stock_value", "us_stock_value", "hk_stock_value",
            "stock_weight", "cash_weight", "shares", "nav", "cash_flow",
            "share_change", "pnl", "mtd_nav_change", "ytd_nav_change",
            "mtd_pnl", "ytd_pnl",
        )
        for field in finite_fields:
            value = getattr(nav_record, field, None)
            if value is None:
                continue
            try:
                if not cls.to_decimal(value).is_finite():
                    errors.append(f"{field} 必须是有限数")
            except Exception:
                errors.append(f"{field} 必须是有限数")

        if nav_record.cash_value is None or nav_record.stock_value is None:
            errors.append("cash_value/stock_value 缺失（必填）")
        else:
            expected_total = float(cls.quantize_money(cls.to_decimal(nav_record.stock_value) + cls.to_decimal(nav_record.cash_value)))
            if not cls.money_equal(nav_record.total_value, expected_total):
                errors.append(f"total_value 不等于 stock_value + cash_value: {nav_record.total_value} != {expected_total}")

        if (
            nav_record.fund_value is not None
            and nav_record.stock_value is not None
            and nav_record.fund_value >= 0
            and nav_record.stock_value >= 0
            and cls.quantize_money(nav_record.fund_value) > cls.quantize_money(nav_record.stock_value)
        ):
            errors.append(
                "fund_value 必须是已持久化 stock_value（非现金）的子集"
            )

        if nav_record.total_value is not None and nav_record.total_value > 0:
            if nav_record.stock_weight is None or nav_record.cash_weight is None:
                errors.append("stock_weight/cash_weight 缺失（必填）")
            elif nav_record.stock_value is not None and nav_record.cash_value is not None:
                expected_stock_weight = float(
                    cls.quantize_weight(
                        cls.to_decimal(nav_record.stock_value)
                        / cls.to_decimal(nav_record.total_value)
                    )
                )
                expected_cash_weight = float(
                    cls.quantize_weight(
                        cls.to_decimal(nav_record.cash_value)
                        / cls.to_decimal(nav_record.total_value)
                    )
                )
                if not cls.approx_equal(nav_record.stock_weight, expected_stock_weight, tolerance=1e-6):
                    errors.append(
                        f"stock_weight 不一致: {nav_record.stock_weight} != {expected_stock_weight}"
                    )
                if not cls.approx_equal(nav_record.cash_weight, expected_cash_weight, tolerance=1e-6):
                    errors.append(
                        f"cash_weight 不一致: {nav_record.cash_weight} != {expected_cash_weight}"
                    )
                weights_sum = nav_record.stock_weight + nav_record.cash_weight
                if not cls.approx_equal(weights_sum, 1.0, tolerance=1e-4):
                    errors.append(f"stock_weight + cash_weight 不接近 1: {weights_sum}")

        if is_closed:
            if not cls.money_equal(nav_record.shares, 0.0):
                errors.append("CLOSED NAV shares 必须为 0")
            if not cls.nav_equal(nav_record.nav, 1.0):
                errors.append("CLOSED NAV nav 必须为 1")
            if not cls.money_equal(nav_record.share_change, 0.0):
                errors.append("CLOSED NAV share_change 必须为 0")
        elif nav_record.shares and nav_record.shares > 0 and nav_record.nav is not None:
            expected_nav = float(cls.quantize_nav(cls.to_decimal(nav_record.total_value) / cls.to_decimal(nav_record.shares)))
            if not cls.approx_equal(nav_record.nav, expected_nav, tolerance=1e-6):
                errors.append(f"nav 不等于 total_value / shares: {nav_record.nav} != {expected_nav}")
        else:
            errors.append("非 CLOSED NAV 要求 shares > 0 且 nav 存在")

        effective_cash_flow = gap_cash_flow if gap_cash_flow is not None else daily_cash_flow
        if not cls.money_equal(nav_record.cash_flow, daily_cash_flow):
            errors.append(
                f"cash_flow 列必须是当日资金流: {nav_record.cash_flow} != {daily_cash_flow}"
            )
        if not is_closed:
            if (
                last_nav is not None
                and last_nav.nav is not None
                and last_nav.nav > 0
                and last_nav.shares is not None
            ):
                expected_share_change = float(
                    cls.quantize_money(
                        cls.to_decimal(effective_cash_flow) / cls.to_decimal(last_nav.nav)
                    )
                )
                expected_shares = float(
                    cls.quantize_money(
                        cls.to_decimal(last_nav.shares or 0)
                        + cls.to_decimal(effective_cash_flow) / cls.to_decimal(last_nav.nav)
                    )
                )
                if not cls.money_equal(nav_record.share_change, expected_share_change):
                    errors.append(
                        f"share_change 与 gap cash flow 不一致: "
                        f"{nav_record.share_change} != {expected_share_change}"
                    )
                if not cls.money_equal(nav_record.shares, expected_shares):
                    errors.append(
                        f"shares 与上期份额及 gap cash flow 不一致: "
                        f"{nav_record.shares} != {expected_shares}"
                    )
            elif last_nav is None:
                if not cls.money_equal(nav_record.share_change, effective_cash_flow):
                    errors.append("首条 NAV share_change 应等于 gap cash flow")
                if not cls.money_equal(nav_record.shares, nav_record.total_value):
                    errors.append("首条 NAV shares 应等于 total_value")

        expected_pnl = None
        if (
            not is_closed
            and last_nav is not None
            and getattr(last_nav, "date", None) is not None
            and (nav_record.date - last_nav.date).days == 1
        ):
            expected_pnl = float(
                cls.quantize_money(
                    cls.to_decimal(nav_record.total_value)
                    - cls.to_decimal(last_nav.total_value)
                    - cls.to_decimal(effective_cash_flow)
                )
            )
        strict_final_record = bool(require_finality) or details.get("finality") is not None
        if (
            nav_record.pnl is not None or strict_final_record
        ) and not cls.money_equal(nav_record.pnl, expected_pnl):
            errors.append(f"pnl 不一致: {nav_record.pnl} != {expected_pnl}")

        basis = details.get("cash_flow_basis")
        if isinstance(basis, Mapping):
            expected_previous_date = getattr(last_nav, "date", None)
            if isinstance(expected_previous_date, datetime):
                expected_previous_date = expected_previous_date.date()
            expected_previous_text = (
                expected_previous_date.isoformat() if expected_previous_date else None
            )
            if basis.get("version") != cls.CASH_FLOW_BASIS_VERSION:
                errors.append("details.cash_flow_basis.version 不一致")
            if basis.get("cash_flow_column_semantics") != "daily":
                errors.append("details.cash_flow_basis 未声明 cash_flow=daily")
            if not cls.money_equal(basis.get("daily_cash_flow"), daily_cash_flow):
                errors.append("details.cash_flow_basis.daily_cash_flow 不一致")
            if not cls.money_equal(basis.get("gap_cash_flow"), effective_cash_flow):
                errors.append("details.cash_flow_basis.gap_cash_flow 不一致")
            if basis.get("previous_nav_date") != expected_previous_text:
                errors.append("details.cash_flow_basis.previous_nav_date 不一致")
            gap_window = basis.get("gap_window") or {}
            expected_start = expected_previous_text or nav_record.date.isoformat()
            if (
                gap_window.get("start") != expected_start
                or gap_window.get("end") != nav_record.date.isoformat()
                or gap_window.get("start_inclusive") is not (expected_previous_date is None)
                or gap_window.get("end_inclusive") is not True
            ):
                errors.append("details.cash_flow_basis.gap_window 不一致")
            if cash_flow_dataset is not None:
                dataset_details = cash_flow_dataset.details()
                if basis.get("dataset_contract_version") != dataset_details.get("contract_version"):
                    errors.append("details.cash_flow_basis dataset contract_version 不一致")
                if basis.get("dataset_financial_fingerprint") != dataset_details.get("financial_fingerprint"):
                    errors.append("details.cash_flow_basis dataset fingerprint 不一致")
                if basis.get("dataset_full_fingerprint") != dataset_details.get("full_fingerprint"):
                    errors.append("details.cash_flow_basis dataset full_fingerprint 不一致")
        elif require_finality or cash_flow_dataset is not None:
            errors.append("details.cash_flow_basis 缺失")

        month_base_nav = mtd_return_base_nav if mtd_return_base_nav is not None else prev_month_end_nav
        year_base_nav = ytd_return_base_nav if ytd_return_base_nav is not None else prev_year_end_nav

        expected_mtd = cls.calc_mtd_nav_change(nav_record.nav, month_base_nav) if nav_record.nav is not None else None
        if not cls.approx_equal_quantized(nav_record.mtd_nav_change, expected_mtd, cls.quantize_nav):
            errors.append(f"mtd_nav_change 不一致: {nav_record.mtd_nav_change} != {expected_mtd}")

        expected_ytd = cls.calc_ytd_nav_change(nav_record.nav, year_base_nav) if nav_record.nav is not None else None
        if not cls.approx_equal_quantized(nav_record.ytd_nav_change, expected_ytd, cls.quantize_nav):
            errors.append(f"ytd_nav_change 不一致: {nav_record.ytd_nav_change} != {expected_ytd}")

        expected_mtd_pnl = cls.calc_mtd_pnl(nav_record.total_value, prev_month_end_nav, monthly_cash_flow)
        if expected_mtd_pnl is not None:
            expected_mtd_pnl = float(cls.quantize_money(expected_mtd_pnl))
        if not cls.money_equal(nav_record.mtd_pnl, expected_mtd_pnl):
            errors.append(f"mtd_pnl 不一致: {nav_record.mtd_pnl} != {expected_mtd_pnl}")

        expected_ytd_pnl = cls.calc_ytd_pnl(nav_record.total_value, prev_year_end_nav, yearly_cash_flow)
        if expected_ytd_pnl is not None:
            expected_ytd_pnl = float(cls.quantize_money(expected_ytd_pnl))
        if not cls.money_equal(nav_record.ytd_pnl, expected_ytd_pnl):
            errors.append(f"ytd_pnl 不一致: {nav_record.ytd_pnl} != {expected_ytd_pnl}")

        if initial_value is not None and nav_record.details is not None:
            expected_cum_pnl = float(
                cls.quantize_money(
                    cls.to_decimal(nav_record.total_value)
                    - cls.to_decimal(initial_value)
                    - cls.to_decimal(cumulative_cash_flow)
                )
            )
            stored_cum_pnl = nav_record.details.get("cumulative_appreciation")
            if stored_cum_pnl is not None and not cls.money_equal(stored_cum_pnl, expected_cum_pnl):
                errors.append(f"details.cumulative_appreciation 不一致: {stored_cum_pnl} != {expected_cum_pnl}")

        finality = details.get("finality")
        should_require_finality = (
            bool(require_finality)
            if require_finality is not None
            else finality is not None
        )
        if should_require_finality:
            reason = finality_validation_reason(
                finality,
                target_date=nav_record.date,
            )
            if reason == "writer_status_mismatch":
                errors.append("details.finality writer/status 不一致")
            elif reason is not None:
                errors.append(f"details.finality 无效: {reason}")
            else:
                if is_closed and finality.get("status") != "closed":
                    errors.append("CLOSED NAV finality.status 必须为 closed")

        if errors:
            raise ValueError("NAV 记录自校验失败: " + " | ".join(errors))

    @classmethod
    def validate_nav_record(cls, **kwargs: Any) -> None:
        """Compatibility alias for the canonical final invariant assertion."""

        cls.assert_nav_invariants(**kwargs)

    @classmethod
    def build_nav_record(
        cls,
        *,
        today,
        account,
        valuation,
        stock_value,
        cash_value,
        total_value,
        stock_ratio,
        cash_ratio,
        daily_cash_flow,
        monthly_cash_flow,
        yearly_cash_flow,
        yearly_data,
        cumulative_cash_flow,
        start_year,
        shares,
        shares_change,
        nav,
        month_nav_change,
        year_nav_change,
        cumulative_nav_change,
        daily_appreciation,
        month_appreciation,
        year_appreciation,
        cumulative_appreciation,
        initial_value,
        first_year_data,
        cagr=0.0,
    ) -> NAVHistory:
        details = {
            "monthly_cash_flow": float(cls.quantize_money(monthly_cash_flow)),
            "year_cash_flow": float(cls.quantize_money(yearly_cash_flow)),
            "cumulative_nav_change": float(cls.quantize_nav(cumulative_nav_change)),
            "cumulative_appreciation": float(cls.quantize_money(cumulative_appreciation)),
            "initial_value": float(cls.quantize_money(initial_value)) if initial_value is not None else None,
            "cumulative_cash_flow": float(cls.quantize_money(cumulative_cash_flow)),
            "cagr": float(cls.quantize_nav(cagr)),
            "cagr_pct": float(cls.quantize_money(cagr * 100)),
        }
        for yr_str, yd in yearly_data.items():
            nav_change = yd.get("nav_change")
            appreciation = yd.get("appreciation")
            details[f"nav_change_{yr_str}"] = float(cls.quantize_nav(nav_change)) if nav_change is not None else None
            details[f"appreciation_{yr_str}"] = float(cls.quantize_money(appreciation)) if appreciation is not None else None
            details[f"cash_flow_{yr_str}"] = float(cls.quantize_money(yd.get("cash_flow", 0)))

        return NAVHistory(
            date=today,
            account=account,
            total_value=float(cls.quantize_money(total_value)),
            cash_value=float(cls.quantize_money(cash_value)),
            stock_value=float(cls.quantize_money(stock_value)),
            fund_value=float(cls.quantize_money(valuation.fund_value_cny)),
            cn_stock_value=float(cls.quantize_money(valuation.cn_asset_value)),
            us_stock_value=float(cls.quantize_money(valuation.us_asset_value)),
            hk_stock_value=float(cls.quantize_money(valuation.hk_asset_value)),
            stock_weight=float(cls.quantize_weight(stock_ratio)),
            cash_weight=float(cls.quantize_weight(cash_ratio)),
            shares=float(cls.quantize_money(shares)),
            nav=float(cls.quantize_nav(nav)),
            cash_flow=float(cls.quantize_money(daily_cash_flow)),
            share_change=float(cls.quantize_money(shares_change)),
            mtd_nav_change=float(cls.quantize_nav(month_nav_change)) if month_nav_change is not None else None,
            ytd_nav_change=float(cls.quantize_nav(year_nav_change)) if year_nav_change is not None else None,
            pnl=float(cls.quantize_money(daily_appreciation)) if daily_appreciation is not None else None,
            mtd_pnl=float(cls.quantize_money(month_appreciation)) if month_appreciation is not None else None,
            ytd_pnl=float(cls.quantize_money(year_appreciation)) if year_appreciation is not None else None,
            details=details,
        )
