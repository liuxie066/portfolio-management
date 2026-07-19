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

`check-live` reads every page returned by the Feishu fields API. Strict mode
requires every documented core required field to have a compatible type;
optional type drift and extra fields are reported without blocking.

The type mapping follows the official Feishu Bitable fields API schema used by
the list-fields response: `1=text`, `2=number`, `3=single select`,
`4=multi select`, and `5=date`. A documented `datetime` alternative requires
type `5` with `ui_type=DateTime`; JSON payloads documented as `text/json` use
type `1`. Unknown type IDs fail required strict checks instead of being inferred.
