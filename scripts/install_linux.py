#!/usr/bin/env python3
"""Install Linux deployment assets for scheduled portfolio NAV jobs.

The script is intentionally conservative:
- default mode is dry-run;
- it requires and never overwrites an explicitly prepared config.yaml;
- it only enables timers or the loopback API when explicitly requested.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.configuration.feishu_credentials import (  # noqa: E402
    AGENT_APP_SECRET_CREDENTIAL,
    LISTENER_APP_SECRET_CREDENTIAL,
)


SERVICE_NAME = "portfolio-nav-daily.service"
TIMER_NAME = "portfolio-nav-daily.timer"
EVENING_SERVICE_NAME = "portfolio-futu-evening.service"
EVENING_TIMER_NAME = "portfolio-futu-evening.timer"
CASH_FLOW_SERVICE_NAME = "portfolio-cash-flow-scan.service"
CASH_FLOW_TIMER_NAME = "portfolio-cash-flow-scan.timer"
API_SERVICE_NAME = "portfolio-management-api.service"
QUALITY_SERVICE_NAME = "portfolio-quality-refresh.service"
QUALITY_TIMER_NAME = "portfolio-quality-refresh.timer"
RECEIPT_SERVICE_NAME = "portfolio-receipt-dispatch.service"
RECEIPT_TIMER_NAME = "portfolio-receipt-dispatch.timer"
HOLDINGS_EVENT_SERVICE_NAME = "portfolio-holdings-event-listener.service"
FEISHU_PREFLIGHT_SERVICE_NAME = "portfolio-feishu-preflight.service"
DEFAULT_MORNING_ON_CALENDAR = "Mon..Sat *-*-* 08:10:00 Asia/Shanghai"
DEFAULT_EVENING_ON_CALENDAR = "Mon..Fri *-*-* 17:10:00 Asia/Shanghai"
DEFAULT_QUALITY_REFRESH_INTERVAL = "15min"
DEFAULT_RECEIPT_DISPATCH_INTERVAL = "5min"
DEFAULT_CASH_FLOW_ON_CALENDAR = "*-*-* *:00/15:00 Asia/Shanghai"
SCHEDULE_LOCK_FILE = "/var/lock/portfolio-management-scheduled.lock"
QUALITY_LOCK_FILE = "/var/lock/portfolio-management-quality.lock"
RECEIPT_LOCK_FILE = "/var/lock/portfolio-management-receipts.lock"
DEFAULT_OPTIONS_MONITOR_ENV_FILE = "/etc/options-monitor/options-monitor.env"
ENCRYPTED_CREDENTIAL_STORE_DIR = Path("/etc/credstore.encrypted")
FEISHU_CREDENTIAL_NAMES = (
    AGENT_APP_SECRET_CREDENTIAL,
    LISTENER_APP_SECRET_CREDENTIAL,
)
LEGACY_FEISHU_ROLE_ENV_KEYS = frozenset(
    {
        "FEISHU_BITABLE_APP_ID",
        "FEISHU_BITABLE_APP_SECRET",
        "FEISHU_CONVERSATION_APP_ID",
        "FEISHU_CONVERSATION_APP_SECRET",
        "FEISHU_CONVERSATION_OPEN_ID",
        "FEISHU_RECEIPT_APP_ID",
        "FEISHU_RECEIPT_APP_SECRET",
        "FEISHU_RECEIPT_OPEN_ID",
        "OM_FEISHU_BOT_APP_ID",
        "OM_FEISHU_BOT_APP_SECRET",
        "OM_FEISHU_BOT_USER_OPEN_ID",
    }
)
OPTIONS_MONITOR_FEISHU_KEYS = (
    "OM_FEISHU_BOT_APP_ID",
    "OM_FEISHU_BOT_APP_SECRET",
    "OM_FEISHU_BOT_USER_OPEN_ID",
)
PLAINTEXT_FEISHU_SECRET_ENV_KEYS = frozenset(
    {
        "FEISHU_APP_SECRET",
        "FEISHU_AGENT_APP_SECRET",
        "FEISHU_LISTENER_APP_SECRET",
        "FEISHU_BITABLE_APP_SECRET",
        "FEISHU_CONVERSATION_APP_SECRET",
        "FEISHU_RECEIPT_APP_SECRET",
        "OM_FEISHU_BOT_APP_SECRET",
    }
)
CANONICAL_FEISHU_NONSECRET_ENV_KEYS = {
    "FEISHU_AGENT_APP_ID": "feishu.agent.app_id",
    "FEISHU_AGENT_OPEN_ID": "feishu.agent.open_id",
    "FEISHU_LISTENER_APP_ID": "feishu.listener.app_id",
}
YAML_APP_SECRET_KEY_PATTERN = re.compile(
    r"(?:^|[\s{,\-])(?:app_secret|'app_secret'|\"app_secret\")\s*:"
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
        "quality": {
            "read_token": "",
            "instance_id": "portfolio-management-prod",
            "accounts": ["lx", "sy"],
            "onboarded": False,
        },
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
            "profiles": {
                "lx": {
                    "host": "127.0.0.1",
                    "port": 11111,
                    "acc_id": None,
                    "trd_env": "REAL",
                    "trd_market": "HK",
                },
                "sy": {
                    "host": "127.0.0.1",
                    "port": 11112,
                    "acc_id": None,
                    "trd_env": "REAL",
                    "trd_market": "HK",
                },
            },
        },
        "cash_flow": {
            "effects": {
                "cutover_date": None,
                "db_path": str(paths.data_dir / "cash_flow_effects.sqlite3"),
            },
        },
        "finnhub_api_key": "",
        "feishu": {
            "agent": {"app_id": "", "open_id": ""},
            "listener": {"app_id": ""},
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
        "# Feishu App Secrets are delivered only through systemd credentials.\n"
        "# Fill non-secret Feishu routing plus Futu/API settings before enablement.\n\n"
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


def detect_legacy_feishu_role_env_keys(
    path: str | Path,
    *,
    location: str,
) -> list[dict[str, str]]:
    """Report legacy role key names without retaining or returning values."""
    source = _as_path(path)
    if not source.exists():
        return []

    detected = []
    positions: set[str] = set()
    with source.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key = stripped.split("=", 1)[0].strip()
            if key in positions:
                label = (
                    "options-monitor"
                    if location == "options_monitor_env"
                    else "target"
                    if location == "target_env"
                    else location
                )
                raise ValueError(f"duplicate {label} env key: {key}")
            positions.add(key)
            if key in LEGACY_FEISHU_ROLE_ENV_KEYS:
                detected.append({"location": location, "key": key})
    return sorted(detected, key=lambda item: item["key"])


def _env_secret_key_presence(path: Path, *, location: str) -> list[dict[str, str]]:
    if not path.exists():
        return []
    detected = []
    with path.open("r", encoding="utf-8") as source:
        for line in source:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key = stripped.split("=", 1)[0].strip()
            if key in PLAINTEXT_FEISHU_SECRET_ENV_KEYS:
                detected.append({"location": location, "key": key})
    return detected


def _yaml_secret_key_presence(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as source:
        for line in source:
            stripped = line.lstrip()
            if not stripped or stripped.startswith("#") or ":" not in stripped:
                continue
            if YAML_APP_SECRET_KEY_PATTERN.search(stripped):
                return [{"location": "config_file", "key": "app_secret"}]
    return []


def detect_plaintext_feishu_shadows(
    paths: InstallPaths,
    *,
    options_monitor_env_file: str | Path,
) -> list[dict[str, str]]:
    """Report legacy secret key names without retaining or returning values."""

    findings = [
        *_env_secret_key_presence(
            _as_path(options_monitor_env_file),
            location="options_monitor_env",
        ),
        *_env_secret_key_presence(paths.env_file, location="target_env"),
        *_yaml_secret_key_presence(paths.config_file),
    ]
    return sorted(findings, key=lambda item: (item["location"], item["key"]))


def _canonical_nonsecret_env_mapping(path: str | Path) -> dict[str, str]:
    source = _as_path(path)
    if not source.exists():
        return {}
    selected = {}
    with source.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key = stripped.split("=", 1)[0].strip()
            logical_key = CANONICAL_FEISHU_NONSECRET_ENV_KEYS.get(key)
            if logical_key is None:
                continue
            value = stripped.split("=", 1)[1].strip().strip("'\"")
            if value:
                selected[logical_key] = value
    return selected


def verify_feishu_role_mapping(
    config_file: str | Path,
    *,
    env_file: str | Path | None = None,
) -> dict[str, object]:
    """Require explicit canonical non-secret roles before deployment writes."""

    path = _as_path(config_file)
    if not path.is_file():
        raise RuntimeError(
            "Feishu role mapping not verified: create config.yaml with explicit "
            "feishu.agent and feishu.listener mappings before --apply"
        )
    if _yaml_secret_key_presence(path):
        raise RuntimeError(
            "Feishu role mapping not verified: plaintext app_secret in config.yaml"
        )
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        raise RuntimeError(
            "Feishu role mapping not verified: invalid config.yaml"
        ) from None
    feishu = payload.get("feishu") if isinstance(payload, dict) else None
    agent = feishu.get("agent") if isinstance(feishu, dict) else None
    listener = feishu.get("listener") if isinstance(feishu, dict) else None
    configured = {
        "feishu.agent.app_id": (
            agent.get("app_id") if isinstance(agent, dict) else None
        ),
        "feishu.agent.open_id": (
            agent.get("open_id") if isinstance(agent, dict) else None
        ),
        "feishu.listener.app_id": (
            listener.get("app_id") if isinstance(listener, dict) else None
        ),
    }
    env_mapping = (
        _canonical_nonsecret_env_mapping(env_file) if env_file is not None else {}
    )
    conflicts = [
        key
        for key, config_value in configured.items()
        if isinstance(config_value, str)
        and config_value.strip()
        and key in env_mapping
        and config_value.strip() != env_mapping[key]
    ]
    if conflicts:
        raise RuntimeError(
            "Feishu role mapping not verified: conflicting canonical key(s): "
            + ", ".join(conflicts)
        )
    resolved = {
        key: (
            value.strip()
            if isinstance(value, str) and value.strip()
            else env_mapping.get(key)
        )
        for key, value in configured.items()
    }
    missing = [
        key
        for key, value in resolved.items()
        if not isinstance(value, str) or not value.strip()
    ]
    if missing:
        raise RuntimeError(
            "Feishu role mapping not verified: missing canonical key(s): "
            + ", ".join(missing)
        )
    legacy_roles = [
        f"feishu.{role}"
        for role in ("bitable", "conversation", "receipt")
        if isinstance(feishu, dict) and role in feishu
    ]
    return {
        "verified": True,
        "required_keys": list(configured),
        "sources": {
            key: "target_env" if key in env_mapping else "config_file"
            for key in configured
        },
        "legacy_role_keys_detected": legacy_roles,
        "secret_values_read": False,
    }


def render_env_file(
    paths: InstallPaths,
    *,
    existing_content: str | None = None,
) -> str:
    if existing_content is None:
        existing_content = "\n".join([
            f"PORTFOLIO_CONFIG_FILE={paths.config_file}",
            f"PM_DATA_DIR={paths.data_dir}",
            f"PM_REPORTS_DIR={paths.reports_dir}",
            f"PORTFOLIO_PM_BIN={paths.launcher_path}",
            "PM_QUALITY_READ_TOKEN=",
            "PYTHONUNBUFFERED=1",
            "",
        ])

    _env_assignments(existing_content, label="target")
    return existing_content


def render_launcher(paths: InstallPaths) -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail

unset PYTHONHOME
export PYTHONPATH="{paths.app_dir}"
export PORTFOLIO_CONFIG_FILE="${{PORTFOLIO_CONFIG_FILE:-{paths.config_file}}}"
exec "{paths.python_bin}" "{paths.app_dir / "scripts" / "pm.py"}" "$@"
"""


def _render_feishu_credential_directives(*roles: str) -> str:
    credential_by_role = {
        "agent": AGENT_APP_SECRET_CREDENTIAL,
        "listener": LISTENER_APP_SECRET_CREDENTIAL,
    }
    invalid = [role for role in roles if role not in credential_by_role]
    if invalid:
        raise ValueError(f"unsupported Feishu credential role: {invalid[0]}")
    lines = ["Environment=PM_REQUIRE_SECURE_FEISHU_CREDENTIALS=1"]
    lines.extend(
        f"LoadCredentialEncrypted={credential_by_role[role]}"
        for role in roles
    )
    return "\n".join(lines)


def _secure_feishu_exec(command: str, *, unit_name: str) -> str:
    """Make secure mode and the system credential path authoritative at exec."""

    if not unit_name.endswith(".service") or "/" in unit_name:
        raise ValueError("invalid systemd service name for secure Feishu exec")
    return (
        "/usr/bin/env PM_REQUIRE_SECURE_FEISHU_CREDENTIALS=1 "
        f"CREDENTIALS_DIRECTORY=/run/credentials/{unit_name} {command}"
    )


def render_service_unit(paths: InstallPaths, *, run_user: str, mode: str) -> str:
    if mode not in {"morning", "evening"}:
        raise ValueError(f"unsupported scheduled job mode: {mode}")
    description = (
        "portfolio-management morning Futu sync and NAV job"
        if mode == "morning"
        else "portfolio-management evening Futu holdings sync"
    )
    unit_name = SERVICE_NAME if mode == "morning" else EVENING_SERVICE_NAME
    schedule_script = paths.app_dir / "scripts" / "portfolio_scheduled_job.sh"
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
EnvironmentFile={paths.env_file}
{_render_feishu_credential_directives("agent")}
ExecStart={_secure_feishu_exec(f"/usr/bin/flock -n {SCHEDULE_LOCK_FILE} {schedule_script} {mode}", unit_name=unit_name)}
"""


def render_cash_flow_service_unit(paths: InstallPaths, *, run_user: str) -> str:
    return f"""[Unit]
Description=portfolio-management Cash Flow effect discovery
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
User={run_user}
WorkingDirectory={paths.app_dir}
Environment=TZ=Asia/Shanghai
EnvironmentFile={paths.env_file}
{_render_feishu_credential_directives("agent")}
ExecStart={_secure_feishu_exec(f"{paths.launcher_path} cash-flow effects scan --json", unit_name=CASH_FLOW_SERVICE_NAME)}
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
{_render_feishu_credential_directives("agent")}
ExecStart={_secure_feishu_exec(f"{paths.python_bin} {paths.app_dir / 'scripts' / 'serve.py'} --host 127.0.0.1 --port 8765", unit_name=API_SERVICE_NAME)}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
"""


def render_quality_service_unit(paths: InstallPaths, *, run_user: str) -> str:
    return f"""[Unit]
Description=portfolio-management quality artifact refresh
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
User={run_user}
WorkingDirectory={paths.app_dir}
Environment=TZ=Asia/Shanghai
EnvironmentFile={paths.env_file}
{_render_feishu_credential_directives("agent")}
ExecStart={_secure_feishu_exec(f"/usr/bin/flock -n {QUALITY_LOCK_FILE} {paths.launcher_path} quality refresh --json", unit_name=QUALITY_SERVICE_NAME)}
RuntimeMaxSec=300
"""


def render_receipt_service_unit(paths: InstallPaths, *, run_user: str) -> str:
    return f"""[Unit]
Description=portfolio-management durable receipt dispatcher
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
User={run_user}
WorkingDirectory={paths.app_dir}
Environment=TZ=Asia/Shanghai
EnvironmentFile={paths.env_file}
{_render_feishu_credential_directives("agent")}
ExecStart={_secure_feishu_exec(f"/usr/bin/flock -n {RECEIPT_LOCK_FILE} {paths.launcher_path} receipts dispatch --limit 100 --confirm --json", unit_name=RECEIPT_SERVICE_NAME)}
RuntimeMaxSec=60
"""


def render_holdings_event_service_unit(paths: InstallPaths, *, run_user: str) -> str:
    return f"""[Unit]
Description=portfolio-management Feishu holdings and cash-flow event listener
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User={run_user}
WorkingDirectory={paths.app_dir}
Environment=TZ=Asia/Shanghai
EnvironmentFile={paths.env_file}
{_render_feishu_credential_directives("agent", "listener")}
ExecStart={_secure_feishu_exec(f"{paths.launcher_path} events listen --confirm --json", unit_name=HOLDINGS_EVENT_SERVICE_NAME)}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""


def render_feishu_preflight_service_unit(
    paths: InstallPaths,
    *,
    run_user: str,
) -> str:
    return f"""[Unit]
Description=portfolio-management local Feishu credential preflight

[Service]
Type=oneshot
User={run_user}
WorkingDirectory={paths.app_dir}
Environment=TZ=Asia/Shanghai
EnvironmentFile={paths.env_file}
{_render_feishu_credential_directives("agent", "listener")}
ExecStart={_secure_feishu_exec(f"{paths.launcher_path} config doctor --require-secure-feishu --json", unit_name=FEISHU_PREFLIGHT_SERVICE_NAME)}
ExecStart={_secure_feishu_exec(f"{paths.launcher_path} events status --json", unit_name=FEISHU_PREFLIGHT_SERVICE_NAME)}
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


def render_interval_timer_unit(*, interval: str, service_name: str, description: str) -> str:
    return f"""[Unit]
Description={description}

[Timer]
OnBootSec=5min
OnUnitActiveSec={interval}
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
        "cash_flow_service": paths.systemd_dir / CASH_FLOW_SERVICE_NAME,
        "cash_flow_timer": paths.systemd_dir / CASH_FLOW_TIMER_NAME,
        "api_service": paths.systemd_dir / API_SERVICE_NAME,
        "quality_service": paths.systemd_dir / QUALITY_SERVICE_NAME,
        "quality_timer": paths.systemd_dir / QUALITY_TIMER_NAME,
        "receipt_service": paths.systemd_dir / RECEIPT_SERVICE_NAME,
        "receipt_timer": paths.systemd_dir / RECEIPT_TIMER_NAME,
        "holdings_event_service": paths.systemd_dir / HOLDINGS_EVENT_SERVICE_NAME,
        "feishu_preflight_service": (
            paths.systemd_dir / FEISHU_PREFLIGHT_SERVICE_NAME
        ),
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
            "cash_flow_service": str(units["cash_flow_service"]),
            "cash_flow_timer": str(units["cash_flow_timer"]),
            "api_service": str(units["api_service"]),
            "quality_service": str(units["quality_service"]),
            "quality_timer": str(units["quality_timer"]),
            "receipt_service": str(units["receipt_service"]),
            "receipt_timer": str(units["receipt_timer"]),
            "holdings_event_service": str(units["holdings_event_service"]),
            "feishu_preflight_service": str(units["feishu_preflight_service"]),
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
            {
                "path": str(paths.config_file),
                "mode": "0600",
                "overwrite": False,
                "required_before_apply": True,
            },
            {"path": str(paths.env_file), "mode": "0600", "overwrite": False},
            {"path": str(paths.launcher_path), "mode": "0755", "overwrite": True},
            *[
                {"path": str(path), "mode": "0644", "overwrite": True}
                for path in units.values()
            ],
        ],
        "systemd": {
            "enable_timers": bool(args.enable_timer),
            "enable_api_service": bool(args.enable_api_service),
            "enable_quality_timer": bool(args.enable_quality_timer),
            "enable_holdings_event_service": bool(args.enable_holdings_event_listener),
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
            "cash_flow": {
                "timer": CASH_FLOW_TIMER_NAME,
                "service": CASH_FLOW_SERVICE_NAME,
                "on_calendar": args.cash_flow_on_calendar,
                "mode": "scan",
            },
            "api": {
                "service": API_SERVICE_NAME,
                "host": "127.0.0.1",
                "port": 8765,
            },
            "quality": {
                "timer": QUALITY_TIMER_NAME,
                "service": QUALITY_SERVICE_NAME,
                "interval": args.quality_refresh_interval,
            },
            "receipts": {
                "timer": RECEIPT_TIMER_NAME,
                "service": RECEIPT_SERVICE_NAME,
                "interval": args.receipt_dispatch_interval,
            },
            "holdings_events": {
                "service": HOLDINGS_EVENT_SERVICE_NAME,
                "enabled": bool(args.enable_holdings_event_listener),
            },
            "feishu_credentials": {
                "credential_names": list(FEISHU_CREDENTIAL_NAMES),
                "capability": {
                    "required": True,
                    "verified": False,
                    "checks": [
                        "systemd-creds",
                        "systemd-analyze verify",
                        "LoadCredentialEncrypted",
                    ],
                },
                "preflight_service": FEISHU_PREFLIGHT_SERVICE_NAME,
                "preflight_enabled": False,
                "preflight_required_before_activation": True,
            },
            "feishu_role_mapping": {
                "required_before_apply": True,
                "verified": False,
                "required_keys": [
                    "feishu.agent.app_id",
                    "feishu.agent.open_id",
                    "feishu.listener.app_id",
                ],
            },
        },
        "legacy_feishu_role_sources": {
            "source": str(_as_path(args.options_monitor_env_file)),
            "target": str(paths.env_file),
            "detected": [
                *detect_legacy_feishu_role_env_keys(
                    args.options_monitor_env_file,
                    location="options_monitor_env",
                ),
                *detect_legacy_feishu_role_env_keys(
                    paths.env_file,
                    location="target_env",
                ),
            ],
            "copied": False,
            "values_read": False,
        },
        "plaintext_feishu_shadows": {
            "detected": detect_plaintext_feishu_shadows(
                paths,
                options_monitor_env_file=args.options_monitor_env_file,
            ),
            "requires_separate_cleanup": True,
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


def _prepare_runtime_ownership(paths: InstallPaths, *, run_user: str) -> list[str]:
    """Make runtime state writable by the systemd service identity."""
    if os.geteuid() != 0:
        return []
    targets = [paths.data_dir, paths.reports_dir]
    operation_db = paths.data_dir / "pm_operation_state.sqlite3"
    targets.extend([
        candidate
        for candidate in (
            operation_db,
            Path(f"{operation_db}-wal"),
            Path(f"{operation_db}-shm"),
        )
        if candidate.exists()
    ])
    commands = []
    for target in targets:
        command = ["chown", run_user, str(target)]
        subprocess.run(command, check=True)
        commands.append(" ".join(command))
    for directory in (paths.data_dir, paths.reports_dir):
        directory.chmod(0o750)
    return commands


def verify_encrypted_credential_presence(
    *,
    store_dir: Path | None = None,
) -> dict[str, object]:
    """Verify only name and regular-file metadata; never read credential bytes."""

    directory = store_dir or ENCRYPTED_CREDENTIAL_STORE_DIR
    missing_or_invalid = []
    for credential_name in FEISHU_CREDENTIAL_NAMES:
        path = directory / credential_name
        try:
            metadata = path.lstat()
        except OSError:
            missing_or_invalid.append(credential_name)
            continue
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size <= 0
        ):
            missing_or_invalid.append(credential_name)
    if missing_or_invalid:
        raise RuntimeError(
            "missing or invalid encrypted Feishu credential: "
            + ", ".join(missing_or_invalid)
        )
    return {
        "verified": True,
        "credential_names": list(FEISHU_CREDENTIAL_NAMES),
        "values_read": False,
    }


def _render_systemd_credential_capability_probe() -> str:
    return f"""[Unit]
Description=portfolio-management systemd credential capability probe

[Service]
Type=oneshot
{_render_feishu_credential_directives("agent", "listener")}
ExecStart=/bin/true
"""


def verify_systemd_credential_capability(
    *,
    which: Callable[[str], str | None] | None = None,
    runner: Callable[..., object] | None = None,
) -> dict[str, object]:
    """Prove exact unit syntax before any installation target is written."""

    command_path = which or shutil.which
    command_runner = runner or subprocess.run
    if not command_path("systemd-creds"):
        raise RuntimeError(
            "systemd credential capability unavailable: systemd-creds"
        )
    systemd_analyze = command_path("systemd-analyze")
    if not systemd_analyze:
        raise RuntimeError(
            "systemd credential capability unavailable: systemd-analyze"
        )
    try:
        with tempfile.TemporaryDirectory(prefix="pm-systemd-credential-probe-") as tmp:
            unit_path = Path(tmp) / "portfolio-credential-probe.service"
            unit_path.write_text(
                _render_systemd_credential_capability_probe(),
                encoding="utf-8",
            )
            completed = command_runner(
                [systemd_analyze, "verify", str(unit_path)],
                check=False,
                capture_output=True,
                text=True,
            )
    except OSError:
        raise RuntimeError(
            "systemd credential capability unavailable: verify failed"
        ) from None
    if int(getattr(completed, "returncode", 1)) != 0:
        raise RuntimeError(
            "systemd credential capability unavailable: unsupported unit syntax"
        )
    return {
        "required": True,
        "verified": True,
        "checks": [
            "systemd-creds",
            "systemd-analyze verify",
            "LoadCredentialEncrypted",
        ],
    }


def apply_install(args) -> dict:
    paths = build_paths(args)
    units = _unit_paths(paths)
    if args.overwrite_config:
        raise RuntimeError(
            "--overwrite-config is disabled: prepare and verify the canonical "
            "Agent/Listener mapping before --apply"
        )
    rendered_env = render_env_file(paths)
    legacy_role_sources = [
        *detect_legacy_feishu_role_env_keys(
            args.options_monitor_env_file,
            location="options_monitor_env",
        ),
        *detect_legacy_feishu_role_env_keys(
            paths.env_file,
            location="target_env",
        ),
    ]
    role_mapping = verify_feishu_role_mapping(
        paths.config_file,
        env_file=paths.env_file,
    )
    credential_presence = verify_encrypted_credential_presence()
    credential_capability = verify_systemd_credential_capability()
    _mkdirs([paths.config_dir, paths.data_dir, paths.reports_dir, paths.systemd_dir, paths.launcher_path.parent])
    ownership_commands = _prepare_runtime_ownership(paths, run_user=args.run_user)

    writes = {
        str(paths.config_file): "verified_existing",
        str(paths.env_file): _write_text(
            paths.env_file,
            rendered_env,
            mode=0o600,
            overwrite=False,
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
        str(units["cash_flow_service"]): _write_text(
            units["cash_flow_service"],
            render_cash_flow_service_unit(paths, run_user=args.run_user),
            mode=0o644,
            overwrite=True,
        ),
        str(units["cash_flow_timer"]): _write_text(
            units["cash_flow_timer"],
            render_timer_unit(
                on_calendar=args.cash_flow_on_calendar,
                service_name=CASH_FLOW_SERVICE_NAME,
                description="Run portfolio-management Cash Flow scan every 15 minutes",
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
        str(units["quality_service"]): _write_text(
            units["quality_service"],
            render_quality_service_unit(paths, run_user=args.run_user),
            mode=0o644,
            overwrite=True,
        ),
        str(units["quality_timer"]): _write_text(
            units["quality_timer"],
            render_interval_timer_unit(
                interval=args.quality_refresh_interval,
                service_name=QUALITY_SERVICE_NAME,
                description="Refresh portfolio-management quality artifact",
            ),
            mode=0o644,
            overwrite=True,
        ),
        str(units["receipt_service"]): _write_text(
            units["receipt_service"],
            render_receipt_service_unit(paths, run_user=args.run_user),
            mode=0o644,
            overwrite=True,
        ),
        str(units["receipt_timer"]): _write_text(
            units["receipt_timer"],
            render_interval_timer_unit(
                interval=args.receipt_dispatch_interval,
                service_name=RECEIPT_SERVICE_NAME,
                description="Retry portfolio-management durable receipts",
            ),
            mode=0o644,
            overwrite=True,
        ),
        str(units["holdings_event_service"]): _write_text(
            units["holdings_event_service"],
            render_holdings_event_service_unit(paths, run_user=args.run_user),
            mode=0o644,
            overwrite=True,
        ),
        str(units["feishu_preflight_service"]): _write_text(
            units["feishu_preflight_service"],
            render_feishu_preflight_service_unit(
                paths,
                run_user=args.run_user,
            ),
            mode=0o644,
            overwrite=True,
        ),
    }

    activation_requested = any(
        (
            args.enable_timer,
            args.enable_api_service,
            args.enable_quality_timer,
            args.enable_holdings_event_listener,
        )
    )
    systemd_commands = [["systemctl", "daemon-reload"]]
    if activation_requested:
        systemd_commands.append(
            ["systemctl", "start", FEISHU_PREFLIGHT_SERVICE_NAME]
        )
    if args.enable_timer:
        systemd_commands.append([
            "systemctl",
            "enable",
            "--now",
            TIMER_NAME,
            EVENING_TIMER_NAME,
            CASH_FLOW_TIMER_NAME,
            RECEIPT_TIMER_NAME,
        ])
    if args.enable_api_service:
        systemd_commands.append(["systemctl", "enable", "--now", API_SERVICE_NAME])
    if args.enable_quality_timer:
        systemd_commands.append(["systemctl", "enable", "--now", QUALITY_TIMER_NAME])
    if args.enable_holdings_event_listener:
        systemd_commands.append(
            ["systemctl", "enable", "--now", HOLDINGS_EVENT_SERVICE_NAME]
        )
    for command in systemd_commands:
        subprocess.run(command, check=True)

    result = build_plan(args)
    result["dry_run"] = False
    result["writes"] = writes
    result["runtime_ownership"] = ownership_commands
    result["systemd"]["feishu_credentials"]["capability"] = credential_capability
    result["systemd"]["feishu_credentials"]["presence"] = credential_presence
    result["systemd"]["feishu_credentials"]["preflight_run"] = (
        activation_requested
    )
    result["systemd"]["feishu_role_mapping"] = role_mapping
    result["legacy_feishu_role_sources"]["detected"] = legacy_role_sources
    result["next_steps"] = [
        "fill the explicit futu.profiles mappings and cash_flow.effects.cutover_date",
        f"systemctl start {FEISHU_PREFLIGHT_SERVICE_NAME}",
        f"systemctl status {TIMER_NAME} {EVENING_TIMER_NAME} {CASH_FLOW_TIMER_NAME} {RECEIPT_TIMER_NAME}",
        f"systemctl status {API_SERVICE_NAME}",
        f"systemctl status {QUALITY_TIMER_NAME}",
        f"systemctl status {HOLDINGS_EVENT_SERVICE_NAME}",
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
    for name in ("morning", "evening", "cash_flow"):
        job = systemd.get(name, {})
        print(f"  {name}: {job.get('on_calendar')} -> {job.get('service')}")
    credentials = systemd.get("feishu_credentials", {})
    capability = credentials.get("capability", {})
    if credentials:
        print(
            "  feishu credentials: "
            f"required={capability.get('required')}, "
            f"verified={capability.get('verified')}"
        )
    shadows = payload.get("plaintext_feishu_shadows", {}).get("detected", [])
    for shadow in shadows:
        print(
            "  plaintext Feishu shadow: "
            f"{shadow.get('location')}:{shadow.get('key')}"
        )
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
        help=(
            "legacy options-monitor env file scanned for Feishu role key "
            "names only; no values are imported"
        ),
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
    parser.add_argument(
        "--cash-flow-on-calendar",
        default=DEFAULT_CASH_FLOW_ON_CALENDAR,
        help="systemd OnCalendar value for the 15-minute Cash Flow scanner",
    )
    parser.add_argument(
        "--overwrite-config",
        action="store_true",
        help="disabled safety flag; production config is never overwritten",
    )
    parser.add_argument("--enable-timer", action="store_true", help="run systemctl enable --now for all three timers")
    parser.add_argument(
        "--enable-api-service",
        action="store_true",
        help="enable and start the loopback-only portfolio HTTP API",
    )
    parser.add_argument(
        "--enable-quality-timer",
        action="store_true",
        help="enable the independent 15-minute quality artifact refresh timer",
    )
    parser.add_argument(
        "--quality-refresh-interval",
        default=DEFAULT_QUALITY_REFRESH_INTERVAL,
        help="systemd OnUnitActiveSec value for quality refresh",
    )
    parser.add_argument(
        "--receipt-dispatch-interval",
        default=DEFAULT_RECEIPT_DISPATCH_INTERVAL,
        help="systemd OnUnitActiveSec value for durable receipt retry",
    )
    parser.add_argument(
        "--enable-holdings-event-listener",
        action="store_true",
        help=(
            "enable the Feishu holdings long-connection service; requires a "
            "separately completed Feishu activation preflight"
        ),
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    payload = apply_install(args) if args.apply else build_plan(args)
    _print_plan(payload, as_json=bool(args.json))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
