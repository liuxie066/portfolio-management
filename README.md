# portfolio-management

基于飞书多维表的投资组合净值系统：用基金净值法记账，管理持仓、交易、现金流、估值、NAV 和日报发布。

## 入口

- CLI 产品入口：`./pm`（人类日常操作）
- 人类使用：`README.md`、`docs/INDEX.md`
- Agent 使用：`SKILL.md`
- HTTP Service：`src/service/http.py`（主服务入口）
- Python API：`skill_api.py`
- MCP Server：`mcp_server.py`（供 OpenClaw、Claude Desktop 等 MCP 客户端调用）
- 架构说明：`docs/architecture.md`
- Schema 与迁移：`docs/schema.md`、`docs/migrations.md`

## Quickstart

```bash
python3 -m venv .venv
./.venv/bin/pip install -U pip
./.venv/bin/pip install -r requirements.txt
cp config.example.json config.json
```

启动 HTTP 服务：

```bash
python scripts/service.py start
python scripts/service.py status
curl http://127.0.0.1:8765/health
curl http://127.0.0.1:8765/accounts
curl 'http://127.0.0.1:8765/accounts/overview?accounts=alice,bob'
```

日常 CLI：

```bash
./pm daily                         # 计算今日净值并输出仓位分布；默认 dry-run
./pm daily --write --confirm       # 真实记录今日 nav_history
./pm nav                           # 查看最近净值
./pm nav record --write --confirm  # 只记录今日净值
./pm positions distribution        # 统计仓位分布
```

`./pm` 会优先使用仓库内 `.venv/bin/python`，否则回退到系统 `python3`。常用命令优先访问本地 HTTP 服务，服务不可用时回退到直连 `skill_api.py`；写入命令默认 dry-run，真实写入必须显式 `--write --confirm`。

HTTP 服务默认只允许绑定 `127.0.0.1`/`localhost`/`::1`。该服务当前不带鉴权；
如果必须绑定到非 loopback 地址，需要显式传 `--allow-remote`，并确保外层网络边界已鉴权。

配置 `config.json` 或环境变量：

- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `FEISHU_APP_TOKEN`
- `FEISHU_TABLE_HOLDINGS`
- `FEISHU_TABLE_TRANSACTIONS`
- `FEISHU_TABLE_NAV_HISTORY`
- `FEISHU_TABLE_CASH_FLOW`
- `FEISHU_TABLE_HOLDINGS_SNAPSHOT`
- `FEISHU_TABLE_COMPENSATION_TASKS`
- `FEISHU_TABLE_SCHEMA_VERSION`
- `PORTFOLIO_SERVICE_URL`（可选，默认 `http://127.0.0.1:8765`）
- `PM_REPORT_ACCOUNT_LABEL` / `report.account_label`（日报展示账户名）
- `FUTU_OPEND_HOST` / `futu.opend.host`、`FUTU_OPEND_PORT` / `futu.opend.port`（可选，富途 OpenD 同步）

配置统一从 `src/config.py` 读取：命令行参数优先，其次环境变量，再其次 `config.json`，最后使用默认值。真实密钥和生产路径仍建议放在仓外 `config.json` 或运行环境变量中。

## Linux 部署约定

生产主机建议只从远端仓库同步代码，真实配置和运行数据放在仓外，仓内用软链接引用：

```bash
mkdir -p /opt/portfolio-management/config
mkdir -p /var/lib/portfolio-management/.data
mkdir -p /var/lib/portfolio-management/reports

ln -s /opt/portfolio-management/config/config.json ./config.json
ln -s /var/lib/portfolio-management/.data ./.data
ln -s /var/lib/portfolio-management/reports ./reports
```

`config.json`、`.data/`、`reports/` 已被 `.gitignore` 忽略。若部署工具会清理未跟踪文件（如 `rsync --delete`、重建工作区），部署后需要重新创建这些软链接。

## 常用调用

```python
from skill_api import buy, sell, deposit, withdraw, get_holdings, full_report, record_nav, sync_futu_cash_mmf, list_accounts, multi_account_overview

get_holdings(include_price=True, group_by_market=True)
get_holdings(include_price=True, account="alice")
list_accounts()
multi_account_overview()
multi_account_overview(accounts=["alice", "bob"])
sync_futu_cash_mmf(dry_run=True)
sync_futu_cash_mmf(dry_run=True, account="alice")
record_nav()
record_nav(account="alice")
full_report()
full_report(account="alice")
buy("600519", "贵州茅台", 100, 1800, broker="平安证券")
buy("600519", "贵州茅台", 100, 1800, broker="平安证券", account="alice")
sell("600519", 50, 1900, broker="平安证券")
deposit(50000, remark="入金")
withdraw(10000, remark="出金")
```

日报数据与 HTML 统一从 `scripts/publish_daily_report.py` 生成；`scripts/generate_daily_report_html.py` 仅负责渲染已准备好的 bundle。
公网发布域名已失效；日报发布当前只保证写入本地静态产物路径，不再生成或维护对外可访问 URL。

HTTP 服务是新的主产品入口；CLI、MCP 和 `skill_api.py` 保持兼容，作为服务/应用层的适配入口逐步收敛。当前核心路径中，账户发现、多账户概览、NAV 记录/读取、现金、持仓、仓位分布、完整报告和日报/月报/年报 payload 已经由 service application 直接调用 `src/app` / `src/portfolio.py`，不再把 `skill_api.py` 作为主路径。
`scripts/pm.py` 的常用命令会优先尝试本地服务，服务不可用时自动回退到直连 `skill_api.py`；CLI 可用 `--no-service` 强制直连，或用 `--require-service` 在服务不可用时直接失败。`scripts/publish_daily_report.py` 也优先调用服务端 `daily_report_bundle`，在一次估值快照内完成 NAV 写入、日报 payload 和页面返回字段组装。

常用 CLI 也支持显式指定账户：

```bash
./pm accounts --json
./pm overview --accounts alice,bob --json
./pm cash --account alice
./pm holdings --account alice --json
./pm daily --account alice --json
./pm daily --account alice --run-id manual-20260523 --json
./pm positions distribution --account alice --json
python scripts/publish_daily_report.py --account alice
python scripts/publish_daily_report.py --account alice --run-id manual-20260523
```

## MCP Server

将全部 Skill API 暴露为 MCP tools，供 OpenClaw、Claude Desktop、Cursor 等 MCP 兼容客户端使用。

```bash
# stdio 模式（默认，适合本地 MCP 客户端）
python mcp_server.py

# SSE 模式（HTTP，适合远程调用）
python mcp_server.py --sse
```

MCP 客户端配置示例：

```json
{
  "mcpServers": {
    "portfolio-management": {
      "command": "python",
      "args": ["mcp_server.py"],
      "cwd": "/path/to/portfolio-management"
    }
  }
}
```

MCP tools 覆盖账户发现、交易、持仓、净值、现金、报告、同步等功能。写入类操作默认带 `dry_run=True` 安全保护。

## 当前结构

```text
src/
├── app/                  # 应用服务：交易、现金、富途余额同步、估值、NAV、快照、报表、补偿
├── service/              # HTTP/service 边界：FastAPI app 与服务门面
├── domain/               # 纯计算：NAV 公式、历史索引、payload 规范化
├── pricing/              # 行情插件化：PriceService + Provider
├── maintenance/          # 运维修复：nav_history repair/backfill 等维护实现
├── migrations/           # Schema 版本化迁移登记
├── portfolio.py          # PortfolioManager facade，委托 app/domain 服务
├── price_fetcher.py      # PriceFetcher facade，委托 pricing service
├── feishu_storage.py     # 飞书表读写与本地缓存索引
└── feishu_client.py      # 飞书 API 客户端
```

`src/app/__init__.py` 和 `src/domain/__init__.py` 是公共导出边界。新增服务优先放到对应包，并补充 `__all__`。

## 关键约束

- 写入前默认先 dry-run；不要提交真实 `config.json` 或密钥。
- 业务日期统一使用北京时间语义。
- NAV 写入前先写 `holdings_snapshot`，保证可审计和可复算。
- 交易/现金/持仓跨表失败要记录补偿任务，不静默吞掉。
- Schema 变更必须登记到 `src/migrations/feishu/registry.py`，并更新 `docs/schema.md`。
- `PortfolioManager` 和 `PriceFetcher` 是 facade，新逻辑不要继续塞回巨型文件；现金逻辑归 `src/app/cash_service.py`，行情分类/Provider 归 `src/pricing/`。

## NAV 写入约定

- 完整 `nav_history` 记录写入统一使用：
  - `FeishuStorage.write_nav_record()`
  - `FeishuStorage.write_nav_records()`
- 派生字段修复统一使用：
  - `FeishuStorage.patch_nav_derived_fields()`
- `update_nav_fields()` 已删除；派生字段 patch 只保留 `patch_nav_derived_fields()`。
- `save_nav()`、`upsert_nav_bulk()`、`update_nav_fields()` 已删除。

## 常用命令

```bash
# touched-area 回归
python3 -m pytest tests/test_module_exports.py tests/test_nav_record_service.py tests/test_snapshot_service.py tests/test_trade_service.py tests/test_valuation_service.py tests/test_pricing_service.py

# 编译检查，避免写 __pycache__ 到源码树
python3 -X pycache_prefix=/tmp/pm_pycache -m compileall src

# Schema 迁移计划，只打印不写飞书
python3 scripts/migrate_schema.py

# Schema live 检查 / code-side expectations
python3 scripts/migrate_schema.py check-live
python3 scripts/migrate_schema.py expectations

# 标记迁移已应用到本地状态
python3 scripts/migrate_schema.py --apply

# NAV 历史修复统一入口
python3 scripts/nav_history_repair.py backfill --account lx --from 2025-01-01 --to 2025-01-31 --dry-run
```

`check-live` 的退出状态只阻断核心净值表；`transactions`、`compensation_tasks`、`schema_version` 是可选能力表，缺失时会体现在 `all_ok=false`，但不影响 `core_ok`。

## 文档索引

- `docs/INDEX.md`：项目地图和诊断命令
- `docs/architecture.md`：架构图与优化 TODO
- `docs/architecture.mmd`：Mermaid 架构图
- `docs/schema.md`：飞书表结构
- `docs/migrations.md`：迁移说明

## License

MIT
