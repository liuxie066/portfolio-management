# Gateflow S3 Implementation Artifact — Holdings Event Ingress

- Work unit: `holdings-validation-completion-conflict-receipts`
- Gate: `implementation`
- Slice: `S3`
- Base: `82f89cc`
- Status: `complete; DeepReview passed`

## Scope

- Add the official Python `lark-oapi` dependency behind one Feishu adapter.
- Accept only `drive.file.bitable_record_changed_v1` for the configured
  `holdings` Base app token and table id.
- Canonicalize only `record_added`, `record_edited`, and `record_deleted`
  actions; persist by `header.event_id` before returning from the callback.
- Process added/edited records outside the callback by fresh-reading the exact
  record and materializing validation cases and receipts. Deleted records are
  retained as ignored transport evidence.
- Keep the worker validation/notification-only. It must not apply, resolve,
  recover, dispatch receipts, or write holdings fields.
- Add read-only local status and separately confirmed exact-document
  subscription commands.
- Generate a singleton systemd long-connection service but do not enable or
  start it.

## Safety invariants

- Event fields are trigger metadata, never holdings authority.
- Target mismatches are filtered before inbox insertion.
- An existing event id with a different canonical digest is an integrity error.
- Cases, discovery/closure receipts, and the inbox processed transition commit
  in one SQLite transaction.
- Transport or fresh-read failure leaves a retryable inbox row.
- Status does not claim remote subscription, app publication, permission, or
  live connection health.
- No live Feishu/Futu request, subscription, message delivery, service
  activation, release, or deployment is part of this slice implementation.

## Verification targets

- Parser target/action filtering, digest stability, duplicate/collision cases.
- Callback durable-before-return behavior with no business reads or writes.
- Worker fresh-read handoff, ignored delete, retry, semantic no-op, and atomic
  rollback when inbox finalization fails.
- SDK adapter construction and exact subscribe request through test doubles.
- CLI confirmation/status boundaries and disabled-by-default unit generation.
- Focused tests, Ruff, compile check, then DeepReview and re-review if needed.

## Result

- Exact-target SDK ingress, durable callback acceptance, leased processing,
  frozen event provenance, read-only local status, confirmed subscription, and
  disabled-by-default installer assets are implemented.
- S1/S2/S3 focused regression suite: `110 passed`.
- Ruff, Python compilation, installer shell syntax, and `git diff --check`
  passed.
- Initial DeepReview findings are documented in
  `docs/reviews/code-review-20260731-221705.md`; all were fixed and the re-review
  at `docs/reviews/code-review-20260731-221743.md` passed.
- No live Feishu/Futu request, document subscription, message send, service
  activation, release, deployment, push, or PR action was performed.
