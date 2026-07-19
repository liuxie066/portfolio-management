from __future__ import annotations

from src.pricing.classifier import (
    canonicalize_pricing_code,
    get_exchange_prefix,
    get_type_hints_from_name,
    is_etf,
    is_otc_fund,
    normalize_code_with_name,
)


def test_pricing_classifier_outputs_are_stable():
    assert normalize_code_with_name("600519", "贵州茅台股份") == "SH600519"
    assert get_type_hints_from_name("华夏成长混合基金") == {
        "is_fund": True,
        "is_etf": False,
        "is_stock": False,
        "is_cash": False,
    }
    assert is_etf("510300") is True
    assert is_otc_fund("004001") is True
    assert get_exchange_prefix("510300") == "sh"


def test_pricing_classifier_keeps_ambiguous_a_stock_codes_out_of_funds():
    assert is_otc_fund("000001") is False
    assert is_otc_fund("300750") is False
    assert is_otc_fund("004001") is True
    assert is_etf("510300") is True


def test_pricing_code_canonicalization_strips_only_known_terminal_suffixes():
    assert canonicalize_pricing_code("FUTU.US") == "FUTU"
    assert canonicalize_pricing_code("0700.HK") == "00700"
    assert canonicalize_pricing_code("600519.SH") == "600519"
    assert canonicalize_pricing_code("000001.SZ") == "000001"
    assert canonicalize_pricing_code("BRK.B") == "BRK.B"
