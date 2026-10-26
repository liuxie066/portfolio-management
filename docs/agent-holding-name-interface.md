# Agent single-record name interface

This interface is **disabled by default**. Adding the code does not provision an
Agent identity, enable access, change listening addresses, or deploy credentials.
Activation requires separate approval of each principal and its exact record grants.
The existing loopback restriction remains in effect. Agent credentials are separate
from PM's Feishu credentials and the quality read token; Feishu secrets never leave PM.

The only action is an explicitly confirmed empty **non-Futu HK stock name** completion.
There is no command, URL, account-wide action, arbitrary field patch, currency edit,
notification option or NAV recomputation. Existing names are preserved.

## Trusted server policy

The server reads `agent_operations` from its existing configuration snapshot:

- `enabled`: must be the boolean `true`; absent or false rejects requests.
- `principals`: nonempty list. Each item contains exactly `id`, `token_sha256`, `grants`.
- `id`: unique principal identity; `token_sha256`: lowercase SHA-256 digest of a
  separately provisioned high-entropy bearer credential (never the Feishu secret).
- `grants`: exact `record_id`, `account`, `broker` triples; no wildcard or duplicate
  record grant within a principal. All principals and grants must validate, otherwise
  authentication fails closed. No actual credentials or production grants are supplied
  by this change.

Policy is trusted server configuration, never request data. Configuration loading is
cached by the existing application; editing YAML does **not** promise instantaneous
revocation. A separately approved configuration reload/restart must make revocation
active. Every request checks the active snapshot; changing a principal, token digest or
its grants invalidates its prior previews/results. Other principals cannot use a nonce.
Deployment must use a high-entropy credential: a stored SHA-256 digest does not protect
weak passwords from offline guessing. Provisioning, delivery and rotation are outside
this local implementation's authorization.

## Requests and side effects

All requests require `Authorization: Bearer <agent-access-credential>`. Do not put
credentials in URLs, logs, preview bodies or issue reports. Unknown body keys and
query parameters are rejected; responses have `Cache-Control: no-store`.

1. `POST /api/v1/agent/holdings/{record_id}/name-preview` with `{}` reads exactly one
   row, verifies the configured account/broker before any source lookup, and requests
   exact public HK identity evidence only for an eligible blank name. It returns the
   name/currency, proposed name, source identity/time and field/status/reason blockers.
   No quantities, costs, raw exceptions or full provider payloads are returned.
2. An executable preview returns `preview_id` and a server-controlled five-minute
   expiry. It **writes a local operation snapshot**, so it is not entirely side-effect
   free. It does not modify Feishu, holding cases or receipt outboxes. Existing names
   return `noop`; missing/unproven evidence returns `not_executable` without a nonce.
3. `POST .../name-confirm` with `{"preview_id":"<nonce>"}` durably claims that
   principal/record-bound preview once, locks the record, rechecks identity and the full
   source digest, and verifies fresh source identity/name. Stale or changed evidence
   rejects the operation. Only the `asset_name` business field can be patched; the repository also updates its
   existing `updated_at` transport metadata. The Agent path invalidates local account
   cache entries without fetching any other account records. It uses the existing
   attempt-before-write, digest and readback workflow. Same-record validation cases
   may be materialized, including unrelated blockers, but other fields are not written.
   All receipt enqueue paths are disabled for this operation. An unchanged natural
   self-event also does not enqueue receipts for already-materialized cases.
4. `GET .../name-operations/{preview_id}` reads the durable sanitized result only;
   it does not fetch Feishu, rerun the provider, initialize the database or recover an
   uncertain write. It still requires the matching current principal/grant.

Repeated confirmation returns the same terminal result without another write.
Concurrent confirmations cannot both claim one preview. Independent previews share
existing record locks and source checks. `executing` means a request may still be
running or may have crashed; `unknown` means its write/readback could not be proven.
Neither state is automatically retried. Operators must investigate under separate
read/recovery authorization; creating another preview does not bypass an uncertain
name case. No manual case or database alteration is recommended.

## Limits and ownership

This namespace is separate from the immutable published OM contract. Agent routes
appear in live OpenAPI, while the OM exporter excludes `/api/v1/agent/`.

Existing Feishu non-atomic read/PATCH timing remains a storage limitation; the normal
locks and precondition rechecks still apply. The retained operation table has no new
background cleanup task; retention/pruning is a future maintenance decision. Expired
previews cannot execute, and unknown operations remain for audit. Live provider health
and production activation require separate acceptance; local tests mock external I/O.

The operation-state change is an additive `agent_name_operations` table, created by
normal state-store initialization. A status request against an older database with no
such table returns unavailable without migration or file writes. Existing holding-case
schema and worker state contracts are unchanged. Deploying default-off code does not
create an Agent token, grant, configuration entry or persistent access; enablement and
any service/configuration change must be approved separately.

HTTP status separates rejected requests from recorded operation outcomes: an expired
unclaimed preview returns409; after a claim, stale source/evidence is a durable
`state: rejected` operation result returned with200 (and identically on replay).
A200 response is therefore not proof of a successful patch; callers must inspect
`state` and `reason`, and treat `unknown`/`executing` as requiring attention.
