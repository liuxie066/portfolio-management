# Yahoo Chart Pricing Cleanup

- Shared provider helpers should own external response parsing and project-wide payload normalization.
- Batch providers should only coordinate many symbols and fallback policy; they should not duplicate single-provider JSON parsing.
- When extracting parsing helpers, preserve failure timing: do not move expensive or failure-prone dependencies ahead of validation checks.
- Tests for pricing cleanup should compare single and batch payload fields from the same fake upstream response.
