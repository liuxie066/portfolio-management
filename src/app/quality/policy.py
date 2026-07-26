from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src import config
from src.app.futu_sync_evidence import FutuSyncEvidenceStore

NAV_REQUIRED_DATASETS = (
    "pm.account_mapping",
    "pm.holdings_quantity",
    "pm.securities_cash",
    "pm.fund_mmf",
    "pm.prices",
    "pm.fx",
)


def nav_gate(
    datasets: Mapping[str, Mapping[str, Any]],
    *,
    finality_eligible: bool,
    finality_reason: str,
) -> dict[str, Any]:
    blocked_by = [
        dataset_id
        for dataset_id in NAV_REQUIRED_DATASETS
        if datasets.get(dataset_id, {}).get("status") != "trusted"
    ]
    if not finality_eligible:
        blocked_by.append(f"nav_finality:{finality_reason}")
    return {
        "status": "trusted" if not blocked_by else "untrusted",
        "blocked_by": blocked_by,
        "reason_code": "NAV_EVIDENCE_COMPLETE" if not blocked_by else "NAV_EVIDENCE_INCOMPLETE",
    }


def assert_official_nav_write_allowed(
    *,
    account: str,
    valuation_quality: Mapping[str, Any],
    receipt_store: FutuSyncEvidenceStore | None = None,
) -> None:
    """Fail closed at the authoritative NAV write boundary after onboarding.

    Cost basis is deliberately excluded: it affects cost/P&L reports but not
    current market-value NAV. The gate consumes the current valuation evidence
    and the latest durable OpenD-to-repository reconciliation receipt; it never
    asks the Quality Hub to make a business write decision.
    """
    if not config.get_bool("quality.onboarded", False):
        return

    mapping_status = "trusted"
    try:
        config.get_futu_account_settings(account)
    except ValueError:
        mapping_status = "unavailable"

    receipt = (receipt_store or FutuSyncEvidenceStore()).latest(account)
    verdicts = ((receipt or {}).get("reconciliation") or {}).get("datasets") or {}
    datasets: dict[str, dict[str, Any]] = {
        "pm.account_mapping": {"status": mapping_status},
        "pm.holdings_quantity": {
            "status": (verdicts.get("pm.holdings_quantity") or {}).get("status", "unavailable")
        },
        "pm.securities_cash": {
            "status": (verdicts.get("pm.securities_cash") or {}).get("status", "unavailable")
        },
        "pm.fund_mmf": {
            "status": (verdicts.get("pm.fund_mmf") or {}).get("status", "unavailable")
        },
        "pm.prices": {
            "status": (valuation_quality.get("prices") or {}).get("status", "unavailable")
        },
        "pm.fx": {
            "status": (valuation_quality.get("fx") or {}).get("status", "unavailable")
        },
    }
    decision = nav_gate(
        datasets,
        finality_eligible=True,
        finality_reason="prospective_finality",
    )
    if decision["status"] != "trusted":
        raise ValueError(
            "NAV 写入拒绝：质量门禁未通过: " + ", ".join(decision["blocked_by"])
        )
