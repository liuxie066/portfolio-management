"""Repository for the Feishu holdings_snapshot table."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List

from ...domain.snapshot_contracts import (
    SnapshotExactSetPlan,
    SnapshotSetActions,
    snapshot_business_key,
    snapshot_row_payload,
)
from ...snapshot_models import HoldingSnapshot
from ..contracts import get_table_contract


_SNAPSHOT_REGISTRY_FIELDS = tuple(
    get_table_contract("holdings_snapshot").fields_by_name
)
_SNAPSHOT_MODEL_FIELDS = tuple(
    field_name
    for field_name in HoldingSnapshot.model_fields
    if field_name != "record_id"
)
if _SNAPSHOT_MODEL_FIELDS != _SNAPSHOT_REGISTRY_FIELDS:
    raise RuntimeError(
        "HoldingSnapshot model disagrees with holdings_snapshot registry; "
        f"model={_SNAPSHOT_MODEL_FIELDS}, registry={_SNAPSHOT_REGISTRY_FIELDS}"
    )


class SnapshotsRepository:
    """Fresh reads and deterministic exact-set writes for holdings snapshots."""

    PROJECTION_FIELDS = list(_SNAPSHOT_REGISTRY_FIELDS)

    def __init__(self, storage):
        self.storage = storage

    def __getattr__(self, name: str):
        return getattr(self.storage, name)

    @staticmethod
    def _snapshot_fields(snapshot: HoldingSnapshot) -> Dict[str, Any]:
        return {
            key: value
            for key, value in snapshot.model_dump(mode="python").items()
            if key != "record_id"
        }

    def _slice_filter(self, *, account: str, as_of: str) -> str:
        return (
            f'CurrentValue.[as_of] = "{self._escape_filter_value(as_of)}" && '
            f'CurrentValue.[account] = "{self._escape_filter_value(account)}"'
        )

    def list_holding_snapshots_fresh(
        self,
        *,
        account: str,
        as_of: str,
    ) -> List[HoldingSnapshot]:
        """Read one complete account/date slice without consulting local caches."""

        records = self.client.list_records(
            "holdings_snapshot",
            filter_str=self._slice_filter(account=account, as_of=as_of),
            field_names=self.PROJECTION_FIELDS,
        )
        rows: list[HoldingSnapshot] = []
        errors: list[str] = []
        for record in records:
            record_id = str((record or {}).get("record_id") or "").strip()
            try:
                if not record_id:
                    raise ValueError("record_id is required")
                fields = self._from_feishu_fields(
                    dict((record or {}).get("fields") or {}),
                    "holdings_snapshot",
                )
                rows.append(HoldingSnapshot(record_id=record_id, **fields))
            except (TypeError, ValueError) as exc:
                errors.append(f"{record_id or '<missing-record-id>'}: {exc}")
        if errors:
            raise ValueError(
                "invalid holdings_snapshot records: " + " | ".join(errors)
            )
        rows.sort(
            key=lambda row: snapshot_business_key(row) + (row.record_id or "",)
        )
        return rows

    @staticmethod
    def _changed_fields(
        current: HoldingSnapshot,
        desired: HoldingSnapshot,
    ) -> Dict[str, Any]:
        current_payload = snapshot_row_payload(current)
        desired_values = SnapshotsRepository._snapshot_fields(desired)
        desired_payload = snapshot_row_payload(desired)
        return {
            field: desired_values[field]
            for field in desired_payload
            if current_payload[field] != desired_payload[field]
        }

    def apply_holding_snapshot_actions(
        self,
        *,
        actions: SnapshotSetActions,
        current: Iterable[HoldingSnapshot],
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Apply already-authorized residual create/update/clear/delete actions."""

        current_by_record_id = {
            str(row.record_id): row for row in current if row.record_id
        }
        creates = [
            {
                "fields": self._to_feishu_fields(
                    self._snapshot_fields(row),
                    "holdings_snapshot",
                )
            }
            for row in actions.creates
        ]
        updates: list[dict[str, Any]] = []
        for record_id, desired in actions.updates:
            current_row = current_by_record_id.get(record_id)
            if current_row is None:
                raise ValueError(
                    f"snapshot update record missing from fresh base: {record_id}"
                )
            changed = self._changed_fields(current_row, desired)
            if changed:
                updates.append({
                    "record_id": record_id,
                    "fields": self._to_feishu_fields(
                        changed,
                        "holdings_snapshot",
                        preserve_none=True,
                    ),
                })
        deletes = list(actions.deletes)
        preview = {
            "dry_run": dry_run,
            "to_create": len(creates),
            "to_update": len(updates),
            "to_delete": len(deletes),
            "unchanged": actions.unchanged,
            "create_sample": creates[:3],
            "update_sample": updates[:3],
            "delete_sample": deletes[:3],
        }
        if dry_run:
            return preview

        created = updated = deleted = 0
        if creates:
            created = len(
                self.client.batch_create_records("holdings_snapshot", creates)
            )
        if updates:
            updated = len(
                self.client.batch_update_records("holdings_snapshot", updates)
            )
        if deletes:
            deleted = int(
                self.client.batch_delete_records("holdings_snapshot", deletes)
            )
        return {
            "dry_run": False,
            "created": created,
            "updated": updated,
            "deleted": deleted,
            "unchanged": actions.unchanged,
        }

    def batch_upsert_holding_snapshots(
        self,
        snapshots: List[HoldingSnapshot],
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Compatibility upsert; official NAV writes use the exact-set API."""

        if not snapshots:
            return {"created": 0, "updated": 0, "dry_run": dry_run}
        any_snapshot = snapshots[0]
        current = self.list_holding_snapshots_fresh(
            account=any_snapshot.account,
            as_of=any_snapshot.as_of,
        )
        plan = SnapshotExactSetPlan.build(
            account=any_snapshot.account,
            as_of=any_snapshot.as_of,
            target_digest="0" * 64,
            before=current,
            desired=snapshots,
        )
        actions = plan.residual_actions(current)
        # Preserve historical upsert semantics: do not remove obsolete rows.
        upsert_actions = SnapshotSetActions(
            creates=actions.creates,
            updates=actions.updates,
            deletes=(),
            unchanged=actions.unchanged,
        )
        return self.apply_holding_snapshot_actions(
            actions=upsert_actions,
            current=current,
            dry_run=dry_run,
        )
