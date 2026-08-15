# 拆分 OperationStateStore 上帝类

日期：2026-08-15
分支：`refactor/split-operation-state-store`

## 目标

把 `src/app/operation_state_store.py`（约 2800 行、单类 60 方法）按 6 个关注点拆成 mixin 模块，每个关注点一个文件，`OperationStateStore` 变薄 facade。

## 非目标

- 不改 schema、不改 SQL、不改行为、不改事务边界
- 不改消费方（11 处实例化 + 12 个测试文件零改动，靠 re-export shim）
- 不重设计 10 处跨关注点 `_tx` 耦合（本轮只记录）
- 不拆 DDL（`_initialize` 的 7 表 `executescript` 留在 base）

## 结构

```
src/app/operation_state/
    __init__.py                     # OperationStateStore = Base + 6 mixin
    _base.py                        # db_path/_connect/_initialize/_validate/now_factory/常量
    _fx_confirmation_mixin.py       # 4 方法
    _nav_receipt_mixin.py           # 5 方法
    _holding_case_mixin.py          # ~16 方法
    _holding_event_mixin.py         # 8 方法
    _cash_flow_event_mixin.py       # 7 方法
    _operation_receipt_mixin.py     # 9 方法（含两个共享 _tx）
```

`src/app/operation_state_store.py` 改为 re-export shim：
`from .operation_state import OperationStateStore`。

## 关键决策（含 Kimi 评审结论）

1. **mixin 继承而非子对象委托**：10 处跨关注点耦合全是 `_tx` 事务内共享助手（已 `@staticmethod` 且显式传 `conn`）。委托会拆散 `complete_holding_event` 的单事务原子性（一个 `BEGIN IMMEDIATE` 同时写 inbox claim + case 物化 + receipt）。mixin 同继承链上 `self._tx` 天然解析。

2. **共享 `_tx` 助手放 `_operation_receipt_mixin.py`，不放 base**：按表所有权划分——它们写 `operation_receipt_outbox`，receipt_type 白名单是 receipt 领域语义；依赖方向是 holding_case/holding_event/cash_flow_event → operation_receipt。base 只留连接管理 + DDL。

3. **`_initialize` DDL 集中 base**：7 表在一个 `executescript` 里带 `BEGIN IMMEDIATE`，建表前有 `operation_meta` schema 版本门禁（跨 holding/cash_flow 两个关注点），拆给各 mixin 会破坏原子性和校验顺序。

## 两个必改的隐蔽 bug（Kimi 发现）

1. **`resolve_db_path` 硬编码 `OperationStateStore.resolve_db_path_read_only(...)`**（:77）→ 搬进 `_base.py` 后会循环导入。改为 `@classmethod` + `cls.resolve_db_path_read_only(...)`。

2. **`Path(__file__).resolve().parents[2]`**（:82,91,94）→ 文件下移一层到 `src/app/operation_state/_base.py` 后，`parents[2]` 会从仓库根变成 `src/`，默认 `.data` 路径静默落错到 `src/.data`。测试全用 `tmp_path` 传路径，一处都不会红。改为 `parents[3]`（或等价 `config.get_data_dir()`，实现时验证二者等价）。

## slice 顺序（callee-first）

1. `_base.py` + facade 骨架，修上面 2 个 bug
2. `_operation_receipt_mixin.py`（含共享 `_tx`，所有跨关注点依赖的汇）
3. `_holding_case_mixin.py`（被 holding_event 依赖）
4. 叶子关注点任意序：`_fx_confirmation_mixin.py`、`_nav_receipt_mixin.py`、`_cash_flow_event_mixin.py`、`_holding_event_mixin.py`
5. 原文件改 re-export shim

每步：剪切方法 → facade bases 加 mixin → `pytest tests/ -x`。

## 诚实定位（Kimi 提醒）

这是**可导航性/所有权重构，不是消费方解耦**。拆完消费方拿到的仍是同一个全量 facade，仍初始化 8 张表。若目标是消费方隔离，应做窄 `typing.Protocol` 切片（独立 follow-up，本轮不做）。

## 验证

- `pytest tests -q`（1477+ 全过）
- `compileall` 干净
- `ruff check` 不新增
- 手动验证一次默认 db 路径解析（覆盖 bug 2 的路径，测试覆盖不到）
- `git diff --check`

## residual risks

- 与 PR #49（ponytail cleanup，未合并）都改 `operation_state_store.py`，合并时可能冲突 → 由后续合并解决
- 测试子类化（`FailMarkStore(OperationStateStore)` 覆盖 `mark_operation_receipt`）需保持兼容 → mixin 继承天然满足
- `test_pm_cli.py` patch `store_module.OperationStateStore` → shim 必须 re-export 同一个类对象
