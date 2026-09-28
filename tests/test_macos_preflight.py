from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest

from scripts import pm


ROOT = Path(__file__).resolve().parents[1]


def test_strict_pm_launcher_rejects_missing_venv(tmp_path):
    shutil.copy2(ROOT / "pm", tmp_path / "pm")
    result = subprocess.run(
        [str(tmp_path / "pm"), "config", "futu-preflight"],
        env={**os.environ, "PM_REQUIRE_VENV": "1"},
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 1
    assert "venv Python is unavailable" in result.stderr
    (tmp_path / ".venv/bin").mkdir(parents=True)
    (tmp_path / ".venv/bin/python").symlink_to(sys.executable)
    result = subprocess.run(
        [str(tmp_path / "pm"), "config", "futu-preflight"],
        env={**os.environ, "PM_REQUIRE_VENV": "1"},
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 1
    assert "venv Python is unavailable" in result.stderr


def test_feishu_preflight_propagates_each_failure(tmp_path):
    (tmp_path / "scripts").mkdir()
    shutil.copy2(ROOT / "scripts/macos_preflight.sh", tmp_path / "scripts/macos_preflight.sh")
    launcher = tmp_path / "pm"
    launcher.write_text("#!/bin/bash\nprintf '%s\\n' \"$*\" >> \"$CALLS\"\n[[ \"$*\" != \"$FAIL_COMMAND\" ]]\n")
    launcher.chmod(0o700)
    calls = tmp_path / "calls"
    first = "config doctor --require-secure-feishu --json"
    second = "events status --json"
    for failure, expected in ((first, [first]), (second, [first, second]), ("none", [first, second])):
        calls.unlink(missing_ok=True)
        result = subprocess.run(
            ["/bin/bash", str(tmp_path / "scripts/macos_preflight.sh")],
            env={**os.environ, "CALLS": str(calls), "FAIL_COMMAND": failure},
            capture_output=True, text=True, check=False,
        )
        assert result.returncode == (0 if failure == "none" else 1)
        assert calls.read_text().splitlines() == expected


def test_futu_preflight_requires_same_interpreter_sdk_and_ready_opend(monkeypatch, capsys):
    from src import config

    profiles = {
        "lx": {"acc_id": 11, "host": "127.0.0.1", "port": 11111},
        "sy": {"acc_id": 22, "host": "127.0.0.1", "port": 11111},
    }
    monkeypatch.setattr(config, "get_futu_account_settings", lambda account: profiles[account])

    class Context:
        state = {"program_status_type": "READY", "qot_logined": True, "trd_logined": True}

        def __init__(self, **_kwargs):
            pass

        def get_global_state(self):
            return 0, self.state

        def close(self):
            pass

    monkeypatch.setitem(sys.modules, "futu", types.SimpleNamespace(RET_OK=0, __version__="test", OpenQuoteContext=Context))
    assert pm.main(["config", "futu-preflight", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["success"] is True
    assert result["accounts"] == ["lx", "sy"]
    assert len(result["opend"]) == 1

    Context.state = {"program_status_type": "READY", "qot_logined": True, "trd_logined": False}
    assert pm.main(["config", "futu-preflight", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["issues"][0]["key"] == "futu.opend"

    monkeypatch.setitem(sys.modules, "futu", None)
    monkeypatch.setitem(sys.modules, "moomoo", None)
    assert pm.main(["config", "futu-preflight", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["issues"][0]["key"] == "futu.sdk"


def test_quality_preflight_is_independent_of_daily_feishu_config(monkeypatch, capsys):
    from src import config

    values = {"quality.read_token": "private-token", "quality.accounts": ["lx"]}
    monkeypatch.setattr(config, "get", lambda key: values.get(key))
    assert pm.main(["config", "quality-preflight", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"success": True, "issues": []}
    values["quality.read_token"] = None
    assert pm.main(["config", "quality-preflight", "--json"]) == 1
    output = capsys.readouterr().out
    assert "private-token" not in output
    assert json.loads(output)["issues"][0]["key"] == "quality.read_token"


def test_native_lockf_skips_overlapping_run(tmp_path):
    if not Path("/usr/bin/lockf").exists():
        pytest.skip("macOS lockf is unavailable")
    lock = str(tmp_path / "scheduled.lock")
    first = subprocess.Popen(["/usr/bin/lockf", "-k", "-t", "0", lock, "/bin/sleep", "0.5"])
    try:
        time.sleep(0.1)
        second = subprocess.run(
            ["/usr/bin/lockf", "-k", "-t", "0", lock, "/usr/bin/true"],
            capture_output=True, check=False,
        )
        assert second.returncode != 0
    finally:
        first.wait(timeout=3)
    assert first.returncode == 0
