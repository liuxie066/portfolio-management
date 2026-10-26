# Goal amendment: confirmed currency correction candidates

User explicitly added currency correction to the current Gateflow work and confirmed HDK was a typo for HKD. This is a functional scope amendment, not authorization to edit a real holding. Existing local work is retained. All previous production/public-action prohibitions remain. Current gate returns to plan review for this amendment before implementing it.

## Goal / success / non-goals

Offer an evidence-labelled HDK -> HKD correction preview and use existing exact-case explicit confirmation to write it in synthetic validation. Unknown/ambiguous codes remain manual. No fuzzy edit-distance conversion, market/account-based guessing, automatic currency writes, new state machine, real record changes or new external dependencies. Correct legitimate currencies retain existing validation and conflict semantics and are never changed by typo policy.

## Decisions and allowed changes

Extend existing currency policy module with a small reviewed typo mapping HDK -> HKD and a versioned deterministic authority (currency_typo_policy / currency-typo.v1). This is a public generic typo rule, not a persisted user-specific instruction or production row binding. Evidence must include observed invalid raw code, unique target, policy source/version; no fabricated observation timestamp for a static rule. Existing real provider evidence time is retained when relevant.

In HoldingsValidator._currency, do not immediately discard invalid raw values before comparing evidence. Preserve existing authoritative proposal calculation (explicit position currency / type or cash identity policy). For HDK: collect the reviewed alias candidate and any valid authority-derived currency. A unique candidate HKD produces a blocking `conflict` outcome with proposed HKD and deterministic policy evidence; if another source proposes a different valid currency, produce blocking invalid/manual outcome with no proposed and describe conflicting candidates in evidence/reason. Invalid source proposals remain rejected; never use them to manufacture a correction. Other invalid raw codes remain invalid without a guess. Blank currency behavior remains existing missing-completable/manual, with valid proposal membership required. No changes to legitimate raw currency overwrite semantics.

`conflict` deliberately reuses current pending_confirmation preview and `resolve(accept-proposed)` locks/fresh-read/case scope/base digest/readback/unknown outcome behavior. Event auto-name completion and apply_missing MUST NOT apply currency conflict targets. `keep-current` MUST be rejected for a currency correction whose current value is outside the accepted currency set: acknowledging an invalid value cannot make it a valid NAV denomination. Update discovery receipt allowed_decisions/action text to present only accept-proposed for this specific invalid-current correction, while retaining both options for legitimate currency conflicts. CLI syntax can remain generic but service enforces the restriction.

Also protect typed/NAV conversion: even if a caller passes confirmed_conflict_fields={currency}, an invalid current currency must never pass to_holding. This closes the same correction contract, not unrelated broad validation. Existing valid manual conflict overrides continue working.

Use the same invalid-current currency check in `_durable_keep_matches` so a stored resolved_keep cannot waive the new correction blocker on subsequent NAV planning. Test a synthetic existing keep record; do not change valid-currency exception retention.

Allowed files add existing holdings_receipt/workflow and typed validation locations and focused tests to original plan; no schema changes. Existing currency v2 bump represents this unreleased policy change; avoid unnecessary further bump. Correction mapping/source participates in authority_id/case identity so a changed source value/identity/proposal invalidates old confirmation. Repeated confirmation may return existing already-resolved behavior or scope-changed/no-write; do not create a new idempotency subsystem. Unknown write outcomes remain explicit recovery, no blind replay.

## Validation

- Synthetic HDK/hdk/whitespace input produces unique HKD proposal, blocking conflict, policy evidence and confirmation-only receipt.
- Auto event may fill name but never currency; apply_missing never applies a conflict.
- Explicit synthetic accept-proposed changes only currency with existing protections, then valid record clears its currency NAV blocker.
- keep-current invalid denomination rejected; direct typed conversion with a fake confirmation cannot waive it.
- Valid HKD/CNY/USD/EUR/CNH retain prior behavior; unknown QQQ has no candidate; HDK plus incompatible explicit/type evidence yields no proposed. Invalid provider proposal and HDK-CASH do not become writes.
- Source value, identity or evidence changes invalidate stale proposal; repeat confirmation writes at most once; unknown outcome remains recoverable without automatic replay.
- Retain original baseline log as historical red evidence. Update future assertions for HDK from invalid to blocking conflict (explicit new requirement), not to silent success. Rerun affected focused suite, independent QA, and required full local checks after final code. No production or remote CI.

## Risks and gates

New risk: mistaken alias recommendation. Mitigation is one explicitly reviewed mapping, no auto-write, unique-candidate agreement, preview with evidence, explicit confirmation and concurrency protections. Additional typo rules require later reviewed policy changes (future owner: maintainers). Invalid-currency keep override is fixed in this amendment. Existing inherited write race and live source acceptance remain later work/user-authorized acceptance. After independent plan pass, amend plan checkpoint locally; implement, independent review/QA, slice checkpoint, aggregate deepreview, stop before push.
