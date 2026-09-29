# Daily NAV consistency design panel

Frozen input: `docs/nav-daily-consistency.md@d46d0fa971b8012e87b20ee21b916ef6b85f42ba7ce11e66123661a3a0a21ecb`.
Each independent, read-only reviewer answered: “Any suggestions to improve this design?”
Four native subagents returned usable suggestions. The requested cross-family
DeepSeek reviewer could not run with this account; a same-brief native fallback
provided the fourth result. `reviewer_backend=native-subagent`,
`reviewer_model=unknown`, `independence=unverified`.

| Reviewer | Material suggestions |
| --- | --- |
| 1 fallback | Validate old artifacts at final write; require cutoff proof for FX and continuous assets; do not let `force_non_business_day` override final NAV eligibility. |
| 2 | Separate historical valuation holdings from current preflight; implement closed-market prior close; define cutoff; avoid double Feishu fetch on empty API read; make blocked dates visible. |
| 3 | Implement closed-market lookup; reject legacy artifacts lacking facts; define holdings completeness; avoid cross-process disk cache writes; name preflight side effects. |
| 4 | Prove holdings and FX effective period independently of hashes; save actual prior-close fact date; provide explicit-date retry for late evidence. |

## Adjudication

- **Accepted:** final gate inspects every loaded artifact, including v1; an
  artifact digest is integrity evidence, not economic-date evidence. Historical
  holdings digest must match the valuation, while current holdings preflight
  remains a separate gate and may differ on delayed replay.
- **Accepted:** define one global NAV cutoff as the latest close among markets
  open on the target date, with source effective-time and completeness proof;
  require market-specific prior-open closes, actual fact dates, and publication
  timing. A forced all-closed date cannot become a final daily NAV.
- **Accepted:** public canonical read refreshes once per request, including an
  empty account, and does not rewrite the process-shared disk cache. State the
  existing preflight mutations that may precede a blocked NAV write.
- **Accepted:** expose blocked account/date and missing facts, retain a
  controlled explicit-date retry for evidence arriving after the default date
  advances. No new automatic backfill queue is needed for this design.
- **Deferred with owner:** proving routine target-period holdings and FX source
  capability belongs to the NAV data-source slice. Until then safety gating
  intentionally blocks, and scheduled NAV availability is not claimed.
- **Needs evidence:** canonical Feishu read visibility during a concurrent
  write and actual provider effective-time capabilities remain unverified;
  track them in implementation/production acceptance and source validation.

No reviewer edited files, ran Planreview, or verified live providers. The
revised design has a new hash and is subject to a separate Planreview.

## 2026-09-29 daily-recording policy revision

Frozen input: `docs/nav-daily-consistency.md@137d305e4ff2f21511cc642a0f747246638dcb0ea9fa7cb448c022b8db085211`.
Four independent native subagents read the same snapshot and answered “Any
suggestions to improve this design?” The cross-family DeepSeek backend failed
before review because this account does not support it; all four usable results
used the available default backend. `reviewer_backend=native-subagent`,
`reviewer_model=unknown`, `independence=unverified`.

| Reviewer | Material suggestions |
| --- | --- |
| 1 | Test durable NAV and linked snapshot, cash-flow refusal artifact behavior, retain optional target-evidence artifact compatibility, clarify automatic versus explicit dates. |
| 2 | Read back observation time and source/quality details, clarify target-evidence-free replay policy. |
| 3 | Preserve cash-flow refusal capture, clarify date selection, state that repair does not backfill 2026-09-28. |
| 4 | Test persisted NAV/snapshot and dry-run nonwrites, retain saved-artifact compatibility, describe snapshot-time precision accurately. |

### Adjudication

- **Accepted:** strengthen the regression to verify durable NAV and linked
  snapshot readback, actual `finality.valuation_as_of`, source/quality details,
  and dry-run nonwrites. The old mocked writer test alone cannot prove this.
- **Accepted:** preserve cash-flow refusal artifact capture for allowed
  confirmed-write failures; reject capture for dry-run and scope mismatch.
- **Accepted:** retain optional `target_period_evidence` in artifact
  serialization, digest, and loading for saved replay artifacts. Remove only
  mandatory admission and its exclusively used parameters/tests.
- **Accepted:** specify automatic versus explicit-date behavior, legacy replay
  without target-period evidence, the lack of automatic 2026-09-28 backfill,
  and the precision of `snapshot_time`.
- **Rejected with reason:** none. **Deferred with owner:** none. **Needs more
  evidence:** none for the source design; live production acceptance remains
  outside this source-only task.

The revised design is `docs/nav-daily-consistency.md@db57eb7c605eeab7213d6c01f6c71fa8c6f93414c71027d9548274d8192df849`.
Planreview: `docs/reviews/plan-review-20260929-094220.md`, pass-with-risks.
