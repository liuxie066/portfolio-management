#!/usr/bin/env python3
"""Install Linux deployment assets for scheduled portfolio NAV jobs.

The script is intentionally conservative:
- default mode is dry-run;
- it never overwrites an existing config.yaml unless --overwrite-config is set;
- it only enables the systemd timer when --enable-timer is explicitly set.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICE_NAME = "portfolio-nav-daily.service"
TIMER_NAME = "portfolio-nav-daily.timer"
DEFAULT_ON_CALENDAR = "*-*-* 08:10:00 Asia/Shanghai"


@dataclass(frozen=True)
class InstallPaths:
    app_dir: Path
    config_dir: Path
    config_file: Path
    env_file: Path
    data_dir: Path
    reports_dir: Path
    systemd_dir: Path
    python_bin: Path
    launcher_path: Path


def _default_user() -> str:
    return os.environ.get("SUDO_USER") or getpass.getuser()


def _as_path(value: str | Path) -> Path:
    return Path(value).expanduser()


def build_paths(args) -> InstallPaths:
    app_dir = _as_path(args.app_dir)
    config_dir = _as_path(args.config_dir)
    data_dir = _as_path(args.data_dir)
    reports_dir = _as_path(args.reports_dir)
    systemd_dir = _as_path(args.systemd_dir)
    config_file = _as_path(args.config_file) if args.config_file else config_dir / "config.yaml"
    env_file = _as_path(args.env_file) if args.env_file else config_dir / "portfolio-management.env"
    python_bin = _as_path(args.python) if args.python else app_dir / ".venv" / "bin" / "python"
    launcher_path = _as_path(args.launcher)
    return InstallPaths(
        app_dir=app_dir,
        config_dir=config_dir,
        config_file=config_file,
        env_file=env_file,
        data_dir=data_dir,
        reports_dir=reports_dir,
        systemd_dir=systemd_dir,
        python_bin=python_bin,
        launcher_path=launcher_path,
    )


def render_config_yaml(paths: InstallPaths) -> str:
    """Render a deploy-ready config skeleton without real secrets."""
    payload = {
        "account": "lx",
        "initial_value": 0,
        "start_year": 2024,
        "data": {"dir": str(paths.data_dir)},
        "nav": {"disable_runtime_validation": False},
        "service": {"host": "127.0.0.1", "port": 8765, "url": ""},
        "calendar": {"holidays": []},
        "report": {
            "account_label": "lx",
            "reports_dir": str(paths.reports_dir),
            "publish_root": str(paths.reports_dir / "public"),
            "sync_futu_cash_mmf": False,
            "sync_futu_dry_run": True,
            "disable_nav_runtime_validation": False,
        },
        "futu": {
            "opend": {"host": "127.0.0.1", "port": 11111},
            "trd_env": "REAL",
            "acc_id": None,
            "trd_market": "HK",
            "cash_currency": "CNH",
        },
        "finnhub_api_key": "",
        "feishu": {
            "app_id": "",
            "app_secret": "",
            "app_token": "",
            "tables": {
                "holdings": "",
                "transactions": "",
                "nav_history": "",
                "cash_flow": "",
                "holdings_snapshot": "",
                "compensation_tasks": "",
                "schema_version": "",
            },
        },
    }
    header = (
        "# portfolio-management production config.\n"
        "# Fill Feishu/Futu/API secrets before enabling the daily timer.\n\n"
    )
    return header + yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)


def render_env_file(paths: InstallPaths) -> str:
    return "\n".join([
        f"PORTFOLIO_CONFIG_FILE={paths.config_file}",
        f"PM_DATA_DIR={paths.data_dir}",
        f"PM_REPORTS_DIR={paths.reports_dir}",
        f"PORTFOLIO_PM_BIN={paths.launcher_path}",
        "PYTHONUNBUFFERED=1",
        "",
    ])


def render_launcher(paths: InstallPaths) -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail

unset PYTHONHOME
export PYTHONPATH="{paths.app_dir}"
export PORTFOLIO_CONFIG_FILE="${{PORTFOLIO_CONFIG_FILE:-{paths.config_file}}}"
exec "{paths.python_bin}" "{paths.app_dir / "scripts" / "pm.py"}" "$@"
"""


def _daily_job_args(*, sync_futu_cash_mmf: bool) -> list[str]:
    args = ["daily-job", "--write", "--confirm", "--json"]
    if sync_futu_cash_mmf:
        args.append("--sync-futu-cash-mmf")
    return args


def render_service_unit(paths: InstallPaths, *, run_user: str, sync_futu_cash_mmf: bool) -> str:
    job_args = " ".join(_daily_job_args(sync_futu_cash_mmf=sync_futu_cash_mmf))
    return f"""[Unit]
Description=portfolio-management daily NAV job
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
User={run_user}
WorkingDirectory={paths.app_dir}
Environment=TZ=Asia/Shanghai
Environment=APP_DIR={paths.app_dir}
Environment=PYTHON_BIN={paths.python_bin}
Environment=PORTFOLIO_PM_BIN={paths.launcher_path}
EnvironmentFile={paths.env_file}
ExecStart=/bin/sh -lc 'exec /usr/bin/flock -n /var/lock/portfolio-nav-daily.lock "$PORTFOLIO_PM_BIN" {job_args}'
"""


def render_timer_unit(*, on_calendar: str) -> str:
    return f"""[Unit]
Description=Run portfolio-management daily NAV job

[Timer]
OnCalendar={on_calendar}
Persistent=true
AccuracySec=1min
Unit={SERVICE_NAME}

[Install]
WantedBy=timers.target
"""


def build_plan(args) -> dict:
    paths = build_paths(args)
    service_file = paths.systemd_dir / SERVICE_NAME
    timer_file = paths.systemd_dir / TIMER_NAME
    return {
        "success": True,
        "dry_run": not bool(args.apply),
        "paths": {
            "app_dir": str(paths.app_dir),
            "config_file": str(paths.config_file),
            "env_file": str(paths.env_file),
            "data_dir": str(paths.data_dir),
            "reports_dir": str(paths.reports_dir),
            "systemd_service": str(service_file),
            "systemd_timer": str(timer_file),
            "python_bin": str(paths.python_bin),
            "launcher": str(paths.launcher_path),
        },
        "directories": [
            str(paths.config_dir),
            str(paths.data_dir),
            str(paths.reports_dir),
            str(paths.systemd_dir),
            str(paths.launcher_path.parent),
        ],
        "files": [
            {"path": str(paths.config_file), "mode": "0600", "overwrite": bool(args.overwrite_config)},
            {"path": str(paths.env_file), "mode": "0600", "overwrite": True},
            {"path": str(paths.launcher_path), "mode": "0755", "overwrite": True},
            {"path": str(service_file), "mode": "0644", "overwrite": True},
            {"path": str(timer_file), "mode": "0644", "overwrite": True},
        ],
        "systemd": {
            "enable_timer": bool(args.enable_timer),
            "timer": TIMER_NAME,
            "service": SERVICE_NAME,
            "on_calendar": args.on_calendar,
        },
        "daily_job_args": _daily_job_args(sync_futu_cash_mmf=bool(args.sync_futu_cash_mmf)),
    }


def _write_text(path: Path, content: str, *, mode: int, overwrite: bool) -> str:
    if path.exists() and not overwrite:
        return "skipped_exists"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(mode)
    return "written"


def _mkdirs(paths: Iterable[Path]) -> list[str]:
    created = []
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)
        created.append(str(path))
    return created


def apply_install(args) -> dict:
    paths = build_paths(args)
    service_file = paths.systemd_dir / SERVICE_NAME
    timer_file = paths.systemd_dir / TIMER_NAME
    _mkdirs([paths.config_dir, paths.data_dir, paths.reports_dir, paths.systemd_dir, paths.launcher_path.parent])

    writes = {
        str(paths.config_file): _write_text(
            paths.config_file,
            render_config_yaml(paths),
            mode=0o600,
            overwrite=bool(args.overwrite_config),
        ),
        str(paths.env_file): _write_text(paths.env_file, render_env_file(paths), mode=0o600, overwrite=True),
        str(paths.launcher_path): _write_text(paths.launcher_path, render_launcher(paths), mode=0o755, overwrite=True),
        str(service_file): _write_text(
            service_file,
            render_service_unit(paths, run_user=args.run_user, sync_futu_cash_mmf=bool(args.sync_futu_cash_mmf)),
            mode=0o644,
            overwrite=True,
        ),
        str(timer_file): _write_text(timer_file, render_timer_unit(on_calendar=args.on_calendar), mode=0o644, overwrite=True),
    }

    systemd_commands = [["systemctl", "daemon-reload"]]
    if args.enable_timer:
        systemd_commands.append(["systemctl", "enable", "--now", TIMER_NAME])
    for command in systemd_commands:
        subprocess.run(command, check=True)

    result = build_plan(args)
    result["dry_run"] = False
    result["writes"] = writes
    result["next_steps"] = [
        f"edit {paths.config_file} and fill Feishu/Futu credentials",
        f"{paths.launcher_path} config doctor --json",
        f"systemctl status {TIMER_NAME}",
    ]
    return result


def _print_plan(payload: dict, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    mode = "dry-run" if payload.get("dry_run") else "applied"
    print(f"portfolio-management Linux install plan ({mode})")
    for key, value in payload.get("paths", {}).items():
        print(f"  {key}: {value}")
    print(f"  timer: {payload.get('systemd', {}).get('on_calendar')}")
    print(f"  daily-job: {' '.join(payload.get('daily_job_args') or [])}")
    if payload.get("writes"):
        print("  writes:")
        for path, status in payload["writes"].items():
            print(f"    {path}: {status}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Install portfolio-management Linux systemd assets")
    parser.add_argument("--apply", action="store_true", help="write files; default is dry-run")
    parser.add_argument("--json", action="store_true", help="output JSON")
    parser.add_argument("--app-dir", default="/opt/portfolio-management/current", help="checked-out application directory")
    parser.add_argument("--config-dir", default="/etc/portfolio-management", help="configuration directory")
    parser.add_argument("--config-file", default=None, help="config YAML path; defaults to CONFIG_DIR/config.yaml")
    parser.add_argument("--env-file", default=None, help="systemd EnvironmentFile path")
    parser.add_argument("--data-dir", default="/var/lib/portfolio-management/.data", help="runtime state/cache directory")
    parser.add_argument("--reports-dir", default="/var/lib/portfolio-management/reports", help="report output directory")
    parser.add_argument("--systemd-dir", default="/etc/systemd/system", help="systemd unit directory")
    parser.add_argument("--python", default=None, help="Python interpreter for systemd job")
    parser.add_argument("--launcher", default="/usr/local/bin/pm", help="pm launcher path")
    parser.add_argument("--run-user", default=_default_user(), help="systemd User for the oneshot service")
    parser.add_argument("--on-calendar", default=DEFAULT_ON_CALENDAR, help="systemd OnCalendar value")
    parser.add_argument("--sync-futu-cash-mmf", action="store_true", help="include Futu cash/MMF sync in daily job")
    parser.add_argument("--overwrite-config", action="store_true", help="overwrite an existing config.yaml")
    parser.add_argument("--enable-timer", action="store_true", help="run systemctl enable --now after writing units")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    payload = apply_install(args) if args.apply else build_plan(args)
    _print_plan(payload, as_json=bool(args.json))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
