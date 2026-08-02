from dataclasses import FrozenInstanceError

import pytest

from src.models import ArchivedTransaction

from src.feishu.contracts import (
    ACTIVE_REMOTE_TABLES,
    RETIRED_REMOTE_TABLES,
    TABLE_CONTRACTS,
    FieldEncoding,
    WriteOperation,
    field_names_by_encoding,
    get_table_contract,
    parse_table_ref,
    validate_write_fields,
)


def test_registry_is_immutable_and_covers_active_remote_tables():
    assert set(ACTIVE_REMOTE_TABLES) == set(TABLE_CONTRACTS)
    assert RETIRED_REMOTE_TABLES == {"price_cache"}
    assert "price_cache" not in TABLE_CONTRACTS

    with pytest.raises(TypeError):
        TABLE_CONTRACTS["other"] = TABLE_CONTRACTS["holdings"]
    with pytest.raises(FrozenInstanceError):
        TABLE_CONTRACTS["holdings"].name = "other"


def test_encoding_projections_are_derived_from_registry():
    for table_name, table in TABLE_CONTRACTS.items():
        expected = {
            field.name
            for field in table.fields
            if field.encoding is FieldEncoding.NUMBER
        }
        assert field_names_by_encoding(table_name, FieldEncoding.NUMBER) == expected


@pytest.mark.parametrize(
    ("raw", "default", "expected"),
    [
        ("base/table", None, ("base", "table")),
        ("  base/table  ", None, ("base", "table")),
        ("table", "shared", ("shared", "table")),
    ],
)
def test_parse_table_ref_accepts_only_unambiguous_forms(raw, default, expected):
    assert parse_table_ref(
        raw,
        default_app_token=default,
        table_name="holdings",
    ) == expected


@pytest.mark.parametrize("raw", [None, "", "   ", "/table", "base/", "base/table/extra"])
def test_parse_table_ref_rejects_empty_or_ambiguous_forms(raw):
    with pytest.raises(ValueError):
        parse_table_ref(raw, default_app_token="shared", table_name="holdings")


def test_write_validation_rejects_domain_only_select_values_before_transport():
    common = {
        "asset_id": "FUND",
        "asset_name": "Fund",
        "asset_type": "us_fund",
        "account": "lx",
        "broker": "IBKR",
        "quantity": 1,
        "currency": "USD",
    }

    validate_write_fields(
        "holdings",
        WriteOperation.CREATE,
        {**common, "industry": "AI"},
    )

    with pytest.raises(ValueError, match="unsupported select value for asset_type"):
        validate_write_fields(
            "holdings",
            WriteOperation.CREATE,
            {**common, "asset_type": "fund"},
        )
    with pytest.raises(ValueError, match="unsupported select value for industry"):
        validate_write_fields(
            "holdings",
            WriteOperation.CREATE,
            {**common, "industry": "互联网"},
        )


def test_transactions_are_structurally_readable_but_have_no_write_contract():
    table = get_table_contract("transactions")

    assert table.fields_by_name["tx_date"].ui_type == "Text"
    assert table.fields_by_name["tx_type"].ui_type == "Text"
    assert table.fields_by_name["market"].schema_required is False
    assert table.business_key == ("account", "request_id")
    assert table.write_contracts == ()
    assert set(ArchivedTransaction.model_fields) - {"record_id"} == set(
        table.fields_by_name
    )
    with pytest.raises(ValueError, match="read-only table"):
        validate_write_fields("transactions", "create", {"tx_date": "2025-01-01"})


def test_create_rejects_null_while_update_preserves_transport_level_null():
    required = {
        "asset_id": "AAPL",
        "asset_name": "Apple",
        "asset_type": "us_stock",
        "account": "lx",
        "broker": "IBKR",
        "quantity": 1,
        "currency": "USD",
    }
    with pytest.raises(ValueError, match="create fields cannot be null"):
        validate_write_fields(
            "holdings",
            "create",
            {**required, "avg_cost": None},
        )

    validate_write_fields("holdings", "update", {"avg_cost": None})


def test_holdings_write_contract_is_the_identity_and_clearability_source():
    table = get_table_contract("holdings")
    create = table.write_contract("create")

    assert create is not None
    assert set(table.business_key).issubset(create.required_fields)
    assert create.required_fields == {
        "asset_id",
        "asset_name",
        "asset_type",
        "account",
        "broker",
        "quantity",
        "currency",
    }
    assert {
        field.name
        for field in table.fields
        if field.clearable
    } == {"avg_cost", "asset_class", "industry"}

    with pytest.raises(ValueError, match="fields cannot be cleared: tag"):
        validate_write_fields("holdings", "update", {"tag": None})
    with pytest.raises(ValueError, match="fields cannot be cleared: asset_name"):
        validate_write_fields("holdings", "update", {"asset_name": "   "})
    validate_write_fields("holdings", "update", {"tag": "[]"})

    with pytest.raises(ValueError, match="缺少必填字段: asset_name"):
        validate_write_fields(
            "holdings",
            "create",
            {
                "asset_id": "AAPL",
                "asset_name": "   ",
                "asset_type": "us_stock",
                "account": "lx",
                "broker": "IBKR",
                "quantity": 1,
                "currency": "USD",
            },
        )


def test_retired_remote_price_cache_fails_closed():
    with pytest.raises(ValueError, match="retired"):
        get_table_contract("price_cache")
