"""Activate a verified, prepared Linux release; maintain history after acceptance.

Package download/dependency installation belong to install.sh. This owner never
changes deployment assets, credentials, configuration, ledgers or timer definitions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from contextlib import ExitStack
from pathlib import Path
from urllib.request import urlopen
from uuid import uuid4

from src.maintenance import service_units as units

RECEIPT = ".pm-prepared-release.json"
SCHEMA = "pm_prepared_release.v1"
VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
BUSY = {"active", "activating", "deactivating", "reloading"}
_PROC_ROOT = Path("/proc")


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _sha(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("unsafe release/configuration file")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _version(root):
    value = (root / "VERSION").read_text().strip()
    if not VERSION.fullmatch(value):
        raise ValueError("a stable release version is required")
    return tuple(map(int, value.split(".")))


def _write(path, payload, *, exclusive=False):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    raw = json.dumps(payload, sort_keys=True, indent=2).encode()
    if exclusive:
        with path.open("xb") as out:
            out.write(raw)
            out.flush()
            os.fsync(out.fileno())
    else:
        temporary = path.with_name(path.name + "." + uuid4().hex)
        with temporary.open("xb") as out:
            out.write(raw)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def finish_preparation(root):
    """Called only after the existing installer's verified extraction/pip check."""
    root = Path(root).resolve(strict=True)
    payload = json.loads((root / RECEIPT).read_text())
    if payload.get("schema") != SCHEMA or payload.get("status") != "source_verified":
        raise ValueError("verified package preparation receipt is required")
    _verify_files(root, payload)
    if not (root / ".venv/bin/python").is_file():
        raise ValueError("prepared interpreter is missing")
    payload.update(status="prepared", venv_config_sha256=_sha(root / ".venv/pyvenv.cfg"))
    _write(root / RECEIPT, payload)


def _verify_files(root, payload):
    files = payload.get("files")
    if not isinstance(files, dict) or not files or len(files) > 5000:
        raise ValueError("release file manifest is invalid")
    required = {"VERSION", "pm", "scripts/serve.py", "scripts/pm.py", "requirements.txt", "src/service/http.py"}
    if not required <= files.keys():
        raise ValueError("release lacks required PM entry points")
    for name, expected in files.items():
        path = Path(name)
        if path.is_absolute() or ".." in path.parts or str(path) != name or name.startswith(".venv/"):
            raise ValueError("unsafe release manifest path")
        target = root / path
        if target.resolve() != target.absolute() or _sha(target) != expected:
            raise ValueError("release source does not match preparation")


def _verify_source_tree(root, files):
    expected = set(files) | {RECEIPT}
    known_directories = {"."}
    for name in expected:
        known_directories.update(str(p) for p in Path(name).parents)
    actual = set()
    def walk_error(error):
        raise error

    for base, directories, names in os.walk(root, followlinks=False, onerror=walk_error):
        if Path(base) == root:
            directories[:] = [d for d in directories if d != ".venv"]
        if any((Path(base) / d).is_symlink() for d in directories):
            raise ValueError("unknown source directory link")
        parent = Path(base).relative_to(root)
        cache = parent.name == "__pycache__" and str(parent.parent) in known_directories
        if str(parent) not in known_directories and not cache:
            raise ValueError("unknown state directory in prepared release")
        for name in names:
            if cache and name.endswith(".pyc") and not (Path(base) / name).is_symlink():
                continue
            actual.add(str((Path(base) / name).relative_to(root)))
        if len(actual) > 5000:
            raise ValueError("release inventory limit")
    if actual != expected:
        raise ValueError("extra state or unknown files in prepared release")


def _prepared(root):
    path = root / RECEIPT
    if path.is_symlink() or path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("invalid preparation receipt")
    value = json.loads(path.read_text())
    if (value.get("schema") != SCHEMA or value.get("status") != "prepared"
            or value.get("version") != (root / "VERSION").read_text().strip()
            or not re.fullmatch(r"[a-f0-9]{64}", str(value.get("package_sha256", "")))):
        raise ValueError("verified, dependency-checked preparation is required")
    _verify_files(root, value)
    _verify_source_tree(root, value["files"])
    if _sha(root / ".venv/pyvenv.cfg") != value.get("venv_config_sha256"):
        raise ValueError("prepared environment changed")
    return value


def _run(command):
    result = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
    if result.returncode:
        # subprocess output may contain provider/configuration evidence; do not expose it.
        raise RuntimeError("upgrade command failed: " + command[0])
    return result.stdout


def _show(unit):
    raw = _run(["systemctl", "show", unit, "--property=LoadState,ActiveState,SubState,MainPID,UnitFileState,WorkingDirectory,ExecStart"])
    result = dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)
    if result.get("LoadState") != "loaded" or not result.get("ActiveState"):
        raise ValueError("required installed PM unit is unavailable")
    execution = result.pop("ExecStart", "")
    result["ExecStartSha256"] = hashlib.sha256(execution.encode()).hexdigest()
    if unit in units.SERVICES:
        root = result.get("WorkingDirectory", "")
        script = "scripts/serve.py" if unit == units.API_SERVICE_NAME else "scripts/pm.py"
        # The installed listener uses the pm launcher, which execs Python.
        # Bind the actual running entry rather than requiring a different unit.
        pid = result.get("MainPID", "0")
        result["StableEntrypoint"] = False
        if root and pid.isdigit() and pid != "0":
            with (_PROC_ROOT / pid / "cmdline").open("rb") as source:
                raw = source.read(65537)
            expected = [str(Path(root) / ".venv/bin/python").encode(), str(Path(root) / script).encode()]
            result["StableEntrypoint"] = len(raw) <= 65536 and raw.endswith(b"\0") and raw.split(b"\0")[:2] == expected
    return result


def _configuration(config_dir):
    files = sorted(p for p in config_dir.iterdir() if p.is_file())
    if not files or not (config_dir / "config.yaml").is_file():
        raise ValueError("existing PM configuration is required")
    return {str(p): _sha(p) for p in files}


def _instance_binding(current_link, config_dir, runtime_dir, states=None):
    states = states or {unit: _show(unit) for unit in units.SERVICES}
    for unit in units.SERVICES:
        state = states[unit]
        if state.get("ActiveState") != "active" or state.get("SubState") != "running":
            raise ValueError("this activation entry requires both existing PM workers running")
        pid = state.get("MainPID", "")
        if not pid.isdigit() or pid == "0":
            raise ValueError("PM worker identity is unavailable")
        proc = _PROC_ROOT / pid
        if (proc / "cwd").resolve(strict=True) != current_link.resolve(strict=True):
            raise ValueError("running PM worker differs from the stable link")
        with (proc / "environ").open("rb") as source:
            raw = source.read(65537)
        if len(raw) > 65536:
            raise ValueError("PM environment observation limit")
        wanted = {b"PORTFOLIO_CONFIG_FILE", b"PM_DATA_DIR", b"PM_REPORTS_DIR"}
        values = {key.decode(): value.decode() for row in raw.split(b"\0")
                  for key, sep, value in [row.partition(b"=")] if sep and key in wanted}
        expected = {"PORTFOLIO_CONFIG_FILE": config_dir / "config.yaml",
                    "PM_DATA_DIR": runtime_dir / ".data", "PM_REPORTS_DIR": runtime_dir / "reports"}
        releases = current_link.resolve(strict=True).parent
        if any(path.resolve(strict=True).is_relative_to(releases) for path in expected.values()):
            raise ValueError("effective configuration/data/reports must be outside the entire releases root")
        if any(not values.get(key) or Path(values[key]).resolve(strict=True) != path.resolve(strict=True)
               for key, path in expected.items()):
            raise ValueError("configuration/runtime parameters do not match the running PM instance")


def _references(roots):
    """Read-only Linux observation; uncertainty protects every candidate."""
    hits = set()
    deadline = time.monotonic() + 10
    for process in _PROC_ROOT.iterdir():
        if not process.name.isdigit():
            continue
        try:
            links = [process / "cwd", process / "exe", *list((process / "fd").iterdir())]
            values = []
            for link in links:
                try:
                    values.append(os.readlink(link))
                except FileNotFoundError:
                    pass
            values.append((process / "maps").read_text())
            for root in roots:
                if any(value == str(root) or str(root) + "/" in value for value in values):
                    hits.add(str(root))
        except FileNotFoundError:
            pass
        if time.monotonic() > deadline:
            raise RuntimeError("reference observation budget exceeded")
    return hits


def _cleanup_plan(releases, current, previous, config_dir):
    deletable, retained = [], []
    for root in sorted(releases.iterdir()):
        reason = "unknown_installation"
        try:
            if root.is_symlink() or not root.is_dir() or not VERSION.fullmatch(root.name):
                retained.append({"path": str(root), "reason": reason})
                continue
            if root in {current, previous}:
                reason = "current_or_actual_previous"
            elif _version(root) >= _version(previous):
                reason = "newer_staged_release"
            else:
                _prepared(root)
                if (root / ".venv").is_symlink():
                    reason = "external_environment"
                else:
                    deletable.append({"path": str(root), "receipt_sha256": _sha(root / RECEIPT)})
                    continue
        except (OSError, ValueError, TypeError, KeyError):
            reason = "unknown_or_changed_installation"
        retained.append({"path": str(root), "reason": reason})
    # A retained environment may explicitly refer to another release. Never
    # reclaim an installation which remains part of that environment.
    try:
        dependencies = []
        for path in config_dir.iterdir():
            if path.is_file():
                if path.is_symlink() or path.stat().st_size > 1024 * 1024:
                    raise ValueError("configuration dependency observation unavailable")
                dependencies.append(path.read_text())
        for item in retained:
            root = Path(item["path"])
            for path in (root / ".venv", root / ".venv/bin/python"):
                if path.is_symlink():
                    dependencies.append(str(path.resolve(strict=True)))
            for folder in (root / ".venv/lib").glob("python*/site-packages"):
                for path in [*folder.glob("*.pth"), *folder.glob("*.egg-link")]:
                    if path.stat().st_size > 65536:
                        raise ValueError("import hook budget exceeded")
                    dependencies.append(path.read_text())
        referenced_dependencies = {item["path"] for item in deletable if any(item["path"] in text for text in dependencies)}
    except (OSError, ValueError):
        referenced_dependencies = {item["path"] for item in deletable}
    for item in deletable[:]:
        if item["path"] in referenced_dependencies:
            deletable.remove(item)
            retained.append({"path": item["path"], "reason": "retained_environment_dependency_or_unknown"})
    try:
        referenced = _references([Path(item["path"]) for item in deletable])
    except (OSError, RuntimeError):
        referenced = {item["path"] for item in deletable}
    for item in deletable[:]:
        if item["path"] in referenced:
            deletable.remove(item)
            retained.append({"path": item["path"], "reason": "referenced_or_observation_unknown"})
    return {"delete": deletable, "retained": retained}


def _snapshot(current_link, release_dir, config_dir, runtime_dir):
    if not current_link.is_symlink():
        raise ValueError("current-link must be an existing stable symlink")
    releases = release_dir.parent.resolve(strict=True)
    previous = current_link.resolve(strict=True)
    if (release_dir.is_symlink() or release_dir.absolute() != release_dir.resolve(strict=True)
            or previous.parent != releases or release_dir == previous
            or _version(release_dir) <= _version(previous) or release_dir.name != (release_dir / "VERSION").read_text().strip()):
        raise ValueError("target must be a newer prepared direct release sibling")
    prepared = _prepared(release_dir)
    states = {unit: _show(unit) for unit in (*units.TIMERS, *units.SERVICES, *units.JOBS)}
    for unit in (*units.SERVICES, *units.JOBS):
        if states[unit].get("WorkingDirectory") != str(current_link):
            raise ValueError("PM services must already bind the stable link")
    if not all(states[unit].get("StableEntrypoint") for unit in units.SERVICES):
        raise ValueError("PM workers must execute the stable release entry points")
    _instance_binding(current_link, config_dir, runtime_dir, states)
    return {"current_link": str(current_link), "previous": str(previous), "target": str(release_dir),
            "prepared_sha256": _digest(prepared), "configuration": _configuration(config_dir),
            "units": states, "cleanup": _cleanup_plan(releases, release_dir, previous, config_dir)}


def _preflight():
    previous_invocation = _run(["systemctl", "show", units.FEISHU_PREFLIGHT_SERVICE_NAME, "--property=InvocationID", "--value"]).strip()
    since = time.time()
    _run(["systemctl", "start", units.FEISHU_PREFLIGHT_SERVICE_NAME])
    raw = _run(["journalctl", "-u", units.FEISHU_PREFLIGHT_SERVICE_NAME, "--since", "@" + str(since), "-o", "json", "--no-pager"])
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    successes = [r for r in rows if r.get("MESSAGE", "").strip() == '"success": true,']
    pids = {r.get("_PID") for r in successes}
    invocations = {r.get("_SYSTEMD_INVOCATION_ID") for r in successes}
    if (len(pids) != 2 or None in pids or len(invocations) != 1 or None in invocations or previous_invocation in invocations
            or not any(r.get("UNIT") == units.FEISHU_PREFLIGHT_SERVICE_NAME and r.get("JOB_RESULT") == "done" for r in rows)):
        raise RuntimeError("fresh two-command credential preflight did not pass")
    return {"ok": True, "invocation_id": next(iter(invocations)), "command_pids": sorted(pids)}


def _accept(target):
    deadline = time.monotonic() + 10
    identity_ready = False
    while time.monotonic() < deadline:
        try:
            states = {unit: _show(unit) for unit in units.SERVICES}
            identity_ready = all(
                info.get("ActiveState") == "active" and info.get("SubState") == "running"
                and info.get("StableEntrypoint")
                and info.get("MainPID", "0").isdigit() and info["MainPID"] != "0"
                and (_PROC_ROOT / info["MainPID"] / "cwd").resolve(strict=True) == target
                for info in states.values()
            )
        except (FileNotFoundError, ProcessLookupError):
            identity_ready = False
        if identity_ready:
            try:
                with urlopen("http://127.0.0.1:8765/health", timeout=1) as response:
                    raw = response.read(65537)
                    if len(raw) > 65536:
                        raise ValueError("health response limit")
                    health = json.loads(raw)
                if health.get("success") is True and health.get("status") == "ok":
                    return {"ok": True, "service_states": states}
            except (OSError, ValueError):
                pass
        time.sleep(0.25)
    if not identity_ready:
        raise RuntimeError("service process does not run the accepted release")
    raise RuntimeError("accepted release health check failed")


def _switch(link, target):
    temporary = link.with_name(link.name + ".upgrade-" + uuid4().hex)
    try:
        temporary.symlink_to(target)
        os.replace(temporary, link)
    finally:
        temporary.unlink(missing_ok=True)


def _locked(stack, path, *, create=False):
    import fcntl

    # Existing job locks keep their provisioned ownership and access modes.
    flags = os.O_RDWR | os.O_CREAT if create else os.O_RDONLY
    descriptor = os.open(path, flags | os.O_NOFOLLOW, 0o600)
    stream = stack.enter_context(os.fdopen(descriptor, "a+" if create else "r"))
    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)


def activate_release(*, current_link, release_dir, config_dir, runtime_dir, apply=False,
                     confirm=False, expected_plan_sha256=None):
    """Read first. Confirmed apply binds a preview, never invokes business APIs."""
    if not sys.platform.startswith("linux"):
        raise ValueError("controlled activation currently requires Linux/systemd")
    current_link, release_dir, config_dir, runtime_dir = [Path(p).absolute() for p in
                                                        (current_link, release_dir, config_dir, runtime_dir)]
    if not current_link.is_symlink() or not runtime_dir.is_dir() or not config_dir.is_dir():
        raise ValueError("existing, separate runtime directory is required")
    if release_dir.is_symlink() or release_dir != release_dir.resolve(strict=True):
        raise ValueError("target must be a physical release directory")
    release_dir = release_dir.resolve(strict=True)
    config_dir, runtime_dir = config_dir.resolve(strict=True), runtime_dir.resolve(strict=True)
    releases = release_dir.parent
    if any(path.is_relative_to(releases) for path in (config_dir, runtime_dir)):
        raise ValueError("configuration/runtime must be outside the entire releases root")
    if apply and (not confirm or not expected_plan_sha256):
        raise ValueError("apply requires explicit confirmation and preview digest")
    _instance_binding(current_link, config_dir, runtime_dir)
    with ExitStack() as stack:
        if apply:
            _locked(stack, runtime_dir / "release-upgrade.lock", create=True)
        plan = _snapshot(current_link, release_dir, config_dir, runtime_dir)
        digest = _digest(plan)
        out = {"success": True, "status": "dry_run", "changed": False, "plan_sha256": digest, "plan": plan,
               "planned_operations": ["pause active PM timers and acquire existing job locks", "stop PM workers and switch stable link",
                                      "verify fresh credential preflight, process paths and API health", "restore original timer activation",
                                      "maintain older verified releases, protecting current and actual previous"]}
        if not apply:
            return out
        if digest != expected_plan_sha256:
            raise ValueError("upgrade preview changed; create a new preview")
        receipt = runtime_dir / ("release-upgrade-" + uuid4().hex + ".json")
        out.update(status="activating", receipt_path=str(receipt), success=False)
        _write(receipt, out, exclusive=True)
        active_timers = [u for u in units.TIMERS if plan["units"][u]["ActiveState"] == "active"]
        paused = stopped = switched = False
        cancellation = None
        job_stack = ExitStack()
        previous = Path(plan["previous"])
        try:
            paused = True
            if active_timers:
                _run(["systemctl", "stop", *active_timers])
            for path in units.JOB_LOCKS:
                _locked(job_stack, path)
            if any(_show(unit)["ActiveState"] in BUSY for unit in units.JOBS):
                raise RuntimeError("PM write job is still active")
            if _configuration(config_dir) != plan["configuration"] or current_link.resolve() != previous:
                raise ValueError("configuration/current release changed")
            _prepared(release_dir)
            _run([str(release_dir / ".venv/bin/python"), "-I", "-m", "pip", "check"])
            stopped = True
            _run(["systemctl", "stop", *units.SERVICES])
            _switch(current_link, release_dir)
            switched = True
            out["preflight"] = _preflight()
            _run(["systemctl", "start", *units.SERVICES])
            out["acceptance"] = _accept(release_dir)
            if _configuration(config_dir) != plan["configuration"]:
                raise ValueError("production configuration changed during activation")
            out.update(success=True, status="upgraded", changed=True)
        except (Exception, KeyboardInterrupt, SystemExit) as exc:  # noqa: BLE001 -- compensate service transitions before propagating cancellation
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                cancellation = exc
            out.update(success=False, status="activation_failed", error=type(exc).__name__)
            if stopped:
                try:
                    _run(["systemctl", "stop", *units.SERVICES])
                    if current_link.resolve() == release_dir:
                        _switch(current_link, previous)
                    if current_link.resolve() != previous:
                        raise RuntimeError("current link changed outside this transition")
                    out["rollback_preflight"] = _preflight()
                    _run(["systemctl", "start", *units.SERVICES])
                    out["rollback_acceptance"] = _accept(previous)
                    out.update(status="activation_failed_restored", changed=False)
                except Exception as rollback:  # noqa: BLE001 -- preserve failed compensation evidence
                    out.update(status="rollback_failed", rollback_error=type(rollback).__name__, changed=switched)
        finally:
            job_stack.close()
            try:
                if out["status"] == "rollback_failed":
                    out.update(timers_restored=False, timers_left_paused=active_timers)
                    raise RuntimeError("rollback is unverified; automatic triggers remain paused")
                if paused and active_timers:
                    _run(["systemctl", "start", *active_timers])
                for timer in units.TIMERS:
                    state = _show(timer)
                    original = plan["units"][timer]
                    if (state["ActiveState"], state.get("UnitFileState")) != (original["ActiveState"], original.get("UnitFileState")):
                        raise RuntimeError("original timer activation was not restored")
                out["timers_restored"] = True
            except Exception as exc:  # noqa: BLE001 -- preserve timer restoration failures
                out.update(success=False, timers_restored=False, timer_error=type(exc).__name__)
                if out["status"] != "rollback_failed":
                    out["status"] = "timer_restore_failed"
        if out["success"]:
            cleanup = {"ok": True, "deleted_paths": [], "status": "cleaned"}
            try:
                fresh = _cleanup_plan(release_dir.parent, release_dir, previous, config_dir)
                if fresh != plan["cleanup"]:
                    raise ValueError("cleanup plan changed")
                for item in fresh["delete"]:
                    path = Path(item["path"])
                    if current_link.resolve() != release_dir or path in {release_dir, previous} or path.parent != release_dir.parent or path.is_symlink():
                        raise ValueError("unsafe release deletion")
                    _prepared(path)
                    if _references([path]):
                        raise ValueError("release acquired an active reference")
                    cleanup["deleting_path"] = str(path)
                    _write(receipt, {**out, "post_upgrade_cleanup": cleanup})
                    shutil.rmtree(path)
                    if path.exists():
                        raise RuntimeError("release deletion incomplete")
                    cleanup["deleted_paths"].append(str(path))
                    cleanup.pop("deleting_path")
                    _write(receipt, {**out, "post_upgrade_cleanup": cleanup})
            except Exception as exc:  # noqa: BLE001 -- cleanup must not invalidate an accepted upgrade
                cleanup.update(ok=False, status="cleanup_failed", error=type(exc).__name__)
            out["post_upgrade_cleanup"] = cleanup
        out["active_path"] = str(current_link.resolve())
        _write(receipt, out)
        if cancellation is not None:
            raise cancellation
        return out


def cli(argv):
    parser = argparse.ArgumentParser(prog="pm service upgrade")
    for flag in ("current-link", "release-dir", "config-dir", "runtime-dir"):
        parser.add_argument("--" + flag, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--expected-plan-sha256")
    parser.add_argument("--json", action="store_true")
    args = vars(parser.parse_args(argv))
    args.pop("json")
    try:
        result = activate_release(**args)
    except (OSError, ValueError, RuntimeError) as exc:
        result = {"success": False, "status": "upgrade_blocked", "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["success"] else 1
