from __future__ import annotations

import os
import subprocess
from pathlib import Path


SCRIPT = Path("scripts/portfolio_scheduled_job.sh").resolve()


def _fixture(tmp_path: Path):
    calls = tmp_path / "calls.log"
    pm = tmp_path / "pm"
    pm.write_text(
        """#!/usr/bin/env bash
set -u
printf '%s|port=%s\\n' "$*" "${FUTU_OPEND_PORT:-unset}" >> "$CALLS_LOG"
if [[ "${FAIL_ACCOUNT:-}" != "" && "$*" == *"--account ${FAIL_ACCOUNT}"* ]]; then
  exit 1
fi
""",
        encoding="utf-8",
    )
    pm.chmod(0o755)
    sy_env = tmp_path / "futu-sy.env"
    sy_env.write_text(
        "FUTU_OPEND_HOST=127.0.0.1\nFUTU_OPEND_PORT=11112\nFUTU_ACC_ID=test-sy\n",
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "PORTFOLIO_PM_BIN": str(pm),
        "PORTFOLIO_FUTU_SY_ENV_FILE": str(sy_env),
        "CALLS_LOG": str(calls),
    }
    env.pop("FUTU_OPEND_PORT", None)
    return calls, sy_env, env


def _run(tmp_path: Path, mode: str, *, env_update=None):
    calls, sy_env, env = _fixture(tmp_path)
    env.update(env_update or {})
    result = subprocess.run(
        [str(SCRIPT), mode],
        env=env,
        capture_output=True,
        text=True,
    )
    lines = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
    return result, lines, sy_env


def test_morning_syncs_both_accounts_then_runs_one_multi_account_nav_job(tmp_path):
    result, calls, _sy_env = _run(tmp_path, "morning")

    assert result.returncode == 0
    assert calls == [
        "futu sync --account lx --write --confirm --json --no-service|port=unset",
        "futu sync --account sy --write --confirm --json --no-service|port=11112",
        "daily-job --accounts lx,hb,sy --write --confirm --json --no-service|port=unset",
    ]


def test_evening_only_syncs_both_futu_accounts(tmp_path):
    result, calls, _sy_env = _run(tmp_path, "evening")

    assert result.returncode == 0
    assert calls == [
        "futu sync --account lx --write --confirm --json --no-service|port=unset",
        "futu sync --account sy --write --confirm --json --no-service|port=11112",
    ]


def test_sync_failure_still_attempts_both_accounts_and_blocks_nav(tmp_path):
    result, calls, _sy_env = _run(tmp_path, "morning", env_update={"FAIL_ACCOUNT": "lx"})

    assert result.returncode == 1
    assert [line.split("|", 1)[0] for line in calls] == [
        "futu sync --account lx --write --confirm --json --no-service",
        "futu sync --account sy --write --confirm --json --no-service",
    ]
    assert "NAV job was not started" in result.stderr


def test_missing_sy_env_blocks_nav_after_lx_attempt(tmp_path):
    calls, sy_env, env = _fixture(tmp_path)
    sy_env.unlink()

    result = subprocess.run(
        [str(SCRIPT), "morning"],
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "futu sync --account lx --write --confirm --json --no-service|port=unset"
    ]
    assert "sy Futu env file is not readable" in result.stderr
