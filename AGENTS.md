# AGENTS.md

## 项目与代码入口

基于飞书多维表的投资组合管理工具，以 CLI + 本地 HTTP 服务完成多账户日净值、
持仓/现金流管理、仓位分析和可审计快照，生成本地日报。产品范围见
[产品约束](docs/product.md)，完整文档地图见 [文档索引](docs/INDEX.md)。

| 位置 | 职责 |
|---|---|
| `pm` → `scripts/pm.py` | CLI；优先调用本地服务，不可用时按选项直连 |
| `scripts/service.py`、`scripts/serve.py`、`src/service/` | 服务进程、HTTP/client、`PortfolioService` 应用边界 |
| `src/app/`、`src/domain/` | 用例编排与纯计算；新业务进入 service/app，纯计算进入 domain |
| `src/pricing/`、`src/feishu/repositories/` | 行情/FX 与飞书表级存储 |
| `src/app/operation_state/`、`src/app/quality/` | 本地持久工作流状态与数据质量门禁 |
| `src/maintenance/`、`scripts/nav_history_repair.py` | 修复、回填等维护路径 |
| `scripts/publish_daily_report.py`、`tests/` | 日报产物入口与回归测试/fixtures |

`skill_api.py`、`PortfolioManager`、`PriceFetcher` 保留为兼容门面，不承接新业务编排。
修改前沿实际 CLI/HTTP → service/app → domain/storage 路径检查调用者和类似实现，
同时确认受影响的测试、fixtures、配置与接口消费者，不复制已有规则或算法。

## 安装、启动与构建

以下从仓库根目录执行。Python 以 CI 的 **3.12** 为验证基线；依赖来源为
`requirements.txt`，pytest 由 CI 额外安装，Ruff 是本文件要求的本地开发检查工具。

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pytest ruff
./pm config init                         # 预览，不覆盖已有配置
./pm config init --apply --confirm       # 仅在首次创建配置时执行
./pm ls                                 # 本地配置/环境摘要，不连接外部服务
./pm config doctor --json
```

按 [README](README.md#快速开始) 填写自己的账户、飞书表和显式 Futu 映射；示例中的
账户名不是通用默认值。密钥按部署文档交付，不写入 YAML、命令参数或版本库。

```bash
python scripts/service.py start
python scripts/service.py status
curl http://127.0.0.1:8765/health
# 结束本次由该脚本启动的开发服务时：
python scripts/service.py stop
```

当前没有独立的打包构建配置或前端构建步骤；源码安装后直接运行。
语法编译使用下节的 `compileall`。日报 HTML 产物按
[README 的日报入口](README.md#日报与发布) 生成，不等同于软件发布或公网部署。

## 按任务阅读

开工先读最新用户要求、本文件、[README](README.md)、[产品约束](docs/product.md) 和
[LESSONS](LESSONS.md) 中相关条目；再按任务选择下列文档，不必遍历历史记录。

| 任务 | 继续阅读 |
|---|---|
| 业务路径、模块归属 | [架构](docs/architecture.md)、[依赖图](docs/dependency-graph.md) |
| CLI/HTTP、OM 消费接口 | [服务](docs/service.md)、[OM API 契约](docs/om-api-contract.md)、`contracts/om-api/` |
| NAV、估值、价格/FX | [估值](docs/valuation.md)、[定时 NAV 一致性](docs/nav-daily-consistency.md)、[交易日历](docs/nav-trading-calendar.md)、[行情](docs/pricing.md) |
| 表字段、现金流、持仓事件 | [Schema](docs/schema.md)、[迁移](docs/migrations.md)、[Cash Effects 设计](docs/cash-flow-holding-effects.md) 与 [操作手册](docs/cash-flow-effects-runbook.md)、[事件监听](docs/holdings-event-listener-runbook.md) |
| 运行/数据质量 | [本地质量文档](docs/quality-monitoring/README.md)；跨系统契约沿其链接读取，不在本仓复制 |
| 故障、安装、发布、升级 | [Runbook](docs/runbook.md)、目标环境的 [Linux](docs/deploy-linux.md) 或 [macOS](docs/deploy-macos-operations.md) 手册，以及下文发布约定 |

涉及已有设计时再读对应 `decisions/`、`docs/gateflow/<work-unit>/` 或
`docs/reviews/` 的相关记录。历史计划和验收是当时证据，不代表当前状态或操作授权；
与最新要求/代码冲突时明确指出，不能默默选一套口径。

## 任务执行与经验维护

1. 先确定用户可观察的验收条件、任务范围和未确认信息，再实施；会影响正确性、
   范围或生产目标且现有证据无法解答的信息先询问。普通可逆实现选择自行判断。
2. 修改前检查实际运行路径、类似实现和共享责任边界；保留无关改动，选择满足
   需求的最小完整改动，不顺手重构。仅要求“看看/分析/检查”时只读。
3. Bug 尽可能先复现，再补能检出旧问题的回归测试；无法复现时记录输入、证据
   和限制，不把推测写成根因。修共享实现时覆盖受影响调用者和接口消费者。
4. 长任务按可验证结果推进，及时报告发现或阻塞。执行与改动影响相称的验证，
   不以定向检查代替必需门禁；有效且输入/环境未变的检查证据可以复用。
5. 行为或接口改变时同步更新其归属文档；完成时报告行为变化、验证命令及结果、
   未运行检查与原因、未解决问题。失败与已有基线分别陈述，不声称失败检查通过。
6. 返工或发现可复用问题时判断是否更新 [LESSONS](LESSONS.md)；条目必须包含
   触发条件、错误原因、下次检查方式和可追溯证据。能用测试/自动检查防止的，
   优先落实到检查，再链接经验；没有可靠案例只保留格式，不虚构。
7. `AGENTS.md` 只保留稳定规则和入口，产品取舍维护在产品页，经验维护在
   `LESSONS.md`，任务过程留在既有工作单/评审记录，不追加日常流水账。

## 交付授权边界

提交、推送、PR、合并、发布及升级统一按
[Delivery](/Users/liuxie/.agents/skills/delivery/SKILL.md) 执行，合并后核验本地主分支
同步；无法安全同步时明确报告阻塞。各阶段需明确授权，不因完成开发而自动推进。

发布/升级语义遵循全局 Codex `AGENTS.md`：`commit and push`、
`merge main`、`release`、`release and upgrade`、`upgrade` 是互相独立的授权边界，前一阶段
授权不自动触发后一阶段；涉及生产写入、服务变更或远端升级时必须取得明确授权。

## 版本规范

- 版本真源：`VERSION` 文件（形如 `0.1.40`，无 `v` 前缀，末尾带换行）。
- git tag：`v0.1.40`（带 `v` 前缀）；push tag 触发 GitHub Release（`.github/workflows/release.yml`）。
- release commit 文案：`Release v0.1.40`。
- Changelog：`CHANGELOG.md`，条目形如 `## 0.1.40 - 2026-08-16`，用户可感知变化用英文 bullet。
- 当前惯例是只 bump patch（重构/修复均走 `X.Y.Z` 的 Z+1）。

## 验证命令

使用上文已激活的开发虚拟环境。业务代码改动交付前保留以下完整项目检查：

```bash
python3 -m pytest tests -q
python3 -X pycache_prefix=/tmp/pm_pycache -m compileall src skill_api.py scripts/pm.py scripts/publish_daily_report.py
ruff check src skill_api.py scripts/pm.py scripts/publish_daily_report.py
git diff --check
```

`.github/workflows/quality-contract.yml` 在 PR/main 和 release 中复用，当前仅执行
契约相关测试，并非全量 pytest。另按其配置执行契约漂移检查：

```bash
python -m scripts.export_om_openapi --check
```

本地全量测试不能因 CI 只跑子集而省略。HTTP/契约改动同时核对
`docs/om-api-contract.md` 和消费者；不要为通过检查擅自发布契约 tag。
纯开发文档改动核对事实、路径、链接、命令来源及 `git diff --check` 即可，
无需为此运行全量业务测试、启动服务或访问生产；明确报告未运行项。

## 发布（release）

```bash
# 1. bump 版本 + 写 Changelog（VERSION 写 X.Y.Z 带尾换行，CHANGELOG.md 顶部加条目）
# 2. 提交并打 tag
git add VERSION CHANGELOG.md
git commit -m "Release vX.Y.Z"
git tag vX.Y.Z
# 3. 推送，tag 触发 release workflow
git push origin main
git push origin vX.Y.Z
```

## 升级远端（upgrade）

生产环境：SSH host `liuxie-incus`（见 `~/.ssh/config`），部署目录
`/home/liuxie/apps/portfolio-management`，配置 `/etc/portfolio-management/config.yaml`，
两份 Feishu App Secret 走 systemd encrypted credentials（不落明文）。

```bash
# 1. 切到目标 tag
ssh liuxie-incus 'cd /home/liuxie/apps/portfolio-management && git fetch --tags origin && git checkout vX.Y.Z'

# 2. 重新生成 systemd/env/launcher（--apply 只写资产，不自动重启服务）
ssh liuxie-incus 'cd /home/liuxie/apps/portfolio-management && sudo scripts/install.sh --apply --dir /home/liuxie/apps/portfolio-management --ref vX.Y.Z'

# 3. 重启常驻服务（timers 是 oneshot，下次触发自动用新代码，无需重启）
ssh liuxie-incus 'sudo systemctl restart portfolio-management-api.service portfolio-holdings-event-listener.service'

# 4. 验证：preflight 两个 ExecStart 均 status=0/SUCCESS，API health ok
ssh liuxie-incus 'sudo systemctl start portfolio-feishu-preflight.service && systemctl status portfolio-feishu-preflight.service --no-pager && curl -s http://127.0.0.1:8765/health'
```

详细的安装、定时器、credential 轮换与事件监听激活见 `docs/deploy-linux.md`。
