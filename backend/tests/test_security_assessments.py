import time
from fastapi.testclient import TestClient
import backend.main as main
import backend.security_assessments as security

client=TestClient(main.app)
HEADERS={'Origin':'http://localhost:5173'}

def create_assessment(tmp_path,monkeypatch):
 monkeypatch.setattr(security,'DB_PATH',tmp_path/'security.db')
 now=time.time()
 payload={'name':'Staging web review','client':'Example Org','owner':'Assessment lead','authorization_reference':'Ticket SEC-123','written_authorization_confirmed':True,'authorized_by':'Lead reviewer','environment':'staging','starts_at':now-60,'ends_at':now+3600,'retention_until':now+90*24*3600,'objectives':'Review staging configuration','rules_of_engagement':'Read-only review; stop and notify the owner if sensitive data appears.'}
 response=client.post('/api/v1/security/assessments',json=payload,headers=HEADERS)
 assert response.status_code==200,response.text
 return response.json()['id']

def add_domain(assessment_id):
 response=client.post(f'/api/v1/security/assessments/{assessment_id}/scope',json={'asset_type':'domain','value':'staging.example.com','confirmed_in_written_scope':True,'confirmed_by':'Lead reviewer'},headers=HEADERS)
 assert response.status_code==200,response.text

def test_assessment_creation_requires_written_authorization_attestation(tmp_path,monkeypatch):
 monkeypatch.setattr(security,'DB_PATH',tmp_path/'no-auth.db')
 response=client.post('/api/v1/security/assessments',json={'name':'No authorization','client':'Example Org','owner':'Lead','authorization_reference':'Ticket 1','written_authorization_confirmed':False,'authorized_by':'Lead','environment':'staging','starts_at':time.time()-1,'ends_at':time.time()+60,'retention_until':time.time()+90*24*3600,'objectives':'Review','rules_of_engagement':'Read only'},headers=HEADERS)
 assert response.status_code==400

def test_scope_check_is_exact_and_never_enables_testing(tmp_path,monkeypatch):
 assessment_id=create_assessment(tmp_path,monkeypatch);add_domain(assessment_id)
 exact=client.post(f'/api/v1/security/assessments/{assessment_id}/scope/check',json={'target':'STAGING.example.com.'},headers=HEADERS).json()
 subdomain=client.post(f'/api/v1/security/assessments/{assessment_id}/scope/check',json={'target':'api.staging.example.com'},headers=HEADERS).json()
 external=client.post(f'/api/v1/security/assessments/{assessment_id}/scope/check',json={'target':'production.example.com'},headers=HEADERS).json()
 assert exact['in_scope'] is True and exact['assessment_actions_enabled'] is False
 assert subdomain['in_scope'] is False
 assert external['in_scope'] is False

def test_subdomains_only_match_when_explicitly_included(tmp_path,monkeypatch):
 assessment_id=create_assessment(tmp_path,monkeypatch)
 response=client.post(f'/api/v1/security/assessments/{assessment_id}/scope',json={'asset_type':'domain','value':'example.com','include_subdomains':True,'confirmed_in_written_scope':True,'confirmed_by':'Lead'},headers=HEADERS)
 assert response.status_code==200
 result=client.post(f'/api/v1/security/assessments/{assessment_id}/scope/check',json={'target':'api.example.com'},headers=HEADERS).json()
 assert result['in_scope'] is True and result['assessment_actions_enabled'] is False

def test_findings_require_in_scope_asset_and_redact_secrets(tmp_path,monkeypatch):
 assessment_id=create_assessment(tmp_path,monkeypatch);add_domain(assessment_id)
 payload={'title':'Sensitive header exposed','severity':'high','asset':'outside.example.net','evidence':'none','impact':'Could expose sensitive information.','remediation':'Remove it.','confidence':'medium','created_by':'Reviewer'}
 outside=client.post(f'/api/v1/security/assessments/{assessment_id}/findings',json=payload,headers=HEADERS)
 assert outside.status_code==400
 payload['asset']='staging.example.com';payload['evidence']='Authorization: Bearer abcdefghijklmnop\napi_key=sk-or-v1-'+'a'*32
 saved=client.post(f'/api/v1/security/assessments/{assessment_id}/findings',json=payload,headers=HEADERS)
 assert saved.status_code==200 and saved.json()['redacted_count']>=2
 report=client.get(f'/api/v1/security/assessments/{assessment_id}/report',headers=HEADERS).json()['markdown']
 assert 'abcdefghijklmnop' not in report and 'sk-or-v1-' not in report and '[REDACTED]' in report

def test_assessment_apis_require_local_dashboard_origin():
 response=client.get('/api/v1/security/assessments',headers={'Origin':'https://untrusted.example'})
 assert response.status_code==403

def test_audit_records_scope_and_finding_work_without_evidence_content(tmp_path,monkeypatch):
 assessment_id=create_assessment(tmp_path,monkeypatch);add_domain(assessment_id)
 events=client.get(f'/api/v1/security/assessments/{assessment_id}/audit',headers=HEADERS).json()['events']
 assert [item['action'] for item in events].count('assessment_created')==1
 assert [item['action'] for item in events].count('scope_target_added')==1
 assert 'example.com' not in str(events)


def test_delete_requires_exact_assessment_name_and_removes_assessment_content(tmp_path,monkeypatch):
 assessment_id=create_assessment(tmp_path,monkeypatch);add_domain(assessment_id)
 denied=client.post(f'/api/v1/security/assessments/{assessment_id}/delete',json={'confirm_name':'wrong','actor':'Lead'},headers=HEADERS)
 assert denied.status_code==400
 deleted=client.post(f'/api/v1/security/assessments/{assessment_id}/delete',json={'confirm_name':'Staging web review','actor':'Lead'},headers=HEADERS)
 assert deleted.status_code==200
 assert 'only a minimal deletion record remains' in deleted.json()['message']
 assert client.get('/api/v1/security/assessments',headers=HEADERS).json()['assessments']==[]
 with security._db() as connection:
  assert connection.execute('SELECT COUNT(*) FROM security_scope WHERE assessment_id=?',(assessment_id,)).fetchone()[0]==0
  assert connection.execute('SELECT COUNT(*) FROM security_audit WHERE assessment_id=?',(assessment_id,)).fetchone()[0]==0
  assert connection.execute('SELECT COUNT(*) FROM security_deletion_log').fetchone()[0]==1
