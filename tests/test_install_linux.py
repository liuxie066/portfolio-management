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
        *extra,
    ])


def test_install_linux_plan_uses_yaml_config_and_daily_timer(tmp_path):
    payload = install_linux.build_plan(_args(tmp_path, "--sync-futu-cash-mmf"))

    assert payload["success"] is True
    assert payload["dry_run"] is True
    assert payload["paths"]["config_file"].endswith("/etc/config.yaml")
    assert payload["paths"]["env_file"].endswith("/etc/portfolio-management.env")
    assert payload["paths"]["launcher"].endswith("/bin/pm")
    assert payload["systemd"]["on_calendar"] == "*-*-* 08:10:00"
    assert payload["daily_job_args"] == [
        "daily-job",
        "--write",
        "--confirm",
        "--json",
        "--sync-futu-cash-mmf",
    ]


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
    assert (paths.systemd_dir / install_linux.SERVICE_NAME).exists()
    assert (paths.systemd_dir / install_linux.TIMER_NAME).exists()
    assert commands == [["systemctl", "daemon-reload"]]


def test_install_linux_service_unit_uses_launcher(tmp_path):
    args = _args(tmp_path, "--sync-futu-cash-mmf")
    paths = install_linux.build_paths(args)
    rendered = install_linux.render_service_unit(paths, run_user="portfolio", sync_futu_cash_mmf=True)

    assert f"Environment=PORTFOLIO_PM_BIN={tmp_path / 'bin' / 'pm'}" in rendered
    assert '"$PORTFOLIO_PM_BIN" daily-job --write --confirm --json --sync-futu-cash-mmf' in rendered


def test_install_shell_help_is_available():
    result = subprocess.run(
        ["bash", "scripts/install.sh", "--help"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "portfolio-management installer" in result.stdout
    assert "--enable-timer" in result.stdout
