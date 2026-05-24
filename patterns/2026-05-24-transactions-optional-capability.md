# Transactions Optional Capability Pattern

- Separate product-core schema health from optional capability schema health.
- Keep optional tables visible in diagnostics, but avoid failing routine NAV checks when they are absent.
- If a table is not manually maintained and not used by the core calculation path, document it as optional before doing field migrations.
- Preserve existing optional-table data shape unless the product starts relying on it again.
