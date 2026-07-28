from pathlib import Path

import pytest

from scripts import migrate_schema


SCHEMA_TEXT = """# Test schema

### holdings

Role: core

Required fields:
- `asset_id` (text) - system
- `quantity` (number) - system

Optional fields:
- `updated_at` (text/datetime) - system
- `details` (text/json) - system
"""


def test_typed_schema_check_reads_all_pages_and_accepts_documented_alternatives(tmp_path, monkeypatch):
    schema_path = tmp_path / "schema.md"
    schema_path.write_text(SCHEMA_TEXT, encoding="utf-8")

    class FakeClient:
        def __init__(self):
            self.calls = []

        def _get_table_config(self, table_name):
            assert table_name == "holdings"
            return "base", "tbl"

        def _request(self, method, endpoint, **kwargs):
            self.calls.append(kwargs["params"])
            if "page_token" not in kwargs["params"]:
                return {
                    "items": [{"field_name": "asset_id", "type": 1, "ui_type": "Text"}],
                    "has_more": True,
                    "page_token": "next",
                }
            return {
                "items": [
                    {"field_name": "quantity", "type": 2, "ui_type": "Number"},
                    {"field_name": "updated_at", "type": 5, "ui_type": "DateTime"},
                    {"field_name": "details", "type": 1, "ui_type": "Text"},
                ],
                "has_more": False,
            }

    client = FakeClient()
    monkeypatch.setattr(migrate_schema, "DOCS_SCHEMA", schema_path)
    monkeypatch.setattr(migrate_schema, "FeishuClient", lambda: client)

    result = migrate_schema.run_schema_check(strict=True)

    assert result["ok"] is True
    assert result["tables"]["holdings"]["required_type_mismatches"] == []
    assert result["tables"]["holdings"]["optional_type_mismatches"] == []
    assert client.calls == [{"page_size": 200}, {"page_size": 200, "page_token": "next"}]


def test_strict_schema_check_rejects_required_type_mismatch(tmp_path, monkeypatch):
    schema_path = tmp_path / "schema.md"
    schema_path.write_text(SCHEMA_TEXT, encoding="utf-8")

    class FakeClient:
        def _get_table_config(self, _table_name):
            return "base", "tbl"

        def _request(self, _method, _endpoint, **_kwargs):
            return {
                "items": [
                    {"field_name": "asset_id", "type": 1, "ui_type": "Text"},
                    {"field_name": "quantity", "type": 1, "ui_type": "Text"},
                ],
                "has_more": False,
            }

    monkeypatch.setattr(migrate_schema, "DOCS_SCHEMA", schema_path)
    monkeypatch.setattr(migrate_schema, "FeishuClient", FakeClient)

    non_strict = migrate_schema.run_schema_check(strict=False)
    mismatch = non_strict["tables"]["holdings"]["required_type_mismatches"]
    assert mismatch == [{
        "field_name": "quantity",
        "expected": ["number"],
        "live_type": 1,
        "live_ui_type": "Text",
    }]
    assert non_strict["ok"] is True

    with pytest.raises(SystemExit) as exc_info:
        migrate_schema.run_schema_check(strict=True)
    assert exc_info.value.code == 2


def test_parse_docs_schema_normalizes_json_text_family(tmp_path):
    schema_path = Path(tmp_path) / "schema.md"
    schema_path.write_text(SCHEMA_TEXT, encoding="utf-8")

    spec = migrate_schema.parse_docs_schema(schema_path)["holdings"]

    assert spec.required["asset_id"] == frozenset({"text"})
    assert spec.optional["updated_at"] == frozenset({"text", "datetime"})
    assert spec.optional["details"] == frozenset({"text", "json-text"})


def test_schema_check_rejects_forbidden_fields(tmp_path, monkeypatch):
    schema_path = tmp_path / "schema.md"
    schema_path.write_text(
        SCHEMA_TEXT
        + """
Forbidden fields:
- `exchange_rate_source`
""",
        encoding="utf-8",
    )

    class FakeClient:
        def _get_table_config(self, _table_name):
            return "base", "tbl"

        def _request(self, _method, _endpoint, **_kwargs):
            return {
                "items": [
                    {"field_name": "asset_id", "type": 1},
                    {"field_name": "quantity", "type": 2},
                    {"field_name": "exchange_rate_source", "type": 1},
                ],
                "has_more": False,
            }

    monkeypatch.setattr(migrate_schema, "DOCS_SCHEMA", schema_path)
    monkeypatch.setattr(migrate_schema, "FeishuClient", FakeClient)

    result = migrate_schema.run_schema_check(strict=False)
    assert result["ok"] is False
    assert result["tables"]["holdings"]["forbidden_present"] == [
        "exchange_rate_source"
    ]
    with pytest.raises(SystemExit) as exc_info:
        migrate_schema.run_schema_check(strict=True)
    assert exc_info.value.code == 2


def test_cash_flow_effect_schema_migration_is_dry_run_then_confirmed_apply():
    class FakeClient:
        def __init__(self):
            self.created = []

        def _get_table_config(self, table_name):
            assert table_name == "cash_flow"
            return "base", "tbl"

        def _request(self, method, endpoint, **kwargs):
            if method == "GET":
                return {
                    "items": [
                    ],
                    "has_more": False,
                }
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
