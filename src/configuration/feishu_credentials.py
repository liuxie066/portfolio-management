"""Strict systemd and macOS Keychain credential access."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


CREDENTIALS_DIRECTORY_ENV = "CREDENTIALS_DIRECTORY"
SECURE_FEISHU_CREDENTIALS_ENV = "PM_REQUIRE_SECURE_FEISHU_CREDENTIALS"
MAX_FEISHU_APP_SECRET_BYTES = 4096

AGENT_APP_SECRET_CREDENTIAL = "pm-feishu-agent-app-secret"
LISTENER_APP_SECRET_CREDENTIAL = "pm-feishu-listener-app-secret"
QUALITY_READ_TOKEN_CREDENTIAL = "pm-quality-read-token"
KEYCHAIN_SERVICE = "com.liuxie.portfolio-management"
KEYCHAIN_BACKEND_ENV = "PM_CREDENTIAL_BACKEND"
KEYCHAIN_FILE_ENV = "PM_KEYCHAIN_FILE"
SECURITY_TOOL = "/usr/bin/security"
KEYCHAIN_TIMEOUT_SECONDS = 3

_TRUE_VALUES = {"1", "true", "yes", "y", "on"}
_FALSE_VALUES = {"0", "false", "no", "n", "off"}


@dataclass
class FeishuCredentialConfigError(ValueError):
    """Redacted configuration error safe for operator-facing output."""

    code: str
    key: str

    def __str__(self) -> str:
        return f"{self.code}: {self.key}"

    def as_issue(self) -> dict[str, str]:
        return {"key": self.key, "error": self.code}


def secure_feishu_credentials_required() -> bool:
    raw_value = os.environ.get(SECURE_FEISHU_CREDENTIALS_ENV)
    if raw_value is None or not str(raw_value).strip():
        return False
    normalized = str(raw_value).strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise FeishuCredentialConfigError(
        "invalid_secure_mode",
        "feishu.credentials.secure_mode",
    )


def keychain_backend_enabled() -> bool:
    backend = str(os.environ.get(KEYCHAIN_BACKEND_ENV) or "").strip()
    if not backend:
        return False
    if backend != "keychain" or sys.platform != "darwin":
        raise FeishuCredentialConfigError("invalid_credential_backend", "feishu.credentials.backend")
    return True


def read_keychain_credential(*, key: str, credential_name: str) -> str:
    """Read one pinned Keychain item without exposing command output in errors."""
    if CREDENTIALS_DIRECTORY_ENV in os.environ:
        raise FeishuCredentialConfigError("conflicting_credential_backend", key)
    configured = str(os.environ.get(KEYCHAIN_FILE_ENV) or "")
    path = Path(configured)
    if not path.is_absolute():
        raise FeishuCredentialConfigError("invalid_keychain_file", key)
    try:
        metadata = path.lstat()
    except OSError:
        raise FeishuCredentialConfigError("invalid_keychain_file", key) from None
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid()):
        raise FeishuCredentialConfigError("invalid_keychain_file", key)

    try:
        result = subprocess.run(
            [SECURITY_TOOL, "find-generic-password", "-s", KEYCHAIN_SERVICE,
             "-a", credential_name, "-w", str(path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=KEYCHAIN_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise FeishuCredentialConfigError("keychain_unavailable", key) from None
    if result.returncode != 0:
        raise FeishuCredentialConfigError("keychain_unavailable", key)
    payload = result.stdout
    if payload.endswith(b"\n"):
        payload = payload[:-1]
    if not payload or len(payload) > MAX_FEISHU_APP_SECRET_BYTES or b"\x00" in payload or b"\n" in payload or b"\r" in payload:
        raise FeishuCredentialConfigError("invalid_keychain_credential", key)
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError:
        raise FeishuCredentialConfigError("invalid_keychain_credential", key) from None


def read_systemd_credential(
    *,
    key: str,
    credential_name: str,
) -> tuple[Optional[str], bool]:
    """Return one credential without exposing its path or bytes in errors."""

    directory = str(os.environ.get(CREDENTIALS_DIRECTORY_ENV) or "").strip()
    if not directory:
        return None, False

    path = Path(directory) / credential_name
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None, False
    except OSError:
        raise FeishuCredentialConfigError("invalid_credential_file", key) from None

    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise FeishuCredentialConfigError("invalid_credential_file", key)
    if metadata.st_size > MAX_FEISHU_APP_SECRET_BYTES:
        raise FeishuCredentialConfigError("invalid_credential_file", key)

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise FeishuCredentialConfigError("invalid_credential_file", key) from None
    try:
        opened_metadata = os.fstat(descriptor)
        if not stat.S_ISREG(opened_metadata.st_mode):
            raise FeishuCredentialConfigError("invalid_credential_file", key)
        with os.fdopen(descriptor, "rb", closefd=True) as credential_file:
            descriptor = -1
            payload = credential_file.read(MAX_FEISHU_APP_SECRET_BYTES + 1)
    except OSError:
        raise FeishuCredentialConfigError("invalid_credential_file", key) from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)

    if not payload or len(payload) > MAX_FEISHU_APP_SECRET_BYTES or b"\x00" in payload:
        raise FeishuCredentialConfigError("invalid_credential_file", key)
    if payload.endswith(b"\n"):
        payload = payload[:-1]
    if not payload or b"\n" in payload or b"\r" in payload:
        raise FeishuCredentialConfigError("invalid_credential_file", key)
    try:
        value = payload.decode("utf-8")
    except UnicodeDecodeError:
        raise FeishuCredentialConfigError("invalid_credential_file", key) from None
    if not value:
        raise FeishuCredentialConfigError("invalid_credential_file", key)
    return value, True
