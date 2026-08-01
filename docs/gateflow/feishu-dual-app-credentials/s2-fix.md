# Gateflow Fix Artifact — S2

- Gate: `fix`
- Work unit: `feishu-dual-app-credentials`
- Slice: `S2 — canonical Feishu role consumers`
- Review: `docs/reviews/code-review-20260802-012915.md`
- Status: `fix complete; pending re-review`

## Finding decisions

### S2-CR-01 — accepted — fixed

The compatibility `holdings events status` command now resolves the canonical
Bitable App ID and App Secret independently from its holdings table target.
Credential resolver failures remain in a redacted `credentials.issues` list,
while table/target failures are reported through `target_status`. A missing table
therefore no longer produces a false `app_id_configured=false` diagnosis.

Regression coverage supplies valid Bitable identity values while forcing target
construction to fail, and verifies that identity remains configured, target
status is invalid, the command remains read-only, and no SDK client is created.
