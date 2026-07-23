from datetime import datetime

from src.app.nav_history_receipt_service import NavHistoryReceiptService


def _written(
    account,
    *,
    nav=1.0,
    total_value=100.0,
    pnl=2.0,
    cash_flow=0.0,
    ytd_nav_change=0.05,
    warnings=None,
):
    return {
        "success": True,
        "status": "written",
        "account": account,
        "report": {
            "nav": nav,
            "total_value": total_value,
            "pnl": pnl,
            "cash_flow": cash_flow,
            "ytd_nav_change": ytd_nav_change,
            "overview": {"stock_ratio": 0.7, "fund_ratio": 0.1, "cash_ratio": 0.2},
            "warnings": warnings or [],
        },
    }


def test_nav_receipt_dry_run_does_not_send():
    calls = []
    service = NavHistoryReceiptService(
        app_id="cli_app",
        app_secret="secret",
        open_id="ou_user",
        client_factory=lambda **kwargs: calls.append(kwargs),
    )

    result = service.send({"success": True, "dry_run": True})

    assert result["status"] == "skipped"
    assert result["reason"] == "dry_run"
    assert calls == []


def test_nav_receipt_sends_one_consolidated_success_message():
    calls = []

    class FakeClient:
        def __init__(self, **kwargs):
            calls.append(("init", kwargs))

        def send_post_message(self, *, open_id, markdown):
            calls.append(("send_post", open_id, markdown))
            return {"message_id": "om_nav"}

    service = NavHistoryReceiptService(
        app_id="cli_app",
        app_secret="secret",
        open_id="ou_user",
        client_factory=FakeClient,
        now_factory=lambda: datetime(2026, 7, 18, 8, 11),
    )
    payload = {
        "success": True,
        "status": "completed",
        "dry_run": False,
        "date": "2026-07-17",
        "run_id": "daily-nav-job-multi-1",
        "items": [
            _written("lx", nav=0.957931, total_value=3893292.82, pnl=65375.44),
            _written("hb", nav=1.023482, total_value=1286450.20, pnl=-3421.18),
            _written("sy", nav=0.884216, total_value=2315621.50, pnl=18932.10, cash_flow=5000),
        ],
    }

    result = service.send(payload)

    assert result == {
        "success": True,
        "status": "sent",
        "channel": "feishu",
        "bot": "刘看山",
        "message_id": "om_nav",
    }
    assert calls[0] == ("init", {"app_id": "cli_app", "app_secret": "secret"})
    assert calls[1][0:2] == ("send_post", "ou_user")
    text = calls[1][2]
    assert "# PM · 回执 · NAV History" in text
    assert "类型｜NAV 记录" in text
    assert "状态｜✅ 成功" in text
    assert "时间｜2026-07-18 08:11 北京时间" in text
    assert "NAV 日期｜2026-07-17" in text
    assert "结果｜写入 3，跳过 0，失败 0" in text
    assert "✅ lx｜NAV 0.957931｜总资产 ¥3,893,292.82｜当期盈亏 +¥65,375.44" in text
    assert "✅ hb｜NAV 1.023482｜总资产 ¥1,286,450.20｜当期盈亏 -¥3,421.18" in text
    assert "YTD NAV +5.00%｜股票 70.00%｜基金 10.00%｜现金 20.00%" in text
    assert "资金变动 +¥5,000.00" in text
    assert "## 告警" not in text
    assert "Run ID｜daily-nav-job-multi-1" in text


def test_nav_receipt_formats_negative_and_missing_ytd_nav_change():
    negative = NavHistoryReceiptService._item_row(_written("lx", ytd_nav_change=-0.0123))
    missing = NavHistoryReceiptService._item_row(_written("hb", ytd_nav_change=None))

    assert "YTD NAV -1.23%" in negative
    assert "YTD NAV -｜" in missing


def test_nav_receipt_formats_existing_nav_skip():
    text = NavHistoryReceiptService.build_message(
        {
            "success": True,
            "status": "completed",
            "dry_run": False,
            "date": "2026-07-17",
            "items": [
                {
                    "success": True,
                    "status": "skipped_existing_nav",
                    "account": "lx",
                    "nav": 0.957931,
                    "total_value": 3893292.82,
                }
            ],
        },
        executed_at=datetime(2026, 7, 20, 8, 11),
    )

    assert "状态｜⏭ 无需写入" in text
    assert "结果｜写入 0，跳过 1，失败 0" in text
    assert "⏭ lx｜NAV 已存在｜NAV 0.957931｜总资产 ¥3,893,292.82" in text


def test_nav_receipt_formats_partial_failure_and_price_warning():
    text = NavHistoryReceiptService.build_message(
        {
            "success": False,
            "status": "partial",
            "dry_run": False,
            "date": "2026-07-17",
            "items": [
                _written("lx", warnings=["FUTU price unavailable"]),
                {
                    "success": False,
                    "status": "cash_flow_error",
                    "account": "hb",
                    "error": "cash_flow has invalid manual rows",
                },
            ],
        },
        executed_at=datetime(2026, 7, 18, 8, 11),
    )

    assert "状态｜⚠️ 部分失败" in text
    assert "结果｜写入 1，跳过 0，失败 1" in text
    assert "❌ hb｜cash_flow_error｜cash_flow has invalid manual rows" in text
    assert "价格：" not in text
    assert "## 告警\nlx: FUTU price unavailable" in text


def test_nav_receipt_formats_snapshot_recovery_error():
    text = NavHistoryReceiptService.build_message(
        {
            "success": False,
            "status": "recovery_required",
            "dry_run": False,
            "date": "2026-07-17",
            "items": [
                {
                    "success": False,
                    "status": "recovery_required",
                    "account": "lx",
                    "error": "snapshot write failed",
                    "snapshot_error": "snapshot write failed",
                    "task_id": "repair_snapshot_1",
                }
            ],
        },
        executed_at=datetime(2026, 7, 18, 8, 11),
    )

    assert "❌ lx｜recovery_required｜snapshot write failed" in text
    assert "unknown error" not in text


def test_nav_receipt_formats_existing_nav_not_final_as_blocker():
    text = NavHistoryReceiptService.build_message(
        {
            "success": False,
            "status": "failed",
            "dry_run": False,
            "date": "2026-07-20",
            "items": [
                {
                    "success": False,
                    "status": "existing_nav_not_final",
                    "account": "lx",
                    "error": "existing row requires classification",
                }
            ],
        },
        executed_at=datetime(2026, 7, 21, 8, 11),
    )

    assert "状态｜❌ 失败" in text
    assert "结果｜写入 0，跳过 0，失败 1" in text
    assert "❌ lx｜existing_nav_not_final｜existing row requires classification" in text


def test_nav_receipt_compacts_healthy_price_summaries_across_accounts():
    text = NavHistoryReceiptService.build_message(
        {
            "success": True,
            "status": "completed",
            "dry_run": False,
            "date": "2026-07-16",
            "items": [
                _written(
                    "lx",
                    warnings=[
                        "[价格汇总] realtime=29, cache=0, stale_fallback=0, missing=0; "
                        "tencent_batch=reqs=1, elapsed_ms=20, returned=15/15"
                    ],
                ),
                _written(
                    "hb",
                    warnings=[
                        "[价格汇总] realtime=14, cache=0, stale_fallback=0, missing=0; "
                        "tencent_batch=reqs=1, elapsed_ms=9, returned=12/12"
                    ],
                ),
                _written(
                    "sy",
                    warnings=[
                        "[价格汇总] realtime=16, cache=0, stale_fallback=0, missing=0; "
                        "tencent_batch=reqs=1, elapsed_ms=9, returned=8/8"
                    ],
                ),
            ],
        },
        executed_at=datetime(2026, 7, 17, 8, 11),
    )

    assert "价格：正常｜实时 59" in text
    assert "tencent_batch" not in text
    assert "elapsed_ms" not in text
    assert "告警：无" not in text


def test_nav_receipt_renders_same_run_reuse_and_accepts_legacy_summary():
    text = NavHistoryReceiptService.build_message(
        {
            "success": True,
            "status": "completed",
            "dry_run": False,
            "date": "2026-07-22",
            "items": [
                _written(
                    "lx",
                    warnings=[
                        "[价格汇总] realtime=3, cache=0, stale_fallback=0, missing=0, run_reused=0"
                    ],
                ),
                _written(
                    "sy",
                    warnings=[
                        "[价格汇总] realtime=5, cache=0, stale_fallback=0, missing=0, run_reused=3"
                    ],
                ),
                _written(
                    "hb",
                    warnings=["[价格汇总] realtime=2, cache=1, stale_fallback=0, missing=0"],
                ),
            ],
        },
        executed_at=datetime(2026, 7, 23, 8, 11),
    )

    assert "价格：正常｜实时 10｜缓存 1｜同轮复用 3" in text


def test_nav_receipt_highlights_only_problematic_price_accounts():
    text = NavHistoryReceiptService.build_message(
        {
            "success": True,
            "status": "completed",
            "dry_run": False,
            "date": "2026-07-16",
            "items": [
                _written(
                    "lx",
                    warnings=["[价格汇总] realtime=28, cache=0, stale_fallback=1, missing=0"],
                ),
                _written(
                    "hb",
                    warnings=["[价格汇总] realtime=13, cache=0, stale_fallback=0, missing=1"],
                ),
            ],
        },
        executed_at=datetime(2026, 7, 17, 8, 11),
    )

    assert "价格：异常｜实时 41｜过期回退 1｜缺失 1（lx 过期回退 1；hb 缺失 1）" in text
    assert "告警：无" not in text


def test_nav_receipt_missing_config_and_send_failure_are_reported():
    missing = NavHistoryReceiptService(app_id="", app_secret="", open_id="").send(
        {"success": False, "dry_run": False}
    )
    assert missing["success"] is False
    assert missing["status"] == "failed"
    assert "feishu.receipt.app_id" in missing["error"]

    class FailedClient:
        def __init__(self, **_kwargs):
            pass

        def send_post_message(self, *, open_id, markdown):
            del open_id, markdown
            raise RuntimeError("send failed")

    failed = NavHistoryReceiptService(
        app_id="cli_app",
        app_secret="secret",
        open_id="ou_user",
        client_factory=FailedClient,
    ).send({"success": False, "dry_run": False, "status": "failed", "error": "nav failed"})
    assert failed == {
        "success": False,
        "status": "failed",
        "channel": "feishu",
        "bot": "刘看山",
        "error": "send failed",
    }


def test_nav_receipt_formats_top_level_skip_without_account_items():
    text = NavHistoryReceiptService.build_message(
        {
            "success": True,
            "status": "skipped_non_business_day",
            "dry_run": False,
            "date": "2026-07-18",
            "items": [],
            "run_id": "run-skip",
        },
        executed_at=datetime(2026, 7, 19, 8, 10),
    )

    assert "状态｜⏭ 无需写入" in text
    assert "结果｜写入 0，跳过 1，失败 0" in text
    assert "Run ID｜run-skip" in text
