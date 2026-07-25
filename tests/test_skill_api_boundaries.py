from __future__ import annotations

import ast
from pathlib import Path


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
