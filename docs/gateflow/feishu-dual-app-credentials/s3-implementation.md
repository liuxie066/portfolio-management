# Gateflow Implementation Artifact — S3

- Gate: `implementation`
- Work unit: `feishu-dual-app-credentials`
- Slice: `S3 — systemd credential wiring and installer safety`
- Base: `61ff84f`
- Status: `accepted after deepreview 20260802-014013`

## Implemented scope

- Generated service units set secure Feishu credential mode and receive only
  the Bitable and/or Conversation credential required by the accepted unit grant
  matrix.
- A disabled `portfolio-feishu-preflight.service` loads both credentials and
  runs only secure config doctor plus local event status.
- Apply mode verifies both encrypted credential names through regular-file
  metadata and proves the exact `LoadCredentialEncrypted` syntax with
  `systemd-analyze verify` before any installation target is created or changed.
- Dry-run reports that capability is required but does not claim verification.
- The installer imports only non-secret OM conversation App ID/open_id values.
  Legacy plaintext secret key names are reported as migration shadows without
  their values and are never silently removed.
- The generated YAML contains canonical non-secret role fields and no App Secret
  field. No installer option accepts credential contents.

## Evidence

```text
python3.12 -m pytest -q -p no:cacheprovider tests/test_install_linux.py
29 passed

python3.12 -m compileall -q scripts/install_linux.py tests/test_install_linux.py
passed

bash -n scripts/install.sh
passed

git diff --check
passed
```

Coverage includes the exact unit grant matrix, preflight non-mutation, missing
and symlinked credential rejection, supported/unsupported capability ordering,
clean idempotent rendering, partial non-secret migration, legacy shadow
reporting, and non-disclosure in plan output.

## Non-actions

- No credential was created, encrypted, decrypted, read back, copied, printed,
  or deleted.
- No systemd target, service, timer, subscription, Feishu connection, release,
  or remote environment was changed.
