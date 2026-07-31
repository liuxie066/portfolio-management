# Holdings Event Listener Activation Runbook

This runbook activates the Feishu Base record-change event as an additional
trigger for holdings validation and durable receipts. It does not make event
payload fields authoritative and does not grant automatic holdings-write
permission.

## Boundaries

- The listener accepts only `drive.file.bitable_record_changed_v1` for the
  configured holdings Base app token and table id.
- The callback validates and durably inserts trigger metadata, then returns. A
  worker fresh-reads the exact record before validation.
- Added and edited records may create validation cases and receipt outbox rows.
  They never patch holdings without a later exact-record manual confirmation.
- Deleted or already-disappeared records are retained as audited no-ops.
- Delivery is at-least-once. `header.event_id` plus the full payload digest is
  the transport deduplication and collision boundary.
- `pm holdings events status` is local/config evidence only. It deliberately
  reports remote subscription and connection health as unverified.

## Preconditions

Stop unless every item is independently confirmed:

1. `feishu.app_id` and `feishu.app_secret` belong to the existing PM data
   enterprise custom app, not the receipt bot.
2. `feishu.tables.holdings` resolves unambiguously to the intended
   `app_token/table_id`. If it is only a table id, `feishu.app_token` is set.
3. The enterprise app has the required Base/Drive record-event permissions and
   the app identity can access the configured Base document.
4. The app event configuration includes the exact record-change event and the
   updated app configuration has been published.
5. The operation-state database and its parent directory are writable by the
   systemd `User`, with the same `PM_DATA_DIR` used by the receipt timer.
6. The installed environment contains the official `lark-oapi` dependency.

Local preflight, with no Feishu request:

```bash
pm config doctor --json
pm holdings events status --json
systemctl cat portfolio-holdings-event-listener.service
```

Required success evidence includes the exact target identity,
`app_secret_configured=true`, `sdk_available=true`, a readable local inbox, and
both remote health booleans still `false`. Those false values are not failures;
they prevent local status from pretending to verify external state.

## Separately confirmed subscription

The following is a Feishu-side mutation. Run it only after the app publication,
permission, document-access, and target-identity checks above are complete:

```bash
pm holdings events subscribe --confirm --json
```

This command creates only the exact configured Base document subscription. It
does not edit the app event configuration and does not enable the listener.
Failure or ambiguous output is a stop condition.

## Separately confirmed service activation

After the subscription succeeds, install or upgrade with the explicit flag:

```bash
sudo scripts/install.sh --apply --enable-holdings-event-listener
systemctl status portfolio-holdings-event-listener.service
journalctl -u portfolio-holdings-event-listener.service -n 100 --no-pager
```

The service is a singleton long connection with no public listening socket or
polling timer. Startup must fail closed on incomplete target configuration,
missing SDK/credentials, or operation-state integrity failure.

## Controlled canary

1. Add one holdings row whose asset type has a deterministic currency and leave
   only `currency` blank.
2. Confirm the event inbox reaches `processed`, one missing-completable case is
   created, and one discovery receipt is queued/sent.
3. Confirm the callback did not write the row. Apply only through the separately
   confirmed exact-record command shown in the receipt.
4. Edit the row to an intentionally conflicting currency. Confirm a conflict
   receipt is produced and no write occurs until an operator chooses
   `accept-proposed` or `keep-current` with a reason.
5. Re-deliver the same event id in a test environment and confirm it does not
   create another case or discovery receipt.

Do not treat receipt delivery alone as listener health. Retain the inbox row,
case/outbox state, exact record fresh-read result, and message delivery evidence
for the canary.

## Disable and recover

Stopping the listener does not delete inbox/case/outbox state:

```bash
sudo systemctl disable --now portfolio-holdings-event-listener.service
pm holdings events status --json
```

Queued receipts remain owned by `portfolio-receipt-dispatch.timer`. Pending or
retryable inbox rows remain durable and can be processed after the service is
re-enabled. Do not delete the SQLite database, fabricate a processed outcome,
or manually patch a conflicted holdings field as a recovery shortcut.
