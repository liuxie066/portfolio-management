# Gateflow Implementation Artifact — S4

- Gate: `implementation`
- Work unit: `feishu-dual-app-credentials`
- Slice: `S4 — operator contract, migration, and full verification`
- Base: `4185aa2`
- Status: `accepted after deepreview 20260802-014947`

## Implemented scope

- Updated the existing public configuration example to expose exactly the
  Bitable and Conversation App IDs plus the Conversation open ID, without any
  plaintext App Secret field.
- Updated the existing README and Linux/service runbooks to define exactly two
  Feishu roles: Bitable owns Base API access, Base subscription, and the record
  listener; Conversation owns outbound receipts and messages.
- Documented the two encrypted systemd credential names, no-third-app rule,
  minimum role permissions, non-secret OM alias migration, and the explicit
  rejection of plaintext fallback in secure mode.
- Documented a staged migration with independently authorized install/apply,
  credential rotation/provisioning, preflight, Base subscription, listener
  activation, canary, rollback, and plaintext cleanup boundaries.
- Added a credential-aware one-shot subscription command that loads only the
  Bitable credential. It preserves the accepted wire contract:
  `file_type=bitable`, no outbound `event_type`, and the exact inbound record
  change registration.
- Added documentation contract tests so the two-role and secret-handling
  boundaries cannot silently regress.

## Evidence

```text
python3.12 -m pytest -q -p no:cacheprovider tests/test_install_linux.py
31 passed

PYTHONPYCACHEPREFIX=/tmp/pm_feishu_dual_app_pycache \
  python3.12 -m pytest -q -p no:cacheprovider
1102 passed

PYTHONPYCACHEPREFIX=/tmp/pm_feishu_dual_app_compile \
  python3.12 -m compileall -q src scripts tests
passed

bash -n scripts/install.sh
passed

git diff --check
passed
```

The secret-placement audit found only constructor parameter names in runtime
source; no App Secret value or plaintext secret field was added to a public
configuration artifact.

## Non-actions

- No real App Secret was used, stored, displayed, encrypted, copied, rotated,
  or deleted.
- No release, remote upgrade, systemd install/apply, Feishu subscription,
  listener activation, canary, or plaintext cleanup was performed.
