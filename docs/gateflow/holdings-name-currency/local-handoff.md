# 本地交付：持仓名称补全与币种纠正

本地实现完成，生产持仓与服务未改。工作树 `/tmp/pm-holdings-hk-name-currency`，
分支 `fix/holdings-hk-name-currency`，基线 `b8e75ea`（v0.2.6），
实现检查点 `e4c892eeec40aff3f41509430296d3cc254de587`。

## 实际行为

1. 已明确为非富途港股、名称为空且市场/代码证据匹配时，自动补名称。
   支持裸代码及 HK 前后缀别名；不会修改资产代码、数量、账户、券商或已有名称。
   无证据、来源不匹配、重复响应、超时或并发人工修改时保留人工处理。
2. 币种不再仅按字母长度放行。合法及历史代码按离线有来源目录保留；
   无效原值和无效建议值均无法成为可写入的普通补全。
3. 已审核的 `HDK → HKD` 拼写规则提供有依据的待确认候选；有其他权威证据
   冲突时不猜。事件补名和普通缺失字段补全不会顺带改币种。
   只有明确确认当前精确 case 后才写币种；源值/身份变化使旧建议失效。
   无效币种不可通过保留当前值、旧豁免或类型转换解除 NAV 阻断。

关键实现入口：

- `src/app/asset_name_service.py:55`：精确港股名称来源。
- `src/domain/holding_names.py:21`：市场、代码、名称、来源与证据时间验证。
- `src/app/holdings_reconciliation_service.py:184`：自动名称候选范围。
- `src/app/holdings_validation.py:793`：币种合法性、唯一纠正候选及冲突证据。
- `src/domain/holding_currencies.py:31`：版本化纠正规则。
- `src/app/holdings_workflow_service.py:1343`：无效币种不能保留豁免。

## 验收

- 最终定向实现测试：253 passed。
- 独立 QA：66 passed，0 failed/0 skipped；独立 code review 无未修复实质 finding。
- 独立 aggregate deepreview：pass-with-risks，无新增实质问题；本地报告
  `docs/reviews/code-review-20261026-110532.md`，审查对象为实现检查点 e4c892e。
- 真实日期全套：1746 passed、2 failed；两项为原有固定日期 NAV 样例过期，
  在未修改的基线代码上也复现了相同失败。
- 仅在外置测试夹具中固定这两项的日期后，全套 1748 passed；没有修改产品时钟
  或仓库中的这两项测试，不能把此结果表述为真实日期下无条件全绿。
- 编译、ruff、git diff --check 均通过。
- 原始红测、失败和通过日志、命令及隔离策略详见 `validation.md`。

## 保留边界

所有验证采用合成记录、mock HTTP/存储及临时 SQLite；没有读取真实账户资料、
调用交易接口、发送真实消息、补全真实行、变更配置或服务。
未 push、创建/修改 PR/issue、评论、merge、release、部署或触发远端 CI。
先前公开派发被拒的操作没有重试或间接执行。主工作树仍在原始基线；旧工作树保留。

当前交付到本地验证阶段，公开交付需另行明确授权。Gateflow 的 draft-PR-pass、
PR review 与 final-closeout 均未执行，不冒充已完成。

## 剩余风险

- 两项日期测试的长期可重复性：交给独立的测试夹具维护工作。
- 飞书无原子 CAS 的既有竞态：本次保留锁、重读和摘要保护，进一步改造留待后续。
- 腾讯真实响应/可用性、真实持仓与生产升级验收：本次明确未执行。
- 币种快照维护和更多拼写规则：后续评审更新，不使用模糊自动纠错。

下一入口：检查本地最终 status.md 及汇总审查结果；取得公开交付授权后才进入 push。
