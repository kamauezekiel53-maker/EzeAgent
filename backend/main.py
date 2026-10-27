from pathlib import Path
from typing import Any
import hashlib, json, os, sqlite3, time, uuid
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
ROOT=Path(os.getenv('EZEAGENT_WORKSPACE','.')).resolve(); DB=Path(os.getenv('EZEAGENT_DATABASE_PATH','./ezeagent.db')).resolve(); TTL=max(60,int(os.getenv('EZEAGENT_APPROVAL_TTL_SECONDS','900')))
app=FastAPI(title='EzeAgent API',version='0.1.0')
app.add_middleware(CORSMiddleware,allow_origins=[x.strip() for x in os.getenv('EZEAGENT_CORS_ORIGINS','http://localhost:5173').split(',') if x.strip()],allow_methods=['GET','POST'],allow_headers=['Content-Type'])
def db():
 DB.parent.mkdir(parents=True,exist_ok=True); c=sqlite3.connect(DB); c.row_factory=sqlite3.Row
 c.execute('CREATE TABLE IF NOT EXISTS activity(id TEXT PRIMARY KEY,kind TEXT,detail TEXT,created REAL)'); c.execute('CREATE TABLE IF NOT EXISTS approvals(id TEXT PRIMARY KEY,path TEXT,content TEXT,digest TEXT,expires REAL,status TEXT,created REAL)'); return c
def audit(kind,detail):
 with db() as c:c.execute('INSERT INTO activity VALUES(?,?,?,?)',(str(uuid.uuid4()),kind,json.dumps(detail)[:4000],time.time()))
def safe(raw):
 p=(ROOT/raw).resolve()
 try:p.relative_to(ROOT)
 except ValueError:raise HTTPException(400,'Path outside configured workspace')
 if p==ROOT:raise HTTPException(400,'File path required')
 return p
class TestReq(BaseModel): provider:str; base_url:str|None=None; api_key:str|None=None
class ChatReq(BaseModel): provider:str='openrouter'; model:str|None=None; messages:list[dict[str,str]]=Field(min_length=1,max_length=80)
class ReadReq(BaseModel): path:str
class Proposal(BaseModel): path:str; content:str=Field(max_length=500000)
P={'openrouter':{'name':'OpenRouter','base':'https://openrouter.ai/api/v1','key':os.getenv('OPENROUTER_API_KEY',''),'model':os.getenv('OPENROUTER_MODEL','meta-llama/llama-3.3-8b-instruct:free'),'kind':'cloud'},'openai':{'name':'OpenAI','base':'https://api.openai.com/v1','key':os.getenv('OPENAI_API_KEY',''),'model':os.getenv('OPENAI_MODEL','gpt-4o-mini'),'kind':'cloud'}}
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
 if cfg['kind']=='cloud' and not cfg['key']:raise HTTPException(400,f"{cfg['name']} API key missing on backend")
 model=req.model or cfg['model']
 try:
  async with httpx.AsyncClient(timeout=60) as c:r=await c.post(cfg['base']+'/chat/completions',json={'model':model,'messages':req.messages},headers={'Authorization':f"Bearer {cfg['key']}"} if cfg['key'] else {})
  if r.status_code in (401,403):raise HTTPException(502,'Provider authentication failed')
  if r.status_code==429:raise HTTPException(429,'Provider rate limit reached')
  if r.status_code>=400:raise HTTPException(502,f'Provider returned HTTP {r.status_code}')
  d=r.json();answer=d['choices'][0]['message']['content'];usage=d.get('usage');audit('chat',{'provider':req.provider,'model':model,'usage':usage if isinstance(usage,dict) else None});return {'provider':req.provider,'model':model,'content':answer,'usage':usage if isinstance(usage,dict) else None}
 except httpx.TimeoutException:raise HTTPException(504,'AI provider timeout')
 except httpx.RequestError:raise HTTPException(502,'Cannot connect to provider')
 except (KeyError,IndexError,TypeError,ValueError):raise HTTPException(502,'Malformed provider response')
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
 aid=str(uuid.uuid4());digest=hashlib.sha256(old.encode()).hexdigest();expires=time.time()+TTL
 with db() as c:c.execute('INSERT INTO approvals VALUES(?,?,?,?,?,?,?)',(aid,req.path,req.content,digest,expires,'pending',time.time()))
 audit('patch_proposed',{'approval_id':aid,'path':req.path,'expires':expires})
 return {'id':aid,'path':req.path,'old_sha256':digest,'new_sha256':hashlib.sha256(req.content.encode()).hexdigest(),'expires_at':expires,'diff':'\n'.join(difflib.unified_diff(old.splitlines(),req.content.splitlines(),fromfile=req.path,tofile=req.path+' (proposed)',lineterm=''))}
@app.get('/api/v1/approvals')
def approvals():
 with db() as c:rows=c.execute('SELECT id,path,digest,expires,status,created FROM approvals ORDER BY created DESC LIMIT 100').fetchall()
 return {'approvals':[dict(r) for r in rows]}
@app.post('/api/v1/approvals/{aid}/approve')
def approve(aid:str):
 with db() as c:
  row=c.execute('SELECT * FROM approvals WHERE id=?',(aid,)).fetchone()
  if not row:raise HTTPException(404,'Approval not found')
  if row['status']!='pending':raise HTTPException(409,'Approval no longer pending')
  if row['expires']<time.time():c.execute("UPDATE approvals SET status='expired' WHERE id=?",(aid,));raise HTTPException(409,'Approval expired')
  p=safe(row['path'])
  if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=row['digest']:
   c.execute("UPDATE approvals SET status='stale' WHERE id=?",(aid,));audit('patch_stale',{'approval_id':aid});raise HTTPException(409,'File changed; create a fresh proposal')
  tmp=p.with_name(p.name+'.ezeagent.tmp')
  try:tmp.write_text(row['content'],encoding='utf-8');os.replace(tmp,p)
  except OSError:
   tmp.unlink(missing_ok=True);raise HTTPException(500,'Patch failed; original file preserved')
  c.execute("UPDATE approvals SET status='applied' WHERE id=?",(aid,))
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
