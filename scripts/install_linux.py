#!/usr/bin/env python3
"""Install Linux deployment assets for scheduled portfolio NAV jobs.

The script is intentionally conservative:
- default mode is dry-run;
- it never overwrites an existing config.yaml unless --overwrite-config is set;
- it only enables timers or the loopback API when explicitly requested.
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
EVENING_SERVICE_NAME = "portfolio-futu-evening.service"
EVENING_TIMER_NAME = "portfolio-futu-evening.timer"
API_SERVICE_NAME = "portfolio-management-api.service"
DEFAULT_MORNING_ON_CALENDAR = "Mon..Sat *-*-* 08:10:00 Asia/Shanghai"
DEFAULT_EVENING_ON_CALENDAR = "Mon..Fri *-*-* 17:10:00 Asia/Shanghai"
SCHEDULE_LOCK_FILE = "/var/lock/portfolio-management-scheduled.lock"
DEFAULT_OPTIONS_MONITOR_ENV_FILE = "/etc/options-monitor/options-monitor.env"
OPTIONS_MONITOR_FEISHU_KEYS = (
    "OM_FEISHU_BOT_APP_ID",
    "OM_FEISHU_BOT_APP_SECRET",
    "OM_FEISHU_BOT_USER_OPEN_ID",
)


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


def _env_assignments(content: str, *, label: str) -> dict[str, int]:
    positions: dict[str, int] = {}
    for index, line in enumerate(content.splitlines(keepends=True)):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip()
        if not key:
            continue
        if key in positions:
            raise ValueError(f"duplicate {label} env key: {key}")
        positions[key] = index
    return positions


def read_options_monitor_feishu_env(path: str | Path) -> dict[str, str]:
    """Read explicitly provided Feishu receipt values from options-monitor."""
    source = _as_path(path)
    if not source.exists():
        return {}

    content = source.read_text(encoding="utf-8")
    _env_assignments(content, label="options-monitor")
    selected: dict[str, str] = {}
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if key not in OPTIONS_MONITOR_FEISHU_KEYS:
            continue
        value = value.strip()
        if value not in {"", "''", '""'}:
            selected[key] = value
    return selected


def render_env_file(
    paths: InstallPaths,
    *,
    receipt_env: dict[str, str] | None = None,
    existing_content: str | None = None,
) -> str:
    receipt_env = receipt_env or {}
    if existing_content is None:
        existing_content = "\n".join([
            f"PORTFOLIO_CONFIG_FILE={paths.config_file}",
            f"PM_DATA_DIR={paths.data_dir}",
            f"PM_REPORTS_DIR={paths.reports_dir}",
            f"PORTFOLIO_PM_BIN={paths.launcher_path}",
            "PYTHONUNBUFFERED=1",
            "",
        ])

    positions = _env_assignments(existing_content, label="target")
    if not receipt_env:
        return existing_content

    lines = existing_content.splitlines(keepends=True)
    for key in OPTIONS_MONITOR_FEISHU_KEYS:
        if key not in receipt_env:
            continue
        value = receipt_env[key]
        if key in positions:
            index = positions[key]
            newline = "\n" if lines[index].endswith("\n") else ""
            lines[index] = f"{key}={value}{newline}"
            continue
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        positions[key] = len(lines)
        lines.append(f"{key}={value}\n")
    return "".join(lines)


def render_launcher(paths: InstallPaths) -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail

unset PYTHONHOME
export PYTHONPATH="{paths.app_dir}"
export PORTFOLIO_CONFIG_FILE="${{PORTFOLIO_CONFIG_FILE:-{paths.config_file}}}"
exec "{paths.python_bin}" "{paths.app_dir / "scripts" / "pm.py"}" "$@"
"""


def render_service_unit(paths: InstallPaths, *, run_user: str, mode: str) -> str:
    if mode not in {"morning", "evening"}:
        raise ValueError(f"unsupported scheduled job mode: {mode}")
    description = (
        "portfolio-management morning Futu sync and NAV job"
        if mode == "morning"
        else "portfolio-management evening Futu holdings sync"
    )
    schedule_script = paths.app_dir / "scripts" / "portfolio_scheduled_job.sh"
    sy_env_file = paths.config_dir / "futu-sy.env"
    return f"""[Unit]
Description={description}
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
Environment=PORTFOLIO_FUTU_SY_ENV_FILE={sy_env_file}
EnvironmentFile={paths.env_file}
ExecStart=/usr/bin/flock -n {SCHEDULE_LOCK_FILE} {schedule_script} {mode}
"""


def render_api_service_unit(paths: InstallPaths, *, run_user: str) -> str:
    return f"""[Unit]
Description=portfolio-management loopback HTTP API
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User={run_user}
WorkingDirectory={paths.app_dir}
Environment=TZ=Asia/Shanghai
EnvironmentFile={paths.env_file}
ExecStart={paths.python_bin} {paths.app_dir / "scripts" / "serve.py"} --host 127.0.0.1 --port 8765
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
"""


def render_timer_unit(*, on_calendar: str, service_name: str, description: str) -> str:
    return f"""[Unit]
Description={description}

[Timer]
OnCalendar={on_calendar}
Persistent=true
AccuracySec=1min
Unit={service_name}

[Install]
WantedBy=timers.target
"""


def _unit_paths(paths: InstallPaths) -> dict[str, Path]:
    return {
        "morning_service": paths.systemd_dir / SERVICE_NAME,
        "morning_timer": paths.systemd_dir / TIMER_NAME,
        "evening_service": paths.systemd_dir / EVENING_SERVICE_NAME,
        "evening_timer": paths.systemd_dir / EVENING_TIMER_NAME,
        "api_service": paths.systemd_dir / API_SERVICE_NAME,
    }


def build_plan(args) -> dict:
    paths = build_paths(args)
    units = _unit_paths(paths)
    return {
        "success": True,
        "dry_run": not bool(args.apply),
        "paths": {
            "app_dir": str(paths.app_dir),
            "config_file": str(paths.config_file),
            "env_file": str(paths.env_file),
            "data_dir": str(paths.data_dir),
            "reports_dir": str(paths.reports_dir),
            "morning_service": str(units["morning_service"]),
            "morning_timer": str(units["morning_timer"]),
            "evening_service": str(units["evening_service"]),
            "evening_timer": str(units["evening_timer"]),
            "api_service": str(units["api_service"]),
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
            *[
                {"path": str(path), "mode": "0644", "overwrite": True}
                for path in units.values()
            ],
        ],
        "systemd": {
            "enable_timers": bool(args.enable_timer),
            "enable_api_service": bool(args.enable_api_service),
            "lock_file": SCHEDULE_LOCK_FILE,
            "morning": {
                "timer": TIMER_NAME,
                "service": SERVICE_NAME,
                "on_calendar": args.morning_on_calendar,
                "mode": "morning",
            },
            "evening": {
                "timer": EVENING_TIMER_NAME,
                "service": EVENING_SERVICE_NAME,
                "on_calendar": args.evening_on_calendar,
                "mode": "evening",
            },
            "api": {
                "service": API_SERVICE_NAME,
                "host": "127.0.0.1",
                "port": 8765,
            },
        },
        "feishu_receipt_env": {
            "source": str(_as_path(args.options_monitor_env_file)),
            "target": str(paths.env_file),
            "keys": list(OPTIONS_MONITOR_FEISHU_KEYS),
        },
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
    units = _unit_paths(paths)
    receipt_env = read_options_monitor_feishu_env(args.options_monitor_env_file)
    existing_env = paths.env_file.read_text(encoding="utf-8") if paths.env_file.exists() else None
    rendered_env = render_env_file(paths, receipt_env=receipt_env, existing_content=existing_env)
    _mkdirs([paths.config_dir, paths.data_dir, paths.reports_dir, paths.systemd_dir, paths.launcher_path.parent])

    writes = {
        str(paths.config_file): _write_text(
            paths.config_file,
            render_config_yaml(paths),
            mode=0o600,
            overwrite=bool(args.overwrite_config),
        ),
        str(paths.env_file): _write_text(
            paths.env_file,
            rendered_env,
            mode=0o600,
            overwrite=True,
        ),
        str(paths.launcher_path): _write_text(paths.launcher_path, render_launcher(paths), mode=0o755, overwrite=True),
        str(units["morning_service"]): _write_text(
            units["morning_service"],
            render_service_unit(paths, run_user=args.run_user, mode="morning"),
            mode=0o644,
            overwrite=True,
        ),
        str(units["morning_timer"]): _write_text(
            units["morning_timer"],
            render_timer_unit(
                on_calendar=args.morning_on_calendar,
                service_name=SERVICE_NAME,
                description="Run portfolio-management morning sync and NAV job",
            ),
            mode=0o644,
            overwrite=True,
        ),
        str(units["evening_service"]): _write_text(
            units["evening_service"],
            render_service_unit(paths, run_user=args.run_user, mode="evening"),
            mode=0o644,
            overwrite=True,
        ),
        str(units["evening_timer"]): _write_text(
            units["evening_timer"],
            render_timer_unit(
                on_calendar=args.evening_on_calendar,
                service_name=EVENING_SERVICE_NAME,
                description="Run portfolio-management evening Futu holdings sync",
            ),
            mode=0o644,
            overwrite=True,
        ),
        str(units["api_service"]): _write_text(
            units["api_service"],
            render_api_service_unit(paths, run_user=args.run_user),
            mode=0o644,
            overwrite=True,
        ),
    }

    systemd_commands = [["systemctl", "daemon-reload"]]
    if args.enable_timer:
        systemd_commands.append(["systemctl", "enable", "--now", TIMER_NAME, EVENING_TIMER_NAME])
    if args.enable_api_service:
        systemd_commands.append(["systemctl", "enable", "--now", API_SERVICE_NAME])
    for command in systemd_commands:
        subprocess.run(command, check=True)

    result = build_plan(args)
    result["dry_run"] = False
    result["writes"] = writes
    result["feishu_receipt_env"]["status"] = "imported" if receipt_env else "source_missing"
    result["next_steps"] = [
        f"edit {paths.config_file} and fill Feishu/Futu credentials",
        f"create {paths.config_dir / 'futu-sy.env'} with sy Futu connection values",
        f"{paths.launcher_path} config doctor --json",
        f"systemctl status {TIMER_NAME} {EVENING_TIMER_NAME}",
        f"systemctl status {API_SERVICE_NAME}",
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
    systemd = payload.get("systemd", {})
    for name in ("morning", "evening"):
        job = systemd.get(name, {})
        print(f"  {name}: {job.get('on_calendar')} -> {job.get('service')}")
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
    parser.add_argument(
        "--options-monitor-env-file",
        default=DEFAULT_OPTIONS_MONITOR_ENV_FILE,
        help="source env file for the three OM_FEISHU_BOT_* receipt values",
    )
    parser.add_argument("--data-dir", default="/var/lib/portfolio-management/.data", help="runtime state/cache directory")
    parser.add_argument("--reports-dir", default="/var/lib/portfolio-management/reports", help="report output directory")
    parser.add_argument("--systemd-dir", default="/etc/systemd/system", help="systemd unit directory")
    parser.add_argument("--python", default=None, help="Python interpreter for systemd job")
    parser.add_argument("--launcher", default="/usr/local/bin/pm", help="pm launcher path")
    parser.add_argument("--run-user", default=_default_user(), help="systemd User for the oneshot service")
    parser.add_argument(
        "--morning-on-calendar",
        "--on-calendar",
        dest="morning_on_calendar",
        default=DEFAULT_MORNING_ON_CALENDAR,
        help="systemd OnCalendar value for morning Futu sync and NAV",
    )
    parser.add_argument(
        "--evening-on-calendar",
        default=DEFAULT_EVENING_ON_CALENDAR,
        help="systemd OnCalendar value for evening Futu sync",
    )
    parser.add_argument("--overwrite-config", action="store_true", help="overwrite an existing config.yaml")
    parser.add_argument("--enable-timer", action="store_true", help="run systemctl enable --now for both timers")
    parser.add_argument(
        "--enable-api-service",
        action="store_true",
        help="enable and start the loopback-only portfolio HTTP API",
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    payload = apply_install(args) if args.apply else build_plan(args)
    _print_plan(payload, as_json=bool(args.json))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
