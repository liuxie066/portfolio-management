from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import pm
from src.maintenance import release_upgrade as upgrade
from src.maintenance import service_units as units


def prepared(root):
    files = {name: name for name in ["VERSION", "pm", "scripts/serve.py", "scripts/pm.py", "requirements.txt", "src/service/http.py"]}
    files["VERSION"] = root.name
    for name, value in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
    (root / ".venv/bin").mkdir(parents=True)
    (root / ".venv/bin/python").write_text("fixture interpreter")
    (root / ".venv/pyvenv.cfg").write_text("home = fixture")
    receipt = {"schema": upgrade.SCHEMA, "status": "source_verified", "version": root.name,
               "package_sha256": "a" * 64, "files": {name: upgrade._sha(root / name) for name in files}}
    (root / upgrade.RECEIPT).write_text(json.dumps(receipt))
    upgrade.finish_preparation(root)
    return root


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    monkeypatch.setattr(upgrade.sys, "platform", "linux")
    releases = tmp_path / "releases"
    old, previous, target, future = [prepared(releases / version) for version in ["1.0.0", "1.0.1", "1.0.2", "2.0.0"]]
    current = tmp_path / "current"
    current.symlink_to(previous)
    config = tmp_path / "etc"
    config.mkdir()
    (config / "config.yaml").write_text("accounts: []")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / ".data").mkdir()
    (runtime / "reports").mkdir()
    locks = tuple(str(tmp_path / ("lock" + str(i))) for i in range(3))
    for path in locks:
        Path(path).touch()
    monkeypatch.setattr(units, "JOB_LOCKS", locks)
    proc = tmp_path / "proc"
    proc.mkdir()
    monkeypatch.setattr(upgrade, "_PROC_ROOT", proc)
    calls, forbidden = [], []
    case = SimpleNamespace(old=old, previous=previous, target=target, future=future, releases=releases,
                           current=current, config=config, runtime=runtime, proc=proc,
                           calls=calls, forbidden=forbidden, failure=None, fail_once=False,
                           stale_preflight=False, missing_preflight=False, invocation="before",
                           jobs_busy=False, unit_exec={}, timers={u: "active" for u in units.TIMERS})
    case.timers[units.TIMERS[-1]] = "inactive"

    def process_paths(target_path):
        for pid in ["100", "101"]:
            p = proc / pid
            p.mkdir(exist_ok=True)
            for name in ["cwd", "exe"]:
                link = p / name
                link.unlink(missing_ok=True)
                link.symlink_to(target_path if name == "cwd" else target_path / ".venv/bin/python")
            (p / "fd").mkdir(exist_ok=True)
            (p / "maps").write_text("")
            script = "scripts/serve.py" if pid == "100" else "scripts/pm.py"
            (p / "cmdline").write_bytes((str(current / ".venv/bin/python") + "\0" + str(current / script) + "\0").encode())
            if case.failure == "cmdline" and target_path == target:
                (p / "cmdline").write_bytes(b"/usr/bin/python\0/tmp/other.py\0")
            (p / "environ").write_bytes(("PORTFOLIO_CONFIG_FILE=" + str(config / "config.yaml") + "\0PM_DATA_DIR=" + str(runtime / ".data") + "\0PM_REPORTS_DIR=" + str(runtime / "reports") + "\0").encode())

    process_paths(previous)

    def result(command, stdout="", code=0):
        return subprocess.CompletedProcess(command, code, stdout, "")

    def run(command, **kwargs):
        calls.append(command)
        assert kwargs["timeout"] == 60
        if command[0] == "systemctl":
            action = command[1]
            names = command[2:]
            if action == "show":
                unit = names[0]
                if "--property=InvocationID" in names:
                    return result(command, case.invocation)
                active = case.timers[unit] if unit in units.TIMERS else "active" if unit in units.SERVICES else "active" if case.jobs_busy else "inactive"
                script = "scripts/serve.py" if unit == units.API_SERVICE_NAME else "scripts/pm.py"
                state = {"LoadState": "loaded", "ActiveState": active, "SubState": "running" if unit in units.SERVICES else "waiting",
                         "MainPID": "100" if unit == units.API_SERVICE_NAME else "101" if unit in units.SERVICES else "0",
                         "UnitFileState": "disabled" if unit == units.TIMERS[-1] else "enabled",
                         "WorkingDirectory": str(current), "ExecStart": case.unit_exec.get(unit, str(current / ".venv/bin/python") + " " + str(current / script))}
                return result(command, "\n".join(k + "=" + v for k, v in state.items()))
            if action in {"start", "stop"}:
                assert set(names) <= {*units.TIMERS, *units.SERVICES, units.FEISHU_PREFLIGHT_SERVICE_NAME}
                if (case.failure == "restart" and unit_or_worker(names) and action == "start"
                        and current.resolve() == target and not case.fail_once):
                    case.fail_once = True
                    return result(command, code=1)
                for unit in names:
                    if unit in units.TIMERS:
                        if case.failure == "timer" and action == "start" and current.resolve() == target:
                            return result(command, code=1)
                        case.timers[unit] = "active" if action == "start" else "inactive"
                if units.FEISHU_PREFLIGHT_SERVICE_NAME in names and not case.stale_preflight:
                    case.invocation = "new-" + current.resolve().name
                if unit_or_worker(names) and action == "start":
                    process_paths(previous if case.failure == "cwd" and current.resolve() == target else current.resolve())
                return result(command)
        if command[0] == "journalctl":
            rows = [{"_PID": pid, "_SYSTEMD_INVOCATION_ID": case.invocation, "MESSAGE": '"success": true,'} for pid in ["1", "2"]]
            if case.missing_preflight:
                rows.pop()
            rows.append({"UNIT": units.FEISHU_PREFLIGHT_SERVICE_NAME, "JOB_RESULT": "done"})
            return result(command, "\n".join(json.dumps(row) for row in rows))
        if command == [str(target / ".venv/bin/python"), "-I", "-m", "pip", "check"]:
            return result(command)
        forbidden.append(command)
        raise AssertionError("unexpected subprocess")

    def urlopen(url, **kwargs):
        assert url == "http://127.0.0.1:8765/health" and kwargs == {"timeout": 1}
        if case.failure == "health" and current.resolve() == target:
            raise OSError("fixture health unavailable")
        return io.BytesIO(b'{"success":true,"status":"ok"}')

    monkeypatch.setattr(upgrade.subprocess, "run", run)
    monkeypatch.setattr(upgrade, "urlopen", urlopen)
    # Advance the health retry budget without a ten-second fixture delay.
    clock = [0]
    monkeypatch.setattr(upgrade.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(upgrade.time, "sleep", lambda delay: clock.__setitem__(0, clock[0] + delay))
    case.arguments = {"current_link": current, "release_dir": target, "config_dir": config, "runtime_dir": runtime}
    case.preview = lambda: upgrade.activate_release(**case.arguments)
    case.apply = lambda digest: upgrade.activate_release(**case.arguments, apply=True, confirm=True, expected_plan_sha256=digest)
    return case


def unit_or_worker(names):
    return bool(set(names) & set(units.SERVICES))


def test_installed_listener_launcher_can_upgrade_without_unit_rewrite(deployment):
    from scripts import install_linux

    c = deployment
    args = install_linux.build_parser().parse_args(["--app-dir", str(c.current), "--launcher", "/usr/local/bin/pm"])
    text = install_linux.render_holdings_event_service_unit(install_linux.build_paths(args), run_user="portfolio")
    execution = next(line.removeprefix("ExecStart=") for line in text.splitlines() if line.startswith("ExecStart="))
    assert "/usr/local/bin/pm events listen" in execution
    assert "scripts/pm.py" not in execution
    c.unit_exec[units.HOLDINGS_EVENT_SERVICE_NAME] = execution
    preview = c.preview()
    result = c.apply(preview["plan_sha256"])
    assert result["success"] and result["timers_restored"]
    assert c.current.resolve() == c.target
    assert not c.forbidden


@pytest.mark.parametrize("pid", ["100", "101"])
def test_matching_unit_text_cannot_hide_different_running_entry(deployment, pid):
    c = deployment
    (c.proc / pid / "cmdline").write_bytes(b"/usr/bin/python\0/tmp/other.py\0")
    with pytest.raises(ValueError, match="stable release entry points"):
        c.preview()
    assert c.current.resolve() == c.previous
    assert not any(command[:2] in [["systemctl", "stop"], ["systemctl", "start"]] for command in c.calls)


def test_unreadable_running_entry_blocks_before_changes(deployment):
    c = deployment
    (c.proc / "101/cmdline").unlink()
    with pytest.raises(FileNotFoundError):
        c.preview()
    assert c.current.resolve() == c.previous
    assert not any(command[:2] in [["systemctl", "stop"], ["systemctl", "start"]] for command in c.calls)


def test_wrong_entry_after_restart_restores_previous_without_cleanup(deployment):
    c = deployment
    preview = c.preview()
    c.failure = "cmdline"
    result = c.apply(preview["plan_sha256"])
    assert result["status"] == "activation_failed_restored"
    assert result["rollback_acceptance"]["ok"] and result["timers_restored"]
    assert c.current.resolve() == c.previous and c.old.is_dir()
    assert "post_upgrade_cleanup" not in result


def test_preview_and_confirmed_activation_through_public_cli(deployment, capsys):
    case = deployment
    argv = ["service", "upgrade", "--current-link", str(case.current), "--release-dir", str(case.target),
            "--config-dir", str(case.config), "--runtime-dir", str(case.runtime), "--json"]
    before = sorted(str(p) for p in case.runtime.iterdir())
    assert pm.main(argv) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["status"] == "dry_run"
    assert sorted(str(p) for p in case.runtime.iterdir()) == before
    assert case.current.resolve() == case.previous
    assert all(c[:2] == ["systemctl", "show"] for c in case.calls)
    assert pm.main([*argv, "--apply", "--confirm", "--expected-plan-sha256", preview["plan_sha256"]]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "upgraded" and result["success"] is True
    assert result["post_upgrade_cleanup"]["deleted_paths"] == [str(case.old)]
    assert not case.old.exists()
    assert all(p.is_dir() for p in [case.previous, case.target, case.future])
    assert case.current.resolve() == case.target
    assert case.timers[units.TIMERS[-1]] == "inactive"
    assert case.forbidden == []
    assert json.loads(Path(result["receipt_path"]).read_text()) == result


@pytest.mark.parametrize("drift", ["config", "target", "current", "timer", "cleanup"])
def test_preview_drift_blocks_all_service_writes(deployment, drift):
    c = deployment
    plan = c.preview()
    if drift == "config":
        (c.config / "config.yaml").write_text("changed: true")
    elif drift == "target":
        (c.target / "scripts/serve.py").write_text("changed")
    elif drift == "current":
        c.current.unlink()
        c.current.symlink_to(c.old)
    elif drift == "timer":
        c.timers[units.TIMERS[0]] = "inactive"
    else:
        (c.old / "data").mkdir()
        (c.old / "data/state.db").write_text("state")
    before = len(c.calls)
    with pytest.raises(ValueError):
        c.apply(plan["plan_sha256"])
    assert all(command[:2] == ["systemctl", "show"] for command in c.calls[before:])
    assert c.old.is_dir()


@pytest.mark.parametrize("index", [0, 1, 2])
def test_every_existing_job_lock_is_respected(deployment, index):
    import fcntl

    c = deployment
    plan = c.preview()
    with open(units.JOB_LOCKS[index], "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = c.apply(plan["plan_sha256"])
    assert result["success"] is False
    assert c.current.resolve() == c.previous and c.old.is_dir()
    assert result["timers_restored"] is True
    assert not any(unit_or_worker(cmd[2:]) and cmd[:2] == ["systemctl", "stop"] for cmd in c.calls)


@pytest.mark.parametrize("failure", ["restart", "health", "cwd"])
def test_acceptance_failure_restores_previous_without_cleanup(deployment, failure):
    c = deployment
    plan = c.preview()
    c.failure = failure
    result = c.apply(plan["plan_sha256"])
    assert result["status"] == "activation_failed_restored"
    assert result["rollback_acceptance"]["ok"] and result["rollback_preflight"]["ok"]
    assert result["timers_restored"]
    assert c.current.resolve() == c.previous and c.old.is_dir()
    assert "post_upgrade_cleanup" not in result


@pytest.mark.parametrize("bad", ["stale", "one_command"])
def test_preflight_requires_fresh_invocation_and_both_commands(deployment, bad):
    c = deployment
    plan = c.preview()
    c.stale_preflight = bad == "stale"
    c.missing_preflight = bad == "one_command"
    result = c.apply(plan["plan_sha256"])
    assert result["success"] is False
    assert c.old.is_dir()
    assert "post_upgrade_cleanup" not in result


def test_timer_restore_failure_does_not_clean_releases(deployment):
    c = deployment
    plan = c.preview()
    c.failure = "timer"
    result = c.apply(plan["plan_sha256"])
    assert result["status"] == "timer_restore_failed" and result["changed"] is True
    assert c.current.resolve() == c.target and c.old.is_dir()
    assert "post_upgrade_cleanup" not in result


@pytest.mark.parametrize("protection", ["state", "empty_state", "cache_state", "nested_venv", "unknown", "reference", "dependency", "config_reference"])
def test_cleanup_retains_state_unknown_and_referenced_releases(deployment, protection):
    c = deployment
    if protection == "state":
        (c.old / "state.sqlite3").write_text("evidence")
    elif protection == "empty_state":
        (c.old / "unknown-state").mkdir()
    elif protection == "cache_state":
        (c.old / "src/__pycache__").mkdir()
        (c.old / "src/__pycache__/real-state.sqlite3").write_text("evidence")
    elif protection == "nested_venv":
        (c.old / "src/.venv").mkdir()
        (c.old / "src/.venv/state.db").write_text("live data")
    elif protection == "unknown":
        (c.old / upgrade.RECEIPT).unlink()
    elif protection == "reference":
        (c.proc / "100/fd/5").symlink_to(c.old / "VERSION")
    elif protection == "config_reference":
        (c.config / "config.yaml").write_text("historical_path: " + str(c.old))
    else:
        folder = c.previous / ".venv/lib/python3.12/site-packages"
        folder.mkdir(parents=True)
        (folder / "dependency.pth").write_text(str(c.old / "src"))
    result = c.apply(c.preview()["plan_sha256"])
    assert result["success"] is True and c.old.is_dir()
    assert result["post_upgrade_cleanup"]["deleted_paths"] == []


def test_cleanup_exception_preserves_success_and_progress_receipt(deployment, monkeypatch):
    c = deployment

    def fail(path):
        raise OSError("fixture permission denied")

    monkeypatch.setattr(upgrade.shutil, "rmtree", fail)
    result = c.apply(c.preview()["plan_sha256"])
    assert result["success"] and result["status"] == "upgraded"
    assert result["post_upgrade_cleanup"]["status"] == "cleanup_failed"
    assert result["post_upgrade_cleanup"]["deleting_path"] == str(c.old)
    assert c.current.resolve() == c.target and c.old.is_dir()
    assert json.loads(Path(result["receipt_path"]).read_text()) == result


def test_preparation_binds_verified_source_and_environment(tmp_path):
    root = prepared(tmp_path / "1.0.0")
    assert upgrade._prepared(root)["status"] == "prepared"
    (root / ".venv/pyvenv.cfg").write_text("different")
    with pytest.raises(ValueError, match="environment changed"):
        upgrade._prepared(root)


def test_apply_requires_confirmation_and_preview_digest(deployment):
    c = deployment
    for kwargs in [{}, {"confirm": True}, {"expected_plan_sha256": "a" * 64}]:
        with pytest.raises(ValueError):
            upgrade.activate_release(**c.arguments, apply=True, **kwargs)
    assert c.calls == []
    assert c.current.resolve() == c.previous


def test_upgrade_lock_contention_has_no_service_side_effects(deployment):
    import fcntl

    c = deployment
    plan = c.preview()
    with (c.runtime / "release-upgrade.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        count = len(c.calls)
        with pytest.raises(BlockingIOError):
            c.apply(plan["plan_sha256"])
    assert all(command[:2] == ["systemctl", "show"] for command in c.calls[count:])
    assert c.current.resolve() == c.previous


def test_busy_oneshot_blocks_switch_and_restores_timers(deployment):
    c = deployment
    c.jobs_busy = True
    result = c.apply(c.preview()["plan_sha256"])
    assert result["success"] is False and result["timers_restored"]
    assert c.current.resolve() == c.previous and c.old.is_dir()


def test_cancellation_after_switch_compensates_then_propagates(deployment, monkeypatch):
    c = deployment
    plan = c.preview()
    preflight = upgrade._preflight

    def cancel_once():
        if c.current.resolve() == c.target:
            raise KeyboardInterrupt()
        return preflight()

    monkeypatch.setattr(upgrade, "_preflight", cancel_once)
    with pytest.raises(KeyboardInterrupt):
        c.apply(plan["plan_sha256"])
    receipt = json.loads(next(c.runtime.glob("release-upgrade-*.json")).read_text())
    assert receipt["status"] == "activation_failed_restored"
    assert receipt["timers_restored"]
    assert c.current.resolve() == c.previous and c.old.is_dir()


@pytest.mark.parametrize("location", ["old_release", "symlink_ancestor"])
def test_runtime_cannot_reside_in_any_release(deployment, tmp_path, location):
    c = deployment
    hidden = c.old / ".venv/runtime"
    hidden.mkdir()
    runtime = hidden
    if location == "symlink_ancestor":
        link = tmp_path / "indirect"
        link.symlink_to(c.old)
        runtime = link / ".venv/runtime"
    with pytest.raises(ValueError, match="outside the entire"):
        upgrade.activate_release(**{**c.arguments, "runtime_dir": runtime})
    assert c.old.is_dir() and c.calls == []


@pytest.mark.parametrize("field", ["config_dir", "runtime_dir"])
def test_parameters_must_bind_running_instance(deployment, tmp_path, field):
    c = deployment
    other = tmp_path / "other"
    other.mkdir()
    (other / "config.yaml").write_text("accounts: []")
    (other / ".data").mkdir()
    (other / "reports").mkdir()
    with pytest.raises(ValueError, match="do not match"):
        upgrade.activate_release(**{**c.arguments, field: other})
    assert not (other / "release-upgrade.lock").exists()
    assert c.current.resolve() == c.previous


def test_failed_compensation_leaves_automatic_triggers_paused(deployment):
    c = deployment
    plan = c.preview()
    c.missing_preflight = True
    result = c.apply(plan["plan_sha256"])
    assert result["status"] == "rollback_failed"
    assert result["timers_restored"] is False
    assert all(value == "inactive" for value in c.timers.values())
    assert "post_upgrade_cleanup" not in result and c.old.is_dir()


@pytest.mark.parametrize("child", [".data", "reports"])
def test_runtime_children_cannot_alias_a_release(deployment, child):
    c = deployment
    destination = c.old / ".venv/live-state"
    destination.mkdir()
    (c.runtime / child).rmdir()
    (c.runtime / child).symlink_to(destination)
    with pytest.raises(ValueError, match="outside the entire"):
        c.preview()
    assert c.old.is_dir()
    assert not any(command[:2] in [["systemctl", "stop"], ["systemctl", "start"]] for command in c.calls)


@pytest.mark.parametrize("transient", ["launcher", "missing_process", "cwd", "starting"])
def test_restart_waits_for_both_worker_identities_before_health(deployment, monkeypatch, transient):
    case = deployment
    original_show, original_health = upgrade._show, upgrade.urlopen
    unstable, health_calls = [], []

    def show(unit):
        info = original_show(unit)
        if case.current.resolve() == case.target and unit == units.HOLDINGS_EVENT_SERVICE_NAME and not unstable:
            unstable.append(unit)
            if transient == "missing_process":
                raise FileNotFoundError("launcher process changed")
            if transient == "launcher":
                info["StableEntrypoint"] = False
            elif transient == "cwd":
                info["MainPID"] = "999"  # cwd not yet observable in fixture /proc
            else:
                info.update(ActiveState="activating", SubState="start")
        return info

    def health(*args, **kwargs):
        assert unstable  # An already healthy HTTP listener cannot bypass process readiness.
        health_calls.append(args)
        return original_health(*args, **kwargs)

    monkeypatch.setattr(upgrade, "_show", show)
    monkeypatch.setattr(upgrade, "urlopen", health)
    result = case.apply(case.preview()["plan_sha256"])
    assert result["status"] == "upgraded" and result["timers_restored"]
    assert len(health_calls) == 1
    assert result["acceptance"]["service_states"][units.HOLDINGS_EVENT_SERVICE_NAME]["StableEntrypoint"]


def test_later_health_success_cannot_reuse_stale_process_identity(deployment, monkeypatch):
    case = deployment
    original_health = upgrade.urlopen
    attempts = []

    def health(*args, **kwargs):
        if case.current.resolve() == case.target:
            attempts.append(args)
            (case.proc / "101/cmdline").write_bytes(b"/usr/bin/python\0/tmp/wrong.py\0")
            raise OSError("initial HTTP startup")
        return original_health(*args, **kwargs)

    monkeypatch.setattr(upgrade, "urlopen", health)
    result = case.apply(case.preview()["plan_sha256"])
    assert result["status"] == "activation_failed_restored"
    assert result["timers_restored"] and result["rollback_acceptance"]["ok"]
    assert len(attempts) == 1
    assert case.current.resolve() == case.previous
