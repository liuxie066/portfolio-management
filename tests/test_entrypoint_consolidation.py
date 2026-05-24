from __future__ import annotations

import pytest

from scripts import nav_history_repair
from scripts import pm
from scripts.migrate_schema import parse_docs_schema, run_schema_check, schema_expectations


def test_nav_history_repair_runs_backfill_module(monkeypatch):
    captured = {}

    def fake_run(args):
        captured["args"] = args

    from src.maintenance.nav_history_repair import backfill

    monkeypatch.setattr(backfill, "run", fake_run)

    assert nav_history_repair.main(["backfill", "--account", "lx", "--from", "2025-01-01", "--to", "2025-01-02", "--dry-run"]) == 0
    assert captured["args"].command == "backfill"
    assert captured["args"].account == "lx"
    assert captured["args"].d_from == "2025-01-01"
    assert captured["args"].d_to == "2025-01-02"
    assert captured["args"].mode == "replace"
    assert captured["args"].dry_run is True


def test_nav_history_repair_runs_patch_module(monkeypatch):
    captured = {}

    def fake_run(args):
        captured["args"] = args

    from src.maintenance.nav_history_repair import patch

    monkeypatch.setattr(patch, "run", fake_run)

    assert nav_history_repair.main(["patch", "--patch-file", "audit/x.json", "--apply"]) == 0
    assert captured["args"].command == "patch"
    assert captured["args"].patch_file == "audit/x.json"
    assert captured["args"].mode == "strong-consistency-gap"
    assert captured["args"].apply is True
    assert captured["args"].validate_level == "basic"
    assert captured["args"].validate_scope == "changed"


def test_nav_history_repair_rejects_unknown_args():
    with pytest.raises(SystemExit) as exc:
        nav_history_repair.main(["patch", "--patch-file", "audit/x.json", "--apply", "--unknown-flag"])

    assert exc.value.code == 2


def test_nav_history_repair_rejects_conflicting_write_flags():
    with pytest.raises(SystemExit) as exc:
        nav_history_repair.main(
            [
                "backfill",
                "--account",
                "lx",
                "--from",
                "2025-01-01",
                "--to",
                "2025-01-02",
                "--dry-run",
                "--apply",
            ]
        )

    assert exc.value.code == 2


def test_pm_cash_flow_reconcile_apply_requires_confirm():
    with pytest.raises(SystemExit) as exc:
        pm.main(["cash-flow", "reconcile", "--apply"])

    assert "requires --confirm" in str(exc.value)


def test_schema_expectations_are_available_from_migrate_schema():
    result = schema_expectations()

    assert result["success"] is True
    assert "holdings" in result["tables"]
    assert "nav_history" in result["tables"]
    assert result["tables"]["transactions"]["role"] == "optional"
    assert result["tables"]["holdings"]["role"] == "core"
    assert "quantity" in result["tables"]["holdings"]["numeric_fields"]
    assert "flow_type" in result["tables"]["cash_flow"]["required"]
    assert "dedup_key" in result["tables"]["cash_flow"]["required"]
    assert "price_cache" not in result["tables"]


def test_docs_schema_field_parser_ignores_non_field_backticks():
    specs = parse_docs_schema()

    assert "price_cache" not in specs
    assert "flow_type" in specs["cash_flow"].required
    assert "direction" not in specs["cash_flow"].required
    assert "broker" not in specs["cash_flow"].required
    assert "total_value" in specs["nav_history"].required
    assert "exchange_fund" not in specs["holdings"].optional
    assert "otc_fund" not in specs["holdings"].optional
    assert "asset_type" not in specs["holdings_snapshot"].required
    assert specs["transactions"].role == "optional"
    assert specs["cash_flow"].role == "core"


def test_schema_check_does_not_block_on_missing_optional_tables(monkeypatch):
    class FakeClient:
        table_configs = {
            "holdings": ("base", "tbl_holdings"),
            "cash_flow": ("base", "tbl_cash_flow"),
            "nav_history": ("base", "tbl_nav_history"),
            "holdings_snapshot": ("base", "tbl_snapshots"),
        }

        fields = {
            "holdings": {"asset_id", "asset_name", "asset_type", "account", "broker", "quantity", "currency"},
            "cash_flow": {"flow_date", "account", "amount", "currency", "flow_type", "cny_amount", "dedup_key"},
            "nav_history": {"date", "account", "total_value", "shares", "nav"},
            "holdings_snapshot": {
                "as_of", "account", "asset_id", "broker", "quantity",
                "currency", "price", "cny_price", "market_value_cny", "dedup_key",
            },
        }

        def _get_table_config(self, table_name):
            if table_name == "transactions":
                raise ValueError("transactions intentionally disabled")
            if table_name in {"compensation_tasks", "schema_version"}:
                raise ValueError(f"{table_name} intentionally disabled")
            return self.table_configs[table_name]

        def _request(self, _method, endpoint, **_kwargs):
            table_id = endpoint.rsplit("/", 2)[-2]
            table_name = {
                "tbl_holdings": "holdings",
                "tbl_cash_flow": "cash_flow",
                "tbl_nav_history": "nav_history",
                "tbl_snapshots": "holdings_snapshot",
            }[table_id]
            return {"items": [{"field_name": name} for name in self.fields[table_name]]}

    monkeypatch.setattr("scripts.migrate_schema.FeishuClient", FakeClient)

    result = run_schema_check()

    assert result["ok"] is True
    assert result["core_ok"] is True
    assert result["all_ok"] is False
    assert result["tables"]["transactions"]["configured"] is False
    assert result["tables"]["transactions"]["blocking"] is False
    assert result["tables"]["transactions"]["ok"] is True
