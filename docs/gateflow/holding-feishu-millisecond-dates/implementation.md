# Holdings Feishu millisecond dates

## Goal and plan

User authorized compatibility with Feishu millisecond timestamps and rerunning the failed Cash Flow scan on liuxie-incus. Production reproduction found nine CASH rows rejected for numeric created_at values (1784732887000 and 1792985446000). Fix the shared date parser, preserve canonical text writes, and pass raw numbers through HoldingsValidator. No schema mutation or cash adjustment.

Plan check: small internal transport compatibility change; no additional architecture or state-machine decision. Add regression coverage for scanner's fresh cash repository path, validator conversion, existing formats, UTC+8 midnight, invalid numbers and overflow. Full project checks and independent final reviews required.

## Implementation

Numeric int/float milliseconds parse with explicit UTC+8 and retain the existing naive datetime return contract. Existing text formats remain strict; numeric strings and bool remain invalid. Nonfinite and out-of-range values raise ValueError. Validator validates the raw transport value instead of its string projection. Schema documentation describes read compatibility, leaving writes unchanged.

## Validation

- Targeted tests: 164 passed. Initial test expectations mistakenly converted one fixture to 15:30 instead of 11:30 Beijing time; corrected expected fixture value, then all targeted tests passed.
- Full suite: sandbox run had 1950 passed / 10 failed due to prohibited local socket binds (/tmp/pm-date-pytest.log). Repeated with local networking allowed: 1960 passed in 15.75s (/tmp/pm-date-pytest-network.log).
- Python 3.12.13 compileall, Ruff, OpenAPI drift check and git diff --check each independently exited 0.
- Independent final reviews: docs/reviews/code-review-20261119-135648.md and docs/reviews/code-review-20261119-135652.md. Both passed with no material findings; controller accepted both. No code changes followed reviews.

## Delivery boundary

User subsequently authorized branch commit/push and PR creation; merge/release/deployment remain unauthorized. Project deployment uses released tags and installer; no supported unversioned patch route was found. The requested production scan rerun depends on deploying this fix. Prepare and verify the local change, then obtain authorization for the exact release/upgrade stages before production code changes. Do not call the old scanner to claim verification of the new parser.

## Current state

Local implementation/validation/review passed. Next entry: obtain release/upgrade authorization. No commit, push, PR, release, deployment or production scan rerun performed. Current local base is v0.2.13, while inspected remote runs v0.2.17; delivery must first refresh and reconcile the production release baseline, retaining the minimal patch and rerunning checks whose evidence changes. No unsupported hot patch applied.

## PR delivery verification

- Refreshed origin/main to 20003c0 (v0.2.17), fast-forwarded task branch with all task edits preserved. Local main remains 10 commits behind origin/main (0 ahead); not modified because this task does not merge/release or request main synchronization.
- Relevant parser/validator/repository files were unchanged between the old base and origin/main. Final full suite on refreshed base: 2086 passed, 1 Pydantic warning, 21.67s (/tmp/pm-date-main-pytest.log).
- Compileall, Ruff and OpenAPI drift check each independently exited 0 on refreshed base. Scope remains the same five functional/test/doc files plus task evidence.
- PR scope excludes VERSION/CHANGELOG changes, production deployment and scan rerun. Next stage after PR delivery requires separate merge/release/upgrade authorization.
