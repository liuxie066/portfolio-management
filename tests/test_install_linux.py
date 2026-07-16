from __future__ import annotations

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


def test_install_linux_plan_uses_yaml_config_and_two_timers(tmp_path):
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
    assert "daily_job_args" not in payload


def test_install_linux_rendered_config_points_runtime_dirs(tmp_path):
    args = _args(tmp_path)
    paths = install_linux.build_paths(args)
    rendered = install_linux.render_config_yaml(paths)
    payload = yaml.safe_load(rendered)

    assert payload["data"]["dir"] == str(tmp_path / "state")
    assert payload["report"]["reports_dir"] == str(tmp_path / "reports")
    assert payload["feishu"]["app_secret"] == ""


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
    ):
        assert (paths.systemd_dir / unit_name).exists()
    assert commands == [["systemctl", "daemon-reload"]]


def test_install_linux_enable_starts_both_timers(tmp_path, monkeypatch):
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
        ],
    ]


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


def test_install_linux_rejects_partial_options_monitor_feishu_config_before_writes(tmp_path, monkeypatch):
    source = tmp_path / "options-monitor.env"
    source.write_text(
        "OM_FEISHU_BOT_APP_ID=cli_liukanshan\n"
        "OM_FEISHU_BOT_APP_SECRET=receipt_secret\n",
        encoding="utf-8",
    )
    args = _args(tmp_path, "--apply")
    paths = install_linux.build_paths(args)
    paths.env_file.parent.mkdir(parents=True)
    paths.env_file.write_text("KEEP_EXISTING=1\n", encoding="utf-8")
    monkeypatch.setattr(install_linux.subprocess, "run", lambda command, check: None)

    try:
        install_linux.apply_install(args)
    except ValueError as exc:
        assert "OM_FEISHU_BOT_USER_OPEN_ID" in str(exc)
    else:
        raise AssertionError("expected partial options-monitor receipt config to fail")

    assert paths.env_file.read_text(encoding="utf-8") == "KEEP_EXISTING=1\n"


def test_install_linux_service_units_use_versioned_wrapper_and_shared_lock(tmp_path):
    args = _args(tmp_path)
    paths = install_linux.build_paths(args)
    morning = install_linux.render_service_unit(paths, run_user="portfolio", mode="morning")
    evening = install_linux.render_service_unit(paths, run_user="portfolio", mode="evening")

    assert f"Environment=PORTFOLIO_PM_BIN={tmp_path / 'bin' / 'pm'}" in morning
    assert f"Environment=PORTFOLIO_FUTU_SY_ENV_FILE={tmp_path / 'etc' / 'futu-sy.env'}" in morning
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
    assert "evening Futu timers" in result.stdout
    assert "--sync-futu-cash-mmf" not in result.stdout
    assert "OM_FEISHU_BOT_APP_ID" in result.stdout
