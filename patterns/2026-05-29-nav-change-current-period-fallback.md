# Pattern: split NAV-return anchors from PnL anchors

Date: 2026-05-29

When a period metric mixes percentage NAV return and currency PnL, model the
anchors separately. NAV return can use a current-period fallback anchor for new
accounts, but PnL should keep the stricter previous-period anchor unless the
cash-flow window is also explicitly re-based.
