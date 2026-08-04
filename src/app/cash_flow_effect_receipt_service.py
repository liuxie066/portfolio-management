"""Best-effort Feishu delivery for durable cash-flow effect receipts."""
from __future__ import annotations

import shlex
from typing import Any, Callable, Dict, Optional

from src import config
from src.app.notification_shells import render_receipt
from src.feishu_client import FeishuClient

from .cash_flow_effect_store import CashFlowEffectStore


class CashFlowEffectReceiptContractError(ValueError):
    """A durable receipt cannot be rendered and must not be retried."""


class CashFlowEffectReceiptService:
    """Drain the SQLite outbox without changing effect business outcomes."""

    SUPPORTED_TYPES = frozenset({
        "applied",
        "compensation_pending",
        "discovery",
        "record_only",
        "runtime_error",
        "runtime_recovered",
        "stale",
    })

    def __init__(
        self,
        *,
        store: CashFlowEffectStore,
        app_id: Optional[str] = None,
        app_secret: Optional[str] = None,
        open_id: Optional[str] = None,
        client_factory: Callable[..., Any] = FeishuClient,
    ):
        self.store = store
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

    def dispatch_pending(self, *, limit: int = 100) -> Dict[str, Any]:
        rows = self.store.list_pending_receipts(limit=limit)
        sent = failed = invalid = 0
        results: list[Dict[str, Any]] = []
        for row in rows:
            result = self._send(row)
            success = bool(result.get("success"))
            retryable = bool(result.get("retryable", True))
            self.store.mark_receipt(
                row["receipt_key"],
                success=success,
                retryable=retryable,
                message_id=result.get("message_id"),
                error=result.get("error"),
            )
            if row.get("effect_id"):
                event_type = (
                    "receipt_sent"
                    if success
                    else "receipt_failed"
                    if retryable
                    else "receipt_invalid"
                )
                self.store.append_event(
                    row["effect_id"],
                    event_type,
                    {
                        "receipt_key": row["receipt_key"],
                        "message_id": result.get("message_id"),
                        "error": result.get("error"),
                        "retryable": retryable,
                    },
                )
            sent += int(success)
            failed += int(not success)
            invalid += int(not success and not retryable)
            results.append({"receipt_key": row["receipt_key"], **result})
        return {
            "success": failed == 0,
            "attempted": len(rows),
            "sent": sent,
            "failed": failed,
            "invalid": invalid,
            "results": results,
        }

    def _send(self, row: Dict[str, Any]) -> Dict[str, Any]:
        try:
            markdown = self.build_message(row)
        except CashFlowEffectReceiptContractError as exc:
            return {
                "success": False,
                "status": "invalid",
                "retryable": False,
                "channel": "feishu",
                "bot": "刘看山",
                "error": str(exc),
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
            response = self.client_factory(
                app_id=str(self.app_id),
                app_secret=str(self.app_secret),
            ).send_post_message(
                open_id=str(self.open_id),
                markdown=markdown,
            )
            return {
                "success": True,
                "status": "sent",
                "channel": "feishu",
                "bot": "刘看山",
                "message_id": response.get("message_id"),
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
    def build_message(row: Dict[str, Any]) -> str:
        receipt_type = str(row.get("receipt_type") or "unknown")
        if receipt_type not in CashFlowEffectReceiptService.SUPPORTED_TYPES:
            raise CashFlowEffectReceiptContractError(
                "unsupported cash-flow effect receipt_type: "
                f"{receipt_type}"
            )
        payload = dict(row.get("payload") or {})
        effect_id = payload.get("effect_id") or row.get("effect_id")
        if receipt_type == "discovery":
            return render_receipt(
                title=str(payload.get("scope") or "all"),
                receipt_type="Cash Flow 发现",
                status="⚠️ 待逐条处理",
                fields=[
                    (
                        "扫描",
                        payload.get("scan_digest") or row.get("scan_run_id") or "-",
                    ),
                    (
                        "变化",
                        f"新增 {payload.get('added', 0)}，"
                        f"修改 {payload.get('changed', 0)}，"
                        f"删除 {payload.get('deleted', 0)}，"
                        f"阻断 {payload.get('blocked', 0)}",
                    ),
                    ("处理", "pm cash-flow review"),
                ],
                sections=[
                    (
                        "变化明细",
                        [
                            (
                                f"{item.get('effect_id')} · {item.get('account')} · "
                                f"{item.get('flow_date')} · {item.get('broker')} · "
                                f"{item.get('currency')} {item.get('signed_amount')} · "
                                f"{item.get('state')}"
                            )
                            for item in (payload.get("effects") or [])[:10]
                        ],
                    )
                ],
            )
        if receipt_type in {"runtime_error", "runtime_recovered"}:
            recovered = receipt_type == "runtime_recovered"
            return render_receipt(
                title=str(payload.get("scope") or "all"),
                receipt_type="Cash Flow 扫描",
                status="✅ 已恢复" if recovered else "❌ 运行异常",
                fields=[
                    ("扫描", payload.get("scan_run_id") or "-"),
                    ("错误", payload.get("error") or "-"),
                ],
            )
        if receipt_type == "stale":
            correction_effect_id = payload.get("correction_effect_id")
            fields: list[tuple[str, Any]] = [
                ("Effect", effect_id or "-"),
                ("状态", payload.get("state") or "stale"),
            ]
            if correction_effect_id:
                fields.extend([
                    ("原因", "原 Effect 已处理，但 Cash Flow 事实随后变化"),
                    ("修正 Effect", correction_effect_id),
                    (
                        "下一步",
                        _effect_command(
                            "preview",
                            correction_effect_id,
                            suffix="--json",
                        ),
                    ),
                ])
            else:
                fields.append(("原因", "预览依据已变化，原确认不可继续使用"))
                if payload.get("provided_preview_hash"):
                    fields.append(("原预览", payload["provided_preview_hash"]))
                if payload.get("current_preview_hash"):
                    fields.append(("当前预览", payload["current_preview_hash"]))
                fields.append((
                    "下一步",
                    _effect_command("preview", effect_id, suffix="--json"),
                ))
            return render_receipt(
                title=str(payload.get("account") or "-"),
                receipt_type="Cash Flow 处理",
                status="⚠️ 需重新确认",
                fields=fields,
            )
        if receipt_type == "compensation_pending":
            target_count = _nonnegative_int(payload.get("target_count"))
            confirmed_count = _nonnegative_int(
                payload.get("confirmed_target_count")
            )
            if (
                target_count is not None
                and confirmed_count is not None
                and confirmed_count <= target_count
            ):
                scope = (
                    f"已确认 {confirmed_count}/{target_count}；"
                    f"未确认 {target_count - confirmed_count}"
                )
            else:
                scope = "未记录；以补偿任务为准"
            return render_receipt(
                title=str(payload.get("account") or "-"),
                receipt_type="Cash Flow 处理",
                status="❌ 可能部分写入",
                fields=[
                    ("Effect", effect_id or "-"),
                    ("状态", payload.get("state") or receipt_type),
                    ("错误", payload.get("error") or "未记录；请查看补偿任务"),
                    ("写入范围", scope),
                    (
                        "补偿任务",
                        payload.get("task_id")
                        or payload.get("compensation_task_id")
                        or "-",
                    ),
                    (
                        "下一步",
                        _effect_command(
                            "retry",
                            effect_id,
                            suffix="--confirm",
                        ),
                    ),
                ],
                sections=[
                    (
                        "未确认目标",
                        [
                            _cash_target_row(item)
                            for item in (
                                payload.get("unconfirmed_targets") or []
                            )
                            if isinstance(item, dict)
                        ],
                    ),
                ],
            )

        status = (
            "✅ 已处理"
            if receipt_type in {"applied", "record_only"}
            else "❌ 可能部分写入"
            if receipt_type == "compensation_pending"
            else "⚠️ 需重新确认"
        )
        targets = list(payload.get("targets") or [])
        befores = list(payload.get("befores") or [])
        if not befores and isinstance(payload.get("before"), dict):
            befores = [dict(payload["before"])]
        cash_rows = []
        for index, target in enumerate(targets):
            if not isinstance(target, dict):
                continue
            before = (
                befores[index]
                if index < len(befores) and isinstance(befores[index], dict)
                else {}
            )
            cash_rows.append(
                f"{target.get('account') or '-'} · "
                f"{target.get('broker') or '-'} · "
                f"{target.get('currency') or '-'} "
                f"{before.get('quantity', '0.00')} → "
                f"{target.get('quantity', '-')}"
            )
        return render_receipt(
            title=str(payload.get("account") or "-"),
            receipt_type="Cash Flow 处理",
            status=status,
            fields=[
                ("Effect", effect_id or "-"),
                ("状态", payload.get("state") or receipt_type),
                ("券商", payload.get("broker") or "-"),
                (
                    "事件",
                    f"{payload.get('flow_date') or '-'} · "
                    f"{payload.get('currency') or '-'} "
                    f"{payload.get('signed_amount') or '-'}",
                ),
                ("目标数", len(targets)),
                ("目标来源", payload.get("target_source") or "-"),
                ("Run", payload.get("run_id") or "-"),
                (
                    "补偿",
                    payload.get("task_id")
                    or payload.get("compensation_task_id")
                    or "-",
                ),
            ],
            sections=[
                ("现金目标", cash_rows),
                ("告警", list(payload.get("warnings") or [])),
            ],
        )


def _effect_command(action: str, effect_id: Any, *, suffix: str) -> str:
    value = str(effect_id or "").strip()
    if not value:
        return "pm cash-flow review --json"
    return (
        f"pm cash-flow effects {action} --effect-id {shlex.quote(value)} "
        f"{suffix}"
    )


def _nonnegative_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _cash_target_row(target: Dict[str, Any]) -> str:
    identity = " · ".join(
        str(target.get(key) or "-")
        for key in ("account", "broker", "asset_id")
    )
    quantity = target.get("quantity")
    return identity if quantity is None else f"{identity} → {quantity}"


__all__ = ["CashFlowEffectReceiptService"]
