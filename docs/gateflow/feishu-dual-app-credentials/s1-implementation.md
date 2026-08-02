# Gateflow S1 Implementation — Canonical Feishu Roles and Credential Resolver

- Gate: `implementation`
- Work unit: `feishu-dual-app-credentials`
- Slice: `S1`
- Status: `accepted after code re-review 20260802-012155`

## Scope

- Added canonical Bitable and conversation configuration keys with same-role
  legacy aliases.
- Added strict systemd credential-file reads for the two App Secrets.
- Added secure-mode enforcement, redacted typed failures, plaintext-shadow
  warnings, non-secret identity conflict detection, and permanent non-disclosure
  for Feishu App Secrets.
- Added `pm config doctor --require-secure-feishu`.
- Did not change Feishu clients, event adapters, receipt services, installer,
  business workflows, network behavior, or production state.

## Changed files

- `src/configuration/__init__.py`
- `src/configuration/feishu_credentials.py`
- `src/config.py`
- `scripts/pm.py`
- `tests/test_config.py`
- `tests/test_pm_cli.py`

## Contract decisions

- Credential files are capped at 4096 bytes, may contain one final LF, and reject
  NUL, embedded newline/CR, invalid UTF-8, symlink, non-regular, empty, and
  oversized inputs.
- Files are opened with `O_NOFOLLOW` where available and validated again with
  `fstat` after opening.
- A valid credential wins. Legacy secret keys then produce only a redacted
  presence warning; their values are not selected or compared.
- Secure mode rejects plaintext fallback or a missing credential.
- App ID/open_id canonical-vs-legacy disagreement fails redacted; equal duplicate
  configuration is accepted with a migration warning.
- `--show-secrets` never reveals either Feishu App Secret.

## Validation

```text
python3.12 -m compileall -q src/config.py src/configuration scripts/pm.py
python3.12 -m pytest -q -p no:cacheprovider tests/test_config.py tests/test_pm_cli.py
70 passed
git diff --check
pass
```

## Residual risks

- Canonical consumers are intentionally moved in S2; legacy callers resolve
  through the canonical alias boundary meanwhile.
- systemd unit injection and capability probing are covered by S3.
- Real systemd credentials and Feishu calls are outside this slice.

All residual risks are covered by later approved slices.
