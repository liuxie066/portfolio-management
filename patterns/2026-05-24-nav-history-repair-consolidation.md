# NAV History Repair Consolidation Pattern

- Keep operator-facing maintenance commands as one canonical script with subcommands.
- Put implementation in importable `src/maintenance/*` modules and expose `run(args)` so tests can patch execution without touching Feishu.
- Let the central CLI parse all flags directly instead of forwarding reconstructed argv to older scripts.
- Lock write gates with argparse mutual exclusion and regression tests for unknown flags.
