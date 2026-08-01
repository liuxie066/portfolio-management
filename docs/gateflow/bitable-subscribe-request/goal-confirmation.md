# Gateflow Goal Confirmation — Bitable Subscribe Request

- Gate: `goal confirmation`
- Work unit: `bitable-subscribe-request`
- Status: accepted by user on 2026-08-01
- Base: `main@2a1ae72`
- Branch: `fix/bitable-subscribe-request`

## Goal and motivation

Make the public `pm events subscribe --confirm --json` command compatible with
the current Feishu Drive subscription API for a Bitable document. The request
must send the exact Base file token and `file_type=bitable`, while omitting the
folder-only `event_type` query parameter.

The same request contract is used by the holdings-only compatibility command,
so `pm holdings events subscribe --confirm --json` is included to prevent the
two public entry points from drifting.

## Direct evidence

- `FeishuBitableEventAdapter.subscribe()` and
  `FeishuHoldingsEventAdapter.subscribe()` both call
  `.event_type("bitable_record_changed_v1")` on `SubscribeFileRequest.Builder`.
- `lark-oapi==1.7.1` serializes that builder call as the `event_type` query
  parameter.
- Production returned Feishu `1069602 param error` for both configured Base
  files with that parameter.
- Reissuing the same two requests with `file_type=bitable` and no `event_type`
  returned `code=0 / Success` for both files.

## Success signals

- Both subscription adapters build requests with only `file_token` and
  `file_type=bitable`.
- Regression tests fail if either adapter adds `event_type` again.
- Existing target validation, document deduplication, per-file reporting,
  long-connection event registration, and CLI confirmation guards remain
  unchanged.
- Focused tests and the relevant CLI/adapter test set pass.

## Scope boundary

Included:

- combined Bitable subscription request construction;
- holdings-only compatibility request construction;
- focused regression assertions;
- Gateflow/review artifacts.

Excluded:

- changing the received event type
  `drive.file.bitable_record_changed_v1`;
- changing Base targets, credentials, permissions, listener workers, inboxes,
  receipts, or business-table writes;
- re-subscribing production Base files or restarting the active listener;
- changing the current JSON result shape;
- refactoring the two adapters into a new abstraction.

## First-principles judgment

The defect is in request serialization, not event routing. Removing one invalid
query parameter at both existing construction sites is sufficient. A shared
request factory or broader adapter redesign would add indirection without
changing the required wire request, so it is intentionally out of scope.

## Open questions

None. The user confirmed the goal and branch creation on 2026-08-01.

