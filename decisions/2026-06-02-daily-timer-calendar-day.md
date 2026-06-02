# Decision: daily NAV timer runs by calendar day

Date: 2026-06-02

The scheduled NAV timer should run every calendar day at
`08:10 Asia/Shanghai`. `daily-job` resolves the run date to the previous
business NAV date and then applies the non-business-day guard to that NAV date.

Do not configure the systemd timer as Monday-Friday only. A Saturday timer run
is the normal next-day window for recording Friday NAV.
