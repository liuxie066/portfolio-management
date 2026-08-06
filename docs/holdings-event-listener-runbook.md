# Holdings and Cash Flow Event Listener Activation Runbook

This runbook activates the Feishu Base record-change event as an additional
trigger for holdings validation and cash-flow generated-field completion. It
does not make event payload fields authoritative, grant automatic holdings
write permission, guess FX, or confirm CASH holding effects.

## Boundaries

- One long connection accepts only `drive.file.bitable_record_changed_v1` for
  the configured holdings and cash-flow Base targets. A Base document is the
  subscription boundary; exact app/file/table routing remains local.
- The Listener application owns only document subscription and the long
  connection. The original Agent application owns every Base fresh read/write
  and every conversation/receipt. There is no third identity and no cross-role
  fallback.
- The callback validates and durably inserts trigger metadata, then returns. A
  worker fresh-reads the exact record before validation.
- Added and edited records may create validation cases and receipt outbox rows.
  They never patch holdings without a later exact-record manual confirmation.
- Valid CNY cash-flow rows may have only their repository-owned generated fields
  completed automatically. Foreign rows require an exact existing local FX
  confirmation; missing/stale evidence produces a receipt and no write.
- Cash-flow generated-field completion never confirms or applies the separate
  CASH holding effect.
- Deleted or already-disappeared records are retained as audited no-ops.
- Delivery is at-least-once. `header.event_id` plus the full payload digest is
  the transport deduplication and collision boundary.
- `pm events status` is local/config evidence only. It deliberately reports
  remote subscription and connection health as unverified. The legacy
  `pm holdings events ...` interface remains available for holdings-only
  compatibility but is not the combined service entry point.

## Preconditions

Stop unless every item is independently confirmed:

1. `feishu.agent.app_id` belongs to the original Agent bot, has access to the
   target Base, and `pm-feishu-agent-app-secret` exists as an encrypted systemd
   credential. `feishu.agent.open_id` names the receipt recipient.
2. `feishu.listener.app_id` belongs to the Listener bot and
   `pm-feishu-listener-app-secret` exists as an encrypted systemd credential.
   This app is used only for event subscription and long-connection ingress.
3. `feishu.tables.holdings` and `feishu.tables.cash_flow` each resolve
   unambiguously to the intended `app_token/table_id`. Their
   `(app_id,file_token,table_id)` identities must be distinct. If a value is
   only a table id, `feishu.app_token` is set.
4. The Agent app can access the configured Base, while the Listener app has the
   required Drive record-event permission and document subscription access.
5. The Listener app event configuration includes the exact record-change event and the
   updated app configuration has been published.
6. The operation-state database and its parent directory are writable by the
   systemd `User`, with the same `PM_DATA_DIR` used by the receipt timer.
7. The installed environment contains the official `lark-oapi` dependency.

Local secure preflight, with no Feishu request:

```bash
sudo systemctl start portfolio-feishu-preflight.service
systemctl status portfolio-feishu-preflight.service --no-pager
journalctl -u portfolio-feishu-preflight.service -n 100 --no-pager
systemctl cat portfolio-holdings-event-listener.service
```

The preflight unit loads both named credentials and runs
`pm config doctor --require-secure-feishu --json` followed by
`pm events status --json`. Required success evidence includes both exact target
identities, a valid target registry,
`credentials.listener_ingress.app_secret_configured=true`,
`credentials.agent_worker.app_secret_configured=true`, `sdk_available=true`,
readable local inboxes, and both remote health booleans still `false`. Those
false values are not failures; they prevent local status from pretending to
verify external state.

## Separately confirmed subscription

The following is a Feishu-side mutation. Run it only after the app publication,
permission, document-access, and target-identity checks above are complete:

```bash
sudo systemd-run --wait --pipe --collect \
  --unit=portfolio-feishu-subscribe-once \
  --property=Type=oneshot \
  --property=User=portfolio \
  --property=WorkingDirectory=/opt/portfolio-management/current \
  --property=EnvironmentFile=/etc/portfolio-management/portfolio-management.env \
  --property=Environment=PM_REQUIRE_SECURE_FEISHU_CREDENTIALS=1 \
  --property=LoadCredentialEncrypted=pm-feishu-listener-app-secret \
  /usr/bin/env PM_REQUIRE_SECURE_FEISHU_CREDENTIALS=1 \
  CREDENTIALS_DIRECTORY=/run/credentials/portfolio-feishu-subscribe-once.service \
  /usr/local/bin/pm events subscribe --confirm --json
```

This command subscribes each distinct configured Base document exactly once.
When both tables share a Base, it performs one document subscription. It does
loads no Agent credential, does not edit the app event configuration, and does not
enable the listener. `file_type=bitable` is sent and outbound `event_type` is
omitted; the inbound registration remains exactly
`drive.file.bitable_record_changed_v1`. Failure, partial success, or ambiguous
output is a stop condition. The final `/usr/bin/env` assignments intentionally
override the shared `EnvironmentFile`; do not remove or reorder them.

## Separately confirmed service activation

After the subscription succeeds, install or upgrade with the explicit flag:

```bash
sudo scripts/install.sh --apply --enable-holdings-event-listener
systemctl status portfolio-holdings-event-listener.service
journalctl -u portfolio-holdings-event-listener.service -n 100 --no-pager
```

The legacy-named service is a singleton combined long connection with no public
listening socket or polling timer. Startup must fail closed on incomplete or
colliding target configuration, missing SDK/credentials, or operation-state
integrity failure.

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
6. Add one complete CNY `cash_flow` row with generated fields blank. Confirm the
   cash-flow inbox reaches `processed`, only that record is completed, and the
   listener's own writeback becomes a silent `already_complete` no-op.
7. Add one foreign `cash_flow` row without confirmed FX evidence. Confirm no
   guessed rate is written and one semantic attention receipt names the exact
   manual reconcile command. Separately confirm that no CASH holding effect was
   applied.

Do not treat receipt delivery alone as listener health. Retain the inbox row,
case/outbox state, exact record fresh-read result, and message delivery evidence
for the canary.

## Disable and recover

Stopping the listener does not delete inbox/case/outbox state:

```bash
sudo systemctl disable --now portfolio-holdings-event-listener.service
pm events status --json
```

Queued receipts remain owned by `portfolio-receipt-dispatch.timer`. Pending or
retryable rows in either inbox remain durable and can be processed after the
service is re-enabled. Do not delete the SQLite database, fabricate a processed
outcome, guess FX, auto-confirm a CASH effect, or manually patch a conflicted
holdings field as a recovery shortcut.
