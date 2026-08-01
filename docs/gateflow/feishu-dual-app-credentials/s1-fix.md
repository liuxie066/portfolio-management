# Gateflow S1 Fix — Canonical Feishu Roles and Credential Resolver

- Gate: `fix`
- Work unit: `feishu-dual-app-credentials`
- Slice: `S1`
- Review artifact: `docs/reviews/code-review-20260802-011916.md`
- Status: `fix complete; pending re-review`

## Finding decisions

### S1-CR-01 — accepted — fixed

The secure-mode parser now recognizes explicit true and false sets, treats
unset/empty as false for development compatibility, and raises the redacted
`invalid_secure_mode` error for any other non-empty value. Deploy validation
records the issue, fails closed, and does not select a plaintext secret.

### S1-CR-02 — accepted — fixed

Credential IO and decode failures now suppress their original exception in
rendered tracebacks. Tests cover invalid UTF-8 and a forced open failure and prove
that neither credential paths nor fixture bytes appear.

### S1-CR-03 — accepted — fixed

Canonical and legacy Bitable/conversation App IDs, App Secrets, and open IDs are
all permanently non-disclosable. `--show-secrets` remains available for unrelated
legacy operator behavior but cannot reveal either Feishu role identity.

## Additional hardening

When secure mode has no credential, the resolver detects plaintext source
presence before building plaintext value candidates. It therefore classifies the
failure without selecting environment/YAML secret values.

## Validation

```text
python3.12 -m pytest -q -p no:cacheprovider tests/test_config.py tests/test_pm_cli.py
74 passed
git diff --check
pass
```

## Residual risks

- Real systemd injection remains S3/deployment evidence.
- No S1 finding is deferred.
