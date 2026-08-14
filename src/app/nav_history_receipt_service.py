"""Send one consolidated Feishu receipt after a real daily NAV job."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

from src import config
from src.app.notification_shells import render_receipt
from src.feishu_client import FeishuClient


_BEIJING = ZoneInfo("Asia/Shanghai")
_MAX_HOLDINGS_ACTION_ITEMS_PER_SCOPE = 5
_BLOCKING_STATUSES = {
    "failed",
    "partial",
    "cash_flow_check_failed",
    "cash_flow_error",
    "cash_flow_pending",
    "nav_history_duplicate",
    "recovery_required",
    "existing_nav_not_final",
    "holdings_confirmation_required",
    "holdings_evidence_unavailable",
    "holdings_preflight_failed",
}
_PRICE_SUMMARY_RE = re.compile(
    r"^\[价格汇总\]\s*"
    r"realtime=(?P<realtime>\d+),\s*"
    r"cache=(?P<cache>\d+),\s*"
    r"stale_fallback=(?P<stale>\d+),\s*"
    r"missing=(?P<missing>\d+)"
    r"(?:,\s*run_reused=(?P<run_reused>\d+))?"
)
_STATUS_EMOJI = {
    "成功": "✅ 成功",
    "部分失败": "⚠️ 部分失败",
    "失败": "❌ 失败",
    "无需写入": "⏭ 无需写入",
}


def _nonnegative_int(value: Any, *, default: int = 0) -> int:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return max(int(default), 0)


class NavHistoryReceiptService:
    """Best-effort NAV receipt; notification failure never rewrites job status."""

    def __init__(
        self,
        *,
        app_id: Optional[str] = None,
        app_secret: Optional[str] = None,
        open_id: Optional[str] = None,
        client_factory: Callable[..., Any] = FeishuClient,
        now_factory: Optional[Callable[[], datetime]] = None,
    ):
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
        self.now_factory = now_factory or (lambda: datetime.now(_BEIJING))

    def send(self, job_result: dict[str, Any]) -> dict[str, Any]:
        if bool(job_result.get("dry_run", True)):
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
                ("feishu.agent.app_id", self.app_id),
                ("feishu.agent.app_secret", self.app_secret),
                ("feishu.agent.open_id", self.open_id),
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
                markdown=self.build_message(job_result, executed_at=self.now_factory()),
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

    @classmethod
    def build_message(
        cls,
        job_result: dict[str, Any],
        *,
        executed_at: Optional[datetime] = None,
    ) -> str:
        items = list(job_result.get("items") or [])
        written, skipped, failed = cls._counts(job_result, items)
        title = cls._title(job_result, written=written, skipped=skipped, failed=failed)
        now = executed_at or datetime.now(_BEIJING)
        if now.tzinfo is None:
            now = now.replace(tzinfo=_BEIJING)
        else:
            now = now.astimezone(_BEIJING)

        fields: list[tuple[str, Any]] = [
            ("时间", f"{now.strftime('%Y-%m-%d %H:%M')} 北京时间"),
            ("NAV 日期", job_result.get("date")),
            ("结果", f"写入 {written}，跳过 {skipped}，失败 {failed}"),
        ]
        if not items and job_result.get("error"):
            fields.append(("错误", job_result.get("error")))
        if job_result.get("run_id"):
            fields.append(("Run ID", job_result["run_id"]))

        price_summary, warnings = cls._warning_summary(items)
        warning_rows = ([price_summary] if price_summary else []) + list(warnings)
        holdings_rows = cls._holdings_preflight_rows(job_result, items)

        return render_receipt(
            title="NAV History",
            receipt_type="NAV 记录",
            status=_STATUS_EMOJI.get(title, title),
            fields=fields,
            sections=[
                ("Holdings 预检", holdings_rows),
                ("账户明细", [cls._item_row(item) for item in items]),
                ("告警", warning_rows),
            ],
        )

    @classmethod
    def _holdings_preflight_rows(
        cls,
        job_result: dict[str, Any],
        items: list[dict[str, Any]],
    ) -> list[str]:
        rows: list[str] = []
        global_preflight = job_result.get("global_holdings_preflight")
        if isinstance(global_preflight, dict):
            rows.extend(cls._holdings_scope_rows("全局", global_preflight))
        for item in items:
            preflight = item.get("holdings_preflight")
            if not isinstance(preflight, dict):
                continue
            rows.extend(
                cls._holdings_scope_rows(
                    str(item.get("account") or preflight.get("account") or "-"),
                    preflight,
                )
            )
        return rows

    @staticmethod
    def _holdings_scope_rows(
        scope: str,
        preflight: dict[str, Any],
    ) -> list[str]:
        workflow = dict(preflight.get("workflow") or {})
        counts = [
            ("新增", len(workflow.get("created_case_keys") or [])),
            ("重开", len(workflow.get("reopened_case_keys") or [])),
            ("关闭", len(workflow.get("closed_case_keys") or [])),
            ("替代", len(workflow.get("superseded_case_keys") or [])),
            (
                "待处理",
                len(
                    preflight.get("pending_case_keys")
                    if preflight.get("pending_case_keys") is not None
                    else (preflight.get("case_keys") or [])
                ),
            ),
            ("阻断", len(preflight.get("blocking_case_keys") or [])),
        ]
        raw_action_items = [
            item
            for item in list(preflight.get("action_items") or [])
            if isinstance(item, dict)
        ]
        action_items = raw_action_items[:_MAX_HOLDINGS_ACTION_ITEMS_PER_SCOPE]
        action_count = max(
            _nonnegative_int(
                preflight.get("action_item_count")
                if preflight.get("action_item_count") is not None
                else len(raw_action_items),
                default=len(raw_action_items),
            ),
            len(raw_action_items),
        )
        omitted_count = max(
            _nonnegative_int(preflight.get("action_item_omitted_count")),
            action_count - len(action_items),
            len(raw_action_items) - len(action_items),
        )
        if not any(count for _, count in counts) and not action_count:
            return []

        count_text = "｜".join(
            f"{label} {count}" for label, count in counts if count
        )
        rows = [f"{scope}｜{count_text}"] if count_text else []
        for item in action_items:
            rows.append(
                f"{scope}｜Case {item.get('case_key') or '-'}"
                f"｜记录 {item.get('record_id') or '-'}"
                f"｜字段 {item.get('field') or '-'}"
                f"｜状态 {item.get('state') or '-'}"
                f"｜处理 {item.get('command') or '-'}"
            )
        if omitted_count:
            rows.append(
                f"{scope}｜另有 {omitted_count} 条行动项未展开，请从 Holdings Case 审计查询"
            )
        return rows

    @staticmethod
    def _counts(job_result: dict[str, Any], items: list[dict[str, Any]]) -> tuple[int, int, int]:
        written = 0
        skipped = 0
        failed = 0
        for item in items:
            status = str(item.get("status") or "")
            if status == "written":
                written += 1
            elif status.startswith("skipped_"):
                skipped += 1
            elif item.get("success") is False or status in _BLOCKING_STATUSES:
                failed += 1
        if not items:
            status = str(job_result.get("status") or "")
            if status.startswith("skipped_"):
                skipped = 1
            elif job_result.get("success") is False or status in _BLOCKING_STATUSES:
                failed = 1
        return written, skipped, failed

    @staticmethod
    def _title(job_result: dict[str, Any], *, written: int, skipped: int, failed: int) -> str:
        if failed and (written or skipped):
            return "部分失败"
        if failed or job_result.get("success") is False:
            return "失败"
        if written:
            return "成功"
        if skipped:
            return "无需写入"
        return "成功"

    @classmethod
    def _item_row(cls, item: dict[str, Any]) -> str:
        account = item.get("account") or "-"
        status = str(item.get("status") or "")
        holdings_snapshot = item.get("holdings_snapshot") or {}
        holdings_digest = (
            item.get("holdings_digest")
            or holdings_snapshot.get("normalized_holdings_digest")
            or item.get("normalized_holdings_digest")
            or item.get("raw_record_digest")
        )
        digest_text = (
            f"｜Holdings {str(holdings_digest)[:12]}"
            if holdings_digest
            else ""
        )
        if status == "written":
            report = item.get("report") or {}
            overview = report.get("overview") or {}
            row = (
                f"✅ {account}｜NAV {_format_nav(report.get('nav'))}"
                f"｜总资产 {_format_money(report.get('total_value'))}"
                f"｜当期盈亏 {_format_signed_money(report.get('pnl'))}"
                f"｜YTD NAV {_format_signed_pct(report.get('ytd_nav_change'))}"
                f"｜股票 {_format_pct(overview.get('stock_ratio'))}"
                f"｜基金 {_format_pct(overview.get('fund_ratio'))}"
                f"｜现金 {_format_pct(overview.get('cash_ratio'))}"
            )
            cash_flow = _as_float(report.get("cash_flow"))
            if cash_flow not in (None, 0.0):
                row += f"｜资金变动 {_format_signed_money(cash_flow)}"
            return row + digest_text

        if status.startswith("skipped_"):
            label = "NAV 已存在" if status == "skipped_existing_nav" else status
            details = []
            if item.get("nav") is not None:
                details.append(f"NAV {_format_nav(item.get('nav'))}")
            if item.get("total_value") is not None:
                details.append(f"总资产 {_format_money(item.get('total_value'))}")
            suffix = f"｜{'｜'.join(details)}" if details else ""
            return f"⏭ {account}｜{label}{suffix}{digest_text}"

        cash_flow_row = cls._cash_flow_failure_row(item)
        if cash_flow_row:
            return cash_flow_row + digest_text

        error = item.get("error") or "unknown error"
        stage = str(item.get("stage") or "").strip()
        stage_text = f"｜阶段 {stage}" if stage else ""
        return (
            f"❌ {account}｜{status or 'failed'}{stage_text}｜{error}"
            f"{digest_text}"
        )

    @staticmethod
    def _cash_flow_failure_row(item: dict[str, Any]) -> Optional[str]:
        failure = item.get("failure")
        if not isinstance(failure, dict):
            return None
        account = item.get("account") or "-"
        code = str(failure.get("code") or "")
        if not code.startswith("CASH_FLOW_"):
            return None
        if code != "CASH_FLOW_DATASET_BLOCKED":
            return (
                f"❌ {account}｜Cash Flow 数据校验未通过"
                "｜处理：请根据本回执 Run ID 排查后重试"
            )
        blockers = [
            blocker
            for blocker in list(failure.get("blockers") or [])
            if isinstance(blocker, dict)
        ]
        effect_blockers: list[dict[str, Any]] = []
        for blocker in blockers:
            if blocker.get("reason_code") != "EFFECT_GATE_BLOCKED":
                continue
            details = blocker.get("details")
            gate = details.get("gate") if isinstance(details, dict) else None
            if isinstance(gate, dict):
                effect_blockers.extend(
                    row
                    for row in list(gate.get("blockers") or [])
                    if isinstance(row, dict)
                )
        if not effect_blockers:
            first = blockers[0] if blockers else {}
            if str(first.get("reason_code") or "").startswith("EFFECT_"):
                return (
                    f"❌ {account}｜Cash Flow 处理状态校验未完成"
                    "｜处理：请根据本回执 Run ID 排查 effect gate 后重试"
                )
            record = first.get("record_id") or "-"
            field = first.get("field") or "-"
            return (
                f"❌ {account}｜Cash Flow 数据待修正"
                f"｜记录 {record}｜字段 {field}"
                "｜处理：修正 Cash Flow 表后按本回执 Run ID 重试"
            )

        effect = effect_blockers[0]
        operations = [
            operation
            for operation in list(effect.get("operations") or [])
            if isinstance(operation, dict)
        ]
        operation = operations[0] if operations else effect
        kind = str(effect.get("effect_kind") or "")
        state = str(effect.get("state") or "")
        broker = str(operation.get("broker") or "").strip()
        currency = str(operation.get("currency") or "").strip()
        identity = " ".join(part for part in (broker, currency) if part)
        identity_text = f"｜{identity}" if identity else ""
        amount = _as_float(operation.get("signed_amount"))
        amount_text = (
            f"｜金额 {amount:+,.2f}{f' {currency}' if currency else ''}"
            if amount is not None and amount != 0
            else ""
        )
        date_text = (
            f"｜日期 {operation['flow_date']}"
            if operation.get("flow_date")
            else ""
        )
        if state == "compensation_pending":
            label = "Cash Flow 写入恢复未完成"
            action = (
                "运行 pm cash-flow effects retry "
                f"--effect-id {effect.get('effect_id') or 'EFFECT_ID'} --confirm"
            )
        elif kind == "cash_flow":
            label = "出入金待确认"
            action = (
                f"运行 pm cash-flow review --account {account} --json，"
                "按提示预览并确认"
            )
        elif broker == "富途" or kind == "broker_cash_reconciliation":
            label = "Futu 现金待核对"
            action = (
                "检查 OpenD 后运行 "
                f"pm cash-flow review --account {account} --json"
            )
        else:
            label = "现金余额待处理"
            action = f"运行 pm cash-flow review --account {account} --json"
        omitted = len(effect_blockers) - 1 + max(len(operations) - 1, 0)
        omitted_text = f"｜另有 {omitted} 项" if omitted else ""
        return (
            f"❌ {account}｜{label}{identity_text}{amount_text}{date_text}"
            f"｜处理：{action}{omitted_text}"
        )

    @staticmethod
    def _warning_summary(items: list[dict[str, Any]]) -> tuple[Optional[str], list[str]]:
        warnings: list[str] = []
        price_totals = {"realtime": 0, "cache": 0, "stale": 0, "missing": 0, "run_reused": 0}
        price_accounts: list[str] = []
        for item in items:
            account = item.get("account") or "-"
            report = item.get("report") or {}
            for warning in report.get("warnings") or []:
                match = _PRICE_SUMMARY_RE.match(str(warning))
                if match:
                    counts = {key: int(value or 0) for key, value in match.groupdict().items()}
                    for key, value in counts.items():
                        price_totals[key] += value
                    account_issues = []
                    if counts["stale"]:
                        account_issues.append(f"过期回退 {counts['stale']}")
                    if counts["missing"]:
                        account_issues.append(f"缺失 {counts['missing']}")
                    if account_issues:
                        price_accounts.append(f"{account} {'、'.join(account_issues)}")
                    continue
                warnings.append(f"{account}: {warning}")

        if not any(price_totals.values()):
            price_summary = None
        elif price_totals["missing"] or price_totals["stale"]:
            status = "异常" if price_totals["missing"] else "需关注"
            parts = [f"价格：{status}", f"实时 {price_totals['realtime']}"]
            if price_totals["cache"]:
                parts.append(f"缓存 {price_totals['cache']}")
            if price_totals["stale"]:
                parts.append(f"过期回退 {price_totals['stale']}")
            if price_totals["missing"]:
                parts.append(f"缺失 {price_totals['missing']}")
            if price_totals["run_reused"]:
                parts.append(f"同轮复用 {price_totals['run_reused']}")
            price_summary = "｜".join(parts)
        else:
            price_summary = f"价格：正常｜实时 {price_totals['realtime']}"
            if price_totals["cache"]:
                price_summary += f"｜缓存 {price_totals['cache']}"
            if price_totals["run_reused"]:
                price_summary += f"｜同轮复用 {price_totals['run_reused']}"

        if price_summary and price_accounts:
            price_summary += f"（{'；'.join(price_accounts)}）"
        return price_summary, warnings


def _as_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_nav(value: Any) -> str:
    number = _as_float(value)
    return f"{number:.6f}" if number is not None else "-"


def _format_money(value: Any) -> str:
    number = _as_float(value)
    return f"¥{number:,.2f}" if number is not None else "-"


def _format_signed_money(value: Any) -> str:
    number = _as_float(value)
    if number is None:
        return "-"
    sign = "+" if number >= 0 else "-"
    return f"{sign}¥{abs(number):,.2f}"


def _format_pct(value: Any) -> str:
    number = _as_float(value)
    return f"{number * 100:.2f}%" if number is not None else "-"


def _format_signed_pct(value: Any) -> str:
    number = _as_float(value)
    if number is None:
        return "-"
    sign = "+" if number >= 0 else "-"
    return f"{sign}{abs(number) * 100:.2f}%"
