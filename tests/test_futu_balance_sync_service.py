from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from pytest import MonkeyPatch

from src import config
from src.app import FutuBalanceSnapshot, FutuBalanceSyncService
from src.app.futu_balance_sync_service import FutuOpenApiBalanceProvider
from src.models import AssetType, Holding
from skill_api import PortfolioSkill


class FakeProvider:
    def __init__(self, cash=100.126, mmf=200.334):
        self.cash = cash
        self.mmf = mmf

    def fetch_balances(self):
        return FutuBalanceSnapshot(cash=self.cash, mmf=self.mmf, source="fake")


class FakeStorage:
    def __init__(self):
        self.holdings = {}
        self.updates = []
        self.creates = []

    def get_holding(self, asset_id, account, broker=None):
        return self.holdings.get((asset_id, account, broker))

    def update_holding_quantity(self, asset_id, account, quantity_change, broker=None):
        self.updates.append((asset_id, account, quantity_change, broker))
        holding = self.holdings[(asset_id, account, broker)]
        holding.quantity += quantity_change

    def upsert_holding(self, holding):
        self.creates.append(holding)
        self.holdings[(holding.asset_id, holding.account, holding.broker)] = holding
        return holding


class FakeReplaceStorage(FakeStorage):
    def __init__(self):
        super().__init__()
        self.replacements = []

    def replace_holding(self, holding):
        self.replacements.append(holding)
        existing = self.holdings.get((holding.asset_id, holding.account, holding.broker))
        holding.record_id = getattr(existing, "record_id", None)
        self.holdings[(holding.asset_id, holding.account, holding.broker)] = holding
        return holding


def test_sync_cash_and_mmf_updates_existing_holdings_by_delta():
    storage = FakeStorage()
    storage.holdings[("CNY-CASH", "lx", "富途")] = Holding(
        asset_id="CNY-CASH",
        asset_name="人民币现金",
        asset_type=AssetType.CASH,
        account="lx",
        broker="富途",
        quantity=20,
        currency="CNY",
    )
    storage.holdings[("CNY-MMF", "lx", "富途")] = Holding(
        asset_id="CNY-MMF",
        asset_name="货币基金",
        asset_type=AssetType.MMF,
        account="lx",
        broker="富途",
        quantity=50,
        currency="CNY",
    )

    result = FutuBalanceSyncService(storage, FakeProvider()).sync_cash_and_mmf(account="lx")

    assert result["success"] is True
    assert result["updated"] == 2
    assert result["created"] == 0
    assert storage.updates == [
        ("CNY-CASH", "lx", 80.13, "富途"),
        ("CNY-MMF", "lx", 150.33, "富途"),
    ]
    assert storage.holdings[("CNY-CASH", "lx", "富途")].quantity == 100.13
    assert storage.holdings[("CNY-MMF", "lx", "富途")].quantity == 200.33


def test_sync_cash_and_mmf_replaces_existing_holding_fields_when_supported():
    storage = FakeReplaceStorage()
    storage.holdings[("CNY-CASH", "lx", "富途")] = Holding(
        record_id="rec_cash",
        asset_id="CNY-CASH",
        asset_name="旧现金名",
        asset_type=AssetType.OTHER,
        account="lx",
        broker="富途",
        quantity=20,
        currency="USD",
    )

    result = FutuBalanceSyncService(storage, FakeProvider(cash=100.126, mmf=None)).sync_cash_and_mmf(account="lx")

    assert result["updated"] == 1
    assert result["items"][0]["fields_changed"] is True
    assert result["items"][0]["field_updates"] == {
        "asset_name": "人民币现金",
        "asset_type": "cash",
        "currency": "CNY",
        "asset_class": "现金",
        "industry": "现金",
    }
    assert storage.updates == []
    assert [h.asset_id for h in storage.replacements] == ["CNY-CASH"]
    holding = storage.holdings[("CNY-CASH", "lx", "富途")]
    assert holding.quantity == 100.13
    assert holding.asset_name == "人民币现金"
    assert holding.asset_type == AssetType.CASH
    assert holding.currency == "CNY"


def test_sync_cash_and_mmf_creates_missing_holdings():
    storage = FakeStorage()

    result = FutuBalanceSyncService(storage, FakeProvider(cash=10, mmf=0)).sync_cash_and_mmf(account="lx")

    assert result["created"] == 2
    assert storage.updates == []
    assert [h.asset_id for h in storage.creates] == ["CNY-CASH", "CNY-MMF"]
    assert storage.holdings[("CNY-CASH", "lx", "富途")].quantity == 10.0
    assert storage.holdings[("CNY-MMF", "lx", "富途")].quantity == 0.0


def test_sync_cash_and_mmf_dry_run_does_not_write():
    storage = FakeStorage()
    storage.holdings[("CNY-CASH", "lx", "富途")] = Holding(
        asset_id="CNY-CASH",
        asset_name="人民币现金",
        asset_type=AssetType.CASH,
        account="lx",
        broker="富途",
        quantity=20,
        currency="CNY",
    )

    result = FutuBalanceSyncService(storage, FakeProvider(cash=100, mmf=None)).sync_cash_and_mmf(account="lx", dry_run=True)

    assert result["items"][0]["delta"] == 80.0
    assert storage.updates == []
    assert storage.creates == []
    assert storage.holdings[("CNY-CASH", "lx", "富途")].quantity == 20


def test_sync_cash_and_mmf_accepts_manual_balances_without_provider():
    storage = FakeStorage()

    result = FutuBalanceSyncService(storage).sync_cash_and_mmf(account="lx", cash_balance=1.235, mmf_balance=None)

    assert result["items"] == [
        {
            "asset_id": "CNY-CASH",
            "asset_name": "人民币现金",
            "current": 0.0,
            "target": 1.24,
            "delta": 1.24,
            "created": True,
            "updated": True,
            "fields_changed": False,
            "field_updates": {},
        }
    ]
    assert storage.holdings[("CNY-CASH", "lx", "富途")].quantity == 1.24


def test_futu_openapi_provider_reads_mmf_from_accinfo_fund_assets():
    class FakeCtx:
        def __init__(self):
            self.position_called = False

        def accinfo_query(self, **kwargs):
            return 0, [{"cash": "12.345", "fund_assets": "345.678"}]

        def position_list_query(self, **kwargs):
            self.position_called = True
            raise AssertionError("MMF should be read from accinfo.fund_assets")

    futu_sdk = SimpleNamespace(RET_OK=0, TrdEnv=SimpleNamespace(REAL="REAL"), Currency=SimpleNamespace(CNH="CNH"))
    ctx = FakeCtx()
    provider = FutuOpenApiBalanceProvider()

    assert provider._fetch_cash(futu_sdk, ctx) == 12.345
    assert provider._fetch_mmf(futu_sdk, ctx) == 345.68
    assert ctx.position_called is False


def test_futu_openapi_provider_reads_defaults_from_config_file():
    with TemporaryDirectory() as tmp:
        config_file = Path(tmp) / "config.json"
        config_file.write_text(
            json.dumps({
                "futu": {
                    "opend": {"host": "10.0.0.2", "port": 22222},
                    "trd_env": "SIMULATE",
                    "acc_id": 123456,
                    "trd_market": "US",
                    "cash_currency": "USD",
                }
            }),
            encoding="utf-8",
        )

        patch = MonkeyPatch()
        try:
            patch.setattr(config, "_CONFIG_FILE", config_file)
            for name in (
                "FUTU_OPEND_HOST",
                "FUTU_OPEND_PORT",
                "FUTU_TRD_ENV",
                "FUTU_ACC_ID",
                "FUTU_TRD_MARKET",
                "FUTU_CASH_CURRENCY",
            ):
                patch.delenv(name, raising=False)
            config.reload_config()

            provider = FutuOpenApiBalanceProvider()
            assert provider.host == "10.0.0.2"
            assert provider.port == 22222
            assert provider.trd_env == "SIMULATE"
            assert provider.acc_id == 123456
            assert provider.trd_market == "US"
            assert provider.cash_currency == "USD"
        finally:
            patch.undo()
            config.reload_config()


def test_portfolio_skill_futu_sync_defaults_to_dry_run(monkeypatch):
    calls = []

    class FakeService:
        def __init__(self, storage):
            self.storage = storage

        def sync_cash_and_mmf(self, **kwargs):
            calls.append(kwargs)
            return {"success": True, "dry_run": kwargs["dry_run"]}

    import skill_api

    monkeypatch.setattr(skill_api, "FutuBalanceSyncService", FakeService)
    skill = PortfolioSkill.__new__(PortfolioSkill)
    skill.account = "lx"
    skill.storage = object()

    result = skill.sync_futu_cash_mmf()

    assert result == {"success": True, "dry_run": True}
    assert calls[0]["dry_run"] is True


class FakePortfolioProvider:
    def __init__(self, positions, cash=None, mmf=None):
        self.positions = tuple(positions)
        self.cash = cash
        self.mmf = mmf

    def fetch_portfolio(self):
        from src.app.futu_balance_sync_service import FutuPortfolioSnapshot

        return FutuPortfolioSnapshot(
            cash=self.cash,
            mmf=self.mmf,
            positions=self.positions,
            source="fake-portfolio",
        )


class FakePortfolioStorage:
    def __init__(self, holdings=None):
        self.holdings = list(holdings or [])
        self.bulk_calls = []

    def get_holdings(self, account=None, include_empty=False, asset_type=None):
        rows = [h for h in self.holdings if account is None or h.account == account]
        return rows if include_empty else [h for h in rows if h.quantity > 0]

    def get_holding(self, asset_id, account, broker=None):
        for holding in self.holdings:
            if holding.asset_id == asset_id and holding.account == account and (broker is None or holding.broker == broker):
                return holding
        return None

    def upsert_holdings_bulk(self, holdings, mode="additive"):
        self.bulk_calls.append((list(holdings), mode))
        return {"updated": len(holdings), "created": 0, "mode": mode}


def _position(
    code="US.FUTU",
    *,
    quantity=10,
    average_cost=100.25,
    security_type="STOCK",
    position_side="LONG",
    currency="USD",
    market="US",
):
    from src.app.futu_balance_sync_service import FutuPositionSnapshot

    return FutuPositionSnapshot(
        asset_id=code.split(".", 1)[1] if "." in code else code,
        asset_name="Futu Holdings",
        security_type=security_type,
        quantity=quantity,
        average_cost=average_cost,
        currency=currency,
        market=market,
        position_side=position_side,
        raw_code=code,
    )


def test_position_snapshot_uses_average_cost_not_diluted_or_deprecated_cost():
    provider = FutuOpenApiBalanceProvider()

    snapshot = provider._position_snapshot(
        {
            "code": "US.FUTU",
            "stock_name": "Futu Holdings",
            "qty": 10,
            "average_cost": 100.25,
            "diluted_cost": 72.8,
            "cost_price": 72.8,
            "currency": "USD",
            "position_side": "LONG",
        },
        "STOCK",
    )

    assert snapshot.average_cost == 100.25
    assert snapshot.asset_id == "FUTU"


def test_sync_portfolio_detects_cost_only_change_and_preserves_manual_metadata():
    existing = Holding(
        record_id="rec_futu",
        asset_id="FUTU",
        asset_name="人工名称",
        asset_type=AssetType.US_STOCK,
        account="lx",
        broker="富途",
        quantity=10,
        avg_cost=72.8,
        currency="USD",
        asset_class="美国资产",
        industry="科技",
        tag=["核心"],
    )
    storage = FakePortfolioStorage([existing])
    service = FutuBalanceSyncService(storage, FakePortfolioProvider([_position()]))

    result = service.sync_portfolio(account="lx", dry_run=True)

    assert result["success"] is True
    assert result["summary"] == {
        "created": 0,
        "updated": 1,
        "zeroed": 0,
        "unchanged": 0,
        "quantity_changed": 0,
        "cost_changed": 1,
    }
    item = result["positions"][0]
    assert item["current_avg_cost"] == 72.8
    assert item["target_avg_cost"] == 100.25
    assert item["cost_source"] == "average_cost"
    assert item["quantity_changed"] is False
    assert storage.bulk_calls == []


def test_sync_portfolio_write_uses_absolute_quantity_and_average_cost():
    existing = Holding(
        record_id="rec_futu",
        asset_id="FUTU",
        asset_name="人工名称",
        asset_type=AssetType.US_STOCK,
        account="lx",
        broker="富途",
        quantity=8,
        avg_cost=90,
        currency="USD",
        asset_class="美国资产",
        industry="科技",
        tag=["核心"],
    )
    storage = FakePortfolioStorage([existing])
    service = FutuBalanceSyncService(storage, FakePortfolioProvider([_position(quantity=10.1256)]))

    result = service.sync_portfolio(account="lx", dry_run=False, confirm=True)

    assert result["success"] is True
    replacements, mode = storage.bulk_calls[0]
    assert mode == "replace"
    replacement = replacements[0]
    assert replacement.quantity == 10.1256
    assert replacement.avg_cost == 100.25
    assert replacement.asset_name == "人工名称"
    assert replacement.industry.value == "科技"
    assert replacement.tag == ["核心"]


def test_sync_portfolio_recognizes_existing_market_fund_and_preserves_metadata():
    existing = Holding(
        record_id="rec_spy",
        asset_id="SPY",
        asset_name="人工 SPY",
        asset_type=AssetType.US_FUND,
        account="sy",
        broker="富途",
        quantity=55.1026,
        avg_cost=500,
        currency="USD",
        asset_class="美国资产",
        industry="非行业指数",
        tag=["指数"],
    )
    storage = FakePortfolioStorage([existing])
    service = FutuBalanceSyncService(
        storage,
        FakePortfolioProvider([
            _position(
                code="US.SPY",
                quantity=55.1026,
                average_cost=501.23,
                security_type="ETF",
            ),
        ]),
    )

    result = service.sync_portfolio(account="sy", dry_run=False, confirm=True)

    assert result["summary"]["created"] == 0
    assert result["summary"]["quantity_changed"] == 0
    assert result["summary"]["cost_changed"] == 1
    replacement = storage.bulk_calls[0][0][0]
    assert replacement.asset_type == AssetType.US_FUND
    assert replacement.currency == "USD"
    assert replacement.asset_class.value == "美国资产"
    assert replacement.industry.value == "非行业指数"
    assert replacement.tag == ["指数"]


def test_sync_portfolio_reports_partial_write_when_cash_sync_fails_after_positions():
    class CashFailureStorage(FakePortfolioStorage):
        def get_holding(self, asset_id, account, broker=None):
            if asset_id == "CNY-CASH" and self.bulk_calls:
                raise RuntimeError("cash write failed")
            return super().get_holding(asset_id, account, broker)

    existing = Holding(
        asset_id="FUTU",
        asset_name="Futu Holdings",
        asset_type=AssetType.US_STOCK,
        account="lx",
        broker="富途",
        quantity=8,
        avg_cost=90,
        currency="USD",
    )
    storage = CashFailureStorage([existing])
    service = FutuBalanceSyncService(
        storage,
        FakePortfolioProvider([_position()], cash=100),
    )

    result = service.sync_portfolio(account="lx", dry_run=False, confirm=True)

    assert result["success"] is False
    assert result["write_stage"] == "cash_mmf"
    assert result["partial_write_possible"] is True
    assert result["summary"]["updated"] == 1
    assert len(storage.bulk_calls) == 1


def test_sync_portfolio_blocks_missing_average_cost_without_fallback_or_writes():
    existing = Holding(
        asset_id="FUTU",
        asset_name="Futu Holdings",
        asset_type=AssetType.US_STOCK,
        account="lx",
        broker="富途",
        quantity=10,
        avg_cost=72.8,
        currency="USD",
    )
    storage = FakePortfolioStorage([existing])
    service = FutuBalanceSyncService(storage, FakePortfolioProvider([_position(average_cost=None)]))

    result = service.sync_portfolio(account="lx", dry_run=False, confirm=True)

    assert result["success"] is False
    assert "average_cost" in result["error"]
    assert storage.bulk_calls == []


def test_sync_portfolio_excludes_options_and_blocks_short_stock():
    option = _position(code="US.FUTU260116C100000", security_type="DRVT", average_cost=None)
    storage = FakePortfolioStorage()
    result = FutuBalanceSyncService(storage, FakePortfolioProvider([option])).sync_portfolio(account="lx")
    assert result["success"] is True
    assert result["positions"] == []

    short = _position(position_side="SHORT")
    result = FutuBalanceSyncService(storage, FakePortfolioProvider([short])).sync_portfolio(account="lx")
    assert result["success"] is False
    assert "short stock/ETF" in result["error"]


def test_sync_portfolio_empty_snapshot_guard_and_confirmed_override():
    existing = Holding(
        asset_id="FUTU",
        asset_name="Futu Holdings",
        asset_type=AssetType.US_STOCK,
        account="lx",
        broker="富途",
        quantity=10,
        avg_cost=72.8,
        currency="USD",
    )
    storage = FakePortfolioStorage([existing])
    service = FutuBalanceSyncService(storage, FakePortfolioProvider([]))

    blocked = service.sync_portfolio(account="lx", dry_run=True)
    assert blocked["success"] is False
    assert "empty eligible Futu stock snapshot" in blocked["error"]

    preview = service.sync_portfolio(account="lx", dry_run=True, allow_empty_stock_snapshot=True, confirm=True)
    assert preview["success"] is True
    assert preview["positions"][0]["action"] == "zero"
    assert preview["positions"][0]["target_avg_cost"] is None


def test_futu_code_normalization_preserves_us_dot_symbol_and_hk_leading_zero():
    from src.app.futu_balance_sync_service import _normalize_futu_code

    assert _normalize_futu_code("US.BRK.B") == "BRK.B"
    assert _normalize_futu_code("HK.00700") == "00700"
    assert _normalize_futu_code("SH.600519") == "600519"


def test_futu_portfolio_provider_fetches_average_cost_and_closes_contexts(monkeypatch):
    class FakeTradeCtx:
        def __init__(self):
            self.closed = False
            self.position_kwargs = None

        def accinfo_query(self, **kwargs):
            return 0, [{"cash": 12.34, "fund_assets": 56.78}]

        def position_list_query(self, **kwargs):
            self.position_kwargs = kwargs
            return 0, [{
                "code": "US.FUTU",
                "stock_name": "Futu Holdings",
                "qty": 10.1256,
                "average_cost": 100.25,
                "diluted_cost": 72.8,
                "cost_price": 72.8,
                "currency": "USD",
                "position_side": "LONG",
            }]

        def close(self):
            self.closed = True

    class FakeQuoteCtx:
        def __init__(self, **kwargs):
            self.closed = False

        def get_stock_basicinfo(self, **kwargs):
            return 0, [{"code": "US.FUTU", "stock_type": "STOCK"}]

        def close(self):
            self.closed = True

    trade_ctx = FakeTradeCtx()
    quote_ctx = FakeQuoteCtx()
    sdk = SimpleNamespace(
        RET_OK=0,
        TrdEnv=SimpleNamespace(REAL="REAL"),
        Currency=SimpleNamespace(CNH="CNH", NONE="N/A"),
        TrdMarket=SimpleNamespace(HK="HK"),
        Market=SimpleNamespace(US="US"),
        SecurityType=SimpleNamespace(NONE="N/A"),
        OpenSecTradeContext=lambda **kwargs: trade_ctx,
        OpenQuoteContext=lambda **kwargs: quote_ctx,
    )
    provider = FutuOpenApiBalanceProvider(acc_id=123)
    monkeypatch.setattr(provider, "_import_sdk", lambda: sdk)

    snapshot = provider.fetch_portfolio()

    assert snapshot.cash == 12.34
    assert snapshot.mmf == 56.78
    assert snapshot.positions[0].quantity == 10.1256
    assert snapshot.positions[0].average_cost == 100.25
    assert trade_ctx.position_kwargs["refresh_cache"] is True
    assert trade_ctx.closed is True
    assert quote_ctx.closed is True


def test_futu_portfolio_provider_closes_contexts_when_classification_fails(monkeypatch):
    class FakeTradeCtx:
        closed = False

        def accinfo_query(self, **kwargs):
            return 0, [{"cash": 1, "fund_assets": 2}]

        def position_list_query(self, **kwargs):
            return 0, [{"code": "US.FUTU", "qty": 1}]

        def close(self):
            self.closed = True

    class FakeQuoteCtx:
        closed = False

        def get_stock_basicinfo(self, **kwargs):
            return 1, "boom"

        def close(self):
            self.closed = True

    trade_ctx = FakeTradeCtx()
    quote_ctx = FakeQuoteCtx()
    sdk = SimpleNamespace(
        RET_OK=0,
        TrdEnv=SimpleNamespace(REAL="REAL"),
        Currency=SimpleNamespace(CNH="CNH", NONE="N/A"),
        TrdMarket=SimpleNamespace(HK="HK"),
        Market=SimpleNamespace(US="US"),
        SecurityType=SimpleNamespace(NONE="N/A"),
        OpenSecTradeContext=lambda **kwargs: trade_ctx,
        OpenQuoteContext=lambda **kwargs: quote_ctx,
    )
    provider = FutuOpenApiBalanceProvider(acc_id=123)
    monkeypatch.setattr(provider, "_import_sdk", lambda: sdk)

    try:
        provider.fetch_portfolio()
        assert False, "expected fetch_portfolio to fail"
    except RuntimeError as exc:
        assert "get_stock_basicinfo" in str(exc)

    assert trade_ctx.closed is True
    assert quote_ctx.closed is True


def test_portfolio_skill_full_futu_sync_defaults_to_dry_run(monkeypatch):
    calls = []

    class FakeService:
        def __init__(self, **kwargs):
            calls.append(("init", kwargs))

        def sync_futu_holdings(self, **kwargs):
            calls.append(("sync", kwargs))
            return {"success": True, "dry_run": kwargs["dry_run"]}

    import skill_api

    monkeypatch.setattr(skill_api, "PortfolioService", FakeService)
    skill = PortfolioSkill.__new__(PortfolioSkill)
    skill.account = "lx"
    skill.storage = object()

    result = skill.sync_futu_holdings()

    assert result == {"success": True, "dry_run": True}
    assert calls == [
        ("init", {"storage": skill.storage, "default_account": "lx"}),
        ("sync", {
            "dry_run": True,
            "confirm": False,
            "allow_empty_stock_snapshot": False,
        }),
    ]
