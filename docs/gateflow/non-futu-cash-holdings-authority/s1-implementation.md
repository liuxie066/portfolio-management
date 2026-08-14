# Gateflow S1 Implementation — Broker-Aware CASH Authority

- Work unit: `non-futu-cash-holdings-authority`
- Slice: `S1`
- Gate: `implementation`
- Status: implementation and code review accepted; pending slice commit
- Artifact path:
  `docs/gateflow/non-futu-cash-holdings-authority/s1-implementation.md`

## Objective and outcome

Implemented broker-aware CASH fingerprint handling and an explicit non-Futu
cash-flow holding declaration without changing the effect-store schema or Futu
OpenD authority.

## Changed files

- `src/app/cash_flow_effect_service.py`
  - non-Futu manual drift now creates or converges to a terminal
    `record_only` audit effect and confirms the observed fingerprint;
  - compensation-owned identities and `compensation_pending` effects remain
    excluded from automatic acceptance;
  - apply/historical-apply cash-flow operations touching non-Futu require
    `apply_delta` or `already_reflected`;
  - `already_reflected` builds a fresh-current no-op absolute target and uses
    the existing apply/CAS/readback/fingerprint path.
- `scripts/pm.py`
  - added both action values to preview and confirm CLI choices.
- `tests/test_cash_flow_effect_service.py`
  - updated non-Futu call sites to declare action;
  - added automatic baseline, legacy convergence, historical apply,
    already-reflected correction, and compensation-authority assertions.
- `docs/cash-flow-holding-effects.md`
- `docs/cash-flow-effects-runbook.md`
- `README.md`
  - documented broker authority and the two non-Futu actions.

## State transitions and invariants

- Non-Futu manual drift:
  `pending legacy external -> record_only`, or new terminal `record_only`;
  holding is never written; fingerprint becomes the observed current value.
- Futu drift: unchanged pending/OpenD review path.
- Compensation: unchanged owner; no auto-baseline.
- Non-Futu cash flow:
  `pending/blocked -> previewed -> applying -> applied` for both actions.
  `already_reflected` has an unchanged absolute target, so the normal CAS path
  returns `already_applied=True` while preserving the applied version chain.

## Validation

```text
PYTHONPYCACHEPREFIX=/tmp/pm_non_futu_cash_s1_fix python3.12 -m pytest -q -p no:cacheprovider tests/test_cash_flow_effect_service.py
27 passed in 0.55s

python3.12 -m ruff check src/app/cash_flow_effect_service.py scripts/pm.py tests/test_cash_flow_effect_service.py
All checks passed!

git diff --check
pass
```

The three findings from `docs/reviews/code-review-20260814-104221.md` were
accepted and fixed; see `s1-review-fix.md`. The additional tests cover
compensation authority, repeated manual-value audit convergence, and mixed
Futu/manual target-source semantics.

No live Feishu, Futu, timer, SQLite production database, effect confirmation,
or NAV write was used.

## Documentation decision

Updated the existing authority document, operator runbook, and README examples.
No new standalone guide was added.

## Residual risks and uncovered areas

- An effect-wide action cannot represent different inclusion declarations for
  different non-Futu targets in one correction. Owner: operator must normalize
  the visible before/target rows before confirmation; a per-target action API
  remains outside this work unit.
- A manual non-Futu typo is authoritative by the confirmed product contract.
  Evidence remains in the terminal audit effect and Feishu history.
- Structured NAV refusal and user-friendly receipt are covered by approved S2,
  not S1.

All residual risks are classified by the approved plan.
