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
