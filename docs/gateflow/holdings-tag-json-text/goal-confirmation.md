# Gateflow Goal Confirmation

- Gate: `goal confirmation`
- Work unit: `holdings-tag-json-text`
- Branch: `fix/holdings-tag-json-text`
- Base: `origin/main@63cce99`
- Artifact path: `docs/gateflow/holdings-tag-json-text/goal-confirmation.md`
- Status: `confirmed`
- User confirmation: `直接实现并验证这个修复`; branch confirmation: `确认`
- Confirmed at: `2026-08-01 13:22:29 +0800`

## Why this work unit exists

The production Daily NAV receipt reported six nonblocking `tag=invalid`
Holdings warnings. Read-only inspection proved that every affected Feishu row
contains the literal text `"[]"`, not a malformed business label. This is the
documented and writer-produced representation for an empty Holdings tag list:
the schema declares `tag` as `text/json`, and `_to_feishu_fields()` serializes
lists with `json.dumps()`.

`HoldingsValidator._optional_tag()` nevertheless accepts only an in-memory
Python list. The raw preflight path intentionally preserves Feishu transport
values, so valid JSON text reaches that validator unchanged and is classified
as `TAG_INVALID`. `RecordValidation.to_holding()` also reads the raw value, so
merely changing the status would turn a valid JSON string into a list of
characters instead of the intended tag list.

## Target outcome

Normalize the documented Holdings `text/json` tag representation exactly once
inside holdings validation so JSON text and an equivalent native list have the
same validation, digest, workflow, and typed-holding semantics.

## Success signals

1. Native `[]` and text `"[]"` are both `optional_missing` and create no
   workflow case or NAV receipt warning.
2. Native string lists and JSON text string lists are both `valid` and produce
   the same typed `Holding.tag` value.
3. Malformed JSON, JSON non-lists, and lists containing non-string items remain
   nonblocking `TAG_INVALID` cases.
4. Existing semantic record-digest equivalence remains intact.
5. A production-shaped preflight row with `tag="[]"` reaches a valid frozen
   snapshot with an empty tag tuple and no warning.
6. Focused tests, the full suite, compile checks, and diff checks pass.

## Scope boundary

### In scope

- Holdings tag parsing and validation in `src/app/holdings_validation.py`.
- Typed holding construction from the normalized validation outcome.
- Unit and preflight regression coverage.
- Gateflow review and closeout artifacts.

### Out of scope

- Feishu schema changes or data migrations.
- Editing the six production records or any Holdings/NAV fact.
- Changing `tag` from optional/nonblocking to required/blocking.
- General JSON-field parsing, repository refactors, cache changes, receipt
  redesign, or changes to NAV calculation.
- Commit/push beyond Gateflow's protected local checkpoint commits, release,
  deployment, service restart, or production verification against modified
  code.

## Direct code evidence

- `docs/schema.md` defines Holdings `tag` as `text/json`.
- `FeishuStorage._to_feishu_fields()` serializes list-valued `tag` fields with
  `json.dumps()`.
- `canonical_record_payload()` already treats JSON text and native tag lists as
  semantically equivalent for digests.
- `HoldingsRepository._strict_holding_tag()` already accepts JSON text lists.
- `HoldingsValidator._optional_tag()` rejects every non-list raw value.
- `RecordValidation.to_holding()` currently reads the raw tag value instead of
  the normalized field outcome.
- Live read-only reconciliation found `current="[]"` and `TAG_INVALID` on lx/sy
  `CNY-MMF`, `CNY-CASH`, and `TCOM`; every case had
  `blocks_official_nav=false`.

## Minimality decision

- Keep the change inside the existing holdings validation owner rather than
  changing raw-source fidelity or the documented Feishu transport contract.
- Decode JSON text strictly inside field validation and leave the already-correct
  digest canonicalization unchanged; do not introduce a general codec layer.
- Use the normalized `FieldOutcome.current` when constructing `Holding.tag` so
  validation and typed materialization cannot diverge.
- Use one implementation slice because parsing, status, and materialization
  must change atomically.

## Blocking open questions

- None.

## Residual risks

- Existing durable warning cases may remain visible until a later formal
  preflight proves them absent: `existing workflow lifecycle owns cleanup`.
- Production warning removal requires a separately authorized release and
  upgrade: `operator-owned deployment boundary`.
- The unrelated untracked
  `docs/reviews/code-review-20260801-084655.md` remains excluded from every
  checkpoint: `preserved user-owned worktree state`.

## Completion state

- Current gate: `goal confirmation pass`.
- Next gate: `plan`.
