# NAV trading calendar design panel

Original snapshot: `docs/nav-trading-calendar.md@285b3ffb0a029b58142c048dd7c86c2f95bf9456196ded46d2538754351e12e8`.
Question to each independent, read-only reviewer: “Any suggestions to improve this design?”
Four native subagents returned usable suggestions. The requested cross-family
DeepSeek backend rejected this account; model-family diversity is unverified.

| Reviewer | Independent suggestions |
| --- | --- |
| 1 | Recheck preceding final NAV before Cash Flow confirmation; reject out-of-range calendar rows; define US session versus NAV date label. |
| 2 | Define date-key semantics; validate `trade_date_type` and wrong-date rows; avoid cross-job cached calendar evidence. |
| 3 | Validate half-day types; recheck old `pending`/`previewed` Cash Flow effects; observe multi-date scan request load. |
| 4 | Make transient scan failures retryable; define cross-midnight date semantics; validate response types. |

## Adjudication

- Accepted: same-date market trading-date label (US Friday belongs to Friday
  even when Beijing clock says Saturday); successful empty list is closed,
  nonempty rows require requested date and recognized `WHOLE`/`MORNING`/
  `AFTERNOON` type; no cross-job evidence cache; confirm-time Cash Flow
  preceding-final-NAV check for existing states. These close concrete ambiguity
  and write-boundary gaps within the approved scope.
- Accepted as validation: test a transient calendar failure and retry, plus a
  multi-date Cash Flow scan if its focused tests exercise the request path.
- Deferred: preemptive calendar batching. There is no measured rate-limit
  failure; the design retains a batch option only if observed load requires it.
- Not adopted as a separate scan-state refactor: the same-source hash concern
  is an inference. A transient error uses `raw_source` while recovery uses
  normalized `source`, and the confirmation recheck is the necessary write
  boundary. If recovery tests prove the state remains stuck, fix that within
  the approved Cash Flow signal.

No reviewer edited files or ran Planreview. Suggestions are bound to the
original snapshot; the revised design requires its own hash and checks.
