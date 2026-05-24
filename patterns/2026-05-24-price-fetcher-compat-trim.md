# PriceFetcher Compatibility Trim

- Before deleting compatibility methods, scan `src`, `scripts`, `tests`, `skill_api.py`, `mcp_server.py`, `README.md`, `docs`, and `SKILL.md` for exact method names.
- Delete wrappers only when the exact symbol appears only at its own definition, or when a same-name hit belongs to a different class/module and is intentionally unrelated.
- Preserve compatibility methods that application code still calls directly, even if they only delegate to provider classes.
- After facade trimming, run targeted pricing tests before the full baseline.
