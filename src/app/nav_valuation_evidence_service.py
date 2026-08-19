"""Immutable normalized-valuation evidence for guarded NAV replay."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import json
import os
from pathlib import Path
import re
from typing import Any, Callable, Mapping, Optional, Sequence
from urllib.parse import quote, unquote

from src import config
from src.domain.snapshot_contracts import (
    NormalizedValuationSnapshot,
    digest_payload,
)


EVIDENCE_VERSION = "pm.nav_valuation_evidence.v1"
REFERENCE_PREFIX = "nav-valuation-evidence:v1"
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


def _date_text(value: Any) -> str:
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.isoformat()
    return date.fromisoformat(str(value)[:10]).isoformat()


# ``holdings_provenance.source_fetch_time`` is the wall-clock moment the
# holdings were read from Feishu. It changes on every run and is not part of
# the *content* (the holdings themselves are fingerprinted by
# ``raw_record_digest`` / ``normalized_holdings_digest``). If it stayed in the
# evidence payload, the preview->write CAS digest would never match because
# each invocation re-fetches holdings at a different timestamp. We normalize it
# away so the evidence artifact digest is deterministic across runs.
_EVIDENCE_PROVENANCE_EXCLUDED_KEYS = frozenset({"source_fetch_time"})


def _evidence_valuation_payload(
    normalized_valuation: NormalizedValuationSnapshot,
) -> dict[str, Any]:
    """Return the canonical valuation payload with volatile provenance metadata
    removed so the evidence digest is content-deterministic."""
    payload = normalized_valuation.canonical_payload()
    provenance = dict(payload.get("holdings_provenance") or {})
    removed = {
        key: provenance.pop(key)
        for key in _EVIDENCE_PROVENANCE_EXCLUDED_KEYS
        if key in provenance
    }
    if removed:
        payload = dict(payload)
        payload["holdings_provenance"] = provenance
    return payload


class NavValuationEvidenceStore:
    """Save and load digest-addressed evidence beneath the runtime data root."""

    def __init__(self, root: Optional[Path] = None) -> None:
        self.root = root or (config.get_data_dir() / "nav_valuation_evidence")

    @staticmethod
    def _reference(*, account: str, nav_date: str, digest: str) -> str:
        return f"{REFERENCE_PREFIX}:{quote(account, safe='')}:{nav_date}:{digest}"

    @staticmethod
    def _parse_reference(reference: str) -> tuple[str, str, str]:
        parts = str(reference or "").split(":")
        if len(parts) != 5 or ":".join(parts[:2]) != REFERENCE_PREFIX:
            raise ValueError("invalid NAV valuation evidence reference")
        encoded_account, nav_date, digest = parts[2:]
        account = unquote(encoded_account)
        if (
            not account
            or quote(account, safe="") != encoded_account
            or account in {".", ".."}
            or "/" in account
            or "\\" in account
        ):
            raise ValueError("invalid NAV valuation evidence account")
        _date_text(nav_date)
        if not _DIGEST_RE.fullmatch(digest):
            raise ValueError("invalid NAV valuation evidence digest")
        return account, nav_date, digest

    def _path(self, *, account: str, nav_date: str, digest: str) -> Path:
        return self.root / account / nav_date / f"{digest}.json"

    def prepare(
        self,
        *,
        account: str,
        nav_date: Any,
        source_run_id: str,
        snapshot_time: str,
        holdings_digest: str,
        cash_flow_financial_fingerprint: str,
        source_effect_store_revision: str,
        normalized_valuation: NormalizedValuationSnapshot,
        preparation: str,
        captured_at: Optional[str] = None,
        source_receipt_key: Optional[str] = None,
    ) -> dict[str, Any]:
        account = str(account or "").strip()
        nav_date_text = _date_text(nav_date)
        source_run_id = str(source_run_id or "").strip()
        snapshot_time = str(snapshot_time or "").strip()
        holdings_digest = str(holdings_digest or "").strip()
        cash_flow_financial_fingerprint = str(
            cash_flow_financial_fingerprint or ""
        ).strip()
        source_effect_store_revision = str(
            source_effect_store_revision or ""
        ).strip()
        preparation = str(preparation or "").strip()
        source_receipt_key = str(source_receipt_key or "").strip() or None
        if not account or not source_run_id or not snapshot_time:
            raise ValueError("NAV valuation evidence scope is incomplete")
        if not _DIGEST_RE.fullmatch(holdings_digest):
            raise ValueError("NAV valuation evidence holdings digest is invalid")
        if not _DIGEST_RE.fullmatch(cash_flow_financial_fingerprint):
            raise ValueError("NAV valuation evidence cash-flow fingerprint is invalid")
        if not source_effect_store_revision:
            raise ValueError("NAV valuation evidence effect revision is required")
        if not isinstance(normalized_valuation, NormalizedValuationSnapshot):
            raise TypeError("NAV valuation evidence requires normalized valuation")
        normalized_valuation.assert_official_eligible(
            expected_source="valuation_service"
        )
        if normalized_valuation.account != account:
            raise ValueError("NAV valuation evidence account mismatch")
        valuation_payload = _evidence_valuation_payload(normalized_valuation)
        valuation_holdings_digest = str(
            (valuation_payload.get("holdings_provenance") or {}).get(
                "normalized_holdings_digest"
            )
            or ""
        )
        if valuation_holdings_digest != holdings_digest:
            raise ValueError("NAV valuation evidence holdings digest mismatch")
        datetime.fromisoformat(snapshot_time.replace("Z", "+00:00"))
        historical_receipt = preparation == "historical_receipt_recovery"
        expected_source_receipt_key = None
        source_suffix = f":{account}"
        if historical_receipt:
            if not source_run_id.endswith(source_suffix):
                raise ValueError("historical receipt source run scope mismatch")
            expected_source_receipt_key = (
                f"nav:{source_run_id[:-len(source_suffix)]}"
            )
            if source_receipt_key != expected_source_receipt_key:
                raise ValueError("historical receipt key mismatch")
        elif source_receipt_key is not None:
            raise ValueError("source receipt key is not allowed for this preparation")

        body = {
            "schema_version": EVIDENCE_VERSION,
            "account": account,
            "nav_date": nav_date_text,
            "source_run_id": source_run_id,
            "snapshot_time": snapshot_time,
            "captured_at": captured_at or snapshot_time,
            "holdings_digest": holdings_digest,
            "cash_flow_financial_fingerprint": cash_flow_financial_fingerprint,
            "source_effect_store_revision": source_effect_store_revision,
            "valuation_digest": digest_payload(valuation_payload),
            "valuation": valuation_payload,
            "preparation": preparation,
        }
        if not body["preparation"]:
            raise ValueError("NAV valuation evidence preparation is required")
        if source_receipt_key is not None:
            body["source_receipt_key"] = source_receipt_key
        artifact_digest = digest_payload(body)
        artifact = {**body, "artifact_digest": artifact_digest}
        return {
            "valuation_ref": self._reference(
                account=account,
                nav_date=nav_date_text,
                digest=artifact_digest,
            ),
            "artifact_digest": artifact_digest,
            "artifact": artifact,
        }

    def save(self, prepared: Mapping[str, Any]) -> dict[str, Any]:
        artifact = dict(prepared.get("artifact") or {})
        reference = str(prepared.get("valuation_ref") or "")
        account, nav_date, digest = self._parse_reference(reference)
        self._validate_artifact(
            artifact,
            expected_account=account,
            expected_nav_date=nav_date,
            expected_digest=digest,
        )
        path = self._path(account=account, nav_date=nav_date, digest=digest)
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(
            artifact,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            existing = path.read_bytes()
            if existing != encoded:
                raise FileExistsError("NAV valuation evidence digest collision")
        else:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        return {
            "valuation_ref": reference,
            "artifact_digest": digest,
            "path": str(path),
        }

    def load(
        self,
        reference: str,
        *,
        expected_account: Optional[str] = None,
        expected_nav_date: Optional[Any] = None,
    ) -> dict[str, Any]:
        account, nav_date, digest = self._parse_reference(reference)
        if expected_account is not None and account != str(expected_account):
            raise ValueError("NAV valuation evidence account scope mismatch")
        if expected_nav_date is not None and nav_date != _date_text(expected_nav_date):
            raise ValueError("NAV valuation evidence date scope mismatch")
        path = self._path(account=account, nav_date=nav_date, digest=digest)
        artifact = json.loads(path.read_text(encoding="utf-8"))
        normalized = self._validate_artifact(
            artifact,
            expected_account=account,
            expected_nav_date=nav_date,
            expected_digest=digest,
        )
        return {
            "valuation_ref": reference,
            "artifact_digest": digest,
            "artifact": artifact,
            "normalized_valuation": normalized,
        }

    @staticmethod
    def _validate_artifact(
        artifact: Mapping[str, Any],
        *,
        expected_account: str,
        expected_nav_date: str,
        expected_digest: str,
    ) -> NormalizedValuationSnapshot:
        if artifact.get("schema_version") != EVIDENCE_VERSION:
            raise ValueError("unsupported NAV valuation evidence version")
        if artifact.get("account") != expected_account:
            raise ValueError("NAV valuation evidence account mismatch")
        if artifact.get("nav_date") != expected_nav_date:
            raise ValueError("NAV valuation evidence date mismatch")
        for field in (
            "source_run_id",
            "snapshot_time",
            "captured_at",
            "source_effect_store_revision",
        ):
            if not str(artifact.get(field) or "").strip():
                raise ValueError(f"NAV valuation evidence {field} is required")
        datetime.fromisoformat(
            str(artifact["snapshot_time"]).replace("Z", "+00:00")
        )
        datetime.fromisoformat(
            str(artifact["captured_at"]).replace("Z", "+00:00")
        )
        preparation = artifact.get("preparation")
        if preparation not in {
            "cash_flow_gate_failure",
            "historical_recovery",
            "historical_receipt_recovery",
        }:
            raise ValueError("NAV valuation evidence preparation is invalid")
        source_receipt_key = str(artifact.get("source_receipt_key") or "").strip()
        if preparation == "historical_receipt_recovery":
            source_run_id = str(artifact.get("source_run_id") or "")
            source_suffix = f":{expected_account}"
            if not source_run_id.endswith(source_suffix):
                raise ValueError("NAV valuation evidence source run scope mismatch")
            if source_receipt_key != f"nav:{source_run_id[:-len(source_suffix)]}":
                raise ValueError("NAV valuation evidence source receipt mismatch")
        elif source_receipt_key:
            raise ValueError("NAV valuation evidence source receipt is not allowed")
        holdings_digest = str(artifact.get("holdings_digest") or "")
        if not _DIGEST_RE.fullmatch(holdings_digest):
            raise ValueError("NAV valuation evidence holdings digest is invalid")
        if not _DIGEST_RE.fullmatch(
            str(artifact.get("cash_flow_financial_fingerprint") or "")
        ):
            raise ValueError("NAV valuation evidence cash-flow fingerprint is invalid")
        body = {key: value for key, value in artifact.items() if key != "artifact_digest"}
        actual_digest = digest_payload(body)
        if artifact.get("artifact_digest") != actual_digest:
            raise ValueError("NAV valuation evidence artifact digest mismatch")
        if actual_digest != expected_digest:
            raise ValueError("NAV valuation evidence reference digest mismatch")
        normalized = NormalizedValuationSnapshot._from_evidence_payload(
            artifact.get("valuation") or {},
            expected_digest=str(artifact.get("valuation_digest") or ""),
        )
        if normalized.account != expected_account:
            raise ValueError("NAV valuation evidence valuation account mismatch")
        valuation_holdings_digest = str(
            (normalized.canonical_payload().get("holdings_provenance") or {}).get(
                "normalized_holdings_digest"
            )
            or ""
        )
        if valuation_holdings_digest != holdings_digest:
            raise ValueError("NAV valuation evidence holdings digest mismatch")
        if preparation == "historical_receipt_recovery":
            holdings_provenance = dict(
                normalized.canonical_payload().get("holdings_provenance") or {}
            )
            if (
                holdings_provenance.get("source_mode") != "nav_receipt_outbox"
                or not _DIGEST_RE.fullmatch(
                    str(holdings_provenance.get("raw_record_digest") or "")
                )
                or int(holdings_provenance.get("record_count") or 0) <= 0
            ):
                raise ValueError(
                    "NAV valuation evidence historical holdings provenance is invalid"
                )
        return normalized


def _rows(payload: Any) -> list[dict[str, Any]]:
    if hasattr(payload, "to_dict"):
        return [dict(row) for row in payload.to_dict(orient="records")]
    if isinstance(payload, list):
        return [dict(row) for row in payload]
    raise RuntimeError("historical price provider returned unsupported rows")


def _fetch_opend_daily_closes(
    symbols: Sequence[str],
    nav_date: date,
) -> dict[str, dict[str, Any]]:
    try:
        import futu as futu_sdk
    except ImportError:
        try:
            import moomoo as futu_sdk
        except ImportError as exc:
            raise RuntimeError("Futu OpenAPI SDK is required for historical prices") from exc

    ctx = futu_sdk.OpenQuoteContext(
        host=config.get("futu.opend.host", "127.0.0.1"),
        port=int(config.get_int("futu.opend.port", 11111) or 11111),
    )
    result: dict[str, dict[str, Any]] = {}
    try:
        for symbol in symbols:
            response = ctx.request_history_kline(
                code=symbol,
                start=nav_date.isoformat(),
                end=nav_date.isoformat(),
                ktype=futu_sdk.KLType.K_DAY,
                autype=futu_sdk.AuType.NONE,
                max_count=1,
            )
            ret, data = response[:2]
            if ret != getattr(futu_sdk, "RET_OK", 0):
                raise RuntimeError(f"OpenD historical close failed for {symbol}: {data}")
            matches = [
                row
                for row in _rows(data)
                if str(row.get("time_key") or "")[:10] == nav_date.isoformat()
            ]
            if len(matches) != 1:
                raise ValueError(
                    f"OpenD returned no unique exact-date close for {symbol}"
                )
            result[symbol] = {
                "fact_date": nav_date.isoformat(),
                "price": matches[0].get("close"),
            }
    finally:
        ctx.close()
    return result


def _fetch_eastmoney_fund_nav(code: str, nav_date: date) -> dict[str, Any]:
    import requests

    response = requests.get(
        "https://api.fund.eastmoney.com/f10/lsjz",
        params={
            "fundCode": code,
            "pageIndex": 1,
            "pageSize": 100,
            "startDate": "",
            "endDate": nav_date.isoformat(),
        },
        headers={
            "Referer": f"https://fundf10.eastmoney.com/jjjz_{code}.html",
            "User-Agent": "portfolio-management historical NAV evidence",
        },
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    rows = ((payload.get("Data") or {}).get("LSJZList") or [])
    eligible = [
        row
        for row in rows
        if str(row.get("FSRQ") or "") <= nav_date.isoformat()
        and str(row.get("DWJZ") or "").strip()
    ]
    if not eligible:
        raise ValueError(f"Eastmoney has no fund NAV on or before {nav_date}: {code}")
    row = max(eligible, key=lambda item: str(item.get("FSRQ")))
    return {"fact_date": str(row["FSRQ"]), "price": row["DWJZ"]}


def _opend_symbol(holding: Any) -> tuple[str, str]:
    from src.asset_utils import normalize_code
    from src.models import AssetType
    from src.pricing.classifier import is_etf

    code = normalize_code(str(holding.asset_id or ""))
    asset_type = holding.asset_type
    if asset_type == AssetType.HK_STOCK and code.isdigit() and len(code) == 5:
        return f"HK.{code}", "HKD"
    if asset_type == AssetType.US_STOCK and code:
        return f"US.{code}", "USD"
    if asset_type in {AssetType.A_STOCK, AssetType.EXCHANGE_FUND} or is_etf(code):
        if not (code.isdigit() and len(code) == 6):
            raise ValueError(f"invalid mainland exchange code: {holding.asset_id}")
        return f"{'SH' if code.startswith(('5', '6')) else 'SZ'}.{code}", "CNY"
    raise ValueError(f"unsupported OpenD historical asset: {holding.asset_id}")


def build_historical_price_snapshot(
    holdings: Sequence[Any],
    *,
    nav_date: date,
    valuation_as_of: str,
    usdcny: Any,
    hkdcny: Any,
    opend_loader: Optional[
        Callable[[Sequence[str], date], Mapping[str, Mapping[str, Any]]]
    ] = None,
    fund_loader: Optional[Callable[[str, date], Mapping[str, Any]]] = None,
) -> dict[str, dict[str, Any]]:
    """Build one fail-closed, deterministic historical price snapshot."""

    from src.models import AssetType
    from src.asset_utils import normalize_code
    from src.pricing.classifier import is_etf
    from src.pricing.fixed import (
        get_cash_price_with_rates,
        get_crypto_value_price_with_rates,
        get_mmf_price_with_rates,
    )
    from src.pricing.payload import normalize_price_payload, positive_finite_decimal

    datetime.fromisoformat(str(valuation_as_of).replace("Z", "+00:00"))
    rates = {
        "USDCNY": float(positive_finite_decimal(usdcny, "USDCNY")),
        "HKDCNY": float(positive_finite_decimal(hkdcny, "HKDCNY")),
    }
    prices: dict[str, dict[str, Any]] = {}
    exchange_jobs: dict[str, tuple[str, str]] = {}
    fund_codes: set[str] = set()
    fund_types = {AssetType.FUND, AssetType.OTC_FUND, AssetType.CN_FUND}

    for holding in holdings:
        if Decimal(str(holding.quantity)) == 0:
            continue
        code = str(holding.asset_id or "").strip()
        normalized_code = normalize_code(code)
        if holding.asset_type == AssetType.CASH:
            payload = get_cash_price_with_rates(code, rates)
        elif holding.asset_type == AssetType.MMF:
            payload = get_mmf_price_with_rates(code, rates)
        elif holding.asset_type == AssetType.CRYPTO:
            payload = get_crypto_value_price_with_rates(code, rates)
        elif holding.asset_type in {
            AssetType.A_STOCK,
            AssetType.HK_STOCK,
            AssetType.US_STOCK,
            AssetType.EXCHANGE_FUND,
        } or is_etf(normalized_code):
            symbol, currency = _opend_symbol(holding)
            if str(holding.currency or "").upper() != currency:
                raise ValueError(f"historical price currency mismatch for {code}")
            exchange_jobs[code] = (symbol, currency)
            continue
        elif holding.asset_type in fund_types:
            if str(holding.currency or "").upper() != "CNY":
                raise ValueError(f"historical fund currency mismatch for {code}")
            fund_codes.add(code)
            continue
        else:
            raise ValueError(f"unsupported historical price asset: {code}")
        if str(holding.currency or "").upper() != payload["currency"]:
            raise ValueError(f"historical price currency mismatch for {code}")
        payload.update(
            {
                "provider": "fixed",
                "fact_date": nav_date.isoformat(),
                "retrieved_at": valuation_as_of,
                "fetched_at": valuation_as_of,
            }
        )
        prices[code] = normalize_price_payload(payload)

    load_opend = opend_loader or _fetch_opend_daily_closes
    close_rows = load_opend(
        tuple(sorted({symbol for symbol, _ in exchange_jobs.values()})),
        nav_date,
    ) if exchange_jobs else {}
    for code, (symbol, currency) in exchange_jobs.items():
        row = dict(close_rows.get(symbol) or {})
        if str(row.get("fact_date") or "") != nav_date.isoformat():
            raise ValueError(f"OpenD close fact date mismatch for {symbol}")
        price = positive_finite_decimal(row.get("price"), f"{symbol} close")
        exchange_rate = Decimal("1") if currency == "CNY" else Decimal(
            str(rates[f"{currency}CNY"])
        )
        prices[code] = normalize_price_payload(
            {
                "code": code,
                "price": price,
                "currency": currency,
                "exchange_rate": exchange_rate,
                "cny_price": price * exchange_rate,
                "market_type": "historical_exchange",
                "source": "futu_opend_history",
                "provider": "futu_opend",
                "fact_date": nav_date.isoformat(),
                "retrieved_at": valuation_as_of,
                "fetched_at": valuation_as_of,
            }
        )

    load_fund = fund_loader or _fetch_eastmoney_fund_nav
    for code in sorted(fund_codes):
        row = dict(load_fund(code, nav_date) or {})
        fact_date = date.fromisoformat(str(row.get("fact_date") or ""))
        if fact_date > nav_date:
            raise ValueError(f"Eastmoney fund NAV is after target date: {code}")
        price = positive_finite_decimal(row.get("price"), f"{code} fund NAV")
        prices[code] = normalize_price_payload(
            {
                "code": code,
                "price": price,
                "currency": "CNY",
                "exchange_rate": 1,
                "cny_price": price,
                "market_type": "fund",
                "source": "eastmoney_history",
                "provider": "eastmoney",
                "fact_date": fact_date.isoformat(),
                "retrieved_at": valuation_as_of,
                "fetched_at": valuation_as_of,
            }
        )
    return prices


class HistoricalNavValuationEvidenceService:
    """Prepare the one legacy valuation artifact through current gates."""

    def __init__(
        self,
        *,
        storage: Any,
        portfolio: Any,
        holdings_preflight: Any = None,
        evidence_store: Optional[NavValuationEvidenceStore] = None,
        receipt_store: Any = None,
        price_snapshot_builder: Callable[..., dict[str, dict[str, Any]]] = (
            build_historical_price_snapshot
        ),
    ) -> None:
        self.storage = storage
        self.portfolio = portfolio
        self.holdings_preflight = holdings_preflight
        self.evidence_store = evidence_store or NavValuationEvidenceStore()
        self.receipt_store = receipt_store
        self.price_snapshot_builder = price_snapshot_builder

    def _load_receipt_holdings(
        self,
        *,
        account: str,
        nav_date: date,
        source_run_id: str,
        expected_holdings_digest: str,
    ) -> tuple[Any, str]:
        from src.app.holdings_nav_preflight_service import (
            ValidatedHoldingsSnapshot,
        )
        from src.app.operation_state_store import OperationStateStore

        suffix = f":{account}"
        if not source_run_id.endswith(suffix) or len(source_run_id) == len(suffix):
            raise ValueError("historical receipt source run scope mismatch")
        parent_run_id = source_run_id[: -len(suffix)]
        receipt_key = f"nav:{parent_run_id}"
        receipt_store = self.receipt_store or OperationStateStore()
        receipt = receipt_store.get_nav_receipt(receipt_key)
        if not receipt:
            raise ValueError("historical NAV receipt not found")
        payload = dict(receipt.get("payload") or {})
        if (
            payload.get("run_id") != parent_run_id
            or str(payload.get("date") or "")[:10] != nav_date.isoformat()
            or payload.get("dry_run") is not False
            or payload.get("confirm") is not True
            or payload.get("success") is not False
            or payload.get("status") not in {"failed", "partial"}
        ):
            raise ValueError("historical NAV receipt scope mismatch")
        matches = [
            dict(item)
            for item in list(payload.get("items") or [])
            if isinstance(item, Mapping) and item.get("run_id") == source_run_id
        ]
        if len(matches) != 1:
            raise ValueError("historical NAV receipt account item is not unique")
        item = matches[0]
        if (
            item.get("account") != account
            or str(item.get("date") or "")[:10] != nav_date.isoformat()
            or item.get("dry_run") is not False
            or item.get("confirm") is not True
            or item.get("success") is not False
            or item.get("status") != "failed"
        ):
            raise ValueError("historical NAV receipt account scope mismatch")
        holdings_preflight = dict(item.get("holdings_preflight") or {})
        snapshot = ValidatedHoldingsSnapshot.from_public_validation(
            account=account,
            validation=dict(holdings_preflight.get("validation") or {}),
            provenance=dict(holdings_preflight.get("holdings_snapshot") or {}),
            expected_normalized_holdings_digest=expected_holdings_digest,
        )
        return snapshot, receipt_key

    def prepare(
        self,
        *,
        account: str,
        nav_date: Any,
        source_run_id: str,
        expected_holdings_digest: str,
        expected_cash_flow_fingerprint: str,
        source_effect_store_revision: str,
        valuation_as_of: str,
        usdcny: Any,
        hkdcny: Any,
        write: bool = False,
        confirm: bool = False,
        expected_digest: Optional[str] = None,
    ) -> dict[str, Any]:
        from src.app.holdings_nav_preflight_service import HoldingsNavPreflightService
        from src.pricing.payload import positive_finite_decimal

        account = str(account or "").strip()
        source_run_id = str(source_run_id or "").strip()
        expected_holdings_digest = str(expected_holdings_digest or "").strip()
        expected_cash_flow_fingerprint = str(
            expected_cash_flow_fingerprint or ""
        ).strip()
        source_effect_store_revision = str(
            source_effect_store_revision or ""
        ).strip()
        valuation_as_of = str(valuation_as_of or "").strip()
        expected_digest = str(expected_digest or "").strip() or None
        target_date = date.fromisoformat(str(nav_date)[:10])
        if not account or not source_run_id or not source_effect_store_revision:
            raise ValueError("historical evidence scope is incomplete")
        if not _DIGEST_RE.fullmatch(expected_holdings_digest):
            raise ValueError("historical evidence holdings digest is invalid")
        if not _DIGEST_RE.fullmatch(expected_cash_flow_fingerprint):
            raise ValueError("historical evidence cash-flow fingerprint is invalid")
        datetime.fromisoformat(valuation_as_of.replace("Z", "+00:00"))
        positive_finite_decimal(usdcny, "USDCNY")
        positive_finite_decimal(hkdcny, "HKDCNY")
        if write and (
            not confirm
            or expected_digest is None
            or not _DIGEST_RE.fullmatch(expected_digest)
        ):
            raise ValueError(
                "historical evidence write requires confirm and expected digest"
            )
        preflight = self.holdings_preflight or HoldingsNavPreflightService(
            storage=self.storage
        )
        preflight_result = preflight.prepare_account(
            account=account,
            dry_run=True,
            confirm=False,
            trigger={
                "mode": "historical_nav_valuation_evidence",
                "account": account,
                "nav_date": target_date.isoformat(),
                "source_run_id": source_run_id,
            },
        )
        if not preflight_result.get("success"):
            raise ValueError(
                preflight_result.get("error") or "holdings preflight failed"
            )
        validated = preflight_result["validated_snapshot"]
        preparation = "historical_recovery"
        source_receipt_key = None
        if validated.normalized_holdings_digest != expected_holdings_digest:
            validated, source_receipt_key = self._load_receipt_holdings(
                account=account,
                nav_date=target_date,
                source_run_id=source_run_id,
                expected_holdings_digest=expected_holdings_digest,
            )
            preparation = "historical_receipt_recovery"

        preparation_run_id = (
            f"nav-valuation-evidence-prepare:{account}:{target_date.isoformat()}"
        )
        dataset = self.portfolio.build_cash_flow_dataset(
            account=account,
            nav_date=target_date,
            run_id=preparation_run_id,
        )
        dataset.assert_official_scope(
            account=account,
            nav_date=target_date,
            run_id=preparation_run_id,
            start_year=config.get_start_year(),
        )
        if dataset.financial_fingerprint != expected_cash_flow_fingerprint:
            raise ValueError("historical evidence cash-flow fingerprint mismatch")

        holdings = validated.to_valuation_holdings()
        price_snapshot = self.price_snapshot_builder(
            holdings,
            nav_date=target_date,
            valuation_as_of=valuation_as_of,
            usdcny=usdcny,
            hkdcny=hkdcny,
        )
        normalized = self.portfolio.calculate_normalized_valuation(
            account=account,
            fetch_prices=False,
            holdings=holdings,
            price_snapshot=price_snapshot,
            price_warnings=[],
            holdings_provenance=validated.provenance(),
        )
        prepared = self.evidence_store.prepare(
            account=account,
            nav_date=target_date,
            source_run_id=source_run_id,
            snapshot_time=valuation_as_of,
            holdings_digest=expected_holdings_digest,
            cash_flow_financial_fingerprint=expected_cash_flow_fingerprint,
            source_effect_store_revision=source_effect_store_revision,
            normalized_valuation=normalized,
            preparation=preparation,
            source_receipt_key=source_receipt_key,
        )
        result = {
            "success": True,
            "status": "preview",
            "write": False,
            "account": account,
            "nav_date": target_date.isoformat(),
            "current_effect_store_revision": dataset.effect_store_revision,
            "holdings_source": (
                "nav_receipt_outbox" if source_receipt_key else "current_preflight"
            ),
            "source_receipt_key": source_receipt_key,
            **prepared,
        }
        if not write:
            return result
        if prepared["artifact_digest"] != expected_digest:
            raise ValueError("historical evidence expected digest mismatch")
        saved = self.evidence_store.save(prepared)
        return {**result, **saved, "status": "written", "write": True}
