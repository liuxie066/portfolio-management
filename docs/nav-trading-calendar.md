# NAV trading-date policy

## Goal and scope

For a Beijing-date NAV, CN, HK, or US being a trading market on that date is
sufficient to run the existing daily NAV workflow. The automatic date is the
latest eligible date strictly before the run date. The rule also applies when
Cash Flow effects require the preceding final NAV date. All other NAV write,
duplicate, valuation, receipt, and authorization gates remain in force.

The NAV date is a **market trading-date label**, queried with that same date
in each market's Futu calendar; it is not a test of whether a session is
physically open during that Beijing civil day. A US Friday session that extends
into Beijing Saturday belongs to Friday's NAV label. Saturday remains closed
for this date policy, even during those US trading hours.

This change does not alter `MarketTimeUtil`'s intraday quote-cache policy,
systemd timer days, market-specific NAVs, broker trades, or production state.
`force_non_business_day` continues to override a confirmed all-closed date;
it does not make missing calendar evidence trustworthy.

## Current facts and source

- `BusinessCalendarService` owns `default_nav_date`, `previous_business_day`,
  `is_business_day`, and `explain`. `DailyNavJobService` uses it to select and
  gate the NAV date; `CashFlowEffectService` uses it to find the preceding NAV.
- The previous runtime policy was weekends plus `calendar.holidays`. That
  single list cannot represent CN/HK/US separately. The historical decision
  in `decisions/2026-05-25-daily-nav-business-date.md` remains historical.
- Futu OpenD's `OpenQuoteContext.request_trading_days(market, start, end)`
  returns market-specific dates and `trade_date_type`. A read-only query on the
  existing remote OpenD on 2026-09-26 returned CN closed and HK/US open for
  2026-09-25. See [Futu API documentation](https://openapi.futunn.com/futu-api-doc/en/quote/request-trading-days.html).
- Futu documents that this calendar excludes ordinary weekends and holidays
  but **does not include temporary market closures**. The API is a date
  calendar, not a live market-state source.

## Ownership and reuse

Search scope: `src/app/business_calendar_service.py`, its two runtime callers,
`src/config.py`, `src/market_time.py`, Futu adapters, relevant tests, and NAV
operation documents. No existing OpenD trading-calendar adapter was found.

| Concept or implementation | Decision |
| --- | --- |
| NAV date traversal and explanation | Reuse `BusinessCalendarService` methods and existing `calendar` result field. |
| Calendar query endpoint and connection settings | Reuse Futu SDK `request_trading_days` and `futu.opend.host` / `futu.opend.port`. Add only the small adapter needed in `BusinessCalendarService`; no new dependency. |
| Tri-state market evidence in result | Add `calendar.markets` (`CN`, `HK`, `US`: true/false/null) because a single boolean cannot distinguish an unavailable market from a confirmed closure. |
| Intraday open/session checks | Keep `MarketTimeUtil` as owner; date-level NAV policy does not change its callers. |
| `calendar.holidays` | Retire as a runtime NAV authority; an entry cannot veto another open market. Keep the historical decision document untouched and update current operator guidance. |

## Decision and failure behavior

For each candidate Beijing date, query the same local date in Futu's CN, HK,
and US calendars. Weekend trading-date labels are closed without an OpenD
call. Do not retain calendar evidence across jobs; a long-lived Cash Flow
service must query again when another scan or confirmation begins.

| Evidence for the date | NAV date result |
| --- | --- |
| At least one market confirmed trading | Eligible, even if another query is unavailable. |
| All three confirmed closed | Ineligible; skip an explicit date or continue searching an automatic date. |
| No confirmed trading market and one or more unavailable/malformed responses | Unknown; fail the job before any NAV write. |

An OpenD connection/SDK failure is also unknown. The provider must close its
quote context. A successful empty list means closed. A nonempty list means
open only when every row has the requested `time` and a recognized
`trade_date_type` (`WHOLE`, `MORNING`, or `AFTERNOON`); half days count as open.
Unexpected dates, missing/unknown types, malformed rows, and non-success
return codes mean unknown, never closed. Explicit dates and
automatic dates use the same rule. `force_non_business_day` applies only to a
confirmed all-closed date. The existing service facade converts calendar
exceptions into failed job results and receipt handling; direct callers may
receive the exception. Cash Flow effect application must require the final NAV
for the latest date eligible under this same rule when the account already has
NAV history; preserve the existing no-history bootstrap exception. In
particular, an old
`pending`, `blocked`, or `previewed` effect must recheck that condition before
confirmation writes holdings. Perform the recheck under confirmation's account
locks, before transitioning the effect to `applying`; include historical apply
when it mutates holdings. Scan-time state alone is insufficient. Calendar
unavailability at confirmation stops that write. An earlier transient scan
failure remains retryable through a later scan, but no stored state
grants write authority without the fresh confirmation check.

One-date requests are sufficient for normal lookback. The existing 366-day
search bound remains. If measured lookbacks approach the OpenD calendar rate
limit, batch the date window without changing the decision table.

Rejected alternatives: a single CN calendar or union of manually configured
holidays violates the 2026-09-25 case; treating provider failures as closed
could silently skip an eligible NAV; treating failures as open could permit an
unjustified write. Live `get_market_state` cannot answer historical NAV dates.

## Implementation slices and acceptance

1. **Calendar evidence** — extend `BusinessCalendarService` with an injected
   market-date provider and the Futu runtime adapter. Verify CN closed/HK open,
   US-only open, all closed, unavailable, malformed rows, automatic lookback,
   Friday/Saturday date labels, and quote-context close. Covers the
   eligible-date and fail-closed signals.
2. **Consumers and operations** — keep `DailyNavJobService` and
   `CashFlowEffectService` on the shared runtime calendar, retire the old
   runtime holiday setting, and update current operating documents. Verify an
   automatic NAV dated 2026-09-25 proceeds past the calendar gate, a fully
   closed date skips, calendar failures block writes, and preceding-NAV
   finality uses the same selected date at confirmation, including existing
   `pending`/`blocked` effects and a transient OpenD failure. Covers integration and operator
   evidence signals; depends on slice 1.

Run focused calendar/NAV/Cash Flow tests per slice, then the repository's
required pytest, compileall, Ruff, and `git diff --check` gates on the final
diff. Review all task changes against the frozen design and isolated base.

## Risks and open questions

- Temporary exchange closures are absent from the chosen Futu calendar.
  Owner: product/operations; destination: a later market-state or exchange
  incident-source decision if the product must treat those closures as dates.
- The production OpenD/SDK must be available for an automatic NAV. Existing
  scheduled Futu sync already depends on OpenD; absence remains a visible
  failed run, never a guessed calendar result.
- No product decision remains open for this slice. Source delivery, release,
  and production upgrade require separate authorization.
