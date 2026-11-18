# PM Quality Producer 操作契约

PM 拥有正股持仓、证券账户 cash、基金账户 MMF、NAV 和 OpenD 对账事实。
Quality Hub 只读取 PM 发布的脱敏状态，不读取 PM 数据库，也不替 PM 修复业务数据。

## 本地只读检查

```bash
./.venv/bin/python scripts/pm.py quality status --json
./.venv/bin/python scripts/pm.py config doctor --require-quality --json
```

`quality status` 只读取最近一次原子发布的 artifact，不连接 OpenD。

## 权威刷新

```bash
./.venv/bin/python scripts/pm.py quality refresh --json
```

刷新会读取配置账户对应的 OpenD 证据并发布新的
`investment.quality_status.v1` artifact。它不得修改持仓、cash、MMF、NAV
或 Feishu 业务记录。

每次 producer 刷新会重新读取账户的 NAV 事实和估值证据，来源失败时输出不可用，
不把进程缓存标成最新事实。带有明确未完成持仓快照标记的 NAV 不能发布为 trusted；
需先完成已有快照恢复。重复 NAV 审计仍独立执行，`quality status` 和 HTTP reader
仍只读取已发布产物，不触发上述来源读取。

## 生产边界

- `quality.onboarded=false` 时保持未接入，不得伪装为 healthy。
- 启用 `portfolio-quality-refresh.timer`、修改 token、账户映射或生产服务均需独立授权。
- `GET /quality/status` 使用独立 bearer token，只监听 loopback。
- 缺失、过期、账号映射不唯一或 OpenD 不可用时必须输出
  `partial/unavailable/critical` 证据，不得用缓存值伪装新鲜事实。
- 正式 NAV 写入继续由 PM 本地质量门禁决定，不依赖 Hub 可用性。
