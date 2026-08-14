# Gateflow Goal Confirmation — Non-Futu Cash Holdings Authority

- Work unit: `non-futu-cash-holdings-authority`
- Base: `origin/main@0f496dac5a38bf72d8410d3df7ca5d7e86bb712d`
- Branch: `fix/non-futu-cash-holdings-authority`
- Decision: confirmed by the user on 2026-08-14
- Current gate: `plan`
- Next entry point: create and review the implementation plan

## Goal and motivation

Correct the Daily NAV cash preflight contract so that a manual CASH holding
change for a non-Futu broker is treated as the authoritative broker snapshot,
while an external deposit or withdrawal remains a cash-flow fact that affects
NAV shares and must be reflected in the corresponding CASH holding.

Preserve fail-closed behavior where an independent authority exists (Futu
OpenD) and replace the raw nested cash-flow blocker JSON in the consolidated
NAV receipt with a structured, actionable user message.

## Direct evidence

- `CashFlowEffectService._scan_holding_fingerprints()` currently creates a
  pending `cash_holding_external_change` for every broker when a CASH holding
  differs from its confirmed fingerprint.
- `_is_nav_blocker()` treats that pending effect as a NAV blocker, so normal
  manual maintenance of a non-Futu CASH row blocks the official NAV.
- `CashFlowDatasetSnapshot.assert_official_scope()` serializes every blocker
  into a `ValueError` string.
- `AccountNavRecorderService.record()` preserves only `str(exception)`, and
  `NavHistoryReceiptService._item_row()` renders that raw string.

## Success signals

- A non-Futu manual CASH change with no cash-flow effect is accepted as a new
  fingerprint baseline and leaves no unresolved NAV blocker.
- Existing pending non-Futu external-change effects converge idempotently to a
  terminal audit state on the next scan; no one-off SQLite migration is needed.
- Every apply-mode non-Futu cash-flow preview explicitly declares whether to
  add the signed delta or whether the current holding already reflects it.
- Both choices end in an `applied` cash-flow version so later corrections use
  only the delta from the prior applied source.
- Futu cash-flow targets continue to use the OpenD absolute observation and
  retain the current fail-closed behavior.
- Daily NAV results retain structured cash-flow refusal data, and the Feishu
  receipt explains the reason and next action without rendering nested JSON.

## Non-goals and scope boundary

- No new SQLite table, schema version, effect kind, timer, Feishu Base field,
  or one-off production migration script.
- No change to cash-flow financial aggregation, NAV share mathematics, Futu
  profile mapping, compensation behavior, or CAS/readback guarantees.
- No release, deployment, remote service change, production effect mutation,
  NAV replay, or live notification.
- No general rewrite of all application error messages. This work unit adds a
  structured path for cash-flow dataset refusals while preserving unrelated
  legacy failure contracts.

## Overengineering decision

The existing cash-flow effect, `record_only` external-change terminal state,
fingerprint store, preview hash, absolute target, and receipt envelope already
provide the necessary boundaries. A conflict table, new effect kind, broker
plugin interface, or new migration framework would duplicate those owners and
is not justified by the current failure.

## Blocking open questions

None. The user confirmed the authority contract and authorized creation of the
isolated work branch while preserving unrelated untracked files.
