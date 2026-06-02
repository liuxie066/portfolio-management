# portfolio-management 1 页 Runbook

目标：5 分钟内判断日净值任务、日报产物或核心数据为什么不对。

## 0) 入口

- 产品入口：`./pm`
- 安装后启动入口：`pm`
- 定时任务入口：`./pm daily-job`
- 本地服务：`python scripts/service.py start`
- 日报发布：`python scripts/publish_daily_report.py --account lx`
- Linux 推荐路径：`/opt/portfolio-management/current`
- 生产配置：`/etc/portfolio-management/config.yaml`

`skill_api.py` / MCP 只保留兼容 adapter，不作为新运维入口。

## 1) 日净值主流程

`daily-job` 是单账户和多账户统一工作流：

1. 解析 NAV 日期；未指定时取运行日前最近业务日。
2. 跳过周六、周日和 `calendar.holidays` 对应的 NAV 日期。
3. 解析账户列表；未指定时从当前 holdings 发现账户。
4. 审计 `nav_history` 同账户同日期重复记录。
5. 检查人工 `cash_flow` 行是否还有待补齐系统字段。
6. 可选同步 Futu 现金/MMF 到 holdings。
7. 为每个账户构建一次带价格的估值快照。
8. 写入 `nav_history`，成功后写入 `holdings_snapshot`。

常用命令：

```bash
./pm daily-job --json
./pm daily-job --accounts lx,alice --write --confirm --json
./pm daily-job --account lx --sync-futu-cash-mmf --write --confirm --json
```

Linux 安装后可直接用 `/usr/local/bin/pm`：

```bash
pm daily-job --json
pm daily-job --write --confirm --sync-futu-cash-mmf --json
```

## 2) 数据真相来源

字段名以 `docs/schema.md` 为准。

核心表：

- `holdings`
- `cash_flow`
- `nav_history`
- `holdings_snapshot`

可选表：

- `transactions`
- `compensation_tasks`
- `schema_version`

本地缓存默认在 `.data/`，生产可用 `data.dir` / `PM_DATA_DIR` 指到仓库外。

## 3) 日报和静态产物

日报发布器用于单账户 HTML 产物：

```bash
python scripts/publish_daily_report.py --account lx
python scripts/publish_daily_report.py --account lx --dry-run
```

产物：

- `reports/investment-daily-YYYY-MM-DD.html`
- `reports/latest.html`
- `<publish_root>/investment-daily-YYYY-MM-DD/index.html`

旧公网日报域名已经失效。不要把 `publish.public_url` 当成可访问链接；当前输出中 `public_url=null`、`public_url_status=disabled`。

## 4) 最小预检

```bash
./pm config inspect --json
./pm config doctor --json
./pm nav duplicates --json
python scripts/migrate_schema.py check-live
```

启用 Futu 现金/MMF 同步前再跑：

```bash
./pm config doctor --require-futu --json
```

## 5) 常见故障

### NAV 日期不对

`daily-job` 的自动日期是“运行日前最近业务日”，不是日历昨天。周一默认记录上一个周五。

systemd timer 应按自然日运行，例如每天 `08:10 Asia/Shanghai`。周六运行负责记录周五；不要把 timer 配成只跑周一到周五。

### 写入被阻断

先看：

```bash
./pm nav duplicates --json
./pm cash-flow reconcile --json
```

重复 NAV 需要先修复；人工 cash_flow 行需要先执行：

```bash
./pm cash-flow reconcile --apply --confirm --json
```

### 价格缺失

```bash
python scripts/diagnose_pricing.py --account lx --json
```

重点看 realtime/cache/stale/missing 数量和 provider error。

### 看不到日报产物

检查 `scripts/publish_daily_report.py` 输出中的 `publish.report_path`、`publish.publish_dir` 和 `publish.relative_path`。如果加了 `--no-publish`，只会生成 bundle/报告，不会写静态发布目录。

## 6) 改动后验证

```bash
python3 -m pytest tests -q
python3 tests/run_tests.py
git diff --check
python3 -X pycache_prefix=/tmp/pm_pycache -m compileall src skill_api.py scripts/pm.py scripts/publish_daily_report.py
```
