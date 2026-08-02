"""Shared, fail-closed contracts for derived-only NAV maintenance."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Iterable, Mapping, Optional

from src.app.nav_finality import NavWriteContext
from src.domain.nav_calculator import NavCalculator
from src.models import NAVHistory, PortfolioValuation


BASE_FIELDS = (
    "total_value",
    "cash_value",
    "stock_value",
    "fund_value",
    "cn_stock_value",
    "us_stock_value",
    "hk_stock_value",
)
MAINTENANCE_FIELDS = (
    "stock_weight",
    "cash_weight",
    "shares",
    "nav",
    "cash_flow",
    "share_change",
    "mtd_nav_change",
    "ytd_nav_change",
    "pnl",
    "mtd_pnl",
    "ytd_pnl",
    "details",
)


@dataclass(frozen=True)
class FieldState:
    """One observed Feishu field state; missing is distinct from null."""

    state: str
    value: Any = None

    def __post_init__(self) -> None:
        if self.state not in {"missing", "null", "value"}:
            raise ValueError(f"invalid field state: {self.state}")
        if self.state != "value" and self.value is not None:
            raise ValueError(f"{self.state} field state cannot carry a value")

    @classmethod
    def missing(cls) -> "FieldState":
        return cls("missing")

    @classmethod
    def null(cls) -> "FieldState":
        return cls("null")

    @classmethod
    def valued(cls, value: Any) -> "FieldState":
        if value is None:
            return cls.null()
        return cls("value", value)

    @classmethod
    def from_envelope(cls, envelope: Mapping[str, Any]) -> "FieldState":
        state = str(envelope.get("state") or "")
        return cls(state, envelope.get("value") if state == "value" else None)

    def envelope(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"state": self.state}
        if self.state == "value":
            payload["value"] = self.value
        return payload


@dataclass(frozen=True)
class FreshNavRow:
    nav: NAVHistory
    field_states: Mapping[str, FieldState]

    @property
    def record_id(self) -> str:
        return str(self.nav.record_id or "")


def _storage_repository(storage: Any) -> Any:
    repository = getattr(storage, "nav_history", None)
    if repository is not None and callable(
        getattr(repository, "read_nav_maintenance_rows", None)
    ):
        return repository
    if callable(getattr(storage, "read_nav_maintenance_rows", None)):
        return storage
    raise AttributeError(
        "nav_history maintenance requires fresh FieldState-aware repository reads"
    )


def read_fresh_nav_rows(context: Any) -> list[FreshNavRow]:
    """Read the complete account history from Feishu, never from local cache."""

    repository = _storage_repository(context.storage)
    raw_rows = repository.read_nav_maintenance_rows(context.account)
    result: list[FreshNavRow] = []
    for raw in raw_rows:
        nav = raw.get("nav") if isinstance(raw, dict) else None
        states = raw.get("field_states") if isinstance(raw, dict) else None
        if not isinstance(nav, NAVHistory) or not isinstance(states, Mapping):
            raise TypeError(
                "read_nav_maintenance_rows must return nav and field_states"
            )
        converted = {
            field: (
                state
                if isinstance(state, FieldState)
                else FieldState.from_envelope(state)
            )
            for field, state in states.items()
        }
        result.append(FreshNavRow(nav=nav, field_states=converted))
    result.sort(key=lambda row: row.nav.date)
    return result


def rows_by_date(rows: Iterable[FreshNavRow]) -> dict[date, list[FreshNavRow]]:
    grouped: dict[date, list[FreshNavRow]] = {}
    for row in rows:
        grouped.setdefault(row.nav.date, []).append(row)
    return grouped


def state_from_value(value: Any, *, preserve_missing: Optional[FieldState] = None) -> FieldState:
    if value is None:
        if preserve_missing is not None and preserve_missing.state == "missing":
            return preserve_missing
        return FieldState.null()
    return FieldState.valued(value)


def states_equal(left: Mapping[str, FieldState], right: Mapping[str, FieldState]) -> bool:
    return dict(left) == dict(right)


def state_subset(
    row: FreshNavRow,
    fields: Iterable[str],
) -> dict[str, FieldState]:
    return {
        field: row.field_states.get(field, FieldState.missing())
        for field in fields
    }


def nav_with_states(nav: NAVHistory, states: Mapping[str, FieldState]) -> NAVHistory:
    payload = nav.model_dump()
    for field, state in states.items():
        payload[field] = state.value if state.state == "value" else None
    return NAVHistory(**payload)


def maintenance_details(existing: Any, calculated: Any) -> dict[str, Any]:
    """Merge calculation evidence without claiming a valuation snapshot rewrite."""

    current = dict(existing) if isinstance(existing, dict) else {}
    derived = dict(calculated) if isinstance(calculated, dict) else {}
    allowed_exact = {
        "monthly_cash_flow",
        "year_cash_flow",
        "cumulative_nav_change",
        "cumulative_appreciation",
        "initial_value",
        "cumulative_cash_flow",
        "cagr",
        "cagr_pct",
        "cash_flow_basis",
    }
    for key, value in derived.items():
        if key == "cash_flow_dataset":
            continue
        if (
            key in allowed_exact
            or key.startswith("nav_change_")
            or key.startswith("appreciation_")
            or key.startswith("cash_flow_")
        ):
            current[key] = value

    # Finality and the top-level run_id describe the authoritative valuation
    # write, not this derived-only maintenance operation.  Preserve any
    # observed classification verbatim.  A legacy row with no classification
    # receives the non-final maintenance classification solely so the
    # canonical invariant can describe what happened without claiming a daily
    # final write.
    derived_finality = derived.get("finality")
    if current.get("finality") is None and isinstance(derived_finality, Mapping):
        current["finality"] = dict(derived_finality)
    if isinstance(derived_finality, Mapping):
        provenance = {
            key: derived_finality.get(key)
            for key in (
                "version",
                "status",
                "nav_date",
                "valuation_as_of",
                "writer",
                "write_reason",
                "run_id",
            )
            if key in derived_finality
        }
        dataset = derived.get("cash_flow_dataset")
        if isinstance(dataset, Mapping):
            provenance["cash_flow_dataset"] = {
                key: dataset.get(key)
                for key in (
                    "contract_version",
                    "financial_fingerprint",
                    "full_fingerprint",
                    "window",
                    "fx_confirmation_fingerprint",
                    "effect_store_revision",
                    "run_id",
                    "source_record_count",
                    "completed_record_count",
                )
                if key in dataset
            }
        current["maintenance_provenance"] = provenance
    # A derived-only repair must retain legacy evidence classification and must
    # never upgrade snapshot evidence to v2 complete.
    if isinstance(existing, dict) and "evidence_version" in existing:
        current["evidence_version"] = existing["evidence_version"]
    return current


def _finite_state_value(row: FreshNavRow, field: str) -> Decimal:
    state = row.field_states.get(field, FieldState.missing())
    if state.state != "value":
        raise ValueError(
            "historical_evidence_required: "
            f"{row.nav.date.isoformat()} field {field} is {state.state}"
        )
    try:
        value = Decimal(str(state.value))
    except Exception as exc:
        raise ValueError(
            "historical_evidence_required: "
            f"{row.nav.date.isoformat()} field {field} is not finite"
        ) from exc
    if not value.is_finite():
        raise ValueError(
            "historical_evidence_required: "
            f"{row.nav.date.isoformat()} field {field} is not finite"
        )
    return value


def assert_maintenance_history_evidence(
    rows: Iterable[FreshNavRow],
    *,
    account: str,
    target_dates: Iterable[date],
) -> None:
    """Validate the one fresh NAV fact set used by maintenance calculation."""

    materialized = tuple(rows)
    targets = frozenset(target_dates)
    if not targets:
        raise ValueError("historical_evidence_required: no target dates")

    grouped = rows_by_date(materialized)
    duplicates = {
        nav_date: matches
        for nav_date, matches in grouped.items()
        if len(matches) != 1
    }
    if duplicates:
        evidence = {
            nav_date.isoformat(): [row.record_id for row in matches]
            for nav_date, matches in sorted(duplicates.items())
        }
        raise ValueError(
            "historical_evidence_required: duplicate NAV dependency dates: "
            f"{evidence}"
        )

    for target_date in sorted(targets):
        matches = grouped.get(target_date) or []
        if len(matches) != 1:
            raise ValueError(
                "historical_evidence_required: every target date must resolve "
                f"to exactly one record: {target_date.isoformat()} count={len(matches)}"
            )
        target = matches[0]
        if target.nav.account != account or not target.record_id:
            raise ValueError(
                "historical_evidence_required: target identity mismatch: "
                f"{target_date.isoformat()}"
            )
        for field in BASE_FIELDS:
            _finite_state_value(target, field)

    # Non-target rows before the last target can be selected as predecessor,
    # month/year base, or inception base.  They must not silently trigger the
    # calculator's first-row fallback.
    last_target = max(targets)
    for row in materialized:
        if row.nav.date >= last_target or row.nav.date in targets:
            continue
        total = _finite_state_value(row, "total_value")
        shares = _finite_state_value(row, "shares")
        nav = _finite_state_value(row, "nav")
        if nav <= 0 or shares < 0:
            raise ValueError(
                "historical_evidence_required: invalid NAV dependency values: "
                f"date={row.nav.date.isoformat()}, total={total}, "
                f"shares={shares}, nav={nav}"
            )


def maintenance_dependency_evidence(
    rows: Iterable[FreshNavRow],
    *,
    target_dates: Iterable[date],
) -> list[dict[str, Any]]:
    """Return stable immutable evidence for non-target calculation dependencies."""

    materialized = tuple(rows)
    targets = frozenset(target_dates)
    if not targets:
        return []
    last_target = max(targets)
    evidence = []
    for row in materialized:
        if row.nav.date >= last_target or row.nav.date in targets:
            continue
        evidence.append({
            "date": row.nav.date.isoformat(),
            "record_id": row.record_id,
            "account": row.nav.account,
            "fields": {
                field: state.envelope()
                for field, state in state_subset(
                    row,
                    ("total_value", "shares", "nav"),
                ).items()
            },
        })
    evidence.sort(key=lambda item: (item["date"], item["record_id"]))
    return evidence


def valuation_from_observed(row: FreshNavRow, *, account: str) -> PortfolioValuation:
    """Project immutable persisted base facts back to the runtime valuation shape."""

    observed = {
        field: _finite_state_value(row, field)
        for field in BASE_FIELDS
    }
    total = observed["total_value"]
    cash = observed["cash_value"]
    non_cash = observed["stock_value"]
    if NavCalculator.quantize_money(total) != NavCalculator.quantize_money(cash + non_cash):
        raise ValueError(
            "historical_evidence_required: observed total/cash/noncash decomposition is inconsistent"
        )
    fund = observed["fund_value"]
    equity = non_cash - fund
    return PortfolioValuation(
        account=account,
        total_value_cny=float(total),
        cash_value_cny=float(cash),
        stock_value_cny=float(equity),
        fund_value_cny=float(fund),
        cn_asset_value=float(observed["cn_stock_value"]),
        us_asset_value=float(observed["us_stock_value"]),
        hk_asset_value=float(observed["hk_stock_value"]),
        holdings=[],
        warnings=[],
    )


def recompute_derived_row(
    *,
    context: Any,
    observed: FreshNavRow,
    working_navs: list[NAVHistory],
    run_id: str,
) -> tuple[NAVHistory, Any]:
    """Recompute one row through the canonical service using a fresh S6 dataset."""

    dataset = context.portfolio.build_cash_flow_dataset(
        account=context.account,
        nav_date=observed.nav.date,
        run_id=run_id,
    )
    valuation = valuation_from_observed(observed, account=context.account)
    calculated = context.portfolio.record_nav(
        context.account,
        valuation=valuation,
        nav_date=observed.nav.date,
        persist=False,
        overwrite_existing=False,
        dry_run=True,
        run_id=run_id,
        cash_flow_dataset=dataset,
        nav_history_snapshot=tuple(
            nav.model_copy(deep=True)
            for nav in working_navs
        ),
        nav_write_context=NavWriteContext(
            status="maintenance",
            writer="nav-repair",
            write_reason="nav_history_derived_repair",
            nav_date=observed.nav.date,
            run_id=run_id,
        ),
    )
    target_details = maintenance_details(observed.nav.details, calculated.details)
    target_values = {
        field: (
            target_details if field == "details" else getattr(calculated, field)
        )
        for field in MAINTENANCE_FIELDS
    }
    target_states = {
        field: state_from_value(
            value,
            preserve_missing=observed.field_states.get(field),
        )
        for field, value in target_values.items()
    }
    candidate = nav_with_states(observed.nav, target_states)

    # The calculated projection must reproduce every immutable observed base
    # fact that was present.  No later assignment may "fix" a mismatch.
    for field in BASE_FIELDS:
        original = observed.field_states.get(field, FieldState.missing())
        if original.state != "value":
            continue
        if not NavCalculator.money_equal(
            getattr(calculated, field),
            getattr(observed.nav, field),
        ):
            raise ValueError(
                f"historical_evidence_required: canonical projection changed base field {field}"
            )

    last_nav = max(
        (nav for nav in working_navs if nav.date < candidate.date),
        key=lambda nav: nav.date,
        default=None,
    )
    summary = dataset.summary(last_nav=last_nav)
    nav_index = context.portfolio._build_nav_lookup(working_navs)
    prev_month_end_nav = context.portfolio._find_prev_month_end_nav(
        working_navs,
        candidate.date.year,
        candidate.date.month,
        nav_index=nav_index,
    )
    prev_year_end_nav = context.portfolio._find_year_end_nav(
        working_navs,
        str(candidate.date.year - 1),
        nav_index=nav_index,
    )
    mtd_return_base_nav = context.portfolio._find_mtd_return_base_nav(
        working_navs,
        candidate.date,
        nav_index=nav_index,
    )
    ytd_return_base_nav = context.portfolio._find_ytd_return_base_nav(
        working_navs,
        candidate.date,
        nav_index=nav_index,
    )
    initial_value = context.portfolio._get_initial_value(
        context.account,
        all_navs=working_navs,
    )
    NavCalculator.assert_nav_invariants(
        nav_record=candidate,
        last_nav=last_nav,
        prev_month_end_nav=prev_month_end_nav,
        prev_year_end_nav=prev_year_end_nav,
        mtd_return_base_nav=mtd_return_base_nav,
        ytd_return_base_nav=ytd_return_base_nav,
        daily_cash_flow=summary["daily"],
        monthly_cash_flow=summary["monthly"],
        yearly_cash_flow=summary["yearly"].get(str(candidate.date.year), 0.0),
        gap_cash_flow=summary["gap"],
        initial_value=initial_value,
        cumulative_cash_flow=summary["cumulative"],
        cash_flow_dataset=dataset,
        require_finality=True,
    )
    return candidate, dataset


def maintenance_target_states(
    observed: FreshNavRow,
    candidate: NAVHistory,
) -> dict[str, FieldState]:
    target: dict[str, FieldState] = {}
    for field in MAINTENANCE_FIELDS:
        before = observed.field_states.get(field, FieldState.missing())
        value = getattr(candidate, field)
        after = state_from_value(value, preserve_missing=before)
        target[field] = after
    return target


def changed_states(
    observed: FreshNavRow,
    candidate: NAVHistory,
) -> tuple[dict[str, FieldState], dict[str, FieldState]]:
    original: dict[str, FieldState] = {}
    target: dict[str, FieldState] = {}
    desired = maintenance_target_states(observed, candidate)
    for field, after in desired.items():
        before = observed.field_states.get(field, FieldState.missing())
        if before != after:
            original[field] = before
            target[field] = after
    return original, target


def restricted_patch(
    context: Any,
    *,
    record_id: str,
    states: Mapping[str, FieldState],
    dry_run: bool = False,
) -> Any:
    repository = getattr(context.storage, "nav_history", None)
    patcher = getattr(repository, "patch_nav_maintenance_fields", None)
    if not callable(patcher):
        patcher = getattr(context.storage, "patch_nav_maintenance_fields", None)
    if not callable(patcher):
        raise AttributeError(
            "nav_history maintenance requires restricted field patch support"
        )
    return patcher(
        record_id,
        {field: state.envelope() for field, state in states.items()},
        dry_run=dry_run,
    )
