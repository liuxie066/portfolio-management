# Gateflow Fix Artifact — S3

- Gate: `fix`
- Work unit: `feishu-dual-app-credentials`
- Slice: `S3 — systemd credential wiring and installer safety`
- Review: `docs/reviews/code-review-20260802-013859.md`
- Status: `fix complete; pending re-review`

## Finding decisions

### S3-CR-01 — accepted — fixed

The Linux installer now imports both credential names from the S1 runtime
credential module. Its repository root is inserted into `sys.path` before that
import so invoking the script by absolute path from a non-repository working
directory remains supported. Unit rendering, encrypted-file presence checks,
the synthetic capability probe, and runtime lookup therefore share one
authoritative pair of names.

A subprocess regression runs `python3.12 scripts/install_linux.py --help` with a
temporary external cwd and proves the direct deployment entrypoint can load the
shared contract.
