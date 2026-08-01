from datetime import date

import pytest

from src import config
from src.app.cash_flow_effect_receipt_service import CashFlowEffectReceiptService
from src.app.cash_flow_effect_service import CashFlowEffectService
from src.app.cash_flow_effect_store import CashFlowEffectStore
from src.app.futu_balance_sync_service import FutuBalanceSnapshot
from src.domain.holding_mutations import HoldingTarget
from src.models import AssetClass, AssetType, CashFlow, Holding


class FakeStorage:
    def __init__(self, *, flows=None, holdings=None, navs=None):
        self.flows = list(flows or [])
        self.holdings = {
            (item.asset_id, item.account, item.broker): item
            for item in (holdings or [])
        }
        self.navs = list(navs or [])
        self.replacements = []

    def get_cash_flows(self, account=None):
        return [
            item for item in self.flows
            if account is None or item.account == account
        ]

    def get_holdings_fresh(self, *, account=None, asset_type=None, include_empty=True):
        rows = [
            item for item in self.holdings.values()
            if account is None or item.account == account
        ]
        if asset_type:
            rows = [
                item for item in rows
                if item.asset_type.value == asset_type or item.asset_type == asset_type
            ]
        return list(rows)

    def get_holding_fresh(self, asset_id, account, broker):
        return self.holdings.get((asset_id, account, broker))

    def get_holding(self, asset_id, account, broker=None):
        return self.holdings.get((asset_id, account, broker))

    def replace_holding(self, holding):
        if isinstance(holding, HoldingTarget):
            key = (
                holding.identity.asset_id,
                holding.identity.account,
                holding.identity.broker,
            )
            current = self.holdings.get(key)
            replacement = holding.to_holding(
                record_id=(
                    current.record_id
                    if current is not None
                    else f"created_{len(self.holdings)}"
                ),
                created_at=current.created_at if current is not None else None,
            )
        else:
            replacement = Holding(**holding.model_dump())
            key = (replacement.asset_id, replacement.account, replacement.broker)
            current = self.holdings.get(key)
        replacement.record_id = current.record_id if current else f"created_{len(self.holdings)}"
        self.holdings[key] = replacement
        self.replacements.append(replacement)
        return replacement

    def get_nav_history(self, account, days=9999):
        return [item for item in self.navs if item.account == account]

    def get_nav_on_date(self, account, nav_date):
        return next(
            (
                item for item in self.navs
                if item.account == account and item.nav_date == nav_date
            ),
            None,
        )


class FailOnceStorage(FakeStorage):
    def __init__(self, *, fail_account, **kwargs):
        super().__init__(**kwargs)
        self.fail_account = fail_account
        self.failed_once = False

    def replace_holding(self, holding):
        account = (
            holding.identity.account
            if isinstance(holding, HoldingTarget)
            else holding.account
        )
        if account == self.fail_account and not self.failed_once:
            self.failed_once = True
            raise RuntimeError("simulated target write failure")
        return super().replace_holding(holding)


class FakeFutuProvider:
    def __init__(self, balances, *, profile_fingerprint="profile-hash"):
        self.balances = balances
        self.profile_fingerprint = profile_fingerprint

    def fetch_balances(self):
        return FutuBalanceSnapshot(
            cash_by_currency=self.balances,
            source="fake-opend",
            account_id=123,
            profile_fingerprint=self.profile_fingerprint,
        )


def _cash(
    quantity=100,
    *,
    currency="CNY",
    broker="某券商",
    account="lx",
):
    return Holding(
        record_id=f"hold_{account}_{broker}_{currency}",
        asset_id=f"{currency}-CASH",
        asset_name=f"{currency}现金",
        asset_type=AssetType.CASH,
        account=account,
        broker=broker,
        quantity=quantity,
        currency=currency,
        asset_class=AssetClass.CASH,
        industry="现金",
    )


def _flow(
    amount=20,
    *,
    currency="CNY",
    broker="某券商",
    record_id="cf_1",
    account="lx",
):
    return CashFlow(
        record_id=record_id,
        flow_date=date(2026, 7, 26),
        account=account,
        broker=broker,
        amount=amount,
        currency=currency,
        cny_amount=amount if currency == "CNY" else None,
        exchange_rate=1 if currency == "CNY" else None,
        flow_type="DEPOSIT" if amount > 0 else "WITHDRAW",
    )


def _service(tmp_path, monkeypatch, storage, *, futu_provider=None):
    monkeypatch.setattr(config, "get_data_dir", lambda: tmp_path)
    original_get = config.get

    def fake_get(key, default=None):
        if key == "cash_flow.effects.cutover_date":
            return "2026-07-01"
        return original_get(key, default)

    monkeypatch.setattr(config, "get", fake_get)
    store = CashFlowEffectStore.initialize(
        db_path=tmp_path / "effects.sqlite3",
        cutover_date="2026-07-01",
    )
    return CashFlowEffectService(
        storage=storage,
        store=store,
        futu_provider_factory=(
            (lambda _account: futu_provider)
            if futu_provider is not None
            else None
        ),
    )


def test_non_futu_effect_requires_preview_hash_and_writes_absolute_target(
    tmp_path,
    monkeypatch,
):
    storage = FakeStorage(flows=[_flow()], holdings=[_cash()])
    service = _service(tmp_path, monkeypatch, storage)
    service.initialize_fingerprints()

    review = service.review(account="lx")
    effect = next(
        item for item in review["effects"]
        if item["effect_kind"] == "cash_flow"
    )
    preview = service.preview(effect["effect_id"])

    assert preview["targets"][0]["quantity"] == 120.0
    assert preview["target_source"] == "estimated_current_plus_event"
    assert storage.replacements == []

    result = service.confirm(
        effect["effect_id"],
        preview_hash=preview["preview_hash"],
        confirm=True,
    )

    assert result["success"] is True
    assert storage.holdings[("CNY-CASH", "lx", "某券商")].quantity == 120.0
    assert service.store.get_effect(effect["effect_id"])["state"] == "applied"


def test_initialize_snapshot_commits_baselines_and_first_full_scan(
    tmp_path,
    monkeypatch,
):
    flow = _flow()
    holding = _cash()
    storage = FakeStorage(flows=[flow], holdings=[holding])
    service = _service(tmp_path, monkeypatch, storage)

    result = service.initialize_from_snapshot(
        flows=[flow],
        holdings=[holding],
    )

    assert result["baselines"]["confirmed_baselines"] == 1
    assert result["scan"]["added"] == 1
    assert result["count"] == 1
    assert result["effects"][0]["effect_kind"] == "cash_flow"
    assert service.store.get_fingerprint(
        "CNY-CASH|lx|某券商"
    )["last_confirmed_amount"] == "100.00"


def test_failed_scan_rolls_back_partial_effect_commit(
    tmp_path,
    monkeypatch,
):
    storage = FakeStorage(flows=[_flow()], holdings=[_cash()])
    service = _service(tmp_path, monkeypatch, storage)

    def fail_holding_stage(**_kwargs):
        raise RuntimeError("simulated scan commit failure")

    monkeypatch.setattr(
        service,
        "_scan_holding_fingerprints",
        fail_holding_stage,
    )
    with pytest.raises(RuntimeError, match="simulated scan commit failure"):
        service.scan()

    assert service.store.list_effects() == []
    assert service.store.latest_scan()["status"] == "failed"


def test_holding_change_after_preview_invalidates_confirmation(tmp_path, monkeypatch):
    storage = FakeStorage(flows=[_flow()], holdings=[_cash()])
    service = _service(tmp_path, monkeypatch, storage)
    service.initialize_fingerprints()
    effect = next(
        item for item in service.review(account="lx")["effects"]
        if item["effect_kind"] == "cash_flow"
    )
    preview = service.preview(effect["effect_id"])
    storage.holdings[("CNY-CASH", "lx", "某券商")].quantity = 101

    with pytest.raises(ValueError, match="stale"):
        service.confirm(
            effect["effect_id"],
            preview_hash=preview["preview_hash"],
            confirm=True,
        )

    assert storage.replacements == []
    assert service.store.get_effect(effect["effect_id"])["state"] == "stale"


def test_holding_change_after_hash_recheck_fails_before_applying(
    tmp_path,
    monkeypatch,
):
    storage = FakeStorage(flows=[_flow()], holdings=[_cash()])
    service = _service(tmp_path, monkeypatch, storage)
    service.initialize_fingerprints()
    effect = next(
        item for item in service.review(account="lx")["effects"]
        if item["effect_kind"] == "cash_flow"
    )
    preview = service.preview(effect["effect_id"])
    original_build_preview = service._build_preview

    def build_then_change(*args, **kwargs):
        recomputed = original_build_preview(*args, **kwargs)
        storage.holdings[("CNY-CASH", "lx", "某券商")].quantity = 120
        return recomputed

    monkeypatch.setattr(service, "_build_preview", build_then_change)

    with pytest.raises(ValueError, match="changed after preview hash validation"):
        service.confirm(
            effect["effect_id"],
            preview_hash=preview["preview_hash"],
            confirm=True,
        )

    assert storage.replacements == []
    assert service.store.get_effect(effect["effect_id"])["state"] == "previewed"


def test_unowned_manual_metadata_change_is_preserved_during_confirmation(
    tmp_path,
    monkeypatch,
):
    cash = _cash()
    cash.tag = ["old"]
    storage = FakeStorage(flows=[_flow()], holdings=[cash])
    service = _service(tmp_path, monkeypatch, storage)
    service.initialize_fingerprints()
    effect = next(
        item for item in service.review(account="lx")["effects"]
        if item["effect_kind"] == "cash_flow"
    )
    preview = service.preview(effect["effect_id"])
    original_build_preview = service._build_preview

    def build_then_change_metadata(*args, **kwargs):
        recomputed = original_build_preview(*args, **kwargs)
        storage.holdings[("CNY-CASH", "lx", "某券商")].tag = ["manual-new"]
        return recomputed

    monkeypatch.setattr(service, "_build_preview", build_then_change_metadata)

    result = service.confirm(
        effect["effect_id"],
        preview_hash=preview["preview_hash"],
        confirm=True,
    )

    holding = storage.holdings[("CNY-CASH", "lx", "某券商")]
    assert result["success"] is True
    assert holding.quantity == 120
    assert holding.tag == ["manual-new"]
    assert len(storage.replacements) == 1


def test_futu_uses_exact_currency_field_allows_negative_and_warns_on_variance(
    tmp_path,
    monkeypatch,
):
    storage = FakeStorage(
        flows=[_flow(amount=10, currency="USD", broker="富途")],
        holdings=[_cash(quantity=100, currency="USD", broker="富途")],
    )
    provider = FakeFutuProvider({"CNY": 1, "USD": -5, "HKD": 2})
    service = _service(
        tmp_path,
        monkeypatch,
        storage,
        futu_provider=provider,
    )
    service.initialize_fingerprints()
    effect = next(
        item for item in service.review(account="lx")["effects"]
        if item["effect_kind"] == "cash_flow"
    )
    preview = service.preview(effect["effect_id"])

    assert preview["targets"][0]["quantity"] == -5.0
    assert preview["target_source"] == "futu_opend_currency_cash"
    assert "variance" in preview["warnings"][0]


def test_non_futu_negative_estimate_blocks_preview(tmp_path, monkeypatch):
    storage = FakeStorage(
        flows=[_flow(amount=-20)],
        holdings=[_cash(quantity=10)],
    )
    service = _service(tmp_path, monkeypatch, storage)
    service.initialize_fingerprints()
    effect = next(
        item for item in service.review(account="lx")["effects"]
        if item["effect_kind"] == "cash_flow"
    )

    with pytest.raises(ValueError, match="cannot be negative"):
        service.preview(effect["effect_id"])

    assert service.store.get_effect(effect["effect_id"])["state"] == "blocked"


def test_direct_feishu_cash_change_requires_explicit_baseline_action(
    tmp_path,
    monkeypatch,
):
    storage = FakeStorage(holdings=[_cash(quantity=100)])
    service = _service(tmp_path, monkeypatch, storage)
    service.initialize_fingerprints()
    storage.holdings[("CNY-CASH", "lx", "某券商")].quantity = 130

    review = service.review(account="lx")
    effect = next(
        item for item in review["effects"]
        if item["effect_kind"] == "cash_holding_external_change"
    )
    assert service.scan()["changed"] == 0
    with pytest.raises(ValueError, match="external_action"):
        service.preview(effect["effect_id"])

    preview = service.preview(
        effect["effect_id"],
        external_action="accept_current",
    )
    result = service.confirm(
        effect["effect_id"],
        preview_hash=preview["preview_hash"],
        external_action="accept_current",
        confirm=True,
    )

    assert result["success"] is True
    assert service.store.get_effect(effect["effect_id"])["state"] == "record_only"
    fingerprint = service.store.get_fingerprint("CNY-CASH|lx|某券商")
    assert fingerprint["last_confirmed_amount"] == "130.00"


def test_account_change_previews_and_confirms_old_and_new_cash_targets(
    tmp_path,
    monkeypatch,
):
    flow = _flow(amount=20)
    storage = FakeStorage(
        flows=[flow],
        holdings=[
            _cash(quantity=100, account="lx"),
            _cash(quantity=50, account="sy"),
        ],
    )
    service = _service(tmp_path, monkeypatch, storage)
    service.initialize_fingerprints()
    first = next(
        item for item in service.review(account="lx")["effects"]
        if item["effect_kind"] == "cash_flow"
    )
    first_preview = service.preview(first["effect_id"])
    service.confirm(
        first["effect_id"],
        preview_hash=first_preview["preview_hash"],
        confirm=True,
    )

    flow.account = "sy"
    correction = next(
        item for item in service.review(account="lx")["effects"]
        if item["effect_kind"] == "cash_flow"
    )
    preview = service.preview(correction["effect_id"])
    targets = {
        (item["account"], item["broker"], item["currency"]): item["quantity"]
        for item in preview["targets"]
    }

    assert targets == {
        ("lx", "某券商", "CNY"): 100.0,
        ("sy", "某券商", "CNY"): 70.0,
    }
    assert service.nav_gate(
        account="lx",
        nav_date=date(2026, 7, 26),
    )["blocker_count"] == 1
    assert service.nav_gate(
        account="sy",
        nav_date=date(2026, 7, 26),
    )["blocker_count"] == 1

    result = service.confirm(
        correction["effect_id"],
        preview_hash=preview["preview_hash"],
        confirm=True,
    )

    assert result["success"] is True
    assert len(result["readbacks"]) == 2
    assert storage.holdings[("CNY-CASH", "lx", "某券商")].quantity == 100.0
    assert storage.holdings[("CNY-CASH", "sy", "某券商")].quantity == 70.0


def test_amount_correction_applies_only_delta_and_deletion_reverses_it(
    tmp_path,
    monkeypatch,
):
    flow = _flow(amount=20)
    storage = FakeStorage(flows=[flow], holdings=[_cash(quantity=100)])
    service = _service(tmp_path, monkeypatch, storage)
    service.initialize_fingerprints()

    first = next(
        item for item in service.review(account="lx")["effects"]
        if item["effect_kind"] == "cash_flow"
    )
    first_preview = service.preview(first["effect_id"])
    service.confirm(
        first["effect_id"],
        preview_hash=first_preview["preview_hash"],
        confirm=True,
    )
    flow.amount = 30
    correction = next(
        item for item in service.review(account="lx")["effects"]
        if item["effect_kind"] == "cash_flow"
    )
    correction_preview = service.preview(correction["effect_id"])
    assert correction_preview["targets"][0]["quantity"] == 130.0
    service.confirm(
        correction["effect_id"],
        preview_hash=correction_preview["preview_hash"],
        confirm=True,
    )

    storage.flows = []
    deletion = next(
        item for item in service.review(account="lx")["effects"]
        if item["effect_kind"] == "cash_flow"
    )
    deletion_preview = service.preview(deletion["effect_id"])

    assert deletion_preview["targets"][0]["quantity"] == 100.0
    result = service.confirm(
        deletion["effect_id"],
        preview_hash=deletion_preview["preview_hash"],
        confirm=True,
    )
    assert result["success"] is True
    assert storage.holdings[("CNY-CASH", "lx", "某券商")].quantity == 100.0


def test_applied_receipt_renders_every_confirmed_cash_target():
    message = CashFlowEffectReceiptService.build_message({
        "receipt_type": "applied",
        "effect_id": "effect_1",
        "payload": {
            "account": "sy",
            "state": "applied",
            "before": {"quantity": "120.00"},
            "befores": [
                {"quantity": "120.00"},
                {"quantity": "50.00"},
            ],
            "targets": [
                {
                    "account": "lx",
                    "broker": "某券商",
                    "currency": "CNY",
                    "quantity": 100.0,
                },
                {
                    "account": "sy",
                    "broker": "某券商",
                    "currency": "CNY",
                    "quantity": 70.0,
                },
            ],
            "warnings": [],
        },
    })

    assert "lx · 某券商 · CNY 120.00 → 100.0" in message
    assert "sy · 某券商 · CNY 50.00 → 70.0" in message


def test_remark_only_change_is_audited_by_scan_without_new_effect_version(
    tmp_path,
    monkeypatch,
):
    flow = _flow()
    storage = FakeStorage(flows=[flow], holdings=[_cash()])
    service = _service(tmp_path, monkeypatch, storage)
    service.initialize_fingerprints()
    first_scan = service.scan()
    first_effect = service.store.get_latest_for_record("cf_1")

    flow.remark = "补充银行流水号"
    second_scan = service.scan()
    second_effect = service.store.get_latest_for_record("cf_1")

    assert second_scan["source_digest"] != first_scan["source_digest"]
    assert second_effect["effect_id"] == first_effect["effect_id"]
    assert second_effect["version"] == first_effect["version"]


def test_partial_multi_target_write_requires_confirmed_compensation_retry(
    tmp_path,
    monkeypatch,
):
    flow = _flow(amount=20)
    storage = FailOnceStorage(
        fail_account="sy",
        flows=[flow],
        holdings=[
            _cash(quantity=100, account="lx"),
            _cash(quantity=50, account="sy"),
        ],
    )
    service = _service(tmp_path, monkeypatch, storage)
    service.initialize_fingerprints()
    first = next(
        effect for effect in service.review()["effects"]
        if effect["effect_kind"] == "cash_flow"
    )
    first_preview = service.preview(first["effect_id"])
    service.confirm(
        first["effect_id"],
        preview_hash=first_preview["preview_hash"],
        confirm=True,
    )

    flow.account = "sy"
    correction = next(
        effect for effect in service.review()["effects"]
        if effect["effect_kind"] == "cash_flow"
    )
    preview = service.preview(correction["effect_id"])
    result = service.confirm(
        correction["effect_id"],
        preview_hash=preview["preview_hash"],
        confirm=True,
    )

    assert result["status"] == "compensation_pending"
    assert storage.holdings[("CNY-CASH", "lx", "某券商")].quantity == 100.0
    assert storage.holdings[("CNY-CASH", "sy", "某券商")].quantity == 50
    with pytest.raises(ValueError, match="requires confirm"):
        service.retry(correction["effect_id"], confirm=False)

    retried = service.retry(correction["effect_id"], confirm=True)
    assert retried["success"] is True
    assert retried["effect"]["state"] == "applied"
    assert storage.holdings[("CNY-CASH", "sy", "某券商")].quantity == 70.0


def test_source_change_waits_for_compensation_then_requires_new_confirmation(
    tmp_path,
    monkeypatch,
):
    flow = _flow(amount=20)
    storage = FailOnceStorage(
        fail_account="sy",
        flows=[flow],
        holdings=[
            _cash(quantity=100, account="lx"),
            _cash(quantity=50, account="sy"),
        ],
    )
    service = _service(tmp_path, monkeypatch, storage)
    service.initialize_fingerprints()
    first = next(
        effect for effect in service.review()["effects"]
        if effect["effect_kind"] == "cash_flow"
    )
    first_preview = service.preview(first["effect_id"])
    service.confirm(
        first["effect_id"],
        preview_hash=first_preview["preview_hash"],
        confirm=True,
    )

    flow.account = "sy"
    correction = next(
        effect for effect in service.review()["effects"]
        if effect["effect_kind"] == "cash_flow"
    )
    correction_preview = service.preview(correction["effect_id"])
    failed = service.confirm(
        correction["effect_id"],
        preview_hash=correction_preview["preview_hash"],
        confirm=True,
    )
    assert failed["status"] == "compensation_pending"

    flow.amount = 30
    flow.cny_amount = 30
    scan = service.scan()
    still_current = service.store.get_latest_for_record("cf_1")

    assert scan["changed"] == 1
    assert still_current["effect_id"] == correction["effect_id"]
    assert still_current["state"] == "compensation_pending"
    assert not [
        effect for effect in service.store.list_effects(latest_only=True)
        if effect["effect_kind"] == "cash_holding_external_change"
    ]
    assert service.nav_gate(
        account="lx",
        nav_date=date(2026, 7, 26),
    )["blocker_count"] == 1
    assert service.nav_gate(
        account="sy",
        nav_date=date(2026, 7, 26),
    )["blocker_count"] == 1

    retried = service.retry(correction["effect_id"], confirm=True)
    assert retried["success"] is False
    assert retried["status"] == "correction_required"
    assert storage.holdings[("CNY-CASH", "sy", "某券商")].quantity == 70.0

    next_effect = service.store.get_effect(retried["correction_effect_id"])
    assert next_effect["state"] == "pending"
    next_preview = service.preview(next_effect["effect_id"])
    assert next_preview["targets"][0]["quantity"] == 80.0
    applied = service.confirm(
        next_effect["effect_id"],
        preview_hash=next_preview["preview_hash"],
        confirm=True,
    )
    assert applied["success"] is True
    assert storage.holdings[("CNY-CASH", "sy", "某券商")].quantity == 80.0


def test_futu_observation_creates_confirmed_reconciliation_without_cash_flow(
    tmp_path,
    monkeypatch,
):
    storage = FakeStorage(
        holdings=[_cash(quantity=100, currency="USD", broker="富途")],
    )
    provider = FakeFutuProvider({"CNY": 0, "USD": 95, "HKD": 0})
    service = _service(
        tmp_path,
        monkeypatch,
        storage,
        futu_provider=provider,
    )
    service.initialize_fingerprints()

    observed = service.observe_futu_cash(
        account="lx",
        cash_by_currency={"USD": 95},
        account_id=123,
        profile_fingerprint="profile-hash",
    )

    assert observed["created"] == 1
    effect = observed["effects"][0]
    assert effect["effect_kind"] == "broker_cash_reconciliation"
    assert storage.replacements == []
    preview = service.preview(effect["effect_id"])
    assert preview["targets"][0]["quantity"] == 95.0
    result = service.confirm(
        effect["effect_id"],
        preview_hash=preview["preview_hash"],
        confirm=True,
    )
    assert result["success"] is True
    assert storage.holdings[("USD-CASH", "lx", "富途")].quantity == 95.0


def test_unresolved_futu_cash_flow_suppresses_competing_reconciliation(
    tmp_path,
    monkeypatch,
):
    storage = FakeStorage(
        flows=[_flow(amount=10, currency="USD", broker="富途")],
        holdings=[_cash(quantity=100, currency="USD", broker="富途")],
    )
    service = _service(
        tmp_path,
        monkeypatch,
        storage,
        futu_provider=FakeFutuProvider({"CNY": 0, "USD": 110, "HKD": 0}),
    )
    service.initialize_fingerprints()
    service.scan()

    observed = service.observe_futu_cash(
        account="lx",
        cash_by_currency={"USD": 110},
        account_id=123,
        profile_fingerprint="profile-hash",
    )

    assert observed["created"] == 0
    assert observed["suppressed_by_cash_flow"] == 1
    assert service.store.get_latest_for_record(
        "futu-reconciliation:USD-CASH|lx|富途",
        effect_kind="broker_cash_reconciliation",
    ) is None


def test_futu_profile_fingerprint_change_invalidates_preview(
    tmp_path,
    monkeypatch,
):
    storage = FakeStorage(
        flows=[_flow(amount=10, currency="USD", broker="富途")],
        holdings=[_cash(quantity=100, currency="USD", broker="富途")],
    )
    provider = FakeFutuProvider({"CNY": 0, "USD": 110, "HKD": 0})
    service = _service(
        tmp_path,
        monkeypatch,
        storage,
        futu_provider=provider,
    )
    service.initialize_fingerprints()
    effect = next(
        item for item in service.review()["effects"]
        if item["effect_kind"] == "cash_flow"
    )
    preview = service.preview(effect["effect_id"])
    provider.profile_fingerprint = "changed-profile"

    with pytest.raises(ValueError, match="stale"):
        service.confirm(
            effect["effect_id"],
            preview_hash=preview["preview_hash"],
            confirm=True,
        )

    assert storage.replacements == []
