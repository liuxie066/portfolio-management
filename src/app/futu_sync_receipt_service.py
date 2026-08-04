"""Send a Feishu receipt after a real Futu holdings synchronization."""
from __future__ import annotations

import shlex
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
        self.app_id = (
            app_id
            if app_id is not None
            else config.get("feishu.conversation.app_id")
        )
        self.app_secret = (
            app_secret
            if app_secret is not None
            else config.get("feishu.conversation.app_secret")
        )
        self.open_id = (
            open_id
            if open_id is not None
            else config.get("feishu.conversation.open_id")
        )
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
                ("feishu.conversation.app_id", self.app_id),
                ("feishu.conversation.app_secret", self.app_secret),
                ("feishu.conversation.open_id", self.open_id),
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
            ).send_post_message(
                open_id=str(self.open_id),
                markdown=self.build_message(sync_result),
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
        stages = sync_result.get("stages") or cash_mmf.get("stages") or {}
        fields: list[tuple[str, Any]] = []
        if not success:
            failed_stage = _failed_stage(stages) or str(
                sync_result.get("write_stage") or "unknown"
            )
            fields.extend([
                ("失败阶段", failed_stage),
                (
                    "错误",
                    sync_result.get("error")
                    or cash_mmf.get("error")
                    or "未返回错误详情",
                ),
            ])
            if sync_result.get("sync_run_id"):
                fields.append(("Run", sync_result["sync_run_id"]))
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
            mmf_counts_confirmed = (
                cash_mmf.get("success") is True
                and "created" in cash_mmf
                and "updated" in cash_mmf
            )
            mmf_result = (
                f"MMF 新增 {cash_mmf.get('created', 0)}，"
                f"更新 {cash_mmf.get('updated', 0)}"
                if mmf_counts_confirmed
                else "MMF 结果未确认"
            )
            fields.append((
                "CASH / MMF",
                "富途原币余额仅观测；PM 使用 CNY-CASH 人民币汇总，不做金额对账；"
                f"{mmf_result}",
            ))
        partial_write_possible = bool(
            sync_result.get("partial_write_possible")
            or cash_mmf.get("partial_write_possible")
            or any(
                isinstance(stage, dict)
                and stage.get("partial_write_possible")
                for stage in stages.values()
            )
        )
        if partial_write_possible:
            failed_stage = _failed_stage(stages) or str(
                sync_result.get("write_stage") or "unknown"
            )
            fields.append((
                "警告",
                f"{failed_stage} 阶段可能已部分写入，请先 dry-run 复核",
            ))
        if not success:
            fields.append(("下一步", _futu_dry_run_command(sync_result.get("account"))))

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

        stage_rows = _stage_rows(stages) if not success else []

        return render_receipt(
            title=sync_result.get("account") or "-",
            receipt_type="持仓同步",
            status="✅ 成功" if success else "❌ 失败",
            fields=fields,
            sections=[
                ("执行阶段", stage_rows),
                ("持仓变化", change_rows),
            ],
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


def _failed_stage(stages: Any) -> Optional[str]:
    if not isinstance(stages, dict):
        return None
    for name, details in stages.items():
        if isinstance(details, dict) and details.get("status") == "failed":
            return str(name)
    return None


def _stage_rows(stages: Any) -> list[str]:
    if not isinstance(stages, dict):
        return []
    labels = {
        "failed": "失败",
        "not_run": "未执行",
        "pending": "未执行",
        "started": "进行中",
        "succeeded": "成功",
    }
    rows = []
    for name, details in stages.items():
        if not isinstance(details, dict):
            continue
        status = str(details.get("status") or "unknown")
        label = labels.get(status, status)
        if details.get("partial_write_possible"):
            label = f"{label} · 可能部分写入"
        rows.append(f"{name} · {label}")
    return rows


def _futu_dry_run_command(account: Any) -> str:
    value = str(account or "").strip()
    account_arg = f" --account {shlex.quote(value)}" if value else ""
    return f"pm futu sync{account_arg} --dry-run --json"
