"""Frozen-payload renderer and Feishu sender for cash-flow attention receipts."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from src import config
from src.feishu_client import FeishuClient

from .cash_flow_event_completion_service import CASH_FLOW_ATTENTION_RECEIPT_TYPE
from .notification_shells import render_receipt


class CashFlowReceiptService:
    SUPPORTED_TYPES = {CASH_FLOW_ATTENTION_RECEIPT_TYPE}

    def __init__(
        self,
        *,
        app_id: Optional[str] = None,
        app_secret: Optional[str] = None,
        open_id: Optional[str] = None,
        client_factory: Callable[..., Any] = FeishuClient,
    ) -> None:
        self.app_id = app_id if app_id is not None else config.get("feishu.receipt.app_id")
        self.app_secret = (
            app_secret
            if app_secret is not None
            else config.get("feishu.receipt.app_secret")
        )
        self.open_id = open_id if open_id is not None else config.get("feishu.receipt.open_id")
        self.client_factory = client_factory

    def send(self, receipt_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if receipt_type not in self.SUPPORTED_TYPES:
            return {
                "success": False,
                "delivery_state": "failed",
                "status": "failed",
                "error": f"unsupported cash flow receipt type: {receipt_type}",
            }
        missing = [
            key
            for key, value in (
                ("feishu.receipt.app_id", self.app_id),
                ("feishu.receipt.app_secret", self.app_secret),
                ("feishu.receipt.open_id", self.open_id),
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
        if receipt_type != CASH_FLOW_ATTENTION_RECEIPT_TYPE:
            raise ValueError(f"unsupported cash flow receipt type: {receipt_type}")
        inputs = dict(payload.get("manual_inputs") or {})
        action = dict(payload.get("action") or {})
        return render_receipt(
            title=str(payload.get("account") or "未归属账户"),
            receipt_type="Cash Flow 数据校验",
            status="⚠️ 需要人工确认",
            fields=[
                ("记录", payload.get("record_id") or "-"),
                ("日期", inputs.get("flow_date") or "-"),
                ("券商", inputs.get("broker") or "-"),
                ("金额", inputs.get("amount")),
                ("币种", inputs.get("currency") or "-"),
                ("原因", payload.get("reason_code") or "-"),
                ("错误", payload.get("error") or "-"),
                ("处理", action.get("command") or "-"),
            ],
        )


__all__ = ["CashFlowReceiptService"]
