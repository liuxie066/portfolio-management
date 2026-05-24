# 兼容面收敛决策

本轮按“能删则删，不能删要有现役理由”清理兼容入口：

- 删除独立 `BitableClient` 并收回 `FeishuClient` 上仅供它使用的公开别名；单条读取只保留 `get_record_strict()`。
- 删除 schema 检查兼容脚本，把 live schema check 收进 `scripts/migrate_schema.py check-live`。
- 删除 `skill_api.clean_data` 和 MCP `tool_clean_data`，避免把跨表删除能力留在通用 Skill/MCP 面。
- 删除 `PortfolioManager` 现金 helper，交易/出入金现金副作用直接走 `CashService`。
- 删除 `PriceFetcher` provider/classifier/cash wrapper，测试和批量美股路径直接依赖 `src/pricing/*`。
- 删除 `storage.backend` / `PORTFOLIO_STORAGE_BACKEND` 选择面，存储工厂只创建 Feishu 后端。

保留项：

- `skill_api.py`、MCP 和 CLI 的本地恢复 fallback 仍保留，因为它们是当前服务不可用时的操作入口。
- 历史 NAV/价格字段兼容仍保留，因为它们保护已存在数据，不是无调用代码。
- `nav_history_patch.py` / `backfill_nav_history_bulk.py` 已继续收敛：实现迁入 `src/maintenance/nav_history_repair/`，命令面只保留 `scripts/nav_history_repair.py`。
