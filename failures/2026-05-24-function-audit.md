# Function Audit Cleanup

- Returning a slug in a field named `public_url` made a local artifact look like a public link. Use `public_url=null` when no real public URL exists.
- The previous publisher tried to avoid external 502 by starting a :3000 publish server, but the actual failure boundary is the invalid public domain. Server self-healing hid the wrong problem.
- `skill_api.py` duplicated `AccountService` logic, so changes to multi-account valuation summaries could drift between service and compatibility paths.
- Static scans report many false positives for tool functions and route handlers. Treat unreferenced public functions in `mcp_server.py`, `src/service/http.py`, and CLI scripts as externally addressed unless the public surface is also being removed.
