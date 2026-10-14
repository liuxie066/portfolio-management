import pytest

from src.app.cash_flow_effect_receipt_service import (
    CashFlowEffectReceiptService,
)
from src.app.cash_flow_effect_store import CashFlowEffectStore


class FakeClient:
    def __init__(self, *, app_id, app_secret, calls):
        calls.append(("init", app_id, app_secret))
        self.calls = calls

    def send_post_message(self, *, open_id, markdown):
        self.calls.append(("send_post", open_id, markdown))
        return {"message_id": "om_123"}


def test_stale_preview_receipt_is_an_actionable_contract():
    message = CashFlowEffectReceiptService.build_message(
        {
            "receipt_type": "stale",
            "effect_id": "effect_1",
            "payload": {
                "account": "lx",
                "state": "stale",
                "provided_preview_hash": "preview_old",
                "current_preview_hash": "preview_new",
            },
        }
    )

    assert message == (
        "# PM · 回执 · lx\n"
        "\n"
        "类型｜Cash Flow 处理\n"
        "状态｜⚠️ 需重新确认\n"
        "Effect｜effect_1\n"
        "状态｜stale\n"
        "原因｜预览依据已变化，原确认不可继续使用\n"
        "原预览｜preview_old\n"
        "当前预览｜preview_new\n"
        "下一步｜pm cash-flow effects preview --effect-id effect_1 --json"
    )


def test_correction_receipt_points_to_the_new_effect():
    message = CashFlowEffectReceiptService.build_message(
        {
            "receipt_type": "stale",
            "effect_id": "effect_old",
            "payload": {
                "account": "sy",
                "state": "correction_required",
                "correction_effect_id": "effect_new",
            },
        }
    )

    assert message == (
        "# PM · 回执 · sy\n"
        "\n"
        "类型｜Cash Flow 处理\n"
        "状态｜⚠️ 需重新确认\n"
        "Effect｜effect_old\n"
        "状态｜correction_required\n"
        "原因｜原 Effect 已处理，但 Cash Flow 事实随后变化\n"
        "修正 Effect｜effect_new\n"
        "下一步｜pm cash-flow effects preview --effect-id effect_new --json"
    )


def test_compensation_receipt_exposes_error_scope_and_recovery_command():
    message = CashFlowEffectReceiptService.build_message(
        {
            "receipt_type": "compensation_pending",
            "effect_id": "effect_2",
            "payload": {
                "account": "sy",
                "state": "compensation_pending",
                "task_id": "task_9",
                "error": "holding fresh readback mismatch",
                "target_count": 3,
                "confirmed_target_count": 1,
                "unconfirmed_targets": [
                    {
                        "account": "sy",
                        "broker": "某券商",
                        "asset_id": "CNY-CASH",
                        "quantity": "70.00",
                    },
                    {
                        "account": "hb",
                        "broker": "某券商",
                        "asset_id": "CNY-CASH",
                        "quantity": "20.00",
                    },
                ],
            },
        }
    )

    assert message == (
        "# PM · 回执 · sy\n"
        "\n"
        "类型｜Cash Flow 处理\n"
        "状态｜❌ 可能部分写入\n"
        "Effect｜effect_2\n"
        "状态｜compensation_pending\n"
        "错误｜holding fresh readback mismatch\n"
        "写入范围｜已确认 1/3；未确认 2\n"
        "补偿任务｜task_9\n"
        "下一步｜pm cash-flow effects retry --effect-id effect_2 --confirm\n"
        "\n"
        "## 未确认目标\n"
        "sy · 某券商 · CNY-CASH → 70.00\n"
        "hb · 某券商 · CNY-CASH → 20.00"
    )


def test_unknown_receipt_type_is_rejected_instead_of_rendered_generically():
    with pytest.raises(
        ValueError,
        match="unsupported cash-flow effect receipt_type: surprise",
    ):
        CashFlowEffectReceiptService.build_message(
            {
                "receipt_type": "surprise",
                "payload": {"account": "lx"},
            }
        )


def test_unknown_receipt_becomes_terminal_without_starving_valid_rows(tmp_path):
    store = CashFlowEffectStore.initialize(
        db_path=tmp_path / "effects.sqlite3",
        cutover_date="2026-07-01",
    )
    calls = []
    service = CashFlowEffectReceiptService(
        store=store,
        app_id="cli_liukanshan",
        app_secret="secret",
        open_id="ou_user",
        client_factory=lambda **kwargs: FakeClient(calls=calls, **kwargs),
    )
    effect = store.create_version(
        source={
            "record_id": "cash_flow_1",
            "account": "lx",
            "broker": "某券商",
            "currency": "CNY",
            "signed_amount": "1.00",
            "flow_date": "2026-07-01",
        },
        source_hash="source_hash_1",
        state="pending",
        mode="apply",
    )
    for index in range(100):
        store.enqueue_receipt(
            receipt_key=f"unknown:{index}",
            receipt_type="surprise",
            effect_id=effect["effect_id"] if index == 0 else None,
            payload={"scope": "all"},
        )

    first = service.dispatch_pending()

    assert first["attempted"] == 100
    assert first["failed"] == 100
    assert first["invalid"] == 100
    assert first["results"][0]["status"] == "invalid"
    assert first["results"][0]["retryable"] is False
    assert calls == []
    assert store.list_pending_receipts() == []
    invalid_rows = store.list_invalid_receipts()
    assert len(invalid_rows) == 100
    assert invalid_rows[0]["receipt_type"] == "surprise"
    assert invalid_rows[0]["status"] == "invalid"
    assert invalid_rows[0]["attempt_count"] == 1
    assert invalid_rows[0]["last_error"] == (
        "unsupported cash-flow effect receipt_type: surprise"
    )
    assert invalid_rows[0]["payload"] == {"scope": "all"}
    event_types = [
        event["event_type"]
        for event in store.list_events(effect["effect_id"])
    ]
    assert event_types.count("receipt_invalid") == 1
    assert "receipt_failed" not in event_types

    store.enqueue_receipt(
        receipt_key="runtime:1",
        receipt_type="runtime_error",
        payload={"scope": "all", "error": "read failed"},
    )
    second = service.dispatch_pending()

    assert second["attempted"] == 1
    assert second["sent"] == 1
    assert second["invalid"] == 0
    assert len(calls) == 2
    assert service.dispatch_pending()["attempted"] == 0
    event_types = [
        event["event_type"]
        for event in store.list_events(effect["effect_id"])
    ]
    assert event_types.count("receipt_invalid") == 1


def test_legacy_discovery_does_not_invent_pending_state_or_origin():
    message = CashFlowEffectReceiptService.build_message({
        "receipt_type": "discovery",
        "payload": {"changed": 1, "effects": [{"effect_id": "old", "state": "record_only"}]},
    })
    assert "待逐条处理" not in message
    assert "处理状态请查询" in message
    assert "来源未记录" in message
    assert "pm cash-flow review" in message


@pytest.mark.parametrize("receipt_type", ["runtime_error", "runtime_recovered"])
@pytest.mark.parametrize("latest_scope, latest_status", [("hb", "completed"), ("hb", "failed"), ("lx", "completed")])
def test_delayed_runtime_receipts_show_occurrence_and_current_same_scope_state(
    tmp_path, monkeypatch, receipt_type, latest_scope, latest_status,
):
    from src import config

    monkeypatch.setattr(config, "get_data_dir", lambda: tmp_path)
    store = CashFlowEffectStore.initialize(db_path=tmp_path / "effects.sqlite3", cutover_date="2026-10-01")
    original_id = store.begin_scan(scope="hb")
    original = store.finish_scan(original_id, status="failed" if receipt_type == "runtime_error" else "completed")
    payload = {"scope": "hb", "scan_run_id": original_id, "error": "original failure",
               "occurred_at": original["completed_at"]}
    store.enqueue_receipt(receipt_key="runtime", receipt_type=receipt_type, payload=payload, scan_run_id=original_id)
    latest_id = store.begin_scan(scope=latest_scope)
    latest = store.finish_scan(latest_id, status=latest_status)
    calls = []
    service = CashFlowEffectReceiptService(store=store, app_id="app", app_secret="secret", open_id="user",
                                          client_factory=lambda **kwargs: FakeClient(**kwargs, calls=calls))
    assert service.dispatch_pending()["sent"] == 1
    message = calls[-1][-1]
    assert original["completed_at"] in message
    assert "发生时间（北京时间）" in message
    if latest_scope == "hb" and latest_status == "completed":
        assert "当前状态｜已恢复" in message
        assert latest["completed_at"] in message and latest_id in message
        if receipt_type == "runtime_error":
            assert "历史异常，当前已恢复" in message
    elif latest_scope == "hb":
        assert "当前状态｜运行异常" in message
        assert "恢复时间（北京时间）" not in message
        if receipt_type == "runtime_recovered":
            assert "历史恢复，当前再次异常" in message
    elif receipt_type == "runtime_error":
        assert "当前状态｜运行异常" in message
        assert latest_id not in message
    # Delivery context is derived, not written back over the original incident.
    with store._connect() as conn:
        import json
        stored = conn.execute("SELECT payload_json FROM cash_flow_effect_receipts WHERE receipt_key = 'runtime'").fetchone()
        assert json.loads(stored[0]) == payload


@pytest.mark.parametrize("receipt_type", ["runtime_error", "runtime_recovered"])
def test_legacy_runtime_receipt_uses_original_queue_time(tmp_path, monkeypatch, receipt_type):
    from src import config

    monkeypatch.setattr(config, "get_data_dir", lambda: tmp_path)
    store = CashFlowEffectStore.initialize(db_path=tmp_path / "effects.sqlite3", cutover_date="2026-10-01")
    original_id = store.begin_scan(scope="hb")
    store.finish_scan(original_id, status="failed" if receipt_type == "runtime_error" else "completed")
    store.enqueue_receipt(receipt_key="old", receipt_type=receipt_type, scan_run_id=original_id,
                          payload={"scope": "hb", "error": "old error", "scan_run_id": original_id})
    created_at = store.list_pending_receipts()[0]["created_at"]
    if receipt_type == "runtime_error":
        latest = store.begin_scan(scope="hb")
        store.finish_scan(latest, status="completed")
    calls = []
    service = CashFlowEffectReceiptService(store=store, app_id="app", app_secret="secret", open_id="user",
                                          client_factory=lambda **kwargs: FakeClient(**kwargs, calls=calls))
    assert service.dispatch_pending()["sent"] == 1
    assert created_at in calls[-1][-1]
    assert "当前状态｜已恢复" in calls[-1][-1]
    if receipt_type == "runtime_error":
        assert "历史异常，当前已恢复" in calls[-1][-1]


def test_cash_receipt_concurrent_drains_send_once(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from src import config

    monkeypatch.setattr(config, "get_data_dir", lambda: tmp_path)
    path = tmp_path / "effects.sqlite3"
    store = CashFlowEffectStore.initialize(db_path=path, cutover_date="2026-10-01")
    store.enqueue_receipt(receipt_key="error", receipt_type="runtime_error", payload={"scope": "hb", "error": "error"})
    sending, second_started, release = Event(), Event(), Event()
    calls = []

    class BlockingClient(FakeClient):
        def send_post_message(self, **kwargs):
            sending.set()
            assert release.wait(5)
            return super().send_post_message(**kwargs)

    def service():
        return CashFlowEffectReceiptService(store=CashFlowEffectStore(path), app_id="app", app_secret="secret", open_id="user",
                                            client_factory=lambda **kwargs: BlockingClient(**kwargs, calls=calls))

    def second_drain():
        second_started.set()
        return service().dispatch_pending()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(service().dispatch_pending)
        assert sending.wait(5)
        second = executor.submit(second_drain)
        try:
            assert second_started.wait(5)
            assert not second.done()
        finally:
            release.set()
        results = [first.result(5), second.result(5)]
    assert sum(item["sent"] for item in results) == 1
    assert sum(item["attempted"] for item in results) == 1
    assert len([item for item in calls if item[0] == "send_post"]) == 1


def _cash_receipt_process(db_path, data_dir, started, release, send_queue, result_queue):
    from pathlib import Path
    from src import config

    config.get_data_dir = lambda: Path(data_dir)

    class Client:
        def __init__(self, **kwargs):
            pass

        def send_post_message(self, **kwargs):
            send_queue.put("send")
            assert release.wait(10)
            return {"message_id": "sent"}

    service = CashFlowEffectReceiptService(store=CashFlowEffectStore(db_path), app_id="app", app_secret="secret",
                                          open_id="user", client_factory=Client)
    started.wait(timeout=10)
    result_queue.put(service.dispatch_pending())


def test_cash_receipt_same_database_locks_across_different_data_dirs(tmp_path):
    from multiprocessing import get_context
    from queue import Empty

    path = tmp_path / "effects.sqlite3"
    store = CashFlowEffectStore.initialize(db_path=path, cutover_date="2026-10-01")
    store.enqueue_receipt(receipt_key="error", receipt_type="runtime_error", payload={"scope": "hb"})
    ctx = get_context("spawn")
    started, release, sends, results = ctx.Barrier(2), ctx.Event(), ctx.Queue(), ctx.Queue()
    processes = [ctx.Process(target=_cash_receipt_process,
                             args=(path, tmp_path / f"data{i}", started, release, sends, results)) for i in range(2)]
    for process in processes:
        process.start()
    try:
        assert sends.get(timeout=10) == "send"
        try:
            sends.get(timeout=1)
            duplicate = True
        except Empty:
            duplicate = False
    finally:
        release.set()
        for process in processes:
            process.join(timeout=10)
            if process.is_alive():
                process.terminate()
                process.join()
    assert [process.exitcode for process in processes] == [0, 0]
    assert not duplicate
    assert sum(results.get(timeout=5)["sent"] for _ in processes) == 1
    assert store.list_pending_receipts() == []
