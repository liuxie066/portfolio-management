# Feishu nav_history Boundary Lessons

## Lesson

Do not mix table repository logic with the `FeishuStorage` composition surface.

`nav_history` had grown duplicate detection, cache preload, bulk write, single write, derived-field patching, and model conversion in one mixin. `holdings` had similarly grown lookup cache, bulk upsert, replacement writes for broker sync, quantity changes, and model conversion in one mixin. `cash_flow` mixed manual-row reconciliation, deduped writes, aggregate caches, FX completion, and model conversion. `transactions` held idempotency and generic dedup lookup used by other tables, and `snapshots` held batch Feishu writes. That made it hard to see which behavior belonged to a table repository versus the storage facade.

## Guardrail

Keep core Feishu table mixins thin. If future work needs direct `list_records`, `create_record`, `batch_create_records`, or `batch_update_records` for a core Feishu table, add it to the matching repository and call it through the existing facade.
