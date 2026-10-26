"""Create a private, credential-free project snapshot with data as Release assets."""
from __future__ import annotations
import argparse
import collections
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import paramiko
import ast
import gzip
import tarfile
import tokenize
import shutil
import subprocess
import tempfile
import datetime
import threading
from audit_github_server_readonly import discover_tokens, profiles, github, REMOTE_INSPECT, ROOT, emit

OUT = ROOT / "deliveries" / "GitHub_Full_Snapshot_20261026_v2"
REPO_NAME = "DiseaseX-Nexus"
TAG = "snapshot-2026-10-26"
ASSET_PREFIX='local-data-part'
SKIP_DIRS = {'.git','.venv','venv','node_modules','__pycache__','.cache','cache','secrets','private','logs','log','runtime_logs','runtime','huggingface','pip','pkgs','envs','deliveries','.agents','.codex','.codex_tmp','.codex_doc_review','.runtime','.streamlit_home','.streamlit-demo','.pytest_cache','.mypy_cache','.ruff_cache','backups','postgres-data','redis-data','.minio'}
SKIP_SUFFIX = {'.pyc','.pyd','.dll','.exe','.log','.pid','.tmp','.zip','.7z','.rar','.tgz','.gz','.tar','.xz','.bz2','.pem','.key','.pfx','.p12','.pgcustom','.dump','.sqlite','.sqlite3','.db','.sqlite-wal','.sqlite-shm','.docx','.doc','.pptx','.ppt','.pdf','.emf','.wmf','.ai'}
CODE_SUFFIX = {'.py','.js','.ts','.tsx','.cjs','.mjs','.css','.scss','.sh','.ps1','.bat','.cmd','.toml','.yaml','.yml','.ini','.mako','.jinja','.j2','.md','.rst','.sql','.example','.html'}
TEXT_SUFFIX = CODE_SUFFIX | {'.json','.jsonl','.csv','.tsv','.txt','.xml','.svg','.geojson','.conf','.cfg','.in','.lock','.patch','.diff','.stdout','.stderr'}
SECRETS = []
IP_RE = re.compile(r'(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])')
TOKEN_RE = re.compile(r'(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{50,}|sk-[A-Za-z0-9_-]{20,}|AKIA[A-Z0-9]{16})')
SENSITIVE_NAME = re.compile(r'password|passwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|private[_-]?key|username|ssh[_-]?user',re.I)


def scrub(text):
    for value in SECRETS:
        if len(value)>=6:
            text=text.replace(value,'REDACTED')
    text=TOKEN_RE.sub('REDACTED_TOKEN',text)
    text=re.sub(r'-----BEGIN [^-\r\n]*PRIVATE KEY-----.*?-----END [^-\r\n]*PRIVATE KEY-----','REDACTED_PRIVATE_KEY',text,flags=re.S)
    text=IP_RE.sub(lambda m:m.group() if m.group().startswith('127.') or m.group() in {'0.0.0.0','255.255.255.255'} else 'SERVER_HOST',text)
    text=re.sub(r'(?i)\b([a-z][a-z0-9+.-]{0,20}://)[^\s/\"\'<>@]{1,200}@',r'\1REDACTED@',text)
    text=re.sub(r'(?i)USER_HOME/\s\"\'<>]{1,100}|/root(?=/)|[a-z]:[\\/]+Users[\\/]+[^\\/\s\"\'<>]{1,100}','USER_HOME',text)
    text=re.sub(r'(?i)\b(Bearer|Basic)\s+[A-Za-z0-9_+/=.-]{12,}',r'\1 REDACTED',text)
    return text


def sanitize_python(text):
    # Redact string literals without changing Python punctuation or identifiers.
    original=text
    try:
        tree=ast.parse(text)
        replacements=[]
        def mark(node):
            if isinstance(node,ast.Constant) and isinstance(node.value,str) and node.value:
                replacements.append((node.lineno,node.col_offset,node.end_lineno,node.end_col_offset,repr('REDACTED')))
        for node in ast.walk(tree):
            if isinstance(node,ast.Assign):
                names=[x.id for x in node.targets if isinstance(x,ast.Name)]
                if any(SENSITIVE_NAME.search(x) for x in names):mark(node.value)
            elif isinstance(node,ast.AnnAssign) and isinstance(node.target,ast.Name) and SENSITIVE_NAME.search(node.target.id):
                mark(node.value)
                if isinstance(node.value,ast.Call):
                    for kw in node.value.keywords:
                        if kw.arg=='default':mark(kw.value)
            elif isinstance(node,ast.Dict):
                for key,value in zip(node.keys,node.values):
                    if isinstance(key,ast.Constant) and isinstance(key.value,str) and SENSITIVE_NAME.search(key.value):mark(value)
            elif isinstance(node,ast.keyword) and node.arg and SENSITIVE_NAME.search(node.arg):mark(node.value)
        lines=text.splitlines(keepends=True)
        for a,b,c,d,value in sorted(set(replacements),reverse=True):
            first=lines[a-1].encode('utf-8');last=lines[c-1].encode('utf-8')
            lines[a-1:c]=[(first[:b]+value.encode()+last[d:]).decode('utf-8')]
        text=''.join(lines)
        tokens=[]
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type in {tokenize.STRING,tokenize.COMMENT}:
                token=token._replace(string=scrub(token.string))
            tokens.append(token)
        text=tokenize.untokenize(tokens)
        ast.parse(text)
        return text,True
    except (SyntaxError,tokenize.TokenError,IndentationError):
        fallback=scrub(original)
        try:
            if not locals().get('replacements'):
                ast.parse(fallback)
                return fallback,True
        except SyntaxError:pass
        return fallback,False


def denied(rel):
    parts=Path(rel).parts
    name=parts[-1].lower()
    if name in {'.pgpass','.netrc','.git-credentials','.npmrc','.pypirc','pg_service.conf'} or ('auth' in name and Path(name).suffix not in CODE_SUFFIX):return 'authentication_state'
    if any(x in SKIP_DIRS or x.endswith('.egg-info') for x in parts):return 'environment_cache_backup_or_credentials'
    if any(k in name for k in ('服务器信息','credential','known_hosts','id_rsa','id_ed25519','密码','用户名')):return 'credentials'
    if (name=='.env' or name.endswith('.env') or name.startswith('.env.')) and name!='.env.example':return 'environment_credentials'
    if name.endswith(('.token','.secret')) or name in {'postmaster.opts','postmaster.pid','pg_hba.conf','pg_ident.conf'}:return 'credentials_or_database_runtime'
    if Path(name).suffix=='.sql' and not ({'database','migrations','scripts'} & set(parts)):return 'historical_database_dump'
    if Path(name).suffix in SKIP_SUFFIX:return 'opaque_archive_runtime_database_or_publication'
    if re.search(r'\.part\d+$',name):return 'historical_archive_part'
    if any(k in rel.lower() for k in ('screenshot','截图','screen_capture')):return 'unreviewed_ui_capture'
    if 'models' in parts and Path(name).suffix in {'.safetensors','.bin'}:return 'redownloadable_base_model'
    if scrub(rel)!=rel:return 'sensitive_filename'
    return None


REMOTE_STREAM = r'''
import os,sys,json,tarfile,io,hashlib,re,subprocess,tempfile,urllib.parse
skip_dirs=SKIP_DIRS
skip_suffix=SKIP_SUFFIX
excluded=[];base_models=[];changed=[];db_status={'status':'not_found'}
def add_bytes(tar,name,data):
 info=tarfile.TarInfo(name);info.size=len(data);info.mode=0o644;tar.addfile(info,io.BytesIO(data))
def pg_environment(main):
 urls={}
 for sub in ['config','configs']:
  start=os.path.join(main,sub)
  for here,dirs,files in os.walk(start):
   if here[len(start):].count(os.sep)>4:dirs[:]=[]
   for name in files:
    if 'env' not in name.lower():continue
    p=os.path.join(here,name)
    if os.path.getsize(p)>1024**2:continue
    for line in open(p,encoding='utf-8',errors='replace'):
     m=re.match(r'\s*(?:export\s+)?([A-Z_]+)\s*=\s*(.+?)\s*$',line)
     if m and 'DATABASE_URL' in m.group(1):urls[m.group(1)]=m.group(2).strip('\"\'')
 for name in ['.env','.env.production']:
  p=os.path.join(main,name)
  if os.path.isfile(p):
   for line in open(p,encoding='utf-8',errors='replace'):
    m=re.match(r'\s*([A-Z_]*DATABASE_URL)\s*=\s*(.+?)\s*$',line)
    if m:urls[m.group(1)]=m.group(2).strip('\"\'')
 if not urls:
  for pid in os.listdir('/proc'):
   if not pid.isdigit():continue
   try:
    cmd=open('/proc/'+pid+'/cmdline','rb').read()
    if main.encode() not in cmd:
     try:
      cwd=os.readlink('/proc/'+pid+'/cwd')
      if not any(cwd.startswith(r) for r in ROOTS):continue
     except OSError:continue
    for entry in open('/proc/'+pid+'/environ','rb').read().split(b'\0'):
     k,sep,v=entry.partition(b'=')
     if sep and b'DATABASE_URL' in k:urls[k.decode()]=v.decode()
   except OSError:pass
 dsn=urls.get('MIGRATION_DATABASE_URL') or urls.get('APP_DATABASE_URL') or urls.get('DATABASE_URL')
 if not dsn:return None,None,None
 parsed=urllib.parse.urlsplit(dsn.replace('postgresql+psycopg://','postgresql://'))
 env=dict(os.environ);env.update(PGHOST=parsed.hostname or 'localhost',PGPORT=str(parsed.port or 5432),PGUSER=urllib.parse.unquote(parsed.username or ''),PGPASSWORD=urllib.parse.unquote(parsed.password or ''),PGDATABASE=parsed.path.lstrip('/'),PGCONNECT_TIMEOUT='10')
 bins={}
 for here,dirs,files in os.walk(os.path.join(main,'runtime')):
  if here[len(main):].count(os.sep)>7:dirs[:]=[]
  for name in ['pg_dump','psql']:
   if name in files and os.access(os.path.join(here,name),os.X_OK):bins[name]=os.path.join(here,name)
 if len(bins)<2:
  import shutil
  for name in ['pg_dump','psql']:
   if shutil.which(name):bins[name]=shutil.which(name)
 return env,bins,urls
with tarfile.open(fileobj=sys.stdout.buffer,mode='w|') as tar:
 for root in ROOTS:
  alias=os.path.basename(root)
  for here,dirs,files in os.walk(root,followlinks=False):
   dirs[:]=sorted(d for d in dirs if d not in skip_dirs and not d.startswith('.') and not os.path.islink(os.path.join(here,d)))
   if os.path.isfile(os.path.join(here,'PG_VERSION')):
    excluded.append({'path':'server/'+alias+'/'+os.path.relpath(here,root),'reason':'active_database_directory_logical_export_instead'});dirs[:]=[];continue
   for name in sorted(files):
    path=os.path.join(here,name);rel=os.path.relpath(path,root).replace(os.sep,'/');key='server/'+alias+'/'+rel;ext=os.path.splitext(name)[1].lower()
    if os.path.islink(path):continue
    lower=name.lower()
    if any(x in lower for x in ['credential','secret','known_hosts','password','id_rsa','id_ed25519']) or (lower.startswith('.env') and lower!='.env.example') or lower.endswith('.env') or ext in skip_suffix:
     excluded.append({'path':key,'reason':'credentials_environment_opaque_archive_or_runtime'});continue
    if 'models' in rel.split('/') and ext in {'.safetensors','.bin'}:
     digest=hashlib.sha256()
     with open(path,'rb') as f:
      for block in iter(lambda:f.read(4*1024**2),b''):digest.update(block)
     base_models.append({'path':key,'bytes':os.path.getsize(path),'sha256':digest.hexdigest(),'restore':'download original base model using included config/tokenizer identifiers; verify hash'});continue
    try:
     before=os.stat(path)
     info=tarfile.TarInfo(key);info.size=before.st_size;info.mode=before.st_mode&0o777;info.mtime=before.st_mtime
     with open(path,'rb') as f:tar.addfile(info,f)
     after=os.stat(path)
     if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):changed.append(key)
    except OSError:excluded.append({'path':key,'reason':'unreadable'})
 for root in ROOTS:
  if os.path.basename(root)!='epidemic-openclaw':continue
  env,bins,urls=pg_environment(root)
  if env and bins and 'psql' in bins and 'pg_dump' in bins:
   try:
    query="SELECT table_schema || '.' || table_name FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog','information_schema') AND table_type='BASE TABLE' ORDER BY 1"
    tables=subprocess.run([bins['psql'],'-XAt','-c',query],env=env,capture_output=True,text=True,timeout=30)
    if tables.returncode:raise RuntimeError('database_metadata_unavailable')
    names=tables.stdout.splitlines();exclude=[x for x in names if re.search(r'(^|[_.])(auth|users|sessions|credentials|secrets|api_keys|tokens)([_.]|$)',x,re.I)]
    cmd=[bins['pg_dump'],'--format=plain','--no-owner','--no-privileges','--no-comments']
    for table in exclude:cmd.extend(['--exclude-table='+table])
    dumped=subprocess.run(cmd,env=env,capture_output=True,timeout=300)
    if dumped.returncode:raise RuntimeError('database_logical_export_failed')
    add_bytes(tar,'database/business_database.sql',dumped.stdout)
    db_status={'status':'exported','business_tables':len(names)-len(exclude),'excluded_auth_tables':len(exclude),'bytes':len(dumped.stdout),'consistent_snapshot':True,'format':'pg_dump plain SQL no owners no privileges'}
   except Exception as exc:db_status={'status':'failed','error_type':type(exc).__name__}
  else:db_status={'status':'connection_configuration_or_tools_not_found'}
 metadata={'excluded':excluded,'redownloadable_base_models':base_models,'changed_during_copy':changed,'database':db_status}
 add_bytes(tar,'SNAPSHOT_REMOTE_METADATA.json',json.dumps(metadata).encode())
'''


class SnapshotWriter:
    def __init__(self):
        self.repo=OUT/'repository'
        self.assets=OUT/'assets'
        self.repo.mkdir(parents=True,exist_ok=True);self.assets.mkdir(parents=True,exist_ok=True)
        self.manifest=[];self.excluded=[];self.blobs={};self.zip=None;self.part=0;self.part_bytes=0;self.code_count=0;self.data_count=0;self.syntax_warnings=[]
    def archive_blob(self,digest,payload):
        if digest in self.blobs:return self.blobs[digest]
        if self.zip is None or (self.part_bytes+len(payload)>900*1024**2 and self.part_bytes):
            if self.zip:self.zip.close()
            self.part+=1;self.part_bytes=0
            path=self.assets/f'{ASSET_PREFIX}-{self.part:03d}.zip'
            self.zip=zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED,compresslevel=3,allowZip64=True)
        key='blobs/'+digest[:2]+'/'+digest
        self.zip.writestr(key,payload);self.part_bytes+=len(payload)
        self.blobs[digest]={'asset':f'{ASSET_PREFIX}-{self.part:03d}.zip','blob':key}
        return self.blobs[digest]
    def add(self,rel,payload):
        reason=denied(rel)
        if reason:self.excluded.append({'path':scrub(rel),'reason':reason});return
        suffix=Path(rel).suffix.lower();redacted=False
        if suffix in TEXT_SUFFIX or (not suffix and b'\0' not in payload[:4096]):
            try:text=payload.decode('utf-8-sig')
            except UnicodeDecodeError:
                try:text=payload.decode('gb18030')
                except UnicodeDecodeError:self.excluded.append({'path':rel,'reason':'unrecognized_text_encoding'});return
            if suffix=='.py':
                clean,valid=sanitize_python(text)
                if not valid:
                    self.syntax_warnings.append(rel);rel+='.source.txt'
            else:
                clean=scrub(text)
                # Never run assignment-pattern matching over numeric CSV/SQL bodies.
                # It is unnecessary there and can be very slow on long scientific rows.
                if suffix in {'.json','.jsonl','.geojson'}:
                    def redact_object(value):
                        if isinstance(value,dict):
                            return {k:('REDACTED' if SENSITIVE_NAME.search(k) and isinstance(v,str) and not k.lower().endswith(('_env','_env_var')) else redact_object(v)) for k,v in value.items()}
                        if isinstance(value,list):return [redact_object(v) for v in value]
                        return value
                    try:
                        if suffix=='.jsonl':
                            rebuilt=[];altered=False
                            for line in clean.splitlines():
                                if not line.strip():rebuilt.append(line);continue
                                obj=json.loads(line);new=redact_object(obj);altered|=new!=obj
                                rebuilt.append(json.dumps(new,ensure_ascii=False) if new!=obj else line)
                            if altered:clean='\n'.join(rebuilt)+'\n'
                        else:
                            obj=json.loads(clean);new=redact_object(obj)
                            if obj!=new:clean=json.dumps(new,ensure_ascii=False,indent=2)
                    except (ValueError,RecursionError):pass
                elif suffix in {'.yaml','.yml','.toml','.ini','.env','.example','.conf','.cfg','.txt'}:
                    pattern=r'''(?m)^(\s*["']?([A-Za-z_][A-Za-z0-9_-]{0,120})["']?\s*[:=]\s*)(["']?)([^\r\n]*)$'''
                    clean=re.sub(pattern,lambda m:m[1]+'"REDACTED"' if SENSITIVE_NAME.search(m[2]) else m[0],clean)
                if Path(rel).name=='.env.example':
                    clean=re.sub(r'(?m)^(\s*(?:export\s+)?[A-Za-z_][A-Za-z0-9_]*\s*=).*$',r'\1CHANGE_ME',clean)
            redacted=clean!=text
            payload=clean.encode('utf-8')
        else:
            # Binary artifacts are kept only if no known credential bytes occur.
            if any(len(secret)>=8 and (secret.encode() in payload or secret.encode('utf-16-le') in payload) for secret in SECRETS):
                self.excluded.append({'path':rel,'reason':'credential_marker_in_binary'});return
        # A second known-secret scan is required before any content is staged for upload.
        if any(len(secret)>=8 and secret.encode() in payload for secret in SECRETS):
            self.excluded.append({'path':rel,'reason':'unresolved_credential_marker'});return
        digest=hashlib.sha256(payload).hexdigest()
        parts=set(Path(rel).parts)
        isdata=bool(parts&{'data','storage','reports','artifacts','models','lora_weights','outputs','output','database','qa','reference_materials'})
        iscode=not isdata and (suffix in CODE_SUFFIX or Path(rel).name in {'Dockerfile','.gitignore','.dockerignore','requirements.txt','package.json','package-lock.json'} or ('src' in parts and suffix=='.json') or ('tests' in parts and len(payload)<1024**2)) and len(payload)<20*1024**2
        entry={'path':rel,'bytes':len(payload),'sha256':digest,'redacted':redacted}
        if iscode:
            target=self.repo/rel;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(payload)
            if suffix=='.sh':target.chmod(0o755)
            entry['storage']='git';self.code_count+=1
        else:
            entry.update(self.archive_blob(digest,payload));entry['storage']='release';self.data_count+=1
        self.manifest.append(entry)
        if len(self.manifest)%250==0:emit({'phase':'snapshot_preparation','files':len(self.manifest),'code_files':self.code_count,'data_files':self.data_count,'unique_data_blobs':len(self.blobs)})
    def close(self):
        if self.zip:self.zip.close();self.zip=None


def prepare(token):
    SECRETS.append(token)
    for _,value in profiles():
        SECRETS.extend(str(value[k]) for k in ('hostname','password','username') if k in value)
    writer=SnapshotWriter()
    client=server()
    metadata={}
    try:
        inventory=remote_json(client,INVENTORY)
        roots=[item['root'] for item in inventory]
        if not any(Path(p).name=='epidemic-openclaw' for p in roots):raise RuntimeError('Main server project not found')
        code='ROOTS='+repr(roots)+'\nSKIP_DIRS='+repr(SKIP_DIRS)+'\nSKIP_SUFFIX='+repr(SKIP_SUFFIX)+'\n'+REMOTE_STREAM
        _,stdout,stderr=client.exec_command('python3 -c '+shlex.quote(code),timeout=900)
        emit({'phase':'server_snapshot_stream_started','project_roots':len(roots)})
        with tarfile.open(fileobj=stdout,mode='r|') as tar:
            for member in tar:
                if not member.isfile():continue
                source=tar.extractfile(member)
                if member.name=='SNAPSHOT_REMOTE_METADATA.json':metadata=json.loads(source.read())
                else:writer.add(member.name,source.read())
        if stdout.channel.recv_exit_status()!=0:raise RuntimeError('Server archive stream incomplete')
    finally:
        client.close()
    emit({'phase':'server_snapshot_received','files':len(writer.manifest),'database':metadata.get('database',{}),'changed_files':len(metadata.get('changed_during_copy',[]))})
    for base,dirs,files in os.walk(ROOT):
        dirs[:]=sorted(d for d in dirs if d not in SKIP_DIRS and not d.endswith('.egg-info') and not d.startswith('_') and not (Path(base,d).is_symlink()))
        for name in sorted(files):
            path=Path(base,name)
            if path.is_symlink():continue
            rel='local/'+path.relative_to(ROOT).as_posix()
            reason=denied(rel)
            if reason:
                writer.excluded.append({'path':scrub(rel),'reason':reason});continue
            try:
                before=path.stat();payload=path.read_bytes();after=path.stat()
                if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
                    writer.excluded.append({'path':scrub(rel),'reason':'changed_during_local_copy'});continue
                writer.add(rel,payload)
            except OSError:writer.excluded.append({'path':scrub(rel),'reason':'unreadable'})
    writer.close()
    meta_path=writer.repo/'snapshot'
    meta_path.mkdir(parents=True,exist_ok=True)
    metadata=json.loads(scrub(json.dumps(metadata,ensure_ascii=False)))
    (meta_path/'server-metadata.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
    (meta_path/'manifest.jsonl').write_text(''.join(json.dumps(item,ensure_ascii=False)+'\n' for item in writer.manifest),encoding='utf-8')
    (meta_path/'exclusions.json').write_text(json.dumps(writer.excluded,ensure_ascii=False,indent=2),encoding='utf-8')
    (meta_path/'syntax-reference-files.json').write_text(json.dumps(writer.syntax_warnings,ensure_ascii=False,indent=2),encoding='utf-8')
    assets=[]
    for path in sorted(writer.assets.glob('*.zip')):
        with zipfile.ZipFile(path) as z:
            if z.testzip() is not None:raise RuntimeError('Archive integrity failure')
        with path.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
        assets.append({'name':path.name,'bytes':path.stat().st_size,'sha256':digest})
    (meta_path/'assets.json').write_text(json.dumps(assets,indent=2),encoding='utf-8')
    status={'code_files':writer.code_count,'data_files':writer.data_count,'unique_blobs':len(writer.blobs),'asset_parts':len(assets),'asset_bytes':sum(x['bytes'] for x in assets),'database':metadata.get('database',{}),'changed_during_copy':len(metadata.get('changed_during_copy',[])),'python_reference_only':len(writer.syntax_warnings),'prepared_at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
    (OUT/'prepared.json').write_text(json.dumps(status,indent=2),encoding='utf-8')
    write_snapshot_docs(writer.repo,status)
    emit({'phase':'prepared',**status})
    if status['database'].get('status')!='exported':
        emit({'phase':'requires_database_export_repair_before_complete_publication'})


def write_snapshot_docs(repo,status):
    readme='''# DiseaseX Nexus 私有项目快照

本仓库以 2026-10-26 从服务器读取的项目主体及 Dashboard、来源审核运行目录为主，同时收录本地独有代码和实验资料。原项目工作目录及服务未被修改。

## 内容位置

- `server/epidemic-openclaw/`：服务器现行项目主体源码。
- `server/epidemic-dashboard-runtime/`：现行 Dashboard 源码。
- `server/epidemic-source-review-runtime/`：现行来源审核运行源码。
- `server/aibola/`：服务器侧建模、数据处理与微调相关源码。
- `local/`：本地代码和文档，保留独有实验及历史阶段实现，不自动覆盖服务器版本。
- `snapshot/manifest.jsonl`：全部收入文件的目标路径、SHA-256、脱敏状态和存储位置。
- Releases 中 `snapshot-2026-10-26`：数据、项目微调产物、实验输出及数据库逻辑导出，以去重 blob 方式分卷保存。

代码存入 Git；原始数据、数据库、模型产物不进入 Git 历史，按原项目 AGENTS.md 要求作为私有 Release 附件保存。

## 恢复

先下载本仓库，再将同一 Release 的全部 `*-data-part-*.zip` 放进一个目录。在 Python 3.11 或更高版本运行：

```bash
python restore_snapshot.py --assets /path/to/downloaded-assets --destination /path/to/new-workspace
```

脚本先校验所有附件的 SHA-256，随后将代码和数据恢复到新目录，逐文件验证。目标应使用新建空目录；脚本拒绝覆盖已有文件。

恢复后的业务数据库 SQL 位于 `database/business_database.sql`。在新建测试数据库中按相应迁移与 PostgreSQL 版本恢复；认证账户与运行凭据需自行重新配置。本仓库不会提供实际环境变量、服务器地址、账户密码或密钥。

恢复脚本只重建归档文件，不会启动或部署服务。`USER_HOME`、`SERVER_HOST`、`REDACTED` 等占位符需按新环境配置；数据库内的文件位置和原始材料根目录也需与恢复后的目录对应。

## 快照边界

凭据、实际环境变量、私钥、访问日志、虚拟环境、缓存、未经复核的 UI 截图、历史压缩备份和数据库物理文件不收入。论文 Office/PDF 原件属于出版资料，未作为应用代码及业务数据上传。具体排除清单见 `snapshot/exclusions.json` 和 `snapshot/server-metadata.json`，不能将本仓库描述成操作系统磁盘镜像。

项目自己的微调权重、训练状态及数值模型产物作为附件保留。可重新下载的基础模型不重复上传，其原始文件哈希及恢复线索见 `snapshot/server-metadata.json`。恢复基础模型时须遵守原模型许可并核对哈希。

文本中的检测到的凭据、连接用户信息、服务器地址和用户路径已经占位化。归档过程中采用了语法检查和保守回退，最终合并时保留原始源码文件名；源码检查结果见 `snapshot/source-validation.json`。这份快照完成的是归档与内容校验，不代表执行过完整训练、线上服务启动或预测准确率验证。

数据库逻辑导出使用 pg_dump 的一致性快照，排除认证相关表、所有权和授权信息。运行中的文件在复制前后发生变化时会记录在元数据中，不能宣称这些文件具有跨文件事务一致性。

该系统面向分析人员辅助监测和预测，输出仍需要来源追溯、不确定性说明和人工复核，不保证预测准确或替代医疗判断。
'''
    (repo/'README.md').write_text(readme,encoding='utf-8')
    (repo/'.gitignore').write_text('.recovery/\n*.env\n.env*\n!.env.example\nsecrets/\ncredentials/\n*.pem\n*.key\n*.db\n*.sqlite*\n*.dump\n*.pgcustom\n*.safetensors\n*.pt\n*.pth\n*.joblib\n*.zip\n__pycache__/\n.venv/\n',encoding='utf-8')
    (repo/'snapshot'/'summary.json').write_text(json.dumps(status,indent=2),encoding='utf-8')
    restore=r'''"""Restore a verified private snapshot into a new, empty workspace."""
import argparse,hashlib,json,shutil,zipfile
from pathlib import Path
def main():
 p=argparse.ArgumentParser();p.add_argument('--assets',type=Path,required=True);p.add_argument('--destination',type=Path,required=True);a=p.parse_args()
 root=Path(__file__).resolve().parent;dest=a.destination.resolve()
 if dest.exists() and any(dest.iterdir()):raise SystemExit('Destination must be empty')
 dest.mkdir(parents=True,exist_ok=True)
 metadata=root/'snapshot'
 for asset in json.loads((metadata/'assets.json').read_text()):
  with (a.assets/asset['name']).open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
  if digest!=asset['sha256']:raise SystemExit('Asset hash mismatch')
 archives={}
 try:
  for line in (metadata/'manifest.jsonl').read_text(encoding='utf-8').splitlines():
   item=json.loads(line);target=(dest/item['path']).resolve()
   if not target.is_relative_to(dest):raise SystemExit('Invalid manifest path')
   if target.exists():raise SystemExit('Refusing to overwrite an existing file')
   target.parent.mkdir(parents=True,exist_ok=True)
   if item['storage']=='git':payload=(root/item['path']).read_bytes()
   else:
    name=item['asset']
    if name not in archives:archives[name]=zipfile.ZipFile(a.assets/name)
    payload=archives[name].read(item['blob'])
   if hashlib.sha256(payload).hexdigest()!=item['sha256']:raise SystemExit('File hash mismatch')
   target.write_bytes(payload)
   if target.suffix=='.sh':target.chmod(0o755)
 finally:
  for archive in archives.values():archive.close()
 print('Restored and verified snapshot')
if __name__=='__main__':main()
'''
    (repo/'restore_snapshot.py').write_text(restore,encoding='utf-8')


def run_git(args, cwd, env=None):
    git='C:/Program Files/Git/cmd/git.exe'
    completed=subprocess.run([git,*args],cwd=cwd,env=env,capture_output=True,text=True,timeout=900)
    if completed.returncode:
        # Only a fixed category is reported, never raw stderr or connection values.
        message=completed.stderr.lower()
        category='other'
        for needle,label in [('authentication failed','authentication'),('invalid username','authentication'),('could not read username','askpass'),('cannot spawn','askpass'),('cannot run','askpass'),('certificate','certificate'),('could not resolve host','dns'),('unable to access','network'),('not found','not-found'),('unknown option','arguments'),('destination path','destination')]:
            if needle in message:category=label;break
        raise RuntimeError('Git operation failed: '+args[0]+' ('+category+')')
    return completed.stdout.strip()


def finish_writer(writer,metadata,prior_assets=None):
    writer.close()
    folder=writer.repo/'snapshot';folder.mkdir(parents=True,exist_ok=True)
    metadata=json.loads(scrub(json.dumps(metadata,ensure_ascii=False)))
    (folder/'server-metadata.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
    (folder/'manifest.jsonl').write_text(''.join(json.dumps(v,ensure_ascii=False)+'\n' for v in writer.manifest),encoding='utf-8')
    (folder/'exclusions.json').write_text(json.dumps(writer.excluded,ensure_ascii=False,indent=2),encoding='utf-8')
    (folder/'syntax-reference-files.json').write_text(json.dumps(writer.syntax_warnings,ensure_ascii=False,indent=2),encoding='utf-8')
    assets=list(prior_assets or [])
    for path in sorted(writer.assets.glob('*.zip')):
        with zipfile.ZipFile(path) as z:
            if z.testzip() is not None:raise RuntimeError('Archive integrity check failed')
        digest=hashlib.sha256()
        with path.open('rb') as f:
            for block in iter(lambda:f.read(4*1024**2),b''):digest.update(block)
        assets.append({'name':path.name,'bytes':path.stat().st_size,'sha256':digest.hexdigest()})
    (folder/'assets.json').write_text(json.dumps(assets,indent=2),encoding='utf-8')
    status={'code_files':sum(v['storage']=='git' for v in writer.manifest),'data_files':sum(v['storage']=='release' for v in writer.manifest),'unique_blobs':len(writer.blobs),'asset_parts':len(assets),'asset_bytes':sum(v['bytes'] for v in assets),'database':metadata.get('database',{}),'changed_during_copy':len(metadata.get('changed_during_copy',[])),'python_reference_only':len(writer.syntax_warnings),'prepared_at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
    (OUT/'prepared.json').write_text(json.dumps(status,indent=2),encoding='utf-8')
    write_snapshot_docs(writer.repo,status)
    emit({'phase':'prepared',**status})
    return status


REMOTE_RUNNER = r'''
payload=json.load(sys.stdin)
TOKEN=payload['token'];LOGIN=payload['login'];ROOTS=payload['roots'];SECRETS=payload['secrets']
ASSET_PREFIX='server-data-part';ROOT=Path('/tmp')
OUT=Path('/tmp/diseasex_snapshot_20261026')
if OUT.exists() and not (OUT/'.owned-by-this-snapshot').is_file():raise RuntimeError('Unexpected checkpoint directory')
OUT.mkdir(mode=0o700,exist_ok=True);(OUT/'.owned-by-this-snapshot').touch()
emit({'phase':'server_direct_snapshot_started'})
if payload.get('repair') and (OUT/'prepared.json').exists():
 current=json.loads((OUT/'prepared.json').read_text())
 if current.get('database',{}).get('status')!='exported':recover_database_snapshot()
if not (OUT/'prepared.json').exists():
 writer=SnapshotWriter();base_models=[];changed=[]
 for root in ROOTS:
  alias=Path(root).name
  for here,dirs,files in os.walk(root,followlinks=False):
   dirs[:]=sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith('.') and not os.path.islink(os.path.join(here,d)))
   if os.path.isfile(os.path.join(here,'PG_VERSION')):
    writer.excluded.append({'path':'server/'+alias+'/'+os.path.relpath(here,root),'reason':'active_database_directory_logical_export_instead'});dirs[:]=[];continue
   for name in sorted(files):
    path=Path(here)/name;rel='server/'+alias+'/'+path.relative_to(root).as_posix()
    if path.is_symlink():continue
    if 'models' in Path(rel).parts and path.suffix in {'.safetensors','.bin'}:
     digest=hashlib.sha256()
     with path.open('rb') as f:
      for block in iter(lambda:f.read(4*1024**2),b''):digest.update(block)
     base_models.append({'path':rel,'bytes':path.stat().st_size,'sha256':digest.hexdigest(),'restore':'redownload foundation-model files using included config identifiers and verify hash'});continue
    reason=denied(rel)
    if reason:writer.excluded.append({'path':scrub(rel),'reason':reason});continue
    try:
     before=path.stat();data=path.read_bytes();after=path.stat()
     if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):changed.append(rel);continue
     writer.add(rel,data)
    except OSError:writer.excluded.append({'path':scrub(rel),'reason':'unreadable'})
 metadata={'redownloadable_base_models':base_models,'changed_during_copy':changed}
 main=next(r for r in ROOTS if Path(r).name=='epidemic-openclaw')
 env,bins,urls=pg_environment(main)
 if env and bins and 'psql' in bins and 'pg_dump' in bins:
  try:
   query="SELECT table_schema || '.' || table_name FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog','information_schema') AND table_type='BASE TABLE' ORDER BY 1"
   result=subprocess.run([bins['psql'],'-XAt','-c',query],env=env,capture_output=True,text=True,timeout=45)
   if result.returncode:raise RuntimeError('database_metadata_unavailable')
   names=result.stdout.splitlines();exclude=[v for v in names if re.search(r'(^|[_.])(auth|users|sessions|credentials|secrets|api_keys|tokens)([_.]|$)',v,re.I)]
   cmd=[bins['pg_dump'],'--format=plain','--no-owner','--no-privileges','--no-comments']
   for table in exclude:cmd.append('--exclude-table='+table)
   result=subprocess.run(cmd,env=env,capture_output=True,timeout=300)
   if result.returncode:raise RuntimeError('database_logical_export_failed')
   writer.add('database/business_database.sql',result.stdout)
   metadata['database']={'status':'exported','business_tables':len(names)-len(exclude),'excluded_auth_tables':len(exclude),'bytes':len(result.stdout),'consistent_snapshot':True}
  except Exception as exc:metadata['database']={'status':'failed','error_type':type(exc).__name__}
 else:metadata['database']={'status':'configuration_or_tools_not_found','dsn_found':bool(env),'dump_tool_found':bool(bins and 'pg_dump' in bins),'query_tool_found':bool(bins and 'psql' in bins)}
 status=finish_writer(writer,metadata)
else:
 status=json.loads((OUT/'prepared.json').read_text());emit({'phase':'reuse_server_checkpoint',**status})
if status.get('database',{}).get('status')!='exported':raise RuntimeError('Business database export needs repair')
if status.get('changed_during_copy'):raise RuntimeError('Changed source files need reconciliation')
repo=ensure_repo(TOKEN,LOGIN);commit=publish_code(TOKEN,LOGIN,repo)
release=release_for(TOKEN,LOGIN,commit)
for item in json.loads((OUT/'repository'/'snapshot'/'assets.json').read_text()):upload_asset(TOKEN,release,OUT/'assets'/item['name'],item)
receipt={'phase':'server_source_and_assets_verified','commit':commit,'release_id':release['id'],'server_summary':status,'checkpoint':OUT.name}
(OUT/'SERVER_UPLOADED.json').write_text(json.dumps(receipt,indent=2))
emit(receipt)
'''


def server_direct(token,login,repair=False):
    import inspect
    from recover_snapshot_database import recover_database_snapshot
    text=Path(__file__).read_text(encoding='utf-8');tree=ast.parse(text)
    needed={'scrub','sanitize_python','denied','SnapshotWriter','write_snapshot_docs','github_write','ensure_repo','finish_writer','run_git','publish_code','release_for','upload_asset'}
    pieces=[]
    for node in tree.body:
        if isinstance(node,(ast.FunctionDef,ast.ClassDef)) and node.name in needed:
            source=ast.get_source_segment(text,node)
            if node.name=='run_git':source=source.replace("git='C:/Program Files/Git/cmd/git.exe'","git=shutil.which('git')")
            if node.name=='publish_code':
                source=source.replace("helper=OUT/'github_askpass.cmd'","helper=OUT/'github_askpass.py'")
                lines=source.splitlines()
                for i,line in enumerate(lines):
                    if 'helper.write_text(' in line:
                        lines[i]="    helper.write_text('#!'+sys.executable+'\\nimport os,sys\\nprint(\\\"x-access-token\\\" if \\\"username\\\" in sys.argv[1].lower() else os.environ[\\\"GH_TOKEN\\\"])\\n');helper.chmod(0o700)"
                source='\n'.join(lines)
            pieces.append(source)
    pg_tree=ast.parse(REMOTE_STREAM)
    pg=next(n for n in pg_tree.body if isinstance(n,ast.FunctionDef) and n.name=='pg_environment')
    pieces.append(ast.get_source_segment(REMOTE_STREAM,pg))
    pieces.append(inspect.getsource(github))
    pieces.append(inspect.getsource(recover_database_snapshot))
    imports='import os,sys,json,re,io,ast,tokenize,hashlib,zipfile,subprocess,shutil,datetime,urllib.request,urllib.error,urllib.parse\nfrom pathlib import Path\n'
    constants='\n'.join(k+'='+repr(globals()[k]) for k in ['REPO_NAME','TAG','SKIP_DIRS','SKIP_SUFFIX','CODE_SUFFIX','TEXT_SUFFIX'])+'\n'
    constants+='IP_RE=re.compile('+repr(IP_RE.pattern)+')\nTOKEN_RE=re.compile('+repr(TOKEN_RE.pattern)+')\nSENSITIVE_NAME=re.compile('+repr(SENSITIVE_NAME.pattern)+',re.I)\n'
    emit_source='def emit(value):\n print(json.dumps(value,ensure_ascii=True),flush=True)\n'
    code=imports+constants+emit_source+'\n\n'.join(pieces)+'\ntry:\n'+''.join(' '+line+'\n' for line in REMOTE_RUNNER.splitlines())+"except Exception as exc:\n emit({'phase':'server_direct_failed','error_type':type(exc).__name__,'http_status':getattr(exc,'code',None)});sys.exit(1)\n"
    ast.parse(code)
    client=server()
    try:
        inventory=remote_json(client,INVENTORY);roots=[v['root'] for v in inventory]
        secrets=[token]
        for _,value in profiles():secrets.extend(str(value[k]) for k in ('hostname','username','password') if value.get(k))
        stdin,stdout,stderr=client.exec_command('python3 -u -c '+shlex.quote(code),timeout=1800)
        def drain_errors():
            while stderr.read(65536):pass
        threading.Thread(target=drain_errors,daemon=True).start()
        stdin.write(json.dumps({'token':token,'login':login,'roots':roots,'secrets':secrets,'repair':repair}));stdin.flush();stdin.channel.shutdown_write()
        for line in stdout:
            try:
                message=json.loads(line)
                emit(message)
                if message.get('phase')=='server_source_and_assets_verified':
                    OUT.mkdir(parents=True,exist_ok=True);(OUT/'server_receipt.json').write_text(json.dumps(message,indent=2),encoding='utf-8')
            except json.JSONDecodeError:pass
        status=stdout.channel.recv_exit_status()
        if status:raise RuntimeError('Server-side preparation or publication failed')
    finally:client.close()


def local_prepare(token):
    global OUT
    original=OUT;OUT=OUT/'local'
    try:
        if (OUT/'prepared.json').exists():emit({'phase':'local_checkpoint_ready'});return
        SECRETS.append(token)
        for _,value in profiles():SECRETS.extend(str(value[k]) for k in ('hostname','username','password') if value.get(k))
        writer=SnapshotWriter()
        for base,dirs,files in os.walk(ROOT):
            dirs[:]=sorted(d for d in dirs if d not in SKIP_DIRS and not d.endswith('.egg-info') and not d.startswith('_') and not (Path(base,d).is_symlink()))
            for name in sorted(files):
                path=Path(base,name)
                if path.is_symlink():continue
                rel='local/'+path.relative_to(ROOT).as_posix()
                reason=denied(rel)
                if reason:writer.excluded.append({'path':scrub(rel),'reason':reason});continue
                try:
                    before=path.stat();data=path.read_bytes();after=path.stat()
                    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
                        writer.excluded.append({'path':scrub(rel),'reason':'changed_during_copy'});continue
                    writer.add(rel,data)
                except OSError:writer.excluded.append({'path':scrub(rel),'reason':'unreadable'})
        finish_writer(writer,{'database':{'status':'awaiting_server_snapshot'},'changed_during_copy':[]})
        emit({'phase':'local_snapshot_prepared'})
    finally:OUT=original


def merge_snapshots(token,login):
    receipt=json.loads((OUT/'server_receipt.json').read_text())
    local=OUT/'local'/'repository';folder=OUT/'repository'
    if not (OUT/'local'/'prepared.json').exists():raise RuntimeError('Local snapshot is not ready')
    helper=OUT/'github_askpass.cmd'
    helper.write_text('@"'+sys.executable+'" -c "import os,sys; print(\'x-access-token\' if \'username\' in sys.argv[1].lower() else os.environ[\'GH_TOKEN\'])" %*\n',encoding='utf-8')
    env={**os.environ,'GH_TOKEN':token,'GIT_ASKPASS':helper.as_posix(),'GIT_TERMINAL_PROMPT':'0','GCM_INTERACTIVE':'never'}
    if not (folder/'.git').exists() and not (OUT/'api_base.json').exists():
        if folder.exists() and any(folder.iterdir()):raise RuntimeError('Unexpected local checkout content')
        request=urllib.request.Request('https://api.github.com/repos/'+login+'/'+REPO_NAME+'/tarball/'+receipt['commit'],headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json','User-Agent':'DiseaseX-project-snapshot'})
        folder.mkdir(parents=True,exist_ok=True)
        with urllib.request.urlopen(request,timeout=120) as stream:
            with tarfile.open(fileobj=stream,mode='r|gz') as archive:
                for member in archive:
                    if not member.isfile():continue
                    parts=Path(member.name).parts[1:]
                    target=folder.joinpath(*parts).resolve()
                    if not target.is_relative_to(folder.resolve()):raise RuntimeError('Invalid repository archive path')
                    target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(archive.extractfile(member).read())
        (OUT/'api_base.json').write_text(json.dumps({'commit':receipt['commit']}),encoding='utf-8')
    # Preserve the verified server manifests, then append the independently prepared local side.
    prior=folder/'snapshot'/'server-manifest.jsonl'
    if not prior.exists():shutil.copy2(folder/'snapshot'/'manifest.jsonl',prior)
    prior_assets=folder/'snapshot'/'server-assets.json'
    if not prior_assets.exists():shutil.copy2(folder/'snapshot'/'assets.json',prior_assets)
    shutil.copytree(local/'local',folder/'local',dirs_exist_ok=True)
    manifest=[json.loads(v) for p in [prior,local/'snapshot'/'manifest.jsonl'] for v in p.read_text(encoding='utf-8').splitlines()]
    # A tokenizer fallback is not a reason to change the project import layout.
    references=set(json.loads((folder/'snapshot'/'syntax-reference-files.json').read_text()))
    references.update(json.loads((local/'snapshot'/'syntax-reference-files.json').read_text()))
    for item in manifest:
        if item['path'].endswith('.source.txt') and item['path'].removesuffix('.source.txt') in references:
            previous=item['path'];item['path']=previous.removesuffix('.source.txt')
            if item['storage']=='git':
                old=(folder/previous).resolve();new=(folder/item['path']).resolve()
                if not old.is_relative_to(folder.resolve()) or not new.is_relative_to(folder.resolve()):raise RuntimeError('Invalid source restoration path')
                if old.exists() and not new.exists():old.rename(new)
    # Include the final versions of the backup tools themselves, rather than mid-run copies.
    SECRETS.append(token)
    for _,value in profiles():SECRETS.extend(str(value[k]) for k in ('hostname','username','password') if value.get(k))
    for name in ['publish_project_github.py','recover_snapshot_database.py','audit_github_server_readonly.py']:
        original=(ROOT/'tools'/name).read_text(encoding='utf-8-sig');clean,valid=sanitize_python(original)
        if not valid:raise RuntimeError('Final backup tool source syntax check failed')
        rel='local/tools/'+name;data=clean.encode('utf-8');target=folder/rel;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
        manifest=[v for v in manifest if v['path'] not in {rel,rel+'.source.txt'}]
        manifest.append({'path':rel,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'redacted':clean!=original,'storage':'git'})
    import warnings
    invalid=[];checked=0
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        for item in manifest:
            if item['storage']=='git' and item['path'].endswith('.py'):
                checked+=1
                try:ast.parse((folder/item['path']).read_text(encoding='utf-8-sig'))
                except SyntaxError:invalid.append(item['path'])
    (folder/'snapshot'/'source-validation.json').write_text(json.dumps({'python_sources_checked':checked,'syntax_warning_paths':invalid,'original_names_preserved':True,'functional_tests_run':False},ensure_ascii=False,indent=2),encoding='utf-8')
    (folder/'snapshot'/'manifest.jsonl').write_text(''.join(json.dumps(v,ensure_ascii=False)+'\n' for v in manifest),encoding='utf-8')
    local_meta=local/'snapshot'/'exclusions.json';shutil.copy2(local_meta,folder/'snapshot'/'local-exclusions.json')
    local_syntax=local/'snapshot'/'syntax-reference-files.json';shutil.copy2(local_syntax,folder/'snapshot'/'local-syntax-reference-files.json')
    assets=json.loads(prior_assets.read_text())+json.loads((local/'snapshot'/'assets.json').read_text())
    (folder/'snapshot'/'assets.json').write_text(json.dumps(assets,indent=2),encoding='utf-8')
    server_status=receipt['server_summary']
    status={**server_status,'code_files':sum(v['storage']=='git' for v in manifest),'data_files':sum(v['storage']=='release' for v in manifest),'unique_blobs':len({v['sha256'] for v in manifest if v['storage']=='release'}),'asset_parts':len(assets),'asset_bytes':sum(v['bytes'] for v in assets),'local_snapshot_included':True,'python_reference_only':len(invalid)}
    (OUT/'prepared.json').write_text(json.dumps(status,indent=2),encoding='utf-8')
    write_snapshot_docs(folder,status)
    emit({'phase':'server_local_snapshots_merged',**status})


def publish_code(token,login,repo_info):
    folder=OUT/'repository'
    status=json.loads((OUT/'prepared.json').read_text())
    if status.get('database',{}).get('status')!='exported':raise RuntimeError('Business database export is not ready')
    if status.get('changed_during_copy'):raise RuntimeError('Changed files must be reconciled before publishing')
    if (OUT/'api_base.json').exists():return publish_code_api(token,login,status)
    # Rescan every Git-bound file just before creating the commit.
    manifest=[json.loads(x) for x in (folder/'snapshot'/'manifest.jsonl').read_text(encoding='utf-8').splitlines()]
    for item in manifest:
        if item['storage']=='git':
            data=(folder/item['path']).read_bytes()
            if hashlib.sha256(data).hexdigest()!=item['sha256']:raise RuntimeError('Staged source hash mismatch')
            if TOKEN_RE.search(data.decode('utf-8',errors='ignore')):raise RuntimeError('Credential pattern detected before push')
    helper=OUT/'github_askpass.cmd'
    helper.write_text('@"'+sys.executable+'" -c "import os,sys; print(\'x-access-token\' if \'username\' in sys.argv[1].lower() else os.environ[\'GH_TOKEN\'])" %*\n',encoding='utf-8')
    env={**os.environ,'GH_TOKEN':token,'GIT_ASKPASS':helper.as_posix(),'GIT_TERMINAL_PROMPT':'0','GCM_INTERACTIVE':'never'}
    if not (folder/'.git').exists():run_git(['init','-b','main'],folder)
    run_git(['config','core.autocrlf','false'],folder)
    run_git(['config','credential.helper',''],folder)
    run_git(['config','user.name','Project Snapshot'],folder)
    run_git(['config','user.email','snapshot@users.noreply.github.com'],folder)
    remote='https://github.com/'+login+'/'+REPO_NAME+'.git'
    if run_git(['remote'],folder):run_git(['remote','set-url','origin',remote],folder)
    else:run_git(['remote','add','origin',remote],folder)
    run_git(['add','--force','--all'],folder)
    if run_git(['status','--porcelain'],folder):run_git(['commit','-m','Archive server and local project source with verified data manifests'],folder)
    commit=run_git(['rev-parse','HEAD'],folder)
    run_git(['push','--set-upstream','origin','main'],folder,env)
    branch=github('/repos/'+login+'/'+REPO_NAME+'/branches/main',token)
    if branch['commit']['sha']!=commit:raise RuntimeError('GitHub commit verification failed')
    emit({'phase':'code_pushed','commit':commit,'private':repo_info['private'],'files':status['code_files']})
    return commit


def publish_code_api(token,login,status):
    import base64
    folder=OUT/'repository';prefix='/repos/'+login+'/'+REPO_NAME
    head=github(prefix+'/git/ref/heads/main',token)['object']['sha']
    fingerprint=hashlib.sha256((folder/'snapshot'/'manifest.jsonl').read_bytes()).hexdigest()
    cache=OUT/'source_commit.json'
    if cache.exists():
        saved=json.loads(cache.read_text())
        if saved['commit']==head and saved['manifest_sha256']==fingerprint:
            emit({'phase':'source_commit_already_verified','commit':head});return head
    base=json.loads((OUT/'api_base.json').read_text())['commit']
    if head!=base:raise RuntimeError('Repository main changed outside this snapshot merge')
    commit_info=github(prefix+'/git/commits/'+head,token)
    tree=github(prefix+'/git/trees/'+commit_info['tree']['sha']+'?recursive=1',token)
    if tree.get('truncated'):raise RuntimeError('Server source tree is too large for verification')
    current={v['path']:v['sha'] for v in tree['tree'] if v['type']=='blob'}
    manifest=[json.loads(v) for v in (folder/'snapshot'/'manifest.jsonl').read_text(encoding='utf-8').splitlines()]
    targets={v['path']:v for v in manifest if v['storage']=='git'}
    for path in (folder/'snapshot').glob('*'):
        if path.is_file():targets[path.relative_to(folder).as_posix()]=None
    for name in ['README.md','.gitignore','restore_snapshot.py']:targets[name]=None
    pending=[];base_tree=commit_info['tree']['sha'];batch=[];batch_bytes=0;sent=0
    def flush():
        nonlocal base_tree,batch,batch_bytes,sent
        if not batch:return
        response=github_write(prefix+'/git/trees',token,{'base_tree':base_tree,'tree':batch})
        base_tree=response['sha'];sent+=len(batch)
        emit({'phase':'source_tree_uploaded','changed_entries':sent})
        batch=[];batch_bytes=0
    for rel,item in sorted(targets.items()):
        data=(folder/rel).read_bytes()
        if item and hashlib.sha256(data).hexdigest()!=item['sha256']:raise RuntimeError('Source manifest mismatch before API upload')
        digest=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
        if current.get(rel)==digest:continue
        record={'path':rel,'mode':'100755' if rel.endswith('.sh') else '100644','type':'blob'}
        try:content=data.decode('utf-8')
        except UnicodeDecodeError:content=None
        if content is None or len(data)>512*1024:
            response=github_write(prefix+'/git/blobs',token,{'content':base64.b64encode(data).decode(),'encoding':'base64'})
            if response['sha']!=digest:raise RuntimeError('Uploaded source blob verification failed')
            record['sha']=response['sha']
        else:record['content']=content
        estimated=len(data)+len(rel)+100
        if batch and (batch_bytes+estimated>1500*1024 or len(batch)>=250):flush()
        batch.append(record);batch_bytes+=estimated
    for rel in current:
        if rel.endswith('.source.txt') and rel.removesuffix('.source.txt') in targets:
            batch.append({'path':rel,'mode':'100644','type':'blob','sha':None})
    flush()
    response=github_write(prefix+'/git/commits',token,{'message':'Merge current local source and experiments into verified server snapshot','tree':base_tree,'parents':[head]})
    commit=response['sha']
    github_write(prefix+'/git/refs/heads/main',token,{'sha':commit,'force':False},method='PATCH')
    if github(prefix+'/git/ref/heads/main',token)['object']['sha']!=commit:raise RuntimeError('Main branch update verification failed')
    cache.write_text(json.dumps({'commit':commit,'manifest_sha256':fingerprint}),encoding='utf-8')
    emit({'phase':'code_pushed','commit':commit,'files':status['code_files'],'transport':'GitHub official API'})
    return commit


def release_for(token,login,commit):
    prefix='/repos/'+login+'/'+REPO_NAME
    releases=github(prefix+'/releases?per_page=100',token)
    existing=[r for r in releases if r['tag_name']==TAG]
    if existing:return existing[0]
    return github_write(prefix+'/releases',token,{'tag_name':TAG,'target_commitish':commit,'name':'Server and local complete application snapshot 2026-10-26','draft':True,'body':'Private data, trained artifacts, experiment outputs and consistent business-database export. Verify SHA-256 with snapshot/assets.json; restore with restore_snapshot.py. Credentials, machine environment and redownloadable foundation-model weights are excluded; see README and exclusion manifests.'})


def upload_asset(token,release,path,expected):
    repo_prefix=release['url'].replace('https://api.github.com','')
    for attempt in range(3):
        current=github(repo_prefix+'/assets?per_page=100',token)
        found=[a for a in current if a['name']==path.name]
        if found:
            item=found[0]
            digest=item.get('digest')
            if item['size']==expected['bytes'] and digest=='sha256:'+expected['sha256']:
                emit({'asset':path.name,'status':'already_verified','bytes':item['size']});return item
            if item.get('state')=='uploaded' and item['size']==expected['bytes'] and not digest:
                raise RuntimeError('Uploaded asset does not provide a verifiable digest')
            req=urllib.request.Request(item['url'],method='DELETE',headers={'Authorization':'Bearer '+token,'User-Agent':'DiseaseX-project-snapshot','Accept':'application/vnd.github+json'})
            with urllib.request.urlopen(req,timeout=45):pass
        url=release['upload_url'].split('{')[0]+'?name='+urllib.parse.quote(path.name)
        try:
            emit({'asset':path.name,'status':'uploading','bytes':path.stat().st_size,'attempt':attempt+1})
            with path.open('rb') as f:
                req=urllib.request.Request(url,data=f,method='POST',headers={'Authorization':'Bearer '+token,'User-Agent':'DiseaseX-project-snapshot','Content-Type':'application/zip','Content-Length':str(path.stat().st_size),'Accept':'application/vnd.github+json'})
                with urllib.request.urlopen(req,timeout=180) as response:item=json.load(response)
            if item['size']!=expected['bytes'] or item.get('digest')!='sha256:'+expected['sha256']:raise RuntimeError('Release asset digest mismatch')
            emit({'asset':path.name,'status':'verified','bytes':item['size']});return item
        except (urllib.error.URLError,TimeoutError,ConnectionError,OSError):
            if attempt==2:raise
    raise RuntimeError('Asset upload failed')


def publish(token,login):
    repo_info=ensure_repo(token,login)
    commit=publish_code(token,login,repo_info)
    release=release_for(token,login,commit)
    assets=json.loads((OUT/'repository'/'snapshot'/'assets.json').read_text())
    uploaded=[]
    for item in assets:
        path=OUT/'assets'/item['name']
        if not path.exists() and (OUT/'local'/'assets'/item['name']).exists():path=OUT/'local'/'assets'/item['name']
        uploaded.append(upload_asset(token,release,path,item))
    prefix='/repos/'+login+'/'+REPO_NAME
    release=github_write(prefix+'/releases/'+str(release['id']),token,{'draft':False,'make_latest':'true','target_commitish':commit},method='PATCH')
    tag=github(prefix+'/git/ref/tags/'+TAG,token)
    tag_commit=tag['object']['sha']
    if tag['object']['type']=='tag':tag_commit=github(prefix+'/git/tags/'+tag_commit,token)['object']['sha']
    if tag_commit!=commit:
        prior=json.loads((OUT/'server_receipt.json').read_text())['commit']
        if tag_commit!=prior:raise RuntimeError('Snapshot tag changed outside this upload')
        github_write(prefix+'/git/refs/tags/'+TAG,token,{'sha':commit,'force':False},method='PATCH')
        if github(prefix+'/git/ref/tags/'+TAG,token)['object']['sha']!=commit:raise RuntimeError('Snapshot tag verification failed')
    tree=github(prefix+'/git/trees/'+commit+'?recursive=1',token)
    if tree.get('truncated'):raise RuntimeError('GitHub tree verification was truncated')
    remote_files={v['path']:v['sha'] for v in tree['tree'] if v['type']=='blob'}
    manifest=[json.loads(v) for v in (OUT/'repository'/'snapshot'/'manifest.jsonl').read_text(encoding='utf-8').splitlines()]
    verified_sources=0
    for item in manifest:
        if item['storage']!='git':continue
        data=(OUT/'repository'/item['path']).read_bytes()
        digest=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
        if remote_files.get(item['path'])!=digest:raise RuntimeError('GitHub file coverage or blob verification failed')
        verified_sources+=1
    repo_info=github(prefix,token)
    if not repo_info['private']:raise RuntimeError('Repository visibility verification failed')
    receipt={'status':'complete','repository_url':repo_info['html_url'],'release_url':release['html_url'],'private':True,'commit':commit,'git_source_files_verified':verified_sources,'release_tag_commit_verified':True,'assets':[{'name':a['name'],'bytes':a['size'],'digest':a['digest']} for a in uploaded],'source_summary':json.loads((OUT/'prepared.json').read_text())}
    (OUT/'UPLOAD_VERIFIED.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    emit({'phase':'publication_complete','private':True,'assets':len(uploaded),'commit':commit,'receipt_file':'UPLOAD_VERIFIED.json'})


def auth():
    for token in reversed(discover_tokens()):
        try:
            user = github("/user", token)
            return token, user["login"]
        except Exception:
            continue
    raise RuntimeError("No usable GitHub authentication")


def server():
    settings = dict(profiles())["profilea100"]
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    for p in ROOT.glob("known_hosts*"):
        try:
            client.load_host_keys(str(p))
        except Exception:
            pass
    client.connect(**settings, timeout=15, banner_timeout=15, auth_timeout=15, allow_agent=False, look_for_keys=False)
    return client


def remote_json(client, code):
    _, out, err = client.exec_command("python3 -c " + shlex.quote(code), timeout=120)
    raw = out.read()
    if out.channel.recv_exit_status() != 0:
        raise RuntimeError("Remote inspection failed (details suppressed)")
    return json.loads(raw)


def github_write(path, token, payload, method="POST"):
    req = urllib.request.Request("https://api.github.com" + path, data=json.dumps(payload).encode(), method=method, headers={"Authorization":"Bearer " + token, "Accept":"application/vnd.github+json", "User-Agent":"DiseaseX-project-snapshot", "Content-Type":"application/json"})
    with urllib.request.urlopen(req, timeout=45) as response:
        return json.load(response)


def ensure_repo(token, login):
    try:
        repo = github("/repos/" + login + "/" + REPO_NAME, token)
        if not repo["private"]:
            raise RuntimeError("Existing repository is not private; refusing to reuse")
        emit({"repository":REPO_NAME,"private":True,"already_exists":True})
        return repo
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
    repo = github_write("/user/repos", token, {"name":REPO_NAME,"private":True,"description":"DiseaseX Nexus: private source snapshot with separately archived project data and model artifacts","auto_init":False})
    emit({"repository":REPO_NAME,"private":repo["private"],"created":True})
    return repo


INVENTORY = r'''
import os,json,collections,glob
roots=[]
skip={'.git','.venv','venv','node_modules','__pycache__','.cache','secrets','private','logs','runtime_logs','huggingface','pip','pkgs','envs','postgres-data','redis-data','.minio'}
for base in ['/root','/opt','/srv','/data','/workspace','/home','/mnt','/www']:
 for here,dirs,files in os.walk(base):
  depth=here[len(base):].count(os.sep)
  dirs[:]=[d for d in dirs if d not in skip and not d.startswith('.')]
  if depth>=5: dirs[:]=[]
  if os.path.basename(here) in {'epidemic-openclaw','aibola','epidemic-source-review-runtime','epidemic-dashboard-runtime'}:
   roots.append(here); dirs[:]=[]
result=[]
for root in roots:
 groups=collections.defaultdict(lambda:[0,0]); ext=collections.defaultdict(lambda:[0,0]); largest=[]
 for here,dirs,files in os.walk(root):
  dirs[:]=[d for d in dirs if d not in skip and not d.startswith('.')]
  for name in files:
   p=os.path.join(here,name)
   if os.path.islink(p):continue
   try: n=os.path.getsize(p)
   except OSError:continue
   rel=os.path.relpath(p,root); group=rel.split(os.sep)[0] if os.sep in rel else '[root]'; suffix=os.path.splitext(name)[1].lower()
   groups[group][0]+=1;groups[group][1]+=n;ext[suffix][0]+=1;ext[suffix][1]+=n
   if n>50*1024**2:largest.append({'category':group,'extension':suffix,'bytes':n})
 result.append({'root':root,'name':os.path.basename(root),'groups':dict(groups),'extensions':dict(ext),'large_files':largest})
print(json.dumps(result))
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["inspect", "initialize", "prepare", "publish", "progress", "stop-export", "server-direct", "server-repair", "local-prepare", "merge", "db-inspect"])
    args = parser.parse_args()
    if args.stage=='db-inspect':
        client=server()
        probe=r'''
import os,json,re,subprocess,glob
result={'root_access':os.geteuid()==0,'project_processes':[],'service_units':[],'project_config_candidates':[]}
result['postgres_tools']=[]
result['postgres_process_count']=0
for pid in os.listdir('/proc'):
 if not pid.isdigit() or int(pid)==os.getpid():continue
 try:
  if open('/proc/'+pid+'/comm').read().strip()=='postgres':result['postgres_process_count']+=1
  cmd=open('/proc/'+pid+'/cmdline','rb').read()
  if not any(x in cmd.lower() for x in [b'epidemic',b'postgres',b'uvicorn']):continue
  env=open('/proc/'+pid+'/environ','rb').read()
  keys=[e.split(b'=')[0].decode(errors='replace') for e in env.split(b'\0') if b'=' in e and any(k in e.split(b'=')[0] for k in [b'DATABASE',b'PGHOST',b'PGPORT',b'PGUSER',b'PGDATABASE'])]
  result['project_processes'].append({'postgres':b'postgres' in cmd.lower(),'database_env_keys':keys})
  if b'postgres' in cmd.lower():
   exe=os.readlink('/proc/'+pid+'/exe');bindir=os.path.dirname(exe)
   result['postgres_tools'].append({'psql':os.path.isfile(bindir+'/psql'),'pg_dump':os.path.isfile(bindir+'/pg_dump')})
 except OSError:pass
p=subprocess.run(['systemctl','list-units','--all','--type=service','--no-legend','--no-pager'],capture_output=True,text=True)
units=[]
for line in p.stdout.splitlines():
 fields=line.split()
 for field in fields:
  if field.endswith('.service') and any(k in field.lower() for k in ['epidemic','postgres','openclaw']):units.append(field)
for unit in units:
 p=subprocess.run(['systemctl','show',unit,'--property=EnvironmentFiles','--value'],capture_output=True,text=True)
 paths=re.findall(r'(/[^\s;]+)',p.stdout)
 fields=[]
 for path in paths:
  try:
   text=open(path).read()
   fields.extend(re.findall(r'^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=',text,re.M))
  except OSError:pass
 result['service_units'].append({'environment_files':len(paths),'database_keys':[k for k in fields if any(x in k for x in ['DATABASE','PGHOST','PGPORT','PGUSER','PGDATABASE'])]})
for root in ROOTS:
 if os.path.basename(root)!='epidemic-openclaw':continue
 for category in ['secrets','private','credentials','config','configs']:
  count=0;urls=0
  for here,dirs,files in os.walk(root+'/'+category):
   if here[len(root):].count(os.sep)>5:dirs[:]=[]
   for name in files:
    p=here+'/'+name
    try:
     if os.path.getsize(p)>1024**2:continue
     text=open(p,encoding='utf-8',errors='replace').read();count+=1
     urls+=bool(re.search(r'postgresql(?:\+psycopg)?://',text))
    except OSError:pass
  result['project_config_candidates'].append({'category':category,'readable_small_files':count,'files_containing_database_urls':urls})
 tools=set();clusters=[];dumps=[]
 for here,dirs,files in os.walk(root):
  dirs[:]=[d for d in dirs if d not in {'.git','node_modules','__pycache__','.cache'}]
  for name in ['psql','pg_dump','pg_restore','pg_ctl','postgres','initdb']:
   if name in files:tools.add(name)
  if 'PG_VERSION' in files:
   try:clusters.append({'version':open(here+'/PG_VERSION').read().strip(),'readable':os.access(here,os.R_OK),'pid_file':os.path.isfile(here+'/postmaster.pid')})
   except OSError:pass
   dirs[:]=[]
  for name in files:
   if name.endswith(('.dump','.pgcustom')):
    try:dumps.append({'bytes':os.path.getsize(here+'/'+name),'mtime':os.path.getmtime(here+'/'+name)})
    except OSError:pass
 result['available_tool_names']=sorted(tools);result['database_clusters']=clusters;result['logical_backups']=sorted(dumps,key=lambda x:-x['mtime'])[:3]
print(json.dumps(result))
'''
        probe=probe.replace('print(json.dumps(result))',r'''
import platform,shutil
result['operating_system']=platform.system();result['architecture']=platform.machine()
system_tools=[]
for pattern in ['/usr/lib/postgresql/*/bin','/usr/local/pgsql/bin','/opt/postgres*/bin','/opt/pgsql*/bin']:
 for directory in glob.glob(pattern):
  present=[n for n in ['pg_dump','psql','pg_restore','postgres','pg_ctl'] if os.path.isfile(directory+'/'+n)]
  if present:system_tools.append({'tools':present,'executable':all(os.access(directory+'/'+n,os.X_OK) for n in present)})
result['system_postgres_tools']=system_tools
print(json.dumps(result))
''')
        try:
            inventory=remote_json(client,INVENTORY)
            emit({'database_configuration_probe':remote_json(client,'ROOTS='+repr([v['root'] for v in inventory])+'\n'+probe)})
        finally:client.close()
        return
    if args.stage in {'progress','stop-export'}:
        client=server()
        code=r'''
import os,json
items=[]
for pid in os.listdir('/proc'):
 if not pid.isdigit() or int(pid)==os.getpid():continue
 try:
  if not open('/proc/'+pid+'/comm').read().strip().startswith('python'):continue
  cmd=open('/proc/'+pid+'/cmdline','rb').read()
  legacy=b'SNAPSHOT_REMOTE_METADATA.json' in cmd and b'base_models=[]' in cmd
  direct=b'server_direct_snapshot_started' in cmd and b'diseasex_snapshot_20261026' in cmd
  if not (legacy or direct):continue
  counters={}
  for line in open('/proc/'+pid+'/io'):
   key,value=line.split(':');counters[key]=int(value)
  opened=[]
  for fd in os.listdir('/proc/'+pid+'/fd'):
   target=os.readlink('/proc/'+pid+'/fd/'+fd)
   if not os.path.isfile(target):continue
   pos=0
   for line in open('/proc/'+pid+'/fdinfo/'+fd):
    if line.startswith('pos:'):pos=int(line.split(':')[1])
   opened.append({'extension':os.path.splitext(target)[1],'size':os.path.getsize(target),'offset':pos})
  items.append({'read_gib':round(counters['rchar']/1024**3,2),'stream_gib':round(counters['wchar']/1024**3,2),'open_files':opened})
 except OSError:pass
checkpoint='/tmp/diseasex_snapshot_20261026'
assets=[]
if os.path.isdir(checkpoint+'/assets'):
 for name in os.listdir(checkpoint+'/assets'):
  p=checkpoint+'/assets/'+name
  if os.path.isfile(p):assets.append({'name':name,'bytes':os.path.getsize(p)})
state={}
for name in ['prepared.json','SERVER_UPLOADED.json']:
 p=checkpoint+'/'+name
 if os.path.isfile(p):
  try:state[name]=json.load(open(p))
  except ValueError:pass
print(json.dumps({'processes':items,'server_assets':assets,'checkpoint_state':state}))
'''
        if args.stage=='stop-export':
            code=code.replace("items.append({'read_gib'", "os.kill(int(pid),15);items.append({'read_gib'")
        try:emit({'server_stream_progress':remote_json(client,code)})
        finally:client.close()
        return
    token, login = auth()
    emit({"authentication": "valid", "target_repository": REPO_NAME, "visibility": "private"})
    if args.stage == "initialize":
        repo = ensure_repo(token, login)
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "repository_status.json").write_text(json.dumps({"name":REPO_NAME,"private":repo["private"],"html_url":repo["html_url"]},indent=2),encoding="utf-8")
        return
    if args.stage == "prepare":
        prepare(token)
        return
    if args.stage == "publish":
        publish(token,login)
        return
    if args.stage == "server-direct":
        server_direct(token,login)
        return
    if args.stage == "server-repair":
        server_direct(token,login,repair=True)
        return
    if args.stage == "local-prepare":
        local_prepare(token)
        return
    if args.stage == "merge":
        merge_snapshots(token,login)
        return
    client = server()
    try:
        inventory = remote_json(client, INVENTORY)
        for item in inventory:
            emit({k:v for k,v in item.items() if k != "root"})
    finally:
        client.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        reason=str(exc) if isinstance(exc,RuntimeError) and re.fullmatch(r'[A-Za-z0-9 :;_().-]{1,180}',str(exc)) else None
        emit({"status": "failed", "error_type": type(exc).__name__, "reason":reason,"http_status": getattr(exc,"code",None), "reason_type": type(getattr(exc,"reason",None)).__name__, "reason_errno":getattr(getattr(exc,"reason",None),"errno",None)})
        sys.exit(1)
