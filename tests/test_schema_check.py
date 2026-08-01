from __future__ import annotations

from pathlib import Path

import pytest

from scripts import migrate_schema
from src.feishu.contracts import TABLE_CONTRACTS


def _live_field(field):
    item = {
        "field_name": field.name,
        "type": field.type_id,
        "ui_type": field.ui_type,
    }
    if field.select_options:
        item["property"] = {
            "options": [{"name": value} for value in field.select_options]
        }
    return item


def _live_table(table_name: str, *, omit: set[str] | None = None):
    omitted = omit or set()
    return [
        _live_field(field)
        for field in TABLE_CONTRACTS[table_name].fields
        if field.name not in omitted
    ]


class FakeSchemaClient:
    def __init__(
        self,
        *,
        configured: set[str],
        overrides: dict[str, list[dict]] | None = None,
        paginate: str | None = None,
    ):
        self.configured = set(configured)
        self.overrides = overrides or {}
        self.paginate = paginate
        self.calls = []

    def _get_table_config(self, table_name):
        if table_name not in self.configured:
            raise ValueError(f"unconfigured: {table_name}")
        return "base", table_name

    def _request(self, method, endpoint, **kwargs):
        assert method == "GET"
        table_name = endpoint.split("/tables/", 1)[1].split("/", 1)[0]
        params = kwargs["params"]
        self.calls.append((table_name, dict(params)))
        items = self.overrides.get(table_name, _live_table(table_name))
        if self.paginate == table_name:
            split_at = max(1, len(items) // 2)
            if "page_token" not in params:
                return {
                    "items": items[:split_at],
                    "has_more": True,
                    "page_token": "next",
                }
            return {"items": items[split_at:], "has_more": False}
        return {"items": items, "has_more": False}


def _core_tables() -> set[str]:
    return {
        name
        for name, table in TABLE_CONTRACTS.items()
        if table.role.value == "core"
    }


def test_exact_schema_check_reads_all_pages_and_marks_optional_unconfigured(monkeypatch):
    client = FakeSchemaClient(configured=_core_tables(), paginate="holdings")
    monkeypatch.setattr(migrate_schema, "FeishuClient", lambda: client)

    result = migrate_schema.run_schema_check(strict=True)

    assert result["ok"] is True
    assert result["core_ok"] is True
    assert result["complete"] is False
    assert result["all_ok"] is False
    assert result["tables"]["holdings"]["field_mismatches"] == []
    assert result["tables"]["transactions"] == {
        "configured": False,
        "role": "optional",
        "blocking": False,
        "status": "skipped_unconfigured",
        "error": "unconfigured: transactions",
        "ok": None,
    }
    holding_calls = [params for name, params in client.calls if name == "holdings"]
    assert holding_calls == [
        {"page_size": 200},
        {"page_size": 200, "page_token": "next"},
    ]


def test_schema_check_reports_exact_type_ui_and_select_option_drift(monkeypatch):
    holdings = _live_table("holdings")
    for item in holdings:
        if item["field_name"] == "quantity":
            item["type"] = 1
            item["ui_type"] = "Text"
        if item["field_name"] == "industry":
            item["property"]["options"] = [{"name": "其他"}]
    client = FakeSchemaClient(
        configured=_core_tables(),
        overrides={"holdings": holdings},
    )
    monkeypatch.setattr(migrate_schema, "FeishuClient", lambda: client)

    result = migrate_schema.run_schema_check(strict=False)

    assert result["ok"] is False
    mismatches = {
        item["field_name"]: item
        for item in result["tables"]["holdings"]["field_mismatches"]
    }
    assert mismatches["quantity"]["expected_type"] == 2
    assert mismatches["quantity"]["live_type"] == 1
    assert mismatches["industry"]["live_select_options"] == ["其他"]

    with pytest.raises(SystemExit) as exc_info:
        migrate_schema.run_schema_check(strict=True)
    assert exc_info.value.code == 2


def test_configured_transaction_archive_uses_observed_text_contract(monkeypatch):
    configured = _core_tables() | {"transactions"}
    client = FakeSchemaClient(configured=configured)
    monkeypatch.setattr(migrate_schema, "FeishuClient", lambda: client)

    result = migrate_schema.run_schema_check(strict=True)

    transaction = result["tables"]["transactions"]
    assert transaction["status"] == "passed"
    assert transaction["ok"] is True
    assert transaction["field_mismatches"] == []
    assert result["tables"]["compensation_tasks"]["status"] == "skipped_unconfigured"


def test_schema_check_rejects_forbidden_and_extra_fields(monkeypatch):
    cash_flow = _live_table("cash_flow") + [
        {"field_name": "exchange_rate_source", "type": 1, "ui_type": "Text"},
        {"field_name": "unexpected", "type": 1, "ui_type": "Text"},
    ]
    client = FakeSchemaClient(
        configured=_core_tables(),
        overrides={"cash_flow": cash_flow},
    )
    monkeypatch.setattr(migrate_schema, "FeishuClient", lambda: client)

    result = migrate_schema.run_schema_check(strict=False)

    table = result["tables"]["cash_flow"]
    assert table["forbidden_present"] == ["exchange_rate_source"]
    assert table["extra_fields"] == ["exchange_rate_source", "unexpected"]
    assert table["ok"] is False


def test_optional_missing_fields_are_reported_without_failing(monkeypatch):
    client = FakeSchemaClient(
        configured=_core_tables(),
        overrides={
            "cash_flow": _live_table("cash_flow", omit={"updated_at"}),
            "nav_history": _live_table("nav_history", omit={"updated_at"}),
        },
    )
    monkeypatch.setattr(migrate_schema, "FeishuClient", lambda: client)

    result = migrate_schema.run_schema_check(strict=True)

    assert result["ok"] is True
    assert result["tables"]["cash_flow"]["missing_optional"] == ["updated_at"]
    assert result["tables"]["nav_history"]["missing_optional"] == ["updated_at"]


def test_schema_expectations_come_from_registry_not_document_prose(tmp_path, monkeypatch):
    schema_path = Path(tmp_path) / "schema.md"
    schema_path.write_text("not a schema definition", encoding="utf-8")
    monkeypatch.setattr(migrate_schema, "DOCS_SCHEMA", schema_path)

    result = migrate_schema.schema_expectations()

    assert result["source"] == "src.feishu.contracts.TABLE_CONTRACTS"
    assert result["tables"]["holdings"]["fields"]["asset_type"] == {
        "type": 3,
        "ui_type": "SingleSelect",
        "encoding": "single_select",
        "ownership": "manual",
        "schema_required": True,
        "clearable": False,
        "select_options": list(
            TABLE_CONTRACTS["holdings"].fields_by_name["asset_type"].select_options
        ),
    }

    compatibility = migrate_schema.parse_docs_schema(schema_path)
    assert set(compatibility) == set(TABLE_CONTRACTS)
    assert "flow_type" in compatibility["cash_flow"].required
    assert compatibility["cash_flow"].forbidden == TABLE_CONTRACTS["cash_flow"].forbidden_fields


def test_cash_flow_effect_schema_migration_is_dry_run_then_confirmed_apply():
    class FakeClient:
        def __init__(self):
            self.created = []

        def _get_table_config(self, table_name):
            assert table_name == "cash_flow"
            return "base", "tbl"

        def _request(self, method, endpoint, **kwargs):
            if method == "GET":
                return {"items": [], "has_more": False}
            self.created.append(kwargs["json"])
            return {"field_id": f"fld{len(self.created)}"}

    client = FakeClient()
    preview = migrate_schema.migrate_cash_flow_effect_fields(client=client)
    assert preview["dry_run"] is True
    assert preview["missing"] == ["broker"]
    assert client.created == []

    applied = migrate_schema.migrate_cash_flow_effect_fields(
        apply=True,
        confirm=True,
        client=client,
    )
    assert applied["success"] is True
    assert [item["field_name"] for item in client.created] == ["broker"]


def test_cash_flow_effect_schema_migration_blocks_incompatible_existing_field():
    class FakeClient:
        def _get_table_config(self, _table_name):
            return "base", "tbl"

        def _request(self, method, _endpoint, **_kwargs):
            assert method == "GET"
            return {
                "items": [{"field_name": "broker", "type": 2}],
                "has_more": False,
            }

    result = migrate_schema.migrate_cash_flow_effect_fields(
        apply=True,
        confirm=True,
        client=FakeClient(),
    )
    assert result["success"] is False
    assert result["incompatible"][0]["field_name"] == "broker"
