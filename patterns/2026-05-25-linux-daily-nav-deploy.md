# Pattern: deploy scheduled NAV with dry-run parity

Date: 2026-05-25

Before enabling a production NAV timer:

1. Run `pm config doctor --require-futu --json`.
2. Audit `nav_history` duplicates.
3. Dry-run the exact account split used by systemd.
4. Keep the timer timezone explicit with `Asia/Shanghai`.

The dry-run account split should match production flags, especially broker sync
flags that are only valid for one account.
