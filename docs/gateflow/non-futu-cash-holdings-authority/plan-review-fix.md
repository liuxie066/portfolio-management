# Gateflow Plan Review Fix — Non-Futu Cash Holdings Authority

- Work unit: `non-futu-cash-holdings-authority`
- Source review: `docs/reviews/plan-review-20260814-103028.md`
- Gate: `fix`
- Status: accepted findings fixed in plan; pending re-review
- Artifact path:
  `docs/gateflow/non-futu-cash-holdings-authority/plan-review-fix.md`

## PR-01 — accepted — fixed

The plan now places non-Futu automatic baseline acceptance strictly after the
existing `compensation_identities` guard and explicitly forbids transitioning a
`compensation_pending` external effect. S1 requires a regression proving that a
partial CASH target remains owned by compensation, the fingerprint is not
auto-confirmed, and NAV remains blocked.

Final status: `已修复`.

## PR-02 — accepted — fixed

The plan now distinguishes ordinary historical record-only preview from
explicit `historical_apply=True`. The former retains the existing no-write
early return; the latter must select `apply_delta` or `already_reflected` for
every non-Futu operation. S1 includes a focused regression.

Final status: `已修复`.

## Residual risks

- Wrong operator action remains an explicit reviewed decision protected by the
  existing preview hash and confirmation boundary.
- Mixed non-Futu inclusion inside one corrected effect remains effect-wide and
  must be normalized before confirmation; a per-target action API remains
  unjustified without production evidence.

Both risks remain classified in the approved plan and neither blocks
implementation.
