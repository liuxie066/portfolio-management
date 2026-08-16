# AGENTS.md

本项目的发布与部署约定。发布/升级语义遵循全局 Codex `AGENTS.md`：`commit and push`、
`merge main`、`release`、`release and upgrade`、`upgrade` 是互相独立的授权边界，前一阶段
授权不自动触发后一阶段；涉及生产写入、服务变更或远端升级时必须取得明确授权。

## 版本规范

- 版本真源：`VERSION` 文件（形如 `0.1.40`，无 `v` 前缀，末尾带换行）。
- git tag：`v0.1.40`（带 `v` 前缀）；push tag 触发 GitHub Release（`.github/workflows/release.yml`）。
- release commit 文案：`Release v0.1.40`。
- Changelog：`CHANGELOG.md`，条目形如 `## 0.1.40 - 2026-08-16`，用户可感知变化用英文 bullet。
- 当前惯例是只 bump patch（重构/修复均走 `X.Y.Z` 的 Z+1）。

## 验证命令

```bash
python3 -m pytest tests -q
python3 -X pycache_prefix=/tmp/pm_pycache -m compileall src skill_api.py scripts/pm.py scripts/publish_daily_report.py
ruff check src skill_api.py scripts/pm.py scripts/publish_daily_report.py
git diff --check
```

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
