# Gateflow Plan Fix — Feishu Dual-App Credentials

- Gate: `fix`
- Work unit: `feishu-dual-app-credentials`
- Review artifact: `docs/reviews/plan-review-20260802-010657.md`
- Status: `fix complete; pending re-review`

## Finding decisions and fixes

### PR-01 — accepted — fixed

Secret alias handling now distinguishes a selected secure credential from a
legacy plaintext shadow. When the credential exists, legacy secret keys are
detected by presence only and are neither read nor compared; they produce a
redacted warning and do not block canary. Secure mode fails only if it would need
to fall back to plaintext. Non-secret App ID/open_id conflicts remain fail-closed.

### PR-02 — accepted — fixed

The unit matrix now follows the real command graph. Cash-flow scan loads the
conversation credential because it immediately dispatches pending receipts. The
API loads it because existing Futu/NAV application use cases may send receipts in
request scope. No business behavior is moved merely to reduce grants.

### PR-03 — accepted — fixed

The S2 validation command now uses existing receipt/workflow test files:
`test_holdings_workflow_service.py` and
`test_operation_receipt_outbox_service.py`. The nonexistent test paths were
removed.

### PR-04 — accepted — fixed

The credential payload contract now defines a 4096-byte maximum and exact 4096/
4097 boundary tests. Apply mode must verify `systemd-creds` availability and run
`systemd-analyze verify` on a temporary rendered credential-bearing unit before
any install write. Feature detection is used instead of assuming support from a
version number; final installed-unit verification remains a deployment gate.

## Residual risks

- systemd credential ciphertext portability remains assigned to a later recovery
  work unit.
- Live systemd and Feishu behavior remains a separately authorized deployment
  canary; local tests do not claim that evidence.

All residual risks are classified and no plan finding is deferred.
