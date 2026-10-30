# 产品范围与约束

本页记录用户已确认的产品定位及有仓库证据的产品边界，不是新路线图。安装和命令由 [README](../README.md)
维护；接口、Schema、运维步骤分别归属 [文档索引](INDEX.md) 指向的现有文档，
此处只摘要和链接。新确认的产品取舍更新本页，不把未决项当作开发要求。

## 目标用户与现有使用角色

- **目标用户（用户已确认）**：本人及固定协作者。现有工作流支持管理一个或多个
  投资账户、维护飞书持仓/现金流并查看每日 NAV。
- 维护本地服务与定时任务、处理数据阻断和回执的运维者；自动化通过同一 CLI/service
  边界执行，兼容 Skill 调用者不代表独立产品入口。
- 同机 options-monitor 是已存在的 API 消费系统，不等同于新增目标用户。

产品定位依据用户明确确认；现有工作流依据：[README](../README.md#核心能力)、[架构的操作者与入口](architecture.md)、
[OM API 契约](om-api-contract.md)。

## 核心能力

- 多账户持仓/现金查询、仓位分布、NAV 计算与历史记录、估值后持仓快照。
- 日净值任务统一处理业务日期、重复记录、现金流与持仓问题的写入门禁，输出汇总回执。
- Futu OpenD 持仓同步、分币种 CASH 观测，以及人工现金流的发现、预览、确认流程。
- 从共享估值快照生成日报数据和本地 HTML；为 OM 提供持仓/估值证据、刷新受理和质量状态。

依据：[主工作流](INDEX.md#main-workflows)、[服务边界](service.md)、
[Cash Effects](cash-flow-holding-effects.md)、[质量文档](quality-monitoring/README.md)。

## 开发必须保持的约束

- **事实归属**：飞书多维表是生产业务事实源，核心表为 `holdings`、`cash_flow`、
  `nav_history`、`holdings_snapshot`；本地 SQLite 保存技术工作流状态，缓存不能替代
  业务权威。`transactions` 是可选历史只读能力，不恢复为日净值必需依赖。
- **写入权限**：默认 dry-run；常规真实写入需显式写入与确认，默认不覆盖同日 NAV。
  CASH Effects 另需绑定当前事实的 preview hash 和独立确认；scanner 只发现，
  Futu sync 对 CASH 只观测。详情以 [Cash Flow 操作手册](cash-flow-effects-runbook.md) 为准。
- **净值可信度**：使用北京日期语义和 CN/HK/US 交易日历；任一市场开市即具有该日
  候选资格，全休市默认跳过，仅显式 `--force-non-business-day` 可覆盖；日历不可用
  时阻断。重复、待补齐字段及未解决 effects 阻断正式写入；
  只有满足 finality 的同日记录可幂等跳过，不能把“已有行”当作完成。
  见 [交易日历](nav-trading-calendar.md)、[服务](service.md#daily-nav-job)。
- **外部依赖**：飞书为生产存储，行情/FX/OpenD 的可用性与证据会影响结果；当前没有
  完整离线写入模式，跨表写入也不具备数据库事务。见 [架构风险](architecture.md#current-risks)。
- **入口与隔离**：产品为 CLI + 本地 HTTP 服务；服务通常绑定 loopback，不把现有
  服务等同于具备公网鉴权的多租户系统。Agent 与 Listener 的飞书权限分离，密钥不落
  YAML 或版本库。见 [README](../README.md#本地服务)、[部署](deploy-linux.md)。
- **输出与证明**：日报只承诺本地静态产物，`public_url=null`；`/health=ok` 只证明
  进程可用，刷新请求 202 只证明受理，二者均不证明持仓或 NAV 已可信更新。
  见 [日报](../README.md#日报与发布)、[质量](quality-monitoring/README.md)、[OM 契约](om-api-contract.md)。
- **跨项目边界**：OM-facing API 由 PM 维护，破坏性变更另开 V2；跨系统质量语义以
  `investment-quality` 为唯一权威，本仓不复制定义。发布、升级与生产写入独立授权。

## 待用户确认的产品信息

以下信息未从现有证据确定，不阻塞当前文档整理，也不预设对应功能需要实现：

1. 下一阶段最重要的用户结果是什么：每日 NAV、现金/持仓对账、日报，还是 OM 取证？
   相应的完成时限、完整性与人工处理负担如何验收？现有调度时间不自动构成 SLA。
2. 已有能力之外，哪些市场、券商、资产或账户组合需要优先支持？未确认前不扩展同步范围。
3. 在已有质量门禁之外，是否需要新增业务级新鲜度/延迟容忍、失败通知或人工确认体验？
   具体阈值和例外待确认，不放宽现有写入约束。
