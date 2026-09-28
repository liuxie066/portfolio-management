from __future__ import annotations

import json
import plistlib
import sys
from pathlib import Path

import pytest

from scripts import install_macos


def _fixture_paths(tmp_path: Path, monkeypatch) -> tuple[object, dict[str, Path]]:
    if sys.platform != "darwin":
        monkeypatch.setattr(install_macos.sys, "platform", "darwin")
        monkeypatch.setattr(install_macos, "PLUTIL", "/usr/bin/true")
    app = tmp_path / "current"
    (app / ".venv/bin").mkdir(parents=True)
    (app / "scripts").mkdir()
    for name in (
        "pm", "scripts/pm.py", "scripts/serve.py",
        "scripts/portfolio_scheduled_job.sh", "scripts/macos_preflight.sh",
        ".venv/bin/python", ".venv/pyvenv.cfg",
    ):
        path = app / name
        path.write_text("placeholder\n")
        path.chmod(0o700)
    config = tmp_path / "config.yaml"
    config.write_text("feishu:\n  agent:\n    app_id: cli_test\n")
    config.chmod(0o600)
    keychain = tmp_path / "login.keychain-db"
    keychain.write_bytes(b"test")
    keychain.chmod(0o600)
    for name in ("data", "reports", "logs", "LaunchAgents"):
        path = tmp_path / name
        path.mkdir()
        path.chmod(0o700)
    paths = {
        "app": app, "config": config, "keychain": keychain,
        "data": tmp_path / "data", "reports": tmp_path / "reports",
        "logs": tmp_path / "logs", "agents": tmp_path / "LaunchAgents",
    }
    args = install_macos.build_parser().parse_args([
        "--plan", "--app-dir", str(app), "--config-file", str(config),
        "--data-dir", str(paths["data"]), "--reports-dir", str(paths["reports"]),
        "--log-dir", str(paths["logs"]),
        "--launch-agents-dir", str(paths["agents"]),
        "--keychain-file", str(keychain),
    ])
    return args, paths


def test_macos_plan_covers_all_jobs_without_loading_or_secrets(tmp_path, monkeypatch):
    args, paths = _fixture_paths(tmp_path, monkeypatch)
    _, rendered = install_macos.build_plan(args)
    assert set(rendered) == {
        "api", "events", "nav-morning", "futu-evening", "cash-flow-scan",
        "receipt-dispatch", "quality-refresh", "preflight", "quality-preflight",
        "futu-preflight",
    }
    assert list(paths["agents"].iterdir()) == []

    jobs = {name: plistlib.loads(blob) for name, blob in rendered.items()}
    for name, job in jobs.items():
        env = job["EnvironmentVariables"]
        assert job["Label"] == f"{install_macos.LABEL_PREFIX}.{name}"
        assert job["Umask"] == 0o077
        assert env["PM_CREDENTIAL_BACKEND"] == "keychain"
        assert env["PM_KEYCHAIN_FILE"] == str(paths["keychain"])
        assert env["PM_REQUIRE_VENV"] == "1"
        assert "CREDENTIALS_DIRECTORY" not in env
        assert "PM_QUALITY_READ_TOKEN" not in env
        assert all("APP_SECRET" not in key for key in env)
        if name in {"api", "events"}:
            assert job["RunAtLoad"] is True
        else:
            assert "RunAtLoad" not in job
    assert jobs["api"]["ProgramArguments"][0] == str(paths["app"] / ".venv/bin/python")
    assert jobs["events"]["ProgramArguments"][1:3] == ["events", "listen"]
    assert jobs["nav-morning"]["EnvironmentVariables"]["PORTFOLIO_PM_BIN"] == str(paths["app"] / "pm")
    assert jobs["nav-morning"]["ProgramArguments"][-1] == "morning"
    assert jobs["futu-evening"]["ProgramArguments"][-1] == "evening"
    assert jobs["nav-morning"]["ProgramArguments"][4] == jobs["futu-evening"]["ProgramArguments"][4]
    assert len(jobs["nav-morning"]["StartCalendarInterval"]) == 6
    assert len(jobs["futu-evening"]["StartCalendarInterval"]) == 5
    assert [item["Minute"] for item in jobs["cash-flow-scan"]["StartCalendarInterval"]] == [0, 15, 30, 45]
    assert jobs["receipt-dispatch"]["StartInterval"] == 300
    assert jobs["quality-refresh"]["StartInterval"] == 900
    assert jobs["quality-preflight"]["ProgramArguments"][1:3] == ["config", "quality-preflight"]
    assert jobs["futu-preflight"]["ProgramArguments"][1:3] == ["config", "futu-preflight"]


def test_macos_apply_writes_private_plists_only_and_is_idempotent(tmp_path, monkeypatch):
    args, paths = _fixture_paths(tmp_path, monkeypatch)
    prepared, rendered = install_macos.build_plan(args)
    first = install_macos.apply_install(prepared, rendered)
    assert set(first.values()) == {"written"}
    second = install_macos.apply_install(prepared, rendered)
    assert set(second.values()) == {"unchanged"}
    assert len(list(paths["agents"].glob("*.plist"))) == 10
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in paths["agents"].glob("*.plist"))


def test_macos_plan_rejects_plaintext_config_and_missing_venv(tmp_path, monkeypatch):
    args, paths = _fixture_paths(tmp_path, monkeypatch)
    paths["config"].write_text("feishu:\n  agent:\n    app_secret: forbidden\n")
    with pytest.raises(install_macos.InstallError, match="without plaintext secrets"):
        install_macos.build_plan(args)
    paths["config"].write_text("{}\n")
    (paths["app"] / ".venv/bin/python").unlink()
    with pytest.raises(install_macos.InstallError, match="required path is unavailable"):
        install_macos.build_plan(args)


def test_macos_plan_rejects_flat_plaintext_alias(tmp_path, monkeypatch):
    args, paths = _fixture_paths(tmp_path, monkeypatch)
    paths["config"].write_text("feishu.agent.app_secret: forbidden\n")
    with pytest.raises(install_macos.InstallError, match="without plaintext secrets"):
        install_macos.build_plan(args)


def test_macos_plan_accepts_owned_venv_python_symlink(tmp_path, monkeypatch):
    args, paths = _fixture_paths(tmp_path, monkeypatch)
    venv_python = paths["app"] / ".venv/bin/python"
    venv_python.unlink()
    venv_python.symlink_to(sys.executable)
    _, rendered = install_macos.build_plan(args)
    assert "api" in rendered


def test_macos_plan_json_contains_no_secret_bytes(tmp_path, monkeypatch, capsys):
    args, paths = _fixture_paths(tmp_path, monkeypatch)
    result = install_macos.main([
        "--plan", "--app-dir", str(paths["app"]),
        "--config-file", str(paths["config"]), "--data-dir", str(paths["data"]),
        "--reports-dir", str(paths["reports"]), "--log-dir", str(paths["logs"]),
        "--launch-agents-dir", str(paths["agents"]),
        "--keychain-file", str(paths["keychain"]),
    ])
    plan = json.loads(capsys.readouterr().out)
    assert result == 0
    assert plan["loaded"] is False
    assert len(plan["jobs"]) == 10
    assert "secret" not in json.dumps(plan).lower()
