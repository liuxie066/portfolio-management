# Pattern: service first, domain helpers for compatibility options

Date: 2026-05-25

When a legacy adapter method has no special compatibility arguments, delegate
to `PortfolioService`.

When a legacy adapter method accepts test/runtime compatibility inputs that the
service API does not expose, compose the narrow app/domain service directly and
keep the composition small. Add an AST boundary test when the goal is to prevent
a broad compatibility alias from returning.
