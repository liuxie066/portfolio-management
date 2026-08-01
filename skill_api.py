#!/usr/bin/env python3
"""
Compatibility Python API adapter for portfolio-management.

The product entrypoints are the CLI and local service. This module preserves
the historical Skill/Python function surface by delegating to service/app/domain
boundaries; new business behavior should not be implemented here.
"""
import sys
from pathlib import Path

from typing import Dict, Any, Optional

# 确保能 import 到 src 模块
SKILL_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SKILL_DIR))

from src.feishu_storage import FeishuStorage, FeishuClient
from src.portfolio import PortfolioManager
from src.price_fetcher import PriceFetcher
from src.models import AssetType, AssetClass, Industry, Holding
from src.asset_utils import (
    detect_asset_type,
    parse_date,
)
from src.app import AccountNavRecorderService, FutuBalanceSyncService, PortfolioReadService, ReportGenerationService, ReportQueryService
from src.app.account_service import AccountService, normalize_accounts
from src.app.audit_service import AuditService
from src.domain.nav.performance import (
    calc_month_return,
    calc_since_inception_return,
    calc_year_return,
)
from src.domain.holding_mutations import (
    HOLDING_REQUIRED_VALUE_FIELDS,
    HoldingTarget,
)
from src.service.application import PortfolioService
from src import config


# ========== 配置 ==========

DEFAULT_ACCOUNT = config.get_account()


# ========== 兼容 API adapter ==========

class PortfolioSkill:
    """Backward-compatible adapter for historical Skill callers.

    Keep this class thin. Product workflows belong in `src/service`,
    `src/app`, `src/domain`, `src/pricing`, or storage repositories.
    """

    def build_snapshot(self, price_timeout_seconds: Optional[int] = None) -> Dict[str, Any]:
        """构建统一估值快照，供 full_report / record_nav 复用，避免时点差。"""
        return self._read_service().build_snapshot(price_timeout_seconds=price_timeout_seconds)

    def audit_nav_history_metrics(self, account: Optional[str] = None, days: int = 900, write_report: bool = True) -> Dict[str, Any]:
        """审计 nav_history 四个核心派生字段，与当前代码公式逐条比对。"""
        return self._audit_service.audit_nav_history_metrics(account=account, days=days, write_report=write_report)

    def audit_nav_history_reconcile(self, account: Optional[str] = None, days: int = 900, write_report: bool = True) -> Dict[str, Any]:
        """按日期顺序对 nav_history 做历史对账，输出 ok / exempt / anomaly。"""
        return self._audit_service.audit_nav_history_reconcile(account=account, days=days, write_report=write_report)

    def audit_nav_history_accuracy(self, account: Optional[str] = None, days: int = 900, write_report: bool = True) -> Dict[str, Any]:
        """统一准确性审计入口：汇总 metrics / reconcile / repair candidates。"""
        return self._audit_service.audit_nav_history_accuracy(account=account, days=days, write_report=write_report)

    def repair_nav_history_metrics(self, account: Optional[str] = None, days: int = 900, dry_run: bool = True, write_report: bool = True) -> Dict[str, Any]:
        """按统一准确性审计结果修复 nav_history 派生字段；仅修复真正 anomaly，默认 dry_run。"""
        return self._audit_service.repair_nav_history_metrics(account=account, days=days, dry_run=dry_run, write_report=write_report)

    def __init__(
        self,
        account: str = DEFAULT_ACCOUNT,
        feishu_client: FeishuClient = None,
        storage: Optional[FeishuStorage] = None,
        portfolio: Optional[PortfolioManager] = None,
        price_fetcher: Optional[PriceFetcher] = None,
    ):
        """
        初始化 Skill

        Args:
            account: 账户标识，默认 "lx"
            feishu_client: 飞书客户端实例（可选，用于自定义配置）
            storage: 存储实例（可选，用于测试或离线注入）
            portfolio: PortfolioManager 实例（可选，用于测试或离线注入）
            price_fetcher: 价格获取器实例（可选）
        """
        self.account = account
        self.storage = storage or FeishuStorage(feishu_client)
        self.portfolio = portfolio or PortfolioManager(self.storage)
        self.price_fetcher = price_fetcher or PriceFetcher(storage=self.storage)
        self._audit_service = AuditService(
            storage=self.storage,
            portfolio=self.portfolio,
            account=account,
            report_dir=SKILL_DIR / 'audit',
            api=self,
        )

    def deposit(self, amount: float, date_str: str = None,
                remark: str = "入金", currency: str = "CNY") -> Dict[str, Any]:
        """记录入金"""
        return {"success": False, "error": "cash_flow_entry_disabled"}

    def withdraw(self, amount: float, date_str: str = None,
                 remark: str = "出金", currency: str = "CNY") -> Dict[str, Any]:
        """记录出金"""
        return {"success": False, "error": "cash_flow_entry_disabled"}

    # ---------- 持仓查询 ----------

    def get_holdings(self, include_cash: bool = True, group_by_market: bool = False,
                     include_price: bool = False, timeout: int = 10) -> Dict[str, Any]:
        """获取持仓列表

        Args:
            include_cash: 是否包含现金资产
            group_by_market: 是否按券商分组
            include_price: 是否包含实时价格
            timeout: 价格获取超时时间（秒）
        """
        try:
            return self._read_service().get_holdings(
                include_cash=include_cash,
                group_by_market=group_by_market,
                include_price=include_price,
            )
        except Exception as e:
            return {"success": False, "error": str(e)}

    def list_accounts(self, include_default: bool = True) -> Dict[str, Any]:
        """发现当前数据集中出现过的账户。

        账户来源保持只读：优先从 holdings 的存储 API 获取，再轻量读取交易、
        现金流和净值表中的 account 字段。任一来源失败时返回 warning，不阻断
        其他来源和默认账户。
        """
        return AccountService(
            storage=self.storage,
            default_account=self.account,
        ).list_accounts(include_default=include_default)

    def list_nav_accounts(self, include_default: bool = False) -> Dict[str, Any]:
        """列出应参与每日净值任务的当前持仓账户。"""
        return AccountService(
            storage=self.storage,
            default_account=self.account,
        ).list_nav_accounts(include_default=include_default)

    def audit_nav_history_duplicates(self, account: Optional[str] = None) -> Dict[str, Any]:
        """审计 nav_history 是否存在同账户同日期重复记录。"""
        audit = getattr(self.storage, "audit_nav_history_duplicates", None)
        if not callable(audit):
            return {"success": False, "error": "storage does not support nav_history duplicate audit"}
        return audit(account=account or self.account)

    def _read_service(self) -> PortfolioReadService:
        return PortfolioReadService(
            account=self.account,
            storage=self.storage,
            portfolio=self.portfolio,
            reporting_service=self.portfolio.reporting_service,
        )

    def get_position(self, holdings_data: Dict[str, Any] = None) -> Dict[str, Any]:
        """获取仓位分析

        Args:
            holdings_data: 已获取的持仓数据，如果提供则直接使用，避免重复查询
        """
        try:
            return self._read_service().get_position(holdings_data=holdings_data)
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_distribution(self, holdings_data: Dict[str, Any] = None) -> Dict[str, Any]:
        """获取资产分布

        Args:
            holdings_data: 已获取的持仓数据，如果提供则直接使用，避免重复查询
        """
        try:
            return self._read_service().get_distribution(holdings_data=holdings_data)
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ---------- 净值和收益 ----------

    def get_nav(self, days: int = 30) -> Dict[str, Any]:
        """获取账户净值

        Args:
            days: 最近 N 天（默认 30）。
        """
        try:
            # 一次 API 调用获取最近 N 天，从中取 latest
            navs = self.storage.get_nav_history(self.account, days=days)
            if not navs:
                return {"success": False, "message": "无净值记录"}

            latest = navs[-1]  # navs 已按日期升序排列

            # 构建 latest 响应，核心指标已为顶层字段
            latest_data = {
                "date": latest.date.isoformat(),
                "nav": latest.nav,
                "shares": latest.shares,
                "total_value": latest.total_value,
                "stock_value": latest.stock_value,
                "cash_value": latest.cash_value,
                "stock_weight": latest.stock_weight,
                "cash_weight": latest.cash_weight,
                "cash_flow": latest.cash_flow,
                "share_change": latest.share_change,
                "mtd_nav_change": latest.mtd_nav_change,
                "ytd_nav_change": latest.ytd_nav_change,
                "mtd_pnl": latest.mtd_pnl,
                "ytd_pnl": latest.ytd_pnl,
            }

            # 添加 details 中的扩展数据（各年份明细、累计等）
            if latest.details:
                latest_data["details"] = latest.details
                # 动态展开各年份数据 (nav_change_YYYY, appreciation_YYYY, ...)
                for k, v in latest.details.items():
                    if k.startswith(("nav_change_", "appreciation_", "cash_flow_")) and k not in latest_data:
                        latest_data[k] = v
                # 展开累计数据
                for key in ("cumulative_appreciation", "cumulative_nav_change",
                            "year_cash_flow", "initial_value"):
                    if key in latest.details:
                        latest_data[key] = latest.details[key]

            # 构建 history，包含关键指标
            history = []
            for n in navs:
                item = {
                    "date": n.date.isoformat(),
                    "nav": n.nav,
                    "share_change": n.share_change,
                }
                history.append(item)

            return {
                "success": True,
                "latest": latest_data,
                "history": history
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_return(self, period_type: str, period: str = None) -> Dict[str, Any]:
        """
        获取收益率

        Args:
            period_type: "month", "year", "since_inception"
            period: 月份(2025-03) 或 年份(2025)
        """
        try:
            if period_type == "month":
                return self._calc_month_return(period)
            elif period_type == "year":
                return self._calc_year_return(period)
            elif period_type in ("since_inception", "since2024"):
                return self._calc_since_inception_return()
            else:
                return {"success": False, "error": f"不支持的周期类型: {period_type}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _calc_month_return(self, month: str, _navs: list = None) -> Dict:
        """计算月收益率（环比：较上月末的变化）"""
        navs = _navs if _navs is not None else self.storage.get_nav_history(self.account, days=365)
        return calc_month_return(month, navs=navs)

    def _calc_year_return(self, year: str, _navs: list = None) -> Dict:
        """计算年收益率（环比：较上年末的变化）"""
        navs = _navs if _navs is not None else self.storage.get_nav_history(self.account, days=730)
        return calc_year_return(year, navs=navs)

    def _calc_since_inception_return(self, _navs: list = None) -> Dict:
        """计算自 start_year 以来收益（以上年末净值为基准，标准化为1）"""
        navs = _navs if _navs is not None else self.storage.get_nav_history(self.account, days=9999)
        return calc_since_inception_return(navs=navs, start_year=config.get_start_year())

    # ---------- 现金管理 ----------

    def get_cash(self) -> Dict[str, Any]:
        """获取现金资产明细"""
        try:
            holdings = self.storage.get_holdings(account=self.account)
            cash_holdings = [h for h in holdings if h.asset_type in [AssetType.CASH, AssetType.MMF]]

            items = []
            by_currency = {}
            for h in cash_holdings:
                currency = h.currency or 'CNY'
                items.append({
                    "code": h.asset_id,
                    "name": h.asset_name,
                    "amount": h.quantity,
                    "currency": currency,
                    "type": h.asset_type.value
                })
                by_currency[currency] = by_currency.get(currency, 0) + h.quantity

            return {
                "success": True,
                "by_currency": by_currency,
                "items": items,
                "count": len(items)
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def add_cash(self, amount: float, asset: str = "CNY-CASH") -> Dict[str, Any]:
        """Increase an existing cash holding under the account write lock."""
        return {"success": False, "error": "cash_flow_entry_disabled"}

    def sub_cash(self, amount: float, asset: str = "CNY-CASH") -> Dict[str, Any]:
        """Decrease an existing cash holding under the account write lock."""
        return {"success": False, "error": "cash_flow_entry_disabled"}

    def sync_futu_cash_mmf(
        self,
        broker: str = "富途",
        dry_run: bool = True,
        cash_balance: float = None,
        mmf_balance: float = None,
    ) -> Dict[str, Any]:
        """通过富途 OpenAPI 同步现金/货基余额到 holdings。

        默认预览不写入；测试或人工校准可传入 cash_balance/mmf_balance 跳过 API。
        """
        try:
            service = FutuBalanceSyncService(self.storage)
            return service.sync_cash_and_mmf(
                account=self.account,
                broker=broker,
                dry_run=dry_run,
                cash_balance=cash_balance,
                mmf_balance=mmf_balance,
            )
        except Exception as e:
            return {"success": False, "error": str(e)}

    def sync_futu_holdings(
        self,
        dry_run: bool = True,
        confirm: bool = False,
        allow_empty_stock_snapshot: bool = False,
    ) -> Dict[str, Any]:
        """同步 Futu 现金/MMF、股票/ETF 数量及平均成本。"""
        try:
            return PortfolioService(
                storage=self.storage,
                default_account=self.account,
            ).sync_futu_holdings(
                dry_run=dry_run,
                confirm=confirm,
                allow_empty_stock_snapshot=allow_empty_stock_snapshot,
            )
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ---------- 完整报告 ----------

    def generate_report(self, report_type: str = "daily",
                        price_timeout: int = 30,
                        snapshot: Optional[Dict[str, Any]] = None,
                        navs: Optional[list] = None,
                        nav_override: Optional[Any] = None) -> Dict[str, Any]:
        """生成日报/月报/年报

        Args:
            report_type: "daily" | "monthly" | "yearly"
            price_timeout: 价格获取超时时间（秒）
        """
        if snapshot is None and navs is None and nav_override is None:
            return self._service().generate_report(
                account=self.account,
                report_type=report_type,
                price_timeout=price_timeout,
            )

        return ReportGenerationService(
            build_snapshot_func=self.build_snapshot,
            full_report_func=self.full_report,
        ).generate_report(
            report_type=report_type,
            price_timeout=price_timeout,
            snapshot=snapshot,
            navs=navs,
            nav_override=nav_override,
        )

    def daily_report_bundle(
        self,
        *,
        nav_date: Optional[Any] = None,
        price_timeout: int = 30,
        dry_run: bool = True,
        confirm: bool = False,
        overwrite_existing: bool = False,
        use_bulk_persist: bool = False,
        sync_futu_cash_mmf: bool = False,
        sync_futu_dry_run: Optional[bool] = None,
        run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """单账户日报包薄入口：业务流程由 DailyAccountNavService 执行。"""
        from src.app import DailyAccountNavService

        return DailyAccountNavService(
            account=self.account,
            storage=self.storage,
            portfolio=self.portfolio,
            read_service=self._read_service(),
        ).run(
            nav_date=nav_date,
            price_timeout=price_timeout,
            dry_run=dry_run,
            confirm=confirm,
            overwrite_existing=overwrite_existing,
            use_bulk_persist=use_bulk_persist,
            sync_futu_cash_mmf=sync_futu_cash_mmf,
            sync_futu_dry_run=sync_futu_dry_run,
            run_id=run_id,
        )

    def daily_nav_job(
        self,
        *,
        nav_date: Optional[Any] = None,
        run_date: Optional[Any] = None,
        accounts: Any = None,
        account: Optional[str] = None,
        price_timeout: int = 30,
        dry_run: bool = True,
        confirm: bool = False,
        overwrite_existing: bool = False,
        use_bulk_persist: bool = False,
        sync_futu_cash_mmf: bool = False,
        sync_futu_dry_run: Optional[bool] = None,
        force_non_business_day: bool = False,
        run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """每日净值任务薄入口：单/多账户统一由 DailyNavJobService 执行。"""
        from src.app import DailyNavJobService

        return DailyNavJobService(
            storage=self.storage,
            portfolio=self.portfolio,
            default_account=self.account,
        ).run(
            nav_date=nav_date,
            run_date=run_date,
            accounts=accounts,
            account=account,
            price_timeout=price_timeout,
            dry_run=dry_run,
            confirm=confirm,
            overwrite_existing=overwrite_existing,
            use_bulk_persist=use_bulk_persist,
            sync_futu_cash_mmf=sync_futu_cash_mmf,
            sync_futu_dry_run=sync_futu_dry_run,
            force_non_business_day=force_non_business_day,
            run_id=run_id,
        )

    def full_report(self, price_timeout: int = 30, snapshot: Optional[Dict[str, Any]] = None, navs: Optional[list] = None) -> Dict[str, Any]:
        """生成完整报告（只读，不记录净值）

        利用实时持仓价格合成"今日"虚拟净值，确保收益统计始终可用，
        即使当天尚未调用 record_nav()。

        Args:
            price_timeout: 价格获取超时时间（秒），默认30秒
        """
        if snapshot is None and navs is None:
            return self._service().full_report(account=self.account, price_timeout=price_timeout)

        return ReportQueryService(
            account=self.account,
            storage=self.storage,
            portfolio=self.portfolio,
            read_service=self._read_service(),
        ).full_report(price_timeout=price_timeout, snapshot=snapshot, navs=navs)

    def close_nav(self, date_str: str = None,
                  total_value: float = None,
                  cash_value: float = None,
                  stock_value: float = 0.0,
                  overwrite_existing: bool = False,
                  dry_run: bool = True,
                  confirm: bool = False) -> Dict[str, Any]:
        """显式记录“清仓/关闭”状态的净值点（shares=0）。

        为什么要单独做一个入口：
        - shares=0 是合法业务语义，但必须显式触发，不能靠缺失字段/默认 0 混入。
        - 该入口不会去拉价格/估值；你提供 total_value（以及可选 cash/stock 拆分），我们按 CLOSED 规则写入。

        约定：
        - shares 固定写 0
        - nav 固定写 1.0
        - details 写入 CLOSED 状态和非最终日结的 finality provenance
        - 允许 total_value > 0（残余现金等），但建议同时提供 cash_value/stock_value 以保持拆分自洽。

        安全约束：默认 dry_run=True；真正写入必须 confirm=True 且 dry_run=False。
        """
        return AccountNavRecorderService(
            account=self.account,
            storage=self.storage,
            portfolio=self.portfolio,
            read_service=None,
        ).record_closed(
            nav_date=parse_date(date_str),
            total_value=total_value,
            cash_value=cash_value,
            stock_value=stock_value,
            overwrite_existing=overwrite_existing,
            dry_run=dry_run,
            confirm=confirm,
        )

    def record_nav(self, price_timeout: int = 30, snapshot: Optional[Dict[str, Any]] = None,
                   overwrite_existing: bool = False, dry_run: bool = True,
                   confirm: bool = False, use_bulk_persist: bool = False,
                   run_id: Optional[str] = None, nav_date: Optional[Any] = None) -> Dict[str, Any]:
        """记录今日净值（兼容入口，委托 AccountNavRecorderService）

        ⚠️ 安全约束：默认 dry_run=True，避免被日报/调试调用误写入历史。
        只有在 confirm=True 且 dry_run=False 时才会真正写入。

        Args:
            price_timeout: 价格获取超时时间（秒）
            snapshot: 可复用的统一估值快照
            overwrite_existing: 是否允许覆盖同日已有净值记录
            dry_run: 仅演练，不实际写入（默认 True）
            confirm: 明确确认写入（默认 False）
        """
        class _SnapshotReadService:
            def build_snapshot(_self, **kwargs):
                return self.build_snapshot(**kwargs)

        result = AccountNavRecorderService(
            account=self.account,
            storage=getattr(self, "storage", None),
            portfolio=self.portfolio,
            read_service=_SnapshotReadService(),
        ).record(
            nav_date=nav_date,
            price_timeout=price_timeout,
            snapshot=snapshot,
            dry_run=dry_run,
            confirm=confirm,
            overwrite_existing=overwrite_existing,
            use_bulk_persist=use_bulk_persist,
            run_id=run_id,
        )
        nav_result = result.get("nav_result")
        if isinstance(nav_result, dict):
            return nav_result
        return result

    def init_nav_history(
        self,
        date_str: str = None,
        price_timeout: int = 30,
        dry_run: bool = True,
        confirm: bool = False,
        use_bulk_persist: bool = False,
    ) -> Dict[str, Any]:
        """为新账户初始化第一条 nav_history 的兼容入口。"""
        return self._service().init_nav_history(
            account=self.account,
            date_str=date_str,
            price_timeout=price_timeout,
            dry_run=dry_run,
            confirm=confirm,
            use_bulk_persist=use_bulk_persist,
        )

    def _service(self) -> PortfolioService:
        return PortfolioService(
            storage=self.storage,
            portfolio=self.portfolio,
            price_fetcher=self.price_fetcher,
            default_account=self.account,
        )

    # ---------- 价格查询 ----------

    def get_price(self, code: str) -> Dict[str, Any]:
        """查询资产价格"""
        try:
            asset_type, currency, _ = detect_asset_type(code)
            result = self.price_fetcher.fetch(code)

            if result and 'price' in result:
                return {
                    "success": True,
                    "code": code.upper(),
                    "name": result.get('name', 'N/A'),
                    "price": result['price'],
                    "currency": result.get('currency', currency),
                    "cny_price": result.get('cny_price'),
                    "change_pct": result.get('change_pct'),
                    "source": result.get('source', 'N/A')
                }
            else:
                return {"success": False, "error": f"无法获取 {code} 的价格"}
        except Exception as e:
            return {"success": False, "error": str(e)}


# ========== 数据库初始化 ==========

def init_db(
    account: str = DEFAULT_ACCOUNT,
    initial_cash: float = 0,
    broker: Optional[str] = None,
) -> Dict[str, Any]:
    """
    初始化投资组合数据库（飞书多维表）

    Args:
        account: 账户标识，默认 "lx"
        initial_cash: 初始现金金额（可选），默认 0
        broker: 初始现金所属券商/平台；initial_cash > 0 时必填

    Returns:
        {"success": bool, "message": str}

    Example:
        # 初始化空数据库
        init_db()

        # 初始化并设置初始现金 10万元
        init_db(initial_cash=100000, broker="手工")
    """
    try:
        storage = FeishuStorage()

        # 检查飞书配置
        if not storage.client.app_token:
            raise ValueError("未配置 FEISHU_APP_TOKEN，无法连接飞书多维表")

        # 创建初始现金持仓（如果需要）
        if initial_cash > 0:
            resolved_broker = str(broker or "").strip()
            if not resolved_broker:
                raise ValueError("initial_cash > 0 requires an explicit broker")
            cash_holding = storage.get_holding_fresh(
                'CNY-CASH',
                account,
                resolved_broker,
            )
            if not cash_holding:
                holding = Holding(
                    asset_id='CNY-CASH',
                    asset_name='人民币现金',
                    asset_type=AssetType.CASH,
                    account=account,
                    broker=resolved_broker,
                    quantity=initial_cash,
                    currency='CNY',
                    asset_class=AssetClass.CASH,
                    industry=Industry.CASH
                )
                storage.replace_holding(HoldingTarget.from_holdings(
                    base=None,
                    target=holding,
                    owned_fields=(
                        set(HOLDING_REQUIRED_VALUE_FIELDS)
                        | {"asset_class", "industry"}
                    ),
                ))

        # 检查数据库状态
        holdings = storage.get_holdings(account=account)
        nav_history = storage.get_nav_history(account, days=1)

        return {
            "success": True,
            "account": account,
            "initial_cash": initial_cash,
            "current_holdings": len(holdings),
            "nav_records": len(nav_history),
            "message": f"已初始化飞书多维表\n" +
                      f"  - 持仓记录: {len(holdings)} 条\n" +
                      f"  - 净值记录: {len(nav_history)} 条\n" +
                      (f"  - 初始现金: ¥{initial_cash:,.2f}" if initial_cash > 0 else "")
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "message": f"初始化失败: {e}"
        }


# ========== 便捷函数（供 Skill 直接调用） ==========

_skill_instances: dict = {}
_skill_lock = __import__('threading').Lock()

def get_skill(account: str = None) -> PortfolioSkill:
    """获取指定账户的 Skill 实例（线程安全，按 account 缓存）。

    同一 account 共享实例；不同 account 的实例共享同一 FeishuClient 以复用连接和缓存。
    account=None 时使用 config 中的默认账户。
    """
    acct = account or DEFAULT_ACCOUNT
    if acct not in _skill_instances:
        with _skill_lock:
            if acct not in _skill_instances:
                # 首个实例正常创建；后续实例复用首个实例的 feishu_client
                if _skill_instances:
                    first = next(iter(_skill_instances.values()))
                    _skill_instances[acct] = PortfolioSkill(account=acct, feishu_client=first.storage.client)
                else:
                    _skill_instances[acct] = PortfolioSkill(account=acct)
    return _skill_instances[acct]


def deposit(amount: float, account: str = None, **kwargs) -> Dict:
    """入金"""
    return get_skill(account).deposit(amount, **kwargs)

def withdraw(amount: float, account: str = None, **kwargs) -> Dict:
    """出金"""
    return get_skill(account).withdraw(amount, **kwargs)

# 持仓查询
def get_holdings(account: str = None, **kwargs) -> Dict:
    """全部持仓"""
    return get_skill(account).get_holdings(**kwargs)

def get_position(account: str = None) -> Dict:
    """仓位分析"""
    return get_skill(account).get_position()

def get_distribution(account: str = None) -> Dict:
    """资产分布"""
    return get_skill(account).get_distribution()

def list_accounts(include_default: bool = True) -> Dict:
    """列出当前数据集中出现过的账户。"""
    return get_skill().list_accounts(include_default=include_default)

def list_nav_accounts(include_default: bool = False) -> Dict:
    """列出应参与每日净值任务的当前持仓账户。"""
    return get_skill().list_nav_accounts(include_default=include_default)

def audit_nav_history_duplicates(account: str = None) -> Dict:
    """审计 nav_history 同账户同日期重复记录。"""
    return get_skill(account).audit_nav_history_duplicates(account=account)

# 净值收益
def get_nav(days: int = 30, account: str = None) -> Dict:
    """账户净值

    Args:
        days: 获取最近 N 天历史（默认 30）。对日报发布通常只需要 2 天即可。
    """
    return get_skill(account).get_nav(days=days)

def get_return(period_type: str, period: str = None, account: str = None) -> Dict:
    """查询收益率"""
    return get_skill(account).get_return(period_type, period)

# 现金管理
def get_cash(account: str = None) -> Dict:
    """现金资产"""
    return get_skill(account).get_cash()

def add_cash(amount: float, account: str = None, **kwargs) -> Dict:
    """增加现金"""
    return get_skill(account).add_cash(amount, **kwargs)

def sub_cash(amount: float, account: str = None, **kwargs) -> Dict:
    """减少现金"""
    return get_skill(account).sub_cash(amount, **kwargs)

def sync_futu_cash_mmf(account: str = None, **kwargs) -> Dict:
    """通过富途 OpenAPI 观测 CASH，并同步货币基金到 holdings。"""
    return get_skill(account).sync_futu_cash_mmf(**kwargs)

def sync_futu_holdings(account: str = None, **kwargs) -> Dict:
    """观测 Futu CASH；同步 MMF、股票/ETF 数量及平均成本。"""
    return get_skill(account).sync_futu_holdings(**kwargs)

# 报告
def generate_report(report_type: str = "daily", price_timeout: int = 30,
                    navs=None, nav_override: Optional[Any] = None, account: str = None) -> Dict:
    """生成日报/月报/年报"""
    return get_skill(account).generate_report(
        report_type=report_type,
        price_timeout=price_timeout,
        navs=navs,
        nav_override=nav_override,
    )

def full_report(price_timeout: int = 30, account: str = None) -> Dict:
    """完整报告（只读，不记录净值）

    Args:
        price_timeout: 价格获取超时时间（秒），默认30秒
    """
    return get_skill(account).full_report(price_timeout=price_timeout)


def multi_account_overview(accounts: Any = None, price_timeout: int = 30,
                           include_details: bool = False) -> Dict:
    """生成多个账户的只读资产概览。

    Args:
        accounts: 账户列表，或逗号分隔字符串；为空时自动发现账户。
        price_timeout: 传给单账户 full_report 的价格超时时间。
        include_details: 是否在每个账户条目中附带完整 full_report。
    """
    try:
        target_accounts = normalize_accounts(accounts)
        storage = None
        default_account = DEFAULT_ACCOUNT
        if target_accounts is None:
            base_skill = get_skill()
            storage = base_skill.storage
            default_account = base_skill.account

        return AccountService(
            storage=storage,
            default_account=default_account,
            full_report_func=lambda account, price_timeout=30: get_skill(account).full_report(price_timeout=price_timeout),
        ).multi_account_overview(
            accounts=target_accounts,
            price_timeout=price_timeout,
            include_details=include_details,
        )
    except Exception as e:
        return {"success": False, "error": str(e)}

def record_nav(price_timeout: int = 30, dry_run: bool = True, confirm: bool = False,
               overwrite_existing: bool = False, use_bulk_persist: bool = False,
               account: str = None, run_id: Optional[str] = None,
               nav_date: Optional[Any] = None) -> Dict:
    """记录今日净值

    ⚠️ 默认 dry_run=True，避免误写入。
    真正写入必须传：dry_run=False 且 confirm=True。
    """
    return get_skill(account).record_nav(
        price_timeout=price_timeout,
        dry_run=dry_run,
        confirm=confirm,
        overwrite_existing=overwrite_existing,
        use_bulk_persist=use_bulk_persist,
        run_id=run_id,
        nav_date=nav_date,
    )


def daily_report_bundle(
    account: str = None,
    nav_date: Optional[Any] = None,
    price_timeout: int = 30,
    dry_run: bool = True,
    confirm: bool = False,
    overwrite_existing: bool = False,
    use_bulk_persist: bool = False,
    sync_futu_cash_mmf: bool = False,
    sync_futu_dry_run: Optional[bool] = None,
    run_id: Optional[str] = None,
) -> Dict:
    """生成单账户每日净值/分布/日报包。"""
    return get_skill(account).daily_report_bundle(
        nav_date=nav_date,
        price_timeout=price_timeout,
        dry_run=dry_run,
        confirm=confirm,
        overwrite_existing=overwrite_existing,
        use_bulk_persist=use_bulk_persist,
        sync_futu_cash_mmf=sync_futu_cash_mmf,
        sync_futu_dry_run=sync_futu_dry_run,
        run_id=run_id,
    )


def daily_nav_job(
    account: str = None,
    accounts: Any = None,
    nav_date: Optional[Any] = None,
    run_date: Optional[Any] = None,
    price_timeout: int = 30,
    dry_run: bool = True,
    confirm: bool = False,
    overwrite_existing: bool = False,
    use_bulk_persist: bool = False,
    sync_futu_cash_mmf: bool = False,
    sync_futu_dry_run: Optional[bool] = None,
    force_non_business_day: bool = False,
    run_id: Optional[str] = None,
) -> Dict:
    """运行每日净值任务；单/多账户统一入口。"""
    return get_skill(account).daily_nav_job(
        account=account,
        accounts=accounts,
        nav_date=nav_date,
        run_date=run_date,
        price_timeout=price_timeout,
        dry_run=dry_run,
        confirm=confirm,
        overwrite_existing=overwrite_existing,
        use_bulk_persist=use_bulk_persist,
        sync_futu_cash_mmf=sync_futu_cash_mmf,
        sync_futu_dry_run=sync_futu_dry_run,
        force_non_business_day=force_non_business_day,
        run_id=run_id,
    )


def init_nav_history(date_str: str = None, price_timeout: int = 30, dry_run: bool = True,
                     confirm: bool = False, use_bulk_persist: bool = False,
                     account: str = None) -> Dict:
    """为新账户初始化第一条 nav_history。

    ⚠️ 默认 dry_run=True，且只允许空 nav_history 账户初始化。
    真正写入必须传：dry_run=False 且 confirm=True。
    """
    return get_skill(account).init_nav_history(
        date_str=date_str,
        price_timeout=price_timeout,
        dry_run=dry_run,
        confirm=confirm,
        use_bulk_persist=use_bulk_persist,
    )


def close_nav(date_str: str = None,
              total_value: float = None,
              cash_value: float = None,
              stock_value: float = 0.0,
              overwrite_existing: bool = False,
              dry_run: bool = True,
              confirm: bool = False,
              account: str = None) -> Dict:
    """显式记录“清仓/关闭”净值点（shares=0, nav=1.0）。

    允许 total_value > 0（残余现金等）。

    ⚠️ 默认 dry_run=True；真正写入必须 dry_run=False 且 confirm=True。
    """
    return get_skill(account).close_nav(
        date_str=date_str,
        total_value=total_value,
        cash_value=cash_value,
        stock_value=stock_value,
        overwrite_existing=overwrite_existing,
        dry_run=dry_run,
        confirm=confirm,
    )

# 价格
def get_price(code: str, account: str = None) -> Dict:
    """查询价格"""
    return get_skill(account).get_price(code)
