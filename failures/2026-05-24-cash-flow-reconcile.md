# Cash Flow Reconcile Failures

- Documentation that says "follow with reconcile" is incomplete unless there is a real command operators can run.
- Recalculating populated `cny_amount` with today's FX rate would silently rewrite historical cash-flow meaning.
- Updating Feishu `cash_flow` rows without clearing aggregate caches can leave daily NAV calculations using stale cash-flow totals.
