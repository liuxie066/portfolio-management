import multiprocessing
from queue import Empty

import pytest

from src.process_lock import (
    cash_flow_record_lock_key,
    futu_full_sync_lock_key,
    process_lock,
)


def _hold_lock(data_dir, events, release):
    with process_lock("shared-key", data_dir=data_dir):
        events.put("first-acquired")
        release.wait(timeout=5)
    events.put("first-released")


def _wait_for_lock(data_dir, events):
    events.put("second-started")
    with process_lock("shared-key", data_dir=data_dir):
        events.put("second-acquired")


def test_process_lock_serializes_separate_processes(tmp_path):
    context = multiprocessing.get_context("spawn")
    events = context.Queue()
    release = context.Event()
    first = context.Process(target=_hold_lock, args=(tmp_path, events, release))
    second = context.Process(target=_wait_for_lock, args=(tmp_path, events))

    first.start()
    assert events.get(timeout=5) == "first-acquired"
    second.start()
    assert events.get(timeout=5) == "second-started"
    with pytest.raises(Empty):
        events.get(timeout=0.3)

    release.set()
    observed = {events.get(timeout=5), events.get(timeout=5)}
    assert observed == {"first-released", "second-acquired"}

    first.join(timeout=5)
    second.join(timeout=5)
    assert first.exitcode == 0
    assert second.exitcode == 0


def test_cash_flow_record_lock_key_is_exact_and_rejects_empty_identity():
    assert cash_flow_record_lock_key(" rec-cf-1 ") == (
        "cash-flow-record-write:rec-cf-1"
    )
    with pytest.raises(ValueError, match="requires record_id"):
        cash_flow_record_lock_key(" ")


def test_futu_full_sync_lock_key_is_account_scoped():
    assert futu_full_sync_lock_key("lx") == "futu-full-sync:lx"
    assert futu_full_sync_lock_key("sy") == "futu-full-sync:sy"
