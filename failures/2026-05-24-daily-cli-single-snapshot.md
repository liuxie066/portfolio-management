# Daily CLI single snapshot failure

Observed failure: `pm daily --account lx --no-service` returned a NAV total and
distribution total that differed by about 26,757.76 CNY.

Cause: the CLI called `record_nav()` and `get_distribution()` as separate
operations. Each operation could build its own live valuation, so the payload
mixed two price moments.

Fix: reuse one snapshot for direct CLI execution and expose distribution from
the service daily bundle.
