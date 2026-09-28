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
