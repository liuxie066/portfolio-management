# Linux Deployment

目标：在 Linux 实例内安装 `portfolio-management`，并每天通过 systemd timer 自动执行多账户日净值记录。

## 目录约定

```text
/opt/portfolio-management/current
/etc/portfolio-management/config.yaml
/etc/portfolio-management/portfolio-management.env
/var/lib/portfolio-management/.data
/var/lib/portfolio-management/reports
```

`config.yaml` 是唯一主配置文件。环境变量只用于覆盖主配置或承载 systemd 路径。

## 安装

推荐入口：

```bash
# 在目标 checkout 内运行；脚本会使用当前 checkout 作为 app 目录
sudo scripts/install.sh --apply
```

这个 bootstrap installer 会：

- 清理继承的 `PYTHONPATH` / `PYTHONHOME`，避免装错 checkout。
- 安装或更新代码目录。
- 创建 `.venv` 并安装 `requirements.txt`。
- 生成稳定启动命令 `/usr/local/bin/pm`。
- 调用 `scripts/install_linux.py` 写入 config/env/systemd 文件。

如果希望安装脚本自己从 GitHub 拉取指定版本：

```bash
sudo bash -c 'curl -fsSL https://raw.githubusercontent.com/liuxie066/portfolio-management/main/scripts/install.sh | bash -s -- --apply --ref main'
```

如果网络环境对 PyPI 慢或不稳定，可以指定镜像：

```bash
sudo scripts/install.sh --apply --pip-index-url https://mirrors.aliyun.com/pypi/simple/
```

底层 Python 安装器仍然可直接使用：

```bash
cd /opt/portfolio-management/current
python3 -m venv .venv
./.venv/bin/pip install -U pip
./.venv/bin/pip install -r requirements.txt

# 先审计计划，不写系统文件
python3 scripts/install_linux.py --json

# 写入 config/env/systemd unit；不会覆盖已有 config.yaml
sudo python3 scripts/install_linux.py --apply
```

安装脚本会生成：

- `/etc/portfolio-management/config.yaml`
- `/etc/portfolio-management/portfolio-management.env`
- `/usr/local/bin/pm`
- `/etc/systemd/system/portfolio-nav-daily.service`
- `/etc/systemd/system/portfolio-nav-daily.timer`
- `/etc/systemd/system/portfolio-futu-evening.service`
- `/etc/systemd/system/portfolio-futu-evening.timer`

如果已有 `config.yaml`，默认保留不覆盖；确需重建模板时显式加 `--overwrite-config`。

## 配置

编辑：

```bash
sudoedit /etc/portfolio-management/config.yaml
sudo chmod 600 /etc/portfolio-management/config.yaml
```

定时日净值任务至少需要：

- `feishu.app_id`
- `feishu.app_secret`
- `feishu.tables.holdings`
- `feishu.tables.nav_history`
- `feishu.tables.cash_flow`
- `feishu.tables.holdings_snapshot`

若表配置只写 `tbl...`，还需要 `feishu.app_token`；也可以直接写成 `app_token/table_id`。

## 预检

```bash
pm config inspect --json
pm config doctor --json
pm nav duplicates --json
pm daily-job --json --no-service
```

如果需要完整 Futu holdings 同步，再检查：

```bash
pm config doctor --require-futu --json
```

## 启用定时任务

安装器生成两组北京时间 timer：

- `portfolio-nav-daily.timer`：周一至周六 `08:10`，先同步 lx/sy holdings，再记录 lx/hb/sy NAV。
- `portfolio-futu-evening.timer`：周一至周五 `17:10`，只同步 lx/sy holdings。

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now portfolio-nav-daily.timer portfolio-futu-evening.timer
systemctl list-timers portfolio-nav-daily.timer portfolio-futu-evening.timer
```

周六早间同步用于捕获周五晚间美股成交，然后记录周五 NAV。周一早间通常会因周五 NAV 已存在而幂等跳过，也能在周六任务失败时提供一次补偿机会。两个 timer 都使用 `Persistent=true`。

手动触发属于真实 holdings/NAV 写入操作。确认后可分别执行：

```bash
sudo systemctl start portfolio-nav-daily.service
sudo systemctl start portfolio-futu-evening.service
sudo journalctl -u portfolio-nav-daily.service -u portfolio-futu-evening.service -n 200 --no-pager
```

定时任务由版本化的 `scripts/portfolio_scheduled_job.sh` 编排。早间模式依次执行 lx、sy 完整 Futu 同步，再单次执行：

```bash
pm daily-job --accounts lx,hb,sy --write --confirm --json --no-service
```

晚间模式只执行两个 `pm futu sync`。两个账户都会被尝试；任一同步失败时，早间模式会阻断 NAV，避免使用过期 holdings 估值。sy 的连接参数只在 subshell 中从 `/etc/portfolio-management/futu-sy.env` 加载。`daily-job` 中的现金/MMF内嵌参数只保留旧调用兼容，不用于生产完整同步。

完整 Futu 同步还需要配置飞书“刘看山”回执：

```yaml
feishu:
  receipt:
    app_id: "cli_..."
    app_secret: "..."
    open_id: "ou_..."
```

也可使用 `FEISHU_RECEIPT_APP_ID`、`FEISHU_RECEIPT_APP_SECRET` 和
`FEISHU_RECEIPT_OPEN_ID`。未设置时会兼容读取 `options-monitor` 已有的
`OM_FEISHU_BOT_APP_ID`、`OM_FEISHU_BOT_APP_SECRET` 和
`OM_FEISHU_BOT_USER_OPEN_ID`。执行 `scripts/install.sh --apply` 时，安装器会从
`/etc/options-monitor/options-monitor.env` 只提取这三项并写入
`/etc/portfolio-management/portfolio-management.env`；不会复制或加载整份
`options-monitor.env`。源文件存在但三项缺失或为空时，部署会在写文件前失败。

`pm config doctor --require-futu --json` 会检查三项最终解析结果。Futu 真实写入成功或失败都会分别发送回执；多账户 NAV 任务会再发送一条汇总回执。dry-run 不发送。飞书应用需要具备发送消息权限，并能向该 `open_id` 发起单聊。

核心保护：

- 排除周六、周日和 `calendar.holidays` 中配置的 NAV 日期。
- 未显式传 `--nav-date` 时，默认记录运行日前最近业务日。
- 写入前阻断 `nav_history` 同账户同日期重复。
- 写入前阻断待补齐的 `cash_flow` 人工录入行。
- 默认不覆盖已有同日 NAV。

## 隔离实例边界

如果部署到受限 SSH 隔离实例，先进入实例并阅读实例内 `~/AGENTS.md`。只在当前实例内操作；不要请求或使用宿主机 Docker/Incus socket、宿主机账号或宿主机 root 权限。长期运行任务使用 systemd timer，不依赖临时 shell。
