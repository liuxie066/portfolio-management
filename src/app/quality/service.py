from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from src import config
from src.app.futu_sync_evidence import FutuSyncEvidenceStore
from src.app.nav_finality import evaluate_nav_finality

from .artifact import QualityArtifactStore
from .futu_evidence import (
    ReceiptFreshness,
    evaluate_receipt_freshness,
    resolve_account_mappings,
    source_receipt_complete,
)
from .policy import nav_gate

_ROOT = Path(__file__).resolve().parents[3]
_SCHEMA = _ROOT / "contracts" / "quality-monitoring" / "quality_status.v1.schema.json"
_REQUIRED_SYNC_STAGES = frozenset({"positions", "securities_cash", "fund_mmf"})
_REQUIRED_RECONCILIATION_DATASETS = frozenset(
    {
        "pm.holdings_quantity",
        "pm.cost_basis",
        "pm.securities_cash",
        "pm.fund_mmf",
    }
)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _value(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


class PMQualityService:
    def __init__(
        self,
        storage: Any,
        *,
        receipt_store: FutuSyncEvidenceStore | None = None,
        artifact_store: QualityArtifactStore | None = None,
        instance_id: str | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.storage = storage
        self.receipt_store = receipt_store or FutuSyncEvidenceStore()
        self.artifact_store = artifact_store or QualityArtifactStore()
        self.instance_id = instance_id or str(config.get("quality.instance_id", "portfolio-management-local"))
        self.now_fn = now_fn or (lambda: datetime.now(UTC))

    def build(self, *, accounts: list[str]) -> dict[str, Any]:
        now = self.now_fn()
        if now.tzinfo is None:
            raise ValueError("quality clock must be timezone-aware")
        now = now.astimezone(UTC)
        observed_at = _iso(now)
        normalized_accounts = list(dict.fromkeys(str(item).strip().lower() for item in accounts))
        mapping_states = resolve_account_mappings(normalized_accounts)
        datasets = []
        runtime_checks = []
        for account in normalized_accounts:
            receipt = self.receipt_store.latest(account)
            freshness = evaluate_receipt_freshness(receipt, now=now)
            mapping_state = mapping_states[account]
            datasets.extend(
                self._account_datasets(
                    account,
                    observed_at,
                    receipt=receipt,
                    freshness=freshness,
                    mapping_state=mapping_state,
                )
            )
            runtime_checks.extend(
                self._runtime_checks(
                    account,
                    observed_at,
                    receipt=receipt,
                    freshness=freshness,
                    mapping_state=mapping_state,
                )
            )
        runtime_status = self._runtime_status(runtime_checks)
        incidents = [
            self._incident(dataset, observed_at)
            for dataset in datasets
            if dataset["status"] in {"untrusted", "unavailable"}
        ]
        counts = {key: 0 for key in ("trusted", "partial", "untrusted", "unavailable")}
        blocked = set()
        for dataset in datasets:
            counts[dataset["status"]] += 1
            blocked.update(dataset["blocked_consumers"])
        payload = {
            "schema_version": "investment.quality_status.v1",
            "producer": {
                "service": "portfolio-management",
                "producer_version": (_ROOT / "VERSION").read_text().strip(),
                "policy_version": "quality-policy-v1",
                "instance_id": self.instance_id,
                "policy_summary": {
                    "regular_scan_minutes": 15,
                    "write_readback_seconds": 30,
                },
            },
            "observed_at_utc": observed_at,
            "runtime": {
                "status": runtime_status,
                "as_of_utc": observed_at,
                "checks": runtime_checks,
            },
            "datasets": datasets,
            "incidents": incidents,
            "summary": {
                "runtime_status": runtime_status,
                "dataset_counts": counts,
                "blocking_consumers": sorted(blocked),
                "message": (
                    "PM scheduled synchronization and OpenD evidence are current."
                    if runtime_status == "healthy"
                    else "PM runtime or required synchronization evidence is not healthy."
                ),
            },
        }
        schema = json.loads(_SCHEMA.read_text(encoding="utf-8"))
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(payload)
        return payload

    def refresh(self, *, accounts: list[str]) -> dict[str, Any]:
        payload = self.build(accounts=accounts)
        self.artifact_store.publish(payload)
        return payload

    def read_published(self) -> dict[str, Any] | None:
        return self.artifact_store.read()

    def _account_datasets(
        self,
        account: str,
        observed_at: str,
        *,
        receipt: dict[str, Any] | None,
        freshness: ReceiptFreshness,
        mapping_state: dict[str, Any],
    ) -> list[dict[str, Any]]:
        mapping = self._mapping_dataset(
            account,
            observed_at,
            receipt=receipt,
            freshness=freshness,
            mapping_state=mapping_state,
        )
        source, sync = self._source_and_sync_datasets(
            account,
            receipt,
            observed_at,
            freshness=freshness,
            mapping_state=mapping_state,
        )
        replica = self._replica_datasets(
            account,
            receipt,
            observed_at,
            freshness=freshness,
            mapping_state=mapping_state,
        )
        by_id = {item["dataset_id"]: item for item in replica}
        cash_statuses = {
            by_id["pm.securities_cash"]["status"],
            by_id["pm.fund_mmf"]["status"],
        }
        if cash_statuses == {"trusted"}:
            cash_like_status = "trusted"
        elif "trusted" in cash_statuses:
            cash_like_status = "partial"
        elif cash_statuses == {"unavailable"}:
            cash_like_status = "unavailable"
        else:
            cash_like_status = "untrusted"
        cash_like = self._dataset(
            dataset_id="pm.cash_like_assets",
            account=account,
            status=cash_like_status,
            observed_at=observed_at,
            reason_code="CASH_LIKE_COMPLETE" if cash_like_status == "trusted" else "CASH_LIKE_INCOMPLETE",
            evidence_refs=[
                *by_id["pm.securities_cash"]["evidence_refs"],
                *by_id["pm.fund_mmf"]["evidence_refs"],
            ],
            blocked_consumers=[] if cash_like_status == "trusted" else ["official_nav"],
            blocked_by=[
                item
                for item in ("pm.securities_cash", "pm.fund_mmf")
                if by_id[item]["status"] != "trusted"
            ],
            usable_for=["official_nav"] if cash_like_status == "trusted" else [],
            freshness=dict(by_id["pm.securities_cash"]["freshness"]),
        )
        valuation_datasets, nav_dataset = self._valuation_and_nav_datasets(
            account,
            observed_at,
            {item["dataset_id"]: item for item in [mapping, source, sync, *replica, cash_like]},
        )
        nav_history = self._nav_history_dataset(account, observed_at)
        if nav_history["status"] != "trusted" and nav_dataset["status"] == "trusted":
            nav_dataset["status"] = "untrusted"
            nav_dataset["required_evidence_complete"] = False
            nav_dataset["usable_for"] = []
            nav_dataset["blocked_consumers"] = ["official_nav"]
            nav_dataset["blocked_by"].append("pm.nav_history")
            nav_dataset["reason_codes"] = ["NAV_HISTORY_UNTRUSTED"]
            nav_dataset["checks"][0]["status"] = "fail"
            nav_dataset["checks"][0]["severity"] = "blocking"
            nav_dataset["checks"][0]["reason_code"] = "NAV_HISTORY_UNTRUSTED"
        return [
            mapping,
            source,
            sync,
            *replica,
            cash_like,
            *valuation_datasets,
            nav_dataset,
            nav_history,
        ]

    def _source_and_sync_datasets(
        self,
        account: str,
        receipt: dict[str, Any] | None,
        observed_at: str,
        *,
        freshness: ReceiptFreshness,
        mapping_state: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        metadata = (receipt or {}).get("source_metadata") or {}
        evidence = (
            [self._evidence(
                f"pm-sync-{receipt['sync_run_id']}",
                "futu-sync-receipt",
                metadata.get("observed_at_utc") or observed_at,
            )]
            if receipt and receipt.get("sync_run_id")
            else []
        )
        source_complete = bool(
            freshness.current
            and mapping_state["valid"]
            and source_receipt_complete(
                receipt,
                settings=mapping_state["settings"],
            )
        )
        source_status = "trusted" if source_complete else "unavailable"
        source_reason = (
            "FUTU_SNAPSHOT_COMPLETE"
            if source_complete
            else freshness.reason_code
            if not freshness.current
            else "FUTU_SNAPSHOT_INCOMPLETE"
        )
        source = self._dataset(
            dataset_id="pm.futu_snapshot",
            account=account,
            status=source_status,
            observed_at=metadata.get("observed_at_utc") or observed_at,
            reason_code=source_reason,
            evidence_refs=evidence,
            blocked_consumers=[] if source_complete else ["futu_sync", "official_nav"],
            usable_for=["futu_sync"] if source_complete else [],
            check_ids=["PM-SRC-001", "PM-SRC-002"],
            freshness=freshness.as_payload(fallback_observed_at_utc=observed_at),
        )
        if source_complete:
            source["source_snapshots"] = [{
                "provider": "futu-openapi",
                "snapshot_id": str(receipt["source_snapshot_id"]),
                "observed_at_utc": metadata["observed_at_utc"],
                "complete": True,
                "refresh_cache": bool(metadata.get("refresh_cache")),
                "account_fingerprint": str(metadata["account_fingerprint"]),
                "environment": str(metadata["trd_env"]),
                "market": str(metadata["trd_market"]),
                "payload_sha256": str(metadata["payload_sha256"]),
            }]
            source["extensions"] = {
                "pm": {
                    "profile_fingerprint": str(
                        metadata["profile_fingerprint"]
                    ),
                    "cash_source_fields": dict(
                        (metadata.get("cash") or {}).get("source_fields") or {}
                    ),
                }
            }

        stages = (receipt or {}).get("stages") or {}
        stage_complete = _REQUIRED_SYNC_STAGES.issubset(stages) and all(
            isinstance(stages.get(stage_id), dict)
            and stages[stage_id].get("status") == "succeeded"
            for stage_id in _REQUIRED_SYNC_STAGES
        )
        reconciliation = (receipt or {}).get("reconciliation") or {}
        reconciliation_datasets = reconciliation.get("datasets") or {}
        reconciliation_complete = bool(
            reconciliation.get("status") == "trusted"
            and _REQUIRED_RECONCILIATION_DATASETS.issubset(reconciliation_datasets)
            and all(
                (reconciliation_datasets.get(dataset_id) or {}).get("status")
                == "trusted"
                for dataset_id in _REQUIRED_RECONCILIATION_DATASETS
            )
        )
        sync_complete = bool(
            source_complete
            and stage_complete
            and not receipt.get("partial_write_possible")
            and reconciliation_complete
        )
        sync_status = (
            "trusted"
            if sync_complete
            else "unavailable"
            if not freshness.current
            else "untrusted"
            if receipt
            else "unavailable"
        )
        sync_reason = (
            "FUTU_SYNC_VERIFIED"
            if sync_complete
            else freshness.reason_code
            if not freshness.current
            else "FUTU_SYNC_INCOMPLETE"
        )
        sync = self._dataset(
            dataset_id="pm.futu_sync",
            account=account,
            status=sync_status,
            observed_at=metadata.get("observed_at_utc") or observed_at,
            reason_code=sync_reason,
            evidence_refs=evidence,
            blocked_consumers=[] if sync_complete else ["official_nav", "portfolio_report"],
            usable_for=["official_nav", "portfolio_report"] if sync_complete else [],
            check_ids=["PM-SYNC-001", "PM-SYNC-002", "PM-SYNC-003"],
            freshness=freshness.as_payload(fallback_observed_at_utc=observed_at),
        )
        return source, sync

    def _mapping_dataset(
        self,
        account: str,
        observed_at: str,
        *,
        receipt: dict[str, Any] | None,
        freshness: ReceiptFreshness,
        mapping_state: dict[str, Any],
    ) -> dict[str, Any]:
        settings = mapping_state["settings"]
        verified = bool(
            mapping_state["valid"]
            and freshness.current
            and source_receipt_complete(receipt, settings=settings)
        )
        status = "trusted" if verified else "unavailable"
        reason = (
            "ACCOUNT_MAPPING_VALID"
            if verified
            else str(mapping_state["reason_code"])
            if not mapping_state["valid"]
            else freshness.reason_code
            if not freshness.current
            else "ACCOUNT_MAPPING_UNVERIFIED"
        )
        evidence = (
            [
                self._evidence(
                    f"pm-account-mapping-{account}",
                    "account-mapping",
                    observed_at,
                ),
                self._evidence(
                    f"pm-sync-{receipt['sync_run_id']}",
                    "futu-sync-receipt",
                    (receipt.get("source_metadata") or {}).get("observed_at_utc")
                    or observed_at,
                ),
            ]
            if verified and receipt and receipt.get("sync_run_id")
            else []
        )
        dataset = self._dataset(
            dataset_id="pm.account_mapping",
            account=account,
            status=status,
            observed_at=observed_at,
            reason_code=reason,
            evidence_refs=evidence,
            blocked_consumers=[] if status == "trusted" else ["futu_sync", "official_nav"],
            usable_for=["futu_sync"] if status == "trusted" else [],
            freshness=freshness.as_payload(fallback_observed_at_utc=observed_at),
        )
        if settings:
            dataset["extensions"] = {
                "account_fingerprint": settings["account_fingerprint"],
                "profile_fingerprint": settings["profile_fingerprint"],
                "trd_env": settings["trd_env"],
                "trd_market": settings["trd_market"],
            }
        return dataset

    def _replica_datasets(
        self,
        account: str,
        receipt: dict[str, Any] | None,
        observed_at: str,
        *,
        freshness: ReceiptFreshness,
        mapping_state: dict[str, Any],
    ) -> list[dict[str, Any]]:
        ids = (
            "pm.holdings_quantity",
            "pm.cost_basis",
            "pm.securities_cash",
            "pm.fund_mmf",
        )
        source_complete = bool(
            freshness.current
            and mapping_state["valid"]
            and source_receipt_complete(
                receipt,
                settings=mapping_state["settings"],
            )
        )
        if not receipt or not source_complete:
            reason_code = (
                "SYNC_RECEIPT_MISSING"
                if not receipt
                else "FUTU_SNAPSHOT_INCOMPLETE"
                if freshness.current
                else freshness.reason_code
            )
            return [
                self._dataset(
                    dataset_id=dataset_id,
                    account=account,
                    status="unavailable",
                    observed_at=observed_at,
                    reason_code=reason_code,
                    evidence_refs=(
                        []
                        if not receipt
                        else [
                            self._evidence(
                                f"pm-sync-{receipt['sync_run_id']}",
                                "futu-sync-receipt",
                                (receipt.get("source_metadata") or {}).get("observed_at_utc")
                                or observed_at,
                            )
                        ]
                    ),
                    blocked_consumers=self._consumers(dataset_id),
                    freshness=freshness.as_payload(fallback_observed_at_utc=observed_at),
                )
                for dataset_id in ids
            ]
        reconciliation = receipt.get("reconciliation") or {}
        verdicts = reconciliation.get("datasets") or {}
        evidence = [
            self._evidence(
                f"pm-sync-{receipt['sync_run_id']}",
                "futu-sync-receipt",
                receipt["source_metadata"].get("observed_at_utc") or observed_at,
            )
        ]
        result = []
        for dataset_id in ids:
            verdict = verdicts.get(dataset_id) or {}
            status = verdict.get("status")
            if status not in {"trusted", "untrusted", "unavailable"}:
                status = "unavailable"
            result.append(self._dataset(
                dataset_id=dataset_id,
                account=account,
                status=status,
                observed_at=receipt["source_metadata"].get("observed_at_utc") or observed_at,
                reason_code=verdict.get("reason_code") or "RECONCILIATION_EVIDENCE_MISSING",
                evidence_refs=evidence,
                blocked_consumers=[] if status == "trusted" else self._consumers(dataset_id),
                usable_for=self._consumers(dataset_id) if status == "trusted" else [],
                check_ids={
                    "pm.holdings_quantity": ["PM-POS-001", "PM-POS-002"],
                    "pm.securities_cash": ["PM-CASH-001", "PM-CASH-002"],
                }.get(dataset_id),
                freshness=freshness.as_payload(fallback_observed_at_utc=observed_at),
            ))
        return result

    def _valuation_and_nav_datasets(
        self,
        account: str,
        observed_at: str,
        existing: dict[str, dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        try:
            navs = self.storage.get_nav_history(account, days=1)
        except Exception:
            navs = []
        latest = navs[-1] if navs else None
        details = dict(_value(latest, "details", {}) or {})
        valuation_quality = details.get("valuation_quality") or {}
        evidence = (
            [self._evidence(f"pm-nav-{account}-{_value(latest, 'date')}", "nav-details", observed_at)]
            if latest
            else []
        )
        price_status = (valuation_quality.get("prices") or {}).get("status", "unavailable")
        if price_status == "partial":
            price_status = "partial"
        elif price_status != "trusted":
            price_status = "unavailable"
        fx_status = (valuation_quality.get("fx") or {}).get("status", "unavailable")
        if fx_status != "trusted":
            fx_status = "unavailable"
        prices = self._dataset(
            dataset_id="pm.prices",
            account=account,
            status=price_status,
            observed_at=valuation_quality.get("observed_at_utc") or observed_at,
            reason_code="PRICE_EVIDENCE_COMPLETE" if price_status == "trusted" else "PRICE_EVIDENCE_INCOMPLETE",
            evidence_refs=evidence,
            blocked_consumers=[] if price_status == "trusted" else ["official_nav"],
            usable_for=["official_nav"] if price_status == "trusted" else [],
        )
        fx = self._dataset(
            dataset_id="pm.fx",
            account=account,
            status=fx_status,
            observed_at=valuation_quality.get("observed_at_utc") or observed_at,
            reason_code="FX_EVIDENCE_COMPLETE" if fx_status == "trusted" else "FX_EVIDENCE_INCOMPLETE",
            evidence_refs=evidence,
            blocked_consumers=[] if fx_status == "trusted" else ["official_nav"],
            usable_for=["official_nav"] if fx_status == "trusted" else [],
        )
        target_date = _value(latest, "date", observed_at[:10])
        finality = evaluate_nav_finality(details, target_date=target_date)
        gate = nav_gate(
            {**existing, "pm.prices": prices, "pm.fx": fx},
            finality_eligible=finality.eligible,
            finality_reason=finality.reason,
        )
        nav = self._dataset(
            dataset_id="pm.nav",
            account=account,
            status=gate["status"],
            observed_at=observed_at,
            reason_code=gate["reason_code"],
            evidence_refs=evidence,
            blocked_consumers=[] if gate["status"] == "trusted" else ["official_nav"],
            blocked_by=gate["blocked_by"],
            usable_for=["official_nav"] if gate["status"] == "trusted" else [],
        )
        return [prices, fx], nav

    def _nav_history_dataset(self, account: str, observed_at: str) -> dict[str, Any]:
        audit = getattr(self.storage, "audit_nav_history_duplicates", None)
        try:
            result = audit(account=account) if callable(audit) else None
        except Exception:
            result = None
        trusted = bool(
            isinstance(result, dict)
            and result.get("success") is True
            and int(result.get("duplicate_group_count") or 0) == 0
        )
        evidence = (
            [self._evidence(f"pm-nav-audit-{account}", "nav-duplicate-audit", observed_at)]
            if result is not None
            else []
        )
        return self._dataset(
            dataset_id="pm.nav_history",
            account=account,
            status="trusted" if trusted else ("untrusted" if result else "unavailable"),
            observed_at=observed_at,
            reason_code="NAV_HISTORY_UNIQUE" if trusted else "NAV_HISTORY_DUPLICATE_OR_UNAVAILABLE",
            evidence_refs=evidence,
            blocked_consumers=[] if trusted else ["performance_report"],
            usable_for=["performance_report"] if trusted else [],
        )

    def _runtime_checks(
        self,
        account: str,
        observed_at: str,
        *,
        receipt: dict[str, Any] | None,
        freshness: ReceiptFreshness,
        mapping_state: dict[str, Any],
    ) -> list[dict[str, Any]]:
        evidence = (
            [
                self._evidence(
                    f"pm-sync-{receipt['sync_run_id']}",
                    "futu-sync-receipt",
                    (receipt.get("source_metadata") or {}).get("observed_at_utc")
                    or observed_at,
                )
            ]
            if receipt and receipt.get("sync_run_id")
            else []
        )
        stages = (receipt or {}).get("stages") or {}
        stages_succeeded = _REQUIRED_SYNC_STAGES.issubset(stages) and all(
            isinstance(stages.get(stage_id), dict)
            and stages[stage_id].get("status") == "succeeded"
            for stage_id in _REQUIRED_SYNC_STAGES
        )
        sync_succeeded = bool(
            freshness.current
            and receipt
            and receipt.get("success") is True
            and stages_succeeded
            and receipt.get("partial_write_possible") is False
        )
        if sync_succeeded:
            timer_status = "pass"
            timer_reason = "PM_SYNC_WINDOW_SUCCEEDED"
        elif not freshness.current:
            timer_status = "fail"
            timer_reason = freshness.reason_code
        else:
            timer_status = "fail"
            timer_reason = "PM_SYNC_WINDOW_FAILED"

        source_complete = bool(
            freshness.current
            and mapping_state["valid"]
            and source_receipt_complete(
                receipt,
                settings=mapping_state["settings"],
            )
        )
        if source_complete:
            source_status = "pass"
            source_reason = "PM_OPEND_EVIDENCE_COMPLETE"
        elif not freshness.current:
            source_status = "fail"
            source_reason = freshness.reason_code
        else:
            source_status = "fail"
            source_reason = "PM_OPEND_EVIDENCE_INCOMPLETE"

        expected_window = {
            "required_trigger_at_utc": _iso(freshness.required_trigger_at_utc),
            "expected_by_utc": _iso(freshness.expected_by_utc),
            "grace_seconds": freshness.grace_seconds,
        }
        return [
            self._runtime_check(
                check_id="RT-PM-002",
                account=account,
                observed_at=observed_at,
                status=timer_status,
                reason_code=timer_reason,
                evidence_refs=evidence,
                observed={
                    "receipt_present": receipt is not None,
                    "receipt_freshness": freshness.status,
                    "stages_succeeded": stages_succeeded,
                    "sync_succeeded": bool((receipt or {}).get("success")),
                },
                expected=expected_window,
            ),
            self._runtime_check(
                check_id="RT-PM-003",
                account=account,
                observed_at=observed_at,
                status=source_status,
                reason_code=source_reason,
                evidence_refs=evidence,
                observed={
                    "receipt_freshness": freshness.status,
                    "account_mapping_valid": bool(mapping_state["valid"]),
                    "source_evidence_complete": source_complete,
                },
                expected={
                    "provider": "futu-openapi",
                    "environment": "REAL",
                    "cash_source_fields": {
                        "CNY": "cn_cash",
                        "USD": "us_cash",
                        "HKD": "hk_cash",
                    },
                    "refresh_cache": True,
                    "account_verified": True,
                    "pagination_complete": True,
                },
                source="opend",
            ),
        ]

    @staticmethod
    def _runtime_check(
        *,
        check_id: str,
        account: str,
        observed_at: str,
        status: str,
        reason_code: str,
        evidence_refs: list[dict[str, Any]],
        observed: dict[str, Any],
        expected: dict[str, Any],
        source: str | None = None,
    ) -> dict[str, Any]:
        scope: dict[str, str] = {"account": account}
        if source:
            scope["source"] = source
        return {
            "check_id": check_id,
            "status": status,
            "severity": "info" if status == "pass" else "blocking",
            "scope": scope,
            "observed_at_utc": observed_at,
            "reason_code": reason_code,
            "message": f"{check_id} for {account}: {reason_code}.",
            "observed": observed,
            "expected": expected,
            "evidence_refs": evidence_refs,
        }

    @staticmethod
    def _runtime_status(checks: list[dict[str, Any]]) -> str:
        statuses = {str(item.get("status")) for item in checks}
        if "fail" in statuses:
            return "unhealthy"
        if "unknown" in statuses:
            return "unknown"
        if "warn" in statuses:
            return "degraded"
        return "healthy"

    @staticmethod
    def _consumers(dataset_id: str) -> list[str]:
        if dataset_id == "pm.cost_basis":
            return ["cost_report", "pnl_report"]
        return ["official_nav", "portfolio_report"]

    def _dataset(
        self,
        *,
        dataset_id: str,
        account: str,
        status: str,
        observed_at: str,
        reason_code: str,
        evidence_refs: list[dict[str, Any]],
        blocked_consumers: list[str],
        usable_for: list[str] | None = None,
        blocked_by: list[str] | None = None,
        check_ids: list[str] | None = None,
        freshness: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        check_id = {
            "pm.account_mapping": "PM-ACC-001",
            "pm.futu_snapshot": "PM-SRC-001",
            "pm.holdings_quantity": "PM-POS-001",
            "pm.cost_basis": "PM-COST-001",
            "pm.securities_cash": "PM-CASH-001",
            "pm.fund_mmf": "PM-MMF-001",
            "pm.cash_like_assets": "PM-CASHLIKE-001",
            "pm.futu_sync": "PM-SYNC-001",
            "pm.prices": "PM-PRICE-001",
            "pm.fx": "PM-FX-001",
            "pm.nav": "PM-NAV-001",
            "pm.nav_history": "PM-NAV-002",
        }[dataset_id]
        resolved_check_ids = check_ids or [check_id]
        check_status = "pass" if status == "trusted" else ("warn" if status == "partial" else "fail")
        return {
            "dataset_id": dataset_id,
            "scope": {"account": account},
            "status": status,
            "as_of_utc": observed_at,
            "required_evidence_complete": status == "trusted" and bool(evidence_refs),
            "freshness": freshness or {
                "status": "fresh" if evidence_refs else "unknown",
                "observed_at_utc": observed_at,
            },
            "checks": [{
                "check_id": resolved_check_id,
                "status": check_status,
                "severity": "info" if status == "trusted" else "blocking",
                "scope": {"account": account},
                "observed_at_utc": observed_at,
                "reason_code": reason_code,
                "message": f"{dataset_id} status is {status}.",
                "evidence_refs": evidence_refs,
            } for resolved_check_id in resolved_check_ids],
            "evidence_refs": evidence_refs,
            "usable_for": usable_for or [],
            "blocked_consumers": blocked_consumers,
            "blocked_by": blocked_by or ([] if status == "trusted" else resolved_check_ids),
            "reason_codes": [] if status == "trusted" else [reason_code],
        }

    @staticmethod
    def _evidence(evidence_id: str, kind: str, observed_at: str) -> dict[str, Any]:
        return {
            "evidence_id": evidence_id,
            "kind": kind,
            "observed_at_utc": observed_at,
            "artifact_ref": f"pm-evidence:{kind}:latest",
            "redacted": True,
        }

    @staticmethod
    def _incident(dataset: dict[str, Any], observed_at: str) -> dict[str, Any]:
        identity = json.dumps(
            [dataset["dataset_id"], dataset["scope"], dataset["reason_codes"]],
            sort_keys=True,
            separators=(",", ":"),
        )
        fingerprint = hashlib.sha256(identity.encode()).hexdigest()
        return {
            "incident_id": f"pm-{fingerprint[:24]}",
            "fingerprint": fingerprint,
            "subject_id": dataset["dataset_id"],
            "scope": dataset["scope"],
            "state": "new",
            "severity": "blocking",
            "reason_code": dataset["reason_codes"][0],
            "first_seen_at_utc": observed_at,
            "last_seen_at_utc": observed_at,
            "occurrence_count": 1,
            "preexisting": False,
            "blocked_consumers": dataset["blocked_consumers"],
            "evidence_refs": dataset["evidence_refs"],
        }
