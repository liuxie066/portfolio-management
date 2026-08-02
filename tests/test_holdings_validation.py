from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.app.holdings_validation import (
    ASSET_CLASS_POLICY_VERSION,
    FutuAccountEvidence,
    FutuPositionEvidence,
    HoldingsEvidenceBundle,
    HoldingsValidator,
    canonical_record_payload,
)
from src.domain.holdings import (
    RawHoldingRecord,
    asset_class_for_economic_exposure,
)
from src.models import AssetClass, AssetType


def _record(record_id: str = "rec_1", **overrides):
    fields = {
        "asset_id": "AAPL",
        "asset_name": "Apple",
        "asset_type": "us_stock",
        "account": "lx",
        "broker": "IBKR",
        "quantity": 10,
        "currency": "USD",
    }
    fields.update(overrides)
    return RawHoldingRecord(
        record_id=record_id,
        raw_fields=fields,
        fetched_at=datetime.now(UTC),
    )


def _outcome(report, field):
    return next(item for item in report.records[0].outcomes if item.field == field)


def _futu_bundle(*positions):
    return HoldingsEvidenceBundle(
        futu_by_account={
            "lx": FutuAccountEvidence(
                account="lx",
                source="futu-openapi",
                source_snapshot_id="snap-1",
                source_as_of="2026-07-31T12:00:00Z",
                positions=tuple(positions),
            )
        }
    )


def test_blank_us_currency_is_proposed_from_asset_type_never_defaulted():
    report = HoldingsValidator().validate([_record(currency="")])

    currency = _outcome(report, "currency")
    assert currency.status == "missing_completable"
    assert currency.proposed == "USD"
    assert currency.authority == "asset_type_policy"
    assert currency.authority_id == "asset_type:us_stock"
    assert report.blocking_count == 1


@pytest.mark.parametrize(
    ("asset_type", "asset_id", "expected_status", "expected_currency"),
    [
        ("a_stock", "000001", "missing_completable", "CNY"),
        ("cn_fund", "000001", "missing_completable", "CNY"),
        ("otc_fund", "FUND-1", "missing_completable", "CNY"),
        ("us_stock", "AAPL", "missing_completable", "USD"),
        ("us_fund", "VTI", "missing_completable", "USD"),
        ("hk_stock", "00700", "missing_manual", None),
        ("exchange_fund", "SPY.US", "missing_completable", "USD"),
        ("exchange_fund", "510300.SH", "missing_completable", "CNY"),
        ("exchange_fund", "02800.HK", "missing_manual", None),
        ("crypto", "BTC", "missing_manual", None),
    ],
)
def test_currency_resolver_asset_type_matrix(
    asset_type,
    asset_id,
    expected_status,
    expected_currency,
):
    report = HoldingsValidator().validate(
        [_record(asset_type=asset_type, asset_id=asset_id, currency="")]
    )

    currency = _outcome(report, "currency")
    assert currency.status == expected_status
    assert currency.proposed == expected_currency


@pytest.mark.parametrize(
    ("asset_type", "asset_id", "current"),
    [
        ("otc_fund", "007721", "美国资产"),
        ("cn_fund", "QDII-CN", "港股资产"),
        ("us_fund", "CROSS-BORDER-US", "中国资产"),
        ("hk_fund", "CROSS-BORDER-HK", "美国资产"),
        ("us_stock", "PDD", "中国资产"),
        ("hk_stock", "00700", "中国资产"),
        ("exchange_fund", "SPY.US", "美国资产"),
    ],
)
def test_asset_class_preserves_manual_underlying_exposure_when_type_is_not_authority(
    asset_type,
    asset_id,
    current,
):
    report = HoldingsValidator().validate(
        [
            _record(
                asset_type=asset_type,
                asset_id=asset_id,
                asset_class=current,
            )
        ]
    )

    asset_class = _outcome(report, "asset_class")
    assert asset_class.status == "valid"
    assert asset_class.current == current
    assert asset_class.proposed is None
    assert asset_class.authority == "manual_raw_unverified"


@pytest.mark.parametrize(
    ("asset_type", "asset_id"),
    [
        ("otc_fund", "007721"),
        ("us_stock", "PDD"),
        ("exchange_fund", "SPY.US"),
    ],
)
def test_asset_class_without_instrument_level_exposure_stays_optional_missing(
    asset_type,
    asset_id,
):
    report = HoldingsValidator().validate(
        [_record(asset_type=asset_type, asset_id=asset_id, asset_class="")]
    )

    asset_class = _outcome(report, "asset_class")
    assert asset_class.status == "optional_missing"
    assert asset_class.proposed is None


@pytest.mark.parametrize(
    ("asset_type", "asset_id", "expected"),
    [
        ("a_stock", "000001", "中国资产"),
        ("cash", "CNY-CASH", "现金"),
        ("mmf", "CNY-MMF", "现金"),
    ],
)
def test_asset_class_is_completed_only_when_instrument_type_proves_exposure(
    asset_type,
    asset_id,
    expected,
):
    report = HoldingsValidator().validate(
        [_record(asset_type=asset_type, asset_id=asset_id, asset_class="")]
    )

    asset_class = _outcome(report, "asset_class")
    assert asset_class.status == "missing_completable"
    assert asset_class.proposed == expected
    assert asset_class.authority == "asset_type_policy"
    assert report.asset_class_policy_version == ASSET_CLASS_POLICY_VERSION
    assert (
        report.as_dict()["asset_class_policy_version"]
        == ASSET_CLASS_POLICY_VERSION
    )


def test_asset_class_authority_is_shared_and_never_uses_listing_currency():
    assert asset_class_for_economic_exposure(AssetType.A_STOCK) == AssetClass.CN_ASSET
    assert asset_class_for_economic_exposure(AssetType.CASH) == AssetClass.CASH
    assert asset_class_for_economic_exposure(AssetType.MMF) == AssetClass.CASH
    assert asset_class_for_economic_exposure(AssetType.HK_STOCK) is None
    assert asset_class_for_economic_exposure(AssetType.US_STOCK) is None
    assert asset_class_for_economic_exposure(AssetType.EXCHANGE_FUND) is None


@pytest.mark.parametrize(
    ("asset_type", "asset_id", "expected"),
    [
        ("cash", "CNY-CASH", "CNY"),
        ("cash", "USD-CASH", "USD"),
        ("mmf", "HKD-MMF", "HKD"),
    ],
)
def test_cash_and_mmf_currency_requires_asset_id_prefix(asset_type, asset_id, expected):
    report = HoldingsValidator().validate(
        [_record(asset_type=asset_type, asset_id=asset_id, currency="")]
    )
    assert _outcome(report, "currency").proposed == expected


def test_cash_without_currency_prefix_stays_manual():
    report = HoldingsValidator().validate(
        [_record(asset_type="cash", asset_id="CASH", currency="")]
    )
    assert _outcome(report, "currency").status == "missing_manual"


def test_exact_futu_currency_overrides_hk_heuristic_and_detects_conflict():
    position = FutuPositionEvidence(
        asset_id="00700",
        raw_code="HK.00700",
        asset_name="Tencent",
        security_type="STOCK",
        market="HK",
        currency="CNY",
        currency_explicit=True,
    )
    report = HoldingsValidator().validate(
        [_record(asset_id="00700", asset_type="hk_stock", broker="富途", currency="HKD")],
        evidence=_futu_bundle(position),
    )

    currency = _outcome(report, "currency")
    assert currency.status == "conflict"
    assert currency.current == "HKD"
    assert currency.proposed == "CNY"
    assert currency.authority == "futu_explicit"


def test_market_defaulted_futu_currency_is_not_completion_authority():
    position = FutuPositionEvidence(
        asset_id="00700",
        raw_code="HK.00700",
        asset_name="Tencent",
        security_type="STOCK",
        market="HK",
        currency="HKD",
        currency_explicit=False,
    )
    report = HoldingsValidator().validate(
        [_record(asset_id="00700", asset_type="hk_stock", broker="富途", currency="")],
        evidence=_futu_bundle(position),
    )

    assert _outcome(report, "currency").status == "missing_manual"


def test_futu_evidence_requires_explicit_market_qualifiers_to_agree():
    position = FutuPositionEvidence(
        asset_id="AAPL",
        raw_code="US.AAPL",
        asset_name="Apple",
        security_type="STOCK",
        market="US",
        currency="USD",
        currency_explicit=True,
    )

    conflicting = HoldingsValidator().validate(
        [_record(asset_id="AAPL.HK", broker="富途", currency="")],
        evidence=_futu_bundle(position),
    )
    same_market = HoldingsValidator().validate(
        [_record(asset_id="AAPL.US", broker="富途", currency="")],
        evidence=_futu_bundle(position),
    )

    assert _outcome(conflicting, "currency").status == "missing_completable"
    assert _outcome(conflicting, "currency").authority == "asset_type_policy"
    assert _outcome(same_market, "currency").authority == "futu_explicit"


def test_unqualified_futu_symbol_with_multiple_markets_is_ambiguous():
    report = HoldingsValidator().validate(
        [_record(asset_id="ABC", asset_type="hk_stock", broker="富途", currency="")],
        evidence=_futu_bundle(
            FutuPositionEvidence("ABC", "US.ABC", "ABC US", "STOCK", "US", "USD", True),
            FutuPositionEvidence("ABC", "HK.ABC", "ABC HK", "STOCK", "HK", "HKD", True),
        ),
    )

    assert _outcome(report, "currency").status == "missing_manual"
    assert _outcome(report, "currency").reason_code == "FUTU_POSITION_AMBIGUOUS"


def test_disputed_asset_type_cannot_authorize_currency_completion():
    position = FutuPositionEvidence(
        asset_id="AAPL",
        raw_code="US.AAPL",
        asset_name="Apple",
        security_type="STOCK",
        market="US",
        currency="USD",
        currency_explicit=True,
    )
    report = HoldingsValidator().validate(
        [_record(asset_type="a_stock", broker="富途", currency="")],
        evidence=_futu_bundle(position),
    )

    assert _outcome(report, "asset_type").status == "conflict"
    assert _outcome(report, "currency").status == "missing_manual"


def test_missing_quantity_differs_from_zero_and_nonfinite_is_invalid():
    missing = HoldingsValidator().validate([_record(quantity="")])
    zero = HoldingsValidator().validate([_record(quantity=0)])
    nonfinite = HoldingsValidator().validate([_record(quantity="NaN")])

    assert _outcome(missing, "quantity").status == "missing_manual"
    assert _outcome(zero, "quantity").status == "valid"
    assert _outcome(zero, "quantity").reason_code == "QUANTITY_ZERO_VALID"
    assert _outcome(nonfinite, "quantity").status == "invalid"


def test_duplicate_identity_and_missing_account_are_scoped_integrity_issues():
    report = HoldingsValidator().validate(
        [
            _record("rec_1"),
            _record("rec_2"),
            _record("rec_orphan", asset_id="MSFT", account=""),
        ]
    )

    assert [issue.kind for issue in report.records[0].issues] == ["duplicate_identity"]
    assert [issue.kind for issue in report.records[1].issues] == ["duplicate_identity"]
    assert [issue.kind for issue in report.records[2].issues] == ["orphan"]
    assert report.blocking_count == 3


def test_valid_raw_record_builds_typed_holding_without_defaults():
    report = HoldingsValidator().validate([_record()])
    record = report.records[0]

    assert record.valid_for_typed_holding is True
    holding = record.to_holding()
    assert holding.currency == "USD"
    assert holding.quantity == 10
    assert holding.broker == "IBKR"


def test_missing_registry_required_asset_name_blocks_typed_holding():
    report = HoldingsValidator().validate([_record(asset_name="   ")])
    record = report.records[0]
    outcome = _outcome(report, "asset_name")

    assert outcome.status == "missing_manual"
    assert outcome.reason_code == "ASSET_NAME_MISSING"
    assert outcome.blocks_official_nav is True
    assert report.blocking_count == 1
    assert record.valid_for_typed_holding is False
    with pytest.raises(ValueError, match="not fully valid"):
        record.to_holding()


def test_provider_can_propose_missing_asset_name_but_cannot_supply_raw_fact():
    position = FutuPositionEvidence(
        asset_id="AAPL",
        raw_code="US.AAPL",
        asset_name="Apple Inc.",
        security_type="STOCK",
        market="US",
        currency="USD",
        currency_explicit=True,
    )
    report = HoldingsValidator().validate(
        [_record(asset_name="", broker="富途")],
        evidence=_futu_bundle(position),
    )

    outcome = _outcome(report, "asset_name")
    assert outcome.status == "missing_completable"
    assert outcome.proposed == "Apple Inc."
    assert outcome.blocks_official_nav is True
    assert report.records[0].valid_for_typed_holding is False


def test_manual_asset_name_remains_usable_during_nonblocking_provider_conflict():
    position = FutuPositionEvidence(
        asset_id="AAPL",
        raw_code="US.AAPL",
        asset_name="Apple Inc.",
        security_type="STOCK",
        market="US",
        currency="USD",
        currency_explicit=True,
    )
    report = HoldingsValidator().validate(
        [_record(asset_name="Manual Apple", broker="富途")],
        evidence=_futu_bundle(position),
    )
    record = report.records[0]

    outcome = _outcome(report, "asset_name")
    assert outcome.status == "conflict"
    assert outcome.blocks_official_nav is False
    assert record.valid_for_typed_holding is True
    assert record.to_holding().asset_name == "Manual Apple"


def test_typed_holding_uses_the_same_normalized_asset_type_as_validation():
    record = HoldingsValidator().validate([_record(asset_type="US_STOCK")]).records[0]

    assert record.valid_for_typed_holding is True
    assert record.to_holding().asset_type.value == "us_stock"


@pytest.mark.parametrize("value", [[], "[]"])
def test_empty_native_and_json_text_tags_are_optional_missing(value):
    report = HoldingsValidator().validate([_record(tag=value)])

    outcome = _outcome(report, "tag")
    assert outcome.status == "optional_missing"
    assert outcome.current == []
    assert outcome.reason_code == "TAG_OPTIONAL_MISSING"
    assert outcome.blocks_official_nav is False


@pytest.mark.parametrize("value", [["core", "income"], '["core", "income"]'])
def test_native_and_json_text_tags_build_the_same_typed_holding(value):
    record = HoldingsValidator().validate([_record(tag=value)]).records[0]

    outcome = next(item for item in record.outcomes if item.field == "tag")
    assert outcome.status == "valid"
    assert outcome.current == ["core", "income"]
    assert record.to_holding().tag == ["core", "income"]


@pytest.mark.parametrize(
    "value",
    [
        "not-json",
        "{}",
        "null",
        '"core"',
        '["core", 1]',
        {"core": True},
        ["core", 1],
    ],
)
def test_invalid_tag_shapes_remain_nonblocking_and_preserve_raw_evidence(value):
    report = HoldingsValidator().validate([_record(tag=value)])

    outcome = _outcome(report, "tag")
    assert outcome.status == "invalid"
    assert outcome.current == value
    assert outcome.reason_code == "TAG_INVALID"
    assert outcome.blocks_official_nav is False


def test_tag_digest_canonicalization_keeps_missing_and_empty_array_distinct():
    assert canonical_record_payload({"tag": None})["tag"] is None
    assert canonical_record_payload({"tag": ""})["tag"] is None
    assert canonical_record_payload({"tag": []})["tag"] == []
    assert canonical_record_payload({"tag": "[]"})["tag"] == []


def test_record_digest_normalizes_semantically_equivalent_field_values():
    first = HoldingsValidator().validate(
        [
            _record(
                asset_id=" AAPL ",
                asset_type="US_STOCK",
                quantity=10,
                avg_cost="150.00",
                currency=" usd ",
                tag=["core"],
            )
        ]
    ).records[0]
    second = HoldingsValidator().validate(
        [
            _record(
                asset_id="AAPL",
                asset_type="us_stock",
                quantity="10.0",
                avg_cost=150,
                currency="USD",
                tag='["core"]',
            )
        ]
    ).records[0]
    changed = HoldingsValidator().validate(
        [
            _record(
                quantity=11,
                avg_cost=150,
                tag=["core"],
            )
        ]
    ).records[0]

    assert first.record_digest == second.record_digest
    assert first.record_digest != changed.record_digest


@pytest.mark.parametrize(
    ("created_at", "expected"),
    [
        ("2026/07/31", datetime(2026, 7, 31)),
        ("2026-07-31 12:00:00", datetime(2026, 7, 31, 12)),
    ],
)
def test_validation_accepts_canonical_and_predecessor_holding_dates(
    created_at,
    expected,
):
    report = HoldingsValidator().validate(
        [_record(created_at=created_at, updated_at="")]
    )

    assert _outcome(report, "created_at").status == "valid"
    assert _outcome(report, "updated_at").status == "optional_missing"
    assert report.records[0].to_holding().created_at == expected


def test_invalid_transport_timestamp_is_a_nonblocking_warning():
    report = HoldingsValidator().validate([_record(updated_at="not-a-timestamp")])

    outcome = _outcome(report, "updated_at")
    assert outcome.status == "invalid"
    assert outcome.blocks_official_nav is False
    assert report.records[0].to_holding().updated_at is None


@pytest.mark.parametrize("updated_at", ["2026-08-01", "2026/8/1", "not-a-timestamp"])
def test_holding_date_validation_is_not_permissive(updated_at):
    report = HoldingsValidator().validate([_record(updated_at=updated_at)])

    assert _outcome(report, "updated_at").status == "invalid"


def test_record_digest_normalizes_negative_zero():
    negative_zero = HoldingsValidator().validate([_record(quantity="-0")]).records[0]
    zero = HoldingsValidator().validate([_record(quantity=0)]).records[0]

    assert negative_zero.record_digest == zero.record_digest
