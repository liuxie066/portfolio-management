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
   canonical `feishu.agent.app_id` / `feishu.agent.open_id` receipt values.
   On upgrade, confirmations in the default `cash_flow_effects.sqlite3` are
   imported idempotently into `pm_operation_state.sqlite3` by NAV preflight.
   For a non-default legacy DB path, import it before enabling timers:

   ```bash
   pm cash-flow fx-evidence import-legacy \
     --legacy-db /persistent/path/cash_flow_effects.sqlite3 --confirm --json
   ```
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

   In a future controlled activation, the combined event listener removes
   this remembered step for deterministic CNY rows. Before enabling it, run the
   read-only target preflight, then separately confirm the Base subscription:

   ```bash
   pm events status --json
   pm events subscribe --confirm --json
   ```

   Subscription is Base-file-level; the listener still routes and validates the
   exact configured `holdings` and `cash_flow` table IDs locally. A target
   collision is a stop condition. Do not infer subscription or connection
   health from the local status command.

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

When `portfolio-holdings-event-listener.service` is explicitly enabled, its
single long connection covers both configured tables. A `cash_flow` add/edit
event is only a durable trigger: the worker fresh-reads that exact record. It
silently completes valid CNY system fields, and may complete a foreign row only
when the existing local FX confirmation still matches its source hash, date,
rate, and CNY amount. Invalid input or missing/stale FX evidence creates a
durable attention receipt; no rate is guessed.

Generated-field completion does not confirm or apply a Cash Flow holding
effect. Every CASH holding mutation remains under the separate exact effect
preview/confirmation workflow below. The manual exact-record reconcile command
remains the recovery path named in attention receipts.

The 15-minute timer, manual review, and NAV preflight all perform complete
Feishu scans. Only the timer drains discovery/runtime receipts. Review displays
results locally, and NAV uses its existing summary receipt.

Futu CASH is observe-only in `pm futu sync`. Its original-currency values remain
source evidence only: they are not compared with PM's CNY-denominated aggregate
`CNY-CASH`, and they do not create `broker_cash_reconciliation` effects. This
does not change the separately confirmed effect workflow for Feishu cash-flow
ledger facts.

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

Legacy Futu per-currency reconciliation is not an active source of new effects.

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

1. Stop all four timers and the PM API.
2. Preserve the failed database and its `-wal`/`-shm` companions.
3. Restore a verified online-backup artifact to the exact configured
   `cash_flow.effects.db_path` while no PM process is running.
4. Confirm file ownership and mode `0600`.
5. Run `effects audit`, a complete `effects scan`, strict schema check, and a
   synthetic NAV blocker test.
6. Re-enable timers only after every account passes.

Deleting the database, changing the cutover date, re-baselining through `init`,
restoring a Futu direct-CASH writer, or bypassing the NAV gate are prohibited.
