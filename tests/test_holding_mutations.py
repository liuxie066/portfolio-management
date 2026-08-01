import pytest

from src.domain.holding_mutations import (
    EMPTY_HOLDING_DIGEST,
    HOLDING_VALUE_FIELDS,
    IDENTITY_FIELDS,
    HoldingIdentity,
    HoldingPatch,
    HoldingRepairPatch,
    HoldingTarget,
    UNSET,
    canonical_holding,
    holding_state_digest,
    raw_holding_state_digest,
)
from src.domain.holdings import RawHoldingRecord
from src.models import AssetClass, AssetType, Holding, Industry
from src.feishu.contracts import get_table_contract


def _holding(**overrides):
    data = {
        "record_id": "rec-1",
        "asset_id": " AAPL ",
        "asset_name": "Apple",
        "asset_type": AssetType.US_STOCK,
        "account": " lx ",
        "broker": " IBKR ",
        "quantity": 10,
        "avg_cost": 123.45,
        "currency": "usd",
        "asset_class": AssetClass.US_ASSET,
        "industry": Industry.TECH,
        "tag": ["核心"],
    }
    data.update(overrides)
    return Holding(**data)


def test_identity_and_values_canonicalize_once():
    canonical = canonical_holding(_holding())

    assert HoldingIdentity.from_holding(canonical) == HoldingIdentity(
        "AAPL",
        "lx",
        "IBKR",
    )
    assert canonical.currency == "USD"
    assert canonical.asset_id == "AAPL"
    assert canonical.account == "lx"
    assert canonical.broker == "IBKR"
    assert HoldingIdentity(" AAPL ", " lx ", " IBKR ").cache_key() == (
        HoldingIdentity("AAPL", "lx", "IBKR").cache_key()
    )


def test_domain_projection_covers_the_registry_and_identity_model():
    table = get_table_contract("holdings")

    assert set(table.fields_by_name) == (
        IDENTITY_FIELDS
        | HOLDING_VALUE_FIELDS
        | {"created_at", "updated_at"}
    )
    assert set(HoldingIdentity.__dataclass_fields__) == set(table.business_key)


def test_patch_distinguishes_unset_from_authorized_null_clear():
    base = canonical_holding(_holding())
    patch = HoldingPatch.from_base(base, avg_cost=None, quantity=0)

    assert patch.asset_name is UNSET
    assert patch.values == {"quantity": 0.0, "avg_cost": None}
    assert patch.owned_fields == {"quantity", "avg_cost"}

    with pytest.raises(ValueError, match="tag cannot be null"):
        HoldingPatch.from_base(base, tag=None)

    with pytest.raises(ValueError, match="asset_name is required"):
        HoldingPatch.from_base(base, asset_name="   ")


def test_raw_repair_patch_is_immutable_restricted_and_base_bound():
    raw = RawHoldingRecord(
        "rec-raw",
        {
            "asset_id": " AAPL ",
            "account": " lx ",
            "broker": " IBKR ",
            "asset_name": "Apple",
            "currency": "",
        },
    )
    patch = HoldingRepairPatch.from_raw(raw, {"currency": " usd "})

    assert patch.identity == HoldingIdentity("AAPL", "lx", "IBKR")
    assert patch.record_id == "rec-raw"
    assert patch.base_digest == raw_holding_state_digest(
        raw.record_id,
        raw.raw_fields,
    )
    assert dict(patch.values) == {"currency": "USD"}
    with pytest.raises(TypeError):
        patch.values["currency"] = "HKD"
    with pytest.raises(ValueError, match="unsupported"):
        HoldingRepairPatch.from_raw(raw, {"quantity": 2})


def test_complete_target_requires_authority_for_every_changed_field():
    base = canonical_holding(_holding())
    unsafe_partial = Holding(
        asset_id="AAPL",
        asset_name="Apple",
        asset_type=AssetType.US_STOCK,
        account="lx",
        broker="IBKR",
        quantity=5,
        currency="USD",
    )

    with pytest.raises(ValueError, match="changes unowned fields"):
        HoldingTarget.from_holdings(
            base=base,
            target=unsafe_partial,
            owned_fields={"quantity"},
        )


def test_explicit_target_clear_is_bound_to_fresh_base_digest():
    base = canonical_holding(_holding())
    desired = canonical_holding(base)
    desired.quantity = 0
    desired.avg_cost = None
    target = HoldingTarget.from_holdings(
        base=base,
        target=desired,
        owned_fields={"quantity", "avg_cost"},
    )

    assert target.values["avg_cost"] is None
    assert target.base_record_id == "rec-1"
    assert target.base_digest == holding_state_digest(base)
    assert target.to_payload()["owned_fields"] == ["avg_cost", "quantity"]
    assert HoldingTarget.from_payload(target.to_payload()) == target


def test_create_target_uses_empty_base_and_does_not_own_default_tag():
    desired = _holding(
        record_id=None,
        avg_cost=None,
        asset_class=None,
        industry=None,
        tag=[],
    )
    target = HoldingTarget.from_holdings(
        base=None,
        target=desired,
        owned_fields={"asset_name", "asset_type", "quantity", "currency"},
    )

    assert target.base_record_id is None
    assert target.base_digest == EMPTY_HOLDING_DIGEST
    assert "tag" not in target.owned_fields


@pytest.mark.parametrize(
    ("overrides", "expected_field"),
    [
        ({"avg_cost": 123.45}, "avg_cost"),
        ({"tag": ["manual"]}, "tag"),
    ],
)
def test_create_target_rejects_non_neutral_optional_value_without_ownership(
    overrides,
    expected_field,
):
    target_values = {
        "record_id": None,
        "avg_cost": None,
        "asset_class": None,
        "industry": None,
        "tag": [],
        **overrides,
    }
    desired = _holding(**target_values)

    with pytest.raises(
        ValueError,
        match=rf"non-neutral unowned values: {expected_field}",
    ):
        HoldingTarget.from_holdings(
            base=None,
            target=desired,
            owned_fields={"asset_name", "asset_type", "quantity", "currency"},
        )
