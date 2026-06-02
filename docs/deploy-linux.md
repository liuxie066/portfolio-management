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
sudo scripts/install.sh --apply --sync-futu-cash-mmf
```

这个 bootstrap installer 会：

- 清理继承的 `PYTHONPATH` / `PYTHONHOME`，避免装错 checkout。
- 安装或更新代码目录。
- 创建 `.venv` 并安装 `requirements.txt`。
- 生成稳定启动命令 `/usr/local/bin/pm`。
- 调用 `scripts/install_linux.py` 写入 config/env/systemd 文件。

如果希望安装脚本自己从 GitHub 拉取指定版本：

```bash
sudo bash -c 'curl -fsSL https://raw.githubusercontent.com/liuxie066/portfolio-management/main/scripts/install.sh | bash -s -- --apply --ref main --sync-futu-cash-mmf'
```

如果网络环境对 PyPI 慢或不稳定，可以指定镜像：

```bash
sudo scripts/install.sh --apply --pip-index-url https://mirrors.aliyun.com/pypi/simple/ --sync-futu-cash-mmf
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
sudo python3 scripts/install_linux.py --apply --sync-futu-cash-mmf
```

安装脚本会生成：

- `/etc/portfolio-management/config.yaml`
- `/etc/portfolio-management/portfolio-management.env`
- `/usr/local/bin/pm`
- `/etc/systemd/system/portfolio-nav-daily.service`
- `/etc/systemd/system/portfolio-nav-daily.timer`

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

如果需要富途现金/MMF 同步，再检查：

```bash
pm config doctor --require-futu --json
```

## 启用定时任务

默认 timer 使用北京时间语义，每天 `08:10` 运行：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now portfolio-nav-daily.timer
systemctl list-timers portfolio-nav-daily.timer
```

这个 timer 是按自然日运行，不是按交易日运行。`daily-job` 会把运行日解析为前一个业务日：周六运行用于记录周五；周日运行通常会因周五已存在而跳过重复写入。不要把 timer 改成只跑周一到周五；如果必须减少运行日，至少覆盖周二到周六。

手动触发一次：

```bash
sudo systemctl start portfolio-nav-daily.service
sudo journalctl -u portfolio-nav-daily.service -n 200 --no-pager
```

定时任务执行：

```bash
pm daily-job --write --confirm --sync-futu-cash-mmf --json
```

核心保护：

- 排除周六、周日和 `calendar.holidays` 中配置的 NAV 日期。
- 未显式传 `--nav-date` 时，默认记录运行日前最近业务日。
- 写入前阻断 `nav_history` 同账户同日期重复。
- 写入前阻断待补齐的 `cash_flow` 人工录入行。
- 默认不覆盖已有同日 NAV。

## 隔离实例边界

如果部署到受限 SSH 隔离实例，先进入实例并阅读实例内 `~/AGENTS.md`。只在当前实例内操作；不要请求或使用宿主机 Docker/Incus socket、宿主机账号或宿主机 root 权限。长期运行任务使用 systemd timer，不依赖临时 shell。
