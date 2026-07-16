---
name: portfolio-management
description: |
  投资组合管理兼容 Skill。用于通过既有 Python API/MCP adapter 查询或触发投资组合操作。
---

# Portfolio Management Skill Adapter

## 边界

这个文件描述的是兼容 Skill/Python API 的使用方式。项目主产品入口是：

- CLI：`./pm`
- 本地服务：`scripts/service.py` / `src/service/http.py`
- 服务应用边界：`src/service/application.py`
- 正式日报发布：`scripts/publish_daily_report.py`

`skill_api.py` 和 `PortfolioSkill` 只作为兼容 adapter。不要把新业务逻辑加到 `skill_api.py`。

## 数据原则

所有投资数据、价格、净值、收益、现金流结论必须来自脚本执行结果或持久化数据。缺字段、缺基准、口径不一致时，直接说明“当前无法可靠计算”，不要估算。

`scripts/generate_daily_report_html.py` 只是 renderer。它不能自行拉取 snapshot、report 或价格。

## 常用兼容 API

```python
from skill_api import (
    get_price,
    get_holdings,
    get_cash,
    get_nav,
    record_nav,
    daily_nav_job,
    full_report,
    sync_futu_cash_mmf,
    sync_futu_holdings,
)
```

- `get_price(code, account=None)`：查询价格或汇率。
- `get_holdings(account=None, include_price=False)`：查询持仓。
- `get_cash(account=None)`：查询现金/MMF 持仓。
- `get_nav(account=None, days=30)`：查询最近 NAV。
- `record_nav(account=None, dry_run=True, confirm=False)`：单账户 NAV 兼容写入入口。
- `daily_nav_job(account=None, accounts=None, dry_run=True, confirm=False)`：统一日净值任务兼容入口。
- `sync_futu_cash_mmf(account=None, dry_run=True)`：旧兼容入口，仅同步 Futu 现金/MMF。
- `sync_futu_holdings(account=None, dry_run=True, confirm=False)`：同步 Futu 现金/MMF、股票/ETF 数量及平均成本。
- `full_report(account=None)`：只读完整报告。

写入类操作必须明确账户、日期、券商/平台、币种、手续费等关键字段；不确定时先 dry-run 或询问。

## 首选命令

日常操作优先用 CLI：

```bash
./pm config doctor --json
./pm nav duplicates --json
./pm daily-job --json
./pm daily-job --accounts lx,alice --write --confirm --json
./pm futu sync --account lx --write --confirm --json
./pm daily-job --account lx --write --confirm --json
```

Futu 真实写入后会由配置的飞书“刘看山”应用发送回执；多账户 `daily-job` 真实执行后也会发送一条汇总 NAV 回执。dry-run 不发送。回执配置键为 `feishu.receipt.app_id`、`feishu.receipt.app_secret`、`feishu.receipt.open_id`；也兼容 `options-monitor` 已有的 `OM_FEISHU_BOT_APP_ID`、`OM_FEISHU_BOT_APP_SECRET`、`OM_FEISHU_BOT_USER_OPEN_ID`。通知失败只反映在返回值 `receipt` 中，不改变同步或 NAV 写入本身的 `success`。完整 Futu 同步由外层调度脚本调用，不放进 `daily-job`。

正式日报：

```bash
python scripts/publish_daily_report.py --account lx
```

旧公网日报域名已经失效。发布结果只保证本地 HTML 产物，`public_url` 为
`null`，`public_url_status=disabled`。

## 架构边界

```text
src/service/         # HTTP 和 PortfolioService 应用边界
src/app/             # 账户、现金、估值、NAV、日报 payload 等编排服务
src/domain/          # NAV 公式、收益/风险、报告投影等纯计算
src/pricing/         # 行情服务、provider、缓存策略、汇率
src/feishu/          # Feishu 表级 repository 和薄 mixin
skill_api.py         # 兼容 adapter
```

新增公共服务必须加入 `src/app/__init__.py` 或 `src/domain/__init__.py`
的 `__all__`，并补 `tests/test_module_exports.py`。

## 开发规则

- 新业务先进 `src/service/application.py` 和 `src/app/*`。
- 纯计算进入 `src/domain/*`。
- 行情源进入 `src/pricing/providers/*`。
- Feishu 表级读写进入 `src/feishu/repositories/*`。
- 不要把新业务继续塞进 `skill_api.py`、`PortfolioManager` 或 `PriceFetcher`。
- 跨表写入部分成功时必须记录补偿任务或暴露可修复状态。
- Schema 变更必须更新 `docs/schema.md` 和迁移登记。

## 验证

```bash
python3 -m pytest tests -q
python3 tests/run_tests.py
git diff --check
python3 -X pycache_prefix=/tmp/pm_pycache -m compileall src skill_api.py scripts/pm.py scripts/publish_daily_report.py
```

部分旧集成测试仍会实例化真实 `PortfolioSkill()` 并依赖飞书配置；本地无真实配置时，优先跑 touched-area 单测并说明环境限制。
