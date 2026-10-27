from __future__ import annotations

import ipaddress
import json
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path
from contextlib import contextmanager
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter()
DB_PATH: Path | None = None

def _database_path() -> Path:
    return DB_PATH or Path(os.getenv('EZEAGENT_DATABASE_PATH', './ezeagent.db')).resolve()

@contextmanager
def _db():
    path = _database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys=ON')
    connection.execute('''CREATE TABLE IF NOT EXISTS security_assessments(
        id TEXT PRIMARY KEY, name TEXT NOT NULL, client TEXT NOT NULL, owner TEXT NOT NULL,
        authorization_reference TEXT NOT NULL, authorization_confirmed INTEGER NOT NULL,
        authorized_by TEXT NOT NULL, authorized_at REAL NOT NULL, environment TEXT NOT NULL,
        starts_at REAL NOT NULL, ends_at REAL NOT NULL, retention_until REAL NOT NULL, objectives TEXT NOT NULL,
        rules_of_engagement TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL)''')
    connection.execute('''CREATE TABLE IF NOT EXISTS security_scope(
        id TEXT PRIMARY KEY, assessment_id TEXT NOT NULL REFERENCES security_assessments(id) ON DELETE CASCADE,
        asset_type TEXT NOT NULL, value TEXT NOT NULL, include_subdomains INTEGER NOT NULL,
        confirmed_by TEXT NOT NULL, created REAL NOT NULL, UNIQUE(assessment_id, asset_type, value))''')
    connection.execute('''CREATE TABLE IF NOT EXISTS security_findings(
        id TEXT PRIMARY KEY, assessment_id TEXT NOT NULL REFERENCES security_assessments(id) ON DELETE CASCADE,
        title TEXT NOT NULL, severity TEXT NOT NULL, asset TEXT NOT NULL, evidence TEXT NOT NULL,
        redacted_count INTEGER NOT NULL, impact TEXT NOT NULL, remediation TEXT NOT NULL,
        confidence TEXT NOT NULL, status TEXT NOT NULL, identifier TEXT NOT NULL,
        created_by TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL)''')
    connection.execute('''CREATE TABLE IF NOT EXISTS security_audit(
        id TEXT PRIMARY KEY, assessment_id TEXT NOT NULL, actor TEXT NOT NULL,
        action TEXT NOT NULL, detail TEXT NOT NULL, created REAL NOT NULL)''')
    connection.execute('''CREATE TABLE IF NOT EXISTS security_deletion_log(
        id TEXT PRIMARY KEY, assessment_hash TEXT NOT NULL, actor TEXT NOT NULL, deleted REAL NOT NULL)''')
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _require_local_ui(request: Request):
    client = request.client.host if request.client else ''
    try:
        local = ipaddress.ip_address(client).is_loopback
    except ValueError:
        local = client == 'testclient'
    allowed_origins = {item.strip() for item in os.getenv('EZEAGENT_CORS_ORIGINS', 'http://localhost:5173').split(',') if item.strip()}
    if not local or request.headers.get('origin') not in allowed_origins:
        raise HTTPException(403, 'Security assessment workspaces are available only in the local EzeAgent dashboard')


def _audit(connection, assessment_id: str, actor: str, action: str, detail: dict):
    connection.execute('INSERT INTO security_audit VALUES(?,?,?,?,?,?)',
                       (str(uuid.uuid4()), assessment_id, actor[:120], action, json.dumps(detail)[:2000], time.time()))


def _assessment(connection, assessment_id: str):
    item = connection.execute('SELECT * FROM security_assessments WHERE id=?', (assessment_id,)).fetchone()
    if not item:
        raise HTTPException(404, 'Assessment not found')
    return item


def _domain(value: str) -> str:
    target = value.strip().rstrip('.').lower()
    if not target or '://' in target or '/' in target or ':' in target or '*' in target:
        raise HTTPException(422, 'Enter a hostname only; URLs, wildcards, and ports are not accepted')
    try:
        target = target.encode('idna').decode('ascii')
    except UnicodeError:
        raise HTTPException(422, 'Invalid hostname')
    if len(target) > 253 or not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in target.split('.')):
        raise HTTPException(422, 'Invalid hostname')
    return target


def _normalize_asset(asset_type: str, value: str) -> str:
    raw = value.strip()
    try:
        if asset_type == 'domain':
            return _domain(raw)
        if asset_type == 'ip':
            return str(ipaddress.ip_address(raw))
        if asset_type == 'cidr':
            return str(ipaddress.ip_network(raw, strict=False))
    except ValueError:
        raise HTTPException(422, 'Invalid IP address or network range')
    raise HTTPException(422, 'Asset type must be domain, IP address, or CIDR range')


def _target_in_scope(connection, assessment_id: str, raw_target: str) -> bool:
    target = raw_target.strip()
    try:
        ip_target = ipaddress.ip_address(target)
    except ValueError:
        domain_target = _domain(target)
        ip_target = None
    for item in connection.execute('SELECT asset_type,value,include_subdomains FROM security_scope WHERE assessment_id=?', (assessment_id,)):
        if item['asset_type'] == 'domain' and ip_target is None:
            if domain_target == item['value'] or (item['include_subdomains'] and domain_target.endswith('.' + item['value'])):
                return True
        elif ip_target is not None and item['asset_type'] == 'ip' and str(ip_target) == item['value']:
            return True
        elif ip_target is not None and item['asset_type'] == 'cidr' and ip_target in ipaddress.ip_network(item['value']):
            return True
    return False


class AssessmentCreate(BaseModel):
    name: str = Field(min_length=3, max_length=160)
    client: str = Field(min_length=2, max_length=160)
    owner: str = Field(min_length=2, max_length=120)
    authorization_reference: str = Field(min_length=3, max_length=500)
    written_authorization_confirmed: bool
    authorized_by: str = Field(min_length=2, max_length=120)
    environment: Literal['lab', 'staging', 'production']
    starts_at: float = Field(gt=0)
    ends_at: float = Field(gt=0)
    retention_until: float = Field(gt=0)
    objectives: str = Field(min_length=3, max_length=4000)
    rules_of_engagement: str = Field(min_length=3, max_length=8000)


class ScopeCreate(BaseModel):
    asset_type: Literal['domain', 'ip', 'cidr']
    value: str = Field(min_length=1, max_length=253)
    include_subdomains: bool = False
    confirmed_in_written_scope: bool
    confirmed_by: str = Field(min_length=2, max_length=120)


class ScopeCheck(BaseModel):
    target: str = Field(min_length=1, max_length=253)


class FindingCreate(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    severity: Literal['info', 'low', 'medium', 'high', 'critical']
    asset: str = Field(min_length=1, max_length=253)
    evidence: str = Field(max_length=12000)
    impact: str = Field(min_length=3, max_length=3000)
    remediation: str = Field(min_length=3, max_length=3000)
    confidence: Literal['low', 'medium', 'high']
    status: Literal['lead', 'confirmed', 'false_positive', 'accepted_risk', 'needs_retest', 'resolved'] = 'lead'
    identifier: str = Field(default='', max_length=80)
    created_by: str = Field(min_length=2, max_length=120)


class FindingStatus(BaseModel):
    status: Literal['lead', 'confirmed', 'false_positive', 'accepted_risk', 'needs_retest', 'resolved']
    actor: str = Field(min_length=2, max_length=120)


_SECRET_PATTERNS = [
    re.compile(r'(?i)(authorization\s*:\s*bearer\s+)[A-Za-z0-9._~+/-]{8,}'),
    re.compile(r'(?i)\b(api[_-]?key|access[_-]?token|password|client_secret)\b(\s*[:=]\s*)["\']?[^\s,"\']{8,}'),
    re.compile(r'\bAKIA[0-9A-Z]{16}\b'),
    re.compile(r'\bgh[pousr]_[A-Za-z0-9_]{20,}\b'),
    re.compile(r'\bsk-or-v1-[A-Za-z0-9]{20,}\b'),
]


def _redact_evidence(text: str) -> tuple[str, int]:
    count = 0
    redacted = text
    for pattern in _SECRET_PATTERNS:
        redacted, hits = pattern.subn(lambda match: (match.group(1) + '[REDACTED]' if match.lastindex and match.lastindex >= 2 else '[REDACTED]'), redacted)
        count += hits
    redacted, hits = re.subn(r'-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----', '[REDACTED PRIVATE KEY]', redacted)
    return redacted, count + hits


@router.get('/security/assessments')
def list_assessments(request: Request):
    _require_local_ui(request)
    with _db() as connection:
        rows = connection.execute('SELECT id,name,client,owner,environment,starts_at,ends_at,retention_until,status,created FROM security_assessments ORDER BY created DESC LIMIT 100').fetchall()
    return {'assessments': [dict(row) for row in rows]}


@router.post('/security/assessments')
def create_assessment(req: AssessmentCreate, request: Request):
    _require_local_ui(request)
    if not req.written_authorization_confirmed:
        raise HTTPException(400, 'Confirm that written authorization exists before creating this assessment')
    if req.ends_at <= req.starts_at:
        raise HTTPException(400, 'Assessment end must be later than its start')
    if req.retention_until < req.ends_at:
        raise HTTPException(400, 'Data deletion date must be on or after the assessment end')
    now = time.time(); assessment_id = str(uuid.uuid4())
    with _db() as connection:
        connection.execute('INSERT INTO security_assessments(id,name,client,owner,authorization_reference,authorization_confirmed,authorized_by,authorized_at,environment,starts_at,ends_at,retention_until,objectives,rules_of_engagement,status,created) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                           (assessment_id, req.name.strip(), req.client.strip(), req.owner.strip(), req.authorization_reference.strip(),
                            1, req.authorized_by.strip(), now, req.environment, req.starts_at, req.ends_at, req.retention_until,
                            req.objectives.strip(), req.rules_of_engagement.strip(), 'open', now))
        _audit(connection, assessment_id, req.authorized_by, 'assessment_created', {'environment': req.environment, 'authorization_recorded': True})
    return {'id': assessment_id, 'status': 'open', 'authorization_confirmed': True}


@router.get('/security/assessments/{assessment_id}')
def get_assessment(assessment_id: str, request: Request):
    _require_local_ui(request)
    with _db() as connection:
        assessment = dict(_assessment(connection, assessment_id))
        scope = connection.execute('SELECT id,asset_type,value,include_subdomains,confirmed_by,created FROM security_scope WHERE assessment_id=? ORDER BY created', (assessment_id,)).fetchall()
        findings = connection.execute('SELECT * FROM security_findings WHERE assessment_id=? ORDER BY created DESC', (assessment_id,)).fetchall()
    assessment['scope'] = [dict(item) for item in scope]
    assessment['findings'] = [dict(item) for item in findings]
    return assessment


@router.post('/security/assessments/{assessment_id}/scope')
def add_scope(assessment_id: str, req: ScopeCreate, request: Request):
    _require_local_ui(request)
    if not req.confirmed_in_written_scope:
        raise HTTPException(400, 'Confirm that this exact target is listed in the written authorization')
    value = _normalize_asset(req.asset_type, req.value)
    scope_id = str(uuid.uuid4()); now = time.time()
    with _db() as connection:
        assessment = _assessment(connection, assessment_id)
        if not assessment['authorization_confirmed'] or assessment['status'] != 'open':
            raise HTTPException(409, 'Assessment is not open with recorded written authorization')
        try:
            connection.execute('INSERT INTO security_scope VALUES(?,?,?,?,?,?,?)',
                               (scope_id, assessment_id, req.asset_type, value, int(req.include_subdomains), req.confirmed_by.strip(), now))
        except sqlite3.IntegrityError:
            raise HTTPException(409, 'This target is already on the allowlist')
        _audit(connection, assessment_id, req.confirmed_by, 'scope_target_added', {'scope_id': scope_id, 'asset_type': req.asset_type})
    return {'id': scope_id, 'asset_type': req.asset_type, 'value': value, 'include_subdomains': req.include_subdomains}


@router.post('/security/assessments/{assessment_id}/scope/check')
def check_scope(assessment_id: str, req: ScopeCheck, request: Request):
    _require_local_ui(request)
    target = req.target.strip()
    try:
        ipaddress.ip_address(target)
    except ValueError:
        target = _domain(target)
    with _db() as connection:
        assessment = _assessment(connection, assessment_id)
        matches = _target_in_scope(connection, assessment_id, target)
        now = time.time()
        window_active = assessment['starts_at'] <= now <= assessment['ends_at']
        authorization_recorded = bool(assessment['authorization_confirmed'])
    if not authorization_recorded:
        message = 'Written authorization is not recorded.'
    elif not window_active:
        message = 'Assessment window is not active. The allowlist match does not authorize activity now.'
    elif not matches:
        message = 'Target is not on the explicit allowlist; no testing is available.'
    else:
        message = 'Target matches the recorded allowlist. This workspace does not run tests.'
    return {'target': target, 'in_scope': matches, 'authorization_recorded': authorization_recorded,
            'window_active': window_active, 'assessment_actions_enabled': False, 'message': message}


@router.get('/security/assessments/{assessment_id}/findings')
def list_findings(assessment_id: str, request: Request):
    _require_local_ui(request)
    with _db() as connection:
        _assessment(connection, assessment_id)
        rows = connection.execute('SELECT * FROM security_findings WHERE assessment_id=? ORDER BY created DESC', (assessment_id,)).fetchall()
    return {'findings': [dict(row) for row in rows]}


@router.post('/security/assessments/{assessment_id}/findings')
def create_finding(assessment_id: str, req: FindingCreate, request: Request):
    _require_local_ui(request)
    finding_id = str(uuid.uuid4()); now = time.time()
    evidence, redacted_count = _redact_evidence(req.evidence)
    with _db() as connection:
        assessment = _assessment(connection, assessment_id)
        if not assessment['authorization_confirmed'] or assessment['status'] != 'open':
            raise HTTPException(409, 'Assessment is not open with recorded written authorization')
        if not _target_in_scope(connection, assessment_id, req.asset):
            raise HTTPException(400, 'Finding target is outside the recorded allowlist')
        connection.execute('INSERT INTO security_findings VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                           (finding_id, assessment_id, req.title.strip(), req.severity, req.asset.strip(), evidence, redacted_count,
                            req.impact.strip(), req.remediation.strip(), req.confidence, req.status, req.identifier.strip(), req.created_by.strip(), now, now))
        _audit(connection, assessment_id, req.created_by, 'finding_created', {'finding_id': finding_id, 'severity': req.severity, 'redacted_count': redacted_count})
    return {'id': finding_id, 'status': req.status, 'redacted_count': redacted_count}


@router.post('/security/assessments/{assessment_id}/findings/{finding_id}/status')
def update_finding_status(assessment_id: str, finding_id: str, req: FindingStatus, request: Request):
    _require_local_ui(request)
    with _db() as connection:
        _assessment(connection, assessment_id)
        result = connection.execute('UPDATE security_findings SET status=?,updated=? WHERE id=? AND assessment_id=?',
                                    (req.status, time.time(), finding_id, assessment_id))
        if result.rowcount == 0:
            raise HTTPException(404, 'Finding not found')
        _audit(connection, assessment_id, req.actor, 'finding_status_changed', {'finding_id': finding_id, 'status': req.status})
    return {'ok': True, 'status': req.status}


@router.get('/security/assessments/{assessment_id}/audit')
def assessment_audit(assessment_id: str, request: Request):
    _require_local_ui(request)
    with _db() as connection:
        _assessment(connection, assessment_id)
        rows = connection.execute('SELECT id,actor,action,detail,created FROM security_audit WHERE assessment_id=? ORDER BY created DESC LIMIT 500', (assessment_id,)).fetchall()
    return {'events': [dict(row) for row in rows]}


class AssessmentDelete(BaseModel):
    confirm_name: str = Field(min_length=3, max_length=160)
    actor: str = Field(min_length=2, max_length=120)


@router.post('/security/assessments/{assessment_id}/delete')
def delete_assessment(assessment_id: str, req: AssessmentDelete, request: Request):
    import hashlib
    _require_local_ui(request)
    with _db() as connection:
        assessment = _assessment(connection, assessment_id)
        if req.confirm_name.strip() != assessment['name']:
            raise HTTPException(400, 'Type the assessment name exactly to confirm deletion')
        digest = hashlib.sha256(assessment_id.encode()).hexdigest()
        connection.execute('DELETE FROM security_findings WHERE assessment_id=?', (assessment_id,))
        connection.execute('DELETE FROM security_scope WHERE assessment_id=?', (assessment_id,))
        connection.execute('DELETE FROM security_audit WHERE assessment_id=?', (assessment_id,))
        connection.execute('DELETE FROM security_assessments WHERE id=?', (assessment_id,))
        connection.execute('INSERT INTO security_deletion_log VALUES(?,?,?,?)',
                           (str(uuid.uuid4()), digest, req.actor.strip(), time.time()))
    return {'ok': True, 'message': 'Assessment content, findings, scope, and event history deleted; only a minimal deletion record remains.'}


@router.get('/security/assessments/{assessment_id}/report')
def assessment_report(assessment_id: str, request: Request):
    _require_local_ui(request)
    with _db() as connection:
        assessment = _assessment(connection, assessment_id)
        assets = connection.execute('SELECT asset_type,value FROM security_scope WHERE assessment_id=? ORDER BY value', (assessment_id,)).fetchall()
        findings = connection.execute('SELECT * FROM security_findings WHERE assessment_id=? ORDER BY severity DESC,created', (assessment_id,)).fetchall()
    lines = [f"# Security assessment: {assessment['name']}", '', f"Client: {assessment['client']}", f"Environment: {assessment['environment']}",
             f"Assessment owner: {assessment['owner']}", f"Written authorization reference: {assessment['authorization_reference']}",
             f"Authorization recorded by: {assessment['authorized_by']}", f"Window: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(assessment['starts_at']))} – {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(assessment['ends_at']))}", '',
             '## Objectives', '', assessment['objectives'], '', '## Rules of engagement', '', assessment['rules_of_engagement'], '', '## Explicit allowlist', '']
    lines += [f"- {row['asset_type']}: {row['value']}" for row in assets] or ['- No targets recorded']
    lines += ['', '## Findings', '']
    for finding in findings:
        lines += [f"### {finding['title']}", '', f"- Severity: {finding['severity']}", f"- Status: {finding['status']}", f"- Confidence: {finding['confidence']}", f"- Asset: {finding['asset']}"]
        if finding['identifier']:
            lines.append(f"- CWE/CVE/reference: {finding['identifier']}")
        lines += ['', '**Evidence (redacted):**', '', finding['evidence'] or 'No evidence recorded.', '', '**Impact:**', '', finding['impact'], '', '**Remediation:**', '', finding['remediation'], '']
    lines += ['---', 'This report was generated from user-supplied assessment records. EzeAgent Scope does not perform active testing.']
    return {'markdown': '\n'.join(lines)}
