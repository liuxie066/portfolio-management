# Feishu Manual Field Ownership Pattern

- Keep manual Feishu views small: expose only fields a person can reliably maintain.
- Keep generated fields in the same table only when they are required by current code, but mark them system-owned and hide them from manual-entry views.
- Make schema check output derive from `docs/schema.md` so the operator checklist and validation command use the same field dictionary.
- Avoid documenting retired Feishu tables as active `###` schema sections, otherwise live schema checks will require unnecessary table configuration.
