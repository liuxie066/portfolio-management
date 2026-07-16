from __future__ import annotations

import ast
import io
import json
from pathlib import Path
import sys
from contextlib import redirect_stdout
from tempfile import TemporaryDirectory

from pytest import MonkeyPatch

from scripts import publish_daily_report


REPO_ROOT = Path(__file__).resolve().parents[1]


def _module_ast(path: str) -> ast.Module:
    return ast.parse((REPO_ROOT / path).read_text(encoding="utf-8"))


def test_generate_daily_report_html_is_renderer_only():
    tree = _module_ast("scripts/generate_daily_report_html.py")

    imported_names = set()
    forbidden_calls = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                forbidden_calls.add(func.id)
            elif isinstance(func, ast.Attribute):
                forbidden_calls.add(func.attr)

    assert "PortfolioSkill" not in imported_names
    assert "build_snapshot" not in forbidden_calls
    assert "generate_report" not in forbidden_calls
    assert "full_report" not in forbidden_calls
    assert "get_nav_history" not in forbidden_calls


def test_publish_daily_report_direct_path_uses_application_bundle_service():
    tree = _module_ast("scripts/publish_daily_report.py")
    build_report = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_build_report_data_direct"
    )
    called_names = set()
    imported_names = set()
    for node in ast.walk(build_report):
        if isinstance(node, ast.ImportFrom):
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                called_names.add(func.id)
            elif isinstance(func, ast.Attribute):
                called_names.add(func.attr)

    assert "PortfolioService" in imported_names
    assert "daily_report_bundle" in called_names
    assert "get_skill" not in called_names
    assert "build_snapshot" not in called_names
    assert "record_nav" not in called_names
    assert "generate_report" not in called_names


def test_publish_daily_report_build_report_data_passes_account():
    import src.service.application as app_module

    calls = []

    class FakePortfolioService:
        def daily_report_bundle(self, **kwargs):
            calls.append(("daily_report_bundle", kwargs))
            return {
                "success": True,
                "account": kwargs["account"],
                "run_id": kwargs["run_id"],
                "snapshot": {"run_id": kwargs["run_id"]},
                "report": {"success": True, "date": "2026-04-20", "run_id": kwargs["run_id"]},
                "nav_result": {"success": True, "nav": 1.23},
                "nav_snapshot": {"success": True},
                "stage_timings": {},
                "futu_sync_result": None,
            }

    old_service = app_module.PortfolioService
    try:
        app_module.PortfolioService = FakePortfolioService
        bundle = publish_daily_report.build_report_data(
            price_timeout=5,
            dry_run=True,
            account="alice",
            no_service=True,
            run_id="run-report-1",
        )
    finally:
        app_module.PortfolioService = old_service

    assert bundle["account"] == "alice"
    assert bundle["run_id"] == "run-report-1"
    assert bundle["snapshot"]["run_id"] == "run-report-1"
    assert bundle["report"]["run_id"] == "run-report-1"
    assert calls == [
        ("daily_report_bundle", {
            "account": "alice",
            "price_timeout": 5,
            "dry_run": True,
            "confirm": False,
            "use_bulk_persist": False,
            "sync_futu_cash_mmf": False,
            "sync_futu_dry_run": True,
            "run_id": "run-report-1",
        }),
    ]


def test_publish_daily_report_futu_sync_defaults_to_dry_run():
    import src.service.application as app_module

    calls = []

    class FakePortfolioService:
        def daily_report_bundle(self, **kwargs):
            calls.append(("daily_report_bundle", kwargs))
            return {
                "success": True,
                "account": kwargs["account"],
                "run_id": kwargs["run_id"],
                "futu_sync_result": {"success": True, "dry_run": kwargs["sync_futu_dry_run"]},
            }

    old_service = app_module.PortfolioService
    try:
        app_module.PortfolioService = FakePortfolioService
        bundle = publish_daily_report.build_report_data(
            price_timeout=5,
            dry_run=True,
            sync_futu_cash_mmf=True,
            account="alice",
            no_service=True,
        )
    finally:
        app_module.PortfolioService = old_service

    assert bundle["futu_sync_result"] == {"success": True, "dry_run": True}
    assert calls == [("daily_report_bundle", {
        "account": "alice",
        "price_timeout": 5,
        "dry_run": True,
        "confirm": False,
        "use_bulk_persist": False,
        "sync_futu_cash_mmf": True,
        "sync_futu_dry_run": True,
        "run_id": bundle["run_id"],
    })]


def test_publish_daily_report_prefers_service_bundle():
    calls = []

    class FakeClient:
        def __init__(self, base_url=None, timeout=0.5):
            calls.append(("client", base_url, timeout))

        def daily_report_bundle(self, **kwargs):
            calls.append(("daily_report_bundle", kwargs))
            return {
                "success": True,
                "account": kwargs["account"],
                "snapshot": {"snapshot_time": "2026-04-20T08:00:00", "holdings_data": {"holdings": []}},
                "nav_result": {"success": True},
                "report": {"success": True, "date": "2026-04-20"},
                "nav_snapshot": {"success": True},
                "stage_timings": {},
                "futu_sync_result": None,
            }

    import src.service.client as client_module

    patch = MonkeyPatch()
    try:
        patch.setattr(client_module, "PortfolioServiceClient", FakeClient)
        bundle = publish_daily_report.build_report_data(
            price_timeout=5,
            dry_run=False,
            use_bulk_nav_upsert=True,
            sync_futu_cash_mmf=True,
            sync_futu_dry_run=False,
            account="alice",
            service_url="http://127.0.0.1:9999",
            service_timeout=1.5,
            run_id="run-report-1",
        )
    finally:
        patch.undo()

    assert bundle["account"] == "alice"
    assert bundle["run_id"] == "run-report-1"
    assert calls == [
        ("client", "http://127.0.0.1:9999", 1.5),
        ("daily_report_bundle", {
            "account": "alice",
            "price_timeout": 5,
            "dry_run": False,
            "confirm": True,
            "use_bulk_persist": True,
            "sync_futu_cash_mmf": True,
            "sync_futu_dry_run": False,
            "run_id": "run-report-1",
        }),
    ]


def test_publish_daily_report_main_prints_result_while_suppressing_internal_stdout():
    def fake_build_report_data(**kwargs):
        print("internal price log")
        return {
            "account": kwargs["account"],
            "run_id": kwargs["run_id"],
            "nav_result": {"success": True, "nav": 1.23},
            "report": {"success": True, "date": "2026-05-23"},
            "nav_snapshot": {"success": True},
            "stage_timings": {"snapshot_ms": 1},
            "futu_sync_result": None,
        }

    patch = MonkeyPatch()
    try:
        patch.setattr(publish_daily_report, "build_report_data", fake_build_report_data)
        patch.setattr(sys, "argv", [
            "publish_daily_report.py",
            "--account",
            "alice",
            "--dry-run",
            "--no-html",
            "--run-id",
            "run-main-1",
        ])

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            publish_daily_report.main()
    finally:
        patch.undo()

    output = stdout.getvalue()
    assert "internal price log" not in output
    payload = json.loads(output)
    assert payload["success"] is True
    assert payload["account"] == "alice"
    assert payload["run_id"] == "run-main-1"
    assert payload["nav_result"]["nav"] == 1.23


def test_publish_daily_report_main_quiet_suppresses_success_output():
    patch = MonkeyPatch()
    try:
        patch.setattr(
            publish_daily_report,
            "build_report_data",
            lambda **kwargs: {
                "account": kwargs["account"],
                "run_id": kwargs["run_id"],
                "nav_result": {"success": True},
                "report": {"success": True, "date": "2026-05-23"},
                "nav_snapshot": {"success": True},
                "stage_timings": {},
                "futu_sync_result": None,
            },
        )
        patch.setattr(sys, "argv", [
            "publish_daily_report.py",
            "--account",
            "alice",
            "--dry-run",
            "--no-html",
            "--quiet",
            "--run-id",
            "run-main-quiet",
        ])

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            publish_daily_report.main()
    finally:
        patch.undo()

    assert stdout.getvalue() == ""


def test_publish_daily_report_parse_args_uses_config_defaults_and_cli_overrides():
    with TemporaryDirectory() as tmp:
        config_file = Path(tmp) / "config.json"
        config_file.write_text(
            json.dumps({
                "account": "cfg-account",
                "report": {
                    "account_label": "family",
                    "reports_dir": "out/reports",
                    "publish_root": "out/publish",
                    "sync_futu_cash_mmf": True,
                    "sync_futu_dry_run": False,
                },
            }),
            encoding="utf-8",
        )

        patch = MonkeyPatch()
        try:
            patch.setattr(publish_daily_report.app_config, "_CONFIG_FILE", config_file)
            for name in (
                "PM_REPORT_ACCOUNT_LABEL",
                "PM_REPORTS_DIR",
                "PM_PUBLISH_ROOT",
                "PM_SYNC_FUTU_CASH_MMF",
                "PM_SYNC_FUTU_DRY_RUN",
            ):
                patch.delenv(name, raising=False)
            publish_daily_report.app_config.reload_config()

            patch.setattr(sys, "argv", ["publish_daily_report.py"])
            args = publish_daily_report.parse_args()
            assert args.account_label == "family"
            assert args.reports_dir == "out/reports"
            assert args.publish_root == "out/publish"
            assert args.sync_futu_cash_mmf is True
            assert args.sync_futu_dry_run is False

            patch.setattr(sys, "argv", [
                "publish_daily_report.py",
                "--account-label",
                "manual",
                "--no-sync-futu-cash-mmf",
                "--sync-futu-dry-run",
            ])
            args = publish_daily_report.parse_args()
            assert args.account_label == "manual"
            assert args.sync_futu_cash_mmf is False
            assert args.sync_futu_dry_run is True
        finally:
            patch.undo()
            publish_daily_report.app_config.reload_config()


def test_publish_report_returns_local_artifact_without_public_url():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        config = publish_daily_report.PublishConfig(
            repo_root=root,
            workspace=root,
            reports_dir=root / "reports",
            publish_root=root / "published",
            account_label="family",
        )

        result = publish_daily_report.publish_report("2026-05-24", "<html></html>", config)

        assert result["slug"] == "investment-daily-2026-05-24"
        assert result["relative_path"] == "investment-daily-2026-05-24/index.html"
        assert result["public_url"] is None
        assert result["public_url_status"] == "disabled"
        assert (root / "published" / "investment-daily-2026-05-24" / "index.html").exists()
