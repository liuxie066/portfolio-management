# macOS design improvement panel

Input snapshot: `docs/deploy-macos.md@696a9a5e6527d8d23b9433b6e466fa2b77f9c4ff6b21b05cbb5b8a46d4e2882b`.
Question to each reviewer: “Any suggestions to improve this design?” All four reviewed the same snapshot independently and made no edits. Backend: `native-subagent`; reviewer model: `unknown` (not exposed in results); cross-family independence: `unverified`. Two earlier cross-family dispatches failed before review and do not count.

| Reviewer | Suggestions and coverage |
| --- | --- |
| A | Pin scheduled launcher; make preflight executable; retain Cash Flow quarter-hour timing; reject plaintext quality-token shadows and make Futu import a hard gate; require single-writer cutover. Did not run live Keychain/launchd/business checks. |
| B | Pin scheduled launcher; single-entry preflight; hard Futu import and lx/sy profile gates; quality token under launchd; single-writer cutover; document Cash Flow timing. Did not run live Keychain/launchd/business checks. |
| C | Strict target-venv path including shell child commands; preflight fresh-result attribution; independent quality-token gate and runtime failure response; hard Futu import. Did not run live Keychain/launchd/business checks. |
| D | Pin scheduled launcher; hard Futu import; single-entry preflight; launchd-context quality-token gate. Did not run live Keychain/launchd/business checks. |

## Main-agent adjudication

| Suggestion cluster | Decision | Evidence and disposition |
| --- | --- | --- |
| Scheduled launcher and strict interpreter | accepted | `scripts/portfolio_scheduled_job.sh` defaults to Linux `/usr/local/bin/pm`; existing root `pm` falls back to global Python. Design now sets `PORTFOLIO_PM_BIN` and requires `PM_REQUIRE_VENV=1`. |
| Single-entry, fresh preflight | accepted | Linux supports two `ExecStart=` lines, while one LaunchAgent has one `ProgramArguments` vector. Design now specifies a small fail-fast script and attribution to the current kickstart invocation. |
| Futu gate and profiles | accepted | `src/config.py` emits SDK absence as warning, not issue; scheduled script uses lx and sy. Design requires same-venv import exit 0, treats doctor warning as blocking, and validates both profiles. |
| Quality token | accepted | `/quality/status` reads `quality.read_token` on each request; Feishu preflight does not test it. Design now has an independent launchd-context quality preflight and redacted runtime failure. |
| Single writer across hosts | accepted | Local locks cannot coordinate with Linux; Mac activation now requires isolated targets or explicit cutover/reversal for overlapping accounts/Base. |
| Cash Flow calendar | accepted | Linux defaults to wall-clock quarter hours; Mac design now uses matching `StartCalendarInterval` rather than relative 900 seconds. |
| Plaintext shadow rejection | accepted | Existing quality token can be read from env/YAML; Keychain mode now rejects these sources instead of merely ignoring them. |

No panel suggestion was used as authorization to implement or activate a service. Final design reference: `docs/deploy-macos.md@6ed4b82b0a56cdef301b47fdf981c42b514ac536edb89031a4213bdde4a3794d`. The adversarial check is recorded in `.devflow/scope.md` and `docs/reviews/plan-review-20260928-133111.md`. Panel outputs apply to the input snapshot, not to the revised version.
