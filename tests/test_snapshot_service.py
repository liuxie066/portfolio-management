import json
import os
from datetime import date
from unittest.mock import Mock

import pytest

from src.app.snapshot_service import SnapshotService, snapshot_digest
from src.domain.snapshot_contracts import (
    NormalizedValuationRow,
    NormalizedValuationSnapshot,
    ValuationComponent,
)
from src.models import AssetClass, AssetType, Holding


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
def test_snapshot_service_writes_when_preview_has_changes(tmp_path):
    storage = Mock()
    storage.batch_upsert_holding_snapshots.side_effect = [
        {"to_create": [{"asset_id": "000001"}], "to_update": []},
        {"created": 1, "updated": 0},
    ]
    service = SnapshotService(storage=storage, data_dir=tmp_path)

    snapshots = service.persist_holdings_snapshot(
        account="a",
        today=date(2026, 3, 19),
        normalized_valuation=_normalized_valuation(),
        dry_run=False,
    )

    assert len(snapshots) == 1
    assert snapshots[0].dedup_key == "a:2026-03-19:CN:000001"
    assert snapshots[0].price == 10.123
    assert snapshots[0].cny_price == 10.123
    assert snapshots[0].market_value_cny == 124.97
    assert storage.batch_upsert_holding_snapshots.call_count == 2
    assert storage.batch_upsert_holding_snapshots.call_args_list[0].kwargs["dry_run"] is True
    assert storage.batch_upsert_holding_snapshots.call_args_list[1].kwargs["dry_run"] is False

    out_file = tmp_path / "holdings_snapshot" / "a" / "2026-03-19.json"
    payload = json.loads(out_file.read_text(encoding="utf-8"))
    assert payload["count"] == 1
    assert payload["digest"] == snapshot_digest(snapshots)
    assert payload["snapshots"][0]["asset_id"] == "000001"


def test_generic_normalized_builder_is_not_official():
    assert _normalized_valuation().official_eligible is False


def test_snapshot_service_skips_feishu_write_when_preview_has_no_changes(tmp_path):
    storage = Mock()
    storage.batch_upsert_holding_snapshots.return_value = {"to_create": [], "to_update": []}
    service = SnapshotService(storage=storage, data_dir=tmp_path)

    service.persist_holdings_snapshot(
        account="a",
        today=date(2026, 3, 19),
        normalized_valuation=_normalized_valuation(),
        dry_run=False,
    )

    storage.batch_upsert_holding_snapshots.assert_called_once()
    assert (tmp_path / "holdings_snapshot" / "a" / "2026-03-19.json").exists()


def test_snapshot_service_passes_dry_run_to_actual_write(tmp_path):
    storage = Mock()
    storage.batch_upsert_holding_snapshots.side_effect = [
        {"to_create": [], "to_update": [{"asset_id": "000001"}]},
        {"created": 0, "updated": 0},
    ]
    service = SnapshotService(storage=storage, data_dir=tmp_path)

    service.persist_holdings_snapshot(
        account="a",
        today=date(2026, 3, 19),
        normalized_valuation=_normalized_valuation(),
        dry_run=True,
    )

    assert storage.batch_upsert_holding_snapshots.call_count == 2
    assert storage.batch_upsert_holding_snapshots.call_args_list[1].kwargs["dry_run"] is True


def test_snapshot_service_dry_run_does_not_modify_existing_local_snapshot(tmp_path):
    storage = Mock()
    storage.batch_upsert_holding_snapshots.return_value = {"to_create": [], "to_update": []}
    service = SnapshotService(storage=storage, data_dir=tmp_path)
    out_file = tmp_path / "holdings_snapshot" / "a" / "2026-03-19.json"
    out_file.parent.mkdir(parents=True)
    out_file.write_text('{"digest":"existing"}\n', encoding="utf-8")
    old_mtime_ns = 1_700_000_000_000_000_000
    os.utime(out_file, ns=(old_mtime_ns, old_mtime_ns))

    service.persist_holdings_snapshot(
        account="a",
        today=date(2026, 3, 19),
        normalized_valuation=_normalized_valuation(),
        dry_run=True,
    )

    assert out_file.read_text(encoding="utf-8") == '{"digest":"existing"}\n'
    assert out_file.stat().st_mtime_ns == old_mtime_ns


def test_snapshot_service_raises_when_feishu_write_fails(tmp_path):
    storage = Mock()
    storage.batch_upsert_holding_snapshots.side_effect = RuntimeError("boom")
    service = SnapshotService(storage=storage, data_dir=tmp_path)

    with pytest.raises(RuntimeError, match="boom"):
        service.persist_holdings_snapshot(
            account="a",
            today=date(2026, 3, 19),
            normalized_valuation=_normalized_valuation(),
            dry_run=False,
        )


def test_snapshot_service_ignores_local_snapshot_write_failure(tmp_path):
    storage = Mock()
    storage.batch_upsert_holding_snapshots.return_value = {"to_create": [], "to_update": []}
    data_dir = tmp_path / "not_a_directory"
    data_dir.write_text("block mkdir", encoding="utf-8")
    service = SnapshotService(storage=storage, data_dir=data_dir)

    snapshots = service.persist_holdings_snapshot(
        account="a",
        today=date(2026, 3, 19),
        normalized_valuation=_normalized_valuation(),
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
