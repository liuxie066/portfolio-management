"""Frozen-payload renderer and Feishu sender for holdings workflow receipts."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from src import config
from src.feishu_client import FeishuClient

from .notification_shells import render_receipt


class HoldingsReceiptService:
    SUPPORTED_TYPES = {
        "holding_case_discovered",
        "holding_case_closed",
        "holding_case_attention_required",
    }

    def __init__(
        self,
        *,
        app_id: Optional[str] = None,
        app_secret: Optional[str] = None,
        open_id: Optional[str] = None,
        client_factory: Callable[..., Any] = FeishuClient,
    ) -> None:
        self.app_id = (
            app_id
            if app_id is not None
            else config.get("feishu.agent.app_id")
        )
        self.app_secret = (
            app_secret
            if app_secret is not None
            else config.get("feishu.agent.app_secret")
        )
        self.open_id = (
            open_id
            if open_id is not None
            else config.get("feishu.agent.open_id")
        )
        self.client_factory = client_factory

    def send(self, receipt_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if receipt_type not in self.SUPPORTED_TYPES:
            return {
                "success": False,
                "delivery_state": "failed",
                "status": "failed",
                "error": f"unsupported holdings receipt type: {receipt_type}",
            }
        missing = [
            key
            for key, value in (
                ("feishu.agent.app_id", self.app_id),
                ("feishu.agent.app_secret", self.app_secret),
                ("feishu.agent.open_id", self.open_id),
            )
            if not str(value or "").strip()
        ]
        if missing:
            return {
                "success": False,
                "delivery_state": "failed",
                "status": "failed",
                "channel": "feishu",
                "bot": "刘看山",
                "error": f"missing receipt config: {', '.join(missing)}",
            }
        try:
            response = self.client_factory(
                app_id=str(self.app_id),
                app_secret=str(self.app_secret),
            ).send_post_message(
                open_id=str(self.open_id),
                markdown=self.build_message(receipt_type, payload),
            )
            return {
                "success": True,
                "delivery_state": "accepted",
                "status": "sent",
                "channel": "feishu",
                "bot": "刘看山",
                "message_id": response.get("message_id"),
            }
        except Exception as exc:
            return {
                "success": False,
                "delivery_state": "unknown",
                "status": "unknown",
                "channel": "feishu",
                "bot": "刘看山",
                "error": str(exc) or exc.__class__.__name__,
            }

    @staticmethod
    def build_message(receipt_type: str, payload: Dict[str, Any]) -> str:
        action = dict(payload.get("action") or {})
        identity = dict(payload.get("identity") or {})
        if receipt_type == "holding_case_discovered":
            state = str(payload.get("state") or "pending")
            status = (
                "⚠️ 待确认"
                if state == "pending_confirmation"
                else "⚠️ 待人工修复"
                if state == "pending_manual_edit"
                else "⚠️ 可确认补全"
            )
            return render_receipt(
                title=str(payload.get("account") or "未归属账户"),
                receipt_type="Holdings 数据校验",
                status=status,
                fields=[
                    ("Case", payload.get("case_key") or "-"),
                    ("记录", payload.get("record_id") or "-"),
                    ("标的", identity.get("asset_id") or "-"),
                    ("券商", identity.get("broker") or "-"),
                    ("字段", payload.get("field") or "-"),
                    ("当前", payload.get("current")),
                    ("建议", payload.get("proposed")),
                    ("依据", payload.get("authority") or payload.get("reason_code") or "-"),
                    ("证据时间", payload.get("evidence_as_of") or "-"),
                    ("阻断 NAV", "是" if payload.get("blocks_official_nav") else "否"),
                    ("处理", action.get("command") or "-"),
                ],
            )
        if receipt_type == "holding_case_attention_required":
            return render_receipt(
                title=str(payload.get("account") or "未归属账户"),
                receipt_type="Holdings 写入状态",
                status="❌ 结果未知，禁止自动重试",
                fields=[
                    ("Case", payload.get("case_key") or "-"),
                    ("记录", payload.get("record_id") or "-"),
                    ("字段", payload.get("field") or "-"),
                    ("Attempt", payload.get("apply_attempt_id") or "-"),
                    ("写前", payload.get("before")),
                    ("目标", payload.get("target")),
                    ("读回", payload.get("readback")),
                    ("错误", payload.get("patch_error") or payload.get("read_error") or "-"),
                    ("处理", action.get("command") or "-"),
                ],
            )
        return render_receipt(
            title=str(payload.get("account") or "未归属账户"),
            receipt_type="Holdings 处理结果",
            status="✅ 已闭环",
            fields=[
                ("Case", payload.get("case_key") or "-"),
                ("记录", payload.get("record_id") or "-"),
                ("字段", payload.get("field") or "-"),
                ("状态", payload.get("terminal_state") or "-"),
                ("决策", payload.get("decision") or "-"),
                ("理由", payload.get("reason") or "-"),
                ("写前", payload.get("before")),
                ("目标", payload.get("target")),
                ("读回", payload.get("readback")),
            ],
        )


__all__ = ["HoldingsReceiptService"]
