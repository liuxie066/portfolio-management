from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import yaml

from scripts import install_linux


def _args(tmp_path: Path, *extra: str):
    return install_linux.build_parser().parse_args([
        "--app-dir", str(tmp_path / "app"),
        "--config-dir", str(tmp_path / "etc"),
        "--data-dir", str(tmp_path / "state"),
        "--reports-dir", str(tmp_path / "reports"),
        "--systemd-dir", str(tmp_path / "systemd"),
        "--launcher", str(tmp_path / "bin" / "pm"),
        "--run-user", "portfolio",
        "--options-monitor-env-file", str(tmp_path / "options-monitor.env"),
        *extra,
    ])


def test_install_linux_plan_uses_yaml_config_and_durable_receipt_timer(tmp_path):
    payload = install_linux.build_plan(_args(tmp_path))

    assert payload["success"] is True
    assert payload["dry_run"] is True
    assert payload["paths"]["config_file"].endswith("/etc/config.yaml")
    assert payload["paths"]["env_file"].endswith("/etc/portfolio-management.env")
    assert payload["paths"]["launcher"].endswith("/bin/pm")
    assert payload["systemd"]["morning"] == {
        "timer": install_linux.TIMER_NAME,
        "service": install_linux.SERVICE_NAME,
        "on_calendar": "Mon..Sat *-*-* 08:10:00 Asia/Shanghai",
        "mode": "morning",
    }
    assert payload["systemd"]["evening"] == {
        "timer": install_linux.EVENING_TIMER_NAME,
        "service": install_linux.EVENING_SERVICE_NAME,
        "on_calendar": "Mon..Fri *-*-* 17:10:00 Asia/Shanghai",
        "mode": "evening",
    }
    assert payload["systemd"]["cash_flow"] == {
        "timer": install_linux.CASH_FLOW_TIMER_NAME,
        "service": install_linux.CASH_FLOW_SERVICE_NAME,
        "on_calendar": "*-*-* *:00/15:00 Asia/Shanghai",
        "mode": "scan",
    }
    assert payload["systemd"]["enable_api_service"] is False
    assert payload["systemd"]["api"] == {
        "service": install_linux.API_SERVICE_NAME,
        "host": "127.0.0.1",
        "port": 8765,
    }
    assert payload["systemd"]["enable_quality_timer"] is False
    assert payload["systemd"]["quality"] == {
        "timer": install_linux.QUALITY_TIMER_NAME,
        "service": install_linux.QUALITY_SERVICE_NAME,
        "interval": "15min",
    }
    assert payload["systemd"]["receipts"] == {
        "timer": install_linux.RECEIPT_TIMER_NAME,
        "service": install_linux.RECEIPT_SERVICE_NAME,
        "interval": "5min",
    }
    assert "daily_job_args" not in payload


def test_install_linux_rendered_config_points_runtime_dirs(tmp_path):
    args = _args(tmp_path)
    paths = install_linux.build_paths(args)
    rendered = install_linux.render_config_yaml(paths)
    payload = yaml.safe_load(rendered)

    assert payload["data"]["dir"] == str(tmp_path / "state")
    assert payload["report"]["reports_dir"] == str(tmp_path / "reports")
    assert payload["feishu"]["app_secret"] == ""
    assert payload["quality"] == {
        "read_token": "",
        "instance_id": "portfolio-management-prod",
        "accounts": ["lx", "sy"],
        "onboarded": False,
    }


def test_install_linux_apply_writes_files_without_overwriting_existing_config(tmp_path, monkeypatch):
    args = _args(tmp_path, "--apply")
    paths = install_linux.build_paths(args)
    paths.config_file.parent.mkdir(parents=True)
    paths.config_file.write_text("account: existing\n", encoding="utf-8")

    commands = []
    monkeypatch.setattr(install_linux.subprocess, "run", lambda command, check: commands.append(command))

    payload = install_linux.apply_install(args)

    assert payload["dry_run"] is False
    assert payload["writes"][str(paths.config_file)] == "skipped_exists"
    assert paths.config_file.read_text(encoding="utf-8") == "account: existing\n"
    assert paths.env_file.exists()
    assert (tmp_path / "bin" / "pm").exists()
    assert "PORTFOLIO_CONFIG_FILE" in (tmp_path / "bin" / "pm").read_text(encoding="utf-8")
    for unit_name in (
        install_linux.SERVICE_NAME,
        install_linux.TIMER_NAME,
        install_linux.EVENING_SERVICE_NAME,
        install_linux.EVENING_TIMER_NAME,
        install_linux.CASH_FLOW_SERVICE_NAME,
        install_linux.CASH_FLOW_TIMER_NAME,
        install_linux.API_SERVICE_NAME,
        install_linux.QUALITY_SERVICE_NAME,
        install_linux.QUALITY_TIMER_NAME,
        install_linux.RECEIPT_SERVICE_NAME,
        install_linux.RECEIPT_TIMER_NAME,
    ):
        assert (paths.systemd_dir / unit_name).exists()
    assert commands == [["systemctl", "daemon-reload"]]


def test_install_linux_enable_starts_all_timers(tmp_path, monkeypatch):
    commands = []
    monkeypatch.setattr(install_linux.subprocess, "run", lambda command, check: commands.append(command))

    install_linux.apply_install(_args(tmp_path, "--apply", "--enable-timer"))

    assert commands == [
        ["systemctl", "daemon-reload"],
        [
            "systemctl",
            "enable",
            "--now",
            install_linux.TIMER_NAME,
            install_linux.EVENING_TIMER_NAME,
            install_linux.CASH_FLOW_TIMER_NAME,
            install_linux.RECEIPT_TIMER_NAME,
        ],
    ]


def test_install_linux_enable_api_service_is_independent_from_timers(tmp_path, monkeypatch):
    commands = []
    monkeypatch.setattr(install_linux.subprocess, "run", lambda command, check: commands.append(command))

    install_linux.apply_install(_args(tmp_path, "--apply", "--enable-api-service"))

    assert commands == [
        ["systemctl", "daemon-reload"],
        ["systemctl", "enable", "--now", install_linux.API_SERVICE_NAME],
    ]


def test_install_linux_enable_quality_timer_is_independent(tmp_path, monkeypatch):
    commands = []
    monkeypatch.setattr(install_linux.subprocess, "run", lambda command, check: commands.append(command))

    install_linux.apply_install(_args(tmp_path, "--apply", "--enable-quality-timer"))

    assert commands == [
        ["systemctl", "daemon-reload"],
        ["systemctl", "enable", "--now", install_linux.QUALITY_TIMER_NAME],
    ]


def test_install_linux_api_service_is_loopback_only_and_long_running(tmp_path):
    paths = install_linux.build_paths(_args(tmp_path))
    unit = install_linux.render_api_service_unit(paths, run_user="portfolio")

    assert "Type=simple" in unit
    assert f"ExecStart={paths.python_bin} {paths.app_dir / 'scripts' / 'serve.py'} --host 127.0.0.1 --port 8765" in unit
    assert "Restart=on-failure" in unit
    assert "RestartSec=5" in unit
    assert "WantedBy=multi-user.target" in unit
    assert "--allow-remote" not in unit
    assert "scripts/service.py" not in unit
    assert "flock" not in unit


def test_install_linux_quality_units_are_read_only_scoped_and_every_15_minutes(tmp_path):
    paths = install_linux.build_paths(_args(tmp_path))
    service = install_linux.render_quality_service_unit(paths, run_user="portfolio")
    timer = install_linux.render_interval_timer_unit(
        interval="15min",
        service_name=install_linux.QUALITY_SERVICE_NAME,
        description="quality",
    )

    assert "Type=oneshot" in service
    assert (
        f"ExecStart=/usr/bin/flock -n {install_linux.QUALITY_LOCK_FILE} "
        f"{paths.launcher_path} quality refresh --json"
    ) in service
    assert "RuntimeMaxSec=300" in service
    assert "OnBootSec=5min" in timer
    assert "OnUnitActiveSec=15min" in timer
    assert f"Unit={install_linux.QUALITY_SERVICE_NAME}" in timer
    assert "WantedBy=timers.target" in timer


def test_install_linux_receipt_units_retry_durable_outbox_every_five_minutes(tmp_path):
    paths = install_linux.build_paths(_args(tmp_path))
    service = install_linux.render_receipt_service_unit(
        paths,
        run_user="portfolio",
    )
    timer = install_linux.render_interval_timer_unit(
        interval="5min",
        service_name=install_linux.RECEIPT_SERVICE_NAME,
        description="receipts",
    )

    assert (
        f"ExecStart=/usr/bin/flock -n {install_linux.RECEIPT_LOCK_FILE} "
        f"{paths.launcher_path} receipts dispatch --limit 100 --confirm --json"
    ) in service
    assert "RuntimeMaxSec=60" in service
    assert "OnUnitActiveSec=5min" in timer
    assert f"Unit={install_linux.RECEIPT_SERVICE_NAME}" in timer


def test_install_linux_root_apply_assigns_runtime_dirs_to_service_user(
    tmp_path,
    monkeypatch,
):
    paths = install_linux.build_paths(_args(tmp_path))
    paths.data_dir.mkdir(parents=True)
    paths.reports_dir.mkdir(parents=True)
    operation_db = paths.data_dir / "pm_operation_state.sqlite3"
    operation_db.write_text("", encoding="utf-8")
    commands = []
    monkeypatch.setattr(install_linux.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        install_linux.subprocess,
        "run",
        lambda command, check: commands.append(command),
    )

    rendered = install_linux._prepare_runtime_ownership(
        paths,
        run_user="portfolio",
    )

    assert ["chown", "portfolio", str(paths.data_dir)] in commands
    assert ["chown", "portfolio", str(paths.reports_dir)] in commands
    assert ["chown", "portfolio", str(operation_db)] in commands
    assert paths.data_dir.stat().st_mode & 0o777 == 0o750
    assert paths.reports_dir.stat().st_mode & 0o777 == 0o750
    assert rendered == [" ".join(command) for command in commands]


def test_install_linux_apply_imports_only_three_options_monitor_feishu_values(tmp_path, monkeypatch):
    source = tmp_path / "options-monitor.env"
    source.write_text(
        "\n".join([
            "OM_FEISHU_BOT_APP_ID=cli_liukanshan",
            "OM_FEISHU_BOT_APP_SECRET=receipt_secret",
            "OM_FEISHU_BOT_USER_OPEN_ID=ou_user",
            "OPENAI_API_KEY=must_not_copy",
            "OM_ASSISTANT_API_KEY=must_not_copy_either",
            "",
        ]),
        encoding="utf-8",
    )
    args = _args(tmp_path, "--apply")
    paths = install_linux.build_paths(args)
    monkeypatch.setattr(install_linux.subprocess, "run", lambda command, check: None)

    payload = install_linux.apply_install(args)
    rendered = paths.env_file.read_text(encoding="utf-8")

    assert payload["feishu_receipt_env"] == {
        "source": str(source),
        "target": str(paths.env_file),
        "keys": list(install_linux.OPTIONS_MONITOR_FEISHU_KEYS),
        "status": "imported",
    }
    assert "OM_FEISHU_BOT_APP_ID=cli_liukanshan" in rendered
    assert "OM_FEISHU_BOT_APP_SECRET=receipt_secret" in rendered
    assert "OM_FEISHU_BOT_USER_OPEN_ID=ou_user" in rendered
    assert "OPENAI_API_KEY" not in rendered
    assert "OM_ASSISTANT_API_KEY" not in rendered
    assert "receipt_secret" not in str(payload)


def test_install_linux_partial_source_updates_only_explicit_receipt_values(tmp_path, monkeypatch):
    source = tmp_path / "options-monitor.env"
    source.write_text(
        "OM_FEISHU_BOT_APP_ID=cli_new\n"
        "OM_FEISHU_BOT_APP_SECRET=secret_new\n",
        encoding="utf-8",
    )
    args = _args(tmp_path, "--apply")
    paths = install_linux.build_paths(args)
    paths.env_file.parent.mkdir(parents=True)
    paths.env_file.write_text(
        "# keep this comment\n"
        "KEEP_EXISTING=1\n"
        "OM_FEISHU_BOT_APP_ID=cli_old\n"
        "OM_FEISHU_BOT_APP_SECRET=secret_old\n"
        "OM_FEISHU_BOT_USER_OPEN_ID=ou_keep\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(install_linux.subprocess, "run", lambda command, check: None)

    payload = install_linux.apply_install(args)
    rendered = paths.env_file.read_text(encoding="utf-8")

    assert payload["feishu_receipt_env"]["status"] == "imported"
    assert "# keep this comment\nKEEP_EXISTING=1\n" in rendered
    assert "OM_FEISHU_BOT_APP_ID=cli_new" in rendered
    assert "OM_FEISHU_BOT_APP_SECRET=secret_new" in rendered
    assert "OM_FEISHU_BOT_USER_OPEN_ID=ou_keep" in rendered


def test_install_linux_missing_source_preserves_target_env_byte_for_byte(tmp_path, monkeypatch):
    args = _args(tmp_path, "--apply")
    paths = install_linux.build_paths(args)
    paths.env_file.parent.mkdir(parents=True)
    original = (
        "# production-only values\n"
        "KEEP_EXISTING=1\n"
        "OM_FEISHU_BOT_APP_ID=cli_keep\n"
        "OM_FEISHU_BOT_APP_SECRET=secret_keep\n"
        "OM_FEISHU_BOT_USER_OPEN_ID=ou_keep\n"
    )
    paths.env_file.write_text(original, encoding="utf-8")
    before_digest = hashlib.sha256(original.encode()).hexdigest()
    monkeypatch.setattr(install_linux.subprocess, "run", lambda command, check: None)

    payload = install_linux.apply_install(args)
    rendered = paths.env_file.read_text(encoding="utf-8")

    assert payload["feishu_receipt_env"]["status"] == "source_missing"
    assert hashlib.sha256(rendered.encode()).hexdigest() == before_digest
    assert rendered == original


def test_install_linux_rejects_duplicate_target_env_before_other_writes(tmp_path, monkeypatch):
    args = _args(tmp_path, "--apply")
    paths = install_linux.build_paths(args)
    paths.env_file.parent.mkdir(parents=True)
    paths.env_file.write_text("DUP=1\nDUP=2\n", encoding="utf-8")
    monkeypatch.setattr(install_linux.subprocess, "run", lambda command, check: None)

    try:
        install_linux.apply_install(args)
    except ValueError as exc:
        assert "duplicate target env key: DUP" in str(exc)
    else:
        raise AssertionError("expected duplicate target env to fail")

    assert not paths.config_file.exists()
    assert paths.env_file.read_text(encoding="utf-8") == "DUP=1\nDUP=2\n"


def test_install_linux_rejects_duplicate_source_env_before_other_writes(tmp_path, monkeypatch):
    source = tmp_path / "options-monitor.env"
    source.write_text(
        "OM_FEISHU_BOT_APP_ID=first\n"
        "OM_FEISHU_BOT_APP_ID=second\n",
        encoding="utf-8",
    )
    args = _args(tmp_path, "--apply")
    paths = install_linux.build_paths(args)
    monkeypatch.setattr(install_linux.subprocess, "run", lambda command, check: None)

    try:
        install_linux.apply_install(args)
    except ValueError as exc:
        assert "duplicate options-monitor env key: OM_FEISHU_BOT_APP_ID" in str(exc)
    else:
        raise AssertionError("expected duplicate source env to fail")

    assert not paths.config_file.exists()
    assert not paths.env_file.exists()


def test_install_linux_service_units_use_versioned_wrapper_and_shared_lock(tmp_path):
    args = _args(tmp_path)
    paths = install_linux.build_paths(args)
    morning = install_linux.render_service_unit(paths, run_user="portfolio", mode="morning")
    evening = install_linux.render_service_unit(paths, run_user="portfolio", mode="evening")

    assert f"Environment=PORTFOLIO_PM_BIN={tmp_path / 'bin' / 'pm'}" in morning
    assert "PORTFOLIO_FUTU_SY_ENV_FILE" not in morning
    assert f"{install_linux.SCHEDULE_LOCK_FILE} {tmp_path / 'app' / 'scripts' / 'portfolio_scheduled_job.sh'} morning" in morning
    assert f"{install_linux.SCHEDULE_LOCK_FILE} {tmp_path / 'app' / 'scripts' / 'portfolio_scheduled_job.sh'} evening" in evening
    assert "--sync-futu-cash-mmf" not in morning
    assert "--sync-futu-cash-mmf" not in evening


def test_install_linux_timer_units_are_persistent_and_target_correct_services(tmp_path):
    morning = install_linux.render_timer_unit(
        on_calendar="Mon..Sat *-*-* 08:10:00 Asia/Shanghai",
        service_name=install_linux.SERVICE_NAME,
        description="morning",
    )
    evening = install_linux.render_timer_unit(
        on_calendar="Mon..Fri *-*-* 17:10:00 Asia/Shanghai",
        service_name=install_linux.EVENING_SERVICE_NAME,
        description="evening",
    )

    assert "OnCalendar=Mon..Sat *-*-* 08:10:00 Asia/Shanghai" in morning
    assert "Unit=portfolio-nav-daily.service" in morning
    assert "OnCalendar=Mon..Fri *-*-* 17:10:00 Asia/Shanghai" in evening
    assert "Unit=portfolio-futu-evening.service" in evening
    assert "Persistent=true" in morning
    assert "Persistent=true" in evening


def test_install_shell_help_is_available():
    result = subprocess.run(
        ["bash", "scripts/install.sh", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "portfolio-management installer" in result.stdout
    assert "--enable-timer" in result.stdout
    assert "--enable-api-service" in result.stdout
    assert "--enable-quality-timer" in result.stdout
    assert "receipt timers" in result.stdout
    assert "--sync-futu-cash-mmf" not in result.stdout
    assert "OM_FEISHU_BOT_APP_ID" in result.stdout
