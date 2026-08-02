from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import Mock

import skill_api


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_skill_api_report_path_uses_service_and_report_query_boundaries():
    tree = ast.parse((REPO_ROOT / "skill_api.py").read_text(encoding="utf-8"))

    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported_names.update(alias.name for alias in node.names)

    assert "PortfolioService" in imported_names
    assert "ReportQueryService" in imported_names


def test_legacy_transaction_entrypoints_are_absent():
    tree = ast.parse((REPO_ROOT / "skill_api.py").read_text(encoding="utf-8"))

    module_functions = {
        node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    portfolio_skill = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "PortfolioSkill"
    )
    skill_methods = {
        node.name
        for node in portfolio_skill.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    retired = {"buy", "sell", "record_transaction_from_message"}
    assert retired.isdisjoint(module_functions)
    assert retired.isdisjoint(skill_methods)


def _init_db_storage():
    storage = Mock()
    storage.client.app_token = "app-token"
    storage.get_holding_fresh.return_value = None
    storage.get_holdings.return_value = []
    storage.get_nav_history.return_value = []
    return storage


def test_init_db_initial_cash_requires_explicit_broker_before_any_holding_write(
    monkeypatch,
):
    storage = _init_db_storage()
    monkeypatch.setattr(skill_api, "FeishuStorage", Mock(return_value=storage))

    result = skill_api.init_db(account="lx", initial_cash=100)

    assert result["success"] is False
    assert "requires an explicit broker" in result["error"]
    storage.get_holding_fresh.assert_not_called()
    storage.replace_holding.assert_not_called()
    storage.upsert_holding.assert_not_called()


def test_init_db_initial_cash_uses_one_exact_broker_identity(monkeypatch):
    storage = _init_db_storage()
    monkeypatch.setattr(skill_api, "FeishuStorage", Mock(return_value=storage))

    result = skill_api.init_db(
        account="lx",
        initial_cash=100,
        broker=" IBKR ",
    )

    assert result["success"] is True
    storage.get_holding_fresh.assert_called_once_with(
        "CNY-CASH",
        "lx",
        "IBKR",
    )
    created_target = storage.replace_holding.call_args.args[0]
    assert created_target.base_record_id is None
    assert created_target.identity.account == "lx"
    assert created_target.identity.broker == "IBKR"
    assert created_target.values["quantity"] == 100
    assert created_target.owned_fields == (
        skill_api.HOLDING_REQUIRED_VALUE_FIELDS
        | {"asset_class", "industry"}
    )
    storage.upsert_holding.assert_not_called()
