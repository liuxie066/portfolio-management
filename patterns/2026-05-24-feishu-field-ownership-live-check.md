# Feishu field ownership live-check pattern

For manual-maintained Feishu tables, separate view ownership from table fields:
manual-entry views should expose only human-owned fields, while generated fields
stay present in the table and are hidden from normal entry views.

After manual cash-flow entry, use `pm cash-flow reconcile` as dry-run preview
before `pm cash-flow reconcile --apply --confirm`.
