# US Pricing: Sina replaces Yahoo Chart

- 2026-07-22 早间 NAV 任务失败：部署主机（广州阿里云）上 Yahoo Chart 稳定返回 403 封锁页，Finnhub 同时大面积超时，lx/sy 账户全部美股缺价，NAV 写入被阻断性告警拒绝。
- US 取价链路改为 Finnhub（有 API key 时）→ 新浪美股（`hq.sinajs.cn/list=gb_*`）→ 过期缓存；`src/pricing/providers/yahoo_chart.py` 删除，新增 `src/pricing/providers/sina_us.py`。
- 新浪接口必须带 `Referer: https://finance.sina.com.cn`，响应为 GBK 编码；字段索引以实盘 FUTU/BABA/GOOGL/PDD/TCOM/TIGR/SPY 核对（[1] 现价、[26] 昨收等）。
- 类别股点号代码（如 BRK.B）在新浪查询串中写作 `$`（`gb_brk$b`），`_sina_query_code()` 统一转换；`gb_brk.b` 会返回空串（plan review PR-01 实盘验证）。
- `fetch_us_batch()` 不再逐代码调兜底源：Finnhub 失败的代码合并成一次新浪批量请求，未命中再走过期缓存；各源失败原因会打印到日志（此前批量路径静默吞异常，增加排查成本）。
- FX 查询仍延迟到确有可用报价之后（沿用 decisions/2026-05-24-pricing-yahoo-chart.md 的设计）。
