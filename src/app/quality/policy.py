from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from src import config
from src.app.futu_sync_evidence import FutuSyncEvidenceStore

from .futu_evidence import (
    evaluate_receipt_freshness,
    resolve_account_mappings,
    source_receipt_complete,
)

NAV_REQUIRED_DATASETS = (
    "pm.account_mapping",
    "pm.holdings_quantity",
    "pm.cash_aggregate",
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
    now: datetime | None = None,
) -> None:
    """Fail closed at the authoritative NAV write boundary after onboarding.

    Cost basis is deliberately excluded: it affects cost/P&L reports but not
    current market-value NAV. The gate consumes the current valuation evidence
    and the latest durable OpenD-to-repository reconciliation receipt; it never
    asks the Quality Hub to make a business write decision.
    """
    if not config.get_bool("quality.onboarded", False):
        return

    mapping_states = resolve_account_mappings([*config.get_quality_accounts(), account])
    mapping_state = mapping_states[account]
    receipt = (receipt_store or FutuSyncEvidenceStore()).latest(account)
    freshness = evaluate_receipt_freshness(
        receipt,
        now=(now or datetime.now(UTC)),
    )
    receipt_usable = bool(
        freshness.current
        and source_receipt_complete(
            receipt,
            settings=mapping_state["settings"],
        )
    )
    mapping_status = "trusted" if receipt_usable else "unavailable"
    verdicts = (
        ((receipt or {}).get("reconciliation") or {}).get("datasets") or {}
        if receipt_usable
        else {}
    )
    datasets: dict[str, dict[str, Any]] = {
        "pm.account_mapping": {"status": mapping_status},
        "pm.holdings_quantity": {
            "status": (verdicts.get("pm.holdings_quantity") or {}).get("status", "unavailable")
        },
        "pm.cash_aggregate": {
            "status": (verdicts.get("pm.cash_aggregate") or {}).get("status", "unavailable")
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
