from src.app.notification_shells import render_receipt


def test_render_receipt_flattens_fields_and_omits_empty_sections() -> None:
    message = render_receipt(
        title="lx\nops",
        receipt_type="NAV 记录",
        status="⚠️ 部分失败",
        fields=(("说明", "first line\n  - second line"), ("次数", 0), ("空值", None)),
        sections=(
            ("账户明细", ["✅ lx｜NAV 1.000000\n总资产 ¥100.00", ""]),
            ("空节", []),
        ),
    )

    assert message == (
        "# PM · 回执 · lx · ops\n\n"
        "类型｜NAV 记录\n"
        "状态｜⚠️ 部分失败\n"
        "说明｜first line · - second line\n"
        "次数｜0\n"
        "空值｜-\n\n"
        "## 账户明细\n"
        "✅ lx｜NAV 1.000000 · 总资产 ¥100.00"
    )
    assert "空节" not in message


def test_render_receipt_without_fields_or_sections() -> None:
    message = render_receipt(title="NAV History", receipt_type="NAV 记录", status="✅ 成功")

    assert message == "# PM · 回执 · NAV History\n\n类型｜NAV 记录\n状态｜✅ 成功"
