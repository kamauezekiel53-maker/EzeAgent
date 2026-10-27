from fastapi.testclient import TestClient
import os
import backend.main as m
client=TestClient(m.app)
def test_health():
 r=client.get('/api/v1/health'); assert r.status_code==200 and r.json()['status']=='ok'
def test_no_secrets_in_provider_list(monkeypatch):
 monkeypatch.setitem(m.P['openai'],'key','top-secret');data=client.get('/api/v1/providers').text;assert 'top-secret' not in data and '"key"' not in data
def test_missing_provider_key(monkeypatch):
 monkeypatch.setitem(m.P['openai'],'key','')
 r=client.post('/api/v1/chat',json={'provider':'openai','messages':[{'role':'user','content':'hi'}]});assert r.status_code==400
def test_path_traversal_rejected(tmp_path,monkeypatch):
 monkeypatch.setattr(m,'ROOT',tmp_path)
 r=client.post('/api/v1/workspace/read',json={'path':'../outside.txt'});assert r.status_code==400
def test_patch_requires_approval_and_applies(tmp_path,monkeypatch):
 monkeypatch.setattr(m,'ROOT',tmp_path);monkeypatch.setattr(m,'DB',tmp_path/'test.db')
 target=tmp_path/'sample.txt';target.write_text('old\n')
 r=client.post('/api/v1/workspace/proposals',json={'path':'sample.txt','content':'new\n'});assert r.status_code==200
 aid=r.json()['id'];assert target.read_text()=='old\n'
 done=client.post(f'/api/v1/approvals/{aid}/approve');assert done.status_code==200 and target.read_text()=='new\n'
def test_stale_patch_rejected(tmp_path,monkeypatch):
 monkeypatch.setattr(m,'ROOT',tmp_path);monkeypatch.setattr(m,'DB',tmp_path/'stale.db')
 target=tmp_path/'sample.txt';target.write_text('old')
 aid=client.post('/api/v1/workspace/proposals',json={'path':'sample.txt','content':'proposed'}).json()['id'];target.write_text('user edit')
 r=client.post(f'/api/v1/approvals/{aid}/approve');assert r.status_code==409 and target.read_text()=='user edit'
def test_reject_does_not_modify_file(tmp_path,monkeypatch):
 monkeypatch.setattr(m,'ROOT',tmp_path);monkeypatch.setattr(m,'DB',tmp_path/'reject.db')
 target=tmp_path/'sample.txt';target.write_text('original')
 aid=client.post('/api/v1/workspace/proposals',json={'path':'sample.txt','content':'new'}).json()['id']
 assert client.post(f'/api/v1/approvals/{aid}/reject').status_code==200;assert target.read_text()=='original'

def test_open_terminal_rejects_untrusted_origin():
 r=client.post('/api/v1/computer/terminal',headers={'Origin':'https://untrusted.example'})
 assert r.status_code==403

def test_open_word_uses_registered_executable(tmp_path,monkeypatch):
 monkeypatch.setattr(m,'DB',tmp_path/'word.db')
 monkeypatch.setattr(m,'find_office_executable',lambda name:r'C:\Office\WINWORD.EXE')
 calls=[]
 class Process: pid=1234
 monkeypatch.setattr(m.subprocess,'Popen',lambda *args,**kwargs:(calls.append((args,kwargs)) or Process()))
 r=client.post('/api/v1/computer/apps/word',headers={'Origin':'http://localhost:5173'})
 if os.name=='nt':
  assert r.status_code==200 and calls[0][0][0]==[r'C:\Office\WINWORD.EXE']
 else:assert r.status_code==501 and not calls

def test_open_word_falls_back_to_apps_folder(tmp_path,monkeypatch):
 monkeypatch.setattr(m,'DB',tmp_path/'word-fallback.db')
 monkeypatch.setattr(m,'find_office_executable',lambda name:None)
 calls=[]
 class Process: pid=1234
 monkeypatch.setattr(m.subprocess,'Popen',lambda *args,**kwargs:(calls.append((args,kwargs)) or Process()))
 r=client.post('/api/v1/computer/apps/word',headers={'Origin':'http://localhost:5173'})
 if os.name=='nt':
  assert r.status_code==200 and calls[0][0][0]==['explorer.exe',r'shell:AppsFolder\Microsoft.Office.WINWORD.EXE.15']
 else:assert r.status_code==501 and not calls

def test_omniroute_is_available_as_a_local_provider():
 providers={item['id']:item for item in client.get('/api/v1/providers').json()}
 assert providers['omniroute']['base_url']=='http://127.0.0.1:20128/v1'
 assert providers['omniroute']['default_model']=='auto'
 assert providers['omniroute']['kind']=='local'

def test_open_terminal_is_local_dashboard_only(tmp_path,monkeypatch):
 monkeypatch.setattr(m,'DB',tmp_path/'terminal.db')
 class Process: pid=1234
 monkeypatch.setattr(m.subprocess,'Popen',lambda *args,**kwargs:Process())
 r=client.post('/api/v1/computer/terminal',headers={'Origin':'http://localhost:5173'})
 if os.name=='nt':assert r.status_code==200 and r.json()['pid']==1234
 else:assert r.status_code==501

def test_command_runs_only_after_review_and_only_once(tmp_path,monkeypatch):
 monkeypatch.setattr(m,'DB',tmp_path/'command.db');monkeypatch.setattr(m,'COMMANDS',{})
 calls=[]
 class Result: returncode=0; stdout='done'; stderr=''
 def fake_run(args,**kwargs):calls.append((args,kwargs));return Result()
 monkeypatch.setattr(m.subprocess,'run',fake_run)
 headers={'Origin':'http://localhost:5173'}
 proposal=client.post('/api/v1/computer/commands/proposals',json={'command':'echo approved'},headers=headers)
 if os.name!='nt':assert proposal.status_code==501 and not calls;return
 assert proposal.status_code==200 and not calls
 command_id=proposal.json()['id']
 done=client.post(f'/api/v1/computer/commands/{command_id}/run',headers=headers)
 assert done.status_code==200 and done.json()['stdout']=='done' and len(calls)==1
 assert calls[0][0]==['cmd.exe','/d','/s','/c','echo approved']
 assert client.post(f'/api/v1/computer/commands/{command_id}/run',headers=headers).status_code==404




def test_computer_order_draft_rejects_untrusted_origin():
 r=client.post('/api/v1/computer/commands/draft',json={'provider':'omniroute','order':'show files'},headers={'Origin':'https://untrusted.example'})
 assert r.status_code==403

def test_computer_order_draft_returns_one_reviewable_command(monkeypatch):
 captured={}
 class Response:
  status_code=200
  def json(self):return {'choices':[{'message':{'content':'dir'}}]}
 class Client:
  def __init__(self,**kwargs):pass
  async def __aenter__(self):return self
  async def __aexit__(self,*args):return False
  async def post(self,url,**kwargs):captured.update(url=url,**kwargs);return Response()
 monkeypatch.setattr(m.httpx,'AsyncClient',Client)
 r=client.post('/api/v1/computer/commands/draft',json={'provider':'omniroute','order':'show me files'},headers={'Origin':'http://localhost:5173'})
 assert r.status_code==200 and r.json()['command']=='dir'
 assert captured['url']=='http://127.0.0.1:20128/v1/chat/completions'
 assert captured['json']['messages'][1]['content']=='show me files'

def test_computer_order_draft_rejects_multiline_command(monkeypatch):
 class Response:
  status_code=200
  def json(self):return {'choices':[{'message':{'content':'dir\ndel important.txt'}}]}
 class Client:
  def __init__(self,**kwargs):pass
  async def __aenter__(self):return self
  async def __aexit__(self,*args):return False
  async def post(self,*args,**kwargs):return Response()
 monkeypatch.setattr(m.httpx,'AsyncClient',Client)
 r=client.post('/api/v1/computer/commands/draft',json={'provider':'omniroute','order':'show files then remove some'},headers={'Origin':'http://localhost:5173'})
 assert r.status_code==422
