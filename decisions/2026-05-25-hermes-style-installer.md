# Decision: Hermes-Style Bootstrap Installer

Date: 2026-05-25

The project now has `scripts/install.sh` as the preferred Linux bootstrap
installer.

Decision:
- Keep `scripts/install_linux.py` as the conservative system asset writer.
- Add `scripts/install.sh` for clone/update, venv creation, dependency install,
  and delegation to `install_linux.py`.
- Generate a stable `pm` launcher, defaulting to `/usr/local/bin/pm` for root
  Linux installs and `~/.local/bin/pm` for user installs.
- Clear inherited `PYTHONPATH` / `PYTHONHOME` in launchers and installers so
  runtime imports come from the installed checkout.
- Keep system writes behind `--apply`, and keep timer activation behind
  `--enable-timer`.

Rationale:
- Hermes Agent's install flow has useful production patterns: stable command
  shims, FHS-like root layout, update-aware installs, and environment cleanup.
- This project should absorb those patterns without adopting Hermes' interactive
  agent setup complexity.
