# Gateflow Goal Confirmation

- Gate: `goal confirmation`
- Work unit: `holdings-date-format`
- Branch: `fix/holdings-date-format`
- Base: `origin/main@43bc738d`
- Artifact path: `docs/gateflow/holdings-date-format/goal-confirmation.md`
- Status: `confirmed`
- User confirmation: `确认`
- Confirmed at: `2026-08-01 08:54:48 +0800`

## Why this work unit exists

Production portfolio synchronization on v0.1.32 failed before any holdings
write because the repository accepted only `YYYY-MM-DD HH:MM:SS` for holdings
`created_at` and `updated_at`. The Feishu holdings table still contains 17
historical values in the established `YYYY/MM/DD` form: eight rows for `lx` and
nine rows for `sy`.

Both accounts failed in the `positions` diff stage with
`FUTU_POSITION_DIFF_INVALID`; `securities_cash` and `fund_mmf` did not run,
`partial_write_possible=false`, and the morning NAV job did not start. The
repository change that introduced strict typed cache publication therefore
made optional transport metadata block the existing Futu synchronization path.

## Target outcome

Restore `YYYY/MM/DD` as the canonical Feishu holdings date representation for
both writes and validation, while retaining bounded read compatibility for the
full timestamps already written by v0.1.30-v0.1.32.

## Success signals

1. Every holdings `created_at` or `updated_at` write emits exactly
   `YYYY/MM/DD`.
2. Repository and standalone holdings validation accept canonical
   `YYYY/MM/DD` values.
3. Existing `YYYY-MM-DD HH:MM:SS` values remain readable during the transition
   and are converted to the existing internal `datetime` model without a
   production migration.
4. Malformed date text still fails repository materialization and remains a
   nonblocking invalid optional field in standalone holdings validation.
5. Production-shaped rows from the incident can preload and reach Futu
   position diffing without a date-format integrity failure.
6. Required holdings field, duplicate identity, numeric, currency, tag, cache
   publication, and write-authority behavior remain unchanged.
7. Focused tests, the full suite, compile checks, diff checks, and all Gateflow
   review gates pass.

## Scope boundary

### In scope

- One holdings-specific canonical date format and parser shared by repository
  materialization and standalone holdings validation.
- All Feishu holdings create/update/bulk/patch and cache serialization paths.
- Transitional read compatibility for the immediately preceding full timestamp
  representation.
- Typed holding construction from canonical and compatible date values.
- Regression tests for exact write payloads, both accepted read forms, invalid
  values, cache reload, validation, and the concrete MMF/Futu sync path.
- Holdings schema documentation.

### Out of scope

- Changing global `DATETIME_FORMAT` or date contracts for NAV, cash flow,
  transaction, price, event, receipt, or operation-state data.
- Editing or normalizing the 17 production rows.
- Re-running holdings synchronization or NAV.
- Changing Futu provider facts, diff logic, write confirmation, locks, receipts,
  or NAV finality.
- Release, tag, GitHub Release, deployment, service restart, or production
  upgrade.

## Direct code and production evidence

- `HoldingsRepository._strict_holding_timestamp()` currently accepts only the
  global `DATETIME_FORMAT` (`%Y-%m-%d %H:%M:%S`).
- The same repository writes that format through cache snapshots,
  `_holding_to_dict()`, patch, single-update, bulk-update, and quantity-update
  paths.
- `HoldingsValidator._optional_transport_field()` independently hard-codes the
  same full timestamp format, so the two validators currently duplicate the
  contract.
- `RecordValidation.to_holding()` passes valid raw timestamp text directly to
  Pydantic; canonical slash dates therefore need explicit shared parsing before
  typed construction.
- Read-only production inspection found 66 holdings rows: 17 format failures,
  all `created_at`/`updated_at` values in `YYYY/MM/DD`, scoped to `lx` and `sy`.
- Production also contains full timestamps written after v0.1.30, so switching
  to slash-only reads without compatibility would create the inverse failure.

## Minimality decision

- Use one small holdings-domain formatter/parser rather than changing the
  repository-wide datetime format or adding a migration framework.
- Keep the internal `Holding` fields as `datetime`; only the Feishu holdings
  transport and cache representation become day-granular.
- Accept exactly two known representations: canonical `YYYY/MM/DD` and the
  immediately preceding full timestamp. Do not add permissive date parsing.
- Use one implementation slice because write serialization, read validation,
  and typed construction must change atomically to avoid another split contract.

## Blocking open questions

- None. The user explicitly confirmed the canonical format, transitional
  compatibility boundary, branch, and non-production scope.

## Residual risks

- Full timestamp rows remain physically mixed in Feishu until an authorized
  business write touches the timestamp: `accepted transition state; parser
  compatibility owns it`.
- Day precision intentionally discards sub-day timestamp precision on future
  holdings writes: `explicit user-owned contract decision`.
- Production recovery requires a separately authorized release and upgrade:
  `operator-owned deployment boundary`.
- The unrelated untracked
  `docs/reviews/code-review-20260801-084655.md` belongs to the prior asset-class
  review and is excluded from every commit in this work unit.

## Completion state

- Current gate: `goal confirmation pass`.
- Next gate: `plan`.
