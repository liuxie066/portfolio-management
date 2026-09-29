# Daily NAV date and read consistency

## Goal, scope, and acceptance

Restore the routine daily NAV behavior after the 2026-09-29 failure. An
automatic job labels the record with the latest eligible CN/HK/US trading
date strictly before the run date; an explicit-date job uses the supplied
eligible date. Both value the validated holdings and quotes available when
the job runs. They save the actual `valuation_as_of` and source provenance.
A routine write must not require a historical target-period evidence artifact.

This is the user's 2026-09-29 change to the earlier decision to block final
daily writes without target-period holdings, price, and FX proof. In this
product, `finality.status=final` means the accepted daily portfolio record,
not an audited reconstruction of holdings at every market close. The NAV date
is a trading-date label; `valuation_as_of` is the Beijing-local snapshot
generation time, not the timestamp of every holding or quote observation.
Neither field alone asserts target-close coverage.

Success signals:

1. An eligible date with passing existing preflights records NAV and its
   holdings snapshot even when no `target_period_evidence` exists; dry-run
   reaches the same writer without persisting.
2. CN/HK/US any-open eligibility, confirmed all-closed skip unless the
   existing `force_non_business_day` override is explicit, and unknown
   calendar failure still behave as designed in `docs/nav-trading-calendar.md`.
3. Duplicate/finality checks, validated holdings, cash-flow gates, valuation
   quality, write confirmation, and valuation replay scope/digest checks remain
   effective. The persisted observation time is not replaced by the NAV date.

No production NAV rows, timer settings, release metadata, or deployed services
are changed by this source repair. The automatic job does not backfill
2026-09-28 after its default date advances; any missing-date replay or
correction requires a separate account/date audit and authorization.

## Observed facts and ownership

- `BusinessCalendarService.default_nav_date` selected 2026-09-28 for the
  2026-09-29 morning run. The calendar stage succeeded. All three accounts
  then failed with `target_nav_evidence_unavailable` and
  `missing_components=["target_period_evidence"]` before NAV persistence.
- The 2026-09-28 target-evidence admission introduced in v0.2.0 is called by
  `AccountNavRecorderService.record` for every final `daily-nav-job` write.
  Normal scheduled runs do not create a valuation artifact, so the gate has
  no input. It was an independent policy change after the calendar repair.
- The existing `NavWriteContext` and `NavRecordService` save
  `valuation_as_of` and the valuation quality/provenance in `nav_history`.
  `DailyNavJobService` already owns date eligibility, duplicate checks, and
  per-account results. `AccountNavRecorderService` already runs holdings and
  cash-flow preflights before calling the canonical writer.
- `NavValuationEvidenceStore` still validates explicit replay artifacts by
  account, date, digest, holdings, and cash-flow fingerprint. A historical
  artifact is optional for routine jobs. Its optional
  `target_period_evidence` field is included in artifact digests and must
  remain readable for saved artifacts. The production data inspected on
  2026-09-29 did not contain target-period evidence for the failed date.

Reuse search covered the owners above, `src/app/nav_target_evidence.py`,
`DailyAccountNavService`, the service facade, and related tests. No new
concept, schema, dependency, or parallel writer is needed. Reuse the existing
daily writer and calendar; remove only the mandatory target-period admission
and its now-unused plumbing. Preserve the public NAV read freshness work from
v0.2.0, which is independent of this failure.

## Decision and failure behavior

For the automatic and explicit-date daily job, the calendar first decides
whether the NAV date is eligible. A confirmed all-closed date skips unless the
caller explicitly uses the existing `force_non_business_day` override; an
unknown calendar result fails even with that flag. On an admitted date, the
account runner
builds the current validated snapshot and cash-flow dataset, then sends it to
the existing NAV writer. A missing target-period artifact is not a refusal.
The writer persists the actual snapshot generation time and source/quality
details; it does not relabel them as target-date facts. Explicit replay also
admits older artifacts without `target_period_evidence`, while retaining
artifact scope, integrity, holdings, and cash-flow checks. Existing
failures from holdings, cash flow, prices/FX quality, duplicate NAVs, or
unconfirmed writes continue to block their respective paths. An eligible
confirmed write refused by the cash-flow dataset may still save the existing
replay artifact for an allowed refusal reason; dry-run and scope-mismatch
refusals do not. Dry-run has no NAV or NAV-linked snapshot writes.

Rejected alternatives:

- Manufacture target-period evidence from a next-morning fetch timestamp:
  the timestamp does not prove close-time holdings or FX.
- Change the NAV label to the run date: this discards the approved previous
  eligible trading-date rule.
- Keep the v0.2.0 gate while lacking a routine evidence producer: it makes
  every normal account fail and cannot restore daily recording.
- Add a second provisional NAV type or a new evidence service: neither is
  required for the approved daily portfolio-recording behavior.

## Implementation and validation

One behavior slice covers all three success signals: remove the mandatory
target-period check from `AccountNavRecorderService` and the parameters used
only to feed it, while retaining the calendar and canonical writer. Retire
only gate-specific code and tests; preserve optional artifact-field
serialization, loading, and digest compatibility. Update the daily-job
regression so a normal eligible run without an artifact reaches the writer.

Expected file ownership: remove the gate and its last-success lookup from
`src/app/account_nav_recorder_service.py`; remove only gate-specific argument
forwarding from `src/app/daily_account_nav_service.py` and
`src/app/daily_nav_job_service.py`; delete the uncalled
`src/app/nav_target_evidence.py`. Keep the optional field in
`src/app/nav_valuation_evidence_service.py` and leave public read freshness
code intact. Replace gate-only assertions in `tests/test_daily_nav_services.py`
and `tests/test_nav_valuation_evidence_service.py`, retaining their unrelated
calendar, artifact, replay, and cash-flow coverage.

Verify a confirmed write by fresh readback of both NAV and linked holdings
snapshot, including `finality.valuation_as_of`, source and quality details,
and completed snapshot status. Verify dry-run leaves both stores untouched.
Check an older replay artifact without target-period evidence succeeds under
the existing scope/digest/fingerprint checks, and cash-flow refusals retain
their allowed replay-artifact behavior. Run the existing calendar, duplicate,
holdings, cash-flow, replay, and public freshness tests; then the repository's
pytest, compileall, Ruff, and `git diff --check` gates.

Risk: a daily record can differ from a reconstructed target-close account
state if holdings, prices, or FX changed between market close and observation.
Owner: portfolio operations. Destination: retain `valuation_as_of` and source
provenance for review; a future exact-close product would need an authoritative
historical source and a separate product decision before reintroducing a
blocking gate.
