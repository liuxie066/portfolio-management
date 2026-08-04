from src.app.futu_sync_receipt_service import FutuSyncReceiptService


class FakeClient:
    def __init__(self, *, app_id, app_secret, calls, error=None):
        calls.append(("init", app_id, app_secret))
        self.calls = calls
        self.error = error

    def send_post_message(self, *, open_id, markdown):
        self.calls.append(("send_post", open_id, markdown))
        if self.error:
            raise RuntimeError(self.error)
        return {"success": True, "message_id": "om_123"}


def _write_result():
    return {
        "success": True,
        "account": "sy",
        "dry_run": False,
        "cash_mmf": {"success": True, "created": 0, "updated": 1},
        "cash_effects": {
            "created": 1,
            "resolved": 0,
            "suppressed_by_cash_flow": 0,
        },
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
    assert calls[1][0:2] == ("send_post", "ou_user")
    assert "# PM · 回执 · sy" in calls[1][2]
    assert "类型｜持仓同步" in calls[1][2]
    assert "状态｜✅ 成功" in calls[1][2]
    assert "富途原币余额仅观测" in calls[1][2]
    assert "PM 使用 CNY-CASH 人民币汇总，不做金额对账" in calls[1][2]
    assert "CASH Effects" not in calls[1][2]
    assert "新增待处理 1" not in calls[1][2]
    assert "pm cash-flow review" not in calls[1][2]
    assert "成本 127.52→116.68" in calls[1][2]


def test_futu_sync_receipt_defaults_to_conversation_role(monkeypatch):
    requested = []
    values = {
        "feishu.conversation.app_id": "cli_conversation",
        "feishu.conversation.app_secret": "conversation_secret",
        "feishu.conversation.open_id": "ou_user",
    }

    def fake_get(key, default=None):
        requested.append(key)
        return values.get(key, default)

    monkeypatch.setattr("src.app.futu_sync_receipt_service.config.get", fake_get)

    service = FutuSyncReceiptService()

    assert (service.app_id, service.app_secret, service.open_id) == (
        "cli_conversation",
        "conversation_secret",
        "ou_user",
    )
    assert requested == list(values)


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


def test_futu_sync_failure_receipt_uses_nested_error_and_exact_failed_stage():
    message = FutuSyncReceiptService.build_message({
        "success": False,
        "account": "lx",
        "dry_run": False,
        "sync_run_id": "sync_123",
        "write_stage": "cash_mmf",
        "partial_write_possible": True,
        "stages": {
            "positions": {
                "status": "succeeded",
                "partial_write_possible": False,
            },
            "securities_cash": {
                "status": "succeeded",
                "partial_write_possible": False,
            },
            "fund_mmf": {
                "status": "failed",
                "partial_write_possible": True,
            },
        },
        "cash_mmf": {
            "success": False,
            "partial_write_possible": True,
            "error": "飞书 API 错误: TextFieldConvFail (code=1254060)",
        },
    })

    assert message == (
        "# PM · 回执 · lx\n"
        "\n"
        "类型｜持仓同步\n"
        "状态｜❌ 失败\n"
        "失败阶段｜fund_mmf\n"
        "错误｜飞书 API 错误: TextFieldConvFail (code=1254060)\n"
        "Run｜sync_123\n"
        "CASH / MMF｜富途原币余额仅观测；PM 使用 CNY-CASH 人民币汇总，"
        "不做金额对账；MMF 结果未确认\n"
        "警告｜fund_mmf 阶段可能已部分写入，请先 dry-run 复核\n"
        "下一步｜pm futu sync --account lx --dry-run --json\n"
        "\n"
        "## 执行阶段\n"
        "positions · 成功\n"
        "securities_cash · 成功\n"
        "fund_mmf · 失败 · 可能部分写入"
    )

    assert "unknown error" not in message
    assert "MMF 新增 0，更新 0" not in message


def test_futu_sync_success_receipt_keeps_confirmed_zero_mmf_counts():
    result = _write_result()
    result["cash_mmf"] = {
        "success": True,
        "created": 0,
        "updated": 0,
    }

    message = FutuSyncReceiptService.build_message(result)

    assert "MMF 新增 0，更新 0" in message


def test_futu_sync_receipt_does_not_infer_counts_from_success_alone():
    result = _write_result()
    result["cash_mmf"] = {"success": True}

    message = FutuSyncReceiptService.build_message(result)

    assert "MMF 结果未确认" in message
    assert "MMF 新增 0，更新 0" not in message
