# Cash Flow Reconcile Pattern

- Keep manual Feishu entry small and put derived-field repair behind a dedicated CLI command.
- Prefer dry-run output that names the exact fields to fill before any write.
- For foreign currency, preserve existing `exchange_rate`; if it is blank, derive it from existing `cny_amount` or fetch FX only when both are blank.
- On reconciliation writes, invalidate local aggregate caches rather than trying to patch cached totals field by field.
