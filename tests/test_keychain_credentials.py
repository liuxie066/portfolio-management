from __future__ import annotations

import json
from pathlib import Path

import pytest

from src import config
from src.configuration import feishu_credentials as credentials


def _mac_keychain(monkeypatch, tmp_path: Path) -> tuple[Path, Path]:
    keychain = tmp_path / "login.keychain-db"
    keychain.write_bytes(b"test keychain placeholder")
    keychain.chmod(0o600)
    argv_path = tmp_path / "security-argv.json"
    security = tmp_path / "security"
    security.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys
import time
from pathlib import Path

Path(os.environ["FAKE_SECURITY_ARGV"]).write_text(json.dumps(sys.argv[1:]))
mode = os.environ.get("FAKE_SECURITY_MODE", "ok")
if mode == "timeout":
    time.sleep(10)
if mode in {"missing", "denied"}:
    sys.stderr.write("private-keychain-path-and-secret\\n")
    sys.exit(44)
if mode == "invalid":
    sys.stdout.buffer.write(b"first\\nsecond")
elif mode == "oversize":
    sys.stdout.buffer.write(b"x" * 4097)
else:
    account = sys.argv[sys.argv.index("-a") + 1]
    sys.stdout.buffer.write((account + ":secret\\n").encode())
""",
        encoding="utf-8",
    )
    security.chmod(0o700)
    config_file = tmp_path / "config.yaml"
    config_file.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(credentials.sys, "platform", "darwin")
    monkeypatch.setattr(credentials, "SECURITY_TOOL", str(security))
    monkeypatch.setenv("FAKE_SECURITY_ARGV", str(argv_path))
    monkeypatch.setenv("PM_CREDENTIAL_BACKEND", "keychain")
    monkeypatch.setenv("PM_KEYCHAIN_FILE", str(keychain))
    monkeypatch.setenv("PM_REQUIRE_SECURE_FEISHU_CREDENTIALS", "1")
    monkeypatch.setenv(config.CONFIG_FILE_ENV, str(config_file))
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    for key in config.SECRET_KEYS:
        if env_name := config.ENV_MAP.get(key):
            monkeypatch.delenv(env_name, raising=False)
        for env_name in config.ENV_FALLBACKS.get(key, ()):
            monkeypatch.delenv(env_name, raising=False)
    monkeypatch.setattr(config, "_cached_config", None)
    config.reload_config()
    return keychain, argv_path


@pytest.mark.parametrize(
    ("key", "account"),
    [
        ("feishu.agent.app_secret", credentials.AGENT_APP_SECRET_CREDENTIAL),
        ("feishu.listener.app_secret", credentials.LISTENER_APP_SECRET_CREDENTIAL),
        ("quality.read_token", credentials.QUALITY_READ_TOKEN_CREDENTIAL),
    ],
)
def test_keychain_uses_pinned_file_and_never_displays_secret(
    monkeypatch, tmp_path, key, account
):
    keychain, argv_path = _mac_keychain(monkeypatch, tmp_path)
    value, source = config.get_with_source(key)

    assert value == f"{account}:secret"
    assert source == f"credential:keychain:{account}"
    assert json.loads(argv_path.read_text()) == [
        "find-generic-password", "-s", credentials.KEYCHAIN_SERVICE,
        "-a", account, "-w", str(keychain),
    ]
    inspected = config.inspect_config(keys=[key], redact=False)
    assert inspected["values"][key]["value"] == "***"
    assert value not in json.dumps(inspected)


@pytest.mark.parametrize("mode", ["missing", "denied", "timeout", "invalid", "oversize"])
def test_keychain_failures_are_redacted_and_do_not_fallback(
    monkeypatch, tmp_path, mode
):
    _mac_keychain(monkeypatch, tmp_path)
    monkeypatch.setenv("FAKE_SECURITY_MODE", mode)
    if mode == "timeout":
        monkeypatch.setattr(credentials, "KEYCHAIN_TIMEOUT_SECONDS", 0.05)

    with pytest.raises(config.FeishuCredentialConfigError) as caught:
        config.get("feishu.agent.app_secret")

    assert caught.value.code in {"keychain_unavailable", "invalid_keychain_credential"}
    assert "private-keychain-path-and-secret" not in str(caught.value)
    assert str(tmp_path) not in str(caught.value)


def test_keychain_rejects_plaintext_shadows_and_systemd_directory(monkeypatch, tmp_path):
    _, argv_path = _mac_keychain(monkeypatch, tmp_path)
    monkeypatch.setenv("PM_QUALITY_READ_TOKEN", "forbidden-plaintext")
    with pytest.raises(config.FeishuCredentialConfigError, match="insecure_secret_source"):
        config.get("quality.read_token")
    assert not argv_path.exists()

    monkeypatch.delenv("PM_QUALITY_READ_TOKEN")
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", "/unused/systemd/path")
    with pytest.raises(config.FeishuCredentialConfigError, match="conflicting_credential_backend"):
        config.get("quality.read_token")
    assert not argv_path.exists()


def test_keychain_rejects_unpinned_or_unsupported_backend(monkeypatch, tmp_path):
    _mac_keychain(monkeypatch, tmp_path)
    monkeypatch.setenv("PM_KEYCHAIN_FILE", "relative.keychain-db")
    with pytest.raises(config.FeishuCredentialConfigError, match="invalid_keychain_file"):
        config.get("quality.read_token")

    monkeypatch.setenv("PM_KEYCHAIN_FILE", str(tmp_path / "login.keychain-db"))
    monkeypatch.setattr(credentials.sys, "platform", "linux")
    with pytest.raises(config.FeishuCredentialConfigError, match="invalid_credential_backend"):
        config.get("feishu.agent.app_secret")
