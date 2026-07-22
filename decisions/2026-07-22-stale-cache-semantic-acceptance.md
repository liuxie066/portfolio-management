# Stale Price Cache: Semantic Acceptance

- 问题：stale 缓存契约要求 `allow_expired=True` + `max_stale_after_expiry_sec > 0` 双条件（local_cache.py:148-151），但生产调用方（valuation_service → fetch_batch）从不传窗口，导致过期缓存兜底自引入（055bbe6）起就是死代码。2026-07-22 美股数据源事故中，该兜底完全未生效。
- 但简单传固定窗口会引入更大风险：2026-07-22 早间 NAV 记录的是周一净值，若兜底生效会把周六缓存的周五美股收盘价静默写成周一 final NAV——跨场旧价语义错误。"失效"当时意外保护了 NAV 正确性。
- 决策：stale 接受条件改为语义判定——仅当报价市场自缓存过期后**未开市交易过**时接受（休市中价格不会动，过期只是 TTL 产物）；开市过则拒绝（fail-closed）。新增 `MarketTimeUtil.has_market_session_between()`；`PriceCachePolicy.get` 在 `accept_stale=True` 且窗口=0 时走该判定，显式窗口（>0）契约保持不变。
- 以 `expires_at` 为锚点近似报价时间（闭市保存时=下次开市，开市保存时=+30min）；开市保存的场景可能漏算 save→expiry 间最长 30 分钟的价格变动，可接受。
- fund/未知市场类型一律拒绝 stale（净值每日更新，旧值无意义）；CN/HK 节假日不在日历契约内，误判方向为拒绝（fail-closed），安全。
- stale 报价不阻断 NAV 写入（既有行为），通过 `价格汇总 stale_fallback=N` 和回执可观测。
