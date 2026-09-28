# macOS LaunchAgent operations

The [macOS design](deploy-macos.md) owns the deployment decisions. This page is the implementation's command checklist. All jobs run only in the logged-in user's `gui/$UID` domain. Generating plists does not load them.

## Prepare and inspect

Use a stable local checkout at `~/.portfolio-management/current`, Python 3.12, and a non-secret `~/.portfolio-management/config.yaml`. Give the state and log directories mode `0700`; give the config mode `0600`. Create `~/.portfolio-management/reports` and `~/Library/LaunchAgents` under the same user. Do not point a job at a removable `/Volumes` checkout.

```bash
cd "$HOME/.portfolio-management/current"
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install 'futu_api==10.6.6608'
.venv/bin/python -c 'import futu; print(futu.__version__)'
.venv/bin/python scripts/install_macos.py --plan
```

The package pin matches the SDK seen in the design investigation; verify compatibility with the target Mac and record the version in the deployment evidence. `requirements.txt` alone does not install it. An import in a global Python does not satisfy this gate.

The installer also requires an existing, user-owned login Keychain file. It pins that file in every plist. Create three generic-password items in that **same** file with service `com.liuxie.portfolio-management` and accounts `pm-feishu-agent-app-secret`, `pm-feishu-listener-app-secret`, and `pm-quality-read-token`. Use `/usr/bin/security add-generic-password` interactively: put `-w` last so it prompts, and test the exact argument order with a disposable item first. Never put a secret in argv, an environment variable, a YAML file, a plist, or this runbook. Do not use `-A`. Grant access interactively to the intended executable, then prove noninteractive access from the LaunchAgent. This Keychain trust decision applies to the logged-in user and does not give Linux-style per-service isolation.

The non-secret config must include explicit Agent/Listener IDs, table references, `quality.accounts`, and `futu.profiles.lx`/`.sy`. Use `./pm config doctor --require-secure-feishu --json` to inspect the full Feishu setup, `./pm config quality-preflight --json` for only the quality token and scope, and `./pm config futu-preflight --json` for SDK, account mappings and OpenD login readiness. The Futu command queries OpenD read-only; success requires `READY`, `qot_logined`, and `trd_logined` for each configured endpoint. Its exit status is the gate. `config doctor --require-futu` remains useful supplementary diagnostics; its SDK warning is not a hard error.

Set the Mac system timezone to Asia/Shanghai before timed jobs. The `TZ` process environment does not change launchd's trigger clock. Verify the intended account/Base is isolated from any existing writer, or complete a separate single-writer cutover plan.

## Install and verify preflights

```bash
cd "$HOME/.portfolio-management/current"
.venv/bin/python scripts/install_macos.py --plan
.venv/bin/python scripts/install_macos.py --apply
```

`--apply` writes ten user-owned `0600` plists and loads none. Review the JSON for paths, labels and arguments. Keep `preflight`, `quality-preflight`, and `futu-preflight` separate from consumers; none has an automatic trigger. Example for one preflight, repeated for the other two:

```bash
label=com.liuxie.portfolio-management.preflight
plist="$HOME/Library/LaunchAgents/$label.plist"
launchctl bootstrap "gui/$UID" "$plist"
launchctl print "gui/$UID/$label"
launchctl kickstart -p "gui/$UID/$label"
launchctl print "gui/$UID/$label"
```

Record the prior run count and timestamp before `kickstart`. The kickstart PID must finish, the count must increase, and the new exit status and timestamped redacted log must match this invocation. A successful `bootstrap` or `kickstart` request is insufficient. Treat `launchctl print` as operator diagnostics, not a stable parser API. If any preflight prompts, times out, or exits nonzero, leave its consumers unloaded. A failed quality check blocks acceptance of `/quality/status`; a failed Futu check blocks all financial write timers.

## Activate and recover

Activation is a separate service-change decision. First use a read-only business dry run and verify OpenD, clock, Keychain, and account mapping. If another host targets the same accounts/Base, stop its overlapping timers/listener, verify they stopped and no invocation remains, then load the Mac jobs. Enable API, quality refresh, financial write timers, and Feishu listener independently. The listener also needs its existing subscription and canary process. `launchctl bootstrap "gui/$UID" <plist>` loads one job; `launchctl bootout "gui/$UID" <plist>` unloads one job without deleting state. Observe `launchctl print`, the private logs, API `/health`, and role-specific business evidence. `/health` alone says nothing about Keychain token access.

Sleep can coalesce calendar wakeups; power-off and logout can miss runs. There is no automatic replay. Inspect durable receipts and duplicate NAV records before any separately authorized recovery write. A native `lockf` conflict is a skipped invocation. On failure, inspect the redacted log, repair the cause, rerun the affected preflight, and explicitly reload only the affected job. To roll back assets, unload affected jobs and restore earlier plists; preserve Keychain items, inbox, outbox, SQLite and NAV state. Rotation changes the exact Keychain item, then reruns its preflight before any separately authorized consumer restart.
