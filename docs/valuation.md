# Valuation (CNY)

## Goal
Compute portfolio valuation in CNY with stable, auditable rules.

## Identity (sanity check)
Within tolerance:

`total_value_cny ≈ cash_value_cny + stock_value_cny + fund_value_cny + option_value_cny`

## Classification
- cash-like: `*-CASH`, `*-MMF` or `asset_type=cash`
- CN stocks: detected as `cn`
- HK stocks: detected as `hk`
- US stocks: detected as `us`
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
