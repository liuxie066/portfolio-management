# portfolio-management

基于飞书多维表的本地投资组合管理工具。核心目标是每天为一个或多个账户计算并记录 NAV，输出仓位分布，并保留可审计的持仓快照。

当前产品形态是 **CLI + 本地 HTTP 服务**。`skill_api.py` 和 `mcp_server.py` 只作为历史 Python/Skill/MCP 兼容 adapter，不再是主业务路径。

## 核心能力

- 记录交易、现金流、持仓和 `nav_history`
- 多账户日净值任务：自动跳过周末和配置的法定节假日
- 写入前审计 `nav_history` 同账户同日期重复记录
- 写入前阻断待补齐的人工 `cash_flow` 行
- 可选通过 Futu OpenD 同步现金/MMF 到 holdings
- 统计仓位分布，生成日报 payload 和本地 HTML 产物
- 通过 `config.yaml` 和 systemd timer 部署到 Linux 长期运行

## 入口

- 日常 CLI：`./pm`
- 本地服务：`scripts/service.py` / `src/service/http.py`
- 服务应用边界：`src/service/application.py`
- 日净值任务：`./pm daily-job`
- 日报 HTML：`scripts/publish_daily_report.py`
- 架构图：`docs/architecture.md`、`docs/dependency-graph.md`
- Linux 部署：`docs/deploy-linux.md`
- Schema：`docs/schema.md`、`docs/migrations.md`

## 快速开始

```bash
python3 -m venv .venv
./.venv/bin/pip install -U pip
./.venv/bin/pip install -r requirements.txt
cp config.example.yaml config.yaml
```

编辑 `config.yaml`，至少配置飞书应用和核心表：

```yaml
feishu:
  app_id: ""
  app_secret: ""
  app_token: ""
  tables:
    holdings: ""
    nav_history: ""
    cash_flow: ""
    holdings_snapshot: ""
```

如果表配置写成 `app_token/table_id`，可以不单独配置 `feishu.app_token`。真实密钥、运行缓存和报告产物不要提交到仓库。

检查配置：

```bash
./pm config inspect --json
./pm config doctor --json
```

## 日常命令

只读查询：

```bash
./pm accounts --json
./pm holdings --account lx --json
./pm cash --account lx --json
./pm nav --account lx --json
./pm positions distribution --account lx --json
./pm nav duplicates --json
```

日净值记录：

```bash
# dry-run，默认取运行日前最近业务日
./pm daily-job --json

# 多账户真实写入
./pm daily-job --accounts lx,alice --write --confirm --json

# 同步 Futu 现金/MMF 后再估值和写 NAV
./pm daily-job --account lx --sync-futu-cash-mmf --write --confirm --json

# 手工指定 NAV 日期
./pm daily-job --accounts lx,alice --nav-date 2026-05-22 --write --confirm --json
```

`daily-job` 是单账户和多账户的统一入口。未显式传 `--nav-date` 时，它会取运行日前最近业务日；周六任务会记录周五，周日/周一会在周五已存在时跳过同日重复写入。

定时器应按自然日运行，例如每天 `08:10 Asia/Shanghai`。不要把 systemd timer 配成只跑周一到周五；如果必须减少运行日，至少要覆盖周二到周六，否则周五的次日记录窗口会被漏掉。

写入保护：

- 默认 `dry_run=true`
- 真实写入必须显式 `--write --confirm`
- 默认不覆盖同日已有 NAV
- NAV 日期为非交易日时默认跳过，可用 `--force-non-business-day` 明确覆盖

## 本地服务

```bash
python scripts/service.py start
python scripts/service.py status
curl http://127.0.0.1:8765/health
curl 'http://127.0.0.1:8765/accounts/overview?accounts=lx,alice'
```

`./pm` 常用命令会优先访问本地服务；服务不可用时回退到 `PortfolioService` 本地直连。使用 `--no-service` 可强制直连，使用 `--require-service` 可禁止 fallback。

HTTP 服务默认只绑定 `127.0.0.1` / `localhost` / `::1`，且当前不带鉴权。非 loopback 绑定必须显式 `--allow-remote`，并放在已有鉴权的网络边界后面。

## 日报与发布

正式日报数据和 HTML 入口只有：

```bash
python scripts/publish_daily_report.py --account lx
```

它会优先调用服务端 `daily_report_bundle`，用同一个估值快照完成 NAV 写入、日报 payload 和 HTML 数据组装。`scripts/generate_daily_report_html.py` 只是 renderer，不负责拉数据。

旧的日报公网域名已经失效。当前发布只保证生成本地静态产物：

- `reports/investment-daily-YYYY-MM-DD.html`
- `reports/latest.html`
- `<publish_root>/investment-daily-YYYY-MM-DD/index.html`

输出中的 `public_url` 固定为 `null`，`public_url_status=disabled`。

## Linux 定时运行

生产部署建议把代码、配置和运行数据分开：

```text
/opt/portfolio-management/current
/etc/portfolio-management/config.yaml
/etc/portfolio-management/portfolio-management.env
/var/lib/portfolio-management/.data
/var/lib/portfolio-management/reports
```

推荐使用 bootstrap installer。它会参考 Hermes Agent 的部署模式：安装/更新
代码、创建 `.venv`、安装依赖、生成稳定 `pm` launcher，并把 systemd/config
写入交给保守的 Python 安装器。

```bash
sudo scripts/install.sh --apply --sync-futu-cash-mmf
sudo systemctl status portfolio-nav-daily.timer
```

首次运行不会自动启用 timer；确认配置后再显式启用：

```bash
sudo scripts/install.sh --apply --enable-timer --sync-futu-cash-mmf
```

完整步骤见 `docs/deploy-linux.md`。

## 配置优先级

配置统一由 `src/config.py` 读取：

1. 命令行参数
2. 环境变量
3. `config.yaml`
4. 默认值

常用环境变量：

- `PORTFOLIO_CONFIG_FILE`
- `PORTFOLIO_SERVICE_URL`
- `PM_DATA_DIR`
- `PM_REPORTS_DIR`
- `PM_BUSINESS_HOLIDAYS`
- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `FEISHU_APP_TOKEN`

## 代码边界

```text
src/
├── service/              # HTTP 和 PortfolioService 应用边界
├── app/                  # 账户、现金、估值、NAV、日报 payload 等编排服务
├── domain/               # NAV 公式、收益/风险、报告投影等纯计算
├── pricing/              # 行情服务、provider、缓存策略、汇率
├── feishu/               # Feishu 表级 repository 和薄 mixin
├── maintenance/          # nav_history repair/backfill 等维护入口
├── portfolio.py          # 兼容 facade，委托 app/domain
├── price_fetcher.py      # 兼容 facade，委托 pricing
└── feishu_storage.py     # FeishuStorage 门面
```

新业务优先进入 `src/service/application.py` 和 `src/app/*`；纯计算进入 `src/domain/*`；行情源进入 `src/pricing/providers/*`。不要把新业务继续塞进 `skill_api.py`、`PortfolioManager` 或 `PriceFetcher`。

## 验证

```bash
python3 -m pytest tests -q
python3 tests/run_tests.py
git diff --check
python3 -X pycache_prefix=/tmp/pm_pycache -m compileall src skill_api.py scripts/pm.py scripts/publish_daily_report.py
```

## 文档

- `docs/INDEX.md`：项目地图
- `docs/runbook.md`：日常运维和排障
- `docs/service.md`：HTTP/service API
- `docs/deploy-linux.md`：Linux 安装和 systemd timer
- `docs/schema.md`：飞书多维表字段
- `docs/dependency-graph.md`：依赖方向图

## License

MIT
