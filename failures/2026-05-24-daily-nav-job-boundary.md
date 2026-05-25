# Daily NAV job failure notes

- Old tests were still validating the publisher/CLI hand-built `snapshot -> record_nav -> report` chain. After moving the workflow into application services, those tests must verify service delegation instead.
- Passing optional fields with `None` from adapters to fake clients created noisy call contracts. Omit optional fields unless the operator supplied them.
- A daily NAV job must not rely on `get_nav_on_date()` alone because duplicate rows can make it return one arbitrary match. Audit duplicates first and hard-block writes until repaired.
- Quantity-only holdings updates are too weak for broker sync: stale `asset_type`, `currency`, or names can survive and pollute NAV snapshots. Futu cash/MMF sync needs a replace-style holdings write.
- A separate Futu write flag makes scheduled NAV jobs easy to misconfigure. Default Futu sync write behavior should follow the job write mode while preserving dry-run safety.
- Checking existing same-day NAV before the cash-flow gate can silently skip an account even when manual cash-flow rows were edited after the NAV was recorded. Cash-flow quality must be checked first.
- Letting one daily bundle method handle Futu sync, snapshot valuation, NAV writing, `nav_history` loading, report generation, and recent NAV reads makes the single-account flow overlap with both job orchestration and report generation. Split recorder and payload builder so the side-effect boundary is visible in tests.
- Keeping `record_nav=True` on report generation leaves a hidden write path outside the daily NAV recorder and makes report previews unsafe to reason about. Remove the option instead of preserving it as compatibility behavior.
