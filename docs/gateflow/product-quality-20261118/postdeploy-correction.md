# Post-deployment report transport correction

## Goal and evidence

The additional E2E acceptance of v0.2.15 found that the native HTML publisher,
when requiring the local HTTP service, receives HTTP 500 from `/report/daily-bundle`.
FastAPI attempts to serialize the private issuer object inside the normalized
valuation dataclass. The affected transport/application/domain files are unchanged
from v0.2.14; this is an existing defect discovered by the expanded acceptance,
not a claim that the v0.2.15 deployment failed.

## Plan and ownership

Keep the fix at the HTTP boundary: copy the bundle and snapshot, omitting only
`normalized_valuation`. The direct Python service retains the exact object and its
write capability; the report, valuation, holdings, NAV and failure fields keep their
existing values. Neither the publisher nor the CLI consumes the internal object.
Do not stringify the capability, expand its contents into a new wire contract, or
modify accounting/authorization logic. The independently assessed minimal plan was
accepted; no new framework, dependency, cache, schema migration or OM API change.

Standing authorization covers local synthetic fixes, checks, commit/push and PR.
The completed v0.2.15 release/tag and production installation are not rewritten.
Any subsequent release is a separate version-bound delivery decision.

## Verification

Before fixing, the focused official-snapshot regression reproduced the serializer
exception. The existing adapter fixtures needed the native cash asset-class value,
an empty holdings_snapshot table and correctly placed CLI global arguments;
completed cash-flow effects correctly reject another confirm instead of returning
success. These harness corrections did not change product behavior.

After fixing, three focused regression cases and three real-loopback/CLI E2E cases
passed (6 total): fresh capital facts plus source-outage refusal, the native publisher
through HTTP to self-contained HTML, durable queue completion and reopen/duplicate
admission with one synthetic outbox delivery, and cash-flow preview/fresh final-NAV
gate/confirm/reopen/duplicate refusal. External broker, Feishu storage and message
sink are synthetic. No production write or real message was used.

Full tests: 2,020 passed. Required compilation, Ruff 0.15.14, OpenAPI drift and diff
checks passed. The Starlette/httpx deprecation remains a dependency warning.
`docs/service.md` documents the transport boundary; README positioning and setup
are unaffected. Original release evidence and new E2E logs remain under the task's
ignored `reports/product-quality/` directory; candidate passes are not presented as
passes on the currently deployed v0.2.15.

## Remaining boundary

The production natural quality refresh generated 24 trusted datasets and zero
incidents, and the native CLI read exactly the persisted artifact. Anonymous HTTP
access was rejected with 401. Authenticated HTTP status was not exercised because
the isolated verifier did not have the configured token. Real ledger writes,
broker operations and external message delivery remain intentionally untested.
The service-mode report E2E remains blocked on v0.2.15 until this correction is
reviewed and included in an explicitly bound subsequent release/upgrade.

Independent full review: [no substantive findings](review-postdeploy-correction.md). All findings are closed; no fix/re-review round was needed. The remaining deployed-version blocker is explicitly retained above, not waived.
