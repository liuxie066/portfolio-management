# 飞书多维表读写与校验契约修复方案

## 1. 目标与状态

- 目标：修复 repo-review-20260801-210200.md 中 29 个未解决问题，使 holdings、cash_flow、nav_history、holdings_snapshot、transactions、compensation_tasks 的读取、校验、计算、写入和写后证明使用一致契约。
- 当前状态：设计完成，尚未实施。
- 设计基线：审计 checkout 为 main@2a1ae72，origin/main@59625b0；本地 main 落后 33 个提交。正式实施必须从执行当时重新拉取并确认过的 origin/main 建立隔离分支或 worktree，不能直接在当前落后且有未跟踪审计文档的 main 上开发。
- 成功标准：29 个未解决 finding 均有唯一归属、对应回归测试和可观察完成证据；两个已在 origin/main 修复的问题只做基线验证，不重复实现。

本方案只授权源码与测试设计，不授权：

- 查询或写入真实飞书数据；
- 修复历史行、删除快照行或清空线上字段；
- 创建分支、提交、推送、合并；
- 修改版本、发布、部署或升级运行环境。

## 2. 设计原则

### 2.1 四段式契约

所有表统一采用以下路径：

1. Raw read：保留飞书实际出现、缺失或为空的字段，不先填业务默认值。
2. Domain validation：按表和用途验证完整业务键、字段类型、有限数、枚举、跨字段公式及所有权。
3. Canonical calculation：只使用通过验证的不可变事实对象计算。
4. Mutation and proof：根据字段所有权生成最小写入或 absolute target；成功只由 fresh readback、业务键和字段摘要证明。

缓存仅用于加速，不是官方事实来源。任何会形成 NAV、同步收据、补偿 resolved 或 repair complete 的路径都不能用乐观缓存代替远端回读。

### 2.2 字段所有权

| 类别 | 含义 | 写入规则 |
|---|---|---|
| manual | 用户或外部权威源维护 | 无明确授权不得覆盖；原始缺失不得由模型默认值掩盖 |
| system-derived | 系统由明确公式生成 | 输入完整且公式校验通过后可创建或纠正；写后回读 |
| observed | broker 或其他权威源观测 | 只在对应 source-of-truth 范围内覆盖 |
| compatibility | 为旧数据读取保留 | 只读兼容，不继续制造旧格式 |
| reserved | 尚未启用 | 不暴露可写 API，不纳入当前运行时正确性判断 |

asset_class 固定表示底层资产或经济敞口地域。只有 A 股、CASH、MMF 可由当前证据确定；港股、美股、ETF、基金不能按币种、上市地或渠道自动分类，已有人工值必须保留。

### 2.3 空值、缺失和值

- Missing：请求中未出现字段，表示“不修改”。
- Null：只允许在明确的 patch/replace clear allowlist 中表示“清空远端单元格”。
- Value：经过 canonicalization 和验证的目标值。
- 读取时必须保留 missing 与 null 的差异；不得把 missing quantity、currency、asset_type、flow_type 等转换成零或默认枚举后再校验。
- 普通 patch 默认只发送发生变化的字段；absolute replace 必须表达完整目标，包括允许清空的 null 字段。

### 2.4 一致性与并发边界

- Feishu 跨表写入不是事务。每个多阶段写入必须先落 durable target/operation plan，再执行写入，再 fresh readback。
- 同机锁只限制同一进程域，不宣称跨主机或外部人工编辑原子性。
- 每个正式 NAV 将 cash-flow dataset fingerprint、读取时间、日期窗口和算法版本写入 details，明确其 as-of 事实。
- source rollback、release、production apply 和历史数据 repair 是四个独立授权边界。

### 2.5 分层唯一真源

本方案采用“一类事实一个唯一真源”，不建立同时承载 wire schema、业务公式和运行数据的万能配置：

1. 飞书结构契约唯一真源：src/feishu/contracts/ 下的不可变 typed Python registry。它定义表/字段、type、ui_type、编码、schema-required、operation row-required、所有权、clearability、业务键及允许的 select options。
2. 领域计算唯一真源：asset_class、cash-flow derivation、NAV invariants、snapshot replay、业务键和 dedup 各有唯一领域函数；writer、repair、backfill 和 readback 不复制公式。
3. 运行事实唯一真源：official workflow 消费一次 fresh read 产生的 account/run/date-bound immutable dataset；cache、model default 和 requested payload 都不是完成证据。

docs/schema.md 是 registry 的生成投影；schema checker 和 migration inspection 直接读取 registry，不再解析 Markdown 作为运行时真源。live Feishu schema 是被比较的外部状态，不得自动反向修改 registry。

## 3. 已锁定的业务语义

### 3.1 holdings

- 业务键唯一为 (asset_id, account, broker)，三者均 trim 后非空。
- 所有 mutation 必须提供 broker；兼容读取省略 broker 时，零候选返回 None，一个候选返回该行，多个候选抛 AmbiguousHoldingIdentity。
- quantity 必须是有限数。CASH 允许负数；当前未建模 short 的 STOCK、ETF、基金、MMF 不允许负数。历史负数进入审计阻断，不自动改写。
- Futu STOCK/ETF 的 quantity 和 avg_cost 是 observed；写 diff 前先验证整个 provider slice。missing、invalid 和 explicit zero 是三种状态。
- Futu 现有行保留 name、currency 和人工 metadata；只有新行在 provider 明确给出合法字段时使用 observed metadata。
- avg_cost、asset_class、industry 是 absolute replace 可清空字段；tag 只有目标明确携带时才修改或清空。
- Holding model 不能同时表达“未指定”和“明确清空”，因此 mutation 不再直接复用读取 model：field patch 使用带 UNSET sentinel 的 HoldingPatch，absolute target 使用完整 HoldingTarget。serializer 只接收这两类已验证 mutation object。

### 3.2 cash_flow

- 手工最小事实：flow_date、account、broker、amount、currency、remark。
- account、broker trim 后必须非空；currency 必须受支持；amount 必须有限且不等于零。
- flow_type 是 amount sign 的 system-derived 值：正数 DEPOSIT，负数 WITHDRAW。冲突由 reconcile 提出并纠正，但在纠正并回读前 effect/NAV 必须阻断。
- CNY exchange_rate 固定为 1；外币必须有与 flow_date 相同日期的可追溯汇率证据。
- cny_amount = round(amount × exchange_rate, 2)，计算使用 Decimal。
- dedup_key 是 canonical manual facts 和版本化规则生成的系统字段；同一 account fresh slice 中出现重复组时，组内所有行均阻断，不“保留第一条”。
- missing flow_date 的行不进入 daily、monthly、yearly 或 cumulative 任一财务聚合。

### 3.3 nav_history

- 飞书 stock_value 的兼容语义固定为“非现金总值，包含 fund_value”；内部变量改名 non_cash_value，暂不改飞书列名。
- cn_stock_value、us_stock_value、hk_stock_value 同样是历史列名；其 canonical 语义为按 asset_class 汇总的中国、美国、香港非现金经济敞口，可能包含基金或 ETF。内部变量使用 cn/us/hk_exposure_value。
- total_value = stock_value + cash_value。
- fund_value 是 stock_value 的子集，不得再次加到 total_value。
- nav、shares、share_change、weights、PnL 必须由最终持久字段共同通过一次末端不变量校验。
- cash_flow 飞书列固定表示 NAV date 当日现金流。
- 上一 NAV 日之后至本 NAV 日的 gap flow 写入 versioned details.cash_flow_basis，不新增飞书列。内容至少包括 basis_version、previous_nav_date、window_start_exclusive、window_end_inclusive、daily_cash_flow、gap_cash_flow、dataset_fingerprint 和 fetched_at。
- repair 不允许从有损索引对象整行 replace；只可从 fresh full row 生成字段级 patch。

### 3.4 holdings_snapshot

- 业务键为 (as_of, account, asset_id, broker)，同一写入批次必须属于一个 account/as_of slice 且键唯一。
- 零数量行不进入正式 NAV holdings snapshot；其排除数量和 key digest 写入 NAV details provenance。
- quantity 沿用当前 8 位数量规范；price 和 cny_price 不再按货币分量化。它们以 Decimal(str(source_value)) 保留输入精度，直到 wire serialization 才转换为 Feishu Number。
- market_value_cny 由最终准备持久化的 quantity 与 cny_price 重新计算并量化到 0.01。不得先用高精度估值、再降低 unit price 精度。
- 远端回读后再次执行 round(quantity × cny_price, 2) = market_value_cny。若 Feishu 数值规范化导致不等式，快照和 NAV finality 保持 partial，不宣告完成。
- 正式写入是 exact-set replace：create、update、clear、delete 后，远端 slice 必须与目标 key set 和 versioned full-row digest 完全一致。

### 3.5 transactions、compensation_tasks、schema_version

- transactions 继续作为 legacy read-only archive。移除或显式拒绝公共 add/delete；reader 不再制造 BUY、CNY、0 等默认值。未来恢复交易账本必须另立迁移方案。
- request_id 作用域问题随 writer 封锁退出当前活动路径；保留的查询必须以 account + request_id 为键并校验返回账户，防止未来误用。
- compensation_tasks 是本地 append-only event log 的 best-effort current-state mirror，不是权威状态。每次本地 fold 先 fsync，再按 task_id 更新或创建镜像；镜像失败只记录 warning/metric，不回滚本地状态。
- schema_version 在本轮定义为 reserved optional table；docs 和 strict schema checker 不得把它描述成已生效的迁移账本。若未来启用，需另行实现 marker CRUD、唯一性和启动 gate。
- price_cache 保持 local-only；删除 dormant Feishu 注册与转换分支，避免被误认为可用远端表。

### 3.6 日期与 select 类型

- cash_flow.flow_date 与 nav_history.date 的 canonical live type 为 Date。
- holdings.created_at/updated_at 是应用写入的 Text，格式固定 YYYY/MM/DD；读取只为兼容旧行接受 YYYY-MM-DD HH:MM:SS。
- transactions.tx_date 与 holdings_snapshot.as_of 在本轮保持 Text YYYY-MM-DD，避免隐式 live schema migration。若 read-only live audit 发现实际字段为 Date，立即停止并另立 schema migration，不让 writer 猜测或按配置静默切换。
- cash_flow.updated_at 与 nav_history.updated_at 是可选 external-observed 字段，不参与业务正确性、cache authority 或写后证明；只有 live automation 存在时才读取。
- compensation_tasks.created_at/updated_at/resolved_at 由应用写入，编码与 live schema 在 S1 strict check 中锁定。
- holdings.asset_type、asset_class、industry 与 cash_flow.flow_type 是 single-select 契约；tag 是 JSON text，不是 multi-select。
- 只读 legacy transactions 的 tx_type 与 asset_type 按 2026-08-01 live evidence 保持 Text，不做 schema migration。
- compensation_tasks 未配置；其 operation_type/status 仅在未来配置该 optional mirror 时要求 single-select。

## 4. 目标数据流

    Feishu raw records
           |
           v
    raw field decoder ---- schema/type errors ----> fail closed
           |
           v
    table-specific validator
           |
           +---- validation/duplicate/authority errors ----> blocker receipt
           |
           v
    immutable canonical facts
           |
           +---- calculations ----> NAV / holding target / snapshot target
           |
           v
    durable operation target
           |
           v
    minimal patch or exact-set mutation
           |
           v
    fresh full readback + business key + digest
           |
           +---- mismatch ----> partial write + compensation
           |
           v
    trusted / resolved / final

## 5. 分阶段实施

### S0：隔离基线与契约锁定

**范围**

- 执行时重新 fetch，记录 origin/main commit。
- 从最新 origin/main 创建隔离分支或 worktree；保留当前 main 及所有既有未跟踪 design/review artifact。
- 重新运行当前完整测试并确认 F27、F28 已包含在基线。
- 已取得 read-only live-schema 查询授权，并把去除 token/table identity 的 field name、type、ui_type 和 select options 写入 docs/gateflow/feishu-bitable-contract-repair/live-schema-baseline.md；未读取业务记录、未写数据。
- live evidence 确认核心日期、snapshot Text date 和核心 select 类型；同时确认 legacy transactions 使用 Text，compensation_tasks/schema_version 未配置，以及 holdings select option 与 domain enum 存在漂移。S1 必须把这些差异建模为显式 contract，不做 live migration。

**完成门槛**

- git diff 仅包含本 work unit 的文档和测试准备。
- F27 outbound subscription request map 与 F28 tag JSON 回归通过。
- 29 个未解决 finding 与以下 work unit 的映射无重复、无遗漏。
- live schema 证据已保存且不含 app/table token；后续 repeat check 使用配置 identity 但 artifact 只输出 redacted fingerprint。

### S1：共用 Feishu 边界与 strict schema

**归属 finding**：F14、F23、F26。S1 提供 F12 所需的共用 batch 基础设施，F12 的关闭责任在 S5。

**代码范围**

- src/config.py
- src/feishu_client.py
- src/feishu_storage.py
- scripts/migrate_schema.py
- 两个 Bitable event adapter
- docs/schema.md
- 对应 config/client/storage/schema tests

**设计**

1. 新增唯一 table-ref parser，要求恰好两个 trim 后非空 segment，返回 app_token/table_id；record client、event adapter 和 deployment validation 全部调用它。
2. 引入 FeishuRecordNotFoundError。read_record 只把明确 404/not-found 转成该异常；storage 的 optional read 只捕获它并返回 None，403、timeout、schema 和 malformed response 原样上抛。
3. 新增 src/feishu/contracts/ typed registry 作为结构契约唯一真源，并区分“live table 必须存在的 schema field”与“某种 operation 的 row required field”，不再从 docs 的 Required fields 标题直接推导 create 规则。建立显式 RecordWriteContract(table, operation)：
   - repository/domain 先完成业务对象验证；
   - client 的 single create 与 batch create 对同一 operation 每行复用 validate_required_record_fields；
   - batch 错误必须包含 row index 和可安全展示的业务键；
   - transactions writer 无 active contract，price_cache 无 remote contract。
4. docs/schema.md 由 registry 生成；生成器带 --check，CI/本地 gate 要求零 drift。schema 文档不再用斜杠同时表示“可选 type”与日期格式：
   - text、number、date、single_select、multi_select 是明确 wire families；
   - json_text、date_text、datetime_text 是存储编码注解，不伪装为 Feishu field type；
   - alternative type 必须使用显式 allowed_types 列表。
5. strict checker 直接消费 registry，精确比较 field type、ui_type 和 contract-declared select options；配置了的 optional table/field 一旦存在但类型错误必须阻断。未配置的 optional table可跳过，但需显示 skipped，而不是 passed。
6. 统一 filter 条件连接 helper；先用 request-map 单测锁住 wire，再在部署前做 read-only live canary。
7. 删除 price_cache 的远端 REQUIRED_FIELDS、table registration 和 wire conversion。
8. holdings write contract 只允许 registry 中已批准的 live select options；domain enum 可保留价格/兼容读取所需的更宽集合，但 writer 对未批准值 fail closed。Industry 增加 live AI 值；不自动删除 domain-only legacy enum。

**测试**

- table ref 的空白、少段、多段、空 segment 参数化测试。
- 404 与 403/timeout/malformed 的错误分类。
- single/batch create 对同一 table/operation 的 row required 字段结果相同；schema-required 与 row-required 不混用。
- single_select 与 multi_select、json_text、YYYY/MM/DD 注解不会被错误拆分。
- configured optional mismatch 在 strict 模式失败；absent optional 显式 skipped。
- registry → docs 生成和 --check；registry → repository projection/serializer coverage。
- holdings asset_type/industry domain-only 与 live-only option drift 的 read/write tests。

**完成门槛**

- 所有 Feishu 入口只有一个 parser、一个 required-row validator 和一套 strict type 判定。
- 不发起 live write。

### S2：holdings 与 Futu 权威事实

**依赖**：S1。

**归属 finding**：F04、F05、F07、F08、F09、F25。

**代码范围**

- src/feishu/repositories/holdings_repository.py
- src/app/futu_balance_sync_service.py
- src/app/futu_sync_reconciler.py
- src/app/cash_service.py
- src/app/compensation_service.py
- src/app/holdings_validation.py
- holdings/Futu/compensation tests

**设计**

1. provider parser 返回 valid(value)、missing、invalid 三态。对整个 Futu account/profile slice 先完成 quantity、average_cost、currency、identity 校验；任一 authoritative position invalid 时，diff 和所有 Feishu mutation 均不得开始。
2. 写边界构造 canonical Holding copy，后续 payload、cache key、返回对象和 readback comparison 都使用该副本。
3. mutation API 强制 broker；迁移 CashService、cash-flow effect 和 compensation 调用点。省略 broker 的兼容 read 只允许唯一候选。
4. Futu new row 复用 holdings_validation 的同一 asset_class authority function。US/HK/ETF/基金无 instrument evidence 时保持 None；已有人工值不覆盖。
5. replace_holding 采用字段状态映射：
   - 未指定：不改；
   - 明确目标值：更新；
   - avg_cost/asset_class/industry 的明确 None：发送 null 清空；
   - tag 仅在 target 显式携带时允许清空。
   Mutation object 同时携带 owned_fields、base_record_id 和 fresh base digest。system workflow 只能设置自己拥有的字段；不拥有的 manual 字段必须从同一次 fresh base 原样 carry forward，不能由 Pydantic/default None 生成 clear。
6. replace/bulk 写后均调用 get_holdings_fresh(account)，再按完整业务键和 owned fields 比较。FutuSyncReconciler 每次 retry 也 fresh read，不读 optimistic cache。
7. compensation 只有在 fresh readback 匹配 absolute target 后才能 RESOLVED；否则保持 FAILED/PENDING 并附逐字段差异。
8. 历史 noncanonical identity、负非现金数量、重复业务键只生成 read-only audit case，不自动改名、合并或清零。

**故障注入与测试**

- 同一 provider slice 一条合法、一条 NaN/N/A qty：零飞书写入。
- explicit zero 仍生成合法关闭目标并清 avg_cost。
- 双 broker 省略 broker：明确歧义，零更新/删除。
- optimistic cache 与远端不一致：receipt 不得 trusted。
- HK ETF、US-listed China ETF、新 Futu 行、人工 asset_class 保留。
- 空白 identity、usd currency 经 canonical copy 后 payload/cache/readback 一致。
- null clear 的 request-map 和 fresh readback mismatch。
- 从 partial/default model 构造 absolute target：在 mutation 前拒绝；manual metadata 不得被隐式清空。

**完成门槛**

- 任一 invalid authoritative row 无部分 holdings mutation。
- trusted/resolved 收据均可指向 fresh readback evidence。

### S3：cash_flow canonical dataset

**依赖**：S1。

**归属 finding**：F06、F15、F16、F17、F21、F22、F29、F30、F31。

**代码范围**

- src/feishu/repositories/cash_flow_repository.py
- src/app/cash_flow_summary_service.py
- src/app/cash_flow_effect_service.py
- src/app/cash_flow_event_completion_service.py
- src/app/daily_nav_job_service.py
- src/app/account_nav_recorder_service.py
- src/app/nav_record_service.py
- scripts/pm.py
- cash-flow/NAV integration tests

**数据对象**

新增不可变 CashFlowDatasetSnapshot：

- account、nav_date、run_id、fetched_at、source_record_count；
- full raw rows 与 validated completed rows；
- blockers、duplicate_groups；
- daily/monthly/yearly/cumulative Decimal aggregates；
- canonical full-row fingerprint；
- earliest/latest flow date、contract_version、local FX confirmation fingerprint 与 cash-flow effect-store revision/gate result。

只有 blockers 为空且所有 system-derived 字段都由 fresh observed row 验证后，snapshot 才可标记 complete。
fingerprint 按 record_id 稳定排序，采用版本化 canonical JSON，并显式编码 Missing 与 Null；不能依赖 API 返回顺序或 Python 默认字符串化。

**设计**

S3 分成两个连续、各自可 review 的子切片；S3A 先修复行契约和 reconcile，S3B 再接入官方 NAV，不允许在 S3A 尚未通过时提前改 NAV orchestration。

**S3A：row contract、reconcile 与 duplicate gate**

1. 建立两层 validator：
   - ManualCashFlowFacts 校验手工五个必填字段、broker、有限非零 amount；
   - CompletedCashFlowFacts 再校验 flow_type、exchange_rate、cny_amount、dedup_key、source 及公式。
2. reconcile 的执行顺序固定为 fresh full scan → manual validation → duplicate audit → generated-field plan → 可选写入 → fresh readback → completed validation → dataset snapshot。
3. duplicate group 使用由 raw manual facts 重新计算的 expected dedup_key 分组，不能信任 observed dedup_key；observed value 仅用于判断是否需要 system patch。
4. CASH_FLOW_PROJECTION_FIELDS 覆盖所有 raw digest/readback 字段，包括 remark、source、dedup_key；exact read 保留远端真实 dedup_key，CLI 不得用本地重算值冒充回读。
5. direct add 在写前必须产出 CompletedCashFlowFacts。外币缺汇率/cny_amount、date mismatch、NaN/Inf/0、空白 identity 均零写入、零缓存更新。
6. manual FX apply 必须先验证 rate_date = flow_date，再写；写后逐字段 fresh readback 后才保存 confirmation。
7. 外币 completed validation 同时读取本地 FX confirmation，以 record_id、observed generated-field fingerprint 和 flow_date 绑定；只有二者匹配才写入 dataset 的 local FX confirmation fingerprint。
8. 新增只读 pm cash-flow duplicates --json，输出 canonical dedup group、record_ids 和阻断原因；修复/删除重复行另需显式数据修复授权。
9. flow_type 冲突由 reconcile 作为 system-owned patch 纠正；effect/NAV 在 fresh readback 证明前 fail closed。
10. dataset 的时间范围固定：
   - 聚合只包含 start_year-01-01 至 nav_date；
   - future-dated rows 保留在 raw audit 中但不计入金额，只有与 in-scope row 构成冲突时才阻断当前 NAV；
   - missing/invalid flow_date 因无法证明在范围外，必须阻断；
   - duplicate group 任一成员在范围内时整组阻断，全部在未来时只报告 warning。

**S3B：run-scoped dataset 与 NAV handoff**

11. 仅顶层 application orchestration 可以构建 dataset。daily NAV precheck 返回 AccountNavPrecheck，其中携带同一个 CashFlowDatasetSnapshot；它通过 AccountNavRecorderService 传到 NavRecordService 和 CashFlowSummaryService。
12. CashFlowEffectService 必须从 dataset 的同一组 raw/manual facts 计算 source revision，并将 effect-store revision 和 gate result 放回 AccountNavPrecheck；不得为 NAV gate 再独立扫描飞书。source revision 不匹配、unresolved effect 或 corrupt/uninitialized store 均使 dataset incomplete。
13. 下游调用 assert_compatible(account, nav_date, run_id)，正式 persist 缺 dataset、dataset incomplete 或上下文不匹配时 fail closed；下游不得自行 reconcile 或回读 storage。非持久化 repair/backfill 也必须由其顶层入口显式提供 ledger dataset。
14. 正式计算不再次调用 reconcile，也不读取全局 aggregate cache。对外非官方 summary API 可由独立 application entry point 加载自己的 fresh snapshot。
15. 旧 aggregate cache 仅保留 UI 加速。任一 create/update/delete、账户移动或 external-event completion 都清理 old/new account 的 memory 和 disk cache。
16. CashFlowDatasetSnapshot 放在不依赖 Feishu client 或 app service 的纯 contracts/models 层；repository 不导入 app 类型，CashFlowSummaryService 只消费 dataset interface，避免 repository → app 反向依赖。

**故障注入与测试**

- 已预载 100 后外部 add/edit/delete，precheck dataset 与 fresh rows 一致。
- 缺 date 行不进入任何聚合且形成 blocker。
- 同一 dedup 的两行均阻断。
- NaN、Inf、0、空白 account/broker、未知 currency。
- foreign rate_date mismatch：零更新，零 confirmation。
- batch update 返回成功但 readback 缺 dedup_key：snapshot incomplete。
- precheck 后旧缓存含不同值：NAV 仍只消费传入 dataset。
- 进程重启、disk cache 存在时，官方路径仍 fresh scan。
- effect store revision 对应另一 source fingerprint：NAV 阻断，不能用第二次 scan 覆盖差异。
- future valid/incomplete row、missing-date row、跨 as-of duplicate group 的范围测试。

**完成门槛**

- 一个 NAV account run 只有一个 cash-flow dataset fingerprint。
- precheck、share calculation、PnL 和 details 均引用同一 snapshot。
- 任何 invalid/duplicate/incomplete row 均阻断全部官方聚合。

### S4：nav_history 完整事实与 repair

**依赖**：S1、S3B。

**归属 finding**：F01、F02、F03、F13、F18。

**代码范围**

- src/feishu/repositories/nav_history_repository.py
- src/app/nav_record_service.py
- src/app/account_service.py
- src/app/portfolio_read_service.py
- src/app/report_query_service.py
- src/app/reporting_service.py
- src/domain/nav_calculator.py
- src/maintenance/nav_history_repair/patch.py
- src/maintenance/nav_history_repair/backfill.py
- src/app/audit_service.py
- NAV API/cache/repair tests

**设计**

1. 明确两个读取接口：
   - get_nav_metrics_index 只供内部日期查找，字段集合显式命名；
   - get_nav_history/get_latest_nav/get_nav_on_date 返回完整 canonical NAVHistory。
2. memory/disk cache 若服务公共读取，必须保存全部 canonical 字段；进程重启回归逐字段相等。不得只修 details 而继续丢失分解字段。
3. repair patch 从 fresh full row 构造 field-level diff，只发送实际变化字段。rollback 保存每个被修改字段的 before FieldState（Missing/Null/Value），不能把“原本无字段”和“空单元格”压成同一个 None。
4. persisted/runtime mapping 固定：
   - runtime valuation.stock + valuation.fund → persisted stock_value；
   - persisted stock_value 不再作为纯股票重新进入 runtime；
   - fund_value 只用于子集和区域展示。
5. backfill/patch 只允许修补 fresh full read 后唯一存在的 legacy 行的派生字段/details：持久化的 identity、total/cash/stock/fund/region 等基准事实只读，输入与远端基准不一致即阻断。缺失日期、upsert-create 或 base-field replacement 均零写并要求另立 historical reconstruction work unit；apply/rollback 只调用 field-level patch，不再 full-row write。
6. cash_flow 列只写 daily；gap 只进入 details.cash_flow_basis。legacy row 缺 basis 时，repair 必须用 S3 dataset 从 ledger 重算，不能从 cash_flow 单列猜测。
7. batch update 与 batch create 分阶段记录 confirmed IDs。create 失败时保留已成功 update 的 FeishuBatchWriteError stage 事实，立即失效/重建 cache，并把 confirmed/unknown/failed scopes 写入 durable receipt。
8. 不新增或重命名飞书 stock_value/region 列；全仓 inventory 所有 stock_value + fund_value 和 cn/us/hk_stock_value consumer。API、account summary、report query、reporting 和 repair 均改为 non-cash/exposure 语义，避免 writer 修好而 reader 继续重复基金。

**测试**

- 完整 20+ 字段行经 fresh、memory、disk restart、GET、audit 全字段恒等。
- repair 某一字段时其他字段未出现在 payload 或保持原值。
- stock=800 且 fund=100 子集的 derived-only backfill 不重复计算；不存在日期、基准漂移和 upsert-create 均零写。
- derived-only apply/readback/rollback 保留全部未触碰字段与 legacy snapshot evidence，不升级为 v2 complete。
- account/report/API 对 persisted stock_value=800、fund_value=100 返回 non_cash=800，不返回 900；region exposure 可以包含 fund/ETF。
- 周五 NAV、周末入金、周一 daily=0/gap>0。
- update 成功、create 失败：返回 confirmed update IDs、cache 不乐观、receipt partial。
- legacy details 缺 cash_flow_basis：无 ledger 证据则阻断 repair。

**完成门槛**

- public NAV read 无有损投影。
- 所有 writer/repair 对相同输入满足同一组不变量。

### S5：holdings_snapshot 可回放 exact set

**依赖**：S1、S2、S4。

**归属 finding**：F10、F11、F12；复用 S1 的 RecordWriteContract 基础设施。

**代码范围**

- src/snapshot_models.py
- src/app/portfolio_read_service.py
- src/app/valuation_service.py
- src/app/snapshot_service.py
- src/feishu/repositories/snapshots_repository.py
- src/app/compensation_service.py
- NAV snapshot finality/digest tests

**设计**

S5 再分为 S5A“可回放行模型”和 S5B“exact-set 状态机”。S5A 的模型和纯函数测试通过后才能实现 destructive-capable 的 S5B。

1. PortfolioReadService/ValuationService 生成单一不可变 NormalizedValuationSnapshot，包含用于 NAV 的 totals 和用于 holdings_snapshot 的 rows。quantity 与 unit price 只规范化一次；NAV record、snapshot target 和 digest 全部消费同一对象。
2. persisted row 的 account、asset_id、broker 必须 trim 后非空；quantity、price、cny_price、market_value_cny、dedup_key 均 required 且 finite。零数量行在目标构建阶段排除。
3. full-row digest v2 覆盖业务键和所有持久字段：asset_name、quantity、currency、price、cny_price、market_value_cny、avg_cost、source、remark、dedup_key；字段顺序和 Decimal serialization 固定。
4. exact-set plan：
   - fresh list account/as_of 全 slice；
   - 检测远端重复业务键并阻断；
   - 计算 create、field-level update/clear、delete；
   - 空 desired set 仍是有效目标，但只有显式 overwrite/compensation authority 才允许删除现有 slice。
5. 正常首次写入禁止覆盖已存在 slice；同日 rewrite/repair 必须显式 overwrite_existing + confirm。
6. 非 dry-run 在 NAV record 首次写入之前，先把 snapshot exact target、before set、plan digest、run_id 和 nav target identity 作为 HOLDINGS_SNAPSHOT_TARGET_SET local-prepared/PENDING 事件 fsync 到现有 compensation log。正常成功前不镜像到飞书，避免每次 NAV 制造瞬时补偿行；只有发生 partial、失败或重启发现未完成 target 时才标记 mirror-eligible。NAV 写抛异常时必须按 account/date fresh read：只有证明 NAV 未创建时才记录“无需重放”；确认目标 NAV 已存在则继续 snapshot recovery；无法判定时保持 PENDING。NAV 写成功后即使进程崩溃，PENDING target 仍可恢复。
7. 写入顺序为 NAV → snapshot create/update/clear → delete obsolete rows；每个阶段记录 confirmed IDs。任何阶段失败都保持 partial，不能 patch NAV finality complete。
8. 最后 fresh list，验证唯一 key set、字段值、逐行 replay 公式和 digest v2；只有完全匹配才把 NAV details snapshot 状态设 complete，并把预写 PENDING target 标为 RESOLVED。
9. compensation 重试执行同一 exact-set engine，不再调用盲 upsert；resolved 也必须有 fresh readback digest。

**故障注入与测试**

- 12.345 × 10.123 的落表字段可重算出 124.96。
- valuation totals 与 snapshot rows 均来自同一 NormalizedValuationSnapshot；构造两套不同精度输入时必须在写前失败。
- Feishu 回读把 price 规范化导致公式不等：partial。
- A+B → A 生成 B delete；A optional value → null 生成 clear。
- 空 target 无 overwrite 权限：阻断；有明确 repair authority：生成 exact delete plan。
- create/update 成功、delete 失败及 readback 超时：durable partial receipt。
- NAV 成功后进程在 snapshot 调用前崩溃：重启可从预写 PENDING target 恢复。
- NAV create 返回 timeout 但远端实际成功：fresh read 后不得错误取消 snapshot target。
- digest v1/v2 兼容读取，但新写只生成 v2。
- 历史 final row 的 legacy digest 不因升级自动降级为 non-final；只标记 evidence_version=legacy。新写和显式 rewrite 必须满足 v2。

**完成门槛**

- 同一 account/as_of 远端 slice 与目标 exact match 后才 complete/resolved。
- 使用落表字段可独立回放每行和总估值。

### S6：遗留接口与运维镜像

**依赖**：S1。可在 S2/S3 之后独立实施。

**归属 finding**：F19、F20、F24。

**代码范围**

- src/feishu/repositories/transactions_repository.py
- src/feishu/_transactions_mixin.py
- src/feishu_storage.py
- src/app/compensation_service.py
- docs/schema.md
- legacy surface/compensation mirror tests

**设计**

1. transactions add/delete 对外 surface 返回明确 LegacyReadOnlyError；删除生产调用点和误导性 capability。保留 raw/typed reader，但 required 字段缺失时返回 validation error，不造默认交易。
2. 任何保留的 request_id helper 都强制 account scope，并校验命中行 account；不再承诺 writer idempotency。
3. compensation local append/fold 永远先于 mirror。只有 mirror-eligible 的外部可行动作任务才进入飞书；正常流程中短暂出现并成功关闭的 local-prepared target 不创建镜像。mirror record_id 可存本地 event metadata；缺失时按 task_id fresh 查找，零条 create、一条 update、多条报 mirror duplicate warning。
4. mirror-eligible task 的 PENDING、RUNNING、FAILED、RESOLVED 每次 fold 后 best-effort 写当前 status、retry_count、updated_at、resolved_at、resolution/error。
5. mirror 失败通过 receipt 和 metric 可见，但不改变本地权威状态，也不触发递归 compensation。

**测试**

- 所有 transactions mutation surface 均零 Feishu 调用。
- archive row 缺 required 字段不变成默认 BUY/CNY/0。
- 两账户相同 request_id 的查询不串行。
- mirror create、后续 update、重复 task_id、远端失败；本地 fold 始终正确。

**完成门槛**

- 代码、schema 文案和 API capability 对 legacy/mirror 的角色一致。

### S7：聚合验证与后续线上审计

**依赖**：S2、S3、S4、S5、S6 全部完成。

**源码聚合验证**

- 每个 finding 对应至少一个失败先行回归测试。
- 运行 scoped tests、完整 pytest、compileall、git diff --check。
- 对新 import 关系更新并验证项目依赖图；不引入 holdings → NAV 或 repository → app 的反向依赖。
- 使用 fake Feishu 做 404、timeout、batch partial、readback mismatch、external edit、duplicate 和 cache restart 故障注入。
- 做一次 aggregate deep review，重点检查字段所有权、默认值、null clear、缓存 authority、partial receipt 和跨表 finality。
- S1-S6 中间状态均不得单独 release/deploy；只有 S7 聚合门槛通过后，才可在新的明确授权下进入提交、合并、release 或 upgrade。

**单独授权后的 read-only live audit**

- 核对 live field type/ui_type/select options、configured optional tables、updated_at automation。
- 对 table-ref/filter expression 做 read-only canary；对 null clear 和 exact-set create/update/delete/readback 做受控非生产 canary。写 canary 必须创建精确临时测试 slice、保存 before/after 和清理计划，并单独取得写权限。
- 只读扫描重复业务键、noncanonical identity、invalid numbers、missing required、NAV invariant 和 snapshot replay。
- 输出 account/date/record_id 级 repair inventory，不在审计中写数据。

**历史数据修复的额外门槛**

任何历史 repair 都必须另立 work unit，至少包含：

- 精确 account/date/record_id scope；
- 写前导出和可恢复 backup；
- dry-run before/after diff 与 plan hash；
- 明确字段覆盖和删除权限；
- partial-write compensation target；
- fresh readback 与业务不变量；
- 显式 confirm；
- source rollback 与 data rollback 分开。

## 6. 依赖顺序

    S0 baseline
       |
       v
    S1 common Feishu boundary
       |----------------------|
       v                      v
    S2 holdings/Futu       S3A cash-flow rows
       |                      |
       |                      v
       |                   S3B run dataset
       |                      |
       |                      v
       |                   S4 NAV facts/repair
       |                      |
       |----------------------|
                              v
                           S5 snapshot exact-set

    S1 --------------------> S6 legacy/mirror

    S2 + S3 + S4 + S5 + S6
                |
                v
         S7 aggregate verification

S2 与 S3A 可并行开发；S3B 必须在 S3A 后实施。同一 shared repository/client 文件若发生重叠，必须按 S1 已锁定的 API 合并，不能各自复制 converter 或 validator。

## 7. Finding 覆盖矩阵

| Finding | 严重度 | 唯一归属 | 计划结果 |
|---|---|---|---|
| F01 NAV 有损读取/repair 清字段 | 高 | S4 | full canonical read + field patch |
| F02 backfill 基金重复 | 高 | S4 | persisted/runtime mapping + final invariant |
| F03 cash_flow daily/gap 歧义 | 高 | S4 | daily column + details gap basis |
| F04 invalid Futu qty → 0 | 高 | S2 | provider slice fail closed |
| F05 holding replace 无法 clear | 高 | S2 | null allowlist + readback |
| F06 cash aggregate stale | 高 | S3 | immutable fresh dataset |
| F07 Futu 乐观 cache reconciliation | 高 | S2 | fresh account readback |
| F08 currency 推导 asset_class | 高 | S2 | shared economic-exposure authority |
| F09 broker 可省略任取一行 | 高 | S2 | mutation 完整业务键 |
| F10 snapshot unit price 精度 | 高 | S5 | persisted-input replay |
| F11 snapshot 非 exact set | 高 | S5 | create/update/clear/delete + digest |
| F12 snapshot/batch required 不一致 | 高 | S5 | required snapshot model + S1 shared per-row validator |
| F13 NAV partial batch 事实丢失 | 高 | S4 | stage-aware partial receipt |
| F14 strict schema false green | 高 | S1 | exact field type/ui_type |
| F15 cash reader 默认值掩盖缺失 | 高 | S3 | raw/manual/completed 两层校验 |
| F16 manual cash duplicate 双计 | 高 | S3 | fresh duplicate audit |
| F17 FX 日期错配先写后阻断 | 高 | S3 | pre-write evidence validation |
| F18 stock_value 名实不符 | 中 | S4 | compatibility name + non-cash semantics |
| F19 transactions archive 仍可写 | 中 | S6 | explicit read-only surface |
| F20 transaction request_id scope | 中 | S6 | account-scoped retained read |
| F21 cash projection 漏字段 | 中 | S3 | complete raw projection |
| F22 cash 跨字段校验不一 | 中 | S3 | shared validators |
| F23 read 吞掉所有错误 | 中 | S1 | typed not-found |
| F24 compensation mirror 过期 | 中 | S6 | best-effort current-state upsert |
| F25 holding 校验副本与 payload 不同 | 中 | S2 | canonical write object |
| F26 table ref 三套 parser | 中 | S1 | single parser |
| F27 outbound event_type | 已修复 | S0 | 只验证 origin/main 基线 |
| F28 tag JSON | 已修复 | S0 | 只验证 origin/main 基线 |
| F29 cash exact read 丢 dedup | 中 | S3 | observed-field readback |
| F30 外币 add 污染热 cache | 中 | S3 | completed facts before create |
| F31 missing date 聚合矛盾 | 中 | S3 | blocker, excluded from all aggregates |

## 8. 兼容与迁移策略

- 本轮不新增、删除或重命名 live Feishu 列。
- details 新增的 cash_flow_basis 与 snapshot digest 采用 version 字段，旧 consumer 忽略未知 JSON key。
- 旧 snapshot digest 可读但不可作为 v2 complete 证明；首次显式 rewrite 后升级。
- stock_value 保留旧列名，避免 schema/data migration；所有新文档和内部代码使用 non-cash 定义。
- transaction writer 由潜在危险接口改为明确拒绝，属于安全收窄；若发现真实调用者，S6 必须停止并上报，不能静默保留。
- null clear 是 wire 行为变化，先完成 request-map 测试和非生产 canary；未验证前不得在线上 repair 路径启用。

## 9. 回滚与停机条件

**源码回滚**

- S1-S6 是独立 review/work-unit 边界，S3 和 S5 再按各自 A/B 子切片形成小提交；避免把 schema boundary、财务语义与 destructive snapshot rewrite 混成一个不可回退提交。
- 若新版本尚未写 live 数据，回退到上一发布版本即可；不自动回滚数据。
- 若已执行 clear/delete/exact-set，源码回滚不能恢复数据，必须使用该次 operation plan 的 before set 另行获得 data rollback 授权。

**必须停止的条件**

- origin/main 与审计假设出现重大漂移；
- live schema 与 docs type/ui_type 不一致；
- Feishu Number 回读不能保持 snapshot replay 所需精度；
- transactions 仍有真实生产 writer 调用者；
- null clear 或 batch partial response 无法可靠区分 confirmed/unknown；
- 发现跨账户重复业务键或历史数据修复范围无法精确界定。

## 10. 最终验收

源码工作完成需同时满足：

1. 29 个未解决 finding 全部有关闭证据，F27/F28 基线验证通过。
2. 所有 public read 返回契约声明的完整字段；所有 raw validator 都能观察 missing。
3. 所有 official NAV 计算使用单一 fresh cash-flow dataset。
4. 所有 trusted/resolved/final 状态都有 fresh readback 或明确的 partial 状态。
5. holdings 与 snapshot 的 clear/delete 只在明确 authority 下执行。
6. 全量测试、静态导入检查、diff check 和 aggregate review 通过。
7. 没有 live Feishu 写入、历史修复、release 或 deployment 被源码实现自动触发。

## 11. 外部协议依据

- 飞书服务端 SDK 文档明确说明：清空多维表格一个或多个单元格时，对应字段传 null。实现仍须用 request-map 测试和经单独授权的非生产 canary 验证当前 SDK/租户行为：
  https://open.feishu.cn/document/server-side-sdk/nodejs-sdk/invoke-server-api?lang=zh-CN
- 飞书 batch update 是多记录更新接口，但本方案不把一次 HTTP 成功等同于跨阶段业务事务；仍按 confirmed stage 和 fresh readback 记录：
  https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-record/batch_update
- 飞书字段指南区分 single-select、multi-select 和 ui_type；strict checker 以 live field metadata 为准，不再把 text/select 斜杠文案当作精确类型：
  https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-field/guide
