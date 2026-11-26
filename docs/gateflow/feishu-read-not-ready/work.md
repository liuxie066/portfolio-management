# Feishu MMF read-not-ready recovery

## Confirmed scope and plan — 2026-11-26

Base: 6575e2e. Branch: cindy/earnest-jang. User approved dry-run, bounded
read retries, and recovery of lx real sync; after seeing the preview, explicitly
accepted the MMF and pending-asset balance differences. Commit/push, PR, merge,
release and production upgrade were separately authorized by the user after
verification: commit/push, PR merge to main, release 0.2.32, Aliyun upgrade and
lx real-sync recovery. Authorization was received before starting delivery.

Incident: run futu-sync-bd5402b0b026406b923d68db1382ff78, Feishu log ID
20261126171102123D92DA8A82F8621501. Aliyun journal confirms a successful PUT
at 17:11:02, followed by GET HTTP 400/code 1254607. Subsequent reconciliation
matched MMF but found pending assets 97922.63 vs 97839.20; pending stage had
not run. Upstream cause of data-not-ready is unknown.

Authorized read-only lx dry-run on Aliyun completed successfully, run
futu-sync-65f5bdd0b3224d2eae302f140029bda3, observed 2026-11-26T09:21:44Z.
No position quantity/cost or cash changes. MMF 446766.88 -> 446835.30 (+68.42),
pending 97922.63 -> 97841.81 (-80.82). No business writes or receipts sent.
These differences were shown to and accepted by the user before implementation.

One slice: extend the existing shared Feishu GET retry policy to the observed
HTTP 400/integer code 1254607 pair. Reuse the 3-retry cap, 1/2/4-second backoff,
server wait hints, deadline budget and sanitized diagnostics. Do not replay
writes or the whole sync; keep final fresh readback and failure semantics.
Shared ownership belongs to the transport rather than an MMF-specific loop:
other GET callers receiving the same transient error benefit from this policy.
No new API/schema/config/dependency or concurrency mechanism.

Plan check: small extension to existing transport policy, no important contract
or architecture decision requiring separate plan review. Regression tests cover
same-page recovery, bounded exhaustion, wait hints/deadlines, rejection of other
status/code pairs and writes, and successful PUT followed only by GET retries.
Run project full pytest, compileall, Ruff, OpenAPI drift and whitespace checks,
then independent complete code review. Runbook owns behavior; README capability
and installation instructions are unaffected. LESSONS records the proven incident.

## Implementation and verification

Baseline: expanded transport regressions failed 14 cases on old code (33 passed).
After the policy extension: 48 transport tests passed. The initial added write
fixture lacked its response fields; corrected the fixture to match the API shape.
Full verification on Python 3.12.13: 2268 passed, one Pydantic warning about
Field alias metadata in test_futu_refresh_inbox, 20.75 seconds.
Log: /tmp/pm-1254607-full-pytest-unrestricted.log.
Initial sandbox execution had 2258 passed and 10 socket-bind permission failures
(log: /tmp/pm-1254607-full-pytest.log); rerunning the same full suite with local
test-server permission passed. Compileall, full project Ruff, OpenAPI drift
check and git diff --check passed. No product service or external account was
used by these tests.

Two independent complete code reviews found no material findings:
- docs/reviews/code-review-20261126-172945.md
- docs/reviews/code-review-20261126-172947.md

Controller read both artifacts and the final diff: no accepted findings require
fixes or re-review. Both reviews trace MMF PUT -> fresh GET -> repository proof
-> pending stage and preserve the shared transport ownership. No source changed
after review; only this verification record was completed. No extra configuration,
public contract, dependencies or README changes are required.

## Delivery and production recovery

Current step: development verification and complete code review passed; authorized
delivery in progress. Refreshed origin/main equals base 6575e2e, local main is
0/0 ahead/behind; latest published release and Aliyun active version are 0.2.31.
Target 0.2.32 has no existing local tag or production directory.
Next: submit the reviewed fix and release metadata via PR, verify CI and exact
head/base before merging, publish tag/Release/assets, prepare and activate the
verified package through the controlled upgrade, then preview and sync lx.
Do not deploy a branch or untagged commit directly to Aliyun. Last checked active
production release is 0.2.31, with API/listener active. Bound target patch is
0.2.32. The controlled upgrade
entry is `pm service upgrade`; no restart or production activation performed.

Residual risks: upstream may remain unready beyond bounded retry; owner PM
operator, destination current failure/dry-run workflow. Real balances may change
between preview and recovery, so obtain a fresh preview before writing. Production
fix and real-sync acceptance are not yet performed and must not be reported passed.
