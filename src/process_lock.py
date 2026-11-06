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


def futu_full_sync_lock_key(account: str) -> str:
    return f"futu-full-sync:{account}"


def nav_history_lock_key() -> str:
    """Return the repository-wide same-host NAV mutation lock key."""
    return "nav-history-write"


def holdings_observation_lock_key(file_token: str, table_id: str) -> str:
    """Coordinate every duplicate observation of the same configured table."""
    target = (str(file_token or "").strip(), str(table_id or "").strip())
    if not all(target):
        raise ValueError("holdings observation lock requires file_token and table_id")
    return f"holdings-observation:{target[0]}:{target[1]}"


@contextmanager
def holdings_observation_lock(
    *, data_dir: Path | None = None, lock_factory=None,
) -> Iterator[None]:
    """Resolve the local table configuration and lock before reading holdings."""
    try:
        file_token, table_id = config.get_feishu_table_ref("holdings")
        key = holdings_observation_lock_key(file_token, table_id)
    except ValueError:
        if config.get("feishu.tables.holdings"):
            raise
        # Offline/fake-storage callers have no remote target to coordinate.
        key = "holdings-observation:local-unconfigured"
    factory = lock_factory or process_lock
    with factory(key, **({"data_dir": data_dir} if data_dir is not None else {})):
        yield


def holding_record_lock_key(record_id: str) -> str:
    normalized = str(record_id or "").strip()
    if not normalized:
        raise ValueError("holding record lock requires record_id")
    return f"holding-record-write:{normalized}"


def cash_flow_record_lock_key(record_id: str) -> str:
    normalized = str(record_id or "").strip()
    if not normalized:
        raise ValueError("cash flow record lock requires record_id")
    return f"cash-flow-record-write:{normalized}"


@contextmanager
def process_lock(
    key: str, *, data_dir: Path | None = None, blocking: bool = True,
) -> Iterator[None]:
    """Hold an exclusive same-host lock; nonblocking contention raises BlockingIOError."""
    digest = hashlib.sha256(str(key).encode("utf-8")).hexdigest()
    lock_dir = (data_dir or config.get_data_dir()) / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    with (lock_dir / f"{digest}.lock").open("a+") as handle:
        flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
        fcntl.flock(handle.fileno(), flags)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
