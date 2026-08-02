"""Holdings snapshot exact-set planning, persistence, and verification."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Optional

from src import config
from src.domain.snapshot_contracts import (
    SNAPSHOT_DIGEST_VERSION,
    BoundSnapshotWriteAuthority,
    NormalizedValuationSnapshot,
    SnapshotExactSetPlan,
    SnapshotSetConflictError,
    SnapshotWriteAuthority,
    digest_payload,
    snapshot_business_key,
    snapshot_digest,
    snapshot_row_payload,
)
from src.snapshot_models import HoldingSnapshot


_RECOVERY_NAV_OWNED_FIELDS = frozenset({
    "snapshot_details_patch_error",
    "snapshot_digest",
    "snapshot_error",
    "snapshot_evidence",
    "snapshot_persisted",
    "snapshot_plan_digest",
    "snapshot_preview",
    "snapshot_retry_command",
    "snapshot_status",
    "snapshot_task_id",
})


class SnapshotService:
    """Own the recoverable exact-set boundary for one account/date slice."""

    def __init__(self, storage: Any, data_dir: Optional[Path] = None):
        self.storage = storage
        self.data_dir = data_dir or config.get_data_dir()

    def build_holdings_snapshots(
        self,
        *,
        account: str,
        as_of: str,
        normalized_valuation: NormalizedValuationSnapshot,
    ) -> list[HoldingSnapshot]:
        if not isinstance(normalized_valuation, NormalizedValuationSnapshot):
            raise TypeError(
                "snapshot persistence requires NormalizedValuationSnapshot"
            )
        if normalized_valuation.account != account:
            raise ValueError(
                "normalized valuation account mismatch for holdings snapshot"
            )
        return list(normalized_valuation.to_snapshot_rows(as_of=as_of))

    def plan_exact_set(
        self,
        *,
        account: str,
        as_of: str,
        normalized_valuation: NormalizedValuationSnapshot,
    ) -> SnapshotExactSetPlan:
        desired = self.build_holdings_snapshots(
            account=account,
            as_of=as_of,
            normalized_valuation=normalized_valuation,
        )
        current = self.storage.list_holding_snapshots_fresh(
            account=account,
            as_of=as_of,
        )
        return SnapshotExactSetPlan.build(
            account=account,
            as_of=as_of,
            target_digest=normalized_valuation.target_digest(as_of=as_of),
            before=current,
            desired=desired,
        )

    @staticmethod
    def bind_authority(
        *,
        authority: SnapshotWriteAuthority,
        plan: SnapshotExactSetPlan,
        dry_run: bool,
    ) -> BoundSnapshotWriteAuthority:
        if not isinstance(authority, SnapshotWriteAuthority):
            raise TypeError("snapshot persistence requires SnapshotWriteAuthority")
        return authority.bind(plan, require_confirm=not dry_run)

    @staticmethod
    def evidence(
        *,
        normalized_valuation: NormalizedValuationSnapshot,
        plan: SnapshotExactSetPlan,
        authority: BoundSnapshotWriteAuthority,
        status: str,
        task_id: Optional[str] = None,
    ) -> dict[str, Any]:
        evidence = normalized_valuation.evidence(
            as_of=plan.as_of,
            status=status,
        )
        evidence.update({
            "plan_digest": plan.plan_digest,
            "row_digest": plan.row_digest,
            "row_count": len(plan.desired),
            "authority_digest": authority.authority_digest,
            "bound_authority_digest": authority.digest,
            "run_id": authority.run_id,
            "issuer": authority.issuer,
            "overwrite_existing": authority.overwrite_existing,
        })
        if task_id:
            evidence["task_id"] = task_id
        return evidence

    @staticmethod
    def _recovery_base_details(details: Mapping[str, Any]) -> dict[str, Any]:
        return {
            str(name): value
            for name, value in details.items()
            if name not in _RECOVERY_NAV_OWNED_FIELDS
        }

    @staticmethod
    def _assert_recovery_nav_details(
        details: Mapping[str, Any],
        *,
        plan: SnapshotExactSetPlan,
        authority: BoundSnapshotWriteAuthority,
        expected_status: str,
        expected_persisted: bool,
    ) -> None:
        evidence = details.get("snapshot_evidence")
        if not isinstance(evidence, Mapping):
            raise SnapshotSetConflictError(
                "snapshot recovery NAV details require snapshot_evidence"
            )
        finality = details.get("finality")
        if finality is not None and not isinstance(finality, Mapping):
            raise SnapshotSetConflictError(
                "snapshot recovery NAV finality must be an object"
            )
        finality = finality or {}
        expected = {
            "run_id": authority.run_id,
            "issuer": authority.issuer,
            "target_digest": plan.target_digest,
            "plan_digest": plan.plan_digest,
            "row_digest": plan.row_digest,
            "authority_digest": authority.authority_digest,
            "bound_authority_digest": authority.digest,
            "overwrite_existing": authority.overwrite_existing,
            "status": expected_status,
        }
        mismatches = [
            name for name, value in expected.items() if evidence.get(name) != value
        ]
        details_run_id = str(details.get("run_id") or "")
        if details_run_id != authority.run_id:
            mismatches.append("details.run_id")
        if finality:
            if str(finality.get("run_id") or "") != authority.run_id:
                mismatches.append("finality.run_id")
            if str(finality.get("writer") or "") != authority.issuer:
                mismatches.append("finality.writer")
        if details.get("snapshot_status") != expected_status:
            mismatches.append("snapshot_status")
        if details.get("snapshot_persisted") is not expected_persisted:
            mismatches.append("snapshot_persisted")
        if details.get("snapshot_plan_digest") != plan.plan_digest:
            mismatches.append("snapshot_plan_digest")
        task_id = str(details.get("snapshot_task_id") or "")
        if not task_id or str(evidence.get("task_id") or "") != task_id:
            mismatches.append("snapshot_task_id")
        if expected_persisted:
            if details.get("snapshot_digest") != plan.row_digest:
                mismatches.append("snapshot_digest")
        if mismatches:
            raise SnapshotSetConflictError(
                "snapshot recovery NAV details mismatch: "
                + ", ".join(sorted(set(mismatches)))
            )

    @classmethod
    def recovery_target(
        cls,
        *,
        plan: SnapshotExactSetPlan,
        authority: BoundSnapshotWriteAuthority,
        planned_nav_details: dict[str, Any],
        complete_nav_details: dict[str, Any],
    ) -> dict[str, Any]:
        authority.assert_matches(plan)
        planned = dict(planned_nav_details)
        complete = dict(complete_nav_details)
        cls._assert_recovery_nav_details(
            planned,
            plan=plan,
            authority=authority,
            expected_status="prepared",
            expected_persisted=False,
        )
        cls._assert_recovery_nav_details(
            complete,
            plan=plan,
            authority=authority,
            expected_status="complete",
            expected_persisted=True,
        )
        planned_base = cls._recovery_base_details(planned)
        complete_base = cls._recovery_base_details(complete)
        if planned_base != complete_base:
            raise SnapshotSetConflictError(
                "snapshot recovery planned/complete NAV base details disagree"
            )
        return {
            "type": "HOLDINGS_SNAPSHOT_TARGET_SET",
            "version": "v2",
            "account": plan.account,
            "as_of": plan.as_of,
            "run_id": authority.run_id,
            "issuer": authority.issuer,
            "target_digest": plan.target_digest,
            "plan_digest": plan.plan_digest,
            "overwrite_existing": authority.overwrite_existing,
            "authority": authority.to_payload(),
            "plan": plan.to_payload(),
            "nav_base_digest": digest_payload(planned_base),
            "planned_nav_details_digest": digest_payload(planned),
            "complete_nav_details_digest": digest_payload(complete),
            "planned_nav_details": planned,
            "complete_nav_details": complete,
        }

    @classmethod
    def parse_recovery_target(
        cls,
        target: dict[str, Any],
    ) -> tuple[SnapshotExactSetPlan, BoundSnapshotWriteAuthority]:
        if target.get("type") != "HOLDINGS_SNAPSHOT_TARGET_SET":
            raise ValueError("not a holdings snapshot exact-set target")
        if target.get("version") != "v2":
            raise ValueError("unsupported holdings snapshot recovery target")
        plan = SnapshotExactSetPlan.from_payload(target.get("plan") or {})
        authority = BoundSnapshotWriteAuthority.from_payload(
            target.get("authority") or {}
        )
        authority.assert_matches(plan)
        expected = {
            "account": plan.account,
            "as_of": plan.as_of,
            "run_id": authority.run_id,
            "issuer": authority.issuer,
            "target_digest": plan.target_digest,
            "plan_digest": plan.plan_digest,
            "overwrite_existing": authority.overwrite_existing,
        }
        mismatches = [
            name for name, value in expected.items() if target.get(name) != value
        ]
        if mismatches:
            raise SnapshotSetConflictError(
                "prepared snapshot target scope/digest mismatch: "
                + ", ".join(mismatches)
            )
        if not authority.confirmed:
            raise PermissionError("prepared snapshot target is not confirmed")
        planned = target.get("planned_nav_details")
        complete = target.get("complete_nav_details")
        if not isinstance(planned, Mapping) or not isinstance(complete, Mapping):
            raise SnapshotSetConflictError(
                "prepared snapshot target requires NAV transition details"
            )
        cls._assert_recovery_nav_details(
            planned,
            plan=plan,
            authority=authority,
            expected_status="prepared",
            expected_persisted=False,
        )
        cls._assert_recovery_nav_details(
            complete,
            plan=plan,
            authority=authority,
            expected_status="complete",
            expected_persisted=True,
        )
        planned_base = cls._recovery_base_details(planned)
        complete_base = cls._recovery_base_details(complete)
        digest_fields = {
            "nav_base_digest": digest_payload(planned_base),
            "planned_nav_details_digest": digest_payload(dict(planned)),
            "complete_nav_details_digest": digest_payload(dict(complete)),
        }
        digest_mismatches = [
            name
            for name, value in digest_fields.items()
            if target.get(name) != value
        ]
        if planned_base != complete_base:
            digest_mismatches.append("planned_complete_nav_base")
        if digest_mismatches:
            raise SnapshotSetConflictError(
                "prepared snapshot NAV transition digest mismatch: "
                + ", ".join(digest_mismatches)
            )
        return plan, authority

    @classmethod
    def classify_recovery_nav_state(
        cls,
        *,
        target: Mapping[str, Any],
        details: Mapping[str, Any],
        plan: SnapshotExactSetPlan,
        authority: BoundSnapshotWriteAuthority,
    ) -> str:
        """Classify a fresh NAV row without accepting non-snapshot base drift."""

        current = dict(details)
        if digest_payload(current) == target.get("complete_nav_details_digest"):
            cls._assert_recovery_nav_details(
                current,
                plan=plan,
                authority=authority,
                expected_status="complete",
                expected_persisted=True,
            )
            return "complete"
        if digest_payload(cls._recovery_base_details(current)) != target.get(
            "nav_base_digest"
        ):
            raise SnapshotSetConflictError(
                "fresh NAV base details drifted from prepared snapshot target"
            )
        status = str(current.get("snapshot_status") or "")
        if status not in {"prepared", "failed"}:
            raise SnapshotSetConflictError(
                f"fresh NAV snapshot status is not recoverable: {status or 'missing'}"
            )
        cls._assert_recovery_nav_details(
            current,
            plan=plan,
            authority=authority,
            expected_status=status,
            expected_persisted=False,
        )
        return "incomplete"

    @staticmethod
    def _assert_exact_readback(
        plan: SnapshotExactSetPlan,
        rows: list[HoldingSnapshot],
    ) -> None:
        # residual_actions rejects duplicates, unknown keys, changed rows, and
        # missing original target rows. A zero-action result is exact equality.
        actions = plan.residual_actions(rows)
        if actions.mutation_count:
            raise RuntimeError(
                "holdings_snapshot fresh readback does not equal exact target set: "
                f"{actions.summary()}"
            )
        desired = {
            snapshot_business_key(row): snapshot_row_payload(row)
            for row in plan.desired
        }
        actual = {
            snapshot_business_key(row): snapshot_row_payload(row) for row in rows
        }
        if actual != desired or snapshot_digest(rows) != plan.row_digest:
            raise RuntimeError(
                "holdings_snapshot v2 digest/readback verification failed"
            )

    def apply_exact_set(
        self,
        *,
        plan: SnapshotExactSetPlan,
        authority: BoundSnapshotWriteAuthority,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        authority.assert_matches(plan)
        if not dry_run and not authority.confirmed:
            raise PermissionError("snapshot exact-set write requires confirmation")
        current = self.storage.list_holding_snapshots_fresh(
            account=plan.account,
            as_of=plan.as_of,
        )
        actions = plan.residual_actions(current)
        result = self.storage.apply_holding_snapshot_actions(
            actions=actions,
            current=current,
            dry_run=dry_run,
        )
        if dry_run:
            return {
                **result,
                "plan_digest": plan.plan_digest,
                "target_digest": plan.target_digest,
                "row_digest": plan.row_digest,
            }

        readback = self.storage.list_holding_snapshots_fresh(
            account=plan.account,
            as_of=plan.as_of,
        )
        self._assert_exact_readback(plan, readback)
        self._write_local_snapshot(
            account=plan.account,
            as_of=plan.as_of,
            snapshots=readback,
        )
        return {
            **result,
            "verified": True,
            "plan_digest": plan.plan_digest,
            "target_digest": plan.target_digest,
            "row_digest": plan.row_digest,
            "row_count": len(readback),
        }

    def persist_holdings_snapshot(
        self,
        *,
        account: str,
        today: Any,
        normalized_valuation: NormalizedValuationSnapshot,
        write_authority: SnapshotWriteAuthority,
        dry_run: bool = False,
    ) -> list[HoldingSnapshot]:
        """Compatibility entrypoint routed through the exact-set engine."""

        as_of = today.strftime("%Y-%m-%d")
        plan = self.plan_exact_set(
            account=account,
            as_of=as_of,
            normalized_valuation=normalized_valuation,
        )
        bound = self.bind_authority(
            authority=write_authority,
            plan=plan,
            dry_run=dry_run,
        )
        self.apply_exact_set(plan=plan, authority=bound, dry_run=dry_run)
        return list(plan.desired)

    def _write_local_snapshot(
        self,
        *,
        account: str,
        as_of: str,
        snapshots: list[HoldingSnapshot],
    ) -> None:
        try:
            out_dir = self.data_dir / "holdings_snapshot" / account
            out_dir.mkdir(parents=True, exist_ok=True)
            out_file = out_dir / f"{as_of}.json"
            payload = {
                "as_of": as_of,
                "account": account,
                "count": len(snapshots),
                "digest_version": SNAPSHOT_DIGEST_VERSION,
                "digest": snapshot_digest(snapshots),
                "snapshots": [
                    snapshot.model_dump(mode="json") for snapshot in snapshots
                ],
            }
            out_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning(
                "_write_local_snapshot failed for %s/%s: %s",
                account,
                as_of,
                exc,
            )


__all__ = ["SnapshotService", "snapshot_digest"]
