from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from src.app.account_nav_recorder_service import AccountNavRecorderService
from src.app.business_calendar_service import BusinessCalendarService
from src.app.daily_account_nav_service import DailyAccountNavService
from src.app.daily_nav_job_service import DailyNavJobService
from src.app.daily_report_payload_service import DailyReportPayloadService
from src.app.nav_initialization_service import NavInitializationService


def _nav_record(account: str = "alice", nav_date: date = date(2026, 5, 22)):
    return SimpleNamespace(
        record_id="rec_nav_1",
        date=nav_date,
        account=account,
        total_value=123.45,
        cash_value=23.45,
        stock_value=100.0,
        fund_value=0.0,
        cash_flow=0.0,
        share_change=0.0,
        pnl=1.23,
        shares=100.0,
        nav=1.2345,
        mtd_nav_change=0.01,
        ytd_nav_change=0.02,
        mtd_pnl=1.0,
        ytd_pnl=2.0,
        details={},
    )


def test_business_calendar_skips_weekend_and_configured_holiday():
    calendar = BusinessCalendarService(holidays=["2026-05-22"])

    assert calendar.default_nav_date(run_date="2026-05-25").isoformat() == "2026-05-21"
    assert calendar.default_nav_date(run_date="2026-05-24").isoformat() == "2026-05-21"
    assert calendar.previous_business_day(before="2026-05-23").isoformat() == "2026-05-21"
    assert calendar.explain("2026-05-23") == {
        "business_day": False,
        "reason": "weekend",
        "date": "2026-05-23",
    }
    assert calendar.explain("2026-05-22") == {
        "business_day": False,
        "reason": "holiday",
        "date": "2026-05-22",
    }
    assert calendar.is_business_day("2026-05-21") is True


def test_business_calendar_weekend_timer_run_records_friday():
    calendar = BusinessCalendarService()

    assert calendar.default_nav_date(run_date="2026-05-30").isoformat() == "2026-05-29"
    assert calendar.default_nav_date(run_date="2026-05-31").isoformat() == "2026-05-29"
    assert calendar.default_nav_date(run_date="2026-06-01").isoformat() == "2026-05-29"


def test_daily_account_nav_service_reuses_one_snapshot_and_respects_nav_date():
    calls = []
    valuation = SimpleNamespace(warnings=[])
    snapshot = {"valuation": valuation, "snapshot_time": "2026-05-22T18:00:00"}
    nav_record = _nav_record()

    class FakeReadService:
        def build_snapshot(self, **kwargs):
            calls.append(("build_snapshot", kwargs))
            return snapshot

        def get_distribution(self, **kwargs):
            calls.append(("get_distribution", kwargs.get("holdings_data") is snapshot))
            return {"success": True, "total_value": 123.45}

    class FakePortfolio:
        reporting_service = object()

        def record_nav(self, *args, **kwargs):
            calls.append(("record_nav", args, kwargs))
            return nav_record

    class FakeStorage:
        def get_nav_history(self, account, days):
            calls.append(("get_nav_history", account, days))
            return []

    result = DailyAccountNavService(
        account="alice",
        storage=FakeStorage(),
        portfolio=FakePortfolio(),
        read_service=FakeReadService(),
    ).run(
        nav_date="2026-05-22",
        price_timeout=7,
        dry_run=False,
        confirm=True,
        overwrite_existing=False,
        use_bulk_persist=True,
        run_id="run-daily-1",
    )

    assert result["success"] is True
    assert result["date"] == "2026-05-22"
    assert result["nav_result"]["nav"] == 1.2345
    assert calls[0] == ("build_snapshot", {"price_timeout_seconds": 7})
    record_call = next(call for call in calls if call[0] == "record_nav")
    assert record_call[1] == ("alice",)
    assert record_call[2]["valuation"] is valuation
    assert record_call[2]["nav_date"] == date(2026, 5, 22)
    assert record_call[2]["dry_run"] is False
    assert record_call[2]["overwrite_existing"] is False
    assert ("get_distribution", True) in calls
    assert result["report"]["date"] == "2026-05-22"


def test_account_nav_recorder_records_nav_without_report_reads():
    calls = []
    valuation = SimpleNamespace(warnings=["price warning"])
    snapshot = {"valuation": valuation, "snapshot_time": "2026-05-22T18:00:00"}
    nav_record = _nav_record()

    class FakeReadService:
        def build_snapshot(self, **kwargs):
            calls.append(("build_snapshot", kwargs))
            return snapshot

        def get_distribution(self, **_kwargs):
            raise AssertionError("recorder should not build report distribution")

    class FakePortfolio:
        def record_nav(self, *args, **kwargs):
            calls.append(("record_nav", args, kwargs))
            return nav_record

    class FakeStorage:
        def get_nav_history(self, *_args, **_kwargs):
            raise AssertionError("recorder should not read nav history")

    result = AccountNavRecorderService(
        account="alice",
        storage=FakeStorage(),
        portfolio=FakePortfolio(),
        read_service=FakeReadService(),
    ).record(
        nav_date="2026-05-22",
        price_timeout=7,
        dry_run=False,
        confirm=True,
        overwrite_existing=False,
        use_bulk_persist=True,
        run_id="run-recorder-1",
    )

    assert result["success"] is True
    assert result["snapshot"] is snapshot
    assert result["nav_record"] is nav_record
    assert result["nav_result"]["warnings"] == ["price warning"]
    assert calls[0] == ("build_snapshot", {"price_timeout_seconds": 7})
    record_call = calls[1]
    assert record_call[0] == "record_nav"
    assert record_call[1] == ("alice",)
    assert record_call[2]["valuation"] is valuation
    assert record_call[2]["nav_date"] == date(2026, 5, 22)
    assert record_call[2]["dry_run"] is False
    assert record_call[2]["overwrite_existing"] is False
    assert record_call[2]["use_bulk_persist"] is True
    assert result["stage_timings"].keys() == {"snapshot_ms", "record_nav_ms"}


def test_nav_initialization_service_initializes_empty_account():
    calls = []
    valuation = SimpleNamespace(
        total_value_cny=1000.0,
        warnings=[],
    )
    snapshot = {"valuation": valuation, "snapshot_time": "2026-05-22T18:00:00"}
    nav_record = SimpleNamespace(
        nav=1.0,
        shares=1000.0,
        total_value=1000.0,
        cash_value=100.0,
        stock_value=900.0,
        fund_value=0.0,
        details={},
    )

    class FakeStorage:
        def get_nav_history(self, account, days):
            calls.append(("get_nav_history", account, days))
            return []

    class FakeReadService:
        def build_snapshot(self, **kwargs):
            calls.append(("build_snapshot", kwargs))
            return snapshot

    class FakePortfolio:
        def record_nav(self, *args, **kwargs):
            calls.append(("record_nav", args, kwargs))
            return nav_record

    result = NavInitializationService(
        account="alice",
        storage=FakeStorage(),
        portfolio=FakePortfolio(),
        read_service=FakeReadService(),
    ).init_nav_history(
        date_str="2026-05-22",
        price_timeout=7,
        dry_run=False,
        confirm=True,
        use_bulk_persist=True,
    )

    assert result["success"] is True
    assert result["account"] == "alice"
    assert result["date"] == "2026-05-22"
    assert result["dry_run"] is False
    assert calls[0] == ("get_nav_history", "alice", 9999)
    assert calls[1] == ("build_snapshot", {"price_timeout_seconds": 7})
    assert calls[2][0] == "record_nav"
    assert calls[2][1] == ("alice",)
    assert calls[2][2]["nav_date"] == date(2026, 5, 22)
    assert calls[2][2]["dry_run"] is False
    assert calls[2][2]["use_bulk_persist"] is True


def test_daily_report_payload_service_uses_existing_snapshot_and_nav_record():
    calls = []
    snapshot = {"valuation": SimpleNamespace(), "snapshot_time": "2026-05-22T18:00:00"}
    nav_record = _nav_record()
    nav_result = {"success": True, "date": "2026-05-22", "run_id": "run-payload-1", "nav": 1.2345}

    class FakeStorage:
        def get_nav_history(self, account, days):
            calls.append(("get_nav_history", account, days))
            return [nav_record]

    class FakeReadService:
        def build_snapshot(self, **_kwargs):
            raise AssertionError("payload builder should not refetch prices")

        def get_distribution(self, **kwargs):
            calls.append(("get_distribution", kwargs.get("holdings_data") is snapshot))
            return {"success": True, "total_value": 123.45}

    result = DailyReportPayloadService(
        account="alice",
        storage=FakeStorage(),
        portfolio=SimpleNamespace(),
        read_service=FakeReadService(),
    ).build(
        snapshot=snapshot,
        nav_record=nav_record,
        nav_result=nav_result,
        price_timeout=7,
        run_id="run-payload-1",
    )

    assert result["success"] is True
    assert result["distribution"]["total_value"] == 123.45
    assert result["report"]["run_id"] == "run-payload-1"
    assert result["nav_snapshot"]["latest"]["date"] == "2026-05-22"
    assert ("get_nav_history", "alice", 9999) in calls
    assert ("get_distribution", True) in calls
    assert result["report"]["report_type"] == "日报"
    assert result["report"]["date"] == "2026-05-22"
    assert result["stage_timings"].keys() == {"navs_all_ms", "generate_report_ms", "get_nav_ms"}


def test_daily_report_payload_service_uses_dry_run_nav_record_for_recent_snapshot():
    old_nav = _nav_record(nav_date=date(2026, 5, 18))
    nav_record = _nav_record(nav_date=date(2026, 5, 22))
    snapshot = {"valuation": SimpleNamespace(), "snapshot_time": "2026-05-22T18:00:00"}

    class FakeStorage:
        def get_nav_history(self, account, days):
            return [old_nav]

    class FakeReadService:
        def get_distribution(self, **_kwargs):
            return {"success": True, "total_value": 123.45}

    result = DailyReportPayloadService(
        account="alice",
        storage=FakeStorage(),
        portfolio=SimpleNamespace(),
        read_service=FakeReadService(),
    ).build(
        snapshot=snapshot,
        nav_record=nav_record,
        nav_result={"success": True, "date": "2026-05-22", "run_id": "run-payload-dry"},
        run_id="run-payload-dry",
    )

    assert result["success"] is True
    assert result["nav_snapshot"]["latest"]["date"] == "2026-05-22"
    assert [row["date"] for row in result["nav_snapshot"]["history"]] == ["2026-05-18", "2026-05-22"]


def test_daily_account_nav_service_returns_failure_when_payload_stage_raises():
    valuation = SimpleNamespace(warnings=[])
    snapshot = {"valuation": valuation, "snapshot_time": "2026-05-22T18:00:00"}

    class FakeReadService:
        def build_snapshot(self, **_kwargs):
            return snapshot

        def get_distribution(self, **_kwargs):
            raise RuntimeError("distribution failed")

    class FakePortfolio:
        def record_nav(self, *_args, **_kwargs):
            return _nav_record()

    class FakeStorage:
        def get_nav_history(self, *_args, **_kwargs):
            return []

    result = DailyAccountNavService(
        account="alice",
        storage=FakeStorage(),
        portfolio=FakePortfolio(),
        read_service=FakeReadService(),
    ).run(
        nav_date="2026-05-22",
        dry_run=False,
        confirm=True,
        run_id="run-payload-error",
    )

    assert result == {
        "success": False,
        "error": "distribution failed",
        "account": "alice",
        "date": "2026-05-22",
        "run_id": "run-payload-error",
        "dry_run": False,
        "confirm": True,
    }


def test_daily_nav_job_skips_non_business_day():
    result = DailyNavJobService(
        storage=SimpleNamespace(),
        portfolio=SimpleNamespace(reporting_service=object()),
        calendar=BusinessCalendarService(),
    ).run(nav_date="2026-05-23")

    assert result["success"] is True
    assert result["status"] == "skipped_non_business_day"
    assert result["calendar"]["reason"] == "weekend"
    assert result["items"] == []


def test_daily_nav_job_auto_date_uses_previous_business_day():
    calls = []

    class FakeStorage:
        def audit_nav_history_duplicates(self, account=None):
            return {"success": True, "duplicate_group_count": 0}

        def get_nav_on_date(self, account, nav_date):
            return None

        def reconcile_cash_flows(self, **_kwargs):
            return {"success": True, "change_count": 0, "error_count": 0}

    class FakeRunner:
        def __init__(self, account):
            self.account = account

        def run(self, **kwargs):
            calls.append(kwargs)
            return {"success": True, "account": self.account}

    result = DailyNavJobService(
        storage=FakeStorage(),
        portfolio=SimpleNamespace(reporting_service=object()),
        calendar=BusinessCalendarService(),
        account_runner_factory=FakeRunner,
    ).run(
        run_date="2026-05-25",
        account="alice",
        dry_run=False,
        confirm=True,
        run_id="run-job-auto-date",
    )

    assert result["success"] is True
    assert result["date"] == "2026-05-22"
    assert result["calendar"] == {
        "business_day": True,
        "reason": "business_day",
        "date": "2026-05-22",
    }
    assert calls[0]["nav_date"] == date(2026, 5, 22)


def test_daily_nav_job_skips_existing_nav_when_no_overwrite():
    calls = []

    class FakeStorage:
        def audit_nav_history_duplicates(self, account=None):
            return {"success": True, "duplicate_group_count": 0}

        def get_nav_on_date(self, account, nav_date):
            calls.append(("get_nav_on_date", account, nav_date))
            return SimpleNamespace(record_id="rec_nav_1", nav=1.23, total_value=123.0)

        def reconcile_cash_flows(self, **kwargs):
            calls.append(("reconcile_cash_flows", kwargs["account"]))
            return {"success": True, "change_count": 0, "error_count": 0}

    result = DailyNavJobService(
        storage=FakeStorage(),
        portfolio=SimpleNamespace(reporting_service=object()),
        calendar=BusinessCalendarService(),
        account_runner_factory=lambda _account: (_ for _ in ()).throw(AssertionError("runner should not run")),
    ).run(nav_date="2026-05-22", account="alice")

    assert result["success"] is True
    assert result["summary"] == {"skipped_existing_nav": 1}
    assert result["items"][0]["record_id"] == "rec_nav_1"
    assert calls == [
        ("reconcile_cash_flows", "alice"),
        ("get_nav_on_date", "alice", date(2026, 5, 22)),
    ]


def test_daily_nav_job_skips_when_discovery_finds_no_accounts():
    class FakeStorage:
        def get_holdings(self, **_kwargs):
            return []

    result = DailyNavJobService(
        storage=FakeStorage(),
        portfolio=SimpleNamespace(reporting_service=object()),
        calendar=BusinessCalendarService(),
        account_runner_factory=lambda _account: (_ for _ in ()).throw(AssertionError("runner should not run")),
    ).run(nav_date="2026-05-22")

    assert result["success"] is True
    assert result["status"] == "skipped_no_accounts"
    assert result["summary"] == {"skipped_no_accounts": 1}
    assert result["items"] == []


def test_daily_nav_job_blocks_cash_flow_pending():
    class FakeStorage:
        def audit_nav_history_duplicates(self, account=None):
            return {"success": True, "duplicate_group_count": 0}

        def get_nav_on_date(self, account, nav_date):
            return None

        def reconcile_cash_flows(self, **kwargs):
            return {"success": True, "account": kwargs["account"], "change_count": 1, "error_count": 0}

    result = DailyNavJobService(
        storage=FakeStorage(),
        portfolio=SimpleNamespace(reporting_service=object()),
        calendar=BusinessCalendarService(),
        account_runner_factory=lambda _account: (_ for _ in ()).throw(AssertionError("runner should not run")),
    ).run(nav_date="2026-05-22", account="alice")

    assert result["success"] is False
    assert result["status"] == "failed"
    assert result["items"][0]["status"] == "cash_flow_pending"


def test_daily_nav_job_applies_cash_flow_reconcile_before_write():
    calls = []

    class FakeStorage:
        def audit_nav_history_duplicates(self, account=None):
            return {"success": True, "duplicate_group_count": 0}

        def get_nav_on_date(self, account, nav_date):
            return None

        def reconcile_cash_flows(self, **kwargs):
            calls.append(("reconcile_cash_flows", kwargs["account"], kwargs["dry_run"]))
            return {
                "success": True,
                "account": kwargs["account"],
                "change_count": 1,
                "updated_count": 1,
                "error_count": 0,
            }

    class FakeRunner:
        def __init__(self, account):
            self.account = account

        def run(self, **kwargs):
            calls.append(("runner", self.account, kwargs["dry_run"]))
            return {"success": True, "account": self.account}

    result = DailyNavJobService(
        storage=FakeStorage(),
        portfolio=SimpleNamespace(reporting_service=object()),
        calendar=BusinessCalendarService(),
        account_runner_factory=FakeRunner,
    ).run(nav_date="2026-05-22", account="alice", dry_run=False, confirm=True)

    assert result["success"] is True
    assert result["summary"] == {"written": 1}
    assert calls == [
        ("reconcile_cash_flows", "alice", False),
        ("runner", "alice", False),
    ]


def test_daily_nav_job_blocks_cash_flow_pending_before_existing_nav_skip():
    calls = []

    class FakeStorage:
        def audit_nav_history_duplicates(self, account=None):
            return {"success": True, "duplicate_group_count": 0}

        def get_nav_on_date(self, account, nav_date):
            calls.append(("get_nav_on_date", account, nav_date))
            return SimpleNamespace(record_id="rec_nav_1", nav=1.23, total_value=123.0)

        def reconcile_cash_flows(self, **kwargs):
            calls.append(("reconcile_cash_flows", kwargs["account"]))
            return {"success": True, "account": kwargs["account"], "change_count": 1, "error_count": 0}

    result = DailyNavJobService(
        storage=FakeStorage(),
        portfolio=SimpleNamespace(reporting_service=object()),
        calendar=BusinessCalendarService(),
        account_runner_factory=lambda _account: (_ for _ in ()).throw(AssertionError("runner should not run")),
    ).run(nav_date="2026-05-22", account="alice")

    assert result["success"] is False
    assert result["status"] == "failed"
    assert result["items"][0]["status"] == "cash_flow_pending"
    assert calls == [("reconcile_cash_flows", "alice")]


def test_daily_nav_job_runs_each_account_through_account_runner():
    calls = []

    class FakeStorage:
        def audit_nav_history_duplicates(self, account=None):
            return {"success": True, "duplicate_group_count": 0}

        def get_nav_on_date(self, account, nav_date):
            return None

        def reconcile_cash_flows(self, **_kwargs):
            return {"success": True, "change_count": 0, "error_count": 0}

    class FakeRunner:
        def __init__(self, account):
            self.account = account

        def run(self, **kwargs):
            calls.append((self.account, kwargs))
            return {
                "success": True,
                "account": self.account,
                "nav_result": {"success": True, "nav": 1.23},
                "distribution": {"success": True},
            }

    result = DailyNavJobService(
        storage=FakeStorage(),
        portfolio=SimpleNamespace(reporting_service=object()),
        calendar=BusinessCalendarService(),
        account_runner_factory=FakeRunner,
    ).run(
        nav_date="2026-05-22",
        accounts=["alice", "bob"],
        price_timeout=9,
        dry_run=False,
        confirm=True,
        overwrite_existing=True,
        run_id="run-job-1",
    )

    assert result["success"] is True
    assert result["status"] == "completed"
    assert result["summary"] == {"written": 2}
    assert [account for account, _kwargs in calls] == ["alice", "bob"]
    assert calls[0][1]["nav_date"] == date(2026, 5, 22)
    assert calls[0][1]["price_timeout"] == 9
    assert calls[0][1]["dry_run"] is False
    assert calls[0][1]["overwrite_existing"] is True
    assert calls[0][1]["run_id"] == "run-job-1:alice"


def test_daily_nav_job_defaults_futu_sync_write_mode_to_job_mode():
    calls = []

    class FakeStorage:
        def audit_nav_history_duplicates(self, account=None):
            return {"success": True, "duplicate_group_count": 0}

        def get_nav_on_date(self, account, nav_date):
            return None

        def reconcile_cash_flows(self, **_kwargs):
            return {"success": True, "change_count": 0, "error_count": 0}

    class FakeRunner:
        def __init__(self, account):
            self.account = account

        def run(self, **kwargs):
            calls.append(kwargs)
            return {"success": True, "account": self.account}

    result = DailyNavJobService(
        storage=FakeStorage(),
        portfolio=SimpleNamespace(reporting_service=object()),
        calendar=BusinessCalendarService(),
        account_runner_factory=FakeRunner,
    ).run(
        nav_date="2026-05-22",
        account="lx",
        dry_run=False,
        confirm=True,
        sync_futu_cash_mmf=True,
        run_id="run-job-1",
    )

    assert result["success"] is True
    assert calls[0]["sync_futu_cash_mmf"] is True
    assert calls[0]["sync_futu_dry_run"] is False


def test_daily_nav_job_never_writes_futu_sync_during_dry_run():
    calls = []

    class FakeStorage:
        def audit_nav_history_duplicates(self, account=None):
            return {"success": True, "duplicate_group_count": 0}

        def get_nav_on_date(self, account, nav_date):
            return None

        def reconcile_cash_flows(self, **_kwargs):
            return {"success": True, "change_count": 0, "error_count": 0}

    class FakeRunner:
        def __init__(self, account):
            self.account = account

        def run(self, **kwargs):
            calls.append(kwargs)
            return {"success": True, "account": self.account}

    result = DailyNavJobService(
        storage=FakeStorage(),
        portfolio=SimpleNamespace(reporting_service=object()),
        calendar=BusinessCalendarService(),
        account_runner_factory=FakeRunner,
    ).run(
        nav_date="2026-05-22",
        account="lx",
        dry_run=True,
        sync_futu_cash_mmf=True,
        sync_futu_dry_run=False,
    )

    assert result["success"] is True
    assert calls[0]["sync_futu_dry_run"] is True
