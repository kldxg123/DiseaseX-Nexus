"""In-process authorization boundary for registered local forecasting adapters.

Roles must come from a trusted caller, not a client-supplied HTTP field. This
component is not a replacement for authentication or a persistent job queue.
"""
from __future__ import annotations
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from threading import Lock
from typing import Any, Callable

FIELDS=frozenset({'dataset_sha256','task_id','graph_sha256','model','parameters','origin','output_scope','risk_review'})
MODELS=frozenset({'last_value','mean4','seasonal52','log_ar2'})

class ContractError(ValueError):
    pass

def aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:raise ContractError('TIMEZONE_REQUIRED')

def fingerprint(payload: dict[str, Any]) -> str:
    if set(payload)!=FIELDS:raise ContractError('INVALID_FIELDS')
    for name in ('dataset_sha256','graph_sha256'):
        value=payload[name]
        if not isinstance(value,str) or len(value)!=64 or any(c not in '0123456789abcdef' for c in value):raise ContractError('INVALID_HASH')
    if payload['model'] not in MODELS:raise ContractError('UNREGISTERED_MODEL')
    if not isinstance(payload['parameters'],dict) or not isinstance(payload['origin'],int) or isinstance(payload['origin'],bool) or payload['origin']<0:raise ContractError('INVALID_PARAMETERS')
    if not isinstance(payload['task_id'],str) or not payload['task_id'] or payload['output_scope']!='analyst_review' or payload['risk_review'] is not True:raise ContractError('REVIEW_SCOPE_REQUIRED')
    try:canonical=json.dumps(payload,sort_keys=True,separators=(',',':'),allow_nan=False)
    except (ValueError,TypeError):raise ContractError('NON_CANONICAL_PAYLOAD') from None
    return hashlib.sha256(canonical.encode()).hexdigest()

@dataclass
class _Record:
    digest: str
    expires_at: datetime
    state: str='PENDING'
    key: str | None=None
    result: Any=None

class ContractGate:
    def __init__(self) -> None:
        self._records: dict[str,_Record]={}
        self._lock=Lock()
        self.audit: list[dict[str,str]]=[]

    def _note(self,identifier: str,event: str,now: datetime) -> None:
        self.audit.append({'contract_id':identifier,'event':event,'at':now.isoformat()})

    def create(self,payload: dict[str,Any],expires_at: datetime,now: datetime) -> str:
        aware(now);aware(expires_at)
        if expires_at<=now:raise ContractError('INVALID_EXPIRY')
        digest=fingerprint(payload)
        with self._lock:
            identifier=f'local-contract-{len(self._records)+1}'
            self._records[identifier]=_Record(digest,expires_at)
            self._note(identifier,'CREATED',now)
            return identifier

    def approve(self,identifier: str,role: str,now: datetime) -> None:
        aware(now)
        with self._lock:
            record=self._lookup(identifier)
            if role!='reviewer':raise ContractError('APPROVER_ROLE_REQUIRED')
            if record.state!='PENDING':raise ContractError('NOT_PENDING')
            if now>=record.expires_at:
                record.state='EXPIRED';raise ContractError('EXPIRED')
            record.state='APPROVED';self._note(identifier,'APPROVED',now)

    def revoke(self,identifier: str,role: str,now: datetime) -> None:
        aware(now)
        with self._lock:
            record=self._lookup(identifier)
            if role!='reviewer':raise ContractError('APPROVER_ROLE_REQUIRED')
            if record.state not in ('PENDING','APPROVED'):raise ContractError('NOT_REVOCABLE')
            record.state='REVOKED';self._note(identifier,'REVOKED',now)

    def _lookup(self,identifier: str) -> _Record:
        if identifier not in self._records:raise ContractError('UNKNOWN_CONTRACT')
        return self._records[identifier]

    def state(self,identifier: str) -> str:
        with self._lock:return self._lookup(identifier).state

    def execute(self,identifier: str,payload: dict[str,Any],key: str,role: str,now: datetime,callback: Callable[[],Any]) -> Any:
        aware(now)
        if role!='executor':raise ContractError('EXECUTOR_ROLE_REQUIRED')
        if not isinstance(key,str) or not key:raise ContractError('IDEMPOTENCY_REQUIRED')
        digest=fingerprint(payload)
        with self._lock:
            record=self._lookup(identifier)
            if digest!=record.digest:raise ContractError('IDENTITY_CHANGED')
            if now>=record.expires_at:
                if record.state!='COMPLETED':record.state='EXPIRED'
                raise ContractError('EXPIRED')
            if record.state=='COMPLETED':
                if key!=record.key:raise ContractError('REPLAY_KEY_CHANGED')
                self._note(identifier,'REPLAY_RETURNED',now)
                return deepcopy(record.result)
            if record.state!='APPROVED':raise ContractError('NOT_APPROVED')
            record.state='RUNNING';record.key=key;self._note(identifier,'RUNNING',now)
            try:
                value=deepcopy(callback())
                if value is None:raise ContractError('EMPTY_RESULT')
            except Exception:
                record.state='FAILED';record.result=None;self._note(identifier,'FAILED',now)
                raise ContractError('EXECUTION_FAILED') from None
            record.result=value;record.state='COMPLETED';self._note(identifier,'COMPLETED',now)
            return deepcopy(value)
