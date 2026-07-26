from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import yaml
import pytest

from src import config
from src.app.futu_sync_evidence import FutuSyncEvidenceStore
from src.app.quality.artifact import QualityArtifactStore
from src.app.quality.evidence import valuation_quality_evidence
from src.app.quality.policy import assert_official_nav_write_allowed, nav_gate
from src.app.quality.service import PMQualityService


@pytest.fixture(autouse=True)
def _restore_config_cache():
    yield
    config.reload_config()


def _configure(monkeypatch, tmp_path: Path, *, onboarded: bool = False) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "data": {"dir": str(tmp_path / "data")},
                "quality": {"onboarded": onboarded},
                "futu": {
                    "accounts": {
                        "lx": {
                            "acc_id": 123456,
                            "trd_env": "REAL",
                            "trd_market": "US",
                            "cash_currency": "CNH",
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(config.CONFIG_FILE_ENV, str(path))
    config.reload_config()


def _receipt(*, cost_status: str = "trusted", cash_status: str = "trusted", mmf_status: str = "trusted"):
    return {
        "schema_version": "pm.futu_sync_receipt.v1",
        "sync_run_id": "sync-001",
        "source_snapshot_id": "snapshot-redacted-001",
        "source_metadata": {
            "provider": "futu-openapi",
            "observed_at_utc": "2026-07-26T01:00:00Z",
            "account_fingerprint": "sha256:redacted",
            "trd_env": "REAL",
            "trd_market": "US",
            "source_currency": "CNH",
            "normalized_currency": "CNY",
            "cash": {"present": True, "source_field": "cash"},
            "fund_mmf": {"present": True, "source_field": "fund_assets"},
            "refresh_cache": True,
        },
        "stages": {
            "positions": {"status": "succeeded"},
            "securities_cash": {"status": "succeeded"},
            "fund_mmf": {"status": "succeeded"},
        },
        "success": True,
        "partial_write_possible": False,
        "reconciliation": {
            "status": "trusted" if {cost_status, cash_status, mmf_status} == {"trusted"} else "untrusted",
            "datasets": {
                "pm.holdings_quantity": {"status": "trusted", "reason_code": "MATCH"},
                "pm.cost_basis": {"status": cost_status, "reason_code": "MATCH" if cost_status == "trusted" else "MISMATCH"},
                "pm.securities_cash": {"status": cash_status, "reason_code": "MATCH" if cash_status == "trusted" else "MISMATCH"},
                "pm.fund_mmf": {"status": mmf_status, "reason_code": "MATCH" if mmf_status == "trusted" else "MISSING"},
            }
        },
    }


def _final_nav(*, include_finality: bool = True, price_status: str = "trusted", fx_status: str = "trusted"):
    details = {
        "valuation_quality": {
            "observed_at_utc": "2026-07-26T01:05:00Z",
            "prices": {"status": price_status},
            "fx": {"status": fx_status},
        }
    }
    if include_finality:
        details["finality"] = {
            "version": 1,
            "status": "final",
            "nav_date": "2026-07-26",
            "valuation_as_of": "2026-07-26T01:05:00Z",
            "writer": "daily-nav-job",
            "write_reason": "canonical_daily_nav_job",
            "run_id": "nav-run-001",
        }
    return SimpleNamespace(date=date(2026, 7, 26), details=details)


def _publish_receipt(store: FutuSyncEvidenceStore, receipt: dict) -> None:
    store.save("lx", receipt["sync_run_id"], receipt)


def test_valuation_evidence_separates_missing_stale_price_and_fx_fact_time():
    valuation = SimpleNamespace(
        holdings=[
            SimpleNamespace(asset_id="NVDA", quantity=1, asset_type="STOCK", currency="USD"),
            SimpleNamespace(asset_id="CNY-CASH", quantity=100, asset_type="CASH", currency="CNY"),
        ],
        price_evidence={
            "NVDA": {
                "price": 100,
                "cny_price": 720,
                "currency": "USD",
                "exchange_rate": 7.2,
                "source": "cache_fallback",
            }
        },
    )

    evidence = valuation_quality_evidence(valuation)

    assert evidence["prices"]["status"] == "partial"
    assert evidence["prices"]["stale_codes"] == ["NVDA"]
    assert evidence["prices"]["missing_codes"] == []
    assert evidence["fx"]["status"] == "unavailable"
    assert evidence["fx"]["missing_fact_time_codes"] == ["NVDA"]


def test_cost_basis_does_not_block_nav_but_cash_or_mmf_does():
    required = {
        "pm.account_mapping": {"status": "trusted"},
        "pm.holdings_quantity": {"status": "trusted"},
        "pm.securities_cash": {"status": "trusted"},
        "pm.fund_mmf": {"status": "trusted"},
        "pm.prices": {"status": "trusted"},
        "pm.fx": {"status": "trusted"},
        "pm.cost_basis": {"status": "untrusted"},
    }
    assert nav_gate(required, finality_eligible=True, finality_reason="eligible")["status"] == "trusted"

    required["pm.fund_mmf"]["status"] = "unavailable"
    decision = nav_gate(required, finality_eligible=True, finality_reason="eligible")
    assert decision["status"] == "untrusted"
    assert decision["blocked_by"] == ["pm.fund_mmf"]


def test_pm_quality_payload_validates_and_is_dataset_scoped(monkeypatch, tmp_path: Path):
    _configure(monkeypatch, tmp_path)
    receipt_store = FutuSyncEvidenceStore(tmp_path / "receipts")
    _publish_receipt(receipt_store, _receipt(cost_status="untrusted"))
    storage = SimpleNamespace(
        get_nav_history=lambda _account, days: [_final_nav()],
        audit_nav_history_duplicates=lambda account: {
            "success": True,
            "duplicate_group_count": 0,
        },
    )
    service = PMQualityService(
        storage,
        receipt_store=receipt_store,
        artifact_store=QualityArtifactStore(tmp_path / "status.json"),
        instance_id="pm-test",
    )

    payload = service.refresh(accounts=["lx"])
    datasets = {item["dataset_id"]: item for item in payload["datasets"]}

    assert datasets["pm.cost_basis"]["status"] == "untrusted"
    assert datasets["pm.nav"]["status"] == "trusted"
    assert datasets["pm.nav"]["blocked_by"] == []
    check_ids = {
        check["check_id"]
        for dataset in payload["datasets"]
        for check in dataset["checks"]
    }
    assert check_ids >= {
        "PM-ACC-001",
        "PM-SRC-001",
        "PM-SRC-002",
        "PM-POS-001",
        "PM-POS-002",
        "PM-COST-001",
        "PM-CASH-001",
        "PM-CASH-002",
        "PM-MMF-001",
        "PM-CASHLIKE-001",
        "PM-SYNC-001",
        "PM-SYNC-002",
        "PM-SYNC-003",
        "PM-PRICE-001",
        "PM-FX-001",
        "PM-NAV-001",
        "PM-NAV-002",
    }
    assert service.read_published() == payload
    assert QualityArtifactStore(tmp_path / "status.json").read() == payload


def test_pm_quality_finality_and_partial_write_fail_closed(monkeypatch, tmp_path: Path):
    _configure(monkeypatch, tmp_path)
    receipt_store = FutuSyncEvidenceStore(tmp_path / "receipts")
    _publish_receipt(receipt_store, _receipt(cash_status="trusted", mmf_status="unavailable"))
    storage = SimpleNamespace(
        get_nav_history=lambda _account, days: [_final_nav(include_finality=False)],
        audit_nav_history_duplicates=lambda account: {
            "success": True,
            "duplicate_group_count": 0,
        },
    )

    payload = PMQualityService(
        storage,
        receipt_store=receipt_store,
        artifact_store=QualityArtifactStore(tmp_path / "status.json"),
    ).build(accounts=["lx"])
    datasets = {item["dataset_id"]: item for item in payload["datasets"]}

    assert datasets["pm.securities_cash"]["status"] == "trusted"
    assert datasets["pm.fund_mmf"]["status"] == "unavailable"
    assert datasets["pm.cash_like_assets"]["status"] == "partial"
    assert datasets["pm.nav"]["status"] == "untrusted"
    assert "pm.fund_mmf" in datasets["pm.nav"]["blocked_by"]
    assert "nav_finality:missing_finality" in datasets["pm.nav"]["blocked_by"]


def test_onboarded_nav_write_gate_uses_local_receipt_not_hub(monkeypatch, tmp_path: Path):
    _configure(monkeypatch, tmp_path, onboarded=True)
    store = FutuSyncEvidenceStore(tmp_path / "receipts")
    _publish_receipt(store, _receipt(cost_status="untrusted"))
    quality = {
        "prices": {"status": "trusted"},
        "fx": {"status": "trusted"},
    }

    assert_official_nav_write_allowed(
        account="lx",
        valuation_quality=quality,
        receipt_store=store,
    )

    _publish_receipt(store, _receipt(cash_status="untrusted"))
    try:
        assert_official_nav_write_allowed(
            account="lx",
            valuation_quality=quality,
            receipt_store=store,
        )
    except ValueError as exc:
        assert "pm.securities_cash" in str(exc)
    else:
        raise AssertionError("expected the onboarded NAV gate to fail closed")
