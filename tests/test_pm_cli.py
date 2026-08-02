from __future__ import annotations

import json
import io
from contextlib import redirect_stdout
from datetime import date
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


def test_pm_holdings_without_subcommand_keeps_list_behavior():
    class FakePortfolioService:
        def get_holdings(self, **kwargs):
            return {
                "success": True,
                "account": kwargs["account"],
                "include_price": kwargs["include_price"],
                "mode": "list",
            }

    stdout = io.StringIO()
    with _PortfolioServicePatch(FakePortfolioService), redirect_stdout(stdout):
        assert pm.main(
            ["holdings", "--account", "alice", "--no-service", "--json"]
        ) == 0

    out = json.loads(stdout.getvalue())
    assert out == {
        "success": True,
        "account": "alice",
        "include_price": False,
        "mode": "list",
    }


def test_pm_holdings_reconcile_uses_fresh_read_only_service():
    import src.app.holdings_reconciliation_service as reconciliation_module

    calls = []

    class FakePortfolioService:
        def __init__(self):
            self.storage = object()

    class FakeReconciliationService:
        def __init__(self, *, storage):
            calls.append(("init", storage))

        def reconcile(self, **kwargs):
            calls.append(("reconcile", kwargs))
            return {
                "success": True,
                "read_only": True,
                "scope": kwargs,
            }

    old_reconciliation = reconciliation_module.HoldingsReconciliationService
    stdout = io.StringIO()
    try:
        reconciliation_module.HoldingsReconciliationService = FakeReconciliationService
        with _PortfolioServicePatch(FakePortfolioService), redirect_stdout(stdout):
            assert pm.main(
                ["holdings", "reconcile", "--account", "alice", "--json"]
            ) == 0
    finally:
        reconciliation_module.HoldingsReconciliationService = old_reconciliation

    out = json.loads(stdout.getvalue())
    assert out["read_only"] is True
    assert out["scope"] == {"account": "alice", "record_id": None}
    assert calls[0][0] == "init"
    assert calls[1] == (
        "reconcile",
        {"account": "alice", "record_id": None},
    )


def test_pm_holdings_apply_requires_exact_record_and_confirmation_before_backend():
    for argv, expected in (
        (["holdings", "reconcile", "--record-id", "rec-1", "--apply"], "requires --confirm"),
        (["holdings", "reconcile", "--apply", "--confirm"], "requires exactly one"),
    ):
        try:
            pm.main(argv)
        except SystemExit as exc:
            assert expected in str(exc)
        else:
            raise AssertionError("expected holdings apply safety rejection")


def test_pm_holdings_event_mutations_require_confirmation_before_backend():
    for argv, expected in (
        (["holdings", "events", "subscribe"], "subscribe requires --confirm"),
        (["holdings", "events", "listen"], "listen requires --confirm"),
    ):
        try:
            pm.main(argv)
        except SystemExit as exc:
            assert expected in str(exc)
        else:
            raise AssertionError("expected holdings event safety rejection")


def test_pm_combined_event_mutations_require_confirmation_before_backend():
    for argv, expected in (
        (["events", "subscribe"], "subscribe requires --confirm"),
        (["events", "listen"], "listen requires --confirm"),
    ):
        try:
            pm.main(argv)
        except SystemExit as exc:
            assert expected in str(exc)
        else:
            raise AssertionError("expected combined event safety rejection")


def test_pm_holdings_event_status_is_local_only_and_does_not_claim_remote_health():
    from src import config as config_module
    import src.app.holdings_event_service as event_module
    import src.app.operation_state_store as store_module
    import src.feishu.holdings_event_adapter as adapter_module

    class Target:
        @classmethod
        def from_config(cls):
            return cls()

        def as_dict(self):
            return {
                "app_id": "cli_data",
                "file_token": "base",
                "table_id": "table",
                "event_type": "drive.file.bitable_record_changed_v1",
            }

    class Store:
        @classmethod
        def inspect_holding_event_status(cls):
            return {"initialized": True, "counts": {}, "latest": None}

    class Adapter:
        @staticmethod
        def sdk_available():
            return True

        def __init__(self, *args, **kwargs):
            raise AssertionError("status must not construct a network adapter")

    patch = MonkeyPatch()
    stdout = io.StringIO()
    config_keys = []

    def configured(key, default=None):
        config_keys.append(key)
        return "secret"

    try:
        patch.setattr(event_module, "HoldingsEventTarget", Target)
        patch.setattr(store_module, "OperationStateStore", Store)
        patch.setattr(adapter_module, "FeishuHoldingsEventAdapter", Adapter)
        patch.setattr(config_module, "get", configured)
        with redirect_stdout(stdout):
            assert pm.main(["holdings", "events", "status", "--json"]) == 0
    finally:
        patch.undo()

    result = json.loads(stdout.getvalue())
    assert result["sdk_available"] is True
    assert result["read_only"] is True
    assert result["remote_subscription_verified"] is False
    assert result["listener_connection_verified"] is False
    assert result["credentials"]["role"] == "bitable"
    assert config_keys == [
        "feishu.bitable.app_id",
        "feishu.bitable.app_secret",
    ]
    assert "no Feishu request" in result["note"]


def test_pm_holdings_event_status_separates_identity_from_target_failure():
    from src import config as config_module
    import src.app.holdings_event_service as event_module
    import src.app.operation_state_store as store_module
    import src.feishu.holdings_event_adapter as adapter_module

    class Target:
        @classmethod
        def from_config(cls):
            raise ValueError("missing holdings table reference")

    class Store:
        @classmethod
        def inspect_holding_event_status(cls):
            return {"initialized": False}

    class Adapter:
        @staticmethod
        def sdk_available():
            return True

    patch = MonkeyPatch()
    stdout = io.StringIO()
    try:
        patch.setattr(event_module, "HoldingsEventTarget", Target)
        patch.setattr(store_module, "OperationStateStore", Store)
        patch.setattr(adapter_module, "FeishuHoldingsEventAdapter", Adapter)
        patch.setattr(config_module, "get", lambda key, default=None: "configured")
        with redirect_stdout(stdout):
            assert pm.main(["holdings", "events", "status", "--json"]) == 1
    finally:
        patch.undo()

    result = json.loads(stdout.getvalue())
    assert result["target_status"] == {
        "valid": False,
        "error": "missing holdings table reference",
    }
    assert result["credentials"] == {
        "role": "bitable",
        "app_id_configured": True,
        "app_secret_configured": True,
        "issues": [],
    }


def test_pm_combined_event_status_is_local_only_and_reports_both_inboxes():
    from src import config as config_module
    import src.app.cash_flow_event_service as cash_event_module
    import src.app.holdings_event_service as holdings_event_module
    import src.app.operation_state_store as store_module
    import src.feishu.bitable_event_adapter as adapter_module

    class HoldingsTarget:
        app_id = "cli_data"
        file_token = "base"
        table_id = "holdings"
        event_type = "drive.file.bitable_record_changed_v1"

        @classmethod
        def from_config(cls):
            return cls()

        def as_dict(self):
            return {"kind": "holdings", "table_id": self.table_id}

    class CashFlowTarget:
        app_id = "cli_data"
        file_token = "base"
        table_id = "cash_flow"
        event_type = "drive.file.bitable_record_changed_v1"

        @classmethod
        def from_config(cls):
            return cls()

        def as_dict(self):
            return {"kind": "cash_flow", "table_id": self.table_id}

    class Store:
        @classmethod
        def inspect_holding_event_status(cls):
            return {"initialized": True, "counts": {"processed": 2}}

        @classmethod
        def inspect_cash_flow_event_status(cls):
            return {"initialized": True, "counts": {"pending": 1}}

    class Adapter:
        @staticmethod
        def sdk_available():
            return True

        def __init__(self, *args, **kwargs):
            raise AssertionError("status must not construct a network adapter")

    patch = MonkeyPatch()
    stdout = io.StringIO()
    config_keys = []

    def configured(key, default=None):
        config_keys.append(key)
        return "configured"

    try:
        patch.setattr(holdings_event_module, "HoldingsEventTarget", HoldingsTarget)
        patch.setattr(cash_event_module, "CashFlowEventTarget", CashFlowTarget)
        patch.setattr(store_module, "OperationStateStore", Store)
        patch.setattr(adapter_module, "FeishuBitableEventAdapter", Adapter)
        patch.setattr(config_module, "get", configured)
        with redirect_stdout(stdout):
            assert pm.main(["events", "status", "--json"]) == 0
    finally:
        patch.undo()

    result = json.loads(stdout.getvalue())
    assert result["success"] is True
    assert result["read_only"] is True
    assert result["target_registry"]["valid"] is True
    assert [
        target["kind"] for target in result["target_registry"]["targets"]
    ] == ["holdings", "cash_flow"]
    assert result["local_inboxes"]["holdings"]["counts"] == {"processed": 2}
    assert result["local_inboxes"]["cash_flow"]["counts"] == {"pending": 1}
    assert result["remote_subscription_verified"] is False
    assert result["listener_connection_verified"] is False
    assert result["credentials"] == {
        "role": "bitable",
        "app_id_configured": True,
        "app_secret_configured": True,
        "issues": [],
    }
    assert config_keys == [
        "feishu.bitable.app_id",
        "feishu.bitable.app_secret",
    ]


def test_pm_combined_event_status_reports_redacted_bitable_credential_issue():
    from src import config as config_module
    import src.app.cash_flow_event_service as cash_event_module
    import src.app.holdings_event_service as holdings_event_module
    import src.app.operation_state_store as store_module
    from src.configuration.feishu_credentials import FeishuCredentialConfigError

    class Target:
        app_id = "cli_data"
        file_token = "base"
        event_type = "drive.file.bitable_record_changed_v1"

        def __init__(self, table_id):
            self.table_id = table_id

        def as_dict(self):
            return {"table_id": self.table_id}

    class HoldingsTarget(Target):
        @classmethod
        def from_config(cls):
            return cls("holdings")

    class CashFlowTarget(Target):
        @classmethod
        def from_config(cls):
            return cls("cash_flow")

    class Store:
        inspect_holding_event_status = classmethod(lambda cls: {"initialized": False})
        inspect_cash_flow_event_status = classmethod(lambda cls: {"initialized": False})

    def configured(key, default=None):
        if key == "feishu.bitable.app_id":
            return "cli_data"
        raise FeishuCredentialConfigError("missing_secure_credential", key)

    patch = MonkeyPatch()
    stdout = io.StringIO()
    try:
        patch.setattr(holdings_event_module, "HoldingsEventTarget", HoldingsTarget)
        patch.setattr(cash_event_module, "CashFlowEventTarget", CashFlowTarget)
        patch.setattr(store_module, "OperationStateStore", Store)
        patch.setattr(config_module, "get", configured)
        with redirect_stdout(stdout):
            assert pm.main(["events", "status", "--json"]) == 1
    finally:
        patch.undo()

    result = json.loads(stdout.getvalue())
    assert result["credentials"] == {
        "role": "bitable",
        "app_id_configured": True,
        "app_secret_configured": False,
        "issues": [
            {
                "key": "feishu.bitable.app_secret",
                "error": "missing_secure_credential",
            }
        ],
    }


def test_pm_combined_event_target_collision_is_reported_and_refuses_mutations():
    from src import config as config_module
    import src.app.cash_flow_event_service as cash_event_module
    import src.app.holdings_event_service as holdings_event_module
    import src.app.operation_state_store as store_module
    import src.feishu.bitable_event_adapter as adapter_module

    class CollidingTarget:
        app_id = "cli_data"
        file_token = "base"
        table_id = "same_table"
        event_type = "drive.file.bitable_record_changed_v1"

        @classmethod
        def from_config(cls):
            return cls()

        def as_dict(self):
            return {"table_id": self.table_id}

    class Store:
        @classmethod
        def inspect_holding_event_status(cls):
            return {"initialized": False}

        @classmethod
        def inspect_cash_flow_event_status(cls):
            return {"initialized": False}

    class Adapter:
        @staticmethod
        def sdk_available():
            return True

        def __init__(self, *args, **kwargs):
            raise AssertionError("collision must be rejected before adapter creation")

    patch = MonkeyPatch()
    stdout = io.StringIO()
    try:
        patch.setattr(holdings_event_module, "HoldingsEventTarget", CollidingTarget)
        patch.setattr(cash_event_module, "CashFlowEventTarget", CollidingTarget)
        patch.setattr(store_module, "OperationStateStore", Store)
        patch.setattr(adapter_module, "FeishuBitableEventAdapter", Adapter)
        patch.setattr(config_module, "get", lambda key, default=None: "configured")
        with redirect_stdout(stdout):
            assert pm.main(["events", "status", "--json"]) == 1
        for argv in (
            ["events", "subscribe", "--confirm"],
            ["events", "listen", "--confirm"],
        ):
            try:
                pm.main(argv)
            except ValueError as exc:
                assert "target collision" in str(exc)
            else:
                raise AssertionError("expected target collision rejection")
    finally:
        patch.undo()

    result = json.loads(stdout.getvalue())
    assert result["success"] is False
    assert result["target_registry"]["valid"] is False
    assert "target collision" in result["target_registry"]["error"]


def test_pm_combined_listener_fans_out_and_joins_both_workers():
    import src.app.cash_flow_event_completion_service as completion_module
    import src.app.cash_flow_event_inbox_service as cash_inbox_module
    import src.app.cash_flow_event_service as cash_event_module
    import src.app.holding_event_inbox_service as holdings_inbox_module
    import src.app.holdings_event_service as holdings_event_module
    import src.app.operation_state_store as store_module
    import src.feishu.bitable_event_adapter as adapter_module

    calls = []

    class HoldingsTarget:
        app_id = "cli_data"
        file_token = "base"
        table_id = "holdings"
        event_type = "drive.file.bitable_record_changed_v1"

        @classmethod
        def from_config(cls):
            return cls()

    class CashFlowTarget:
        app_id = "cli_data"
        file_token = "base"
        table_id = "cash_flow"
        event_type = "drive.file.bitable_record_changed_v1"

        @classmethod
        def from_config(cls):
            return cls()

    class Store:
        pass

    class PortfolioService:
        def __init__(self):
            self.storage = "shared-storage"

    class Completion:
        def __init__(self, *, storage, operation_store):
            calls.append(("completion", storage, operation_store))

        def __call__(self, **_kwargs):
            return {"status": "complete"}

        def terminal_failure_receipts(self, **_kwargs):
            return []

    class _Inbox:
        table_id = ""

        def __init__(self, *, storage=None, store, target, **kwargs):
            self.store = store
            self.target = target
            calls.append(
                (
                    "inbox",
                    self.table_id,
                    storage,
                    store,
                    sorted(kwargs),
                )
            )

        def accept(self, payload):
            accepted = payload["table_id"] == self.table_id
            calls.append(("accept", self.table_id, payload["table_id"], accepted))
            return {
                "success": True,
                "accepted": accepted,
                "filtered": not accepted,
            }

        def run_worker_loop(self, **_kwargs):
            raise AssertionError("fake thread must not invoke the worker target")

    class HoldingsInbox(_Inbox):
        table_id = "holdings"

    class CashFlowInbox(_Inbox):
        table_id = "cash_flow"

    class Adapter:
        def __init__(self, *, targets):
            calls.append(("adapter", [target.table_id for target in targets]))

        def start(self, callback):
            for table_id in ("holdings", "cash_flow", "unknown"):
                outcome = callback({"table_id": table_id})
                calls.append(("fanout", table_id, outcome["accepted_by"]))

    class Thread:
        def __init__(self, *, target, kwargs, name, daemon):
            self.name = name
            calls.append(("thread_init", name, daemon, sorted(kwargs)))

        def start(self):
            calls.append(("thread_start", self.name))

        def join(self, *, timeout):
            calls.append(("thread_join", self.name, timeout))

    patch = MonkeyPatch()
    stdout = io.StringIO()
    try:
        patch.setattr(holdings_event_module, "HoldingsEventTarget", HoldingsTarget)
        patch.setattr(cash_event_module, "CashFlowEventTarget", CashFlowTarget)
        patch.setattr(store_module, "OperationStateStore", Store)
        patch.setattr(completion_module, "CashFlowEventCompletionService", Completion)
        patch.setattr(holdings_inbox_module, "HoldingEventInboxService", HoldingsInbox)
        patch.setattr(cash_inbox_module, "CashFlowEventInboxService", CashFlowInbox)
        patch.setattr(adapter_module, "FeishuBitableEventAdapter", Adapter)
        patch.setattr(pm.threading, "Thread", Thread)
        with _PortfolioServicePatch(PortfolioService), redirect_stdout(stdout):
            assert pm.main(["events", "listen", "--confirm", "--json"]) == 0
    finally:
        patch.undo()

    assert json.loads(stdout.getvalue()) == {"success": True, "status": "stopped"}
    assert ("adapter", ["holdings", "cash_flow"]) in calls
    assert ("fanout", "holdings", ["holdings"]) in calls
    assert ("fanout", "cash_flow", ["cash_flow"]) in calls
    assert ("fanout", "unknown", []) in calls
    assert [item[1] for item in calls if item[0] == "thread_start"] == [
        "holdings-event-worker",
        "cash-flow-event-worker",
    ]
    assert [item[1] for item in calls if item[0] == "thread_join"] == [
        "holdings-event-worker",
        "cash-flow-event-worker",
    ]
    stores = [item[3] for item in calls if item[0] == "inbox"]
    assert len(stores) == 2 and stores[0] is stores[1]


def test_pm_receipts_dispatch_attempts_typed_branch_when_nav_branch_raises():
    import src.app.nav_receipt_outbox_service as nav_module
    import src.app.operation_receipt_outbox_service as operation_module

    calls = []

    class FailingNav:
        def dispatch_pending(self, **kwargs):
            calls.append(("nav", kwargs))
            raise RuntimeError("nav unavailable")

    class Typed:
        def dispatch_pending(self, **kwargs):
            calls.append(("operations", kwargs))
            return {"success": True, "attempted": 1, "sent": 1}

    old_nav = nav_module.NavReceiptOutboxService
    old_operation = operation_module.OperationReceiptOutboxService
    stdout = io.StringIO()
    try:
        nav_module.NavReceiptOutboxService = FailingNav
        operation_module.OperationReceiptOutboxService = Typed
        with redirect_stdout(stdout):
            assert pm.main(["receipts", "dispatch", "--confirm", "--json"]) == 1
    finally:
        nav_module.NavReceiptOutboxService = old_nav
        operation_module.OperationReceiptOutboxService = old_operation

    result = json.loads(stdout.getvalue())
    assert result["branches"]["nav"]["success"] is False
    assert result["branches"]["operations"]["sent"] == 1
    assert [item[0] for item in calls] == ["nav", "operations"]


def test_pm_quality_status_uses_same_published_application_payload():
    expected = {
        "schema_version": "investment.quality_status.v1",
        "producer": {"service": "portfolio-management"},
        "datasets": [],
    }

    class FakePortfolioService:
        def quality_status(self):
            return expected

    stdout = io.StringIO()
    with _PortfolioServicePatch(FakePortfolioService), redirect_stdout(stdout):
        assert pm.main(["quality", "status", "--no-service", "--json"]) == 0

    assert json.loads(stdout.getvalue()) == expected


def test_pm_quality_refresh_publishes_for_explicit_accounts():
    class FakePortfolioService:
        def refresh_quality_status(self, **kwargs):
            return {
                "schema_version": "investment.quality_status.v1",
                "accounts": kwargs["accounts"],
            }

    stdout = io.StringIO()
    with _PortfolioServicePatch(FakePortfolioService), redirect_stdout(stdout):
        assert pm.main(["quality", "refresh", "--accounts", "LX,sy", "--json"]) == 0

    assert json.loads(stdout.getvalue())["accounts"] == ["lx", "sy"]


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
            "overwrite_existing": False,
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
            "overwrite_existing": False,
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
    assert out["values"]["feishu.app_secret"]["source"] == f"legacy-file:{config_file}"


def test_pm_config_inspect_never_discloses_feishu_secret_with_show_secrets():
    from src import config

    with TemporaryDirectory() as tmp:
        credential_dir = Path(tmp) / "credentials"
        credential_dir.mkdir()
        config_file = Path(tmp) / "config.yaml"
        config_file.write_text("{}\n", encoding="utf-8")
        secret = "never-return-this-feishu-secret"
        (credential_dir / config.BITABLE_APP_SECRET_CREDENTIAL).write_text(
            secret,
            encoding="utf-8",
        )
        conversation_secret = "never-return-conversation-secret"
        (credential_dir / config.CONVERSATION_APP_SECRET_CREDENTIAL).write_text(
            conversation_secret,
            encoding="utf-8",
        )
        patch = MonkeyPatch()
        stdout = io.StringIO()
        try:
            patch.setenv(config.CONFIG_FILE_ENV, str(config_file))
            patch.setenv("CREDENTIALS_DIRECTORY", str(credential_dir))
            patch.setenv("FEISHU_BITABLE_APP_ID", "cli_bitable_private")
            patch.setenv("FEISHU_CONVERSATION_APP_ID", "cli_conversation_private")
            patch.setenv("FEISHU_CONVERSATION_OPEN_ID", "ou_private_user")
            config.reload_config()
            with redirect_stdout(stdout):
                assert pm.main(
                    [
                        "config",
                        "inspect",
                        "--keys",
                        "feishu.bitable.app_id,feishu.bitable.app_secret,"
                        "feishu.conversation.app_id,"
                        "feishu.conversation.app_secret,"
                        "feishu.conversation.open_id",
                        "--show-secrets",
                        "--json",
                    ]
                ) == 0
        finally:
            patch.undo()
            config.reload_config()

        encoded = stdout.getvalue()
        out = json.loads(encoded)
        assert secret not in encoded
        assert conversation_secret not in encoded
        assert "cli_bitable_private" not in encoded
        assert "cli_conversation_private" not in encoded
        assert "ou_private_user" not in encoded
        assert out["values"]["feishu.bitable.app_id"]["value"] == "cli...ate"
        assert out["values"]["feishu.bitable.app_secret"]["value"] == "nev...ret"
        assert out["values"]["feishu.conversation.app_id"]["value"] == "cli...ate"
        assert out["values"]["feishu.conversation.app_secret"]["value"] == "nev...ret"
        assert out["values"]["feishu.conversation.open_id"]["value"] == "ou_...ser"


def test_pm_config_doctor_rejects_invalid_secure_mode_without_secret_leak():
    stdout = io.StringIO()
    patch = MonkeyPatch()
    try:
        patch.setenv("PM_REQUIRE_SECURE_FEISHU_CREDENTIALS", "treu")
        patch.setenv("FEISHU_APP_SECRET", "doctor-plaintext-must-not-leak")
        patch.delenv("CREDENTIALS_DIRECTORY", raising=False)
        with redirect_stdout(stdout):
            assert pm.main(["config", "doctor", "--json"]) == 1
    finally:
        patch.undo()

    encoded = stdout.getvalue()
    out = json.loads(encoded)
    assert "doctor-plaintext-must-not-leak" not in encoded
    assert {
        (issue["key"], issue["error"])
        for issue in out["issues"]
    } >= {("feishu.credentials.secure_mode", "invalid_secure_mode")}
    assert out["secure_feishu_required"] is True
    assert out["secure_feishu_mode_valid"] is False


def test_pm_config_doctor_can_require_both_secure_feishu_roles():
    from src import config

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        config_file = root / "config.yaml"
        config_file.write_text(
            """
feishu:
  app_token: appToken
  bitable:
    app_id: cli_bitable
  conversation:
    app_id: cli_conversation
    open_id: ou_user
  tables:
    holdings: appToken/tbl_holdings
    nav_history: appToken/tbl_nav
    cash_flow: appToken/tbl_cash
    holdings_snapshot: appToken/tbl_snapshot
""",
            encoding="utf-8",
        )
        credential_dir = root / "credentials"
        credential_dir.mkdir()
        (credential_dir / config.BITABLE_APP_SECRET_CREDENTIAL).write_text(
            "bitable-secret",
            encoding="utf-8",
        )
        (credential_dir / config.CONVERSATION_APP_SECRET_CREDENTIAL).write_text(
            "conversation-secret",
            encoding="utf-8",
        )
        patch = MonkeyPatch()
        stdout = io.StringIO()
        try:
            patch.setenv(config.CONFIG_FILE_ENV, str(config_file))
            patch.setenv("CREDENTIALS_DIRECTORY", str(credential_dir))
            config.reload_config()
            with redirect_stdout(stdout):
                assert pm.main(
                    ["config", "doctor", "--require-secure-feishu", "--json"]
                ) == 0
        finally:
            patch.undo()
            config.reload_config()

    out = json.loads(stdout.getvalue())
    encoded = json.dumps(out, ensure_ascii=False)
    assert out["success"] is True
    assert out["secure_feishu_required"] is True
    assert "bitable-secret" not in encoded
    assert "conversation-secret" not in encoded


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


def test_pm_futu_accounts_is_direct_read_only_and_requires_explicit_market(
    monkeypatch,
):
    calls = []

    class FakeProvider:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def discover_accounts(self):
            return {
                "success": True,
                "read_only": True,
                "accounts": [{"acc_id": 123, "trd_env": "REAL"}],
            }

    import src.app as app_module

    monkeypatch.setattr(app_module, "FutuOpenApiBalanceProvider", FakeProvider)
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        assert pm.main(["futu", "accounts", "--market", "US", "--json"]) == 0

    assert json.loads(stdout.getvalue())["read_only"] is True
    assert calls == [{"trd_market": "US", "verify_account": False}]

    try:
        pm.main(["futu", "accounts", "--json"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("expected --market to be required")


def test_pm_futu_accounts_returns_safe_error_without_upstream_details(monkeypatch):
    class FailingProvider:
        def __init__(self, **kwargs):
            pass

        def discover_accounts(self):
            raise RuntimeError("sensitive upstream detail")

    import src.app as app_module

    monkeypatch.setattr(app_module, "FutuOpenApiBalanceProvider", FailingProvider)
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        assert pm.main(["futu", "accounts", "--market", "US", "--json"]) == 1

    output = stdout.getvalue()
    assert "FUTU_ACCOUNT_DISCOVERY_FAILED" in output
    assert "sensitive upstream detail" not in output


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



def test_pm_manual_nav_commands_default_to_no_overwrite_and_require_explicit_opt_in():
    parser = pm.build_parser()

    nav_default = parser.parse_args(['nav', 'record'])
    daily_default = parser.parse_args(['daily'])
    nav_overwrite = parser.parse_args(['nav', 'record', '--overwrite'])
    daily_overwrite = parser.parse_args(['daily', '--overwrite'])
    legacy_no_overwrite = parser.parse_args(['nav', 'record', '--no-overwrite'])

    assert nav_default.overwrite is False
    assert daily_default.overwrite is False
    assert nav_overwrite.overwrite is True
    assert daily_overwrite.overwrite is True
    assert legacy_no_overwrite.overwrite is False


def test_pm_cash_flow_duplicates_is_fresh_read_only_audit():
    import src.feishu_storage as storage_module

    calls = []

    class FakeCashFlowRepository:
        def audit_cash_flow_duplicates(self, *, account=None):
            calls.append(("audit", account))
            return {
                "success": True,
                "read_only": True,
                "account": account,
                "duplicate_group_count": 1,
                "duplicate_groups": [{"record_ids": ["cf_1", "cf_2"]}],
            }

    class FakeStorage:
        def __init__(self):
            self.cash_flow = FakeCashFlowRepository()

    old_storage = storage_module.FeishuStorage
    stdout = io.StringIO()
    try:
        storage_module.FeishuStorage = FakeStorage
        with redirect_stdout(stdout):
            assert pm.main([
                "cash-flow",
                "duplicates",
                "--account",
                "lx",
                "--json",
            ]) == 0
    finally:
        storage_module.FeishuStorage = old_storage

    result = json.loads(stdout.getvalue())
    assert result["read_only"] is True
    assert result["duplicate_groups"][0]["record_ids"] == ["cf_1", "cf_2"]
    assert calls == [("audit", "lx")]


def test_pm_manual_fx_date_mismatch_records_zero_confirmation():
    import src.app.operation_state_store as state_module
    import src.feishu_storage as storage_module

    confirmation_calls = []
    reconcile_calls = []

    class FakeStorage:
        def reconcile_cash_flows(self, **kwargs):
            reconcile_calls.append(kwargs)
            return {
                "success": False,
                "reason_code": "cash_flow_readback_not_verified",
                "partial_write_possible": False,
                "rows": [{
                    "record_id": "cf_usd",
                    "status": "error",
                    "error": "exchange_rate_date must equal cash_flow flow_date",
                }],
            }

    class FakeOperationStore:
        def record_fx_confirmation(self, **kwargs):
            confirmation_calls.append(kwargs)
            return "should-not-exist"

    old_storage = storage_module.FeishuStorage
    old_store = state_module.OperationStateStore
    stdout = io.StringIO()
    try:
        storage_module.FeishuStorage = FakeStorage
        state_module.OperationStateStore = FakeOperationStore
        with redirect_stdout(stdout):
            assert pm.main([
                "cash-flow",
                "reconcile",
                "--record-id",
                "cf_usd",
                "--exchange-rate",
                "7.2",
                "--rate-date",
                "2026-07-25",
                "--rate-source",
                "bank:receipt-1",
                "--apply",
                "--confirm",
                "--json",
            ]) == 1
    finally:
        storage_module.FeishuStorage = old_storage
        state_module.OperationStateStore = old_store

    assert reconcile_calls[0]["rate_date"] == date(2026, 7, 25)
    assert confirmation_calls == []
    result = json.loads(stdout.getvalue())
    assert result["reason_code"] == "cash_flow_readback_not_verified"
    assert result["partial_write_possible"] is False


def test_pm_placeholder_fx_source_records_zero_confirmation():
    import src.app.operation_state_store as state_module
    import src.feishu_storage as storage_module

    confirmation_calls = []

    class FakeStorage:
        def reconcile_cash_flows(self, **_kwargs):
            raise ValueError("exchange_rate_source must be traceable text")

    class FakeOperationStore:
        def record_fx_confirmation(self, **kwargs):
            confirmation_calls.append(kwargs)
            return "should-not-exist"

    old_storage = storage_module.FeishuStorage
    old_store = state_module.OperationStateStore
    try:
        storage_module.FeishuStorage = FakeStorage
        state_module.OperationStateStore = FakeOperationStore
        try:
            pm.main([
                "cash-flow",
                "reconcile",
                "--record-id",
                "cf_usd",
                "--exchange-rate",
                "7.2",
                "--rate-date",
                "2026-07-26",
                "--rate-source",
                "unknown",
                "--apply",
                "--confirm",
                "--json",
            ])
        except SystemExit as exc:
            assert "traceable text" in str(exc)
        else:
            raise AssertionError("expected placeholder source rejection")
    finally:
        storage_module.FeishuStorage = old_storage
        state_module.OperationStateStore = old_store

    assert confirmation_calls == []


def test_pm_manual_fx_confirmation_binds_observed_generated_fingerprint():
    import src.app.operation_state_store as state_module
    import src.feishu_storage as storage_module

    confirmation_calls = []

    class FakeStorage:
        def reconcile_cash_flows(self, **_kwargs):
            return {
                "success": True,
                "rows": [{
                    "record_id": "cf_usd",
                    "status": "ok",
                    "completion_state": "completed",
                    "readback_verified": True,
                    "generated_fingerprint": "generated-fingerprint-1",
                    "exchange_rate": 7.2,
                    "cny_amount": 720.0,
                    "fx_evidence": {
                        "exchange_rate_date": "2026-07-26",
                        "exchange_rate_source": "bank:receipt-1",
                        "exchange_rate_evidence_type": "manual_supplement",
                    },
                }],
            }

    class FakeOperationStore:
        def record_fx_confirmation(self, **kwargs):
            confirmation_calls.append(kwargs)
            return "confirmation-1"

    old_storage = storage_module.FeishuStorage
    old_store = state_module.OperationStateStore
    stdout = io.StringIO()
    try:
        storage_module.FeishuStorage = FakeStorage
        state_module.OperationStateStore = FakeOperationStore
        with redirect_stdout(stdout):
            assert pm.main([
                "cash-flow",
                "reconcile",
                "--record-id",
                "cf_usd",
                "--exchange-rate",
                "7.2",
                "--rate-date",
                "2026-07-26",
                "--rate-source",
                "bank:receipt-1",
                "--apply",
                "--confirm",
                "--json",
            ]) == 0
    finally:
        storage_module.FeishuStorage = old_storage
        state_module.OperationStateStore = old_store

    result = json.loads(stdout.getvalue())
    assert result["fx_confirmation_id"] == "confirmation-1"
    assert confirmation_calls[0]["record_id"] == "cf_usd"
    assert confirmation_calls[0]["source_hash"] == "generated-fingerprint-1"
