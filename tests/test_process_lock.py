import multiprocessing
from queue import Empty

import pytest

from src.process_lock import process_lock


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
