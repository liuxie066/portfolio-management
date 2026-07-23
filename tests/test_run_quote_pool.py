from __future__ import annotations

import pytest

from src.app.run_quote_pool import RunQuotePool
from src.models import AssetType


def _us_quote(code: str, price: float = 100.0) -> dict:
    return {
        "code": code,
        "price": price,
        "currency": "USD",
        "cny_price": price * 7,
        "source": "test-provider",
        "source_chain": ["test-provider"],
    }


def test_pool_reuses_earlier_account_quotes_and_fetches_only_misses():
    calls = []

    def fetch_batch(codes, **_kwargs):
        calls.append(list(codes))
        return {code: _us_quote(code) for code in codes}

    pool = RunQuotePool()
    first_codes = ["FUTU.US", "PDD.US", "TCOM.US"]
    first = pool.fetch_batch(
        first_codes,
        fetch_batch=fetch_batch,
        asset_type_map={code: AssetType.US_STOCK for code in first_codes},
    )
    second_codes = ["FUTU", "PDD", "TCOM", "SPY", "BABA"]
    second = pool.fetch_batch(
        second_codes,
        fetch_batch=fetch_batch,
        asset_type_map={code: AssetType.US_STOCK for code in second_codes},
    )

    assert calls == [first_codes, ["SPY", "BABA"]]
    assert all(not payload.get("is_from_run_pool") for payload in first.values())
    assert all(second[code]["is_from_run_pool"] is True for code in ("FUTU", "PDD", "TCOM"))
    assert all(not second[code].get("is_from_run_pool") for code in ("SPY", "BABA"))
    assert pool.summary() == {
        "unique_requested": 5,
        "fetch_attempted": 5,
        "fetcher_resolved": 5,
        "run_reused": 3,
        "retried": 0,
        "failed_unique": 0,
    }


def test_pool_normalizes_aliases_and_returns_independent_payload_copies():
    calls = []

    def fetch_batch(codes, **_kwargs):
        calls.append(list(codes))
        return {codes[0]: _us_quote(codes[0])}

    pool = RunQuotePool()
    first = pool.fetch_batch(
        ["BABA.US"],
        fetch_batch=fetch_batch,
        asset_type_map={"BABA.US": AssetType.US_STOCK},
    )
    first["BABA.US"]["source_chain"].append("mutated-by-caller")

    second = pool.fetch_batch(
        ["BABA"],
        fetch_batch=fetch_batch,
        asset_type_map={"BABA": AssetType.US_STOCK},
    )

    assert calls == [["BABA.US"]]
    assert second["BABA"]["code"] == "BABA"
    assert second["BABA"]["source_chain"] == ["test-provider"]
    assert second["BABA"]["is_from_run_pool"] is True


def test_pool_partitions_same_canonical_code_across_markets_in_one_call():
    calls = []

    def fetch_batch(codes, **_kwargs):
        calls.append(list(codes))
        code = codes[0]
        if code.endswith(".US"):
            return {code: _us_quote(code, price=20)}
        return {
            code: {
                "code": code,
                "price": 10,
                "currency": "CNY",
                "cny_price": 10,
                "source": "cn-provider",
            }
        }

    pool = RunQuotePool()
    result = pool.fetch_batch(
        ["510300.US", "510300.SH"],
        fetch_batch=fetch_batch,
        asset_type_map={
            "510300.US": AssetType.US_STOCK,
            "510300.SH": AssetType.A_STOCK,
        },
    )

    assert calls == [["510300.US"], ["510300.SH"]]
    assert result["510300.US"]["price"] == 20
    assert result["510300.US"]["currency"] == "USD"
    assert result["510300.SH"]["price"] == 10
    assert result["510300.SH"]["currency"] == "CNY"


def test_pool_retries_failure_once_then_reuses_success():
    calls = []

    def fetch_batch(codes, **_kwargs):
        calls.append(list(codes))
        if len(calls) == 1:
            return {}
        return {codes[0]: _us_quote(codes[0])}

    pool = RunQuotePool()
    asset_types = {"BABA": AssetType.US_STOCK}

    assert pool.fetch_batch(["BABA"], fetch_batch=fetch_batch, asset_type_map=asset_types) == {}
    assert "BABA" in pool.fetch_batch(["BABA"], fetch_batch=fetch_batch, asset_type_map=asset_types)
    third = pool.fetch_batch(["BABA"], fetch_batch=fetch_batch, asset_type_map=asset_types)

    assert calls == [["BABA"], ["BABA"]]
    assert third["BABA"]["is_from_run_pool"] is True
    assert pool.summary() == {
        "unique_requested": 1,
        "fetch_attempted": 1,
        "fetcher_resolved": 1,
        "run_reused": 1,
        "retried": 1,
        "failed_unique": 0,
    }


def test_pool_does_not_admit_invalid_payload_and_caps_failed_attempts():
    calls = []

    def fetch_batch(codes, **_kwargs):
        calls.append(list(codes))
        return {codes[0]: {"price": float("nan"), "currency": "USD", "cny_price": 700}}

    pool = RunQuotePool()
    asset_types = {"PDD": AssetType.US_STOCK}
    for _ in range(3):
        assert pool.fetch_batch(["PDD"], fetch_batch=fetch_batch, asset_type_map=asset_types) == {}

    assert calls == [["PDD"], ["PDD"]]
    assert pool.summary() == {
        "unique_requested": 1,
        "fetch_attempted": 1,
        "fetcher_resolved": 0,
        "run_reused": 0,
        "retried": 1,
        "failed_unique": 1,
    }


def test_separate_pools_share_no_quotes():
    calls = []

    def fetch_batch(codes, **_kwargs):
        calls.append(list(codes))
        return {codes[0]: _us_quote(codes[0])}

    asset_types = {"SPY": AssetType.US_STOCK}
    first_pool = RunQuotePool()
    second_pool = RunQuotePool()

    first_pool.fetch_batch(["SPY"], fetch_batch=fetch_batch, asset_type_map=asset_types)
    first_pool.fetch_batch(["SPY"], fetch_batch=fetch_batch, asset_type_map=asset_types)
    second = second_pool.fetch_batch(["SPY"], fetch_batch=fetch_batch, asset_type_map=asset_types)

    assert calls == [["SPY"], ["SPY"]]
    assert second["SPY"].get("is_from_run_pool") is None
    assert first_pool.summary()["run_reused"] == 1
    assert second_pool.summary()["run_reused"] == 0


def test_pool_rejects_stale_hit_when_current_account_disallows_fallback():
    calls = []

    def fetch_batch(codes, **_kwargs):
        calls.append(list(codes))
        if len(calls) == 1:
            payload = _us_quote(codes[0])
            payload.update(source="cache_fallback", is_stale=True, is_from_cache=True)
            return {codes[0]: payload}
        return {}

    pool = RunQuotePool()
    asset_types = {"BABA": AssetType.US_STOCK}
    first = pool.fetch_batch(
        ["BABA"],
        fetch_batch=fetch_batch,
        asset_type_map=asset_types,
        accept_stale_when_closed=True,
    )
    second = pool.fetch_batch(
        ["BABA"],
        fetch_batch=fetch_batch,
        asset_type_map=asset_types,
        accept_stale_when_closed=False,
    )

    assert first["BABA"]["is_stale"] is True
    assert second == {}
    assert calls == [["BABA"], ["BABA"]]
    assert pool.summary()["retried"] == 1
    assert pool.summary()["failed_unique"] == 1


def test_pool_replaces_disallowed_stale_hit_with_fresh_quote():
    calls = []

    def fetch_batch(codes, **_kwargs):
        calls.append(list(codes))
        payload = _us_quote(codes[0], price=80 if len(calls) == 1 else 81)
        if len(calls) == 1:
            payload.update(source="cache_fallback", is_stale=True, is_from_cache=True)
        return {codes[0]: payload}

    pool = RunQuotePool()
    asset_types = {"BABA": AssetType.US_STOCK}
    pool.fetch_batch(
        ["BABA"],
        fetch_batch=fetch_batch,
        asset_type_map=asset_types,
        accept_stale_when_closed=True,
    )
    refreshed = pool.fetch_batch(
        ["BABA"],
        fetch_batch=fetch_batch,
        asset_type_map=asset_types,
        accept_stale_when_closed=False,
    )

    assert calls == [["BABA"], ["BABA"]]
    assert refreshed["BABA"]["price"] == 81
    assert refreshed["BABA"].get("is_stale") is None
    assert refreshed["BABA"].get("is_from_run_pool") is None


def test_pool_preserves_exception_and_attempt_state_for_later_retry():
    calls = []

    def fetch_batch(codes, **_kwargs):
        calls.append(list(codes))
        if len(calls) == 1:
            raise TimeoutError("deadline")
        return {codes[0]: _us_quote(codes[0])}

    pool = RunQuotePool()
    asset_types = {"SPY": AssetType.US_FUND}

    with pytest.raises(TimeoutError, match="deadline"):
        pool.fetch_batch(["SPY"], fetch_batch=fetch_batch, asset_type_map=asset_types)
    second = pool.fetch_batch(["SPY"], fetch_batch=fetch_batch, asset_type_map=asset_types)

    assert "SPY" in second
    assert calls == [["SPY"], ["SPY"]]
    assert pool.summary()["retried"] == 1
    assert pool.summary()["failed_unique"] == 0
