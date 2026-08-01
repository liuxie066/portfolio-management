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

`config.yaml` 是非秘密业务配置的主文件。环境变量只用于覆盖非秘密配置或承载
systemd 路径；两个 Feishu App Secret 只通过 systemd encrypted credentials
进入各自服务。

## 安装

推荐入口：

```bash
# 先准备 checkout/venv 并查看 system asset 计划，不写 config/env/unit
sudo scripts/install.sh
```

这个 bootstrap installer 会：

- 清理继承的 `PYTHONPATH` / `PYTHONHOME`，避免装错 checkout。
- 安装或更新代码目录。
- 创建 `.venv` 并安装 `requirements.txt`。
- 生成稳定启动命令 `/usr/local/bin/pm`。
- 调用 `scripts/install_linux.py` 写入 config/env/systemd 文件。
- 将 runtime data/reports 目录交给 `--run-user`，确保该用户可创建和恢复
  `pm_operation_state.sqlite3`；数据库文件自身保持 `0600`。

如果希望安装脚本自己从 GitHub 拉取指定版本：

```bash
sudo bash -c 'curl -fsSL https://raw.githubusercontent.com/liuxie066/portfolio-management/main/scripts/install.sh | bash -s -- --ref main'
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

# 仅在下文两份 encrypted credentials 已配置后执行；不会覆盖已有 config.yaml
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
- `/etc/systemd/system/portfolio-cash-flow-scan.service`
- `/etc/systemd/system/portfolio-cash-flow-scan.timer`
- `/etc/systemd/system/portfolio-management-api.service`
- `/etc/systemd/system/portfolio-quality-refresh.service`
- `/etc/systemd/system/portfolio-receipt-dispatch.service`
- `/etc/systemd/system/portfolio-holdings-event-listener.service`
- `/etc/systemd/system/portfolio-feishu-preflight.service`（默认禁用）

如果已有 `config.yaml`，默认保留不覆盖；确需重建模板时显式加 `--overwrite-config`。

## 配置

编辑：

```bash
sudoedit /etc/portfolio-management/config.yaml
sudo chmod 600 /etc/portfolio-management/config.yaml
```

定时日净值任务至少需要：

- `feishu.bitable.app_id`
- `feishu.tables.holdings`
- `feishu.tables.nav_history`
- `feishu.tables.cash_flow`
- `feishu.tables.holdings_snapshot`

若表配置只写 `tbl...`，还需要 `feishu.app_token`；也可以直接写成 `app_token/table_id`。

## 两个 Feishu 应用与密钥

只配置两个应用身份，不配置第三个 event-only 应用：

| 角色 | 非秘密配置 | systemd credential | 需要的能力 |
|---|---|---|---|
| Bitable | `feishu.bitable.app_id` | `pm-feishu-bitable-app-secret` | 目标 Base 的记录读取/写入；云文档事件订阅；`drive.file.bitable_record_changed_v1` 长连接；目标 Base 管理/访问权限 |
| Conversation | `feishu.conversation.app_id`、`feishu.conversation.open_id` | `pm-feishu-conversation-app-secret` | 开启机器人能力；精确授予以应用身份发送消息 `im:message:send_as_bot`；目标用户在机器人可用范围内 |

Bitable 应用同时负责 Base API 与表格变更事件。Conversation 应用只发对话/回执，
不需要 Base 权限。飞书官方接口权限依据见
[多维表格 API 概述](https://open.feishu.cn/document/server-docs/docs/bitable-v1/bitable-overview?lang=zh-CN)、
[订阅云文档事件](https://open.feishu.cn/document/server-docs/docs/drive-v1/event/subscribe?lang=zh-CN)
和[发送消息](https://open.feishu.cn/document/server-docs/im-v1/message/create?lang=zh-CN)。

先通过组织认可的安全终端流程把两份 Secret 加密到 systemd credential store。
下面的示例使用隐藏输入；Secret 不进入命令行参数、shell history、YAML 或 env：

```bash
sudo install -d -m 0700 /etc/credstore.encrypted
systemd-ask-password "Bitable App Secret" | \
  sudo systemd-creds encrypt --name=pm-feishu-bitable-app-secret - \
  /etc/credstore.encrypted/pm-feishu-bitable-app-secret
systemd-ask-password "Conversation App Secret" | \
  sudo systemd-creds encrypt --name=pm-feishu-conversation-app-secret - \
  /etc/credstore.encrypted/pm-feishu-conversation-app-secret

# 两份 encrypted credentials 就绪后才写部署资产；仍不会自动启用服务
sudo scripts/install.sh --apply
```

任何曾出现在聊天、日志或明文配置中的 Secret 都必须先在飞书后台轮换，不能把已
披露值直接迁入 credential store。安装器不会接收、创建、加密、解密、复制或打印
Secret；apply 只按名称和 regular-file 元数据检查两份文件，并在任何目标文件写入前
使用临时 unit 执行 `systemd-analyze verify`。dry-run 只报告能力要求，不声称已验证。

## 预检

生产服务必须通过生成的 oneshot 运行预检，使 systemd 注入两份 credentials：

```bash
sudo systemctl start portfolio-feishu-preflight.service
systemctl status portfolio-feishu-preflight.service --no-pager
journalctl -u portfolio-feishu-preflight.service -n 100 --no-pager
```

这个 service 只执行
`pm config doctor --require-secure-feishu --json` 和本地
`pm events status --json`；不请求飞书、不订阅、不连接 listener、不发送消息，也不写
业务数据。成功只证明配置解析、两份 credential 注入、SDK 与本地 target/inbox
状态通过，不证明远端权限、订阅或连接健康。

开发环境不使用 secure systemd unit 时，可另外执行只读检查：

```bash
pm config inspect --json
pm config doctor --json
pm nav duplicates --json
pm daily-job --json --no-service
```

如果需要完整 Futu holdings 同步，再检查：

```bash
pm config doctor --require-futu --json
pm config doctor --require-futu --require-quality --json
```

首次配置 `futu.profiles.<account>.acc_id` 前，在 OpenD 所在主机只读发现账户：

```bash
pm futu accounts --market US --json
```

该命令只读取 OpenD 账户列表，不读取余额/持仓、不发飞书、不写业务数据。输出的
`acc_id` 是敏感配置标识，不得进入日志、文档或 Git；空、不完整或重复列表会
fail closed。由操作者将已核实的 REAL 账户分别映射到 `lx`/`sy`，随后再执行
`config doctor` 和同步 dry-run。

质量 producer 还要求 `quality.accounts`、各账户显式唯一的
`futu.profiles.<account>`（包含 `host`、`port`、`acc_id`、REAL 环境和市场），
以及独立 `quality.read_token`。正式接入 Hub
时才将 `quality.onboarded` 改为 `true`；该状态一旦启用，正式 NAV 写入会直接在
PM 本地 fail closed，不依赖 Hub 在线。

## 启用同机只读 API 边界

当 `options-monitor` Copilot 与本项目运行在同一台主机时，可独立启用 HTTP API：

```bash
sudo scripts/install.sh --apply --enable-api-service
systemctl status portfolio-management-api.service
curl http://127.0.0.1:8765/health
curl -H "Authorization: Bearer $PM_QUALITY_READ_TOKEN" \
  http://127.0.0.1:8765/quality/status
```

安装器始终生成 unit，但只有显式传入 `--enable-api-service` 才会执行 `systemctl enable --now`。unit 固定运行 `scripts/serve.py --host 127.0.0.1 --port 8765`，不使用 `--allow-remote`，也不依赖两个 timer。普通业务接口仍只依赖 loopback 边界；质量接口额外要求独立只读 token。禁止把服务直接绑定或转发到非 loopback 网络。

## 启用质量 artifact 刷新

质量检查独立于 holdings/NAV 写入任务，默认每 15 分钟读取现有控制证据并原子
发布 artifact：

```bash
sudo scripts/install.sh --apply --enable-quality-timer
systemctl status portfolio-quality-refresh.timer
systemctl list-timers portfolio-quality-refresh.timer
```

`portfolio-quality-refresh.service` 执行 `pm quality refresh --json`，不会主动
触发 OpenD 同步或飞书业务写入。安装器始终生成 service/timer，但只有显式传入
`--enable-quality-timer` 才会启用；可通过
`--quality-refresh-interval=<systemd duration>` 覆盖默认的 `15min`。

## 启用定时任务

安装器生成四组北京时间 timer：

- `portfolio-nav-daily.timer`：周一至周六 `08:10`，先同步 lx/sy holdings，再记录 lx/hb/sy NAV。
- `portfolio-futu-evening.timer`：周一至周五 `17:10`，只同步 lx/sy holdings。
- `portfolio-cash-flow-scan.timer`：每 15 分钟完整读取 Feishu cash flow 和
  CASH holdings，只发现 effect、更新 SQLite 并发送回执。
- `portfolio-receipt-dispatch.timer`：每 5 分钟重试到期的 NAV 回执 outbox。

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now portfolio-nav-daily.timer portfolio-futu-evening.timer portfolio-cash-flow-scan.timer portfolio-receipt-dispatch.timer
systemctl list-timers portfolio-nav-daily.timer portfolio-futu-evening.timer portfolio-cash-flow-scan.timer portfolio-receipt-dispatch.timer
```

周六早间同步用于捕获周五晚间美股成交，然后记录周五 NAV。周一早间通常会因周五 NAV 已存在而幂等跳过，也能在周六任务失败时提供一次补偿机会。四个 timer 都使用 `Persistent=true`。

手动触发属于真实 holdings/NAV 写入操作。确认后可分别执行：

```bash
sudo systemctl start portfolio-nav-daily.service
sudo systemctl start portfolio-futu-evening.service
sudo systemctl start portfolio-cash-flow-scan.service
sudo systemctl start portfolio-receipt-dispatch.service
sudo journalctl -u portfolio-nav-daily.service -u portfolio-futu-evening.service -u portfolio-cash-flow-scan.service -u portfolio-receipt-dispatch.service -n 200 --no-pager
```

定时任务由版本化的 `scripts/portfolio_scheduled_job.sh` 编排。早间模式依次执行 lx、sy 完整 Futu 同步，再单次执行：

```bash
pm daily-job --accounts lx,hb,sy --write --confirm --json --no-service
```

晚间模式只执行两个 `pm futu sync`。两个账户都会被尝试；任一同步失败时，
早间模式会阻断 NAV，避免使用过期 holdings 估值。lx/sy 都通过
`config.yaml` 的同名 `futu.profiles` 显式路由，不再读取单独的 sy env 文件。
Futu CASH 只保留原币观测证据，不与 PM 的 `CNY-CASH` 人民币汇总金额对账，
也不生成 reconciliation effect；股票/ETF 与 MMF 保持各自同步。
Cash Flow 激活、备份和恢复见 `docs/cash-flow-effects-runbook.md`。

完整 Futu 同步还需要配置 Conversation 应用的非秘密身份：

```yaml
feishu:
  conversation:
    app_id: "cli_..."
    open_id: "ou_..."
```

`FEISHU_CONVERSATION_APP_ID` 和 `FEISHU_CONVERSATION_OPEN_ID` 可覆盖这两个
非秘密值。安装器也可从 options-monitor 兼容读取
`OM_FEISHU_BOT_APP_ID`/`OM_FEISHU_BOT_USER_OPEN_ID`，但绝不会导入
`OM_FEISHU_BOT_APP_SECRET`。`feishu.receipt.*`、`FEISHU_RECEIPT_*`、
`feishu.app_*`、`FEISHU_APP_*` 和 `OM_FEISHU_BOT_*` 的 Secret 形式都只用于
识别旧安装的迁移 shadow，不是生产稳态配置。

生产 secure mode 不会回退到任何明文 Secret。Futu 真实写入成功或失败都会分别
发送回执；多账户 NAV 任务会再发送一条汇总回执。dry-run 不发送。

### 迁移、轮换和回滚边界

按以下状态逐步推进，每一步都要独立确认，不能因前一步成功自动执行后一步：

```text
旧明文仍在
  -> 仅准备 credential-capable checkout/venv（不 apply system assets）
  -> 轮换并配置两份 encrypted credentials
  -> apply credential-capable config/env/units
  -> secure preflight 通过
  -> 按授权切换非 listener 消费者
  -> 单独完成 Base subscription
  -> 单独启用 listener
  -> controlled canary 通过
  -> 单独授权后清理明文 shadow
```

- 安装不会自动启用 timer、API 或 listener；订阅也不会启用 listener。
- `config inspect`/doctor 报告 `plaintext_shadow_detected` 时，credential 仍优先，
  canary 不会因旧值不同而失败；不要在验证前删除旧行。
- canary 通过后，使用 `sudoedit` 从目标 env/config 和 options-monitor 源中逐项移除
  shadow key。删除属于单独的破坏性操作；安装器绝不代做。清理后再次运行 secure
  preflight。
- 轮换时先用相同 `--name` 生成新的 encrypted 文件，经 preflight 验证后再按明确
  授权重启消费者。不要把明文 Secret 放进命令参数或临时文件。
- 明文清理前可回滚到上一套 credential-capable unit；清理后不得回滚到只支持明文
  的版本。此时应恢复已备份的 encrypted credential 或再次轮换，而不是重建明文。
- install、release、remote upgrade、subscription、service activation、canary 和
  plaintext cleanup 是互相独立的授权边界。

## Holdings 与 Cash Flow 变更事件入口

安装器会生成 `portfolio-holdings-event-listener.service`，但默认不启用。
为了兼容已有安装，unit 名保持不变，但其 `ExecStart` 运行
`pm events listen --confirm --json`，用一个长连接处理配置的
`holdings` 和 `cash_flow` 表。完整的飞书侧配置、精确 Base
文件订阅、table 路由、canary 和回滚步骤见
`docs/holdings-event-listener-runbook.md`。不得仅因 unit 已生成就启用服务。

核心保护：

- 排除周六、周日和 `calendar.holidays` 中配置的 NAV 日期。
- 未显式传 `--nav-date` 时，默认记录运行日前最近业务日。
- 写入前阻断 `nav_history` 同账户同日期重复。
- 写入前阻断待补齐的 `cash_flow` 人工录入行。
- 默认不覆盖已有同日 NAV。

## 隔离实例边界

如果部署到受限 SSH 隔离实例，先进入实例并阅读实例内 `~/AGENTS.md`。只在当前实例内操作；不要请求或使用宿主机 Docker/Incus socket、宿主机账号或宿主机 root 权限。长期运行任务使用 systemd timer，不依赖临时 shell。
