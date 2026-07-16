# Schema Checks

`docs/schema.md` is the schema authority. Feishu table and field changes remain
manual; this project does not maintain a second local migration-state ledger.

Inspect documented expectations:

```bash
python scripts/migrate_schema.py
python scripts/migrate_schema.py expectations
```

Compare the documented schema with live Feishu fields:

```bash
python scripts/migrate_schema.py check-live
python scripts/migrate_schema.py check-live --strict
```
