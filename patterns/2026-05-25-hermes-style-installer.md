# Pattern: Bootstrap Then Delegate System Writes

Date: 2026-05-25

For Linux installation:
- Use a shell bootstrapper for OS-level flow: clone/update, venv, pip, and
  operator-facing command output.
- Use the Python installer for deterministic rendering of config/env/systemd
  files because it is easier to unit test.
- The generated launcher should be the operator startup surface; examples
  should prefer `pm ...` after installation.
- Timer enabling should remain explicit. Installation can prepare units without
  starting scheduled writes.
- Do not let inherited Python environment variables leak into install or launch
  commands.
