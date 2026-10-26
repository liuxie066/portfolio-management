# Local validation and independent finding disposition

Final implementation source was frozen before the checks below. Python 3.12.13
was explicitly selected because the host's bare python3 is 3.9.6. No production
read/write, live HTTP, credentials, messages, remote CI or public delivery occurred.

## Results

| Check | Actual result |
|---|---|
| Original synthetic baseline, before code | 3 expected assertion failures in 0.34s |
| Implementation focused suite, final amended scope | 253 passed in 1.58s |
| Independent synthetic QA | 66 passed in 0.65s, zero failed/skipped |
| Original baseline assertions retained after amendment | 2 passed, 1 explicitly deselected: the old HDK=invalid assertion was superseded by user-requested confirmation-only conflict |
| First strict-isolation full run | 1719 passed, 29 failed in 28.17s; runner multiprocessing/config/subprocess incompatibilities plus two existing date fixtures |
| Audited local-subprocess runner compatibility checks | 26 passed |
| Full repository suite, actual current date | 1746 passed, 2 failed in 12.16s |
| Unmodified b8e75ea baseline, the same two NAV tests | 2 failed in 0.40s at the same assertions |
| Two NAV tests with externally fixed fixture clock | 2 passed in 0.33s |
| Full repository suite with only those two tests' fixture clock fixed | 1748 passed in 12.41s, zero failed/skipped |
| compileall src, skill_api.py, scripts/pm.py, scripts/publish_daily_report.py | passed |
| ruff check on the same required source targets | All checks passed |
| git diff --check | passed |

The two current-date failures are in tests/test_nav_public_freshness.py. They
hard-code 2026-09-24 rows but query the most recent 30 days; on 2026-10-26 the
fixture is outside that window. src/feishu/repositories/nav_history_repository.py
uses bj_today() - timedelta(days=days). Neither this repository source nor those
tests changed in this work unit. Baseline archive reproduction proves this is
pre-existing; the supplemental external pytest fixture freezes bj_today to
2026-09-26 only for those two tests. No product clock or repository test was
edited. The deterministic green run does not erase the real-date failures.
Maintainer follow-up owns making that unrelated date fixture self-contained.

## Commands and evidence

External local runner: /tmp/pm-holdings-qa/run_isolated.py. Full run:

```
PM_QA_LOCAL_SUBPROCESS=1 /Users/liuxie/.pyenv/versions/3.12.13/bin/python3 /tmp/pm-holdings-qa/run_isolated.py tests -q
```

Deterministic full run substitutes run_nav_clock.py, which adds only the external
qa_nav_clock.py fixture for the two dated NAV tests. Raw logs:

- /tmp/pm-holdings-qa/baseline.log and baseline-report.md
- /tmp/pm-holdings-qa/final-independent.log and baseline-green.log
- /tmp/pm-holdings-qa/full-suite.log
- /tmp/pm-holdings-qa/runner-compatibility.log
- /tmp/pm-holdings-qa/full-suite-compatible.log
- /tmp/pm-holdings-qa/base-nav-date.log
- /tmp/pm-holdings-qa/nav-frozen-date.log
- /tmp/pm-holdings-qa/full-suite-deterministic.log

Isolation retains network/private-file guards, scrubbed environment and temporary
data. The initial runner's unconditional pytest entry under multiprocessing and
forced config variable were corrected only in the external harness. By default
real subprocesses remain blocked; the compatibility flag permits only audited
fake temp scripts, read-only git object checks, plutil lint, install help, and
lockf/sleep/true fixtures. Python children inherit sitecustomize guards. Native
OS sandbox setup was unavailable (Operation not permitted), not retried or
escalated; this is not claimed to be an OS sandbox for arbitrary native code.
No arbitrary/native service commands were allowed. An initial explicit pytest
plugin-loading attempt was blocked by private package metadata discovery; direct
local plugin registration resolved it without broadening private file access.

## Independent code review

Artifact: docs/reviews/code-review-20261026-025619.md (local ignored artifact).
Decision: pass for local code review/QA, no unresolved substantive finding.
QA-01 invalid-currency keep/NAV waiver: accepted and 已修复, independently checked
at resolve, durable keep and typed conversion; valid EUR keep behavior retained.
Implementation owner and reviewer were separate. Aggregate deepreview is next,
not implied by this review.

## Classified remaining limits

- Two pre-existing current-date NAV test failures: assigned to separate maintainer
  test-fixture follow-up; deterministic supplemental acceptance documented above.
- Existing remote compare-and-set window: assigned to later work unit, unchanged.
- Live provider/production acceptance and remote CI/public delivery: not run,
  requiring later explicit authorization.
- Currency snapshot and future typo rules: maintainer-reviewed policy updates;
  no live catalog or fuzzy correction in this change.
