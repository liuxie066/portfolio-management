# Cash Flow Holding Effects Runbook

This workflow is single-host and fail-closed. Feishu `cash_flow` and
`holdings` remain the business facts; SQLite owns only effect versions,
confirmations, fingerprints, compensation references, scans, and receipt
delivery. The existing durable compensation log owns compensation task state.

## Activate

1. Stop and disable every old process that can write Futu CASH or invoke legacy
   `deposit/withdraw/add_cash/sub_cash`.
2. Configure one persistent `data.dir`, `cash_flow.effects.db_path`, immutable
   `cash_flow.effects.cutover_date`, `futu.profiles`, and the existing
   `feishu.receipt.*` values.
3. Preview and apply the Feishu schema migration:

   ```bash
   python scripts/migrate_schema.py cash-flow-effects
   python scripts/migrate_schema.py cash-flow-effects --apply --confirm
   python scripts/migrate_schema.py check-live --strict
   ```

4. In the normal Feishu operator view, show
   `flow_date/account/broker/amount/currency/remark` and hide generated fields.
5. Initialize exactly once:

   ```bash
   pm cash-flow effects init --cutover-date YYYY-MM-DD --confirm
   ```

6. Reconcile generated CNY/FX fields separately. Historical manual FX evidence
   must be one exact record, one exact `flow_date`, and a traceable source:

   ```bash
   pm cash-flow reconcile --account ACCOUNT
   pm cash-flow reconcile --apply --confirm
   pm cash-flow reconcile --record-id RECORD_ID \
     --exchange-rate RATE --rate-date YYYY-MM-DD --rate-source SOURCE \
     --apply --confirm
   ```

7. Scan and process every current effect one by one:

   ```bash
   pm cash-flow review --json
   pm cash-flow effects preview --effect-id ID --json
   pm cash-flow effects confirm \
     --effect-id ID --preview-hash HASH --confirm
   pm cash-flow effects record-only --effect-id ID --confirm
   pm cash-flow effects audit --account ACCOUNT --json
   ```

8. Only after strict schema check and all account audits pass, enable the NAV,
   Futu, and 15-minute Cash Flow timers.

## Normal Operations

The operator manually enters only external deposits and withdrawals in Feishu.
`broker` is mandatory. Internal transfers and FX conversions are not events in
this workflow.

The 15-minute timer, manual review, and NAV preflight all perform complete
Feishu scans. Only the timer drains discovery/runtime receipts. Review displays
results locally, and NAV uses its existing summary receipt.

Futu CASH is observe-only in `pm futu sync`. Drift creates
`broker_cash_reconciliation`; it never creates a Feishu cash-flow row and never
writes CASH until the effect is individually previewed and confirmed.

If a CASH row was changed directly in Feishu, choose one explicit path:

```bash
pm cash-flow effects preview --effect-id ID \
  --external-action accept_current --json
pm cash-flow effects confirm --effect-id ID --preview-hash HASH \
  --external-action accept_current --confirm

pm cash-flow effects preview --effect-id ID \
  --external-action restore --json
pm cash-flow effects confirm --effect-id ID --preview-hash HASH \
  --external-action restore --confirm
```

Futu external-change preview always refreshes the exact OpenD currency field.

## Compensation

An incomplete write remains `compensation_pending` and blocks NAV. Every retry
requires a new explicit confirmation:

```bash
pm cash-flow effects retry --effect-id ID --confirm
```

The generic compensation command may also repair the absolute target. The next
effect audit performs fresh readback and closes the effect only if the target
matches.

If the Feishu fact changes while compensation is pending, finish the recorded
absolute targets first. The service then creates a new correction effect; it
must be previewed and confirmed separately. A scan never supersedes unresolved
compensation or treats its partial target as a direct Feishu holding edit.

## Backup and Recovery

Never copy an active `.sqlite3` file directly because WAL state may be omitted.
Use the SQLite online backup command:

```bash
pm cash-flow effects backup \
  --output /var/backups/portfolio-management/cash-flow-effects-YYYYMMDD.sqlite3 \
  --confirm
```

Recovery is forward-only:

1. Stop all three timers and the PM API.
2. Preserve the failed database and its `-wal`/`-shm` companions.
3. Restore a verified online-backup artifact to the exact configured
   `cash_flow.effects.db_path` while no PM process is running.
4. Confirm file ownership and mode `0600`.
5. Run `effects audit`, a complete `effects scan`, strict schema check, and a
   synthetic NAV blocker test.
6. Re-enable timers only after every account passes.

Deleting the database, changing the cutover date, re-baselining through `init`,
restoring a Futu direct-CASH writer, or bypassing the NAV gate are prohibited.
