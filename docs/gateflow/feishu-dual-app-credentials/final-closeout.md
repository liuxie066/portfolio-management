# Gateflow Final Closeout — Feishu Dual-App Credentials

- Gate: `final closeout`
- Work unit: `feishu-dual-app-credentials`
- Branch: `codex/feishu-dual-app-credentials`
- Base: `main@59625b0d6c666da338c0a520e85a221932846949`
- Draft PR: `https://github.com/liuxie066/portfolio-management/pull/44`
- Accepted PR review commit: `f60865b50697a66f85d8579397fa98f1066b2d07`
- Artifact path: `docs/gateflow/feishu-dual-app-credentials/final-closeout.md`
- Status: `final closeout pass`

## What changed

- Defined exactly two Feishu application roles: Bitable for Base API access,
  document subscriptions, and record-change listening; Conversation for outbound
  receipts/messages.
- Added canonical role-named configuration and same-role migration aliases with
  no cross-role fallback.
- Added strict named systemd credential-file resolution, fail-closed secure mode,
  redacted diagnostics, and final `ExecStart` overrides that cannot be weakened
  by the shared `EnvironmentFile`.
- Routed every Base/event consumer to the Bitable role and every receipt sender
  to the Conversation role.
- Added least-privilege credential grants for every generated service and a
  disabled local preflight service.
- Stopped importing the options-monitor App Secret; only the two non-secret
  conversation identity values remain eligible compatibility inputs.
- Preserved plaintext legacy keys only as reported migration shadows requiring a
  separately authorized cleanup after canary validation.

## Verification

- Focused configuration/installer/CLI regression suite: `108 passed`.
- Full Python suite: `1103 passed`.
- `python3.12 -m compileall -q src scripts tests`: passed.
- `bash -n scripts/install.sh`: passed.
- `git diff --check`: passed.
- Slice DeepReviews S1-S4: passed after accepted fixes.
- Aggregate DeepReview: passed after the accepted systemd environment-precedence fix.
- PR review: PR-CR-01 fixed and re-reviewed.
- GitHub Quality Contract run 45 on accepted PR review head `f60865b`: success.

## Documentation

- Updated `README.md`, `config.example.yaml`, Linux deployment/service runbooks,
  event-listener guidance, the repository skill, and the Cash Flow receipt
  contract.
- Documented exact app roles/permissions, encrypted credential names,
  provisioning, local preflight, subscription, activation, canary, rollback,
  rotation, and the separate plaintext-cleanup authorization boundary.

## Finding status

- Plan review findings: all accepted findings fixed and re-reviewed.
- Slice review findings: all accepted findings fixed and re-reviewed.
- Aggregate finding AGG-CR-01: fixed and re-reviewed.
- PR finding PR-CR-01: fixed and re-reviewed.
- Blocking open questions: none.

## Remaining risks and owners

- Real credential provisioning/rotation, production systemd apply, Feishu
  permission checks, Base subscription, listener activation, receipt canary, and
  plaintext cleanup were intentionally not performed. Owner: production operator
  following `docs/deploy-linux.md`, with a separate authorization at every named
  boundary.
- Any App Secret previously disclosed in chat or plaintext must be rotated before
  migration. Owner: Feishu application administrator; the disclosed value must
  not be reused.
- The current implementation is a Linux/systemd product deployment path, not a
  multi-tenant hosted secret service. Owner: a later product work unit if hosted
  multi-user onboarding becomes an approved requirement.

## Issue link and closeout comment status

- This work unit was not created from a GitHub issue; no issue link, closing
  keyword, or issue closeout comment is required.

## Next entry point

- The Draft PR remains intentionally not ready for review. The next authorized
  action is for the user to inspect PR #44 and explicitly decide whether to mark
  it ready and merge it. Release, remote upgrade, credential provisioning,
  service activation, subscription, canary, and plaintext cleanup remain separate
  later actions.
