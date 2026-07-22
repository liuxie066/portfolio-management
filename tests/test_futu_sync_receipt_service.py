from src.app.futu_sync_receipt_service import FutuSyncReceiptService


class FakeClient:
    def __init__(self, *, app_id, app_secret, calls, error=None):
        calls.append(("init", app_id, app_secret))
        self.calls = calls
        self.error = error

    def send_text_message(self, *, open_id, text):
        self.calls.append(("send", open_id, text))
        if self.error:
            raise RuntimeError(self.error)
        return {"success": True, "message_id": "om_123"}


def _write_result():
    return {
        "success": True,
        "account": "lx",
        "dry_run": False,
        "cash_mmf": {"created": 0, "updated": 1},
        "summary": {
            "created": 0,
            "updated": 1,
            "zeroed": 0,
            "quantity_changed": 0,
            "cost_changed": 1,
        },
        "positions": [{
            "asset_id": "FUTU",
            "action": "update",
            "quantity_changed": False,
            "current_quantity": 200,
            "target_quantity": 200,
            "cost_changed": True,
            "current_avg_cost": 127.52,
            "target_avg_cost": 116.68,
        }],
    }


def test_futu_sync_receipt_sends_write_summary_from_liukanshan():
    calls = []
    service = FutuSyncReceiptService(
        app_id="cli_liukanshan",
        app_secret="secret",
        open_id="ou_user",
        client_factory=lambda **kwargs: FakeClient(calls=calls, **kwargs),
    )

    result = service.send(_write_result())

    assert result == {
        "success": True,
        "status": "sent",
        "channel": "feishu",
        "bot": "刘看山",
        "message_id": "om_123",
    }
    assert calls[0] == ("init", "cli_liukanshan", "secret")
    assert calls[1][0:2] == ("send", "ou_user")
    assert "# PM · 回执 · lx" in calls[1][2]
    assert "类型｜持仓同步" in calls[1][2]
    assert "状态｜✅ 成功" in calls[1][2]
    assert "成本 127.52→116.68" in calls[1][2]


def test_futu_sync_receipt_skips_dry_run_without_creating_client():
    calls = []
    service = FutuSyncReceiptService(
        app_id="cli_liukanshan",
        app_secret="secret",
        open_id="ou_user",
        client_factory=lambda **kwargs: FakeClient(calls=calls, **kwargs),
    )

    result = service.send({"success": True, "account": "lx", "dry_run": True})

    assert result["status"] == "skipped"
    assert result["reason"] == "dry_run"
    assert calls == []


def test_futu_sync_receipt_failure_does_not_claim_delivery():
    calls = []
    service = FutuSyncReceiptService(
        app_id="cli_liukanshan",
        app_secret="secret",
        open_id="ou_user",
        client_factory=lambda **kwargs: FakeClient(calls=calls, error="send failed", **kwargs),
    )

    result = service.send(_write_result())

    assert result["success"] is False
    assert result["status"] == "failed"
    assert result["error"] == "send failed"
