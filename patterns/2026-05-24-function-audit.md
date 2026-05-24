# Function Audit Cleanup

- When a capability is invalid at the environment boundary, keep the old CLI option only as a no-op compatibility shim and make outputs explicit about disabled status.
- For compatibility facades such as `skill_api.py`, route shared behavior to app services instead of copying formulas or discovery code.
- A function audit should combine AST size/duplicate scans with business-boundary checks; raw low-reference counts are only candidates because CLI, MCP, and HTTP handlers often look unused statically.
- Publisher code should produce data and local artifacts. Renderer formatting should stay in the renderer module.
