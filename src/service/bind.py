"""Bind-host and client-address safety checks for the local HTTP service."""
from __future__ import annotations

import ipaddress
import os


ALLOW_REMOTE_ENV = "PORTFOLIO_SERVICE_ALLOW_REMOTE"
LOOPBACK_NAMES = {"localhost"}
_TRUE_VALUES = {"1", "true", "yes", "on"}


def is_loopback_host(host: str) -> bool:
    normalized = (host or "").strip().lower()
    if normalized in LOOPBACK_NAMES:
        return True
    return is_loopback_client(normalized)


def is_loopback_client(host: str) -> bool:
    try:
        return ipaddress.ip_address((host or "").strip()).is_loopback
    except ValueError:
        return False


def allow_remote_from_env() -> bool:
    return os.getenv(ALLOW_REMOTE_ENV, "").strip().lower() in _TRUE_VALUES


def validate_bind_host(host: str, *, allow_remote: bool = False) -> None:
    if allow_remote or is_loopback_host(host):
        return
    raise ValueError(
        f"refusing to bind unauthenticated portfolio service to non-loopback host {host!r}; "
        "use --allow-remote only behind an authenticated local network boundary"
    )
