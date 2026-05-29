# Decision: NAV change can use current-period fallback anchors

Date: 2026-05-29

For `mtd_nav_change` and `ytd_nav_change`, if a new account has no previous
month-end or previous year-end NAV record, the calculation may fall back to the
first earlier NAV record in the current month or year.

This fallback is only for NAV percentage change. `mtd_pnl` and `ytd_pnl` still
require strict previous-period anchors, because cash-flow attribution is not
safe to infer from a partial current-period baseline.
