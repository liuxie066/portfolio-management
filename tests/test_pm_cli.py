from __future__ import annotations

import json
import io
import sys
import types
from contextlib import redirect_stdout

from scripts import pm


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


def test_pm_report_requires_preview_flag():
    try:
        pm.main(["report", "daily", "--json"])
    except SystemExit as exc:
        assert "preview-only" in str(exc)
    else:
        raise AssertionError("expected SystemExit")


def test_pm_report_preview_marks_noncanonical_output():
    fake_skill_api = types.SimpleNamespace(
        generate_report=lambda **kwargs: {
            "success": True,
            "report_type": kwargs["report_type"],
            "account": kwargs["account"],
        }
    )
    stdout = io.StringIO()
    with _SysModulesPatch("skill_api", fake_skill_api), redirect_stdout(stdout):
        assert pm.main(["report", "daily", "--preview", "--account", "alice", "--no-service", "--json"]) == 0

    out = json.loads(stdout.getvalue())
    assert out["success"] is True
    assert out["report_type"] == "daily"
    assert out["account"] == "alice"
    assert out["preview_only"] is True
    assert out["canonical_entrypoint"] == "scripts/publish_daily_report.py"


def test_pm_cash_passes_account():
    fake_skill_api = types.SimpleNamespace(
        get_cash=lambda **kwargs: {
            "success": True,
            "account": kwargs["account"],
        }
    )
    stdout = io.StringIO()
    with _SysModulesPatch("skill_api", fake_skill_api), redirect_stdout(stdout):
        assert pm.main(["cash", "--account", "bob", "--no-service", "--json"]) == 0

    out = json.loads(stdout.getvalue())
    assert out["success"] is True
    assert out["account"] == "bob"


def test_pm_json_suppresses_internal_stdout_by_default():
    def noisy_get_cash(**kwargs):
        print("internal log")
        return {"success": True, "account": kwargs["account"]}

    fake_skill_api = types.SimpleNamespace(get_cash=noisy_get_cash)
    stdout = io.StringIO()
    with _SysModulesPatch("skill_api", fake_skill_api), redirect_stdout(stdout):
        assert pm.main(["cash", "--account", "bob", "--no-service", "--json"]) == 0

    out = json.loads(stdout.getvalue())
    assert out["success"] is True
    assert out["account"] == "bob"
    assert "internal log" not in stdout.getvalue()


def test_pm_failure_payload_returns_nonzero_exit_code():
    fake_skill_api = types.SimpleNamespace(
        get_distribution=lambda **_kwargs: {
            "success": False,
            "error": "missing holdings table",
        }
    )
    stdout = io.StringIO()
    with _SysModulesPatch("skill_api", fake_skill_api), redirect_stdout(stdout):
        assert pm.main(["positions", "distribution", "--account", "bob", "--no-service", "--json"]) == 1

    out = json.loads(stdout.getvalue())
    assert out["success"] is False
    assert out["error"] == "missing holdings table"


def test_pm_accounts_lists_discovered_accounts():
    fake_skill_api = types.SimpleNamespace(
        list_accounts=lambda **kwargs: {
            "success": True,
            "include_default": kwargs["include_default"],
            "accounts": ["alice"],
        }
    )
    stdout = io.StringIO()
    with _SysModulesPatch("skill_api", fake_skill_api), redirect_stdout(stdout):
        assert pm.main(["accounts", "--exclude-default", "--no-service", "--json"]) == 0

    out = json.loads(stdout.getvalue())
    assert out["success"] is True
    assert out["include_default"] is False
    assert out["accounts"] == ["alice"]


def test_pm_overview_passes_accounts_and_timeout():
    fake_skill_api = types.SimpleNamespace(
        multi_account_overview=lambda **kwargs: {
            "success": True,
            "accounts": kwargs["accounts"],
            "price_timeout": kwargs["price_timeout"],
            "include_details": kwargs["include_details"],
        }
    )
    stdout = io.StringIO()
    with _SysModulesPatch("skill_api", fake_skill_api), redirect_stdout(stdout):
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

    fake_skill_api = types.SimpleNamespace(
        get_cash=lambda **_kwargs: calls.append(("fallback", None))
    )
    old_client = client_module.PortfolioServiceClient
    try:
        client_module.PortfolioServiceClient = FakeClient
        with _SysModulesPatch("skill_api", fake_skill_api):
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

    fake_skill_api = types.SimpleNamespace(
        get_cash=lambda **_kwargs: calls.append(("fallback", None))
    )
    old_client = client_module.PortfolioServiceClient
    try:
        client_module.PortfolioServiceClient = FakeClient
        with _SysModulesPatch("skill_api", fake_skill_api):
            try:
                pm.main(["cash", "--account", "bob", "--service-url", "http://local", "--require-service", "--json"])
            except SystemExit as exc:
                assert "--require-service" in str(exc)
            else:
                raise AssertionError("expected SystemExit")
    finally:
        client_module.PortfolioServiceClient = old_client

    assert calls == [("init", "http://local", 0.5), ("get_cash", "bob")]


def test_pm_init_nav_passes_account_and_write_flags():
    fake_skill_api = types.SimpleNamespace(
        init_nav_history=lambda **kwargs: {
            "success": True,
            "account": kwargs["account"],
            "date": kwargs["date_str"],
            "dry_run": kwargs["dry_run"],
            "confirm": kwargs["confirm"],
        }
    )
    stdout = io.StringIO()
    with _SysModulesPatch("skill_api", fake_skill_api), redirect_stdout(stdout):
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
    fake_skill_api = types.SimpleNamespace(
        record_nav=lambda **kwargs: {
            "success": True,
            "account": kwargs["account"],
            "dry_run": kwargs["dry_run"],
            "confirm": kwargs["confirm"],
            "overwrite_existing": kwargs["overwrite_existing"],
            "use_bulk_persist": kwargs["use_bulk_persist"],
            "price_timeout": kwargs["price_timeout"],
        }
    )
    stdout = io.StringIO()
    with _SysModulesPatch("skill_api", fake_skill_api), redirect_stdout(stdout):
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
    calls = []
    snapshot = {"valuation": object(), "holdings_data": {"total_value": 123.45}}
    fake_skill = types.SimpleNamespace(
        build_snapshot=lambda **kwargs: calls.append(("build_snapshot", kwargs)) or snapshot,
        record_nav=lambda **kwargs: calls.append(("record_nav", kwargs)) or {
            "success": True,
            "date": "2026-05-23",
            "nav": 1.2345,
            "shares": 100,
            "total_value": 123.45,
            "dry_run": kwargs["dry_run"],
        },
        get_distribution=lambda **kwargs: calls.append(("get_distribution", kwargs)) or {
            "success": True,
            "total_value": 123.45,
            "by_type": [{"type": "stock", "value": 100, "ratio": 0.81}],
        },
    )
    fake_skill_api = types.SimpleNamespace(
        get_skill=lambda account=None: calls.append(("get_skill", account)) or fake_skill,
    )
    stdout = io.StringIO()
    with _SysModulesPatch("skill_api", fake_skill_api), redirect_stdout(stdout):
        assert pm.main(["daily", "--account", "alice", "--timeout", "8", "--no-service", "--json"]) == 0

    out = json.loads(stdout.getvalue())
    assert out["success"] is True
    assert out["command"] == "daily"
    assert out["account"] == "alice"
    assert out["dry_run"] is True
    assert out["nav"]["nav"] == 1.2345
    assert out["distribution"]["by_type"][0]["type"] == "stock"
    assert calls == [
        ("get_skill", "alice"),
        ("build_snapshot", {"price_timeout_seconds": 8}),
        ("record_nav", {
            "price_timeout": 8,
            "dry_run": True,
            "confirm": False,
            "overwrite_existing": True,
            "use_bulk_persist": False,
            "snapshot": snapshot,
            "run_id": None,
        }),
        ("get_distribution", {"holdings_data": snapshot}),
    ]


def test_pm_daily_write_requires_confirm():
    try:
        pm.main(["daily", "--account", "hb", "--write"])
    except SystemExit as exc:
        assert "--confirm" in str(exc)
    else:
        raise AssertionError("expected SystemExit")


def test_pm_daily_failure_payload_returns_nonzero_exit_code():
    snapshot = {"valuation": object(), "holdings_data": {}}
    fake_skill_api = types.SimpleNamespace(
        get_skill=lambda account=None: types.SimpleNamespace(
            build_snapshot=lambda **_kwargs: snapshot,
            record_nav=lambda **_kwargs: {"success": False, "error": "nav failed"},
            get_distribution=lambda **_kwargs: (_ for _ in ()).throw(AssertionError("distribution should not run")),
        ),
    )
    stdout = io.StringIO()
    with _SysModulesPatch("skill_api", fake_skill_api), redirect_stdout(stdout):
        assert pm.main(["daily", "--account", "alice", "--no-service", "--json"]) == 1

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

        def get_distribution(self, *, account):
            calls.append(("get_distribution", account))
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
    assert calls == [("init", "http://local", 0.5), ("get_distribution", "bob")]


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
