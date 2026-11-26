"""Complete, read-only external cash-flow facts for OM reservation consumers."""
from datetime import datetime, timezone
from hashlib import sha256
import json

from src.domain.cash_flow_contracts import ManualCashFlowFacts


def read_cash_flows(storage, account):
    rows = storage.get_raw_cash_flows(require_complete=True)
    records, errors = [], []
    for raw in rows:
        owner = str(raw.raw_fields.get("account") or "").strip()
        if not owner:
            errors.append({"issues": [{"code": "cash_flow_account_missing"}]})
            continue
        if owner != account:
            continue
        facts, issues = ManualCashFlowFacts.validate(raw)
        if issues:
            errors.append({"record_id": raw.record_id, "account": raw.raw_fields.get("account"),
                           "issues": [issue.as_dict() for issue in issues]})
            continue
        direction = raw.raw_fields.get("flow_type")
        if direction not in (None, "", facts.expected_flow_type):
            errors.append({"record_id": raw.record_id, "account": facts.account,
                           "issues": [{"code": "cash_flow_direction_conflict"}]})
            continue
        financial = {"account": facts.account, "broker": facts.broker,
                     "flow_date": facts.flow_date.isoformat(), "amount": str(facts.amount),
                     "currency": facts.currency}
        version = sha256(json.dumps(financial, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        records.append({**financial, "record_id": raw.record_id, "financial_version": version,
                        "flow_type": facts.expected_flow_type, "remark": facts.remark})
    now = datetime.now(timezone.utc).isoformat()
    return {"success": True, "schema_version": "portfolio.cash_flows.v1", "account": account,
            "source_identity": "pm.cash_flow", "complete": True, "records": records,
            "row_errors": errors, "retrieved_at_utc": now,
            "freshness": {"status": "fresh", "trust_status": "trusted" if not errors else "partial",
                          "observed_at_utc": now, "dataset_ids": ["pm.cash_flow"],
                          "reason_codes": ["cash_flow_row_invalid"] if errors else []}}
