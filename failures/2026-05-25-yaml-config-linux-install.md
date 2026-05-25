# Failure Lesson: JSON config and repo-local runtime paths

Date: 2026-05-25

Keeping production config as `config.json` made manual operations harder
because the file could not carry comments and was easy to break with commas.
Relying on repo-local `.data` and `reports` paths also made Linux deployment
depend on fragile symlinks after checkout refreshes.

Prefer YAML config plus explicit runtime path settings for scheduled production
jobs.
