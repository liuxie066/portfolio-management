import multiprocessing
from queue import Empty

import pytest

from src.process_lock import (
    cash_flow_record_lock_key,
    futu_full_sync_lock_key,
    holdings_observation_lock,
    holdings_observation_lock_key,
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


def test_holdings_observation_lock_is_target_scoped_and_config_only(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from src import config
    seen = []
    monkeypatch.setattr(config, "get_feishu_table_ref", lambda name: ("base-one", "table-one"))
    @contextmanager
    def capture(key, **kwargs):
        seen.append((key, kwargs))
        yield
    with holdings_observation_lock(data_dir=tmp_path, lock_factory=capture):
        pass
    assert seen == [(holdings_observation_lock_key("base-one", "table-one"), {"data_dir": tmp_path})]
    assert holdings_observation_lock_key("base-one", "table-one") != holdings_observation_lock_key("base-two", "table-one")
    with pytest.raises(ValueError, match="requires file_token and table_id"):
        holdings_observation_lock_key("", "table-one")


def test_invalid_configured_target_cannot_fall_back_to_local_lock(monkeypatch):
    from src import config
    def fail(name):
        raise ValueError("bad table target")
    monkeypatch.setattr(config, "get_feishu_table_ref", fail)
    monkeypatch.setattr(config, "get", lambda key: "configured-bad-target")
    with pytest.raises(ValueError, match="bad table target"):
        with holdings_observation_lock():
            raise AssertionError("invalid target lock must fail before entry")


def test_cash_flow_record_lock_key_is_exact_and_rejects_empty_identity():
    assert cash_flow_record_lock_key(" rec-cf-1 ") == (
        "cash-flow-record-write:rec-cf-1"
    )
    with pytest.raises(ValueError, match="requires record_id"):
        cash_flow_record_lock_key(" ")


def test_futu_full_sync_lock_key_is_account_scoped():
    assert futu_full_sync_lock_key("lx") == "futu-full-sync:lx"
    assert futu_full_sync_lock_key("sy") == "futu-full-sync:sy"


def test_nonblocking_lock_contends_only_with_same_root_and_key(tmp_path):
    with process_lock("worker", data_dir=tmp_path):
        with pytest.raises(BlockingIOError), process_lock(
            "worker", data_dir=tmp_path, blocking=False,
        ):
            pytest.fail("duplicate owner")
        with process_lock("worker", data_dir=tmp_path / "other", blocking=False):
            pass
    with process_lock("worker", data_dir=tmp_path, blocking=False):
        pass
