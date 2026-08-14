# NAV 估值证据恢复

本流程只用于“估值已经完成、随后被 cash-flow gate 阻断”的 NAV。它不会绕过
Holdings 或 cash-flow 校验，也不会接受本地路径或任意 JSON。

## 新失败记录

受支持的 daily-job cash-flow gate 失败会返回 `valuation_ref`。处理 blocker 后，
用同一账户和日期重放：

```bash
./pm daily-job \
  --account lx \
  --nav-date 2026-08-13 \
  --valuation-ref 'nav-valuation-evidence:v1:...' \
  --write --confirm --json
```

重放会先重新校验证据作用域、当前 Holdings digest 和当前 cash-flow fingerprint/
gate，再复用已保存的估值；不会重新取价格。NAV 份额、现金流、收益和 Holdings
snapshot 仍由现有正式写入路径计算和保存。

## 旧失败记录：先生成历史证据

前提：

- 已处理目标日期的 cash-flow blocker，当前 gate 能通过；
- 当前 Holdings digest 仍等于失败回执中的 digest；
- OpenD 可用，且能访问 Eastmoney 历史基金净值接口；
- 使用失败运行原始的 FX 和 valuation-as-of，不使用当前汇率。

先 preview。以下是 `lx / 2026-08-13` 的已确认输入：

```bash
./pm nav evidence prepare-historical \
  --account lx \
  --nav-date 2026-08-13 \
  --source-run-id 'daily-nav-job-20260814T081131264745-multi-00fc7d67:lx' \
  --expected-holdings-digest c5c224999115a39638ffc46e1e83cf49562804fea76d00df73af4e77286df815 \
  --expected-cash-flow-fingerprint 667bad901de6a740e84124c76d02bd9a3c14cc97982fdc97e91d6d4265b17009 \
  --source-effect-store-revision cfs_d39268c5b600401ab5b83eb367294984 \
  --valuation-as-of 2026-08-14T08:11:45.216546 \
  --usdcny 6.757 \
  --hkdcny 0.8611 \
  --json
```

Preview 必须返回 `status=preview`，且不会写 artifact。核对：

- Holdings 和 cash-flow fingerprint 均未变化；
- A/H/US 股票及场内基金的 `provider=futu_opend`、`fact_date=2026-08-13`；
- 场外基金的 `provider=eastmoney` 且 `fact_date <= 2026-08-13`；
- CASH/MMF/crypto 的 FX 与命令输入一致；
- 总资产和逐项价格符合原始运行证据。

核对后，原样重跑并增加 preview 返回的 digest：

```bash
./pm nav evidence prepare-historical \
  ...与 preview 完全相同的参数... \
  --write --confirm \
  --expected-digest '<PREVIEW_ARTIFACT_DIGEST>' \
  --json
```

成功会返回 `status=written` 和 `valuation_ref`。随后使用上一节的 `daily-job
--valuation-ref` 命令先 dry-run，再在核对输出后执行 `--write --confirm`。

任一日期、digest、provider fact、FX、Holdings 或 cash-flow 变化都会 fail closed；
不要修改 artifact 文件或改用当前价格规避失败。
