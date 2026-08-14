from __future__ import annotations

from datetime import date
import json
from types import SimpleNamespace

import pytest

from src.app.account_nav_recorder_service import AccountNavRecorderService
from src.app.nav_finality import NavWriteContext
from src.app.nav_valuation_evidence_service import NavValuationEvidenceStore
from src.domain.cash_flow_contracts import (
    CashFlowDatasetBlocker,
    CashFlowDatasetRefusal,
)
from src.domain.snapshot_contracts import NormalizedValuationRow, NormalizedValuationSnapshot
from src.models import AssetClass, AssetType, Holding


HOLDINGS_DIGEST = "1" * 64
CASH_FLOW_FINGERPRINT = "2" * 64


def _official(account: str = "lx") -> NormalizedValuationSnapshot:
    holding = Holding(
        record_id="rec_1",
        asset_id="CNY-CASH",
        asset_name="人民币现金",
        asset_type=AssetType.CASH,
        account=account,
        broker="平安证券",
        quantity=100,
        currency="CNY",
        asset_class=AssetClass.CASH,
    )
    return NormalizedValuationSnapshot._from_valuation_service(
        account=account,
        rows=(
            NormalizedValuationRow.from_holding(
                holding,
                account=account,
                normalized_type="cash",
                price=1,
                cny_price=1,
                source="fixed",
            ),
        ),
        shares=100,
        price_evidence={
            "CNY-CASH": {
                "price": 1,
                "cny_price": 1,
                "currency": "CNY",
                "source": "fixed",
            }
        },
        holdings_provenance={
            "normalized_holdings_digest": HOLDINGS_DIGEST,
        },
        source_provenance={"price_mode": "snapshot"},
    )


def _prepared(store: NavValuationEvidenceStore) -> dict:
    return store.prepare(
        account="lx",
        nav_date="2026-08-13",
        source_run_id="daily-nav-job-source:lx",
        snapshot_time="2026-08-14T08:11:45.216546",
        holdings_digest=HOLDINGS_DIGEST,
        cash_flow_financial_fingerprint=CASH_FLOW_FINGERPRINT,
        source_effect_store_revision="cfs_source",
        normalized_valuation=_official(),
        preparation="cash_flow_gate_failure",
    )


def test_evidence_store_round_trips_idempotently_and_detects_tampering(tmp_path):
    store = NavValuationEvidenceStore(tmp_path)
    prepared = _prepared(store)

    first = store.save(prepared)
    second = store.save(prepared)
    loaded = store.load(
        first["valuation_ref"],
        expected_account="lx",
        expected_nav_date=date(2026, 8, 13),
    )

    assert second == first
    assert loaded["normalized_valuation"].official_eligible is True
    assert loaded["normalized_valuation"].digest == _official().digest

    path = tmp_path / "lx" / "2026-08-13" / f"{first['artifact_digest']}.json"
    artifact = json.loads(path.read_text(encoding="utf-8"))
    artifact["source_run_id"] = "tampered"
    path.write_text(json.dumps(artifact), encoding="utf-8")
    with pytest.raises(ValueError, match="artifact digest mismatch"):
        store.load(first["valuation_ref"])


def test_evidence_store_rejects_holdings_digest_not_bound_to_valuation(tmp_path):
    store = NavValuationEvidenceStore(tmp_path)

    with pytest.raises(ValueError, match="holdings digest mismatch"):
        store.prepare(
            account="lx",
            nav_date="2026-08-13",
            source_run_id="daily-nav-job-source:lx",
            snapshot_time="2026-08-14T08:11:45.216546",
            holdings_digest="9" * 64,
            cash_flow_financial_fingerprint=CASH_FLOW_FINGERPRINT,
            source_effect_store_revision="cfs_source",
            normalized_valuation=_official(),
            preparation="cash_flow_gate_failure",
        )


class _Validated:
    normalized_holdings_digest = HOLDINGS_DIGEST
    warnings = ()

    @staticmethod
    def provenance():
        return {"normalized_holdings_digest": HOLDINGS_DIGEST}

    @staticmethod
    def to_valuation_holdings():
        return []


class _Preflight:
    @staticmethod
    def prepare_account(**_kwargs):
        return {
            "success": True,
            "validated_snapshot": _Validated(),
            "holdings_snapshot": _Validated.provenance(),
        }


class _Dataset:
    account = "lx"
    nav_date = date(2026, 8, 13)
    run_id = "daily-nav-job-source:lx"
    financial_fingerprint = CASH_FLOW_FINGERPRINT
    effect_store_revision = "cfs_source"

    def details(self):
        return {
            "financial_fingerprint": self.financial_fingerprint,
            "effect_store_revision": self.effect_store_revision,
        }


def _blocked_refusal(reason_code: str = "CASH_FLOW_DATASET_BLOCKED"):
    return CashFlowDatasetRefusal(
        reason_code=reason_code,
        message="blocked",
        blockers=(
            CashFlowDatasetBlocker(
                reason_code="EFFECT_GATE_BLOCKED",
                message="pending",
            ),
        ),
    )


def test_daily_gate_failure_captures_evidence_but_scope_failure_does_not(tmp_path):
    snapshot = {
        "valuation": _official().to_portfolio_valuation(),
        "normalized_valuation": _official(),
        "snapshot_time": "2026-08-14T08:11:45.216546",
    }
    store = NavValuationEvidenceStore(tmp_path)

    class Read:
        @staticmethod
        def build_snapshot(**_kwargs):
            return snapshot

    class Portfolio:
        @staticmethod
        def build_cash_flow_dataset(**_kwargs):
            return _Dataset()

        @staticmethod
        def record_nav(*_args, **_kwargs):
            raise _blocked_refusal()

    context = NavWriteContext(
        status="final",
        writer="daily-nav-job",
        write_reason="canonical_daily_nav_job",
        nav_date=date(2026, 8, 13),
        run_id="daily-nav-job-source:lx",
    )
    result = AccountNavRecorderService(
        account="lx",
        storage=SimpleNamespace(),
        portfolio=Portfolio(),
        read_service=Read(),
        holdings_preflight=_Preflight(),
        valuation_evidence_store=store,
    ).record(
        nav_date="2026-08-13",
        dry_run=False,
        confirm=True,
        run_id="daily-nav-job-source:lx",
        nav_write_context=context,
    )

    assert result["valuation_ref"].startswith("nav-valuation-evidence:v1:")
    assert store.load(result["valuation_ref"])["artifact"]["source_run_id"] == (
        "daily-nav-job-source:lx"
    )

    class ScopeFailurePortfolio(Portfolio):
        @staticmethod
        def record_nav(*_args, **_kwargs):
            raise _blocked_refusal("CASH_FLOW_DATASET_SCOPE_MISMATCH")

    rejected = AccountNavRecorderService(
        account="lx",
        storage=SimpleNamespace(),
        portfolio=ScopeFailurePortfolio(),
        read_service=Read(),
        holdings_preflight=_Preflight(),
        valuation_evidence_store=store,
    ).record(
        nav_date="2026-08-13",
        dry_run=False,
        confirm=True,
        run_id="daily-nav-job-source:lx",
        nav_write_context=context,
    )
    assert "valuation_ref" not in rejected


def test_replay_uses_evidence_without_price_fetch_and_audits_revisions(tmp_path):
    store = NavValuationEvidenceStore(tmp_path)
    saved = store.save(_prepared(store))
    calls = []

    class Read:
        @staticmethod
        def build_snapshot(**_kwargs):
            raise AssertionError("replay must not fetch prices")

        @staticmethod
        def build_snapshot_from_normalized(**kwargs):
            calls.append(("rehydrate", kwargs))
            normalized = kwargs["normalized_valuation"]
            return {
                "snapshot_time": kwargs["snapshot_time"],
                "normalized_valuation": normalized,
                "valuation": normalized.to_portfolio_valuation(),
                "holdings_snapshot": kwargs["holdings_snapshot"],
            }

    dataset = _Dataset()
    dataset.run_id = "daily-nav-job-replay:lx"
    dataset.effect_store_revision = "cfs_replay"

    class Portfolio:
        @staticmethod
        def build_cash_flow_dataset(**_kwargs):
            return dataset

        @staticmethod
        def record_nav(*_args, **kwargs):
            calls.append(("record", kwargs))
            return SimpleNamespace(
                record_id=None,
                date=date(2026, 8, 13),
                account="lx",
                total_value=100.0,
                cash_value=100.0,
                stock_value=0.0,
                fund_value=0.0,
                shares=100.0,
                nav=1.0,
                cash_flow=0.0,
                share_change=0.0,
                pnl=0.0,
                mtd_nav_change=0.0,
                ytd_nav_change=0.0,
                mtd_pnl=0.0,
                ytd_pnl=0.0,
                details={},
            )

    context = NavWriteContext(
        status="final",
        writer="daily-nav-job",
        write_reason="canonical_daily_nav_replay",
        nav_date=date(2026, 8, 13),
        run_id="daily-nav-job-replay:lx",
    )
    result = AccountNavRecorderService(
        account="lx",
        storage=SimpleNamespace(),
        portfolio=Portfolio(),
        read_service=Read(),
        holdings_preflight=_Preflight(),
        valuation_evidence_store=store,
    ).record(
        nav_date="2026-08-13",
        dry_run=True,
        run_id="daily-nav-job-replay:lx",
        nav_write_context=context,
        valuation_ref=saved["valuation_ref"],
    )

    assert result["success"] is True
    record_context = calls[-1][1]["nav_write_context"]
    assert record_context.write_reason == "canonical_daily_nav_replay"
    assert record_context.provenance["source_effect_store_revision"] == "cfs_source"
    assert record_context.provenance["replay_effect_store_revision"] == "cfs_replay"
    assert calls[0][0] == "rehydrate"


def test_replay_rejects_cash_flow_fingerprint_change(tmp_path):
    store = NavValuationEvidenceStore(tmp_path)
    saved = store.save(_prepared(store))

    class Read:
        @staticmethod
        def build_snapshot_from_normalized(**kwargs):
            normalized = kwargs["normalized_valuation"]
            return {
                "snapshot_time": kwargs["snapshot_time"],
                "normalized_valuation": normalized,
                "valuation": normalized.to_portfolio_valuation(),
            }

    dataset = _Dataset()
    dataset.run_id = "daily-nav-job-replay:lx"
    dataset.financial_fingerprint = "3" * 64

    class Portfolio:
        @staticmethod
        def build_cash_flow_dataset(**_kwargs):
            return dataset

    result = AccountNavRecorderService(
        account="lx",
        storage=SimpleNamespace(),
        portfolio=Portfolio(),
        read_service=Read(),
        holdings_preflight=_Preflight(),
        valuation_evidence_store=store,
    ).record(
        nav_date="2026-08-13",
        dry_run=True,
        run_id="daily-nav-job-replay:lx",
        nav_write_context=NavWriteContext(
            status="final",
            writer="daily-nav-job",
            write_reason="canonical_daily_nav_replay",
            nav_date=date(2026, 8, 13),
            run_id="daily-nav-job-replay:lx",
        ),
        valuation_ref=saved["valuation_ref"],
    )

    assert result["success"] is False
    assert result["error"] == "NAV valuation replay cash-flow fingerprint mismatch"
