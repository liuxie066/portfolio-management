from __future__ import annotations

import json
import io
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from pytest import MonkeyPatch

from scripts import pm


class _PortfolioServicePatch:
    def __init__(self, service_cls):
        self.service_cls = service_cls
        self.old = None

    def __enter__(self):
        import src.service.application as app_module

        self.app_module = app_module
        self.old = app_module.PortfolioService
        app_module.PortfolioService = self.service_cls
        return self

    def __exit__(self, exc_type, exc, tb):
        self.app_module.PortfolioService = self.old


def test_pm_report_requires_preview_flag():
    try:
        pm.main(["report", "daily", "--json"])
    except SystemExit as exc:
        assert "preview-only" in str(exc)
    else:
        raise AssertionError("expected SystemExit")


def test_pm_report_preview_marks_noncanonical_output():
    class FakePortfolioService:
        def generate_report(self, **kwargs):
            return {
                "success": True,
                "report_type": kwargs["report_type"],
                "account": kwargs["account"],
            }

    stdout = io.StringIO()
    with _PortfolioServicePatch(FakePortfolioService), redirect_stdout(stdout):
        assert pm.main(["report", "daily", "--preview", "--account", "alice", "--no-service", "--json"]) == 0

    out = json.loads(stdout.getvalue())
    assert out["success"] is True
    assert out["report_type"] == "daily"
    assert out["account"] == "alice"
    assert out["preview_only"] is True
    assert out["canonical_entrypoint"] == "scripts/publish_daily_report.py"


def test_pm_cash_passes_account():
    class FakePortfolioService:
        def get_cash(self, **kwargs):
            return {
                "success": True,
                "account": kwargs["account"],
            }

    stdout = io.StringIO()
    with _PortfolioServicePatch(FakePortfolioService), redirect_stdout(stdout):
        assert pm.main(["cash", "--account", "bob", "--no-service", "--json"]) == 0

    out = json.loads(stdout.getvalue())
    assert out["success"] is True
    assert out["account"] == "bob"


def test_pm_json_suppresses_internal_stdout_by_default():
    class FakePortfolioService:
        def get_cash(self, **kwargs):
            print("internal log")
            return {"success": True, "account": kwargs["account"]}

    stdout = io.StringIO()
    with _PortfolioServicePatch(FakePortfolioService), redirect_stdout(stdout):
        assert pm.main(["cash", "--account", "bob", "--no-service", "--json"]) == 0

    out = json.loads(stdout.getvalue())
    assert out["success"] is True
    assert out["account"] == "bob"
    assert "internal log" not in stdout.getvalue()


def test_pm_failure_payload_returns_nonzero_exit_code():
    class FakePortfolioService:
        def get_distribution(self, **_kwargs):
            return {
                "success": False,
                "error": "missing holdings table",
            }

    stdout = io.StringIO()
    with _PortfolioServicePatch(FakePortfolioService), redirect_stdout(stdout):
        assert pm.main(["positions", "distribution", "--account", "bob", "--no-service", "--json"]) == 1

    out = json.loads(stdout.getvalue())
    assert out["success"] is False
    assert out["error"] == "missing holdings table"


def test_pm_accounts_lists_discovered_accounts():
    class FakePortfolioService:
        def list_accounts(self, **kwargs):
            return {
                "success": True,
                "include_default": kwargs["include_default"],
                "accounts": ["alice"],
            }

    stdout = io.StringIO()
    with _PortfolioServicePatch(FakePortfolioService), redirect_stdout(stdout):
        assert pm.main(["accounts", "--exclude-default", "--no-service", "--json"]) == 0

    out = json.loads(stdout.getvalue())
    assert out["success"] is True
    assert out["include_default"] is False
    assert out["accounts"] == ["alice"]


def test_pm_overview_passes_accounts_and_timeout():
    class FakePortfolioService:
        def multi_account_overview(self, **kwargs):
            return {
                "success": True,
                "accounts": kwargs["accounts"],
                "price_timeout": kwargs["price_timeout"],
                "include_details": kwargs["include_details"],
            }

    stdout = io.StringIO()
    with _PortfolioServicePatch(FakePortfolioService), redirect_stdout(stdout):
        assert pm.main(["overview", "--accounts", "alice,bob", "--timeout", "7", "--details", "--no-service", "--json"]) == 0

    out = json.loads(stdout.getvalue())
    assert out["success"] is True
    assert out["accounts"] == "alice,bob"
    assert out["price_timeout"] == 7
    assert out["include_details"] is True


def test_pm_cash_prefers_service_when_available():
    import src.service.client as client_module

    calls = []

    class FakeClient:
        def __init__(self, base_url=None, timeout=0.5):
            calls.append(("init", base_url, timeout))

        def get_cash(self, *, account):
            calls.append(("get_cash", account))
            return {"success": True, "account": account, "source": "service"}

    old_client = client_module.PortfolioServiceClient
    try:
        client_module.PortfolioServiceClient = FakeClient
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            assert pm.main(["cash", "--account", "bob", "--service-url", "http://local", "--service-timeout", "1", "--json"]) == 0
    finally:
        client_module.PortfolioServiceClient = old_client

    out = json.loads(stdout.getvalue())
    assert out["source"] == "service"
    assert out["account"] == "bob"
    assert calls == [("init", "http://local", 1.0), ("get_cash", "bob")]


def test_pm_service_response_error_does_not_fallback():
    import src.service.client as client_module
    from src.service.client import PortfolioServiceResponseError

    calls = []

    class FakeClient:
        def __init__(self, base_url=None, timeout=0.5):
            calls.append(("init", base_url, timeout))

        def get_cash(self, *, account):
            calls.append(("get_cash", account))
            raise PortfolioServiceResponseError("bad service payload")

    old_client = client_module.PortfolioServiceClient
    try:
        client_module.PortfolioServiceClient = FakeClient
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            assert pm.main(["cash", "--account", "bob", "--service-url", "http://local", "--json"]) == 1
    finally:
        client_module.PortfolioServiceClient = old_client

    assert calls == [("init", "http://local", 0.5), ("get_cash", "bob")]
    out = json.loads(stdout.getvalue())
    assert out["success"] is False
    assert "bad service payload" in out["error"]


def test_pm_require_service_fails_instead_of_fallback():
    import src.service.client as client_module
    from src.service.client import PortfolioServiceUnavailable

    calls = []

    class FakeClient:
        def __init__(self, base_url=None, timeout=0.5):
            calls.append(("init", base_url, timeout))

        def get_cash(self, *, account):
            calls.append(("get_cash", account))
            raise PortfolioServiceUnavailable("down")

    old_client = client_module.PortfolioServiceClient
    try:
        client_module.PortfolioServiceClient = FakeClient
        try:
            pm.main(["cash", "--account", "bob", "--service-url", "http://local", "--require-service", "--json"])
        except SystemExit as exc:
            assert "--require-service" in str(exc)
        else:
            raise AssertionError("expected SystemExit")
    finally:
        client_module.PortfolioServiceClient = old_client

    assert calls == [("init", "http://local", 0.5), ("get_cash", "bob")]


def test_pm_read_unavailable_falls_back_once():
    import src.service.client as client_module
    from src.service.client import PortfolioServiceUnavailable

    calls = []

    class FakeClient:
        def __init__(self, base_url=None, timeout=0.5):
            calls.append(("client", base_url, timeout))

        def get_cash(self, *, account):
            calls.append(("service", account))
            raise PortfolioServiceUnavailable("down")

    class FakePortfolioService:
        def get_cash(self, *, account):
            calls.append(("direct", account))
            return {"success": True, "account": account, "source": "direct"}

    old_client = client_module.PortfolioServiceClient
    try:
        client_module.PortfolioServiceClient = FakeClient
        stdout = io.StringIO()
        with _PortfolioServicePatch(FakePortfolioService), redirect_stdout(stdout):
            assert pm.main(["cash", "--account", "bob", "--service-url", "http://local", "--json"]) == 0
    finally:
        client_module.PortfolioServiceClient = old_client

    assert json.loads(stdout.getvalue())["source"] == "direct"
    assert calls == [
        ("client", "http://local", 0.5),
        ("service", "bob"),
        ("direct", "bob"),
    ]


def test_pm_write_unavailable_never_falls_back():
    import src.service.client as client_module
    from src.service.client import PortfolioServiceUnavailable

    calls = []

    class FakeClient:
        def __init__(self, base_url=None, timeout=0.5):
            calls.append(("client", base_url, timeout))

        def sync_futu_holdings(self, **kwargs):
            calls.append(("service", kwargs))
            raise PortfolioServiceUnavailable("down")

    class FakePortfolioService:
        def sync_futu_holdings(self, **kwargs):
            calls.append(("direct", kwargs))
            return {"success": True, **kwargs}

    old_client = client_module.PortfolioServiceClient
    try:
        client_module.PortfolioServiceClient = FakeClient
        stdout = io.StringIO()
        with _PortfolioServicePatch(FakePortfolioService), redirect_stdout(stdout):
            assert pm.main(["futu", "sync", "--account", "lx", "--service-url", "http://local", "--json"]) == 1
    finally:
        client_module.PortfolioServiceClient = old_client

    error = json.loads(stdout.getvalue())["error"]
    assert "outcome is unknown" in error
    assert "request may already have executed" in error
    assert "direct fallback was not attempted" in error
    assert "Do not blindly retry" in error
    assert "--no-service" in error
    assert calls == [
        ("client", "http://local", 0.5),
        ("service", {
            "account": "lx",
            "dry_run": True,
            "confirm": False,
            "allow_empty_stock_snapshot": False,
        }),
    ]


def test_pm_init_nav_passes_account_and_write_flags():
    class FakePortfolioService:
        def init_nav_history(self, **kwargs):
            return {
                "success": True,
                "account": kwargs["account"],
                "date": kwargs["date_str"],
                "dry_run": kwargs["dry_run"],
                "confirm": kwargs["confirm"],
            }

    stdout = io.StringIO()
    with _PortfolioServicePatch(FakePortfolioService), redirect_stdout(stdout):
        assert pm.main([
            "init-nav",
            "--account", "sy",
            "--date", "2026-04-20",
            "--write",
            "--confirm",
            "--json",
        ]) == 0

    out = json.loads(stdout.getvalue())
    assert out["success"] is True
    assert out["account"] == "sy"
    assert out["date"] == "2026-04-20"
    assert out["dry_run"] is False
    assert out["confirm"] is True


def test_pm_init_nav_write_requires_confirm():
    try:
        pm.main(["init-nav", "--account", "hb", "--write"])
    except SystemExit as exc:
        assert "--confirm" in str(exc)
    else:
        raise AssertionError("expected SystemExit")


def test_pm_nav_record_passes_account_and_write_flags():
    class FakePortfolioService:
        def record_nav(self, **kwargs):
            return {
                "success": True,
                "account": kwargs["account"],
                "dry_run": kwargs["dry_run"],
                "confirm": kwargs["confirm"],
                "overwrite_existing": kwargs["overwrite_existing"],
                "use_bulk_persist": kwargs["use_bulk_persist"],
                "price_timeout": kwargs["price_timeout"],
            }

    stdout = io.StringIO()
    with _PortfolioServicePatch(FakePortfolioService), redirect_stdout(stdout):
        assert pm.main([
            "nav",
            "record",
            "--account", "alice",
            "--timeout", "9",
            "--write",
            "--confirm",
            "--no-overwrite",
            "--use-bulk-persist",
            "--no-service",
            "--json",
        ]) == 0

    out = json.loads(stdout.getvalue())
    assert out["success"] is True
    assert out["account"] == "alice"
    assert out["dry_run"] is False
    assert out["confirm"] is True
    assert out["overwrite_existing"] is False
    assert out["use_bulk_persist"] is True
    assert out["price_timeout"] == 9


def test_pm_nav_record_write_requires_confirm():
    try:
        pm.main(["nav", "record", "--account", "hb", "--write"])
    except SystemExit as exc:
        assert "--confirm" in str(exc)
    else:
        raise AssertionError("expected SystemExit")


def test_pm_daily_runs_nav_record_and_distribution():
    import src.service.application as app_module

    calls = []

    class FakePortfolioService:
        def daily_report_bundle(self, **kwargs):
            calls.append(("daily_report_bundle", kwargs))
            return {
                "success": True,
                "nav_result": {
                    "success": True,
                    "date": "2026-05-23",
                    "nav": 1.2345,
                    "shares": 100,
                    "total_value": 123.45,
                    "dry_run": kwargs["dry_run"],
                },
                "distribution": {
                    "success": True,
                    "total_value": 123.45,
                    "by_type": [{"type": "stock", "value": 100, "ratio": 0.81}],
                },
            }

    old_service = app_module.PortfolioService
    stdout = io.StringIO()
    try:
        app_module.PortfolioService = FakePortfolioService
        with redirect_stdout(stdout):
            assert pm.main(["daily", "--account", "alice", "--timeout", "8", "--no-service", "--json"]) == 0
    finally:
        app_module.PortfolioService = old_service

    out = json.loads(stdout.getvalue())
    assert out["success"] is True
    assert out["command"] == "daily"
    assert out["account"] == "alice"
    assert out["dry_run"] is True
    assert out["nav"]["nav"] == 1.2345
    assert out["distribution"]["by_type"][0]["type"] == "stock"
    assert calls == [
        ("daily_report_bundle", {
            "account": "alice",
            "price_timeout": 8,
            "dry_run": True,
            "confirm": False,
            "overwrite_existing": True,
            "use_bulk_persist": False,
        }),
    ]


def test_pm_daily_write_requires_confirm():
    try:
        pm.main(["daily", "--account", "hb", "--write"])
    except SystemExit as exc:
        assert "--confirm" in str(exc)
    else:
        raise AssertionError("expected SystemExit")


def test_pm_daily_failure_payload_returns_nonzero_exit_code():
    import src.service.application as app_module

    class FakePortfolioService:
        def daily_report_bundle(self, **_kwargs):
            return {"success": False, "error": "nav failed"}

    old_service = app_module.PortfolioService
    stdout = io.StringIO()
    try:
        app_module.PortfolioService = FakePortfolioService
        with redirect_stdout(stdout):
            assert pm.main(["daily", "--account", "alice", "--no-service", "--json"]) == 1
    finally:
        app_module.PortfolioService = old_service

    out = json.loads(stdout.getvalue())
    assert out["success"] is False
    assert out["status"] == "failed"
    assert out["nav"]["error"] == "nav failed"


def test_pm_positions_distribution_prefers_service_when_available():
    import src.service.client as client_module

    calls = []

    class FakeClient:
        def __init__(self, base_url=None, timeout=0.5):
            calls.append(("init", base_url, timeout))

        def get_distribution(self, *, account, accounts=None, by_asset=False, include_value=True):
            calls.append(("get_distribution", account, accounts, by_asset, include_value))
            return {"success": True, "total_value": 10, "source": "service"}

    old_client = client_module.PortfolioServiceClient
    try:
        client_module.PortfolioServiceClient = FakeClient
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            assert pm.main(["positions", "distribution", "--account", "bob", "--service-url", "http://local", "--json"]) == 0
    finally:
        client_module.PortfolioServiceClient = old_client

    out = json.loads(stdout.getvalue())
    assert out["source"] == "service"
    assert calls == [("init", "http://local", 0.5), ("get_distribution", "bob", None, False, True)]


def test_pm_positions_distribution_by_asset_no_value_flags_passed_to_service():
    import src.service.client as client_module

    calls = []

    class FakeClient:
        def __init__(self, base_url=None, timeout=0.5):
            calls.append(("init", base_url, timeout))

        def get_distribution(self, *, account=None, accounts=None, by_asset=False, include_value=True):
            calls.append(("get_distribution", account, accounts, by_asset, include_value))
            return {"success": True, "by_asset": []}

    old_client = client_module.PortfolioServiceClient
    try:
        client_module.PortfolioServiceClient = FakeClient
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            assert pm.main(["positions", "distribution", "--accounts", "alice,bob", "--by-asset", "--no-value", "--service-url", "http://local", "--json"]) == 0
    finally:
        client_module.PortfolioServiceClient = old_client

    out = json.loads(stdout.getvalue())
    assert out["success"] is True
    assert calls == [("init", "http://local", 0.5), ("get_distribution", None, "alice,bob", True, False)]


def test_pm_positions_distribution_group_cash_implies_asset_merge():
    import src.service.client as client_module

    calls = []

    class FakeClient:
        def __init__(self, base_url=None, timeout=0.5):
            calls.append(("init", base_url, timeout))

        def get_distribution(
            self,
            *,
            account=None,
            accounts=None,
            by_asset=False,
            include_value=True,
            group_cash=False,
        ):
            calls.append((
                "get_distribution",
                account,
                accounts,
                by_asset,
                include_value,
                group_cash,
            ))
            return {"success": True, "by_asset": []}

    old_client = client_module.PortfolioServiceClient
    try:
        client_module.PortfolioServiceClient = FakeClient
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            assert pm.main([
                "positions",
                "distribution",
                "--accounts",
                "lx,sy",
                "--group-cash",
                "--service-url",
                "http://local",
                "--json",
            ]) == 0
    finally:
        client_module.PortfolioServiceClient = old_client

    assert json.loads(stdout.getvalue())["success"] is True
    assert calls == [
        ("init", "http://local", 0.5),
        ("get_distribution", None, "lx,sy", True, True, True),
    ]


def test_pm_nav_record_prefers_service_when_available():
    import src.service.client as client_module

    calls = []

    class FakeClient:
        def __init__(self, base_url=None, timeout=0.5):
            calls.append(("init", base_url, timeout))

        def record_nav(self, **kwargs):
            calls.append(("record_nav", kwargs))
            return {"success": True, "source": "service", **kwargs}

    old_client = client_module.PortfolioServiceClient
    try:
        client_module.PortfolioServiceClient = FakeClient
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            assert pm.main([
                "nav",
                "record",
                "--account", "alice",
                "--timeout", "9",
                "--write",
                "--confirm",
                "--no-overwrite",
                "--use-bulk-persist",
                "--run-id", "run-nav-1",
                "--service-url", "http://local",
                "--json",
            ]) == 0
    finally:
        client_module.PortfolioServiceClient = old_client

    out = json.loads(stdout.getvalue())
    assert out["source"] == "service"
    assert out["account"] == "alice"
    assert calls == [
        ("init", "http://local", 0.5),
        ("record_nav", {
            "account": "alice",
            "price_timeout": 9,
            "dry_run": False,
            "confirm": True,
            "overwrite_existing": False,
            "use_bulk_persist": True,
            "run_id": "run-nav-1",
        }),
    ]


def test_pm_daily_prefers_service_for_nav_and_distribution():
    import src.service.client as client_module

    calls = []

    class FakeClient:
        def __init__(self, base_url=None, timeout=0.5):
            calls.append(("init", base_url, timeout))

        def daily_report_bundle(self, **kwargs):
            calls.append(("daily_report_bundle", kwargs))
            return {
                "success": True,
                "run_id": kwargs["run_id"],
                "nav_result": {"success": True, "nav": 1.23, "dry_run": kwargs["dry_run"], "run_id": kwargs["run_id"]},
                "distribution": {"success": True, "total_value": 10},
            }

    old_client = client_module.PortfolioServiceClient
    try:
        client_module.PortfolioServiceClient = FakeClient
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            assert pm.main(["daily", "--account", "alice", "--timeout", "8", "--run-id", "run-daily-1", "--service-url", "http://local", "--json"]) == 0
    finally:
        client_module.PortfolioServiceClient = old_client

    out = json.loads(stdout.getvalue())
    assert out["success"] is True
    assert out["nav"]["nav"] == 1.23
    assert out["run_id"] == "run-daily-1"
    assert calls == [
        ("init", "http://local", 0.5),
        ("daily_report_bundle", {
            "account": "alice",
            "price_timeout": 8,
            "dry_run": True,
            "confirm": False,
            "overwrite_existing": True,
            "use_bulk_persist": False,
            "run_id": "run-daily-1",
        }),
    ]


def test_pm_daily_job_prefers_service_client():
    import src.service.client as client_module

    calls = []

    class FakeClient:
        def __init__(self, base_url=None, timeout=0.5):
            calls.append(("init", base_url, timeout))

        def daily_nav_job(self, **kwargs):
            calls.append(("daily_nav_job", kwargs))
            return {"success": True, "status": "completed", "summary": {"dry_run": 2}, "items": [], **kwargs}

    old_client = client_module.PortfolioServiceClient
    try:
        client_module.PortfolioServiceClient = FakeClient
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            assert pm.main([
                "daily-job",
                "--accounts", "alice,bob",
                "--nav-date", "2026-05-22",
                "--timeout", "9",
                "--overwrite",
                "--force-non-business-day",
                "--run-id", "run-job-1",
                "--service-url", "http://local",
                "--json",
            ]) == 0
    finally:
        client_module.PortfolioServiceClient = old_client

    out = json.loads(stdout.getvalue())
    assert out["status"] == "completed"
    assert calls == [
        ("init", "http://local", 0.5),
        ("daily_nav_job", {
            "accounts": "alice,bob",
            "nav_date": "2026-05-22",
            "price_timeout": 9,
            "dry_run": True,
            "confirm": False,
            "overwrite_existing": True,
            "use_bulk_persist": False,
            "sync_futu_cash_mmf": False,
            "force_non_business_day": True,
            "run_id": "run-job-1",
        }),
    ]


def test_pm_config_inspect_outputs_yaml_sources_and_redacts_secrets():
    from src import config

    with TemporaryDirectory() as tmp:
        config_file = Path(tmp) / "config.yaml"
        config_file.write_text(
            """
account: lx
feishu:
  app_secret: secret123456
""",
            encoding="utf-8",
        )

        patch = MonkeyPatch()
        stdout = io.StringIO()
        try:
            patch.setenv(config.CONFIG_FILE_ENV, str(config_file))
            patch.delenv("PORTFOLIO_ACCOUNT", raising=False)
            patch.delenv("FEISHU_APP_SECRET", raising=False)
            config.reload_config()

            with redirect_stdout(stdout):
                assert pm.main([
                    "config",
                    "inspect",
                    "--keys", "account,feishu.app_secret",
                    "--json",
                ]) == 0
        finally:
            patch.undo()
            config.reload_config()

    out = json.loads(stdout.getvalue())
    assert out["success"] is True
    assert out["config_format"] == "yaml"
    assert out["values"]["account"]["value"] == "lx"
    assert out["values"]["feishu.app_secret"]["value"] == "sec...456"
    assert out["values"]["feishu.app_secret"]["source"] == f"file:{config_file}"


def test_pm_config_doctor_returns_nonzero_for_missing_deploy_config():
    from src import config

    with TemporaryDirectory() as tmp:
        config_file = Path(tmp) / "config.yaml"
        config_file.write_text("account: lx\n", encoding="utf-8")

        patch = MonkeyPatch()
        stdout = io.StringIO()
        try:
            patch.setenv(config.CONFIG_FILE_ENV, str(config_file))
            for key in config.REQUIRED_DAILY_JOB_KEYS:
                env_key = config.ENV_MAP.get(key)
                if env_key:
                    patch.delenv(env_key, raising=False)
            config.reload_config()

            with redirect_stdout(stdout):
                assert pm.main(["config", "doctor", "--json"]) == 1
        finally:
            patch.undo()
            config.reload_config()

    out = json.loads(stdout.getvalue())
    assert out["success"] is False
    assert {issue["key"] for issue in out["issues"]} >= set(config.REQUIRED_DAILY_JOB_KEYS)


def test_pm_futu_sync_defaults_to_dry_run_and_passes_safety_flags():
    calls = []

    class FakePortfolioService:
        def sync_futu_holdings(self, **kwargs):
            calls.append(kwargs)
            return {"success": True, **kwargs}

    stdout = io.StringIO()
    with _PortfolioServicePatch(FakePortfolioService), redirect_stdout(stdout):
        assert pm.main(["futu", "sync", "--account", "lx", "--no-service", "--json"]) == 0

    out = json.loads(stdout.getvalue())
    assert out["dry_run"] is True
    assert calls == [{
        "account": "lx",
        "dry_run": True,
        "confirm": False,
        "allow_empty_stock_snapshot": False,
    }]


def test_pm_futu_sync_write_and_empty_override_require_confirm():
    for argv in (
        ["futu", "sync", "--account", "lx", "--write"],
        ["futu", "sync", "--account", "lx", "--allow-empty-stock-snapshot"],
    ):
        try:
            pm.main(argv)
        except SystemExit as exc:
            assert "confirm" in str(exc)
        else:
            raise AssertionError("expected SystemExit")


def test_pm_compensation_list_outputs_folded_tasks():
    import src.app.compensation_service as compensation_module

    class FakeCompensationService:
        def list_tasks(self, include_resolved=False):
            assert include_resolved is False
            return [{"task_id": "repair-1", "status": "PENDING", "supported": True}]

    old = compensation_module.CompensationService
    compensation_module.CompensationService = FakeCompensationService
    stdout = io.StringIO()
    try:
        with redirect_stdout(stdout):
            assert pm.main(["compensation", "list", "--json"]) == 0
    finally:
        compensation_module.CompensationService = old

    out = json.loads(stdout.getvalue())
    assert out["count"] == 1
    assert out["tasks"][0]["task_id"] == "repair-1"


def test_pm_compensation_retry_requires_confirm():
    try:
        pm.main(["compensation", "retry", "--task-id", "repair-1", "--json"])
    except SystemExit as exc:
        assert "--confirm" in str(exc)
    else:
        raise AssertionError("expected SystemExit")


def test_pm_compensation_retry_calls_local_recovery_service():
    class FakeCompensation:
        def retry(self, task_id, confirm=False):
            return {"success": True, "task_id": task_id, "status": "RESOLVED", "confirm": confirm}

    class FakePortfolioService:
        def __init__(self):
            self.portfolio = type("Portfolio", (), {"compensation": FakeCompensation()})()

    stdout = io.StringIO()
    with _PortfolioServicePatch(FakePortfolioService), redirect_stdout(stdout):
        assert pm.main([
            "compensation", "retry", "--task-id", "repair-1", "--confirm", "--json",
        ]) == 0

    out = json.loads(stdout.getvalue())
    assert out == {"success": True, "task_id": "repair-1", "status": "RESOLVED", "confirm": True}
