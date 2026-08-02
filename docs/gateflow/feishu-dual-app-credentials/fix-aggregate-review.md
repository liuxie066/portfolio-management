# Gateflow Fix Artifact — Aggregate DeepReview

- Gate: `fix`
- Work unit: `feishu-dual-app-credentials`
- Review: `docs/reviews/code-review-20260802-015353.md`
- Status: `fix complete; pending aggregate re-review`

## Finding decision and fix

### AGG-CR-01 — accepted — fixed

Every generated credential-bearing service now retains its least-privilege
`LoadCredentialEncrypted` grants and wraps every business `ExecStart` with
`/usr/bin/env`. The wrapper sets both
`PM_REQUIRE_SECURE_FEISHU_CREDENTIALS=1` and the exact system-service credential
directory `/run/credentials/<unit-name>` after systemd has assembled the shared
environment. A preserved or subsequently edited `EnvironmentFile` can no longer
disable secure mode or redirect credential lookup for the executed process.

The documented transient Base-subscription command applies the same final exec
override for its exact one-shot unit name. Regression coverage preserves a
deliberately conflicting shared env and proves that every generated service,
including both preflight commands, has the authoritative final exec prefix.
