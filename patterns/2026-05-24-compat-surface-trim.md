# 兼容面收敛模式

有效模式：

- 先用 `rg` 找到兼容/legacy/deprecated/wrapper/alias 标记，再按生产调用、测试调用、文档调用分层判断。
- 对只有测试调用的 wrapper，不保留 wrapper 迁就测试；更新测试去覆盖新权威模块。
- 对已经有权威 service/provider 的逻辑，调用方直接走权威模块，例如现金走 `CashService`，价格分类和 provider 走 `src/pricing/*`。
- 对破坏性入口，默认从广泛适配面删除，而不是继续靠 `dry_run`/`confirm` 降低风险。
- 删除兼容入口后立即跑 `rg` 查断点，再跑 targeted tests、`tests/run_tests.py`、全量 pytest、compileall 和 diff check。
