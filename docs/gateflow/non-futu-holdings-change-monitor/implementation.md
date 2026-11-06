# 非富途持仓变动监控实施记录

## 目标确认与授权

2026-11-06 用户明确要求“按这个方案实现”，依据 [第 2 版设计](../../non-futu-holdings-change-monitor-design.md) 及 [受影响面复核](../../reviews/plan-review-20261106-093533.md)，采用已审的完整读取、静默基线、非富途股票／非 MMF 基金、数量／身份／删除观测和重复 case 交接。本轮实现本地代码、测试和文档；后台默认关闭，账户由配置显式指定，2 秒合并／60 秒核对／120 秒 stale 为配置默认。未来长桥尚未实现，其数量通知 owner 不在本轮变更现有运行行为。

不访问生产、不改真实配置、不启用服务或订阅、不发送真实回执。本地研发授权不自动进入交付；后续提交／推送授权与实际结果在下文单列，PR／合并／发布／升级仍需另行授权。分支 `docs/longbridge-holdings-sync`，基线 `d8ff64747262e08211ea64b1fdfb4a566aba5ed4`；已有索引及两份设计归属明确，保留长桥文件与评审快照。

## 实施计划与总控核对

一个 implementation slice，代码、测试、文档共同验收：

1. 纯观测投影／比较保留 raw 缺失与非法语义、最后有效基线、两次删除证明、身份／范围变化和重复 gap；提供观测回执冻结 payload。
2. OperationStateStore 增量 feature schema：scope／epoch、baseline、generations／到期／退避、变更审计；原子 baseline＋case＋outbox，明确 ready／scope mismatch 和只读 status。
3. app monitor 编排 fresh 完整读取（分页严格模式）、target 锁、初始化／预览／单次执行、周期／dirty 调度及释放锁后派发；同一事件 admission 事务标 dirty。
4. NFHC-01 复用重复规则与 workflow owner；单行 case 关闭保护、完整 notify／NAV preflight 的证明及锁顺序，保留原门禁。
5. CLI `holdings changes init/scan/status`，配置默认关闭并显式账户；两个 listener 共用 monitor 装配和退出等待；归属文档更新。
6. 定向回归、完整门禁、完整 deepreview 和必要修复／受影响面复审。无交付授权时完成本地可审查结果后停止。

总控核对：实现方向和重要契约已由第 2 版两路计划审核覆盖，源码 HEAD／配置／相应设计 hash 未变，可复用其设计证据（不视作实现验证）。实现仅落实已审责任和状态边界，无新增公共 API 或架构目标；具体函数与表字段由实现决定。普通实现选择不再询问。独立任务负责共享重复 case 路径，主任务负责 monitor／store／入口／回执，文件所有权明确，不重复修改。

## 验证计划

定向测试覆盖静默初始化、数量与身份变化、gap／重复恢复、双完整读取删除、行替换、事件去重／代次、事务崩溃、投递 unknown、preview／manual／background 隔离、scope 变化及旧能力兼容。严格分页用模拟客户端证明缺 items／终止字段不能当完整空表。

完整门禁：

```bash
python3 -m pytest tests -q
python3 -X pycache_prefix=/tmp/pm_pycache -m compileall src skill_api.py scripts/pm.py scripts/publish_daily_report.py
ruff check src skill_api.py scripts/pm.py scripts/publish_daily_report.py
python3 -m scripts.export_om_openapi --check
git diff --check
```

## 进度与状态

- 当前交付阶段：用户于 2026-11-06 明确要求“提交并推送本次实现”，提交／推送检查进行中；下一入口为远端提交核验。未授权 PR／合并／发布／升级，不标记 draft-PR-pass 或整个 Gateflow final closeout pass。
- 本轮本地实施状态：**完成**。完整 slice 代码／测试／运行文档已实现；共享 NFHC-01 路径 262 项定向测试。初审版全库 **1929 passed in 16.63s**；审核数值问题和总控新进程只读问题修复后，232 项定向通过、全库 **1948 passed in 17.54s**，compileall／Ruff／OpenAPI／diff 检查重新通过。8 份归属文档／记录、52 个本地链接／锚点及格式检查通过；实际 CLI help 与本地 JSON status 隔离验证通过，未创建数据目录或 DB。
- 代码包括 monitor/domain/store、严格完整 raw read、CLI 与两 listener、Agent outbox、重复 case 的全量交接和单行关闭保护、notify／preflight target 锁。默认关闭且 accounts 为空，未触及真实配置。
- [完整审核记录](../../reviews/code-review-20261106-102504.md)保存初审及修复版本清单和快照，最终结论 **pass-with-risks**。两路完整初审及双路必要受影响面复核均已完成；NFHC-C01 数值异常／舍入、NFHC-C02 只读 status 导入目录副作用均 accepted，最终状态 `已修复`，无未解决 blocker。两位审核者复核前后均核实 32 份实现清单匹配；第二路额外验证共享 digest／case precondition，第一路 142 项独立定向通过。
- 证据适用性：1948 项完整验证发生在最后源码修复后，之后只编辑开发记录／LESSONS；源码、测试和运行文档与可取回修复快照保持一致，因此不重复全库。LESSONS 已记录真实 Decimal 规范化问题及防回归入口。
- 本次仅交付非富途实现、设计依据及归属文档；同分支已有长桥草案及其索引条目保留在工作区。review 目录按现有 ignore 规则仅本地保留，审核结论和修复状态在本记录中同步可读。PR／生产联调／启用／长桥业务实现均未进入。

## 剩余风险与 owner

- 实际飞书耗时、跨页非原子和投递恢复：assigned to later work unit；运维在独立生产联调授权下测量，本轮模拟适配器故障测试，不声称真实达标。
- 相同重复组再现沿用不重发首次发现：requiring new issue or explicit user decision；用户若要强化另行确认，不增机制。
- 未来长桥通知归属：requiring new issue or explicit user decision；该接入实施前协调两份设计，不改变当前运行链路。
- 完整回归／共享锁与事务正确性：fixed in current slice；1948 项全库及必需静态门禁、两路完整审核和实际修复复核通过。跨页非事务和真实时效仍按上文独立联调分工，不把本地结果扩大为生产保证。
- PR／合并／发布／升级：requiring new issue or explicit user decision；本次用户只授权提交／推送，完成后核验远端并停止。

## 提交／推送交付核对

- 用户于 2026-11-06 明确授权“提交并推送本次实现”；交付按 [Delivery](/Users/liuxie/.agents/skills/delivery/SKILL.md) 执行，不创建 PR 或变更版本。
- 目标：`origin`（`https://github.com/liuxie066/portfolio-management`）的 `docs/longbridge-holdings-sync` 分支；检查时该远端分支尚不存在。已 fetch 远端 main，本地 main 与 origin/main ahead／behind 为 0／0，均为已审基线 `d8ff64747262e08211ea64b1fdfb4a566aba5ed4`。
- 32 份实现／测试／运行文档与已审修复 manifest 匹配，1948 项 pytest、compileall／Ruff／OpenAPI 证据仍适用；提交准备仅同步设计／本记录的状态文案、隔离索引中长桥条目，不改变业务源码、依赖或配置。
- 暂存范围：本轮 32 文件清单、LESSONS、非富途设计及本实施记录；`docs/INDEX.md` 只纳入非富途条目。长桥文件／索引和 ignored review 产物不提交；原始审核快照保留本地，不 force-stage。
- 本节固定提交前证据；生成的 commit、远端 ref 及其一致性在交付回执中记录，避免在被交付提交内自引用尚未生成的 SHA。请求到授权终点的精确起始时间未知，首次交付时钟采样为 `2026-11-06T03:01:25Z`，不将后续命令耗时冒充端到端耗时。
