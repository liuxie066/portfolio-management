"""Known holding denominations, not an FX-capability or current-tender list.

Static Foundation/ICU snapshot exported on 2026-10-26 from macOS ICU 78
(/usr/share/icu/icudt78l.dat) via Locale.Currency.isoCurrencies.identifier.
The source includes historical currencies and CNH; retaining them preserves
existing valid records. XTS (testing) and XXX (no currency) are excluded.
This is an offline accepted-code policy, not a claim to mirror the latest ISO
register. Updates require code review; no runtime catalog or FX lookup occurs.
"""

HOLDING_CURRENCIES = frozenset("""
ADP AED AFA AFN ALK ALL AMD ANG AOA AOK AON AOR ARA ARL ARM ARP ARS ATS AUD AWG AZM
AZN BAD BAM BAN BBD BDT BEC BEF BEL BGL BGM BGN BGO BHD BIF BMD BND BOB BOL BOP BOV
BRB BRC BRE BRL BRN BRR BRZ BSD BTN BUK BWP BYB BYN BYR BZD CAD CDF CHE CHF CHW CLE
CLF CLP CNH CNX CNY COP COU CRC CSD CSK CUC CUP CVE CYP CZK DDM DEM DJF DKK DOP DZD
ECS ECV EEK EGP ERN ESA ESB ESP ETB EUR FIM FJD FKP FRF GBP GEK GEL GHC GHS GIP GMD
GNF GNS GQE GRD GTQ GWE GWP GYD HKD HNL HRD HRK HTG HUF IDR IEP ILP ILR ILS INR IQD
IRR ISJ ISK ITL JMD JOD JPY KES KGS KHR KMF KPW KRH KRO KRW KWD KYD KZT LAK LBP LKR
LRD LSL LSM LTL LTT LUC LUF LUL LVL LVR LYD MAD MAF MCF MDC MDL MGA MGF MKD MKN MLF
MMK MNT MOP MRO MRU MTL MTP MUR MVP MVR MWK MXN MXP MXV MYR MZE MZM MZN NAD NGN NIC
NIO NLG NOK NPR NZD OMR PAB PEI PEN PES PGK PHP PKR PLN PLZ PTE PYG QAR RHD ROL RON
RSD RUB RUR RWF SAR SBD SCR SDD SDG SDP SEK SGD SHP SIT SKK SLE SLL SOS SRD SRG SSP
STD STN SUR SVC SYP SZL THB TJR TJS TMM TMT TND TOP TPE TRL TRY TTD TWD TZS UAH UAK
UGS UGX USD USN USS UYI UYP UYU UYW UZS VEB VED VEF VES VND VNN VUV WST XAF XAG XAU
XBA XBB XBC XBD XCD XCG XDR XEU XFO XFU XOF XPD XPF XPT XRE XSU XUA YDD YER YUD YUM
YUN YUR ZAL ZAR ZMK ZMW ZRN ZRZ ZWD ZWG ZWL ZWR
""".split())


# A reviewed spelling correction is a proposal, never automatic write authority.
CURRENCY_TYPO_POLICY_VERSION = "currency-typo.v1"
CURRENCY_TYPO_CORRECTIONS = {"HDK": "HKD"}


def is_known_holding_currency(value: object) -> bool:
    return isinstance(value, str) and value.strip().upper() in HOLDING_CURRENCIES
