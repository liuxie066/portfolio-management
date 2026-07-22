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
_BLOCKING_STATUSES = {
    "failed",
    "partial",
    "cash_flow_check_failed",
    "cash_flow_error",
    "cash_flow_pending",
    "nav_history_duplicate",
    "recovery_required",
    "existing_nav_not_final",
}
_PRICE_SUMMARY_RE = re.compile(
    r"^\[价格汇总\]\s*"
    r"realtime=(?P<realtime>\d+),\s*"
    r"cache=(?P<cache>\d+),\s*"
    r"stale_fallback=(?P<stale>\d+),\s*"
    r"missing=(?P<missing>\d+)"
)
_STATUS_EMOJI = {
    "成功": "✅ 成功",
    "部分失败": "⚠️ 部分失败",
    "失败": "❌ 失败",
    "无需写入": "⏭ 无需写入",
}


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
        self.app_id = app_id if app_id is not None else config.get("feishu.receipt.app_id")
        self.app_secret = app_secret if app_secret is not None else config.get("feishu.receipt.app_secret")
        self.open_id = open_id if open_id is not None else config.get("feishu.receipt.open_id")
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
                text=self.build_message(job_result, executed_at=self.now_factory()),
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

        return render_receipt(
            title="NAV History",
            receipt_type="NAV 记录",
            status=_STATUS_EMOJI.get(title, title),
            fields=fields,
            sections=[
                ("账户明细", [cls._item_row(item) for item in items]),
                ("告警", warning_rows),
            ],
        )

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
            return row

        if status.startswith("skipped_"):
            label = "NAV 已存在" if status == "skipped_existing_nav" else status
            details = []
            if item.get("nav") is not None:
                details.append(f"NAV {_format_nav(item.get('nav'))}")
            if item.get("total_value") is not None:
                details.append(f"总资产 {_format_money(item.get('total_value'))}")
            suffix = f"｜{'｜'.join(details)}" if details else ""
            return f"⏭ {account}｜{label}{suffix}"

        error = item.get("error") or "unknown error"
        return f"❌ {account}｜{status or 'failed'}｜{error}"

    @staticmethod
    def _warning_summary(items: list[dict[str, Any]]) -> tuple[Optional[str], list[str]]:
        warnings: list[str] = []
        price_totals = {"realtime": 0, "cache": 0, "stale": 0, "missing": 0}
        price_accounts: list[str] = []
        for item in items:
            account = item.get("account") or "-"
            report = item.get("report") or {}
            for warning in report.get("warnings") or []:
                match = _PRICE_SUMMARY_RE.match(str(warning))
                if match:
                    counts = {key: int(value) for key, value in match.groupdict().items()}
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
            price_summary = "｜".join(parts)
        else:
            price_summary = f"价格：正常｜实时 {price_totals['realtime']}"
            if price_totals["cache"]:
                price_summary += f"｜缓存 {price_totals['cache']}"

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
