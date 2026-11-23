# Valuation (CNY)

## Goal
Compute portfolio valuation in CNY with stable, auditable rules.

## Identity (sanity check)
Within tolerance:

`total_value_cny ≈ cash_value_cny + stock_value_cny + fund_value_cny + option_value_cny + pending_asset_value_cny`

## Classification
- cash-like: `*-CASH`, `*-MMF` or `asset_type=cash`
- CN stocks: detected as `cn`
- HK stocks: detected as `hk`
- US stocks: detected as `us`
- other assets: includes the separately recorded Futu pending asset balance (see below)
- options: signed contracts × actual multiplier × closing price × FX
- exchange-traded funds / ETFs: persisted as `exchange_fund`, normalized as `fund`, priced through CN exchange quotes
- open-end funds / fund NAV: persisted as `otc_fund`, normalized as `fund`, priced through fund NAV providers
- legacy/unknown fund rows may still use `fund`; pricing falls back to code-based routing

## FX
- Convert non-CNY assets with current FX rate (USDCNY / HKDCNY).
- Missing FX MUST surface as warning (do not silently treat as 0).

## Timezone
All business dates are Beijing time (Asia/Shanghai).

## Futu daily NAV

The scheduled morning run captures verified Futu cash, MMF and positions
between 08:10 and 09:00 Beijing time. The account cash and MMF balances are
separate holdings and both contribute to cash-like value. Futu option prices
use the requested market session's close, or the latest prior close when that
market is closed. Intraday report prices wait until 16:15 New York time for
US options or 16:30 Hong Kong time for HK options before reading that day’s
regular-session close. Late NAV runs require archived valuation evidence.
The NAV holdings snapshot keeps each option's verified contract multiplier for
both capture verification and valuation.

## Futu pending assets

Futu synchronization automatically replaces a separate holding,
CNY-FUTU-PENDING (富途在途资产), from the current account-info
pending_asset. This is an account aggregate, not a redemption-only or
spendable-cash balance. It uses the existing other asset type and
另类资产 class; it is excluded from cash/MMF totals but included in total
portfolio value, priced holdings, daily reports and NAV evidence. The runtime
pending_asset_value_cny subtotal is separate from stock_value_cny, so pending
does not inflate stock allocation. The historical compatibility stock_value
column continues to mean all non-cash assets (including pending), with the
pending subtotal disclosed in NAV details; it is never added again to that
persisted column.

The provider requests account-info in CNH, as for fund_assets; PM records
that RMB value in CNY at unit price 1. A missing, negative, nonfinite pending
amount or a non-CNH/CNY aggregate currency fails synchronization instead of
assuming zero. A genuine zero replaces the prior amount. Each account and
broker has one business-key row; retries replace rather than increment it.
No additional enable switch or manually entered transfer receipt is required.

The sync receipt checks the stored pending row's amount, type and currency.
It also checks the same broker response's identity:

cash + fund_assets + market_val + pending_asset ≈ total_assets

All five fields use the same reporting currency and snapshot. The absolute
rounding tolerance is RMB 0.03 (four cent-rounded components and one rounded
total can differ by up to 0.025). Missing total/component data is unavailable;
a larger discrepancy is untrusted and makes the sync unsuccessful. The
existing sync receipt displays the discrepancy. This broker decomposition
check is distinct from PM's closing-price NAV: intraday broker market values
must not be substituted for NAV closing prices or used as a balancing plug.

The former shared-file CNY-MMF-TRANSIT valuation adjustment is no longer
injected into holdings or NAV. Its manual evidence may remain available to
other consumers, but PM does not add it on top of pending_asset. Cash-flow
rows still follow their existing accounting workflow; this sync creates no
cash-flow entry. A failed sync cannot provide a successful formal NAV capture.
