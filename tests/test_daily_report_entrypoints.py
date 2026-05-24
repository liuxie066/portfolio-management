from __future__ import annotations

import ast
import io
import json
from pathlib import Path
import sys
import types
from contextlib import redirect_stdout
from tempfile import TemporaryDirectory

from pytest import MonkeyPatch

from scripts import publish_daily_report


REPO_ROOT = Path(__file__).resolve().parents[1]


def _module_ast(path: str) -> ast.Module:
    return ast.parse((REPO_ROOT / path).read_text(encoding="utf-8"))


class _SysModulesPatch:
    def __init__(self, name, value):
        self.name = name
        self.value = value
        self.old = None
        self.had_old = False

    def __enter__(self):
        self.had_old = self.name in sys.modules
        self.old = sys.modules.get(self.name)
        sys.modules[self.name] = self.value
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.had_old:
            sys.modules[self.name] = self.old
        else:
            sys.modules.pop(self.name, None)


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


def test_publish_daily_report_returns_renderer_bundle_shape():
    tree = _module_ast("scripts/publish_daily_report.py")
    build_report = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_build_report_data_direct"
    )
    return_dicts = [node.value for node in ast.walk(build_report) if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict)]
    keys = {
        key.value
        for ret in return_dicts
        for key in ret.keys
        if isinstance(key, ast.Constant)
    }

    assert {"snapshot", "report", "nav_result", "nav_snapshot"}.issubset(keys)


def test_publish_daily_report_build_report_data_passes_account():
    calls = []

    class FakeStorage:
        def get_nav_history(self, account, days):
            calls.append(("get_nav_history", account, days))
            return [{"date": "2026-04-20", "nav": 1.0}]

    class FakeSkill:
        def __init__(self, account):
            self.account = account
            self.storage = FakeStorage()

        def build_snapshot(self, **kwargs):
            calls.append(("build_snapshot", self.account, kwargs))
            return {"valuation": None}

        def record_nav(self, **kwargs):
            calls.append(("record_nav", self.account, kwargs["dry_run"], kwargs.get("run_id")))
            return {
                "success": True,
                "date": "2026-04-20",
                "nav": 1.23,
                "total_value": 123.0,
                "pnl": 4.5,
            }

        def generate_report(self, **kwargs):
            calls.append(("generate_report", self.account, kwargs["report_type"], kwargs.get("nav_override", {}).get("nav")))
            return {"success": True, "date": "2026-04-20"}

        def get_nav(self, **kwargs):
            calls.append(("get_nav", self.account, kwargs["days"]))
            return {"success": True}

    fake_skill_api = types.SimpleNamespace(
        get_skill=lambda account=None: FakeSkill(account or "default")
    )
    with _SysModulesPatch("skill_api", fake_skill_api):
        bundle = publish_daily_report.build_report_data(
            price_timeout=5,
            dry_run=True,
            account="alice",
            no_service=True,
            run_id="run-report-1",
        )

    assert bundle["account"] == "alice"
    assert bundle["run_id"] == "run-report-1"
    assert bundle["snapshot"]["run_id"] == "run-report-1"
    assert bundle["report"]["run_id"] == "run-report-1"
    assert ("build_snapshot", "alice", {"price_timeout_seconds": 5}) in calls
    assert ("record_nav", "alice", True, "run-report-1") in calls
    assert ("generate_report", "alice", "daily", 1.23) in calls
    assert ("get_nav", "alice", 2) in calls


def test_publish_daily_report_futu_sync_defaults_to_dry_run():
    calls = []

    class FakeStorage:
        def get_nav_history(self, account, days):
            return []

    class FakeSkill:
        def __init__(self, account):
            self.account = account
            self.storage = FakeStorage()

        def sync_futu_cash_mmf(self, dry_run):
            calls.append(("sync_futu_cash_mmf", dry_run))
            return {"success": True, "dry_run": dry_run}

        def build_snapshot(self, **_kwargs):
            return {"valuation": None}

        def record_nav(self, **kwargs):
            return {"success": True}

        def generate_report(self, **kwargs):
            return {"success": True, "date": "2026-04-20"}

        def get_nav(self, **kwargs):
            return {"success": True}

    fake_skill_api = types.SimpleNamespace(
        get_skill=lambda account=None: FakeSkill(account or "default")
    )
    with _SysModulesPatch("skill_api", fake_skill_api):
        bundle = publish_daily_report.build_report_data(
            price_timeout=5,
            dry_run=True,
            sync_futu_cash_mmf=True,
            account="alice",
            no_service=True,
        )

    assert bundle["futu_sync_result"] == {"success": True, "dry_run": True}
    assert calls == [("sync_futu_cash_mmf", True)]


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
            assert args.publish_base_url is None
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
