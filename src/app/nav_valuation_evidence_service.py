"""Immutable normalized-valuation evidence for guarded NAV replay."""
from __future__ import annotations

from datetime import date, datetime
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping, Optional
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
        valuation_payload = normalized_valuation.canonical_payload()
        valuation_holdings_digest = str(
            (valuation_payload.get("holdings_provenance") or {}).get(
                "normalized_holdings_digest"
            )
            or ""
        )
        if valuation_holdings_digest != holdings_digest:
            raise ValueError("NAV valuation evidence holdings digest mismatch")
        datetime.fromisoformat(snapshot_time.replace("Z", "+00:00"))

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
            "valuation_digest": normalized_valuation.digest,
            "valuation": valuation_payload,
            "preparation": str(preparation or "").strip(),
        }
        if not body["preparation"]:
            raise ValueError("NAV valuation evidence preparation is required")
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
        return normalized
