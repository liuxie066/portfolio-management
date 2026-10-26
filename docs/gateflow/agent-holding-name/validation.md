# Validation ledger — Agent single-record name interface

## Before implementation
Independent QA preserved synthetic baseline `/tmp/pm-agent-qa/baseline.log`:
1 expected failure in0.42s. Proposed POST name-preview returned404 at base931fc08;
required disabled-by-default endpoint should return503 without backend access.
This proves the entry is absent, not production behavior.

## Isolation
External `/tmp/pm-agent-qa/run_isolated.py` is the prior audited launcher adapted
only to this worktree and its own harness directory. It scrubs environment,
uses temporary data, disables pytest plugin auto-loading and blocks real network,
private user files and unapproved subprocesses. Python3.12.13 explicit; no
production/credentials/real service test. Full suite's fixture-only subprocess
allowlist is opt-in and unchanged. Filesystem /tmp had52GiB available pre-test.

## Pending
Implementation focused tests, independent QA, full regression, lint/compile/diff,
code review and aggregate deepreview not yet completed. Prior work-unit counts
are not acceptance evidence for this new implementation.

## Existing baseline limitation
Two tests in test_nav_public_freshness.py use fixed September dates against a
30-day current-date query; previous work proved both fail at untouched base.
If still failing here, preserve native results and separately label any external
fixture-clock supplemental run; never describe conditional green as native green.

## Frozen-source acceptance results
- Implementer focused suite:113passed1.95s (`implementation-final-focused.log`).
- Independent QA round2:30passed; expanded round3:38passed0.85s (final reviewer artifact pending).
- Full native required suite:1764passed,2failed13.85s (`full-native.log`). Only unchanged test_nav_public_freshness fixed-date baseline failures; no new failure.
- Supplemental full suite with external clock fixture scoped only to those two NAV tests:1766passed12.27s,0failed/skipped (`full-scoped-clock.log`). This is not unconditional native green.
- compileall required source targets using Python3.12.13 and /tmp/pm_agent_pycache:passed.
- Ruff required source targets:All checks passed. git diff --check:passed.
- Published OM contract compatibility regression included in focused/full suites; no published contract or manifest changes.

Commands: `PM_QA_LOCAL_SUBPROCESS=1 /Users/liuxie/.pyenv/versions/3.12.13/bin/python3 /tmp/pm-agent-qa/run_isolated.py tests -q`; supplemental replaces runner with run_nav_clock.py. No repository test/product clock modified. Existing previous baseline reproduction remains applicable because dated tests/NAV-history implementation are unchanged.

Preserved initial failures: missing endpoint baseline1expectedfailure; focused test collection import error; implementation case-descriptor failures; independent round1 six failures/thirteen passes. Case-descriptor requirement was fixed without enabling receipt enqueue. Pre-provider identity ordering race and read-only old-schema status were fixed and tested. Full raw logs remain under /tmp/pm-agent-qa; no production data in tests.

Final independent QA:39passed0.81s, independent-final.log. Code/docs deepreview pass-with-risks, docs/reviews/code-review-20261026-122039.md; no unresolved substantive finding. QA-01 descriptor missing and controller identity-ordering finding accepted/已修复. Old-schema status read byte-preservation checked. Current final transport clarification documented:200 durable rejected result after claim, not claimed write success.

## Aggregate correction AGG-01 (supersedes pre-fix acceptance)
Real FeishuStorage/repository independent test reproduced account-wide postpatch fetch:1failed0.39s, real-adapter-before.log. Accepted fix adds explicit scoped cache behavior, default old path unchanged. Fake storage had missed this boundary; initial39-test pass was insufficient for transport scope and is not final evidence.

After fix: developer174passed2.11s; independent40passed0.82s zero skipped (independent-agg01-final.log). Real client forbids list_records and verifies exact target GET/PATCH, only asset_name plus pre-existing updated_at metadata, other rows/fields unchanged, other-account cache retained, outbox unchanged, replay no I/O. One intermediate QA assertion inspected facade bool instead of repository-owned flag; corrected test assertion recorded without changing product behavior.

Final native full:1765passed2failed13.34s, same untouched baseline date failures (full-native-final.log). Requiredcompile/Ruff/diff checks passed after fix. Final supplemental scoped-clock run pending. Independent code-review122039 updated accepted/已修复; aggregate reviewer rechecks fix before gate acceptance.

Final supplemental scoped-clock full:1767passed12.45s,0failed/skipped (full-scoped-clock-final.log). Aggregate reviewer strengthened shipped regression: forbidden list_records now records attempted call before raising, because workflow may catch and prove PATCH success via readback. Targeted Agent suite after this test-only assertion fix:18passed0.81s (aggregate-test-hardening.log). Product source unchanged from final full runs; no redundant full rerun claimed.
