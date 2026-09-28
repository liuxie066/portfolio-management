#!/usr/bin/env python3
"""Render per-user launchd assets; never load or start jobs."""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml


LABEL_PREFIX = "com.liuxie.portfolio-management"
PLUTIL = "/usr/bin/plutil"
SENSITIVE_CONFIG_KEYS = {"app_secret", "read_token", "user_token", "finnhub_api_key"}


class InstallError(ValueError):
    pass


def _owned_path(path: Path, *, directory: bool, executable: bool = False, allow_symlink: bool = False) -> None:
    if not path.is_absolute():
        raise InstallError(f"path must be absolute: {path}")
    try:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) and allow_symlink and info.st_uid == os.geteuid():
            info = path.stat()
    except OSError:
        raise InstallError(f"required path is unavailable: {path}") from None
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if stat.S_ISLNK(info.st_mode) or not expected(info.st_mode) or info.st_uid != os.geteuid():
        raise InstallError(f"invalid type or owner: {path}")
    if executable and not (info.st_mode & stat.S_IXUSR):
        raise InstallError(f"path is not executable: {path}")


def _contains_plaintext_secret(node: object, seen: set[int] | None = None) -> bool:
    if not isinstance(node, (dict, list)):
        return False
    seen = set() if seen is None else seen
    if id(node) in seen:
        return False
    seen.add(id(node))
    if isinstance(node, dict):
        return any(
            (str(key).lower().split(".")[-1] in SENSITIVE_CONFIG_KEYS and value not in (None, ""))
            or _contains_plaintext_secret(value, seen)
            for key, value in node.items()
        )
    return any(_contains_plaintext_secret(value, seen) for value in node)


def _paths(args: argparse.Namespace) -> dict[str, Path]:
    result = {
        name: Path(getattr(args, name)).expanduser()
        for name in (
            "app_dir", "config_file", "data_dir", "reports_dir", "log_dir",
            "launch_agents_dir", "keychain_file",
        )
    }
    result["python"] = result["app_dir"] / ".venv/bin/python"
    result["pm"] = result["app_dir"] / "pm"
    return result


def _validate(args: argparse.Namespace) -> dict[str, Path]:
    if sys.platform != "darwin":
        raise InstallError("macOS launchd assets require Darwin")
    paths = _paths(args)
    app_dir = paths["app_dir"]
    if Path("/Volumes") in app_dir.resolve().parents:
        raise InstallError("runtime checkout must be on a stable local path")
    for name in ("app_dir", "data_dir", "reports_dir", "log_dir", "launch_agents_dir"):
        _owned_path(paths[name], directory=True)
    for name in ("config_file", "keychain_file", "python", "pm"):
        _owned_path(paths[name], directory=False, executable=name in {"python", "pm"}, allow_symlink=name == "python")
    _owned_path(app_dir / ".venv/pyvenv.cfg", directory=False)
    for relative in (
        "scripts/pm.py", "scripts/serve.py", "scripts/portfolio_scheduled_job.sh",
        "scripts/macos_preflight.sh",
    ):
        _owned_path(app_dir / relative, directory=False)
    if stat.S_IMODE(paths["log_dir"].stat().st_mode) & 0o077:
        raise InstallError("log directory must have mode 0700 or stricter")
    if stat.S_IMODE(paths["data_dir"].stat().st_mode) & 0o077:
        raise InstallError("data directory must have mode 0700 or stricter")
    try:
        config = yaml.safe_load(paths["config_file"].read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError):
        raise InstallError("configuration file cannot be parsed") from None
    if not isinstance(config, dict) or _contains_plaintext_secret(config):
        raise InstallError("configuration must be a mapping without plaintext secrets")
    return paths


def _jobs(paths: dict[str, Path]) -> dict[str, dict]:
    app = paths["app_dir"]
    pm = str(paths["pm"])
    state = paths["data_dir"]
    lockf = "/usr/bin/lockf"
    env = {
        "PORTFOLIO_CONFIG_FILE": str(paths["config_file"]),
        "PM_DATA_DIR": str(state),
        "PM_REPORTS_DIR": str(paths["reports_dir"]),
        "TZ": "Asia/Shanghai",
        "PM_REQUIRE_SECURE_FEISHU_CREDENTIALS": "1",
        "PM_CREDENTIAL_BACKEND": "keychain",
        "PM_KEYCHAIN_FILE": str(paths["keychain_file"]),
        "PM_REQUIRE_VENV": "1",
    }
    schedule_env = {**env, "PORTFOLIO_PM_BIN": pm}
    jobs = {
        "api": ([str(paths["python"]), str(app / "scripts/serve.py"), "--host", "127.0.0.1", "--port", "8765"], env, {"RunAtLoad": True, "KeepAlive": True}),
        "events": ([pm, "events", "listen", "--confirm", "--json"], env, {"RunAtLoad": True, "KeepAlive": True}),
        "nav-morning": ([lockf, "-k", "-t", "0", str(state / "scheduled.lock"), str(app / "scripts/portfolio_scheduled_job.sh"), "morning"], schedule_env, {"StartCalendarInterval": [{"Weekday": day, "Hour": 8, "Minute": 10} for day in range(1, 7)]}),
        "futu-evening": ([lockf, "-k", "-t", "0", str(state / "scheduled.lock"), str(app / "scripts/portfolio_scheduled_job.sh"), "evening"], schedule_env, {"StartCalendarInterval": [{"Weekday": day, "Hour": 17, "Minute": 10} for day in range(1, 6)]}),
        "cash-flow-scan": ([pm, "cash-flow", "effects", "scan", "--json"], env, {"StartCalendarInterval": [{"Minute": minute} for minute in (0, 15, 30, 45)]}),
        "receipt-dispatch": ([lockf, "-k", "-t", "0", str(state / "receipts.lock"), pm, "receipts", "dispatch", "--limit", "100", "--confirm", "--json"], env, {"StartInterval": 300}),
        "quality-refresh": ([lockf, "-k", "-t", "0", str(state / "quality.lock"), pm, "quality", "refresh", "--json"], env, {"StartInterval": 900}),
        "preflight": (["/bin/bash", str(app / "scripts/macos_preflight.sh")], env, {}),
        "quality-preflight": ([pm, "config", "quality-preflight", "--json"], env, {}),
        "futu-preflight": ([pm, "config", "futu-preflight", "--json"], env, {}),
    }
    result = {}
    for suffix, (arguments, environment, trigger) in jobs.items():
        result[suffix] = {
            "Label": f"{LABEL_PREFIX}.{suffix}",
            "ProgramArguments": arguments,
            "WorkingDirectory": str(app),
            "EnvironmentVariables": environment,
            "Umask": 0o077,
            "StandardOutPath": str(paths["log_dir"] / f"{suffix}.out.log"),
            "StandardErrorPath": str(paths["log_dir"] / f"{suffix}.err.log"),
            **trigger,
        }
    return result


def build_plan(args: argparse.Namespace) -> tuple[dict[str, Path], dict[str, bytes]]:
    paths = _validate(args)
    rendered = {}
    for suffix, job in _jobs(paths).items():
        blob = plistlib.dumps(job, sort_keys=True)
        if plistlib.loads(blob) != job:
            raise InstallError(f"plist round-trip failed: {suffix}")
        checked = subprocess.run(
            [PLUTIL, "-lint", "-"],
            input=blob, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            check=False,
        )
        if checked.returncode != 0:
            raise InstallError(f"plist validation failed: {suffix}")
        rendered[suffix] = blob
    return paths, rendered


def apply_install(paths: dict[str, Path], rendered: dict[str, bytes]) -> dict[str, str]:
    result = {}
    for suffix, blob in rendered.items():
        target = paths["launch_agents_dir"] / f"{LABEL_PREFIX}.{suffix}.plist"
        if target.exists() or target.is_symlink():
            _owned_path(target, directory=False)
            if target.read_bytes() == blob:
                result[suffix] = "unchanged"
                continue
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix=f".{target.name}.", delete=False) as handle:
                temporary = Path(handle.name)
                os.fchmod(handle.fileno(), 0o600)
                handle.write(blob)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            result[suffix] = "written"
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return result


def build_parser() -> argparse.ArgumentParser:
    home = Path.home()
    base = home / ".portfolio-management"
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--plan", action="store_true", help="validate and render without writing (default)")
    modes.add_argument("--apply", action="store_true", help="write plists only; never load jobs")
    parser.add_argument("--app-dir", default=str(base / "current"))
    parser.add_argument("--config-file", default=str(base / "config.yaml"))
    parser.add_argument("--data-dir", default=str(base / ".data"))
    parser.add_argument("--reports-dir", default=str(base / "reports"))
    parser.add_argument("--log-dir", default=str(home / "Library/Logs/portfolio-management"))
    parser.add_argument("--launch-agents-dir", default=str(home / "Library/LaunchAgents"))
    parser.add_argument("--keychain-file", default=str(home / "Library/Keychains/login.keychain-db"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        paths, rendered = build_plan(args)
        written = apply_install(paths, rendered) if args.apply else None
    except (InstallError, OSError) as exc:
        print(f"install_macos: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({
        "success": True,
        "mode": "apply" if args.apply else "plan",
        "jobs": [
            {"label": f"{LABEL_PREFIX}.{suffix}",
             "path": str(paths["launch_agents_dir"] / f"{LABEL_PREFIX}.{suffix}.plist"),
             "program_arguments": plistlib.loads(blob)["ProgramArguments"],
             "status": None if written is None else written[suffix]}
            for suffix, blob in rendered.items()
        ],
        "loaded": False,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
