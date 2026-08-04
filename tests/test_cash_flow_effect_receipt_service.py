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
