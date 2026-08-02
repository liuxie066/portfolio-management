# Gateflow Fix Artifact — S4

- Gate: `fix`
- Work unit: `feishu-dual-app-credentials`
- Slice: `S4 — operator contract, migration, and full verification`
- Review: `docs/reviews/code-review-20260802-014847.md`
- Status: `fix complete; pending re-review`

## Finding decisions

### S4-CR-01 — accepted — fixed

The migration state now separates non-system checkout/venv preparation from
deployment apply. It rotates and provisions both encrypted credentials before
applying the credential-bearing config/env/units, matching the installer's
fail-closed presence check. A documentation contract test locks that order.

### S4-CR-02 — accepted — fixed

The Conversation permission table now requires the exact least-privilege
`im:message:send_as_bot` application permission and no longer authorizes an
unspecified equivalent. A documentation contract test locks the exact scope and
rejects the old ambiguous phrase.
