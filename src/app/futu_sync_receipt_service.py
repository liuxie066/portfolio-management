"""Send a Feishu receipt after a real Futu holdings synchronization."""
from __future__ import annotations

from typing import Any, Callable, Optional

from src import config
from src.app.notification_shells import render_receipt
from src.feishu_client import FeishuClient


class FutuSyncReceiptService:
    """Best-effort outbound receipt; notification failure never rewrites sync status."""

    def __init__(
        self,
        *,
        app_id: Optional[str] = None,
        app_secret: Optional[str] = None,
        open_id: Optional[str] = None,
        client_factory: Callable[..., Any] = FeishuClient,
    ):
        self.app_id = app_id if app_id is not None else config.get("feishu.receipt.app_id")
        self.app_secret = app_secret if app_secret is not None else config.get("feishu.receipt.app_secret")
        self.open_id = open_id if open_id is not None else config.get("feishu.receipt.open_id")
        self.client_factory = client_factory

    def send(self, sync_result: dict[str, Any]) -> dict[str, Any]:
        if bool(sync_result.get("dry_run", True)):
            return {
                "success": True,
                "status": "skipped",
                "channel": "feishu",
                "bot": "刘看山",
                "reason": "dry_run",
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
                "status": "failed",
                "channel": "feishu",
                "bot": "刘看山",
                "error": f"missing receipt config: {', '.join(missing)}",
            }

        try:
            sent = self.client_factory(
                app_id=str(self.app_id),
                app_secret=str(self.app_secret),
            ).send_text_message(
                open_id=str(self.open_id),
                text=self.build_message(sync_result),
            )
            return {
                "success": True,
                "status": "sent",
                "channel": "feishu",
                "bot": "刘看山",
                "message_id": sent.get("message_id"),
            }
        except Exception as exc:
            return {
                "success": False,
                "status": "failed",
                "channel": "feishu",
                "bot": "刘看山",
                "error": str(exc),
            }

    @staticmethod
    def build_message(sync_result: dict[str, Any]) -> str:
        success = bool(sync_result.get("success"))
        summary = sync_result.get("summary") or {}
        cash_mmf = sync_result.get("cash_mmf") or {}
        fields: list[tuple[str, Any]] = []
        if summary:
            fields.append((
                "股票/ETF",
                f"新增 {summary.get('created', 0)}，"
                f"更新 {summary.get('updated', 0)}，"
                f"清零 {summary.get('zeroed', 0)}，"
                f"数量变化 {summary.get('quantity_changed', 0)}，"
                f"成本变化 {summary.get('cost_changed', 0)}",
            ))
        if cash_mmf:
            fields.append((
                "现金/MMF",
                f"新增 {cash_mmf.get('created', 0)}，更新 {cash_mmf.get('updated', 0)}",
            ))
        if not success:
            fields.append(("错误", sync_result.get("error") or "unknown error"))
        if sync_result.get("partial_write_possible"):
            fields.append((
                "警告",
                f"{sync_result.get('write_stage') or 'unknown'} 阶段可能已部分写入，请先 dry-run 复核",
            ))

        changed = [
            item for item in (sync_result.get("positions") or [])
            if item.get("action") != "unchanged"
        ]
        change_rows: list[str] = []
        for item in changed[:8]:
            details = []
            if item.get("quantity_changed"):
                details.append(
                    f"数量 {_format_number(item.get('current_quantity'))}→{_format_number(item.get('target_quantity'))}"
                )
            if item.get("cost_changed"):
                details.append(
                    f"成本 {_format_cost(item.get('current_avg_cost'))}→{_format_cost(item.get('target_avg_cost'))}"
                )
            change_rows.append(f"{item.get('asset_id')}: {', '.join(details) or item.get('action')}")
        if len(changed) > 8:
            change_rows.append(f"另有 {len(changed) - 8} 项变化")

        return render_receipt(
            title=sync_result.get("account") or "-",
            receipt_type="持仓同步",
            status="✅ 成功" if success else "❌ 失败",
            fields=fields,
            sections=[("持仓变化", change_rows)],
        )


def _format_number(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    return str(int(number)) if number.is_integer() else str(number)


def _format_cost(value: Any) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)
