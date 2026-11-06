# 非富途持仓变动监控与回执设计

- 状态：第 2 版设计已确认并完成本地实现和代码审核；生产监控尚未启用，未发送真实消息。第 2 版原始设计及审核快照保留为本地评审证据，当前实现与验证见[实施记录](gateflow/non-futu-holdings-change-monitor/implementation.md)。
- 日期：2026-11-06。
- 代码基线：`d8ff64747262e08211ea64b1fdfb4a566aba5ed4`（v0.2.12）。
- 范围：设计、非富途监控实现及相应验证／运行文档；用户后续已授权提交和推送。长桥设计保持独立，不纳入本实现提交。生产访问、PR、合并、发布或部署仍需单独授权；下文“当前事实”和“拟新增”保留设计时基线口径，运行行为以[监听手册](holdings-event-listener-runbook.md#non-futu-holdings-change-monitor)为准。

## 1. 目标与成功信号

在飞书 `holdings` 的非富途股票、基金记录发生新增、数量增减、归零、删除或身份变更时，PM 能发现变化，持久保存前后观测，并通过现有 Agent 发送可理解、可追溯的回执。

本设计监控的是飞书表事实。它不查询券商，不证明成交、不推导买卖或现金流，也不改写持仓。人工编辑、导入、PM 或未来长桥同步造成的表变化，都按实际观测处理；没有来源证明时不能把操作者写成“用户”或“券商”。

建议正常依赖下的初始验收目标：事件被本地收到后 10 秒内完成一次观测并将必要回执入队；每 60 秒全量核对一次，稳定存在的变化在一个周期加处理时间内发现。消息另行展示 queued／sent／unknown，入队不等于送达。目标账户数量及实际耗时待确认、实测；失败、限流或处理超时不算达标。

首期不覆盖逐次编辑日志、行情／基金净值变化、持仓估值、现金流 effects、NAV 质量契约、券商账户监听或持仓自动修复。监控失败只影响本功能状态，不新增正式 NAV blocker；现有校验和门禁继续独立执行。

## 2. 当前事实与可复用边界

| 当前能力／缺口 | 直接证据 | 设计处理 |
|---|---|---|
| holdings 新增、编辑事件进入持久 inbox | [事件接纳](../src/app/holding_event_inbox_service.py#L41) | 复用精确 target 校验、事件去重和持久接纳 |
| 删除动作目前不参与业务处理 | [动作范围](../src/app/holdings_event_service.py#L23) | 删除也触发本监控；原字段校验仍只处理新增／编辑 |
| 现有事件 worker 负责单行校验、补名与 case 回执 | [处理入口](../src/app/holdings_workflow_service.py#L106) | 保留职责；单行输入不能证明跨行重复，重复事实按第 6.1 节交给同一 case owner |
| 重复检测依赖传入的记录集合，外部关闭目前按 record_id 处理旧 case | [纯检测](../src/app/holdings_validation.py#L1127)、[现有关闭逻辑](../src/app/operation_state/_holding_case_mixin.py#L612) | 复用检测与 case 语义；补齐完整集合证明及关闭范围，防止单行事件误关闭重复 case |
| 当前 case 状态与现金 fingerprint 都不是证券数量基线 | [operation schema](../src/app/operation_state/_schema.py)、[现金扫描](../src/app/cash_flow_effect_service.py#L626) | 在同一 OperationStateStore 增加独立观测状态，不占用现金 effects |
| 可读取完整 raw holdings，避免模型默认值制造事实 | [仓库 raw read](../src/feishu/repositories/holdings_repository.py#L414) | 通过仓库读取，不走缓存持仓、估值或 Futu observer |
| broker 已有 Futu／non_futu／unknown 分类 | [现有分类](../src/app/holdings_validation.py#L439) | 复用，不另造券商别名列表 |
| operation outbox 有事务入队、claim 和未知发送状态 | [事务插入](../src/app/operation_state/_receipt_tx.py)、[派发](../src/app/operation_receipt_outbox_service.py#L47) | 扩展一个明确的回执类型与 renderer，保持既有状态语义 |
| 富途后台已有独立数量回执 | [Futu 回执条件](../src/app/futu_quantity_receipt.py#L12) | 排除 Futu scope，保留原链路 |

现有 [combined listener](../scripts/pm.py#L686) 启动独立 holdings／cash-flow worker；它没有本功能的周期核对。新监控循环放在这一运行入口及已有 holdings-only 入口的共同装配处，不要求启用 HTTP API 服务，也不新建常驻服务或飞书应用。

## 3. 监控范围与回执规则

### 3.1 明确纳入的记录

scope 由配置的 holdings Base／table 与显式 PM 账户列表组成。纳入 broker 分类为 `non_futu` 的股票及基金：`a_stock`、`hk_stock`、`us_stock`、`exchange_fund`、`otc_fund`、`us_fund`，以及现有读取和校验仍接受的历史基金类型 `cn_fund`、`hk_fund`、`fund`。历史值仅按当前校验兼容读取，不增加新的 Feishu select 选项。

CASH、MMF、crypto、other、bond 等不纳入本轮。Futu／moomoo／富途按现有 broker 分类排除；空 broker 和 unknown 不当作非富途。类型无法确定时不按名称猜股票／基金。

数量使用有限、非负数，支持基金份额和股票碎股，不取整，不以金额代替份额。负值属于首期范围之外，标为不可比较，保持原数据并由现有校验／人工路径处理。比较时复用现有数字解析规则，规范化有效零与小数表示，不以显示格式差异触发回执。

### 3.2 变化含义

| 观测前后 | 回执行为 |
|---|---|
| 基线初始化时已存在的持仓 | 静默建立基线，不补发历史新增 |
| 初始化后首次观察到非零、有效的纳入记录 | “新增观察持仓”，显示当前数量；不声称刚刚开户或买入 |
| 首次观察到零数量行 | 建立基线，静默 |
| 同身份数量增加／减少 | 显示“数量 旧值 → 新值”，不写成买入／卖出 |
| 同身份从非零变为零 | 显示“数量归零”，保留记录，不推导实际清仓交易 |
| 零数量恢复非零 | 显示“恢复持仓”及前后数量 |
| 已跟踪记录经确认删除 | 显示“记录已删除”及删除前身份／数量，数量后值为“不存在”，不能写成 0 或卖出 |
| account／broker／asset_id 改变 | 一条“持仓身份变更”，显示旧、新身份和各自数量；不拆成一笔卖出和一笔买入 |
| asset_type／currency 改变 | 显示“类型／计量信息变更”，不把不同单位数量相减；同批数量变化也同时展示 |
| 从纳入范围移入 Futu、现金或未配置账户 | 对原纳入记录提示“移出监控范围”，保留最后观测，不触发新范围的数量回执 |
| 已跟踪但曾移出范围的记录再次有效纳入 | 显示“重新纳入监控”；重新建立比较起点，不跨范围推导交易差额 |
| 仅 avg_cost、名称、分类、行业、标签、时间戳改变 | 可更新观测描述；不主动发数量回执，现有数据校验仍独立运行 |
| 值非法／身份冲突／重复业务键 | 不发正常数量回执、不覆盖最后有效基线；状态显示 gap，现有校验负责修复提示 |

数量与身份可比较至少需要有效的 `record_id/asset_id/account/broker/asset_type/currency/quantity`。缺名称或可选成本不会让一条原本可比较的数量变化失去观测机会：回执可只显示代码，不能把它标成完整数据校验通过。

## 4. 观察身份、基线与首次启用

### 4.1 两种身份各司其职

本地观测主键为 `(holdings target, monitor epoch, record_id)`，用稳定行 ID 追踪身份编辑与删除。业务身份继续复用 `(asset_id, account, broker)`，用于匹配和发现重复；不静默重写代码、账户或券商别名。

每个已纳入或曾纳入的行保存：最近有效身份、类型、币种、规范数量、显示名称、可选成本、最近观测时间、单调观测版本及 `active/gap/missing_candidate/deleted/out_of_scope` 状态。另保留当前不可比较的原因与观测时间；gap 不能覆盖最后有效值。

未纳入且从未跟踪的行无需保存完整快照。它们后来进入范围时以“首次观察／纳入”表达，不声称知道其完整历史。删除 tombstone 和 out_of_scope 状态保留到显式重建基线，防止重启或迟到事件重发删除。

### 4.2 首次基线

配置默认关闭。拟新增 `pm holdings changes init` 的预览和显式确认入口：读取目标表、列出纳入账户／行数、排除项和不可比较项；确认后将整次可用基线与初始化完成标记在同一 SQLite 事务中写入，不发送变动回执。

初始化必须在可证明完整的全表读取后进行。属于目标账户且疑似应纳入的行缺少必要身份／类型／数量，或业务身份重复时，初始化保持未完成，报告待修数据，不跳过后标记 ready。确定在范围外的行不阻塞。完全没有纳入记录但完整读取成功，可初始化为空基线，以便之后发现新持仓。

监听接纳可继续将事件持久化；基线未就绪时不发送本功能回执。初始化完成后立即安排一次追赶观测，再处理后续到期扫描。初始化期间各行的采集不是飞书事务快照，回执起点是该批实际观测值；不能承诺还原建立基线之前或读取间隙的所有编辑。

同一 scope 已 ready 时，重复 init 不覆盖基线。配置账户、target 或比较策略变化标记 `scope_changed`，暂停本功能结算并要求预览和显式重建；不在代码升级、进程启动或丢失 SQLite 后静默重置基线。重建只影响监控技术状态，不撤销已发消息或修改飞书；历史 outbox 的处理需在重建预览中明确。

## 5. 事件与周期核对共用一条观测流程

为保持实现简单，首期采用**事件触发的全表观测**，不同时实现单记录与全表两套比较器。持仓表属于当前个人／固定协作者规模；全表读取能同时检查重复身份、账户迁移和删除。若实测分页规模导致 10 秒目标无法满足，再评估增量优化，不预先增加索引服务。

```text
飞书 holdings 新增／编辑／删除事件
    → 现有 target 校验与 event_id 去重
    → 同一接纳事务更新“监控需刷新”标记
启动／每 60 秒周期核对 ──────────┘
    → 取得当前 target 的同机观测锁
    → 经仓库完整 fresh-read raw holdings
    → 比较当前行与最后有效基线，确认缺失候选
    → 事务更新基线、观测序号、变更记录及回执 outbox
    → 事务提交后尝试派发新回执
```

### 5.1 与现有校验事件解耦

在现有 `accept_holding_event` 的事务中，为已启用、已初始化的监控 scope 增加 dirty generation／到期时间。只在事件实际新增时标记，重复 event_id 不增加新的监控工作；回调仍只做本地持久化，不读取表、不发消息。新增／编辑／删除均可触发观测，原字段校验的 actionable 常量不直接扩大到删除。

原 holdings worker 继续消费 inbox、生成字段校验 case／自动补名及其回执；新监控循环消费账户 scope 的刷新标记。两者独立调度：名称补全失败、Futu observer 不可用等不能阻断一个已持久接纳的非富途数量观测；监控失败也不能让原校验误报未执行。新比较器不调用会遍历 Futu 账户的 `HoldingsReconciliationService.evaluate_records`。跨行重复复用纯检测规则，由 `HoldingsWorkflowService` 用本轮完整 raw rows 规划 case，在观测事务中落地，具体交接和关闭证明见第 6.1 节；无需等待原事件 worker，也不让 monitor 自建 case 或告警规则。

### 5.2 调度、并发与恢复

首个 dirty 触发设置固定 2 秒合并窗口，后续通知增加 generation，但不无限延后已有到期时间。每 60 秒无条件安排核对，即使没有事件；启动时如基线 ready，立即查询以恢复断线期间的持仓净变化。不可依赖 SDK 提供重连回调或事件补发。

同 target 的 init、手工 scan 和后台观测共用 `process_lock`，从检查状态、取 generation 到源查询、事务结算全程持有；不得先读表后抢锁。多个 listener 进程竞争相同锁，后台取得后再次检查 due 状态，不重复使用旧查询；手工 scan 不受 due 限制，按单次调用契约执行。锁以实际 Base／table 和数据目录隔离，不以不同配置 hash 建立两把可并行写同一基线的锁。

扫描冻结本轮 generation，远端读取在 SQLite 事务之外执行。结算时只完成冻结的代次；若读取期间有新 generation，保留到期标记再扫一次。周期和缺失候选复查也能保留下一次到期，不能被“本轮完成”覆盖。

读取／解析失败不推进基线或 completed generation。失败按 2／5／15／30／60 秒退避，默认上限 60 秒；新事件不重置失败退避造成风暴，成功后恢复正常周期。进程崩溃后未结算代次重新 fresh-read，不重放旧事件字段。监控循环可复用已有 listener 的 stop 事件，正常退出等待本轮本地结算结束，锁释放后才能由其他循环接手。

### 5.3 完整读取与删除证明

必须从配置的准确 Base／table 无账户、类型或非零数量过滤地读取 raw rows，再在本地确定范围。这样记录改账户、变零或变成非法类型时不会仅因过滤结果缺失被当成删除；原记录 ID 仍在原始列表中就不得走删除分支。

已有 `list_records` 会拒绝重复 page token、非终页空列表及缺 token，但[终止判断](../src/feishu_client.py#L448)仍可能接受部分缺省字段。监控专用的仓库读取需要显式完整性结果：校验每页 items、record_id／fields 结构及分页终止，失败与字段缺失不能默认成空列表／终页。完整列表只证明本次读取遍历完成，不证明飞书跨页提供事务快照；不修改行情／估值缓存，也不在 app 中另造直接 HTTP 入口。

之前存在、当前原始全表列表缺失的记录先记为 missing_candidate，不立即通知删除、不丢失最后有效值。安排至少间隔 2 秒的下一次完整读取，仍缺失才结算删除。只对两次完整读取均覆盖的相同 target／scope 判断缺失；失败、权限错误、不完整分页和配置变化不算第二次确认。

收到删除事件也不把 payload 当删除事实；经上述读取确认。没有基线的未知 record_id 被删，只保留已有事件审计，不凭空补删除前数量或账户。

如果旧行缺失同时出现唯一同业务身份的新 record_id，先与 missing_candidate 一起等待确认，再提示“记录重建／替换”，展示新旧行及数量，不能分别发新增、删除并解释成交易。当前表内相同业务身份有多行时，该身份保持 gap，不求和、不自动挑一行。

## 6. 不完整数据与观测能力边界

整次远端读取失败时不结算任何缺失或数量变化。读取完整但部分行不可比较时，已有行以 record_id 保持 gap；其余无冲突、可比较记录仍可推进。不得先滤除非法行，再把未出现于有效列表的基线行清成删除。

缺失／非法数量和身份字段继续由现有单行校验处理；本功能记录 gap，不另建字段修复提示。跨行重复不能依赖单行事件发现，按第 6.1 节把完整读取的重复事实交给现有 case owner，复用其“Holdings 数据校验／处理结果”回执，不另发一套告警。修复后与最后有效观测比较，回执标明观测有间断及前后时间，不声称变化恰好发生在修复瞬间。

身份编辑发生在同一 record_id 时，先处理身份／范围变化，再决定是否仍可比较数量。记录改为未知 broker 或缺少账户时保持 gap，不能声称其已移到某个新账户；明确改成范围外的有效值才可提示移出监控。

本方案是观测之间的净变化监控。例如 `100 → 120 → 100` 在 worker 查询前已全部发生，只读到 100 时不会报告未观测到的中间值；事件只证明值得刷新，不能补造变化。跨两次已结算观测的 `100 → 120 → 100` 则应产生两条不同变更，不被内容 hash 去重丢掉。无法可靠取得编辑人、成交或准确修改时间时，回执只用观测时间区间。

### 6.1 重复业务身份向现有 case 链路的交接（NFHC-01）

**数据与职责**：monitor 将本轮完整 raw rows、target／scope、观测标识及完整性结果交给 `HoldingsWorkflowService` 的只读规划能力。由它复用 `HoldingsValidator` 的重复分组规则，生成原有 `duplicate_identity` issue、`__record__:duplicate_identity` case 及现有发现／关闭回执计划。实现可提取当前纯重复分组作为共享函数，不复制算法、不让 monitor 直接拼 case key。该规划不做网络读取、补名、Futu 查询或自动应用，也不把完整 validator 产生的其他字段 case 一并导入监控事务。

检测集合必须保留同业务身份的全部原始行，不能先按股票／基金或 quantity 有效性过滤。例：目标股票行与同身份但误填为 cash 的行仍是重复；至少一个成员是当前或最后有效基线纳入的记录时，关联整组以证明 gap，只为本监控覆盖且存在的行规划对应 case。范围外的其他账户／broker 不生成新 case；证明身份不完整时保持 gap，不推测无重复。身份分组不改变既有业务键规范。

**创建与去重**：case 仍由现有 owner 管理，成员 `record_ids` 排序，使用现有 case 身份／policy 规则及 `pending_manual_edit` 状态；记录成员、业务身份和本轮完整读取证据。case 创建、monitor gap 及 outbox 在同一次观测事务提交。相同成员组重复读取只刷新证据，不重复入队；成员变化使用现有 supersede 语义，不能因为 quantity、名称或触发 event_id 改变而新建同一重复问题。已有其他入口创建的同语义 case 直接刷新，不能形成第二份。

**关闭证明与并发**：现有[关闭函数](../src/app/operation_state/_holding_case_mixin.py#L612)没有区分单行字段证明和跨行重复证明，须补明确的证明覆盖范围。凡单记录事件、`reconcile --record-id`、字段应用后的读回等，仅有单行数据的入口都不得以“本轮 active_case_keys 不含重复 case”关闭、supersede 或证明重复已修复；不能只保护 monitor 来源的 case。重复 case 的创建／刷新／关闭均只能使用完整覆盖关联身份集合的 fresh-read。所有能写重复 case 的入口须复用第 5.2 节 target 观测锁，从读取到结算串行；其他单行 case 不抢此锁，锁顺序为 target 观测锁在前、既有 account／record 写锁在后，不能持有 account／record 锁后再等 target 锁。手工全量／账户级 `holdings reconcile --notify` 及 [NAV preflight](../src/app/holdings_nav_preflight_service.py#L505)也能产生重复 case：它们保留原校验和门禁语义，须将观测锁放到源读取及既有 account 锁之前，携带本次真实读取的完整性、身份覆盖范围和适用 target。NAV 的投影 dry-run、单行读回或不完整读取都不能作为关闭证明，也不能标记监控 baseline／后台新鲜度；它们的重复 issue 仍可用于原预览／门禁判断。没有监控 ready 或 enabled 时也不能静默跳过原重复校验。无需把整套字段校验改成全表查询，完整账户读取可证明该账户内未变身份的唯一性，移出账户或删除的成员仍需目标表观测确认。

**重复消失与观测恢复**：完整读取证明剩余记录身份唯一且字段可比较时，将其重复 case 按原外部修复语义关闭；若记录明确改身份，旧身份 case 用 superseded，不称原数据已经正确。缺失成员继续走第 5.3 节二次缺失确认：第一次缺失不关闭该组 case、不释放该身份的 gap；确认后被删除行的 case 由 case owner 以“记录已删除”原因归档／关闭，不能冒称字段修复。组成员删除、改身份或移出范围均保留证据和关联；仍有两行时保持或更新重复问题。

完成关闭证明后，已有行与其最后有效基线比较，变化回执注明 gap；期间首次见到、没有有效前值的新行只报首次观察的当前值。恢复时 case 处理结果与真实数量变化分别复用各自类型，同轮只结算一次。初始化仍遵循第 4.2 节：有重复则预览报错、apply 不写 ready 或静默发送修复回执；主动修复 case 的交接发生在 ready 后的真实 scan／后台观测中。

**再现与投递**：已解决的完全相同重复成员组再次出现时，复用已有 case 并记录 reopened，monitor 恢复 gap。沿用当前[发现回执 key](../src/app/holdings_workflow_service.py#L1290)只有 case_key、[重开路径](../src/app/operation_state/_holding_case_mixin.py#L327)不重发发现回执的规则：首次问题有发现通知，同成员再现仍在 case／status 可见，不声称会再发一次首次发现；新成员组合按新的 case 语义通知。初次创建、证据刷新、重开和关闭必须分别验证，不能把同轮消息去重误写成新问题永不建立。若之后要求每次完全相同问题再现都主动提醒，应单独确认现有 case 发现回执策略的改变，本轮不增加通知代次或新回执类型。

这些是拟新增的交接、证明覆盖及重复 case 生命周期约束，不声称当前代码已具备。观测回执只证明表内事实；复用现有重复 issue 的级别和后续门禁语义，不新增质量规则或允许绕过已有 blocker。

## 7. 状态、事务与回执契约

### 7.1 本地技术状态

在现有 `OperationStateStore` 增加独立的监控 feature schema version 和必要表／mixin，保留原 case、cash-flow、Futu inbox 和 outbox 兼容性。必要事实包括：

- scope：Base／table、配置账户、策略版本、epoch、初始化状态、dirty／completed generation、下次到期、尝试／完整扫描时间与错误。
- 行观测：record_id、最后有效快照、最近有效观测时间、当前状态／gap 原因、missing_candidate 的首次完整缺失时间。
- 变更记录：单调 `observation_seq`、冻结 before／after、类型、触发模式、观测区间及 receipt key；record_id 变化也保留对应关系。
- 交接证明：本轮完整性、所覆盖 target／身份集合、重复成员及关联 case key，供现有 case owner 作创建／关闭判定；case 事实与投递仍保存在原表，不复制一份到 monitor。

这些都是技术观测和通知历史，不替代飞书业务事实，不拿现有 `holdings_snapshot`（估值后业务快照）作监控基线。认识本功能的程序遇到较新且不支持的监控 schema 时须拒绝运行该能力，检查先于版本写回；关闭监控不删除新状态。v0.2.12 不认识新增版本键和回执类型，不能假定新增键会阻止它运行。后续升级／回滚必须让共享该数据库的 listener、手工命令及 receipt dispatcher 使用兼容版本；回滚先停止观测和派发、处理新类型待发项并保留状态备份，不能让旧 dispatcher 直接接管新 outbox。具体字段与函数拆分交实现，但初始化、版本检查、回滚边界和原能力兼容性需保留测试。

### 7.2 原子推进

一次成功观测在同一 SQLite 事务中完成：核对 scope（后台另核对本轮冻结 generation）→ 分配 observation_seq → 比较并更新有效基线或 gap／候选状态 → 用已规划且覆盖充分的重复证明调用现有 case owner 的事务 helper，创建／刷新／关闭对应 duplicate case → 保存变更 → 插入数量及 case 的冻结 operation receipts → 后台推进本轮代次／下次到期。不能先提交 gap／completed 再另发一次 case materialize，任一步失败需整体回滚；不确定或未覆盖的重复 case 保留原状态，不扫掉其他字段 case。手工结算不修改后台调度，见第 9 节。网络读取、姓名查询与消息发送均不放在此事务中。

receipt key 使用 `(scope epoch, observation_seq, group, part)`，不能只用 event_id 或 before／after 内容 hash。事件重放或事件／周期竞争读到相同当前值时不再生成变更；真正再次发生的往返变化有新序号。事务失败时基线、变更及 outbox 一起回滚；崩溃发生在提交之后、发送之前时，由 outbox 恢复派发，不能因未发消息而回滚观测值。

### 7.3 内容与发送

拟新增 operation receipt type `non_futu_holdings_changed`，在[类型白名单](../src/app/operation_state/_receipt_tx.py#L24)和 `HoldingsReceiptService` 中同时注册。复用现有 `render_receipt`、Agent 身份和目的地，Listener 仍仅接收事件。

普通数量变化按账户、券商、同一轮观测分组，每条最多 10 行；超过则分片并显示部分序号，不能静默截断。跨账户／身份变化单独一条显示完整旧、新身份，不伪装成两笔交易。

示例仅为格式示意，数据虚构：

```text
example
类型｜Holdings 持仓变动
状态｜已观察并记录
券商｜某券商
变化｜EXAMPLE：数量 100 → 120
观测｜上次有效时间 → 本次读取时间
依据｜飞书 holdings；表内变化，不代表成交确认
```

“记录已删除”“身份变更”“记录重建”“间断后观察”使用明确标签；不写“已买入”“已卖出”或“券商同步成功”。发送前后不修改源表、不调用效果确认或自动下单。

结算提交并释放观测锁后，可立即按新 receipt key 调用现有 dispatcher 尝试发送；没有新消息时不扫描历史队列。既有独立 receipt dispatcher 每 5 分钟兜底，见[部署文档](deploy-linux.md#启用定时任务)；后台正常首次发送与失败／重启后的恢复延迟分别展示，不新增投递服务。发送耗时单列并测量，不能借此把观测入队称为已送达。

沿用 outbox：明确未发送的 failed 可重试；发送结果不确定为 unknown，按现有人工 resolve 入口处理，不能盲目重发。既有 renderer 捕获网络异常也可能返回 unknown；不能把它当自动重试成功。回执失败不重新读取、写持仓或重建同一变化。外部发送与本地 sent 标记没有整体事务，不承诺恰好一次送达。

## 8. 与现金、富途及长桥的通知归属

| 来源／对象 | 自主数量变化回执 owner | 本功能行为 |
|---|---|---|
| CASH | 现有 cash-flow effects／现金基线扫描 | 排除；不改变非富途自动接受、富途待处理规则 |
| 富途股票／ETF | 现有 Futu sync／durable quantity receipt | 按 broker scope 排除，避免事件回流二次通知 |
| 非富途股票／基金的表内变化 | 本监控 | 统一按观测 before／after 发回执 |
| 字段缺失、跨行重复、校验修复、写入未知 | 现有 holdings case 回执 | 单行字段沿用原路径；monitor 以完整重复事实补齐同一 owner 的 case 交接，不另发第二套校验告警 |
| 未来长桥后台同步写入的非现金持仓 | 建议本监控统一负责自主数量回执 | 长桥保留源验证、同步证据和状态；不再另发同一变化的后台数量回执 |

本地独立长桥草案（`docs/longbridge-holdings-sync-design.md`，本提交未纳入）的 §8.2 仍建议由长桥 worker 发后台数量回执，与本设计的统一 owner 不同。长桥尚未实现或启用；其集成前需要协调通知归属，不能不作选择同时开启。本轮保留长桥设计原文，不将这里的建议当成已有契约。

建议采用“非富途表内数量变化统一由监控回执”的原因是：人工修改和同步写入落到同一事实表，现有事件契约不提供可信写入来源，按事件操作者、时间窗口或数值相似度抑制可能漏通知。首期不为此增加写入来源 registry 或跨服务 suppression 状态机。

长桥同步的单次显式命令仍可返回／发送执行汇总，但那是该次操作的结果，不再作为后台数量 owner。若以后希望所有消息合并成一条或只在长桥来源核验可信后通知，需要另行设计来源关联；本功能不能因看见飞书变化就声称券商核验通过。

## 9. 配置、入口和运行状态

以下是拟新增能力，不是当前可执行配置／命令：

```yaml
holdings:
  change_monitor:
    enabled: false
    accounts: [example]
    coalesce_seconds: 2
    reconcile_interval_seconds: 60
```

```bash
# 拟新增：只读飞书，预览基线、范围和数据问题；不落基线、不发消息
./pm holdings changes init --json
# 拟新增：只写本地技术基线，首次初始化静默
./pm holdings changes init --apply --confirm --json
# 拟新增：只读预览本次变化，不推进基线和新鲜度
./pm holdings changes scan --json
# 拟新增：显式进行一次观测结算，可入队／发送回执，不改飞书持仓
./pm holdings changes scan --apply --confirm --json
# 拟新增：纯本地状态，不访问飞书／券商
./pm holdings changes status --json
```

init／scan 默认预览；`--apply --confirm` 分别授权本次本地状态写入及其明确展示的通知副作用，不自动开启持续监控。手工 scan 复用比较和原子结算，单独记录 manual 尝试／完整观测时间，不 claim 或完成后台 generation、不改变后台退避／到期时间；下一轮后台仍须 fresh-read，已被手工结算的相同变化不会重复入队。手工删除检查只完成本次读取，不自动启动第二轮；缺失候选等待下一次显式 scan，或独立启用的后台复查。启用后台开关、既有 listener／订阅和通知接收人均按部署授权执行，本轮不修改任何配置。一次 scan 失败不隐式创建永久任务；后台已独立启用时仍按自己的事件／周期授权恢复。

status 至少显示 scope、策略版本、初始化状态、enabled、当前代次、manual／background 各自的上次尝试／完整观测时间、活跃行数、gap／缺失候选数、最近错误及 queued／failed／unknown 回执数。建议后台超过 120 秒没有完整成功观测标 stale；最新读取失败或有 gap 时显示 degraded。预览或手工成功不能把后台停止标为健康。状态是“表内观测／消息投递”状态，不代表券商、持仓真实性或 NAV 质量。

## 10. 实现边界与验证要求

建议一个完整 work unit。先确认范围和第 8 节通知 owner，再实现纯变化比较、OperationStateStore 事务及配置／基线／状态入口；随后接入现有事件 admission、后台周期和 renderer。代码、测试、文档共同形成可验收能力，不按文件数量拆成多个交付。

拟新增 `src/app/holding_change_monitor_service.py` 及对应 operation state mixin；纯比较规则放 `src/domain/`。修改点包括 holdings event admission、两个 listener 入口、operation schema／版本、回执类型和配置／CLI；完整读取能力归 Feishu repository／client。NFHC-01 另要求 `HoldingsWorkflowService` 的重复 case 规划、现有 holding case/event mixin 的覆盖证明及事务 helper、共享纯重复规则，以及手工 notify／NAV preflight 等会创建或关闭重复 case 的调用者；保留原门禁、字段校验和授权，补齐跨行事实来源，不能只改新 monitor 后假定旧入口安全。业务编排不放入 `skill_api.py`、SDK callback、renderer 或现金 effects。不改 Feishu 业务字段、OM 公共接口或长桥业务实现。

### 10.1 必须证明的行为

| 场景 | 验收断言 |
|---|---|
| 首次初始化／重复 init／进程重启 | 首次静默；重复不能覆盖；重启沿用有效基线并追赶净变化 |
| 股票、基金碎份额新增／增减／归零／恢复 | 精确比较规范数量，before／after、账户及券商正确；无浮点显示噪声 |
| 重复事件、乱序事件、事件／周期竞争 | 新鲜读取一次事实序列，同一变化一份 outbox；旧 payload 不覆盖新值 |
| 读取中收到新事件 | 结算冻结代次后仍有下一轮，不丢工作 |
| 100→120→100 | 跨两轮观察产生两份回执；一轮前已恢复则不编造中间编辑 |
| 删除、全表空、分页错误、权限错误 | 仅两次明确完整缺失结算删除；错误不被当空表 |
| 修改 account／broker／asset_id／type／currency | 行 ID 保持关联，按身份或计量变更描述，不形成虚假买卖 |
| 删除旧行再建相同业务身份／重复行 | 唯一替换明确标记；重复身份 gap，不求和或误发双份 |
| 新增重复行事件／事件丢失由周期发现 | 复用完整 raw rows，同事务形成 gap、既有 duplicate_identity case 及发现 outbox；不查 Futu，也不导入额外字段 case |
| 重复未修复时的相同扫描／单行事件／单行 notify／字段 apply 读回 | 原重复 case 保持打开；同成员只刷新，不重发发现；单行结果不能关闭或 supersede 跨行问题 |
| 重复成员数量或名称变化／成员新增／相同组修复后再现 | 前者不换 case；成员集变化按既有语义 supersede；相同组再现重开且 gap 可见，按既有规则不再发首次发现回执 |
| 删除一个重复成员／改身份／移出范围 | 缺失首次保留 gap 和 case，二次完整确认后正确关闭或归档；身份改变标 superseded，不伪造修复或交易 |
| 手工全量 notify／账户级 NAV preflight 与 monitor 交错 | 共享 target 锁及覆盖证明，旧读取不覆盖新 case 结果；原 duplicate blocker 和通知授权保持，单行 apply 无反向锁等待 |
| case/outbox 失败或进程在事务前后崩溃 | gap、duplicate case、数量变化、两类回执及 generation 同进退，重试不丢修复提示或重复数量通知 |
| 非法数字、缺字段、unknown broker、gap 后恢复 | 旧有效基线保留，记录存在不判删除；恢复标注观测间断 |
| 仅补名／成本／行业／标签／时间变化 | 不发数量回执，不干扰现有校验回执 |
| Futu observer 失败或补名失败 | 不阻断已接纳的非富途观测，且不调用 Futu／行情／FX |
| CASH／MMF／Futu 记录变化 | 不从本功能发数量回执，原链路行为不变 |
| 本地事务失败／崩溃 | 基线、变更和 outbox 同进退；提交后崩溃可继续投递 |
| 消息 failed／unknown／发送后崩溃 | 沿用现有重试／人工确认语义；不重新生成持仓变化 |
| 多进程／手工 scan 与后台并行 | 源读取前共用 target 锁，单一基线顺序，不用旧快照结算 |
| 禁用、缺基线、scope 变化、SQLite 丢失 | 不静默启用或重建，原 case／现金／Futu 能力保持兼容 |

新增 monitor domain／service／store／CLI 测试。既有回归至少覆盖：`tests/test_holding_event_inbox_service.py`、`tests/test_holdings_event_service.py`、`tests/test_holdings_validation.py`、`tests/test_holdings_workflow_service.py`、`tests/test_holdings_workflow_store.py`、`tests/test_holdings_nav_preflight_service.py`、`tests/test_operation_receipt_outbox_service.py`、`tests/test_operation_state_store.py`、`tests/test_feishu_bitable_event_adapter.py`、`tests/test_feishu_client.py`、`tests/test_cash_flow_effect_service.py` 和 `tests/test_futu_quantity_receipt.py`。使用真实适配器加模拟外部客户端验证调用范围；禁止调用的计数在抛错前记录，不能因异常被捕获误判未调用。

业务实现交付执行项目完整门禁，不以 CI 子集代替：

```bash
python3 -m pytest tests -q
python3 -X pycache_prefix=/tmp/pm_pycache -m compileall src skill_api.py scripts/pm.py scripts/publish_daily_report.py
ruff check src skill_api.py scripts/pm.py scripts/publish_daily_report.py
python -m scripts.export_om_openapi --check
git diff --check
```

后续真实联调需单独授权：先预览、静默初始化，再验证人工新增、数量编辑、删行、断线追赶与实际回执；记录接收、扫描、事务入队和发送各自耗时。只观察已有正常编辑或明确授权的测试记录，不为验收擅自改生产持仓。行为落地时更新 README、listener runbook、服务／运行文档和产品页；本轮不把草案写成现有产品能力。

## 11. 待确认信息与风险去向

| 信息／风险 | 建议与处理 | 分类及 owner／destination |
|---|---|---|
| 监控账户及资产范围 | 显式账户，股票／非 MMF 基金，数量与身份变更；成本单独变化静默 | requiring new issue or explicit user decision；用户确认目标范围 |
| 时效和周期 | 事件到入队 10 秒、60 秒核对、120 秒 stale，送达单独报告 | requiring new issue or explicit user decision；用户确认，实施者测量 |
| 与未来长桥回执 owner 冲突 | 建议本监控统一负责非富途后台数量回执，集成前协调长桥草案 §8.2 | requiring new issue or explicit user decision；用户确认通知归属；长桥设计维护者落实联动 |
| 观测无法还原全部中间编辑 | 明确净变化与观测区间，不宣称逐笔完整日志 | assigned to later work unit；本功能实现者，回执措辞及验收 |
| 飞书分页非事务快照与删除误判 | 完整性校验、缺失候选二次确认；不承诺上游原子性 | assigned to later work unit；实现者，仓库读取与故障验证 |
| 初始化、配置变更或本地基线丢失 | 不静默重建，预览和显式确认；scope mismatch 可见 | assigned to later work unit；实现者，初始化／运维契约 |
| 消息可能不确定或重复 | 复用 outbox 的 failed／unknown 语义，不声称 exactly-once | assigned to later work unit；实现者与运维，投递状态和 resolve 流程 |
| 全表观测耗时及频率 | 先按当前个人／固定协作者规模实现，测量后才优化 | assigned to later work unit；实现者，规模和延迟验收 |
| 相同重复组关闭后再现不重发首次发现 | 沿用现有 case reopening／receipt key 规则，status 与 case 可见；若要求每次再现主动提醒，先确认既有发现通知策略变化 | requiring new issue or explicit user decision；用户确认提醒强度，现有 case owner 后续落实 |
| 证券真实来源、长桥核验及正式 NAV 质量 | 本功能不证明，保留其他独立链路与门禁 | assigned to later work unit；用户作为产品 owner 决定独立需求 |

当前 gate 为 `goal confirmation`：本设计是可评审草案，未把建议阈值或长桥 owner 取舍当成用户已确认。下一入口是确认监控账户／范围、通知归属和时效，再形成具体实现计划并执行适用的设计审核。本轮到设计交付为止。

## 12. 审核修订与本轮检查

[首轮审核](reviews/plan-review-20261106-091237.md)对第 1 版结论为 `fail`：NFHC-01 确认全表重复观测与现有 case owner 的数据交接缺失。该结论仍对应[第 1 版原文快照](reviews/non-futu-holdings-change-monitor-design-reviewed-20261106-091237.md)，SHA-256 为 `e752eba84e89c0ff496dba9c834a681f0f2ed5cc9c0691bda87e293cedfc8f93`，不把旧审核改写为新版已通过。

| 审核项与受影响面 | 本版修订 | 验证去向 |
|---|---|---|
| NFHC-01：重复事实没有 case 生产者 | 第 5.1／6.1 节明确完整 raw rows → workflow 纯规划 → 原有 duplicate case／发现回执；第 7.2 节将 gap、case 与 outbox 原子结算 | 第 10.1 节新增重复事件、周期补漏、事务失败断言 |
| 单行事件可能误关重复 case | 第 6.1 节明确单行证明不能关闭跨行问题，覆盖 event、单行 notify、apply／Agent 补名读回；完整 notify／preflight 复用 target 锁和覆盖证明，保留原门禁 | 第 10 节列共享 owner／调用者与锁顺序、交错及旧能力回归 |
| 去重、修复及再现 | 第 6.1 节复用 case key／supersede／reopen；缺失两次确认后再释放 gap；相同组再现不承诺重发首次发现 | 第 10.1／11 节明确不同情形和提醒强度边界，不扩建告警机制 |

本轮为设计修订与受影响面复核，不访问生产、不运行服务。相关代码、测试断言仅静态阅读；文档链接／格式和版本验证记录在本次复核文件。账户／资产范围、建议阈值和长桥通知 owner 仍待确认；拟新增命令与行为不代表已实现。

未运行 pytest、compileall、Ruff 或公共 API 导出：本轮只有开发文档变化，没有业务实现或公共契约改动。长桥设计保持已评审的 `7446e4ec21f03d2711e832df626888df28ecb8d56add87985378c1ec45253350` 版本。
