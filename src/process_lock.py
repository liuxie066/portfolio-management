"""Small same-host process locks for financial write coordination."""
from __future__ import annotations

import fcntl
import hashlib
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from src import config


def account_lock_key(account: str) -> str:
    return f"account-write:{account}"


def nav_history_lock_key() -> str:
    """Return the repository-wide same-host NAV mutation lock key."""
    return "nav-history-write"


@contextmanager
def process_lock(key: str, *, data_dir: Path | None = None) -> Iterator[None]:
    """Hold an exclusive same-host lock for a logical key."""
    digest = hashlib.sha256(str(key).encode("utf-8")).hexdigest()
    lock_dir = (data_dir or config.get_data_dir()) / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    with (lock_dir / f"{digest}.lock").open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
