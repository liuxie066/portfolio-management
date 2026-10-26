"""Default-deny Agent authorization and one-record name-operation orchestration."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import re
import time
from uuid import uuid4

from .holdings_reconciliation_service import HoldingsReconciliationService
from .holdings_workflow_service import HoldingsWorkflowService
from .operation_state_store import OperationStateStore

RECORD_PATTERN = r'^[A-Za-z0-9_-]{1,128}$'


class AgentNameError(Exception):
    def __init__(self, status: int, code: str):
        self.status = status
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class AgentNameGrant:
    principal_id: str
    record_id: str
    account: str
    broker: str
    digest: str


def authenticate(policy, authorization: str, record_id: str) -> AgentNameGrant:
    """Only trusted configured exact grants confer authority; nonce IDs do not."""
    if not isinstance(policy, dict) or policy.get('enabled') is not True:
        raise AgentNameError(503, 'AGENT_OPERATIONS_DISABLED')
    principals = policy.get('principals')
    if not isinstance(principals, list) or not principals:
        raise AgentNameError(503, 'AGENT_AUTH_UNAVAILABLE')
    seen_ids, seen_tokens = set(), set()
    validated = []
    for principal in principals:
        if not isinstance(principal, dict) or set(principal) != {'id', 'token_sha256', 'grants'}:
            raise AgentNameError(503, 'AGENT_AUTH_UNAVAILABLE')
        pid, token, grants = principal['id'], principal['token_sha256'], principal['grants']
        if (not isinstance(pid, str) or not re.fullmatch(RECORD_PATTERN, pid)
                or pid in seen_ids or not isinstance(token, str)
                or not re.fullmatch(r'[0-9a-f]{64}', token) or token in seen_tokens
                or not isinstance(grants, list) or not grants):
            raise AgentNameError(503, 'AGENT_AUTH_UNAVAILABLE')
        seen_ids.add(pid)
        seen_tokens.add(token)
        records = set()
        for grant in grants:
            if (not isinstance(grant, dict) or set(grant) != {'record_id', 'account', 'broker'}
                    or any(not isinstance(v, str) or not v.strip() or v != v.strip() or '*' in v
                           for v in grant.values())
                    or not re.fullmatch(RECORD_PATTERN, grant['record_id'])
                    or grant['record_id'] in records):
                raise AgentNameError(503, 'AGENT_AUTH_UNAVAILABLE')
            records.add(grant['record_id'])
        validated.append(principal)
    if not authorization.startswith('Bearer ') or not authorization[7:] or len(authorization) > 4096:
        raise AgentNameError(401, 'AGENT_AUTH_FAILED')
    supplied = hashlib.sha256(authorization[7:].encode()).hexdigest()
    matched = None
    for principal in validated:
        if hmac.compare_digest(principal['token_sha256'], supplied):
            matched = principal
    if matched is None:
        raise AgentNameError(401, 'AGENT_AUTH_FAILED')
    grant = next((g for g in matched['grants'] if g['record_id'] == record_id), None)
    if grant is None:
        raise AgentNameError(403, 'AGENT_SCOPE_DENIED')
    # Bind to the active principal policy, including access-credential rotation.
    digest = hashlib.sha256(json.dumps(matched, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return AgentNameGrant(matched['id'], record_id, grant['account'], grant['broker'], digest)


def safe_validation(validation) -> dict:
    fields = validation.raw.raw_fields
    return {
        'asset_name': fields.get('asset_name'), 'currency': fields.get('currency'),
        'blocks_official_nav': validation.blocks_official_nav,
        'blockers': [{'field': o.field, 'status': o.status, 'reason': o.reason_code}
                     for o in validation.outcomes if o.blocks_official_nav and o.status != 'valid'],
    }


class AgentHoldingNameService:
    def __init__(self, *, storage, store=None, reconciliation=None, clock=time.time):
        self.storage = storage
        self._store = store
        self.reconciliation = reconciliation or HoldingsReconciliationService(storage=storage)
        self.clock = clock

    @property
    def store(self):
        if self._store is None:
            self._store = OperationStateStore()
        return self._store

    def _row(self, grant):
        # Authorize identity before any provider lookup, including Futu observation.
        rows = self.storage.get_raw_holdings(record_id=grant.record_id)
        if len(rows) != 1 or rows[0].record_id != grant.record_id:
            raise AgentNameError(404, 'RECORD_UNAVAILABLE')
        fields = rows[0].raw_fields
        if fields.get('account') != grant.account or fields.get('broker') != grant.broker:
            raise AgentNameError(403, 'AGENT_SCOPE_DENIED')
        return rows[0]

    def preview(self, grant: AgentNameGrant) -> dict:
        raw = self._row(grant)
        # HK/non-Futu is a mandatory scope boundary, not a fallback to other providers.
        eligible = self.reconciliation.is_hk_stock_name_candidate(raw.raw_fields)
        from .holdings_validation import holding_broker_scope
        if (raw.raw_fields.get('asset_type') != 'hk_stock'
                or holding_broker_scope(raw.raw_fields.get('broker')) != 'non_futu'):
            raise AgentNameError(403, 'NAME_SCOPE_DENIED')
        evaluation = self.reconciliation.evaluate_records(
            [raw], record_id=grant.record_id, include_hk_stock_name=eligible,
        )
        validation = evaluation.report.records[0]
        response = {'state': 'noop' if raw.raw_fields.get('asset_name') else 'not_executable',
                    'record_id': grant.record_id, **safe_validation(validation)}
        outcome = next(o for o in validation.outcomes if o.field == 'asset_name')
        if not eligible or outcome.status != 'missing_completable' or outcome.authority != 'hk_stock_quote':
            return response
        evidence = {k: (outcome.evidence or {}).get(k) for k in ('source', 'code', 'market', 'source_as_of')}
        preview_id = uuid4().hex
        operation = {
            'preview_id': preview_id, 'principal_id': grant.principal_id,
            'record_id': grant.record_id, 'grant_digest': grant.digest,
            'identity': {k: raw.raw_fields.get(k) for k in ('account', 'broker', 'asset_id')},
            'record_digest': validation.record_digest, 'proposed_name': outcome.proposed,
            'evidence': evidence, 'created_at': self.clock(), 'expires_at': self.clock() + 300,
        }
        self.store.create_agent_name_operation(operation)
        return {**response, 'state': 'previewed', 'preview_id': preview_id,
                'expires_at': operation['expires_at'], 'proposed_name': outcome.proposed,
                'evidence': evidence}

    def _operation(self, grant, preview_id):
        if not isinstance(preview_id, str) or not re.fullmatch(r'[0-9a-f]{32}', preview_id):
            raise AgentNameError(404, 'OPERATION_UNAVAILABLE')
        operation = (self._store.get_agent_name_operation(preview_id) if self._store is not None
                     else OperationStateStore.read_agent_name_operation(
                         OperationStateStore.resolve_db_path_read_only(), preview_id))
        if (not operation or operation['principal_id'] != grant.principal_id
                or operation['record_id'] != grant.record_id or operation['grant_digest'] != grant.digest):
            raise AgentNameError(404, 'OPERATION_UNAVAILABLE')
        return operation

    @staticmethod
    def _result(operation):
        return operation['result'] or {'preview_id': operation['preview_id'],
                                      'record_id': operation['record_id'],
                                      'state': operation['state'],
                                      'attention_required': operation['state'] == 'executing',
                                      'expires_at': operation['expires_at']}

    def status(self, grant: AgentNameGrant, preview_id: str) -> dict:
        return self._result(self._operation(grant, preview_id))

    def confirm(self, grant: AgentNameGrant, preview_id: str) -> dict:
        operation = self._operation(grant, preview_id)
        if operation['state'] != 'previewed':
            return self._result(operation)
        if operation['expires_at'] <= self.clock():
            raise AgentNameError(409, 'PREVIEW_EXPIRED')
        if not self.store.claim_agent_name_operation(
            preview_id, principal_id=grant.principal_id, record_id=grant.record_id,
            grant_digest=grant.digest, now=self.clock(),
        ):
            operation = self._operation(grant, preview_id)
            if operation['state'] == 'previewed':
                raise AgentNameError(409, 'PREVIEW_EXPIRED')
            return self._result(operation)
        result = {'preview_id': preview_id, 'record_id': grant.record_id}
        try:
            self._row(grant)
            workflow = HoldingsWorkflowService(storage=self.storage, store=self.store,
                                               reconciliation=self.reconciliation)
            outcome = workflow.complete_name_from_preview(
                record_id=grant.record_id, identity=operation['identity'],
                expected_digest=operation['record_digest'], proposed_name=operation['proposed_name'],
                evidence_identity={k: operation['evidence'][k] for k in ('source', 'code', 'market')},
                principal_id=grant.principal_id, preview_id=preview_id,
            )
            result.update(state=outcome['state'], reason=outcome['reason'])
            if outcome.get('validation') is not None:
                result.update(safe_validation(outcome['validation']))
        except AgentNameError as exc:
            result.update(state='rejected', reason=exc.code)
        except Exception:
            # A crash/failure after claim may have reached Feishu. Never retry blindly.
            result.update(state='unknown', reason='OPERATION_OUTCOME_UNCERTAIN')
        self.store.finish_agent_name_operation(preview_id, state=result['state'], result=result)
        return result
