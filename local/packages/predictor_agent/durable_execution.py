"""File-backed experimental execution gate, not a production identity provider.

The caller must authenticate a principal before invoking this interface.
Linux advisory locks coordinate cooperating processes. Running work is never
silently retried after a crash; recovery requires an explicit review.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
from typing import Any, Callable, Iterator
import uuid

REGISTERED = frozenset(('last_value','mean4','seasonal52','log_ar2','log_ar4','mean8','mean13','drift4','drift13'))
FIELDS = frozenset(('dataset_sha256','graph_sha256','task_id','model','parameters','origin','output_scope','risk_review'))


class GateError(ValueError):
    """Stable fail-closed error code only; never return credentials or paths."""


@dataclass(frozen=True)
class Principal:
    subject: str
    role: str


class OpaqueTokenRegistry:
    """Test/development identity adapter; no enrollment or production SSO."""

    def __init__(self, entries: dict[str, Principal]) -> None:
        self._entries = {hashlib.sha256(k.encode()).digest(): v for k,v in entries.items()}

    def authenticate(self, header: str | None) -> Principal:
        if not isinstance(header,str) or not header.startswith('Bearer '):
            raise GateError('AUTH_REQUIRED')
        digest=hashlib.sha256(header[7:].encode()).digest()
        # Constant-time digest comparisons; request JSON never supplies roles.
        for expected,principal in self._entries.items():
            if secrets.compare_digest(digest,expected):
                return principal
        raise GateError('AUTH_REQUIRED')


def _time(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise GateError('TIMEZONE_REQUIRED')
    return value.astimezone(timezone.utc)


def payload_digest(payload: dict[str,Any]) -> str:
    if not isinstance(payload,dict) or set(payload)!=FIELDS:
        raise GateError('INVALID_FIELDS')
    for name in ('dataset_sha256','graph_sha256'):
        value=payload[name]
        if not isinstance(value,str) or len(value)!=64 or any(x not in '0123456789abcdef' for x in value):
            raise GateError('INVALID_HASH')
    if payload['model'] not in REGISTERED:
        raise GateError('UNREGISTERED_MODEL')
    if not isinstance(payload['task_id'],str) or not payload['task_id']:
        raise GateError('INVALID_TASK')
    if not isinstance(payload['origin'],int) or isinstance(payload['origin'],bool) or payload['origin']<0:
        raise GateError('INVALID_ORIGIN')
    if not isinstance(payload['parameters'],dict):
        raise GateError('INVALID_PARAMETERS')
    if payload['output_scope']!='analyst_review' or payload['risk_review'] is not True:
        raise GateError('REVIEW_SCOPE_REQUIRED')
    try:
        raw=json.dumps(payload,sort_keys=True,separators=(',',':'),allow_nan=False)
    except (TypeError,ValueError):
        raise GateError('NON_CANONICAL_PAYLOAD') from None
    return hashlib.sha256(raw.encode()).hexdigest()


class DurableGate:
    def __init__(self, directory: Path) -> None:
        self.directory=Path(directory)
        self.directory.mkdir(mode=0o700,parents=True,exist_ok=True)
        self.state_file=self.directory/'ledger.json'
        self.lock_file=self.directory/'ledger.lock'

    @contextmanager
    def _locked(self) -> Iterator[dict[str,Any]]:
        import fcntl
        with self.lock_file.open('a+b') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            try:
                state=json.loads(self.state_file.read_text()) if self.state_file.exists() else {'version':1,'records':{},'audit':[]}
                if state.get('version')!=1 or not isinstance(state.get('records'),dict):
                    raise GateError('INVALID_LEDGER')
                yield state
                self._save(state)
            finally:
                fcntl.flock(lock,fcntl.LOCK_UN)

    def _save(self, state: dict[str,Any]) -> None:
        temporary=self.directory/('ledger-'+uuid.uuid4().hex+'.tmp')
        try:
            descriptor=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(descriptor,'w') as stream:
                json.dump(state,stream,sort_keys=True,allow_nan=False)
                stream.flush();os.fsync(stream.fileno())
            os.replace(temporary,self.state_file)
            descriptor=os.open(self.directory,os.O_RDONLY)
            try:os.fsync(descriptor)
            finally:os.close(descriptor)
        finally:
            if temporary.exists():temporary.unlink()

    @staticmethod
    def _role(principal: Principal, required: str) -> None:
        if not isinstance(principal,Principal) or principal.role!=required:
            raise GateError('ROLE_REQUIRED')

    @staticmethod
    def _record(state: dict[str,Any], identifier: str) -> dict[str,Any]:
        if identifier not in state['records']:
            raise GateError('UNKNOWN_CONTRACT')
        return state['records'][identifier]

    @staticmethod
    def _note(state: dict[str,Any], identifier: str, event: str, actor: Principal, at: datetime) -> None:
        state['audit'].append({'contract':identifier,'event':event,'actor':actor.subject,'role':actor.role,'at':at.isoformat()})

    @staticmethod
    def _expiry(state: dict[str,Any], record: dict[str,Any], identifier: str, actor: Principal, at: datetime) -> None:
        if at>=datetime.fromisoformat(record['expires_at']):
            if record['state'] not in ('COMPLETED','FAILED','REVOKED','EXPIRED'):
                record['state']='EXPIRED'
                DurableGate._note(state,identifier,'EXPIRED',actor,at)
            raise GateError('EXPIRED')

    def create(self, payload: dict[str,Any], actor: Principal, expires_at: datetime, now: datetime) -> str:
        self._role(actor,'analyst');now=_time(now);expires_at=_time(expires_at)
        if expires_at<=now:raise GateError('INVALID_EXPIRY')
        digest=payload_digest(payload);identifier=uuid.uuid4().hex
        with self._locked() as state:
            state['records'][identifier]={'digest':digest,'expires_at':expires_at.isoformat(),'state':'PENDING','creator':actor.subject,'key':None,'result':None}
            self._note(state,identifier,'CREATED',actor,now)
        return identifier

    def approve(self, identifier: str, actor: Principal, now: datetime) -> None:
        self._role(actor,'reviewer');now=_time(now)
        with self._locked() as state:
            record=self._record(state,identifier)
            # Expiry transitions must be persisted even when the request fails.
            try:self._expiry(state,record,identifier,actor,now)
            except GateError:self._save(state);raise
            if actor.subject==record['creator']:raise GateError('SELF_APPROVAL_FORBIDDEN')
            if record['state']!='PENDING':raise GateError('NOT_PENDING')
            record['state']='APPROVED';record['approver']=actor.subject
            self._note(state,identifier,'APPROVED',actor,now)

    def revoke(self, identifier: str, actor: Principal, now: datetime) -> None:
        self._role(actor,'reviewer');now=_time(now)
        with self._locked() as state:
            record=self._record(state,identifier)
            if record['state'] not in ('PENDING','APPROVED'):raise GateError('NOT_REVOCABLE')
            record['state']='REVOKED';self._note(state,identifier,'REVOKED',actor,now)

    def execute(self, identifier: str, payload: dict[str,Any], key: str, actor: Principal, now: datetime, callback: Callable[[],Any]) -> Any:
        self._role(actor,'executor');now=_time(now);digest=payload_digest(payload)
        if not isinstance(key,str) or not key:raise GateError('IDEMPOTENCY_REQUIRED')
        with self._locked() as state:
            record=self._record(state,identifier)
            if record['digest']!=digest:raise GateError('IDENTITY_CHANGED')
            try:self._expiry(state,record,identifier,actor,now)
            except GateError:self._save(state);raise
            if record['state']=='COMPLETED':
                if record['key']!=key:raise GateError('REPLAY_KEY_CHANGED')
                self._note(state,identifier,'REPLAY_RETURNED',actor,now)
                return deepcopy(record['result'])
            if record['state']!='APPROVED':raise GateError('NOT_APPROVED')
            record['state']='RUNNING';record['key']=key
            self._note(state,identifier,'RUNNING',actor,now)
            # Persist before executing. A crash leaves RUNNING and is fail-closed.
            self._save(state)
            try:
                value=callback()
                if value is None:raise GateError('EMPTY_RESULT')
                value=json.loads(json.dumps(value,allow_nan=False))
            except Exception:
                record['state']='FAILED';record['result']=None
                self._note(state,identifier,'FAILED',actor,now);self._save(state)
                raise GateError('EXECUTION_FAILED') from None
            record['state']='COMPLETED';record['result']=value
            self._note(state,identifier,'COMPLETED',actor,now)
            return deepcopy(value)

    def snapshot(self) -> dict[str,Any]:
        with self._locked() as state:return deepcopy(state)
