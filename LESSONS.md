# 开发经验

只记录真实发生、具有复用价值且有证据的问题，不记录任务流水账。
返工时先考虑测试或自动检查；已有检查能覆盖的，链接它而不再造一套规则。
历史验收结果只证明当时状态，不代表当前测试已通过或生产问题已关闭。

## 条目格式

每条使用稳定主题，并填写：

- **触发条件**：什么任务、输入或运行状态下应检查。
- **错误原因**：已证实的机制；未证实的猜测不能写作根因。
- **下次检查方式**：具体检查位置、回归用例或命令；无自动检查时说明限制。
- **证据**：仓库内的问题/修复记录与对应测试或实现链接。

## SQLite 事务上下文不等于关闭连接

- **触发条件**：常驻监听或反复轮询通过共享 OperationStateStore 创建 SQLite 连接，
  包括没有待处理事件和异常退出的分支。
- **错误原因**：将连接的事务上下文当作连接生命周期管理；提交/回滚后没有保证关闭，
  可累积文件描述符。共享连接入口已改为在 `finally` 显式关闭，并保留事务语义。
- **下次检查方式**：检查共享连接入口及调用者；修改生命周期时运行
  `python -m pytest tests/test_operation_state_store.py -q`，保留正常/异常路径关闭、
  commit/rollback 和空队列反复 claim 的断言。
- **证据**：[实施记录](docs/gateflow/feishu-event-listener-fd-leak/implementation.md)、
  [连接实现](src/app/operation_state/_base.py)、[回归测试](tests/test_operation_state_store.py)。

## 权威元数据必须穿过缓存和兼容回退

- **触发条件**：修改 NAV 索引、进程重启恢复、飞书字段兼容重试或单笔/批量写入。
- **错误原因**：本地索引曾丢弃 `details.finality`，重启后把有效 final 记录误判为
  `existing_nav_not_final`；另一处 `FieldNameNotFound` 回退曾删除 `details` 后重试，
  造成写入表面成功、飞书未保存权威 finality，而缓存声称完成。
- **下次检查方式**：运行 `python -m pytest tests/test_nav_bulk_upsert_minimal.py -q`；
  保留远端 → 索引 → 重启往返测试，以及单笔/批量 create/update 的拒绝降级测试。
  核实缺失必需元数据时无成功缓存发布；无 finality 的历史兼容路径仍按原约定工作。
- **证据**：[两次评审发现及修复](docs/gateflow/nav-finality-write-authority/fix-aggregate-review.md)、
  [回归测试](tests/test_nav_bulk_upsert_minimal.py)、[存储实现](src/feishu/repositories/nav_history_repository.py)。

## 完成声明不能强于验证证据

- **触发条件**：部分检查通过，但全库检查存在失败或本任务尚无生产验收时准备收口。
- **错误原因**：监听器修复曾在全库 Ruff 仍报继承问题时标记满足全部检查，顶层完成
  声明与详细结果矛盾；改动文件 lint 通过不能推导为全库 lint 通过。
- **下次检查方式**：逐项对照验收条件、命令退出状态和改动范围；继承基线需给出证据，
  明确失败、未运行和剩余阻塞，不绕过门禁，也不越界修无关文件。该项仍需收口时人工核对。
- **证据**：[发现 DR-FD-01](docs/reviews/code-review-20260818-123350.md)、
  [修复复核](docs/reviews/code-review-20260818-123500.md)。其中历史 Ruff 数量不代表当前基线。

## 数值规范化不能依赖 Decimal 运算精度

- **触发条件**：将原始持仓 quantity／avg_cost 转成基线、比较值或 case digest；尤其是
  完整读表包含范围外数据，或原始数字超过默认 28 位精度时。
- **错误原因**：用 `quantize`／`normalize` 做字符串规范化，前者对 `1e30` 抛异常，
  后者会舍入长小数；投影在范围过滤前调用时，一条排除行也能阻断整轮扫描。
- **下次检查方式**：以不改变数值的格式转换生成 canonical 值；运行
  `python3 -m pytest tests/test_holding_change_monitor.py tests/test_holding_change_monitor_boundaries.py -q`，
  保留不同 Decimal precision、大数／长小数、范围外 quantity／cost 及长小数变更断言。
- **证据**：[发现及修复 NFHC-C01](docs/reviews/code-review-20261106-102504.md#findings)、
  [共享数值规则](src/domain/holding_values.py)、
  [观测回归](tests/test_holding_change_monitor.py)、
  [精度与 canonical payload 回归](tests/test_holding_change_monitor_boundaries.py)。

## Git 忽略规则不定义用户发布范围

- **触发条件**：发布源码归档、构建用户运行包或调整安装器获取代码的路径。
- **错误原因**：v0.2.13 仅创建 GitHub Release，自动归档包含已跟踪的测试、开发流程和
  评审记录；安装器使用完整 `git clone`，即使归档过滤也不能缩小该安装路径的范围。
  `.gitignore` 不排除已跟踪文件，也不控制 `git archive`。
- **下次检查方式**：使用 `.gitattributes` 的运行文件白名单构建实际归档，运行
  `python -m pytest tests/test_release_package.py -q`，同时检查归档内容、必要资源、
  默认安装路径和校验失败分支；完整源码安装须显式选择，不能作为下载失败的回退。
- **证据**：[v0.2.13 发布](https://github.com/liuxie066/portfolio-management/releases/tag/v0.2.13)、
  [归档白名单](.gitattributes)、[归档与离线安装回归](tests/test_release_package.py)。
