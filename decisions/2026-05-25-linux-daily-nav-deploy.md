# Decision: Linux daily NAV deployment

Date: 2026-05-25

The remote Linux deployment runs one systemd timer for the daily NAV workflow at
08:10 Asia/Shanghai.

The service command is intentionally split:

- `lx` runs with Futu cash/MMF sync enabled.
- `hb,sy` run through the same daily NAV job without Futu sync.

This keeps Futu broker cash updates scoped to the account that owns that data
while still recording multiple accounts on the same schedule.
