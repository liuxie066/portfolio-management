from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, date, datetime
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.app.holding_case_contract import confirmation_scope
from src.app.holdings_nav_preflight_service import HoldingsNavPreflightService
from src.app.account_nav_recorder_service import AccountNavRecorderService
from src.app.holdings_reconciliation_service import HoldingsReconciliationService
from src.app.holdings_workflow_service import HoldingsWorkflowService
from src.app.business_calendar_service import BusinessCalendarService
from src.app.daily_nav_job_service import DailyNavJobService
from src.app.operation_state_store import OperationStateStore
from src.app.portfolio_read_service import PortfolioReadService
from src.domain.holdings import RawHoldingRecord
from src.models import AssetType, NAVHistory, PortfolioValuation


def _raw(
    record_id: str,
    *,
    account: str | None = "lx",
    asset_id: str = "CNY-CASH",
    asset_type: str = "cash",
    broker: str = "IBKR",
    currency: str = "CNY",
    quantity: float = 10,
    asset_class: str = "现金",
) -> RawHoldingRecord:
    return RawHoldingRecord(
        record_id=record_id,
        raw_fields={
            "asset_id": asset_id,
            "asset_name": asset_id,
            "asset_type": asset_type,
            "account": account,
            "broker": broker,
            "quantity": quantity,
            "avg_cost": None,
            "currency": currency,
            "asset_class": asset_class,
            "industry": None,
            "tag": [],
            "created_at": None,
            "updated_at": None,
        },
        fetched_at=datetime(2026, 7, 31, 12, tzinfo=UTC),
    )


class RawStorage:
    def __init__(self, records):
        self.records = list(records)
        self.raw_calls = []

    def get_raw_holdings(self, *, account=None, record_id=None):
        self.raw_calls.append((account, record_id))
        rows = self.records
        if account is not None:
            rows = [
                row
                for row in rows
                if str(row.raw_fields.get("account") or "") == account
            ]
        if record_id is not None:
            rows = [row for row in rows if row.record_id == record_id]
        return list(rows)


def _futu_snapshot(account: str):
    return SimpleNamespace(
        source="futu",
        source_snapshot_id=f"snapshot-{account}",
        observed_at_utc="2026-07-31T12:00:00+00:00",
        profile_fingerprint="profile",
        account_fingerprint=f"account-{account}",
        positions=(),
    )


def _futu_plan_metadata():
    return {
        "provider": "futu-openapi",
        "source_snapshot_id": "snapshot-lx",
        "observed_at_utc": "2026-07-31T11:59:00+00:00",
        "profile_fingerprint": "profile-lx",
        "account_fingerprint": "account-lx",
        "trd_env": "REAL",
        "trd_market": "US",
        "refresh_cache": True,
        "account_verified": True,
        "pagination_complete": True,
        "fund_mmf": {"present": True, "source_field": "fund_assets"},
    }


def _service(storage, *, store=None):
    reconciliation = HoldingsReconciliationService(
        storage=storage,
        futu_observer=_futu_snapshot,
    )
    workflow = HoldingsWorkflowService(
        storage=storage,
        store=store,
        reconciliation=reconciliation,
        lock_factory=lambda _key: nullcontext(),
    )
    return HoldingsNavPreflightService(
        storage=storage,
        reconciliation=reconciliation,
        workflow=workflow,
        lock_factory=lambda _key: nullcontext(),
        now_factory=lambda: datetime(2026, 7, 31, 12, tzinfo=UTC),
    )


def test_validated_snapshot_returns_private_valuation_copies():
    storage = RawStorage([_raw("rec_cash")])

    result = _service(storage).prepare_account(
        account="lx",
        dry_run=True,
        confirm=False,
        trigger={"mode": "test"},
    )

    assert result["success"] is True
    snapshot = result["validated_snapshot"]
    frozen_before = tuple(row.as_dict() for row in snapshot.rows)
    storage.records[0] = _raw("rec_cash", quantity=999)
    valuation_holdings = snapshot.to_valuation_holdings()
    valuation_holdings[0].current_price = 123
    valuation_holdings[0].market_value_cny = 1230
    valuation_holdings[0].weight = 1

    assert tuple(row.as_dict() for row in snapshot.rows) == frozen_before
    assert snapshot.normalized_holdings_digest == result["holdings_snapshot"][
        "normalized_holdings_digest"
    ]
    assert snapshot.rows[0].currency == "CNY"
    assert snapshot.rows[0].quantity == 10


def test_dry_run_futu_projection_replaces_conflicting_mmf_without_mutating_source():
    source = _raw(
        "rec_mmf",
        asset_id="CNY-MMF",
        asset_type="us_stock",
        broker="富途",
        currency="USD",
        quantity=1,
        asset_class="美国资产",
    )
    storage = RawStorage([source])
    result = _service(storage).prepare_account(
        account="lx",
        dry_run=True,
        confirm=False,
        trigger={"mode": "test"},
        project_futu_dry_run=True,
        futu_sync_result={
            "success": True,
            "dry_run": True,
            "source": "futu-openapi",
            "source_snapshot_id": "snapshot-lx",
            "source_metadata": _futu_plan_metadata(),
            "partial_write_possible": False,
            "stages": {"fund_mmf": {"status": "succeeded"}},
            "items": [
                {
                    "asset_id": "CNY-MMF",
                    "target": 20,
                    "created": False,
                    "projected_fields": {
                        "record_id": "rec_mmf",
                        "asset_id": "CNY-MMF",
                        "asset_name": "货币基金",
                        "asset_type": "mmf",
                        "account": "lx",
                        "broker": "富途",
                        "quantity": 20,
                        "avg_cost": None,
                        "currency": "CNY",
                        "asset_class": "现金",
                        "industry": "现金",
                        "tag": [],
                        "created_at": None,
                        "updated_at": None,
                    },
                }
            ],
        },
    )

    assert result["success"] is True
    assert result["holdings_snapshot"]["source_mode"] == "projected_futu_dry_run"
    projected = result["validated_snapshot"].rows[0]
    assert (projected.asset_type, projected.currency, projected.quantity) == (
        "mmf",
        "CNY",
        20,
    )
    assert source.raw_fields["asset_type"] == "us_stock"
    assert source.raw_fields["currency"] == "USD"
    assert source.raw_fields["quantity"] == 1


def test_real_futu_sync_completes_before_fresh_account_validation(monkeypatch):
    storage = RawStorage(
        [
            _raw(
                "rec_mmf",
                asset_id="CNY-MMF",
                asset_type="us_stock",
                broker="富途",
                currency="USD",
                quantity=1,
                asset_class="美国资产",
            )
        ]
    )

    class FakeSyncService:
        def __init__(self, resolved_storage):
            self.storage = resolved_storage

        def sync_cash_and_mmf(self, **_kwargs):
            self.storage.records = [
                _raw(
                    "rec_mmf",
                    asset_id="CNY-MMF",
                    asset_type="mmf",
                    broker="富途",
                    currency="CNY",
                    quantity=20,
                    asset_class="现金",
                )
            ]
            return {
                "success": True,
                "dry_run": False,
                "items": [],
                "cash_observations": {},
            }

    class FakePortfolio:
        cash_flow_dataset = SimpleNamespace(
            details=lambda: {"financial_fingerprint": "preflight-dataset"}
        )

        def build_cash_flow_dataset(self, **kwargs):
            assert kwargs == {
                "account": "lx",
                "nav_date": date(2026, 7, 31),
                "run_id": "run-sync-first",
            }
            return self.cash_flow_dataset

        def calculate_valuation(self, account, **kwargs):
            holdings = list(kwargs["holdings"])
            assert len(holdings) == 1
            assert holdings[0].asset_type == AssetType.MMF
            assert holdings[0].currency == "CNY"
            assert holdings[0].quantity == 20
            return PortfolioValuation(
                account=account,
                total_value_cny=20,
                cash_value_cny=20,
                holdings=holdings,
            )

        def record_nav(self, account, *, valuation, nav_date, **_kwargs):
            assert _kwargs["cash_flow_dataset"] is self.cash_flow_dataset
            assert valuation.holdings_provenance[
                "normalized_holdings_digest"
            ]
            return NAVHistory(
                date=nav_date,
                account=account,
                total_value=20,
                cash_value=20,
                stock_value=0,
                fund_value=0,
                shares=20,
                nav=1,
            )

    monkeypatch.setattr("src.app.FutuBalanceSyncService", FakeSyncService)
    portfolio = FakePortfolio()
    read_service = PortfolioReadService(
        account="lx",
        storage=storage,
        portfolio=portfolio,
        reporting_service=Mock(),
    )

    result = AccountNavRecorderService(
        account="lx",
        storage=storage,
        portfolio=portfolio,
        read_service=read_service,
        holdings_preflight=_service(storage),
    ).record(
        nav_date="2026-07-31",
        dry_run=False,
        confirm=True,
        sync_futu_cash_mmf=True,
        run_id="run-sync-first",
    )

    assert result["success"] is True
    assert "cash_effects" not in result["futu_sync_result"]
    assert result["holdings_snapshot"]["normalized_holdings_digest"]
    assert storage.raw_calls == [("lx", None)]


def test_incomplete_futu_projection_fails_closed():
    storage = RawStorage([_raw("rec_mmf", asset_id="CNY-MMF", asset_type="mmf")])

    with pytest.raises(ValueError, match="lacks complete holding fields"):
        _service(storage).prepare_account(
            account="lx",
            dry_run=True,
            confirm=False,
            trigger={"mode": "test"},
            project_futu_dry_run=True,
            futu_sync_result={
                "success": True,
                "dry_run": True,
                "source": "futu-openapi",
                "source_snapshot_id": "snapshot-lx",
                "source_metadata": _futu_plan_metadata(),
                "partial_write_possible": False,
                "stages": {"fund_mmf": {"status": "succeeded"}},
                "items": [
                    {
                        "asset_id": "CNY-MMF",
                        "target": 20,
                        "created": False,
                        "projected_fields": {"asset_id": "CNY-MMF"},
                    }
                ],
            },
        )


def test_formal_nav_rejects_futu_dry_run_projection():
    storage = RawStorage([_raw("rec_mmf", asset_id="CNY-MMF", asset_type="mmf")])

    with pytest.raises(ValueError, match="formal NAV cannot consume"):
        _service(storage).prepare_account(
            account="lx",
            dry_run=False,
            confirm=True,
            trigger={"mode": "daily_nav_preflight"},
            project_futu_dry_run=True,
            futu_sync_result={"success": True, "dry_run": True},
        )

    assert storage.raw_calls == []


def test_account_recorder_rejects_formal_futu_projection_before_sync(monkeypatch):
    class RefusingSync:
        def __init__(self, _storage):
            raise AssertionError("formal projected sync must not start")

    monkeypatch.setattr("src.app.FutuBalanceSyncService", RefusingSync)
    result = AccountNavRecorderService(
        account="lx",
        storage=RawStorage([_raw("rec_mmf", asset_type="mmf")]),
        portfolio=SimpleNamespace(),
        read_service=SimpleNamespace(),
        holdings_preflight=Mock(),
    ).record(
        nav_date="2026-07-31",
        dry_run=False,
        confirm=True,
        sync_futu_cash_mmf=True,
        sync_futu_dry_run=True,
        run_id="run-formal-projection-refused",
    )

    assert result["success"] is False
    assert result["status"] == "holdings_preflight_failed"
    assert result["error"] == "formal NAV cannot consume a Futu dry-run projection"


def test_formal_conflict_materializes_case_without_individual_receipt(tmp_path):
    storage = RawStorage(
        [
            _raw(
                "rec_conflict",
                asset_id="AAPL.US",
                asset_type="us_stock",
                currency="CNY",
                asset_class="美国资产",
            )
        ]
    )
    store = OperationStateStore(tmp_path / "operations.sqlite3")

    result = _service(storage, store=store).prepare_account(
        account="lx",
        dry_run=False,
        confirm=True,
        trigger={"mode": "daily_nav_preflight", "run_id": "run-1"},
    )

    assert result["success"] is False
    assert result["status"] == "holdings_confirmation_required"
    cases = store.list_holding_cases(account="lx")
    assert any(case["kind"] == "conflict" for case in cases)
    conflict = next(case for case in cases if case["kind"] == "conflict")
    receipt = store.get_operation_receipt(
        f"holdings:case:discovered:{conflict['case_key']}"
    )
    assert receipt is None
    assert result["workflow"]["enqueued_receipt_keys"] == []
    assert result["action_item_count"] == 1
    assert result["action_item_omitted_count"] == 0
    assert result["action_items"] == [
        {
            "case_key": conflict["case_key"],
            "record_id": "rec_conflict",
            "field": "currency",
            "state": "pending_confirmation",
            "command": (
                f"pm holdings resolve --case-key {conflict['case_key']} "
                "--decision accept-proposed|keep-current --reason REASON --confirm"
            ),
        }
    ]


def test_matching_keep_current_confirmation_unblocks_exact_conflict(tmp_path):
    storage = RawStorage(
        [
            _raw(
                "rec_conflict",
                asset_id="AAPL.US",
                asset_type="us_stock",
                currency="CNY",
                asset_class="美国资产",
            )
        ]
    )
    store = OperationStateStore(tmp_path / "operations.sqlite3")
    service = _service(storage, store=store)
    blocked = service.prepare_account(
        account="lx",
        dry_run=False,
        confirm=True,
        trigger={"mode": "daily_nav_preflight"},
    )
    conflict = next(
        case
        for case in store.list_holding_cases(account="lx")
        if case["kind"] == "conflict" and case["blocks_official_nav"]
    )

    resolved = service.workflow.resolve(
        case_key=conflict["case_key"],
        decision="keep-current",
        reason="operator verified instrument classification",
        confirmed_operator={"username": "tester", "trusted_identity": False},
    )
    allowed = service.prepare_account(
        account="lx",
        dry_run=True,
        confirm=False,
        trigger={"mode": "daily_nav_preflight"},
    )

    assert blocked["success"] is False
    assert resolved["status"] == "resolved_keep"
    assert allowed["success"] is True
    assert allowed["validated_snapshot"].rows[0].asset_type == "us_stock"
    assert "confirmed keep-current" in " ".join(allowed["warnings"])
    assert allowed["pending_case_keys"] == []
    assert allowed["action_items"] == []
    assert allowed["action_item_count"] == 0


def test_nav_preflight_preserves_and_migrates_legacy_non_dependent_keep(tmp_path):
    initial = _raw(
        "rec_legacy_name",
        asset_id="AAPL",
        asset_type="hk_stock",
        broker="富途",
        currency="USD",
        asset_class="美国资产",
    )
    initial.raw_fields["asset_name"] = "Manual Apple"
    storage = RawStorage([initial])

    def observe(_account):
        return SimpleNamespace(
            source="futu",
            source_snapshot_id="snapshot-lx",
            observed_at_utc="2026-07-31T12:00:00+00:00",
            profile_fingerprint="profile-lx",
            account_fingerprint="account-lx",
            positions=(
                SimpleNamespace(
                    asset_id="AAPL.US",
                    raw_code="US.AAPL",
                    asset_name="Apple",
                    security_type="STOCK",
                    market="US",
                    currency="USD",
                    currency_explicit=True,
                ),
            ),
        )

    store = OperationStateStore(tmp_path / "operations.sqlite3")
    reconciliation = HoldingsReconciliationService(
        storage=storage,
        futu_observer=observe,
    )
    workflow = HoldingsWorkflowService(
        storage=storage,
        store=store,
        reconciliation=reconciliation,
        lock_factory=lambda _key: nullcontext(),
    )
    service = HoldingsNavPreflightService(
        storage=storage,
        reconciliation=reconciliation,
        workflow=workflow,
        lock_factory=lambda _key: nullcontext(),
    )
    initial_evaluation = reconciliation.evaluate(account="lx")
    name_case = next(
        case
        for case in workflow._cases_for_record(
            initial_evaluation.report.records[0], initial_evaluation
        )
        if case["field"] == "asset_name"
    )
    legacy = dict(name_case)
    legacy["case_precondition_digest"] = name_case[
        "legacy_case_precondition_digest"
    ]
    store.materialize_holding_cases(
        cases=[legacy],
        discovery_receipts=[workflow._discovery_receipt(legacy)],
        trigger={"mode": "seed_legacy_keep"},
    )
    legacy_scope = confirmation_scope(legacy)
    resolution = {
        "decision": "keep-current",
        "reason": "operator verified display name",
        "operator_context": {"username": "tester"},
        "confirmation_scope": legacy_scope,
    }
    store.finalize_holding_cases(
        outcomes=[
            {
                "case_key": legacy["case_key"],
                "state": "resolved_keep",
                "event_type": "resolved_keep",
                "resolution": resolution,
            }
        ],
        receipts=[
            workflow._terminal_receipt(
                legacy,
                state="resolved_keep",
                resolution=resolution,
                resolution_digest=legacy_scope,
            )
        ],
    )
    corrected = _raw(
        "rec_legacy_name",
        asset_id="AAPL",
        asset_type="us_stock",
        broker="富途",
        currency="USD",
        asset_class="美国资产",
    )
    corrected.raw_fields["asset_name"] = "Manual Apple"
    storage.records = [corrected]
    with store._connect() as conn:
        receipt_count = conn.execute(
            "SELECT COUNT(*) FROM operation_receipt_outbox"
        ).fetchone()[0]

    dry_run = service.prepare_account(
        account="lx",
        dry_run=True,
        confirm=False,
        trigger={"mode": "daily_nav_preflight"},
    )

    assert dry_run["success"] is True
    assert "confirmed keep-current" in " ".join(dry_run["warnings"])
    assert store.get_holding_case(legacy["case_key"])[
        "case_precondition_digest"
    ] == legacy["case_precondition_digest"]

    formal = service.prepare_account(
        account="lx",
        dry_run=False,
        confirm=True,
        trigger={"mode": "daily_nav_preflight"},
    )

    durable = store.get_holding_case(legacy["case_key"])
    assert formal["success"] is True
    assert durable["state"] == "resolved_keep"
    assert durable["case_precondition_digest"].startswith(
        "holdings-precondition.v2:"
    )
    assert durable["resolution"]["confirmation_scope"] == confirmation_scope(
        next(
            case
            for case in workflow.plan_evaluation(
                reconciliation.evaluate(account="lx"),
                trigger={"mode": "assert"},
            )["cases"]
            if case["field"] == "asset_name"
        )
    )
    assert [
        event["event_type"]
        for event in store.list_holding_case_events(legacy["case_key"])
    ].count("precondition_contract_migrated") == 1
    with store._connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM operation_receipt_outbox"
        ).fetchone()[0] == receipt_count


def test_matching_futu_keep_current_survives_later_provider_outage(tmp_path):
    storage = RawStorage(
        [
            _raw(
                "rec_futu_conflict",
                asset_id="AAPL",
                asset_type="hk_stock",
                broker="富途",
                currency="HKD",
                asset_class="香港资产",
            )
        ]
    )

    def observe(_account):
        return SimpleNamespace(
            source="futu",
            source_snapshot_id="snapshot-lx",
            observed_at_utc="2026-07-31T12:00:00+00:00",
            profile_fingerprint="profile-lx",
            account_fingerprint="account-lx",
            positions=(
                SimpleNamespace(
                    asset_id="AAPL.US",
                    raw_code="US.AAPL",
                    asset_name="Apple",
                    security_type="STOCK",
                    market="US",
                    currency="USD",
                    currency_explicit=True,
                ),
            ),
        )

    store = OperationStateStore(tmp_path / "operations.sqlite3")
    reconciliation = HoldingsReconciliationService(
        storage=storage,
        futu_observer=observe,
    )
    workflow = HoldingsWorkflowService(
        storage=storage,
        store=store,
        reconciliation=reconciliation,
        lock_factory=lambda _key: nullcontext(),
    )
    service = HoldingsNavPreflightService(
        storage=storage,
        reconciliation=reconciliation,
        workflow=workflow,
        lock_factory=lambda _key: nullcontext(),
    )
    blocked = service.prepare_account(
        account="lx",
        dry_run=False,
        confirm=True,
        trigger={"mode": "daily_nav_preflight"},
    )
    type_case = next(
        case
        for case in store.list_holding_cases(account="lx")
        if case["field"] == "asset_type"
    )
    workflow.resolve(
        case_key=type_case["case_key"],
        decision="keep-current",
        reason="operator verified the HK classification",
        confirmed_operator={"username": "tester", "trusted_identity": False},
    )
    fresh_type_case = next(
        case
        for case in workflow.plan_evaluation(
            reconciliation.evaluate(account="lx"),
            trigger={"mode": "seed_legacy_outage_keep"},
        )["cases"]
        if case["field"] == "asset_type"
    )
    legacy_type_case = dict(fresh_type_case)
    legacy_type_case["case_precondition_digest"] = fresh_type_case[
        "legacy_case_precondition_digest"
    ]
    legacy_resolution = dict(
        store.get_holding_case(type_case["case_key"])["resolution"]
    )
    legacy_resolution["confirmation_scope"] = confirmation_scope(
        legacy_type_case
    )
    with store._connect() as conn:
        conn.execute(
            "UPDATE holding_reconciliation_cases "
            "SET case_precondition_digest = ?, resolution_json = ? "
            "WHERE case_key = ?",
            (
                legacy_type_case["case_precondition_digest"],
                json.dumps(
                    legacy_resolution,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                type_case["case_key"],
            ),
        )
    reconciliation.futu_observer = lambda _account: (_ for _ in ()).throw(
        RuntimeError("OpenD unavailable")
    )

    allowed = service.prepare_account(
        account="lx",
        dry_run=True,
        confirm=False,
        trigger={"mode": "daily_nav_preflight"},
    )

    assert blocked["success"] is False
    assert type_case["authority_id"].startswith("futu:")
    assert allowed["success"] is True
    assert store.get_holding_case(type_case["case_key"])[
        "case_precondition_digest"
    ] == legacy_type_case["case_precondition_digest"]
    assert allowed["validated_snapshot"].rows[0].asset_type == "hk_stock"
    assert "confirmed keep-current during evidence outage" in " ".join(
        allowed["warnings"]
    )
    storage.records = [
        _raw(
            "rec_futu_conflict",
            asset_id="AAPL",
            asset_type="us_stock",
            broker="富途",
            currency="HKD",
            asset_class="美国资产",
        )
    ]

    drifted = service.prepare_account(
        account="lx",
        dry_run=True,
        confirm=False,
        trigger={"mode": "daily_nav_preflight"},
    )

    assert drifted["success"] is False
    assert "confirmed keep-current during evidence outage" not in " ".join(
        drifted.get("warnings") or []
    )


def test_provider_outage_does_not_hide_an_unconfirmed_deterministic_conflict(tmp_path):
    storage = RawStorage(
        [
            _raw(
                "rec_unconfirmed",
                asset_id="AAPL.US",
                asset_type="us_stock",
                broker="富途",
                currency="CNY",
                asset_class="美国资产",
            )
        ]
    )
    reconciliation = HoldingsReconciliationService(
        storage=storage,
        futu_observer=lambda _account: (_ for _ in ()).throw(
            RuntimeError("OpenD unavailable")
        ),
    )
    workflow = HoldingsWorkflowService(
        storage=storage,
        store=OperationStateStore(tmp_path / "operations.sqlite3"),
        reconciliation=reconciliation,
        lock_factory=lambda _key: nullcontext(),
    )
    service = HoldingsNavPreflightService(
        storage=storage,
        reconciliation=reconciliation,
        workflow=workflow,
        lock_factory=lambda _key: nullcontext(),
    )

    result = service.prepare_account(
        account="lx",
        dry_run=True,
        confirm=False,
        trigger={"mode": "daily_nav_preflight"},
    )

    assert result["success"] is False
    assert result["status"] == "holdings_evidence_unavailable"
    assert result["blocking_case_keys"]


def test_formal_nonblocking_case_materializes_then_valuation_can_continue(tmp_path):
    storage = RawStorage([_raw("rec_cash", asset_class=None)])
    store = OperationStateStore(tmp_path / "operations.sqlite3")

    result = _service(storage, store=store).prepare_account(
        account="lx",
        dry_run=False,
        confirm=True,
        trigger={"mode": "daily_nav_preflight"},
    )

    assert result["success"] is True
    assert result["status"] == "valid_with_warnings"
    assert result["warnings"]
    cases = store.list_holding_cases(account="lx")
    assert len(cases) == 1
    assert cases[0]["blocks_official_nav"] is False
    assert result["validated_snapshot"].rows[0].asset_class is None


def test_json_text_empty_tag_does_not_create_a_case_or_nav_warning(tmp_path):
    base = _raw("rec_text_empty_tag")
    record = RawHoldingRecord(
        record_id=base.record_id,
        raw_fields={**base.raw_fields, "tag": "[]"},
        source=base.source,
        fetched_at=base.fetched_at,
    )
    store = OperationStateStore(tmp_path / "operations.sqlite3")

    result = _service(RawStorage([record]), store=store).prepare_account(
        account="lx",
        dry_run=True,
        confirm=False,
        trigger={"mode": "daily_nav_preflight"},
    )

    assert result["success"] is True
    assert result["status"] == "valid"
    assert result["warnings"] == []
    assert result["case_keys"] == []
    assert result["blocking_case_keys"] == []
    assert result["validated_snapshot"].rows[0].tag == ()
    assert store.list_holding_cases(account="lx") == []


def test_dry_run_reports_cases_without_materializing_state():
    class RefusingStore:
        def get_holding_case(self, _case_key):
            return None

        def materialize_holding_cases(self, **_kwargs):
            raise AssertionError("dry-run must not materialize workflow state")

    storage = RawStorage(
        [_raw("rec_conflict", asset_type="us_stock", asset_class="美国资产")]
    )
    result = _service(storage, store=RefusingStore()).prepare_account(
        account="lx",
        dry_run=True,
        confirm=False,
        trigger={"mode": "test"},
    )

    assert result["success"] is False
    assert result["would_materialize"] is True
    assert result["case_keys"]


def test_operation_state_materialization_failure_blocks_account():
    class BrokenStore:
        def get_holding_case(self, _case_key):
            return None

        def materialize_holding_cases(self, **_kwargs):
            raise RuntimeError("sqlite unavailable")

    storage = RawStorage(
        [_raw("rec_conflict", asset_type="us_stock", asset_class="美国资产")]
    )
    result = _service(storage, store=BrokenStore()).prepare_account(
        account="lx",
        dry_run=False,
        confirm=True,
        trigger={"mode": "daily_nav_preflight"},
    )

    assert result["success"] is False
    assert result["status"] == "holdings_preflight_failed"
    assert "sqlite unavailable" in result["error"]


def test_formal_valid_account_still_fails_closed_when_state_store_is_unavailable():
    class BrokenStore:
        def get_holding_case(self, _case_key):
            return None

        def materialize_holding_cases(self, **_kwargs):
            raise RuntimeError("sqlite unavailable")

    storage = RawStorage([_raw("rec_valid")])
    result = _service(storage, store=BrokenStore()).prepare_account(
        account="lx",
        dry_run=False,
        confirm=True,
        trigger={"mode": "daily_nav_preflight"},
    )

    assert result["success"] is False
    assert result["status"] == "holdings_preflight_failed"
    assert "sqlite unavailable" in result["error"]


def test_formal_preflight_closes_account_case_after_fresh_repair(tmp_path):
    storage = RawStorage(
        [
            _raw(
                "rec_conflict",
                asset_id="AAPL.US",
                asset_type="us_stock",
                currency="CNY",
                asset_class="美国资产",
            )
        ]
    )
    store = OperationStateStore(tmp_path / "operations.sqlite3")
    service = _service(storage, store=store)
    blocked = service.prepare_account(
        account="lx",
        dry_run=False,
        confirm=True,
        trigger={"mode": "daily_nav_preflight"},
    )
    conflict = next(
        case
        for case in store.list_holding_cases(account="lx")
        if case["field"] == "currency"
    )
    storage.records = [
        _raw(
            "rec_conflict",
            asset_id="AAPL.US",
            asset_type="us_stock",
            currency="USD",
            asset_class="美国资产",
        )
    ]

    repaired = service.prepare_account(
        account="lx",
        dry_run=False,
        confirm=True,
        trigger={"mode": "daily_nav_preflight"},
    )

    assert blocked["success"] is False
    assert repaired["success"] is True
    assert store.get_holding_case(conflict["case_key"])["state"] == (
        "resolved_external"
    )


def test_global_orphans_create_one_case_without_individual_receipt(tmp_path):
    storage = RawStorage(
        [
            _raw("orphan_1", account=None),
            _raw("orphan_2", account=None, asset_id="USD-CASH", currency="USD"),
            _raw("owned", account="lx"),
        ]
    )
    store = OperationStateStore(tmp_path / "operations.sqlite3")

    result = _service(storage, store=store).scan_global_orphans(
        dry_run=False,
        confirm=True,
        trigger={"mode": "daily_nav_global_preflight", "run_id": "run-1"},
    )

    assert result["success"] is False
    assert result["orphan_count"] == 2
    cases = store.list_holding_cases()
    assert len(cases) == 1
    assert cases[0]["kind"] == "orphan_global"
    receipt = store.get_operation_receipt(
        f"holdings:case:discovered:{cases[0]['case_key']}"
    )
    assert receipt is None
    assert result["workflow"]["enqueued_receipt_keys"] == []
    assert result["action_items"][0]["command"] == (
        "pm holdings reconcile --notify --confirm"
    )


def test_formal_global_scan_closes_orphan_case_after_fresh_repair(tmp_path):
    storage = RawStorage([_raw("orphan", account=None)])
    store = OperationStateStore(tmp_path / "operations.sqlite3")
    service = _service(storage, store=store)
    blocked = service.scan_global_orphans(
        dry_run=False,
        confirm=True,
        trigger={"mode": "daily_nav_global_preflight"},
    )
    case = store.list_holding_cases()[0]
    storage.records = [_raw("orphan", account="lx")]

    repaired = service.scan_global_orphans(
        dry_run=False,
        confirm=True,
        trigger={"mode": "daily_nav_global_preflight"},
    )

    assert blocked["success"] is False
    assert repaired["success"] is True
    assert store.get_holding_case(case["case_key"])["state"] == (
        "resolved_external"
    )
    receipt_keys = repaired["workflow"]["enqueued_receipt_keys"]
    assert receipt_keys == []
    assert repaired["workflow"]["closed_case_keys"] == [case["case_key"]]
    with sqlite3.connect(store.db_path) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM operation_receipt_outbox"
        ).fetchone()[0] == 0


def test_preflight_action_items_are_bounded_and_keep_frozen_commands(tmp_path):
    storage = RawStorage(
        [
            _raw(
                f"rec_conflict_{index}",
                asset_id=f"ASSET-{index}.US",
                asset_type="us_stock",
                currency="CNY",
                asset_class="美国资产",
            )
            for index in range(6)
        ]
    )
    result = _service(
        storage,
        store=OperationStateStore(tmp_path / "operations.sqlite3"),
    ).prepare_account(
        account="lx",
        dry_run=True,
        confirm=False,
        trigger={"mode": "daily_nav_preflight"},
    )

    assert result["success"] is False
    assert result["action_item_count"] == 6
    assert len(result["action_items"]) == 5
    assert result["action_item_omitted_count"] == 1
    first = result["action_items"][0]
    assert first["record_id"] == "rec_conflict_0"
    assert first["case_key"] in first["command"]
    assert "accept-proposed|keep-current" in first["command"]


def test_attributed_blocker_is_account_local():
    storage = RawStorage(
        [
            _raw("lx_bad", account="lx", currency="USD"),
            _raw("sy_good", account="sy"),
        ]
    )
    service = _service(storage)

    lx = service.prepare_account(
        account="lx",
        dry_run=True,
        confirm=False,
        trigger={"mode": "test"},
    )
    sy = service.prepare_account(
        account="sy",
        dry_run=True,
        confirm=False,
        trigger={"mode": "test"},
    )

    assert lx["status"] == "holdings_confirmation_required"
    assert sy["success"] is True
    assert sy["validated_snapshot"].account == "sy"


def test_global_source_failure_blocks_every_ready_account_without_running_accounts():
    class Storage:
        def audit_nav_history_duplicates(self, account=None):
            return {"duplicate_group_count": 0}

        def reconcile_cash_flows(self, **_kwargs):
            return {"success": True, "change_count": 0, "error_count": 0}

        def get_nav_on_date(self, account, nav_date):
            return None

        def get_raw_holdings(self, **_kwargs):
            raise RuntimeError("holdings pagination incomplete")

    result = DailyNavJobService(
        storage=Storage(),
        portfolio=SimpleNamespace(reporting_service=object()),
        calendar=BusinessCalendarService(),
        account_runner_factory=lambda _account: (_ for _ in ()).throw(
            AssertionError("account runner must not run")
        ),
    ).run(
        nav_date="2026-07-31",
        accounts=["lx", "sy"],
        dry_run=True,
    )

    assert result["success"] is False
    assert result["status"] == "partial"
    assert [item["status"] for item in result["items"]] == [
        "holdings_preflight_failed",
        "holdings_preflight_failed",
    ]
    assert all("pagination incomplete" in item["error"] for item in result["items"])


def test_existing_final_nav_skips_before_global_holdings_gate():
    class Storage:
        def audit_nav_history_duplicates(self, account=None):
            return {"duplicate_group_count": 0}

        def reconcile_cash_flows(self, **_kwargs):
            return {"success": True, "change_count": 0, "error_count": 0}

        def get_nav_on_date(self, account, nav_date):
            return SimpleNamespace(
                record_id="nav-final",
                nav=1,
                total_value=100,
                details={
                    "finality": {
                        "version": 1,
                        "status": "final",
                        "nav_date": "2026-07-31",
                        "valuation_as_of": "2026-07-31T18:00:00",
                        "writer": "daily-nav-job",
                        "write_reason": "canonical_daily_nav_job",
                    }
                },
            )

    class RefusingPreflight:
        def scan_global_orphans(self, **_kwargs):
            raise AssertionError("existing final NAV must skip holdings preflight")

    result = DailyNavJobService(
        storage=Storage(),
        portfolio=SimpleNamespace(reporting_service=object()),
        calendar=BusinessCalendarService(),
        holdings_preflight=RefusingPreflight(),
        account_runner_factory=lambda _account: (_ for _ in ()).throw(
            AssertionError("account runner must not run")
        ),
    ).run(nav_date="2026-07-31", account="lx", dry_run=True)

    assert result["success"] is True
    assert result["items"][0]["status"] == "skipped_existing_nav"


def test_portfolio_read_uses_explicit_holdings_and_attaches_same_digest():
    private_holding = _service(RawStorage([_raw("rec_cash")])).prepare_account(
        account="lx",
        dry_run=True,
        confirm=False,
        trigger={"mode": "test"},
    )["validated_snapshot"]
    valuation = PortfolioValuation(account="lx", holdings=[])
    portfolio = SimpleNamespace(calculate_valuation=Mock(return_value=valuation))
    storage = Mock()
    read_service = PortfolioReadService(
        account="lx",
        storage=storage,
        portfolio=portfolio,
        reporting_service=Mock(),
    )
    holdings = private_holding.to_valuation_holdings()
    provenance = private_holding.provenance()

    snapshot = read_service.build_snapshot(
        holdings=holdings,
        holdings_provenance=provenance,
    )

    portfolio.calculate_valuation.assert_called_once_with("lx", holdings=holdings)
    storage.get_holdings.assert_not_called()
    assert snapshot["holdings_snapshot"] == provenance
    assert valuation.holdings_provenance == provenance


def test_nav_history_details_preserve_holdings_provenance_via_valuation():
    provenance = {
        "account": "lx",
        "normalized_holdings_digest": "abc123",
        "raw_record_digest": "raw123",
    }
    valuation = PortfolioValuation(
        account="lx",
        holdings_provenance=provenance,
    )
    assert valuation.holdings_provenance == provenance

    nav = NAVHistory(
        date=date(2026, 7, 31),
        account="lx",
        total_value=0,
        details={"holdings_snapshot": valuation.holdings_provenance},
    )
    assert nav.details["holdings_snapshot"]["normalized_holdings_digest"] == "abc123"
