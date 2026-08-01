# Gateflow S8 Scope Correction — Public CLOSED Defaults and Finality Vocabulary

- Gate: implementation
- Work unit: feishu-bitable-contract-repair
- Slice: S8
- Base: `6cbe411`
- Recorded at: 2026-08-02T06:03:21+08:00
- Status: bounded scope correction

## Added Production Files

- `skill_api.py`
- `src/app/nav_finality.py`
- `src/domain/nav_finality_contract.py`
- `src/portfolio.py`

## Reason

The accepted S8 allowlist includes the CLOSED recorder service but not the two
public `SkillAPI.close_nav` signatures. Both public signatures still supplied
`stock_value=0.0`, which would manufacture an unobserved base component before
the stricter CLOSED service could reject missing evidence. The public default
must therefore become `None` and its documentation must require explicit
total/cash/non-cash components.

The final NAV invariant also has to validate the existing finality
writer/status vocabulary without copying that vocabulary into the calculator.
Importing `src.app.nav_finality` from the domain calculator creates a package
cycle through `src.app.__init__`. The pure constants are therefore moved to
`src/domain/nav_finality_contract.py`; `src/app/nav_finality.py` imports and
continues to expose the same names. This preserves one definition and the
existing public import surface.

Maintenance recomputation also needs to pass its already fresh, immutable NAV
history directly through the existing `PortfolioManager.record_nav` delegator.
Adding that one optional delegation argument avoids publishing hypothetical
dry-run candidates into the shared NAV cache, which would otherwise make a
plan mutate later read authority before any Feishu write.

## Boundary

- The CLOSED scope correction changes only missing-value defaults and text;
  it does not call a repository or authorize a live write.
- The finality module changes only the ownership location of existing pure
  constants. `NavWriteContext` and `evaluate_nav_finality` retain their public
  behavior and continue using those constants.
- The portfolio change only delegates an explicit immutable history snapshot;
  the application service accepts it solely for non-persisting `nav-repair`
  calculations.
- No Feishu schema, table, business row, deployment, release, or merge is in
  scope.
