# Futu sync failure diagnostics

## Scope and plan

The user approved fixing error information and failure receipts on 2026-11-23.
Base is `9a51a70` (v0.2.24), fetched from origin/main.

1. Preserve Feishu HTTP status, business code/message and available log ID in
   exceptions, retaining HTTPError response access and record-not-found handling.
2. Preserve completed cash writeback when a later stage fails, and report stages
   that have not started as not run.
3. Render pending-asset validation reasons accurately and retain confirmed MMF
   results if the pending-asset stage fails.

The plan is checked against the production failure and current callers. This is
a diagnostic correction within existing transport and receipt owners. No new
retry policy, storage contract or write behavior is needed.

## Validation

The added regression cases failed on the original implementation (17 failures).
They exercise real requests.Response HTTP 200/400/500 business errors, header/body
trace IDs, transport response access, all three failure stages through portfolio
sync, persisted stage outcomes, legacy cash mode and pending-asset reason text.

Python 3.12.13, local `.venv`, requirements.txt plus pytest and Ruff

- Targeted Feishu client, Futu balance service, evidence, receipts and reconciler
  tests passed, 193 tests.
- `.venv/bin/python -m pytest tests -q` initially passed, 2140 tests in 19.67 seconds.
  Initial sandbox execution passed 2130 and failed 10 socket tests because local
  port binding was denied. The authorized rerun outside the sandbox passed all.
- After the review fix below, the final full suite passed, 2153 tests in 19.39
  seconds. Compileall, Ruff, OpenAPI drift and diff checks passed again.
- `.venv/bin/python -X pycache_prefix=/tmp/pm_pycache -m compileall -q src
  skill_api.py scripts/pm.py scripts/publish_daily_report.py` passed.
- `.venv/bin/python -m ruff check src skill_api.py scripts/pm.py
  scripts/publish_daily_report.py` passed. Changed test files also passed Ruff.
- `.venv/bin/python -m scripts.export_om_openapi --check` passed.
- `git diff --check` passed.

Transport exceptions retain HTTPError response/request access and the existing
record-not-found classification. Only explicit business error fields are included
in the new error message. The request URL and full body are not appended.
The runbook now describes the failure output and conservative partial-write warning.
Regression tests cover this incident's reusable lesson without another LESSONS entry.

## Review and disposition

Two independent reviews identified the same diagnostic gap in the typed
record-not-found branch. Accepted and fixed by adding optional HTTP status and log
ID to the existing exception and preserving its original constructor, code,
message and classification. Thirteen additional cases cover HTTP 200/400/404/500
with header/body/absent trace IDs and the legacy constructor.

- Local receipt review, `docs/reviews/code-review-20261123-204241.md`
- Local transport review, `docs/reviews/code-review-20261123-204124.md`

Review reports reside in the repository's ignored `docs/reviews/` directory and
are available locally. Re-review is limited to the accepted finding and its
affected transport/type callers. Both independent reviewers verified the fix.
The shared finding is accepted and fixed; no unresolved in-scope findings remain.

## Status and residual risk

Authorized local implementation, validation and review are complete.
The original intermittent HTTP 400 cause remains
unconfirmed because its business response was discarded. Future diagnosis belongs
to the operator using the improved error output. Commit, push, PR, release and
production upgrade require separate user authorization.

The next delivery entry point, if requested, is local-change verification against
this base followed by the explicitly authorized delivery stage. Production has
not been upgraded during this implementation.

## Branch delivery

On 2026-11-23 the user authorized committing and pushing this repair branch.
The target is `origin/fix/futu-sync-failure-receipts` in
`liuxie066/portfolio-management`. The refreshed origin/main remains `9a51a70`,
matching the verified implementation base. Source, tests, dependencies and
validation environment are unchanged, so the 2153-test result and review
evidence above remain applicable.

The outgoing scope contains the four production files, three test files, runbook
update and this work record. README positioning, setup and feature boundaries
are unaffected. Review files remain local according to repository ignore rules.
The local main is 0 commits ahead and 29 behind origin/main; updating it is
outside this branch-only delivery. The final commit and remote ref verification
are recorded in the delivery response.
