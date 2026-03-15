---
name: portfolio-management
description: |
  管理投资组合的持仓记录、收益率计算和完整报告输出，支持A股、港股、美股、基金、现金。

  支持的资产类型：A股(6位数字)、港股(4-5位数字)、美股(字母代码)、
  场内ETF(6位数字)、场外基金(6位数字)、现金(CNY-CASH等)。

  数据存储在飞书多维表，需先配置环境变量。
---

# 投资组合管理

## 环境变量

```bash
# 认证方式（二选一）
FEISHU_APP_ID=cli_xxxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxx
# 或
FEISHU_USER_TOKEN=u-xxxxxxxxxx

# 多维表配置
FEISHU_APP_TOKEN=bascnxxxxxxxxxxxxx
FEISHU_TABLE_HOLDINGS=tblxxxxxxxx
FEISHU_TABLE_TRANSACTIONS=tblxxxxxxxx
FEISHU_TABLE_NAV_HISTORY=tblxxxxxxxx
FEISHU_TABLE_CASH_FLOW=tblxxxxxxxx

# 可选
PORTFOLIO_ACCOUNT=lx
FINNHUB_API_KEY=xxxxxxxxxx  # 美股价格（可选，有则优先使用）
```

## API 使用指南

### 交易操作

```python
from skill_api import buy, sell, deposit, withdraw

# 买入（自动补全资产名称，幂等性控制）
buy(code="600519", name="贵州茅台", quantity=100, price=1500,
    date_str="2025-03-01", market="平安证券", request_id="order_001")

# 卖出
sell(code="600519", quantity=50, price=1600, request_id="sell_001")

# 入金/出金
deposit(amount=50000, date_str="2025-03-01", remark="工资入金")
withdraw(amount=30000, date_str="2025-03-15", remark="消费")
```

### 查询操作

```python
from skill_api import get_holdings, get_position, get_distribution, get_nav, get_return, get_cash

# 持仓查询（按市值排序）
get_holdings()                          # 基础查询
get_holdings(include_price=True)        # 包含实时价格
get_holdings(group_by_market=True)      # 按券商分组

# 仓位/资产分布
get_position()                          # 股票/现金/基金占比
get_distribution()                      # 按类型/券商/币种汇总

# 净值与收益
get_nav()                               # 最新净值及30天趋势
get_return("daily")                     # 当日收益
get_return("month", "2026-03")          # 指定月份收益
get_return("year", "2026")              # 指定年度收益
get_return("since_inception")           # 自成立以来收益

# 现金管理
get_cash()                              # 查看现金明细（按币种汇总）
add_cash(10000)                         # 增加现金
sub_cash(5000)                          # 减少现金
```

### 报告与净值

```python
from skill_api import full_report, generate_report, record_nav

# 完整报告（只读，不记录净值）
full_report()
full_report(price_timeout=30)           # 带超时保护

# 日报/月报/年报
generate_report("daily")               # 日报
generate_report("monthly")             # 月报
generate_report("yearly")              # 年报
generate_report("daily", record_nav=True)  # 日报并记录净值

# 独立记录净值（与报告解耦）
record_nav()
```

### 其他

```python
from skill_api import get_price, init_db, clean_data

# 查询单个资产价格
get_price("600519")

# 初始化数据库
init_db(account="lx", initial_cash=100000)

# 数据清理（默认 dry_run=True 预览模式）
clean_data(table="transactions", code="TEST")           # 预览
clean_data(table="transactions", code="TEST", dry_run=False)  # 实际删除
clean_data(table="all", empty_only=True, dry_run=False)       # 清理空记录
clean_data(table="nav_history", date_before="2024-01-01")     # 按日期过滤
```

## 高频指令

### 查看持仓明细

**调用**: `get_holdings(include_price=True, group_by_market=True)`

**输出样式**:
```
## 持仓明细 (总市值: ¥1,500,000.00, 现金比例: 15.0%)

### 平安证券 (¥900,000.00, 60.0%)
| 代码 | 名称 | 数量 | 现价 | 市值 | 权重 |
|------|------|------|------|------|------|
| 600519 | 贵州茅台 | 100 | ¥1,413.64 | ¥141,364 | 9.4% |
| 00700 | 腾讯控股 | 200 | ¥547.50 | ¥109,500 | 7.3% |
```

### 查看完整报告

**调用**: `full_report()` + `record_nav()`

**输出样式**:
```
## 投资组合报告 (2026-03-14)

### 概览
- 总市值: ¥1,500,000.00
- 现金比例: 15.0%
- 股票比例: 60.0%
- 基金比例: 25.0%

### 净值
- 最新净值: 1.2345 (2026-03-14)

### 收益统计
| 周期 | 涨幅 |
|------|------|
| 当日 | +0.85% (较昨日) |
| 当月 | +3.25% |
| 当年 | +8.50% |
| 2024至今 | +23.45% |

### Top10 持仓
| 代码 | 名称 | 市值 | 权重 |
|------|------|------|------|
| 600519 | 贵州茅台 | ¥141,364 | 9.4% |
| 00700 | 腾讯控股 | ¥109,500 | 7.3% |

### 资产分布
| 类型 | 市值 | 占比 |
|------|------|------|
| 股票 | ¥900,000 | 60.0% |
| 基金 | ¥375,000 | 25.0% |
| 现金 | ¥225,000 | 15.0% |
```

## 数据源

| 资产类型 | 主数据源 | 备用源 |
|---------|---------|--------|
| A股/ETF | 腾讯财经 | AKShare |
| 港股 | 腾讯财经 | AKShare |
| 美股 | Finnhub API (需配置 key) | Yahoo Finance API → yfinance |
| 场外基金 | AKShare (单个查询) | AKShare (全量排行) → 东方财富 |
| 汇率 | exchangerate-api.com | 新浪财经 → exchangerate.host |

## 缓存策略

- **交易时间**: 缓存 30 分钟
- **非交易时间**: 缓存到下次开盘
- **基金**: 缓存到下次 19:00 净值更新
- **汇率**: 内存 + 本地文件双层缓存，24 小时有效
- **价格缓存**: 本地 JSON 文件 (`.data/price_cache.json`)

## 数据表结构

| 表名 | 说明 | 业务主键 | 关键字段 |
|------|------|----------|----------|
| holdings | 持仓表 | (asset_id, account, market) | asset_name, asset_type, quantity, avg_cost, currency, asset_class, industry |
| transactions | 交易记录表 | dedup_key + request_id | tx_date, tx_type, asset_id, account, market, quantity, price, amount, currency, fee |
| cash_flow | 出入金记录表 | dedup_key | flow_date, account, amount, currency, cny_amount, flow_type |
| nav_history | 净值历史表 | (account, date) | total_value, stock_value, cash_value, fund_value, cn/us/hk_stock_value, stock_weight, cash_weight, shares, nav, cash_flow, share_change, nav_change, ytd_nav_change, pnl, ytd_pnl, details |

### 本地文件

| 文件 | 说明 |
|------|------|
| `.data/price_cache.json` | 价格缓存（自动过期清理） |
| `.data/rate_cache.json` | 汇率缓存 |

## 飞书 API 限制

- **日期字段**: 存储为 Unix 时间戳（毫秒），**不支持比较操作符**（>=, <=, <, >），所有日期过滤在客户端完成
- **QPS 限制**: 20 QPS，客户端已内置 60ms 间隔限流 + 429 指数退避重试
- **批量操作**: 单次最多 500 条记录
