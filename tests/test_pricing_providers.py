from unittest.mock import patch

from src.price_fetcher import PriceFetcher
from src.pricing.providers.etf import ETFProvider
from src.pricing.providers.cn import CNStockProvider
from src.pricing.providers.fund import FundProvider
from src.pricing.providers.hk import HKStockProvider
from src.pricing.providers.tencent_batch import fetch_tencent_quotes_batch
from src.pricing.providers.us import USStockProvider
from src.pricing.providers.us_batch import fetch_us_batch


class FakeSinaResponse:
    status_code = 200

    def __init__(self, text: str):
        self.content = text.encode("gbk", errors="replace")

    def raise_for_status(self):
        return None


def _sina_us_line(code: str) -> str:
    fields = [""] * 36
    fields[0] = "Apple Inc"
    fields[1] = "193.00"
    fields[2] = "1.58"
    fields[4] = "3.00"
    fields[5] = "191.00"
    fields[6] = "195.00"
    fields[7] = "190.00"
    fields[10] = "200"
    fields[26] = "190.00"
    return f'var hq_str_gb_{code.lower()}="' + ",".join(fields) + '";'


def test_fetch_realtime_routes_cn_stock_through_provider(monkeypatch):
    fetcher = PriceFetcher()

    def fake_fetch(self, code):
        return {"code": code, "price": 10.0, "currency": "CNY", "cny_price": 10.0, "source": "fake-cn"}

    monkeypatch.setattr(CNStockProvider, "fetch_a_stock", fake_fetch)

    result = fetcher._fetch_realtime("000001", "平安银行")

    assert result["source"] == "fake-cn"
    assert result["provider"] == "cn-stock"
    assert result["source_chain"] == ["cn-stock"]


def test_fetch_realtime_routes_fund_before_cn_stock(monkeypatch):
    fetcher = PriceFetcher()

    def fake_fund(self, code):
        return {"code": code, "price": 1.0, "currency": "CNY", "cny_price": 1.0, "source": "fake-fund"}

    def fail_cn(self, code):
        raise AssertionError("fund-like code should not reach CN stock provider")

    monkeypatch.setattr(FundProvider, "fetch_fund", fake_fund)
    monkeypatch.setattr(CNStockProvider, "fetch_a_stock", fail_cn)

    result = fetcher._fetch_realtime("004001", "基金")

    assert result["source"] == "fake-fund"
    assert result["provider"] == "fund"


def test_fetch_realtime_routes_otc_fund_asset_type_to_fund(monkeypatch):
    fetcher = PriceFetcher()

    def fake_fund(self, code):
        return {"code": code, "price": 1.0, "currency": "CNY", "cny_price": 1.0, "source": "fake-fund"}

    def fail_cn(self, code):
        raise AssertionError("otc_fund should not reach CN stock provider")

    monkeypatch.setattr(FundProvider, "fetch_fund", fake_fund)
    monkeypatch.setattr(CNStockProvider, "fetch_a_stock", fail_cn)

    result = fetcher._fetch_realtime("000001", "", "otc_fund")

    assert result["source"] == "fake-fund"
    assert result["provider"] == "fund"


def test_fetch_realtime_routes_exchange_fund_asset_type_to_etf(monkeypatch):
    fetcher = PriceFetcher()

    def fake_etf(self, code):
        return {"code": code, "price": 4.0, "currency": "CNY", "cny_price": 4.0, "source": "fake-etf"}

    def fail_fund(self, code):
        raise AssertionError("exchange_fund should not reach fund NAV provider")

    monkeypatch.setattr(ETFProvider, "fetch_etf", fake_etf)
    monkeypatch.setattr(FundProvider, "fetch_fund", fail_fund)

    result = fetcher._fetch_realtime("510300", "", "exchange_fund")

    assert result["source"] == "fake-etf"
    assert result["provider"] == "etf"


def test_fetch_realtime_routes_hk_stock_through_provider(monkeypatch):
    fetcher = PriceFetcher()

    def fake_hk(self, code):
        return {"code": code, "price": 400.0, "currency": "HKD", "cny_price": 360.0, "source": "fake-hk"}

    monkeypatch.setattr(HKStockProvider, "fetch_hk_stock", fake_hk)

    result = fetcher._fetch_realtime("00700", "腾讯控股")

    assert result["source"] == "fake-hk"
    assert result["provider"] == "hk-stock"


def test_fetch_realtime_defaults_to_us_provider(monkeypatch):
    fetcher = PriceFetcher()

    def fake_us(self, code):
        return {"code": code, "price": 100.0, "currency": "USD", "cny_price": 720.0, "source": "fake-us"}

    monkeypatch.setattr(USStockProvider, "fetch_us_stock", fake_us)

    result = fetcher._fetch_realtime("AAPL", "Apple")

    assert result["source"] == "fake-us"
    assert result["provider"] == "us-stock"


def test_tencent_batch_provider_fetches_cn_quote():
    fetcher = PriceFetcher()
    fetcher._fetch_exchange_rates = lambda: {"HKDCNY": 0.9}

    parts = [""] * 46
    parts[1] = "沪深300ETF"
    parts[3] = "4.20"
    parts[4] = "4.00"
    parts[5] = "4.10"
    parts[30] = "2026-05-23 15:00:00"
    parts[31] = "0.20"
    parts[32] = "5.00"
    parts[33] = "4.30"
    parts[34] = "4.00"
    parts[36] = "100"

    def fake_batch(session, query_codes, timeout=8, chunk_size=50):
        assert query_codes == ["sh510300"]
        return {"sh510300": parts}, {"requested": 1, "returned": 1}

    with patch("src.pricing.providers.tencent_batch.tencent_fetch_batch", fake_batch):
        results, leftover = fetch_tencent_quotes_batch(fetcher, ["510300"], name_map={"510300": "沪深300ETF"})

    assert leftover == []
    assert results["510300"]["source"] == "tencent_batch"
    assert results["510300"]["price"] == 4.2
    assert fetcher._last_tencent_batch_meta == {"requested": 1, "returned": 1}


def test_us_batch_provider_falls_back_to_stale_cache():
    fetcher = PriceFetcher()
    fetcher.session.get = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("offline"))

    result = fetch_us_batch(
        fetcher,
        ["AAPL"],
        name_map={},
        expired_cache={"AAPL": {"code": "AAPL", "source": "old_cache", "is_from_cache": True}},
        _nested=True,
    )

    assert result["AAPL"]["source"] == "cache_fallback"
    assert result["AAPL"]["is_from_cache"] is True


def test_sina_us_single_and_batch_share_normalized_payload():
    fetcher = PriceFetcher()
    fetcher._fetch_exchange_rates = lambda **kw: {"USDCNY": 7.1}
    fetcher.session.get = lambda *args, **kwargs: FakeSinaResponse(_sina_us_line("aapl"))

    single = USStockProvider(fetcher).fetch_sina("AAPL")
    with patch("src.pricing.providers.us_batch._config.get", return_value=None):
        batch = fetch_us_batch(fetcher, ["AAPL"], name_map={}, expired_cache={}, _nested=True)["AAPL"]

    expected = {
        "code": "AAPL",
        "name": "Apple Inc",
        "price": 193.0,
        "prev_close": 190.0,
        "open": 191.0,
        "high": 195.0,
        "low": 190.0,
        "volume": 200,
        "currency": "USD",
        "cny_price": 1370.3,
        "exchange_rate": 7.1,
        "market_type": "us",
        "source": "sina_us",
    }
    for key, value in expected.items():
        assert single[key] == value
        assert batch[key] == value


def test_sina_us_empty_quote_does_not_fetch_exchange_rate():
    fetcher = PriceFetcher()
    fetcher.session.get = lambda *args, **kwargs: FakeSinaResponse('var hq_str_gb_aapl="";')
    fetcher._fetch_exchange_rates = lambda: (_ for _ in ()).throw(AssertionError("rates should not be fetched"))

    assert USStockProvider(fetcher).fetch_sina("AAPL") is None


def test_us_batch_merges_finnhub_failures_into_one_sina_request(monkeypatch):
    fetcher = PriceFetcher()
    fetcher._fetch_exchange_rates = lambda **kw: {"USDCNY": 7.1}
    requests = []

    def fake_get(url, **kwargs):
        requests.append(url)
        return FakeSinaResponse("\n".join([_sina_us_line("futu"), _sina_us_line("baba")]))

    fetcher.session.get = fake_get
    monkeypatch.setattr("src.pricing.providers.us_batch._config.get", lambda key, default=None: "key")
    monkeypatch.setattr(
        USStockProvider, "fetch_finnhub", lambda self, code, key, **kw: (_ for _ in ()).throw(RuntimeError("timeout"))
    )

    result = fetch_us_batch(fetcher, ["FUTU", "BABA"], name_map={}, expired_cache={}, _nested=True)

    assert len(requests) == 1
    assert "gb_futu,gb_baba" in requests[0]
    assert result["FUTU"]["source"] == "sina_us"
    assert result["BABA"]["source"] == "sina_us"


def test_us_batch_break_path_merges_skipped_codes_into_sina_request(monkeypatch):
    fetcher = PriceFetcher()
    fetcher._fetch_exchange_rates = lambda **kw: {"USDCNY": 7.1}
    codes = ["FUTU", "BABA", "GOOGL", "PDD", "TCOM"]
    requests = []

    def fake_get(url, **kwargs):
        requests.append(url)
        return FakeSinaResponse("\n".join(_sina_us_line(c) for c in codes))

    fetcher.session.get = fake_get
    monkeypatch.setattr("src.pricing.providers.us_batch._config.get", lambda key, default=None: "key")
    monkeypatch.setattr(
        USStockProvider, "fetch_finnhub", lambda self, code, key, **kw: (_ for _ in ()).throw(RuntimeError("timeout"))
    )

    result = fetch_us_batch(fetcher, codes, name_map={}, expired_cache={}, _nested=True)

    assert len(requests) == 1
    for code in codes:
        assert "gb_" + code.lower() in requests[0]
        assert result[code]["source"] == "sina_us"


def test_us_batch_deadline_exhausted_still_reaches_fallbacks(monkeypatch):
    import time as _time

    fetcher = PriceFetcher()
    fetcher._fetch_exchange_rates = lambda **kw: {"USDCNY": 7.1}

    # deadline already past: must not raise; codes fall back to expired cache
    monkeypatch.setattr("src.pricing.providers.us_batch._config.get", lambda key, default=None: "key")
    monkeypatch.setattr(
        USStockProvider, "fetch_finnhub", lambda self, code, key, **kw: (_ for _ in ()).throw(RuntimeError("timeout"))
    )
    result = fetch_us_batch(
        fetcher,
        ["FUTU", "BABA"],
        name_map={},
        expired_cache={"BABA": {"code": "BABA", "source": "old", "is_from_cache": True}},
        _nested=True,
        deadline=_time.monotonic() - 1,
    )
    assert result["BABA"]["source"] == "cache_fallback"

    # TimeoutError at the loop top must not skip the Sina merge for leftovers
    requests = []

    def fake_get(url, **kwargs):
        requests.append(url)
        return FakeSinaResponse("\n".join([_sina_us_line("futu"), _sina_us_line("baba")]))

    fetcher.session.get = fake_get
    real_remaining = getattr(__import__("src.pricing.providers.us_batch", fromlist=["remaining_timeout"]), "remaining_timeout")
    state = {"tripped": False}

    def trip_once(deadline, default):
        if not state["tripped"]:
            state["tripped"] = True
            raise TimeoutError("pricing deadline exceeded")
        return real_remaining(deadline, default)

    monkeypatch.setattr("src.pricing.providers.us_batch.remaining_timeout", trip_once)

    result = fetch_us_batch(fetcher, ["FUTU", "BABA"], name_map={}, expired_cache={}, _nested=True)
    assert len(requests) == 1
    assert "gb_futu,gb_baba" in requests[0]
    assert result["FUTU"]["source"] == "sina_us"
    assert result["BABA"]["source"] == "sina_us"


def test_us_batch_reserves_deadline_for_sina(monkeypatch):
    import time as _time

    fetcher = PriceFetcher()
    fetcher._fetch_exchange_rates = lambda **kw: {"USDCNY": 7.1}
    fetcher.session.get = lambda url, **kwargs: FakeSinaResponse(
        "\n".join([_sina_us_line("futu"), _sina_us_line("baba")])
    )
    monkeypatch.setattr("src.pricing.providers.us_batch._config.get", lambda key, default=None: "key")

    finnhub_deadlines = []
    monkeypatch.setattr(
        USStockProvider,
        "fetch_finnhub",
        lambda self, code, key, **kw: (
            finnhub_deadlines.append(kw.get("deadline")),
            (_ for _ in ()).throw(RuntimeError("timeout")),
        )[1],
    )

    deadline = _time.monotonic() + 25
    result = fetch_us_batch(fetcher, ["FUTU", "BABA"], name_map={}, expired_cache={}, _nested=True, deadline=deadline)

    # Finnhub attempts run against a sub-deadline that reserves SINA_DEADLINE_RESERVE_SEC
    assert finnhub_deadlines
    for d in finnhub_deadlines:
        assert abs(d - (deadline - 6.0)) < 0.001
    assert result["FUTU"]["source"] == "sina_us"
    assert result["BABA"]["source"] == "sina_us"


def test_us_batch_skips_finnhub_when_its_budget_already_spent(monkeypatch):
    import time as _time

    fetcher = PriceFetcher()
    fetcher._fetch_exchange_rates = lambda **kw: {"USDCNY": 7.1}
    requests = []

    def fake_get(url, **kwargs):
        requests.append(url)
        return FakeSinaResponse("\n".join([_sina_us_line("futu"), _sina_us_line("baba")]))

    fetcher.session.get = fake_get
    monkeypatch.setattr("src.pricing.providers.us_batch._config.get", lambda key, default=None: "key")

    calls = {"n": 0}

    def counting_finnhub(self, code, key, **kw):
        calls["n"] += 1
        raise RuntimeError("should not be called")

    monkeypatch.setattr(USStockProvider, "fetch_finnhub", counting_finnhub)

    # Only 3s left overall: the 6s reserve leaves no Finnhub budget at all
    result = fetch_us_batch(
        fetcher,
        ["FUTU", "BABA"],
        name_map={},
        expired_cache={},
        _nested=True,
        deadline=_time.monotonic() + 3,
    )
    assert calls["n"] == 0
    assert len(requests) == 1
    assert result["FUTU"]["source"] == "sina_us"
    assert result["BABA"]["source"] == "sina_us"


def test_sina_us_dotted_symbol_maps_to_dollar_query_code():
    fetcher = PriceFetcher()
    fetcher._fetch_exchange_rates = lambda **kw: {"USDCNY": 7.1}
    requests = []

    def fake_get(url, **kwargs):
        requests.append(url)
        return FakeSinaResponse(_sina_us_line("brk$b"))

    fetcher.session.get = fake_get

    result = USStockProvider(fetcher).fetch_sina("BRK.B")

    assert "gb_brk$b" in requests[0]
    assert result is not None
    assert result["code"] == "BRK.B"


def test_realtime_pricing_strips_supported_suffixes_but_preserves_internal_dot(monkeypatch):
    fetcher = PriceFetcher()
    seen = []

    def fake_us(self, code):
        seen.append(code)
        return {"code": code, "price": 100.0, "currency": "USD", "cny_price": 700.0}

    monkeypatch.setattr(USStockProvider, "fetch_us_stock", fake_us)

    assert fetcher._fetch_realtime("FUTU.US", "Futu")["code"] == "FUTU"
    assert fetcher._fetch_realtime("BRK.B", "Berkshire")["code"] == "BRK.B"
    assert seen == ["FUTU", "BRK.B"]


def test_batch_pricing_maps_canonical_provider_result_back_to_caller_code(monkeypatch):
    fetcher = PriceFetcher(storage=None, use_cache=False)

    monkeypatch.setattr(
        "src.pricing.batch.fetch_us_batch",
        lambda *_args, **_kwargs: {
            "FUTU": {"code": "FUTU", "price": 100, "currency": "USD", "cny_price": 700}
        },
    )

    result = fetcher.fetch_batch(["FUTU.US"])

    assert list(result) == ["FUTU.US"]
    assert result["FUTU.US"]["code"] == "FUTU.US"


def test_tencent_batch_splits_remaining_deadline_across_http_chunks(monkeypatch):
    import time

    fetcher = PriceFetcher()
    captured = {}

    def fake_batch(session, query_codes, timeout=8, chunk_size=50):
        captured.update(timeout=timeout, chunk_size=chunk_size, count=len(query_codes))
        return {}, {"requests": 0}

    monkeypatch.setattr("src.pricing.providers.tencent_batch.tencent_fetch_batch", fake_batch)
    codes = [f"600{i:03d}" for i in range(51)]
    _, leftover = fetch_tencent_quotes_batch(
        fetcher,
        codes,
        deadline=time.monotonic() + 2,
    )

    assert len(leftover) == 51
    assert captured["count"] == 51
    assert captured["chunk_size"] == 50
    assert 0 < captured["timeout"] <= 1


def test_explicit_sh_suffix_overrides_fund_heuristic(monkeypatch):
    fetcher = PriceFetcher(storage=None, use_cache=False)

    def fail_fund(self, code):
        raise AssertionError("explicit .SH stock must not use fund provider")

    def fake_cn(self, code):
        return {"code": code, "price": 10.0, "currency": "CNY", "cny_price": 10.0}

    monkeypatch.setattr(FundProvider, "fetch_fund", fail_fund)
    monkeypatch.setattr(CNStockProvider, "fetch_a_stock", fake_cn)

    result = fetcher.fetch("004001.SH", force_refresh=True)

    assert result["provider"] == "cn-stock"
    assert result["code"] == "004001.SH"


def test_explicit_us_suffix_overrides_numeric_etf_and_hk_heuristics(monkeypatch):
    fetcher = PriceFetcher(storage=None, use_cache=False)

    def fail_etf(self, code):
        raise AssertionError("explicit .US stock must not use ETF provider")

    def fail_hk(self, code):
        raise AssertionError("explicit .US stock must not use HK provider")

    def fake_us(self, code):
        return {"code": code, "price": 20.0, "currency": "USD", "cny_price": 140.0}

    monkeypatch.setattr(ETFProvider, "fetch_etf", fail_etf)
    monkeypatch.setattr(HKStockProvider, "fetch_hk_stock", fail_hk)
    monkeypatch.setattr(USStockProvider, "fetch_us_stock", fake_us)

    result = fetcher.fetch("510300.US", force_refresh=True)

    assert result["provider"] == "us-stock"
    assert result["code"] == "510300.US"


def test_batch_routing_keeps_explicit_us_market_before_canonicalization(monkeypatch):
    fetcher = PriceFetcher(storage=None, use_cache=False)
    seen = []

    def fake_us_batch(_fetcher, codes, *_args, **_kwargs):
        seen.extend(codes)
        return {
            "510300": {
                "code": "510300",
                "price": 20.0,
                "currency": "USD",
                "cny_price": 140.0,
            }
        }

    monkeypatch.setattr("src.pricing.batch.fetch_us_batch", fake_us_batch)
    monkeypatch.setattr(
        "src.pricing.batch.fetch_tencent_quotes_batch",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("explicit .US must not enter Tencent batch")
        ),
    )

    result = fetcher.fetch_batch(["510300.US"])

    assert seen == ["510300"]
    assert result["510300.US"]["code"] == "510300.US"
