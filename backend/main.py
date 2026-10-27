from pathlib import Path
from typing import Any
import hashlib, ipaddress, json, os, shutil, sqlite3, subprocess, time, uuid
from threading import Lock
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
ROOT=Path(os.getenv('EZEAGENT_WORKSPACE','.')).resolve(); DB=Path(os.getenv('EZEAGENT_DATABASE_PATH','./ezeagent.db')).resolve(); TTL=max(60,int(os.getenv('EZEAGENT_APPROVAL_TTL_SECONDS','900')))
app=FastAPI(title='EzeAgent API',version='0.1.0')
app.add_middleware(CORSMiddleware,allow_origins=[x.strip() for x in os.getenv('EZEAGENT_CORS_ORIGINS','http://localhost:5173').split(',') if x.strip()],allow_methods=['GET','POST'],allow_headers=['Content-Type'])
def db():
 DB.parent.mkdir(parents=True,exist_ok=True); c=sqlite3.connect(DB,timeout=10); c.row_factory=sqlite3.Row
 c.execute('CREATE TABLE IF NOT EXISTS activity(id TEXT PRIMARY KEY,kind TEXT,detail TEXT,created REAL)'); c.execute('CREATE TABLE IF NOT EXISTS approvals(id TEXT PRIMARY KEY,path TEXT,content TEXT,digest TEXT,expires REAL,status TEXT,created REAL)'); return c
def audit(kind,detail):
 with db() as c:c.execute('INSERT INTO activity VALUES(?,?,?,?)',(str(uuid.uuid4()),kind,json.dumps(detail)[:4000],time.time()))
def safe(raw):
 p=(ROOT/raw).resolve()
 try:p.relative_to(ROOT)
 except ValueError:raise HTTPException(400,'Path outside configured workspace')
 if p==ROOT:raise HTTPException(400,'File path required')
 return p
def require_local_ui(request:Request):
 client=request.client.host if request.client else ''
 try:local=ipaddress.ip_address(client).is_loopback
 except ValueError:local=client=='testclient'
 origins={x.strip() for x in os.getenv('EZEAGENT_CORS_ORIGINS','http://localhost:5173').split(',') if x.strip()}
 if not local or request.headers.get('origin') not in origins:raise HTTPException(403,'Computer controls are available only from the local EzeAgent dashboard')
class TestReq(BaseModel): provider:str; base_url:str|None=None; api_key:str|None=None
class ChatReq(BaseModel): provider:str='openrouter'; model:str|None=None; api_key:str|None=None; messages:list[dict[str,str]]=Field(min_length=1,max_length=80)
class ReadReq(BaseModel): path:str
class Proposal(BaseModel): path:str; content:str=Field(max_length=500000)
class CommandReq(BaseModel): command:str=Field(min_length=1,max_length=2000)
P={'openrouter':{'name':'OpenRouter','base':'https://openrouter.ai/api/v1','key':os.getenv('OPENROUTER_API_KEY',''),'model':os.getenv('OPENROUTER_MODEL','meta-llama/llama-3.3-8b-instruct:free'),'kind':'cloud'},'openai':{'name':'OpenAI','base':'https://api.openai.com/v1','key':os.getenv('OPENAI_API_KEY',''),'model':os.getenv('OPENAI_MODEL','gpt-4o-mini'),'kind':'cloud'},'omniroute':{'name':'OmniRoute','base':os.getenv('OMNIROUTE_BASE_URL','http://127.0.0.1:20128/v1').rstrip('/'),'key':os.getenv('OMNIROUTE_API_KEY',''),'model':os.getenv('OMNIROUTE_MODEL','auto'),'kind':'local'}}
COMMANDS:dict[str,dict[str,Any]]={}
COMMANDS_LOCK=Lock()
APP_LAUNCH_IDS={'word':'Microsoft.Office.WINWORD.EXE.15','excel':'Microsoft.Office.EXCEL.EXE.15'}
def find_office_executable(app_name:str)->str|None:
 executable='WINWORD.EXE' if app_name=='word' else 'EXCEL.EXE'
 found=shutil.which(executable)
 if found:return found
 if os.name=='nt':
  try:
   import winreg
   for hive in (winreg.HKEY_CURRENT_USER,winreg.HKEY_LOCAL_MACHINE):
    for view in (winreg.KEY_WOW64_64KEY,winreg.KEY_WOW64_32KEY):
     try:
      with winreg.OpenKey(hive,rf'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{executable}',0,winreg.KEY_READ|view) as key:
       value,_=winreg.QueryValueEx(key,None)
       if Path(value).is_file():return value
     except OSError:pass
  except ImportError:pass
 return None
if os.getenv('COMPATIBLE_API_BASE_URL'):P['compatible']={'name':'OpenAI-compatible endpoint','base':os.getenv('COMPATIBLE_API_BASE_URL','').rstrip('/'),'key':os.getenv('COMPATIBLE_API_KEY',''),'model':os.getenv('COMPATIBLE_MODEL','local-model'),'kind':'local' if 'localhost' in os.getenv('COMPATIBLE_API_BASE_URL','') or '127.0.0.1' in os.getenv('COMPATIBLE_API_BASE_URL','') else 'compatible'}
@app.get('/api/v1/health')
def health():return {'status':'ok','version':'0.1.0'}
@app.get('/api/v1/providers')
def providers():return [{'id':k,'name':v['name'],'configured':bool(v['key']) or v['kind']!='cloud','kind':v['kind'],'base_url':v['base'],'default_model':v['model'],'capabilities':['chat'],'streaming':False,'tool_calling':False} for k,v in P.items()]
@app.post('/api/v1/providers/test')
async def test(req:TestReq):
 cfg=P.get(req.provider); base=(req.base_url or (cfg['base'] if cfg else '')).rstrip('/'); key=req.api_key if req.api_key is not None else (cfg['key'] if cfg else '')
 if not base:raise HTTPException(400,'Unknown provider; provide compatible base_url')
 try:
  async with httpx.AsyncClient(timeout=8) as c:r=await c.get(base+'/models',headers={'Authorization':f'Bearer {key}'} if key else {})
  if r.status_code==402:raise HTTPException(402,'Provider account has no available credits or billing is required')
  if r.status_code in (401,403):raise HTTPException(502,'Provider authentication failed; check the API key')
  if r.status_code>=400:raise HTTPException(502,f'Provider returned HTTP {r.status_code}')
  data=r.json();return {'ok':True,'provider':req.provider,'models':len(data.get('data',[])) if isinstance(data,dict) and isinstance(data.get('data'),list) else None}
 except httpx.TimeoutException:raise HTTPException(504,'Provider timeout')
 except httpx.RequestError:raise HTTPException(502,'Cannot connect to provider')
@app.get('/api/v1/models')
async def models(provider:str='openrouter'):
 cfg=P.get(provider)
 if not cfg:raise HTTPException(404,'Provider not configured')
 if cfg['kind']=='cloud' and not cfg['key']:return {'provider':provider,'models':[],'note':'Configure backend API key'}
 try:
  async with httpx.AsyncClient(timeout=8) as c:r=await c.get(cfg['base']+'/models',headers={'Authorization':f"Bearer {cfg['key']}"} if cfg['key'] else {});r.raise_for_status();d=r.json()
  return {'provider':provider,'models':[{'id':x['id']} for x in d.get('data',[]) if isinstance(x,dict) and x.get('id')][:200]}
 except (httpx.HTTPError,ValueError):raise HTTPException(502,'Model discovery failed; enter model manually')
@app.post('/api/v1/chat')
async def chat(req:ChatReq):
 cfg=P.get(req.provider)
 if not cfg:raise HTTPException(404,'Provider not configured')
 key=req.api_key if req.api_key is not None else cfg['key']
 if cfg['kind']=='cloud' and not key:raise HTTPException(400,f"{cfg['name']} API key required; enter it in Providers")
 model=req.model or cfg['model']
 try:
  async with httpx.AsyncClient(timeout=60) as c:r=await c.post(cfg['base']+'/chat/completions',json={'model':model,'messages':req.messages},headers={'Authorization':f"Bearer {key}"} if key else {})
  if r.status_code in (401,403):raise HTTPException(502,'Provider authentication failed')
  if r.status_code==429:raise HTTPException(429,'Provider rate limit reached')
  if r.status_code==402:raise HTTPException(402,'Provider account has no available credits or billing is required')
  if r.status_code>=400:raise HTTPException(502,f'Provider returned HTTP {r.status_code}')
  d=r.json();answer=d['choices'][0]['message']['content'];usage=d.get('usage');audit('chat',{'provider':req.provider,'model':model,'usage':usage if isinstance(usage,dict) else None});return {'provider':req.provider,'model':model,'content':answer,'usage':usage if isinstance(usage,dict) else None}
 except httpx.TimeoutException:raise HTTPException(504,'AI provider timeout')
 except httpx.RequestError:raise HTTPException(502,'Cannot connect to provider')
 except (KeyError,IndexError,TypeError,ValueError):raise HTTPException(502,'Malformed provider response')
@app.post('/api/v1/computer/terminal')
def open_terminal(request:Request):
 require_local_ui(request)
 if os.name!='nt':raise HTTPException(501,'Opening a Windows Command Prompt is available only on Windows')
 try:p=subprocess.Popen(['cmd.exe'],cwd=str(ROOT),creationflags=subprocess.CREATE_NEW_CONSOLE,close_fds=True)
 except OSError:raise HTTPException(500,'Could not open Command Prompt')
 audit('computer_terminal_opened',{'pid':p.pid})
 return {'ok':True,'message':'Command Prompt opened','pid':p.pid}
@app.post('/api/v1/computer/apps/{app_name}')
def open_app(app_name:str,request:Request):
 require_local_ui(request)
 if os.name!='nt':raise HTTPException(501,'Application launch is available only on Windows')
 app_name=app_name.lower()
 if app_name not in APP_LAUNCH_IDS:raise HTTPException(404,'Application shortcut is not configured')
 executable=find_office_executable(app_name)
 try:
  if executable:p=subprocess.Popen([executable],cwd=str(ROOT),close_fds=True)
  else:p=subprocess.Popen(['explorer.exe',f'shell:AppsFolder\\{APP_LAUNCH_IDS[app_name]}'],cwd=str(ROOT),close_fds=True)
 except OSError:raise HTTPException(500,f'Could not open {app_name.title()}. Make sure Microsoft Office is installed.')
 audit('computer_app_opened',{'app':app_name.lower(),'pid':p.pid})
 return {'ok':True,'message':f'{app_name.title()} launch requested','pid':p.pid}
@app.post('/api/v1/computer/commands/proposals')
def propose_command(req:CommandReq,request:Request):
 require_local_ui(request)
 if os.name!='nt':raise HTTPException(501,'Windows commands are available only on Windows')
 now=time.time();command_id=str(uuid.uuid4())
 with COMMANDS_LOCK:
  for key in [k for k,v in COMMANDS.items() if v['expires']<now]:COMMANDS.pop(key,None)
  COMMANDS[command_id]={'command':req.command,'expires':now+TTL}
 audit('computer_command_proposed',{'proposal_id':command_id,'expires':now+TTL})
 return {'id':command_id,'command':req.command,'expires_at':now+TTL}
@app.post('/api/v1/computer/commands/{command_id}/run')
def run_command(command_id:str,request:Request):
 require_local_ui(request)
 if os.name!='nt':raise HTTPException(501,'Windows commands are available only on Windows')
 now=time.time()
 with COMMANDS_LOCK:
  item=COMMANDS.get(command_id)
  if not item:raise HTTPException(404,'Command proposal not found or already used')
  if item['expires']<now:COMMANDS.pop(command_id,None);raise HTTPException(409,'Command proposal expired; review it again')
  COMMANDS.pop(command_id,None)
 command=item['command']
 try:result=subprocess.run(['cmd.exe','/d','/s','/c',command],cwd=str(ROOT),capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=30,creationflags=subprocess.CREATE_NO_WINDOW)
 except subprocess.TimeoutExpired as exc:
  out=exc.stdout or '';err=exc.stderr or ''
  if isinstance(out,bytes):out=out.decode('utf-8','replace')
  if isinstance(err,bytes):err=err.decode('utf-8','replace')
  audit('computer_command_timeout',{'proposal_id':command_id})
  return {'exit_code':None,'stdout':out[-12000:],'stderr':err[-12000:],'timed_out':True}
 except OSError:raise HTTPException(500,'Could not run the reviewed command')
 audit('computer_command_run',{'proposal_id':command_id,'exit_code':result.returncode})
 return {'exit_code':result.returncode,'stdout':result.stdout[-12000:],'stderr':result.stderr[-12000:],'timed_out':False}
@app.get('/api/v1/workspace/files')
def files():
 out=[]
 for p in ROOT.rglob('*'):
  if len(out)>=500:break
  if any(x in {'.git','.venv','node_modules','__pycache__'} for x in p.parts) or p.is_symlink():continue
  if p.is_file():
   try:out.append({'path':p.relative_to(ROOT).as_posix(),'size':p.stat().st_size})
   except OSError:pass
 return {'workspace':str(ROOT),'files':out}
@app.post('/api/v1/workspace/read')
def read(req:ReadReq):
 p=safe(req.path)
 if not p.is_file() or p.stat().st_size>1000000:raise HTTPException(400,'Missing file or file exceeds 1 MB')
 try:return {'path':req.path,'content':p.read_text(encoding='utf-8')}
 except (UnicodeDecodeError,OSError):raise HTTPException(400,'File is not readable UTF-8 text')
@app.post('/api/v1/workspace/proposals')
def propose(req:Proposal):
 p=safe(req.path)
 if not p.is_file() or p.stat().st_size>1000000:raise HTTPException(400,'Only existing files up to 1 MB can be patched')
 try:old=p.read_text(encoding='utf-8')
 except (UnicodeDecodeError,OSError):raise HTTPException(400,'Target must be UTF-8 text')
 import difflib
 aid=str(uuid.uuid4());digest=hashlib.sha256(p.read_bytes()).hexdigest();expires=time.time()+TTL
 with db() as c:c.execute('INSERT INTO approvals VALUES(?,?,?,?,?,?,?)',(aid,req.path,req.content,digest,expires,'pending',time.time()))
 audit('patch_proposed',{'approval_id':aid,'path':req.path,'expires':expires})
 return {'id':aid,'path':req.path,'old_sha256':digest,'new_sha256':hashlib.sha256(req.content.encode()).hexdigest(),'expires_at':expires,'diff':'\n'.join(difflib.unified_diff(old.splitlines(),req.content.splitlines(),fromfile=req.path,tofile=req.path+' (proposed)',lineterm=''))}
@app.get('/api/v1/approvals')
def approvals():
 with db() as c:rows=c.execute('SELECT id,path,digest,expires,status,created FROM approvals ORDER BY created DESC LIMIT 100').fetchall()
 return {'approvals':[dict(r) for r in rows]}
@app.post('/api/v1/approvals/{aid}/approve')
def approve(aid:str):
 stale_path=None
 with db() as c:
  row=c.execute('SELECT * FROM approvals WHERE id=?',(aid,)).fetchone()
  if not row:raise HTTPException(404,'Approval not found')
  if row['status']!='pending':raise HTTPException(409,'Approval no longer pending')
  if row['expires']<time.time():c.execute("UPDATE approvals SET status='expired' WHERE id=?",(aid,));c.commit();raise HTTPException(409,'Approval expired')
  p=safe(row['path'])
  if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=row['digest']:
   c.execute("UPDATE approvals SET status='stale' WHERE id=?",(aid,));c.commit();stale_path=row['path']
  else:
   tmp=p.with_name(p.name+'.ezeagent.tmp')
   try:tmp.write_text(row['content'],encoding='utf-8');os.replace(tmp,p)
   except OSError:
    tmp.unlink(missing_ok=True);raise HTTPException(500,'Patch failed; original file preserved')
   c.execute("UPDATE approvals SET status='applied' WHERE id=?",(aid,))
 if stale_path:
  audit('patch_stale',{'approval_id':aid,'path':stale_path});raise HTTPException(409,'File changed; create a fresh proposal')
 audit('patch_applied',{'approval_id':aid,'path':row['path']});return {'ok':True,'status':'applied','path':row['path']}
@app.post('/api/v1/approvals/{aid}/reject')
def reject(aid:str):
 with db() as c:
  row=c.execute('SELECT status FROM approvals WHERE id=?',(aid,)).fetchone()
  if not row:raise HTTPException(404,'Approval not found')
  if row['status']!='pending':raise HTTPException(409,'Approval no longer pending')
  c.execute("UPDATE approvals SET status='rejected' WHERE id=?",(aid,))
 audit('patch_rejected',{'approval_id':aid});return {'ok':True,'status':'rejected'}
@app.get('/api/v1/activity')
def activity():
 with db() as c:rows=c.execute('SELECT id,kind,detail,created FROM activity ORDER BY created DESC LIMIT 100').fetchall()
 return {'events':[dict(x) for x in rows]}


