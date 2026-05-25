# Failure: Docs Can Reintroduce Removed Boundaries

Date: 2026-05-25

Issue:
- Documentation still had old production paths and could imply that the daily
  report publisher was the scheduled multi-account NAV entrypoint.
- Stale references to old compatibility layers make later cleanup harder because
  tests and operators appear to depend on paths that are no longer intended.

Avoidance:
- After architecture cleanup, scan docs for removed services, old config formats,
  old public domains, and old deployment paths.
- Keep compatibility API references only where they are explicitly described as
  adapters.
- Run the normal validation suite after doc rewrites too, because docs include
  command examples and public boundaries that tests may assert indirectly.
