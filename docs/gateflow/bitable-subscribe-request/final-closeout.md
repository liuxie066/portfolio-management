# Gateflow Final Closeout — Bitable Subscribe Request

- Gate: `final closeout`
- Work unit: `bitable-subscribe-request`
- Status: `final closeout pass`
- Draft PR: `https://github.com/liuxie066/portfolio-management/pull/39`
- Branch: `fix/bitable-subscribe-request`
- Current accepted PR-review head: `f03c420`

## What changed

- Removed the unsupported `event_type` query parameter from the combined
  `pm events subscribe` Bitable document-subscription request.
- Applied the same request correction to the public holdings-only compatibility
  command.
- Strengthened tests to assert complete request maps for same-file deduplication,
  distinct-file ordering, and the holdings compatibility path.
- Preserved callback event registration, CLI confirmation, response/error
  handling, result shape, target validation, inboxes, receipts, and all business
  data behavior.

## What was verified

- Incident evidence: both production Base requests failed with Feishu `1069602`
  when `event_type` was present and succeeded with `code=0` when it was omitted.
- Focused adapter and CLI tests: `52 passed in 1.00s`.
- Complete local suite: `1014 passed in 20.96s`.
- Local branch `git diff --check`: clean.
- GitHub PR merge state against current `main@4ef3492`: clean.
- GitHub quality-contract after the accepted PR review push: passed in 27
  seconds, Actions run `30678359753`.

## Review finding status

- Plan review: no material findings; `pass-with-risks`.
- S1 code review: no material findings; `pass-with-risks`.
- Aggregate deepreview: no production-code finding.
- `DR-AGG-01`: accepted and fixed; EOF blank lines were removed and branch-level
  diff-check evidence was re-reviewed as `已修复`.
- PR review: no material findings; `pass-with-risks`.

## Documentation decision

No operator runbook changed because the public command and activation sequence
remain unchanged. Gateflow and review artifacts record the protocol correction,
incident evidence, tests, and review decisions.

## Remaining risks and owners

- The compatibility result key `subscription_event_type` remains semantically
  ambiguous but unchanged. Owner: a separate public-contract cleanup work unit
  if requested.
- Future Feishu protocol drift is external. Owner: release/operations monitoring
  and a new compatibility work unit if observed.
- Production subscriptions and the listener are already active through the
  corrected one-off request, but the repository fix is not yet merged,
  released, or deployed. Owner: the separate merge and release workflow.

## Scope preservation

- No production re-subscription, listener restart, permission change, Base row
  write, NAV run, CASH effect, release, or deployment occurred in this work
  unit.
- Unrelated local artifacts under
  `docs/gateflow/holdings-case-precondition-identity/` and the unrelated review
  files were neither staged nor modified by this work unit.

## Issue link status

No GitHub issue was provided or created; no issue closing keyword or closeout
comment is required.

## Next entry point

After user authorization, merge draft PR #39. Publishing a new version and
upgrading the remote are separate subsequent authorization boundaries.
