import json
import os
from datetime import date
from unittest.mock import Mock

import pytest

from src.app.snapshot_service import SnapshotService, snapshot_digest
from src.domain.snapshot_contracts import (
    SNAPSHOT_BUSINESS_KEY_FIELDS,
    NormalizedValuationRow,
    NormalizedValuationSnapshot,
    SnapshotExactSetPlan,
    SnapshotSetConflictError,
    SnapshotWriteAuthority,
    ValuationComponent,
    snapshot_business_key,
    snapshot_dedup_key,
    snapshot_row_payload,
)
from src.domain.nav_calculator import ClosedNavTarget
from src.feishu.contracts import get_table_contract, validate_write_fields
from src.models import AssetClass, AssetType, Holding
from src.feishu.repositories.snapshots_repository import SnapshotsRepository


def _normalized_valuation():
    holding = Holding(
        asset_id="000001",
        asset_name="平安银行",
        asset_type=AssetType.A_STOCK,
        account="a",
        broker="CN",
        quantity=12.345,
        avg_cost=9.876,
        currency="CNY",
        asset_class=AssetClass.CN_ASSET,
    )
    row = NormalizedValuationRow.from_holding(
        holding,
        account="a",
        normalized_type="equity",
        price="10.123",
        cny_price="10.123",
    )
    return NormalizedValuationSnapshot.build(
        account="a",
        rows=(row,),
        shares=100.0,
        warnings=[],
        excluded_zero_keys=(),
    )


def _authority(normalized, *, overwrite=False, confirmed=True):
    as_of = "2026-03-19"
    return SnapshotWriteAuthority(
        account="a",
        as_of=as_of,
        run_id="run-snapshot",
        issuer="test",
        overwrite_existing=overwrite,
        confirmed=confirmed,
        target_digest=normalized.target_digest(as_of=as_of),
    )


class _ExactStorage:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.apply_calls = []
        self.fail_readback = False

    def list_holding_snapshots_fresh(self, *, account, as_of):
        assert account == "a"
        assert as_of == "2026-03-19"
        if self.fail_readback and self.apply_calls:
            return []
        return list(self.rows)

    def apply_holding_snapshot_actions(self, *, actions, current, dry_run=False):
        self.apply_calls.append((actions, list(current), dry_run))
        if dry_run:
            return {"dry_run": True, **actions.summary()}
        next_id = 1
        by_id = {row.record_id: row for row in self.rows}
        for row in actions.creates:
            while f"snapshot-{next_id}" in by_id:
                next_id += 1
            created = row.model_copy(update={"record_id": f"snapshot-{next_id}"})
            by_id[created.record_id] = created
        for record_id, row in actions.updates:
            by_id[record_id] = row.model_copy(update={"record_id": record_id})
        for record_id in actions.deletes:
            by_id.pop(record_id, None)
        self.rows = list(by_id.values())
        return {
            "dry_run": False,
            "created": len(actions.creates),
            "updated": len(actions.updates),
            "deleted": len(actions.deletes),
        }


def test_snapshot_service_exact_set_updates_clears_and_deletes(tmp_path):
    normalized = _normalized_valuation()
    desired = normalized.to_snapshot_rows(as_of="2026-03-19")[0]
    current_a = desired.model_copy(
        update={"record_id": "snapshot-a", "remark": "stale"}
    )
    current_b = desired.model_copy(update={
        "record_id": "snapshot-b",
        "asset_id": "obsolete",
        "dedup_key": "a:2026-03-19:CN:obsolete",
    })
    storage = _ExactStorage((current_a, current_b))
    service = SnapshotService(storage=storage, data_dir=tmp_path)

    snapshots = service.persist_holdings_snapshot(
        account="a",
        today=date(2026, 3, 19),
        normalized_valuation=normalized,
        write_authority=_authority(normalized, overwrite=True),
        dry_run=False,
    )

    assert len(snapshots) == 1
    assert snapshots[0].dedup_key == "a:2026-03-19:CN:000001"
    assert storage.rows[0].record_id == "snapshot-a"
    assert storage.rows[0].remark is None
    actions = storage.apply_calls[0][0]
    assert actions.summary() == {
        "create": 0,
        "update": 1,
        "delete": 1,
        "unchanged": 0,
    }

    out_file = tmp_path / "holdings_snapshot" / "a" / "2026-03-19.json"
    payload = json.loads(out_file.read_text(encoding="utf-8"))
    assert payload["count"] == 1
    assert payload["digest"] == snapshot_digest(storage.rows)


def test_generic_normalized_builder_is_not_official():
    assert _normalized_valuation().official_eligible is False


def test_snapshot_service_first_write_requires_confirmed_authority(tmp_path):
    normalized = _normalized_valuation()
    storage = _ExactStorage()
    service = SnapshotService(storage=storage, data_dir=tmp_path)

    service.persist_holdings_snapshot(
        account="a",
        today=date(2026, 3, 19),
        normalized_valuation=normalized,
        write_authority=_authority(normalized),
        dry_run=False,
    )

    assert len(storage.rows) == 1
    assert (tmp_path / "holdings_snapshot" / "a" / "2026-03-19.json").exists()


def test_snapshot_service_dry_run_plans_without_mutation(tmp_path):
    normalized = _normalized_valuation()
    storage = _ExactStorage()
    service = SnapshotService(storage=storage, data_dir=tmp_path)

    service.persist_holdings_snapshot(
        account="a",
        today=date(2026, 3, 19),
        normalized_valuation=normalized,
        write_authority=_authority(normalized, confirmed=False),
        dry_run=True,
    )

    assert storage.rows == []
    assert storage.apply_calls[0][2] is True


def test_snapshot_service_dry_run_does_not_modify_existing_local_snapshot(tmp_path):
    normalized = _normalized_valuation()
    storage = _ExactStorage()
    service = SnapshotService(storage=storage, data_dir=tmp_path)
    out_file = tmp_path / "holdings_snapshot" / "a" / "2026-03-19.json"
    out_file.parent.mkdir(parents=True)
    out_file.write_text('{"digest":"existing"}\n', encoding="utf-8")
    old_mtime_ns = 1_700_000_000_000_000_000
    os.utime(out_file, ns=(old_mtime_ns, old_mtime_ns))

    service.persist_holdings_snapshot(
        account="a",
        today=date(2026, 3, 19),
        normalized_valuation=normalized,
        write_authority=_authority(normalized, confirmed=False),
        dry_run=True,
    )

    assert out_file.read_text(encoding="utf-8") == '{"digest":"existing"}\n'
    assert out_file.stat().st_mtime_ns == old_mtime_ns


def test_snapshot_service_requires_overwrite_for_existing_slice(tmp_path):
    normalized = _normalized_valuation()
    existing = normalized.to_snapshot_rows(as_of="2026-03-19")[0].model_copy(
        update={"record_id": "snapshot-a"}
    )
    storage = _ExactStorage((existing,))
    service = SnapshotService(storage=storage, data_dir=tmp_path)

    with pytest.raises(PermissionError, match="overwrite_existing"):
        service.persist_holdings_snapshot(
            account="a",
            today=date(2026, 3, 19),
            normalized_valuation=normalized,
            write_authority=_authority(normalized, overwrite=False),
            dry_run=False,
        )
    assert storage.apply_calls == []


def test_snapshot_service_empty_target_requires_overwrite_for_existing_slice(tmp_path):
    existing_normalized = _normalized_valuation()
    existing = existing_normalized.to_snapshot_rows(
        as_of="2026-03-19"
    )[0].model_copy(update={"record_id": "snapshot-a"})
    closed = NormalizedValuationSnapshot.from_closed_input(
        ClosedNavTarget.build(
            total_value=100,
            cash_value=100,
            non_cash_value=0,
        ),
        account="a",
        source_provenance={"run_id": "run-snapshot"},
    )
    storage = _ExactStorage((existing,))
    service = SnapshotService(storage=storage, data_dir=tmp_path)

    with pytest.raises(PermissionError, match="overwrite_existing"):
        service.persist_holdings_snapshot(
            account="a",
            today=date(2026, 3, 19),
            normalized_valuation=closed,
            write_authority=_authority(closed, overwrite=False),
            dry_run=False,
        )
    assert storage.apply_calls == []


def test_snapshot_service_unconfirmed_overwrite_performs_zero_mutation(tmp_path):
    normalized = _normalized_valuation()
    existing = normalized.to_snapshot_rows(as_of="2026-03-19")[0].model_copy(
        update={"record_id": "snapshot-a"}
    )
    storage = _ExactStorage((existing,))
    service = SnapshotService(storage=storage, data_dir=tmp_path)

    with pytest.raises(PermissionError, match="confirmed"):
        service.persist_holdings_snapshot(
            account="a",
            today=date(2026, 3, 19),
            normalized_valuation=normalized,
            write_authority=_authority(
                normalized,
                overwrite=True,
                confirmed=False,
            ),
            dry_run=False,
        )
    assert storage.apply_calls == []


def test_snapshot_plan_blocks_duplicate_remote_business_keys():
    normalized = _normalized_valuation()
    desired = normalized.to_snapshot_rows(as_of="2026-03-19")[0]
    duplicate_a = desired.model_copy(update={"record_id": "snapshot-a"})
    duplicate_b = desired.model_copy(update={"record_id": "snapshot-b"})

    with pytest.raises(SnapshotSetConflictError, match="duplicate business keys"):
        SnapshotExactSetPlan.build(
            account="a",
            as_of="2026-03-19",
            target_digest=normalized.target_digest(as_of="2026-03-19"),
            before=(duplicate_a, duplicate_b),
            desired=(desired,),
        )


def test_snapshot_repository_sends_explicit_clear_then_deletes_obsolete():
    normalized = _normalized_valuation()
    desired = normalized.to_snapshot_rows(as_of="2026-03-19")[0]
    current_a = desired.model_copy(
        update={"record_id": "snapshot-a", "remark": "stale"}
    )
    current_b = desired.model_copy(update={
        "record_id": "snapshot-b",
        "asset_id": "obsolete",
        "dedup_key": "a:2026-03-19:CN:obsolete",
    })
    plan = SnapshotExactSetPlan.build(
        account="a",
        as_of="2026-03-19",
        target_digest=normalized.target_digest(as_of="2026-03-19"),
        before=(current_a, current_b),
        desired=(desired,),
    )
    actions = plan.residual_actions((current_a, current_b))
    client = Mock()
    client.batch_update_records.return_value = [{"record_id": "snapshot-a"}]
    client.batch_delete_records.return_value = 1

    class Storage:
        @staticmethod
        def _to_feishu_fields(data, _table, preserve_none=False):
            return {
                key: value
                for key, value in data.items()
                if preserve_none or value is not None
            }

    storage = Storage()
    storage.client = client
    repository = SnapshotsRepository(storage)

    result = repository.apply_holding_snapshot_actions(
        actions=actions,
        current=(current_a, current_b),
        dry_run=False,
    )

    update = client.batch_update_records.call_args.args[1][0]
    assert update == {
        "record_id": "snapshot-a",
        "fields": {"remark": None},
    }
    client.batch_delete_records.assert_called_once_with(
        "holdings_snapshot",
        ["snapshot-b"],
    )
    assert result == {
        "dry_run": False,
        "created": 0,
        "updated": 1,
        "deleted": 1,
        "unchanged": 0,
    }


@pytest.mark.parametrize("field", ["asset_name", "avg_cost", "source", "remark"])
def test_snapshot_write_contract_allows_owned_optional_fields_to_clear(field):
    validate_write_fields(
        "holdings_snapshot",
        "update",
        {field: None},
    )


def test_snapshot_service_raises_when_fresh_readback_is_not_exact(tmp_path):
    normalized = _normalized_valuation()
    storage = _ExactStorage()
    storage.fail_readback = True
    service = SnapshotService(storage=storage, data_dir=tmp_path)

    with pytest.raises(RuntimeError, match="fresh readback"):
        service.persist_holdings_snapshot(
            account="a",
            today=date(2026, 3, 19),
            normalized_valuation=normalized,
            write_authority=_authority(normalized),
            dry_run=False,
        )


def test_snapshot_service_ignores_local_snapshot_write_failure(tmp_path):
    normalized = _normalized_valuation()
    storage = _ExactStorage()
    data_dir = tmp_path / "not_a_directory"
    data_dir.write_text("block mkdir", encoding="utf-8")
    service = SnapshotService(storage=storage, data_dir=data_dir)

    snapshots = service.persist_holdings_snapshot(
        account="a",
        today=date(2026, 3, 19),
        normalized_valuation=normalized,
        write_authority=_authority(normalized),
        dry_run=False,
    )

    assert len(snapshots) == 1


def test_holding_snapshot_preserves_quantity_precision():
    from src.snapshot_models import HoldingSnapshot

    base = {
        "as_of": "2026-07-19",
        "account": "a",
        "asset_id": "asset",
        "broker": "broker",
        "currency": "USD",
        "price": 1,
        "cny_price": 1,
        "dedup_key": "a:2026-07-19:broker:asset",
    }

    assert HoldingSnapshot(
        **base,
        quantity=10.1256,
        market_value_cny=10.13,
    ).quantity == 10.1256
    assert HoldingSnapshot(
        **base,
        quantity=0.004,
        market_value_cny=0,
    ).quantity == 0.004
    assert HoldingSnapshot(
        **base,
        quantity=0.00000001,
        market_value_cny=0,
    ).quantity == 0.00000001


@pytest.mark.parametrize("quantity", [0, "-0", "0.000000004", "-0.000000004"])
def test_holding_snapshot_rejects_quantity_that_normalizes_to_zero(quantity):
    from src.snapshot_models import HoldingSnapshot

    with pytest.raises(ValueError, match="quantity must be nonzero"):
        HoldingSnapshot(
            as_of="2026-07-19",
            account="a",
            asset_id="asset",
            broker="broker",
            quantity=quantity,
            currency="USD",
            price=1,
            cny_price=1,
            market_value_cny=0,
            dedup_key="a:2026-07-19:broker:asset",
        )


@pytest.mark.parametrize("field,value", [
    ("price", None),
    ("price", float("nan")),
    ("price", float("inf")),
    ("cny_price", None),
    ("cny_price", float("nan")),
    ("market_value_cny", None),
    ("market_value_cny", float("inf")),
])
def test_holding_snapshot_rejects_missing_or_nonfinite_required_numbers(
    field,
    value,
):
    from src.snapshot_models import HoldingSnapshot

    payload = {
        "as_of": "2026-07-19",
        "account": "a",
        "asset_id": "asset",
        "broker": "broker",
        "quantity": 1,
        "currency": "USD",
        "price": 1,
        "cny_price": 1,
        "market_value_cny": 1,
        "dedup_key": "a:2026-07-19:broker:asset",
    }
    payload[field] = value

    with pytest.raises(ValueError):
        HoldingSnapshot(**payload)


def test_holding_snapshot_replay_invariant_rejects_inconsistent_value():
    from src.snapshot_models import HoldingSnapshot

    with pytest.raises(ValueError, match="replay mismatch"):
        HoldingSnapshot(
            as_of="2026-07-19",
            account="a",
            asset_id="asset",
            broker="broker",
            quantity="12.345",
            currency="CNY",
            price="10.123",
            cny_price="10.123",
            market_value_cny="124.96",
            dedup_key="a:2026-07-19:broker:asset",
        )


def test_snapshot_digest_v2_covers_native_unit_price():
    from src.snapshot_models import HoldingSnapshot

    base = {
        "as_of": "2026-07-19",
        "account": "a",
        "asset_id": "asset",
        "broker": "broker",
        "quantity": 2,
        "currency": "USD",
        "cny_price": 7,
        "market_value_cny": 14,
        "dedup_key": "a:2026-07-19:broker:asset",
        "source": "record_nav",
    }
    first = HoldingSnapshot(**base, price="1.234567")
    second = HoldingSnapshot(**base, price="1.234568")

    assert first.price == 1.234567
    assert snapshot_digest([first]) != snapshot_digest([second])


def test_snapshot_identity_and_full_row_projection_have_one_contract():
    row = _normalized_valuation().to_snapshot_rows(as_of="2026-03-19")[0]
    table = get_table_contract("holdings_snapshot")

    assert table.business_key == SNAPSHOT_BUSINESS_KEY_FIELDS
    assert snapshot_business_key(row) == tuple(
        str(getattr(row, field_name))
        for field_name in SNAPSHOT_BUSINESS_KEY_FIELDS
    )
    assert tuple(snapshot_row_payload(row)) == tuple(table.fields_by_name)
    assert tuple(SnapshotsRepository.PROJECTION_FIELDS) == tuple(
        table.fields_by_name
    )
    assert row.dedup_key == snapshot_dedup_key(
        account=row.account,
        as_of=row.as_of,
        broker=row.broker,
        asset_id=row.asset_id,
    )
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        type(row)(
            **row.model_dump(),
            future_unregistered_field="ignored",
        )


def test_normalized_total_is_rows_plus_declared_components():
    normalized = _normalized_valuation()
    with_component = NormalizedValuationSnapshot.build(
        account=normalized.account,
        rows=normalized.rows,
        components=(
            ValuationComponent.build(
                name="manual_cash",
                category="cash",
                value_cny="5.00",
                source="test",
                provenance={"authority": "fixture"},
            ),
        ),
        shares=normalized.shares,
        excluded_zero_keys=(),
        source="test_fixture",
    )

    assert with_component.total_value == with_component.rows[0].market_value_cny + 5
    projection = with_component.to_portfolio_valuation()
    assert projection.total_value_cny == 129.97
    assert projection.cash_value_cny == 5.0
