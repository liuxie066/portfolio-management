# 统一 Bitable 事件归一化

日期：2026-08-29
分支：`codex/pm-maintainability-20260829`
基线：`origin/main@7e39155`（`v0.1.43`）

## Goal

让 holdings 与 cash-flow 两条 Feishu Bitable 事件入口共享同一份协议级校验与归一化实现，消除复制粘贴导致的规则漂移风险，同时保持现有运行行为和公共导入路径不变。

## Non-goals

- 不合并 holdings/cash-flow 的目标类型、归一化结果类型、异常类型或 inbox 状态机。
- 不删除兼容 CLI，不改订阅、长连接、worker、Feishu/SQLite 写入或 credential 行为。
- 不引入 Protocol、泛型框架、新依赖或新的配置项。
- 不顺手拆分其它大文件或清理项目级复杂度告警。

## Success signals

1. 协议 schema、payload 上限、identifier 上限、target 匹配、action 校验/去重/排序、revision、digest 与 create_time 的算法只有一个 owner。
2. `normalize_holding_event()` 与 `normalize_cash_flow_event()` 的函数签名、返回类型、异常类型、校验顺序和既有错误文案保持不变。
3. 两条 normalizer、两个 inbox、holdings compatibility adapter、共享 adapter 和 CLI 相关测试通过；项目完整验证不新增失败。
4. 改动限于共享 contract、两个薄 wrapper、两个现有 normalizer 测试和当前设计文档；不增加依赖或生产副作用。

## Current facts and constraints

- `src/app/holdings_event_service.py:68-153` 与 `src/app/cash_flow_event_service.py:69-152` 各有一份近乎逐行相同的归一化算法。
- `src/app/bitable_event_contract.py` 已拥有两条入口共享的协议常量和接收上限，是现成的协议 owner。
- 两条入口必须保留领域命名：`NormalizedHoldingEvent` / `NormalizedCashFlowEvent`，以及 `HoldingEventTargetMismatch` / `CashFlowEventTargetMismatch`。
- wrapper 当前暴露共享协议常量；`src/feishu/holdings_event_adapter.py` 与现有测试直接从 holdings wrapper 导入这些名字。实现必须用显式兼容绑定保留它们，不能把测试改为绕过现有导入路径。
- 事件进入 inbox 前先归一化；target mismatch 是可过滤事件，其它校验错误是失败。此失败分类不可漂移。
- 基线 focused validation 使用 Python 3.12：`95 passed`。系统 `python3` 是 3.9，不能作为当前项目验证解释器。
- 本仓库没有 `om-doc-hygiene` 约定；遵循现有 `decisions/YYYY-MM-DD-*.md` owner-first 方式保存本设计，不调用该 skill。

## Chosen design

在 `src/app/bitable_event_contract.py` 增加一个私有共享函数，且不加入 `__all__`：

```text
_normalize_bitable_record_changed_event(
    payload, *, target, event_label, target_mismatch_error
)
    -> normalized field dict
```

函数复用现有常量，完成全部协议级步骤，并通过 `event_label` 保留当前错误文案，通过 `target_mismatch_error` 抛出当前领域异常。两个现有 public normalizer 只负责传入领域参数并用现有 dataclass 构造返回值。

`target` 继续使用现有对象的四个属性；不为两个实现创建只有一个用途的 Protocol 或基类。共享函数返回仅供两个 wrapper 展开的字段 dict；不新增第三个公共结果类型。两个 wrapper 显式保留现有 module-visible 协议常量，避免抽取后出现兼容导入漂移。

### Rejected alternatives

- **只抽 action helper**：仍会保留 schema、target、digest 等大段重复，收益不足。
- **合并两个 dataclass/target/exception**：减少的代码很少，却会扩大公共契约变化和调用方风险。
- **重写整个 event listener 层**：当前重复有明确单点 owner，可用一个小改动解决；全面重构没有成功信号支持。

## Owners, contracts, and data flow

```text
Feishu payload
  -> holdings_event_service.normalize_holding_event
  -> bitable_event_contract shared normalizer
  -> NormalizedHoldingEvent
  -> HoldingEventInboxService

Feishu payload
  -> cash_flow_event_service.normalize_cash_flow_event
  -> bitable_event_contract shared normalizer
  -> NormalizedCashFlowEvent
  -> CashFlowEventInboxService
```

- 协议级 owner：`src/app/bitable_event_contract.py`。
- 领域 target/result/exception owner：各自的 `*_event_service.py`。
- durable acceptance、lease/retry、业务 completion owner 不变。
- 无数据模型、持久化 schema、公共 JSON、CLI 或服务 contract 变化。

## State transitions and failure behavior

本改动不改变状态转换。合法事件仍先完整归一化，随后才允许 durable inbox acceptance。校验顺序冻结为：payload object / canonical size / schema / header+event object / required event ID / identifier limits / exact target / action list / revision+digest。target mismatch 因而仍在 action 校验前抛各自领域异常供 inbox 过滤；畸形、超限或 schema 错误仍不会因目标不同而被静默过滤。其它校验错误仍抛 `ValueError`；任何失败都发生在 SQLite/Feishu 业务副作用之前。

## Implementation slices

仅一个 slice：

1. 把两份相同协议算法移动到 `bitable_event_contract.py`。
2. 把两个 public normalizer 收敛为薄 wrapper，并显式保留当前协议常量兼容绑定。
3. 在两个现有 normalizer 测试中锁定领域结果类型、target mismatch 类型/完整文案，以及可区分 `event_label` 的完整错误文案；用最小非法 payload 表证明校验优先级不变。
4. 运行两条 normalizer、inbox、holdings compatibility adapter、共享 adapter、CLI focused tests，再运行项目规定的完整验证。

## Validation plan

```bash
python3.12 -m pytest tests/test_holdings_event_service.py tests/test_cash_flow_event_service.py tests/test_holding_event_inbox_service.py tests/test_cash_flow_event_inbox_service.py tests/test_feishu_holdings_event_adapter.py tests/test_feishu_bitable_event_adapter.py tests/test_pm_cli.py -q -p no:cacheprovider
python3.12 -m pytest tests -q
python3.12 -X pycache_prefix=/tmp/pm_pycache -m compileall src skill_api.py scripts/pm.py scripts/publish_daily_report.py
ruff check src skill_api.py scripts/pm.py scripts/publish_daily_report.py
git diff --check
```

Ruff 当前存在已知 `E402` 基线时，验收标准是不新增 finding，并明确记录基线；不借此维护性切口扩大修复范围。

## Risks and open questions

- 风险：共享 helper 参数错误可能让一条入口抛错类型、错误文案或校验优先级漂移。Owner：本 worktree；由两条现有 normalizer/inbox 契约测试和 diff review 覆盖。
- 风险：抽取时把 wrapper 上现有的协议常量误删为 unused import。Owner：本 worktree；用显式兼容绑定和 holdings adapter focused test 覆盖。
- 风险：共享实现未来被误当公共 API。Owner：`bitable_event_contract.py`；保持窄导出并仅由两个 wrapper 调用。
- Residual risk：其它大文件与复杂度告警不在本 work unit；后续只有在出现具体重复、缺陷或变更成本证据时再开独立 slice。
- Open questions：无。
