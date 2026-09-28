# Daily NAV date and read consistency

## Goal and authorization

Keep the automatic NAV label on the latest eligible CN/HK/US trading date
strictly before the run date. A final NAV may be written only when its holdings,
prices, and FX can be tied to that target date. Missing or ambiguous evidence
blocks that account's NAV write. Make the public NAV API observe a successful
write from the separate scheduled process without an API restart.

This design covers source behavior and tests only. It does not authorize
rewriting existing `nav_history`, changing production timers, restarting
services, release, or remote upgrade. The 2026-09-25 rows require a separate
read-only audit and an explicitly authorized correction if their values are
wrong.

Success signals: an unproved target-period input cannot become a final daily
NAV; a fully proved target-period artifact can use the existing writer; and a
public NAV read sees a separate-process successful write without a restart.

## Observed facts

- On 2026-09-28 the morning job wrote three final rows labelled 2026-09-25.
  Their `valuation_as_of` and all persisted quote observation dates are
  2026-09-28. Observation time is not a market price fact date, so numerical
  error is not yet proven; target-date input provenance is not proven either.
- The job resolves the preceding trading date through
  `BusinessCalendarService.default_nav_date`. The wrapper synchronizes lx/sy
  holdings first. `PortfolioReadService.build_snapshot` values the holdings
  and quotes available at run time. `NavWriteContext` validates timestamp
  syntax and target date independently, without comparing their economic
  periods.
- The existing historical valuation builder can obtain Futu daily closes and
  fund NAVs for a requested date, but it requires explicit FX inputs and a
  validated holdings source. Its receipt fallback is designed for a failed
  historical run, not arbitrary routine backdating. `fetched_at` and
  `source_fetch_time` mean retrieval time, not effective trading date.
- Production's local NAV index contains 2026-09-25 for lx/hb/sy while the
  long-lived API returns 2026-09-24. The API process started on 2026-09-26.
  `NavHistoryRepository.get_nav_history` trusts a populated process-memory
  index; `LocalNavIndexCache` loads disk only when constructed. HTTP has one
  long-lived `PortfolioService`. A separate process cannot invalidate it.

## Reuse and owners

Search covered `BusinessCalendarService`, `DailyNavJobService`,
`AccountNavRecorderService`, `NavWriteContext`, `NavRecordService`,
`NavValuationEvidenceStore`, `HistoricalNavValuationEvidenceService`,
`ValuationService`, `NavHistoryRepository`, `LocalNavIndexCache`, the scheduled
wrapper, and their tests/docs. No existing routine target-date holdings or FX
fact source was found; that absence is a delivery constraint, not permission
to infer one from a retrieval timestamp.

| Need | Owner decision |
| --- | --- |
| Date eligibility | Reuse `BusinessCalendarService`; any confirmed CN/HK/US opening still qualifies. |
| Final write admission | Reuse `AccountNavRecorderService` and `NavWriteContext` for the final daily writer. |
| Historical price facts and immutable replay | Reuse `build_historical_price_snapshot` and `NavValuationEvidenceStore`; extend only after source evidence is established. |
| Canonical NAV read | Reuse `NavHistoryRepository.preload_nav_index(force_refresh=True)` and the existing public service facade. |
| New fact-date evidence | Add the minimum typed validation at the final write boundary; retrieval timestamps must not substitute for fact dates. |

## Proposed behavior

### Final NAV evidence gate

The NAV date is a market trading-date label. It is not required to equal the
Beijing date of retrieval: US sessions can close on the next Beijing day.
For a final daily write, the date must have a confirmed opening in at least
one of CN/HK/US. `force_non_business_day` may bypass the job's early skip for
an operator workflow, but never makes an all-closed date eligible for a final
daily NAV.
Before `Portfolio.record_nav` receives a `daily-nav-job` final write, require
evidence bound to `(account, nav_date, holdings digest, price facts, FX facts)`.
The gate applies to automatic and explicit-date daily jobs and their HTTP/CLI
entry points. It runs after existing holdings/cash-flow preflights but before
NAV or NAV-linked holdings-snapshot persistence. Existing preflight sync and
materialization can occur first and must be reported; a blocked result must
never claim that the run made no writes of any kind. Dry-run reports the same
evidence decision without persisting NAV. Manual, initial, close, and maintenance
writers retain their current classification; they cannot be passed as final
daily evidence.

Define the target cutoff as the latest close instant, converted to UTC, among
the CN/HK/US markets confirmed open on `nav_date`. The evidence must identify
that instant, each market's local trading date and session close, and the
source's effective as-of or publication time. Target-period holdings must
include every trade and cash movement effective through the cutoff and exclude
later events; source completeness through that cutoff is required. FX and
continuous-value assets such as crypto need a rate or value effective at the
cutoff under a documented source policy. A calendar date, `fetched_at`, or a
same-date timestamp alone cannot prove this coverage. If any required source
cannot make that assertion, the account remains blocked.

The admission contract is a validated fact bundle, not a second valuation
formula. Its minimum fields are:

| Fact | Required binding |
| --- | --- |
| Scope | `account`, `nav_date`, cutoff instant, source identity, immutable bundle digest. |
| Holdings | Normalized holdings digest used by the valuation, effective-as-of instant, complete-through instant, and source receipt or snapshot identity. |
| Each price | Asset identity, market, value/currency, source, actual market `fact_date`, publication or close instant, and matched market calendar decision. |
| Each FX/continuous value | Pair or asset identity, value, source, effective instant, and source policy establishing validity at cutoff. |

Validation requires `complete_through >= cutoff` for holdings, no holdings
event after cutoff in the valued snapshot, and a fact for every nonzero holding
and required currency conversion. Check the bundle against the actual
normalized valuation before finality; missing, extra, conflicting, or
unparseable facts fail closed. The existing `NavValuationEvidenceStore` remains
the immutable carrier. A future bundle schema may add these fields, but old
artifacts receive no implicit upgrade or assumed effective date.

For each priced holding, the evidence must identify the source and economic
fact date. For a market open on `nav_date`, require that market's target-date
close; for a market closed that date, use its last confirmed trading close on
or before `nav_date`. The closed-market path must first prove closure, query
that market's latest confirmed open session, and save its actual `fact_date`;
the current historical OpenD loader's exact-date query cannot implement this
path unchanged. Fund NAVs may use the last fact published by the cutoff under
the existing historical builder policy. CASH, MMF, crypto, and foreign-currency
conversion require target-period value/FX evidence; a fixed calculation using
an unbound current FX quote is insufficient. Price `fetched_at` alone never
satisfies this gate.

The holdings input must be a durable validated snapshot attributable to the
target period, with the same normalized digest used in the valuation. A
current Feishu read, a run timestamp, or a current Futu sync receipt without
an effective-date assertion does not establish historical holdings. Existing
receipt reconstruction may be reused when its account/date/run/digests are
verified **and** independently proves holdings and FX effective through the
cutoff. The historical holdings digest must match the valuation's holdings;
the fresh current-state holdings preflight remains a separate admission check
and may legitimately have a different digest on delayed replay. If no source
can establish the target period, return
`target_nav_evidence_unavailable` for that account, with missing components;
do not recalculate from current state or silently relabel the NAV. Other
accounts may proceed under the existing per-account job result semantics.

An immutable valuation artifact with bound target facts can be replayed
through the existing `valuation_ref` path after fresh holdings and cash-flow
gates. The final gate must inspect the loaded artifact's individual price,
holdings, and FX facts on every replay, including older v1 artifacts; a valid
artifact hash, account/date scope, or digest alone is not target-period proof.
Legacy artifacts missing these facts are blocked. Duplicate/finality checks
remain unchanged. A second run against an
existing final row must never overwrite it implicitly.

The current sources do not guarantee routine historical holdings and FX facts.
The first safe release may therefore block automatic NAV for accounts lacking
those facts. Producing routine target-date evidence is a separate delivery
slice, contingent on verifying a real source; the design does not invent a
broker effective date or treat Saturday/Monday capture time as Friday proof.
Deploying the gate alone is a deliberate safety stop, not a claim that the
scheduled NAV is restored. Each blocked account/date must expose missing
components and the last successful NAV date in job output or operational logs.
When evidence arrives after the default date advances, operations can use the
existing explicit-date daily-job entry point for a controlled retry after the
same gate; no automatic backfill queue is introduced by this design. Routine
availability is accepted only after the source slice proves normal writes and
delayed catch-up for all supported account types.

### Public NAV freshness

For `GET /api/v1/nav` and `/nav`, read the account's canonical Feishu NAV
index once per request before formatting the response, reusing the existing
`preload_nav_index(force_refresh=True)` fetch and index builder. The public
path must publish the result to this process's memory without writing the
shared disk cache: the current `LocalNavIndexCache` rewrites the whole file
from a process-local copy, so read-triggered writes can race the scheduled
writer. A successful empty result is still one fetch, not a trigger for the
current `get_nav_history` second fetch. This shared service boundary covers
both routes, never returns a known-stale in-memory/disk row on a Feishu
refresh failure, and does not depend on a restart. Keep the repository's
normal cached index for internal calculations; its other consumers and
external direct Feishu edits need a separate freshness contract if they are
required to be live. A canonical fetch can only observe a write once Feishu's
list read exposes it; immediate read-after-write visibility is a live
acceptance question, not an assumed guarantee.
Avoid adding disk file watchers, generation protocols, or polling machinery
without measured API request volume that makes canonical reads too costly.

### Existing 2026-09-25 records

Audit the three persisted rows and their source receipts read-only. Preserve
them and expose their actual `nav_date` and `valuation_as_of`; do not relabel,
delete, or overwrite on deployment. If target-date input facts cannot be
reconstructed, classify them as needing operator review instead of claiming
the recorded `valuation_quality=trusted` proves target-date correctness.
Any repair needs its own preview, account/date scope, authorization, and
readback through the existing NAV repair procedure.

## Rejected shortcuts

- Label the 2026-09-28 morning snapshot as 2026-09-28 NAV: it changes the
  approved previous-trading-date rule and precedes that day's sessions.
- Require `valuation_as_of.date() == nav_date`: retrieval after US close in
  Beijing can legitimately occur the next day; equality proves little.
- Trust fresh quote timestamps or `valuation_quality=trusted` as target-date
  market facts: they do not carry the price's economic date.
- Restart the API after every scheduled job: it repairs one symptom while the
  long-lived cache remains stale after subsequent independent writes.

## Implementation slices and validation

1. **Fresh public NAV reads** (independent): add canonical refresh at the
   shared public service read boundary, without writing the shared disk cache.
   A two-process/stale-memory regression must return the new date; a Feishu
   refresh error must fail closed; populated and empty accounts each need one
   remote fetch; unchanged API response fields must be checked. Success:
   direct API response includes a newly persisted NAV without restart.
2. **Final daily NAV admission** (independent): validate target-period price,
   FX, and holdings evidence before the existing write path, including loaded
   `valuation_ref` artifacts. Test CN-closed / HK-US-open, US close after
   Beijing midnight, target market closed with earlier close, missing FX,
   current-only holdings, legacy replay, all-closed forced dates,
   explicit-date calls, dry-run, preflight side effects, and per-account mixed
   outcomes. Success: unsupported facts produce a visible per-account blocked
   result and zero NAV/NAV-linked snapshot writes; valid bound evidence still
   uses the canonical writer. The job must expose the last successful date and
   missing facts for subsequent explicit-date retry.
3. **Routine evidence source** (depends on 2): only after a provider/receipt
   source is proved to carry target-period holdings and FX facts, adapt the
   existing historical valuation/evidence path for ordinary scheduled runs.
   Add the closed-market latest-confirmed-open close lookup; validate actual
   fact dates and publication/effective times. Test successful first write,
   delayed catch-up with exact saved evidence, and fail-closed behavior when
   no saved evidence exists. Success: eligible dates update automatically when
   facts exist, without substituting current holdings or FX. This slice is
   deferred until source capability is verified.

For implementation, run focused tests per slice and the repository's pytest,
compileall, Ruff, and `git diff --check` gates on the final change. Production
acceptance, if separately authorized, must compare a new canonical NAV read
with the durable row and inspect evidence dates, not just HTTP health.

## Risks and decisions

- Owner: portfolio operations. The 2026-09-25 rows may be economically wrong;
  destination: read-only audit followed by a separately authorized repair.
- Owner: NAV data-source design. Current routine holdings/FX data lack a
  proved target-period source; destination: slice 3 source validation. Until
  then the guard intentionally blocks affected accounts. Source discovery is
  the explicit prerequisite for routine NAV availability, not for safety.
- Owner: API operations. Per-request Feishu reads cost more and fail when
  canonical Feishu is unavailable; destination: measure request rate and
  latency before considering a bounded cache freshness protocol.
