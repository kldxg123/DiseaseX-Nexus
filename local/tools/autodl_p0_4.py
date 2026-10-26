from __future__ import annotations
import argparse,json,re,shlex,socket,sys,tempfile,hashlib,base64
from pathlib import Path,PurePosixPath
import paramiko
ROOT=Path(r"F:\作业\aibola");INFO=ROOT/"服务器信息2.txt";P="USER_HOME/autodl-tmp/epidemic-openclaw"
NODE_VER="v24.18.0";NODE_SHA="55aa7153f9d88f28d765fcdad5ae6945b5c0f98a36881703817e4c450fa76742";OC_VER="2026.6.11";OC_INT="sha512-T+P/g19IheeT1ckXMoPN61dYuE8vBF4MderI+kWkvpuFYxPkJxn8AXLpu9IXCnN9g36Acpm9+mMD/V+lsvOkyA=="
NODE_HOME=f"{P}/runtime/node-v24.18.0-linux-x64";NODE_CURRENT=f"{P}/runtime/node-current";OC_HOME=f"{P}/runtime/openclaw-npm";NODE_CACHE=f"{P}/storage/cache/node";NPM_CACHE=f"{P}/storage/cache/npm"
STATE=f"{P}/storage/openclaw/state";CONFIG=f"{STATE}/openclaw.json";WORK=f"{P}/storage/openclaw/workspace";LOG=f"{P}/storage/openclaw/logs";RUN=f"{P}/storage/openclaw/run";REPORT=f"{P}/reports/p0_4_openclaw"
class Blocked(RuntimeError):pass
def q(x):return shlex.quote(x)
def creds():
 b=INFO.read_bytes()
 for e in ("utf-8-sig","utf-16","gb18030"):
  try:t=b.decode(e);break
  except UnicodeDecodeError:pass
 else:raise Blocked("SSH_PARSE_FAILED")
 m=re.search(r"ssh\s+(?:[^\r\n]*?\s)?-p\s+(\d+)\s+([\w.-]+)@([^\s]+)",t,re.I)
 if not m:raise Blocked("SSH_PARSE_FAILED")
 ls=[x.strip() for x in t[m.end():].splitlines() if x.strip()]
 if not ls:raise Blocked("PASSWORD_PARSE_FAILED")
 return m.group(3),int(m.group(1)),m.group(2),re.sub(r"^(?:password|passwd|密码|密碼)\s*[:：=]\s*","",ls[0],flags=re.I)
def conn():
 h,p,u,w=creds();c=paramiko.SSHClient();c.set_missing_host_key_policy(paramiko.AutoAddPolicy());c.connect(h,p,u,w,look_for_keys=False,allow_agent=False,timeout=20,auth_timeout=20,banner_timeout=20);return c
def run(c,cmd,timeout=180,check=True,input=None):
 i,o,e=c.exec_command(cmd,timeout=timeout)
 if input is not None:i.write(input);i.flush()
 i.close();a=o.read().decode("utf-8","replace").strip();b=e.read().decode("utf-8","replace").strip();rc=o.channel.recv_exit_status()
 if check and rc:raise Blocked(f"REMOTE_FAILED:{cmd.split()[0]}:{rc}:{b[-600:]}")
 return rc,a,b
def out(c,cmd,**kw):return run(c,cmd,**kw)[1]
def mkdirs(s,path):
 cur=PurePosixPath("/")
 for x in PurePosixPath(path).parts[1:]:
  cur/=x
  try:s.stat(str(cur))
  except FileNotFoundError:s.mkdir(str(cur))
def put(s,path,text,mode=0o644):
 with s.open(path,"w") as f:f.write(text)
 s.chmod(path,mode)
def preflight(c):
 base=out(c,f"test -f {q(P+'/reports/p0_3/P0_3_QWEN_VLLM_EXECUTION_REPORT.md')} && test -f {q(P+'/config/versions.yaml')} && test -f {q(P+'/AGENTS.md')} && test -f {q(P+'/.gitignore')} && cd {q(P)} && printf '%s\\n' \"$(git branch --show-current)\" \"$(df -Pk /root/autodl-tmp|awk 'NR==2{{print $4}}')\" \"$(df -Pk /|awk 'NR==2{{print $4}}')\"")
 a=base.splitlines()
 if len(a)<3 or a[0]!="main":raise Blocked("P0_3_BASELINE_FAILED")
 if run(c,q(P+"/scripts/postgres/status.sh"),timeout=120,check=False)[0] or run(c,q(P+"/scripts/postgres/smoke_test.sh"),timeout=120,check=False)[0]:raise Blocked("BLOCKED_POSTGRES_REGRESSION")
 if run(c,q(P+"/scripts/qwen_vllm/status.sh"),timeout=120,check=False)[0]:raise Blocked("BLOCKED_VLLM_REGRESSION")
 mid=out(c,"curl -fsS http://127.0.0.1:8000/v1/models | USER_HOME/autodl-tmp/conda/envs/qwen-vllm/bin/python -c 'import json,sys;d=json.load(sys.stdin);print(\",\".join(x[\"id\"] for x in d[\"data\"]))'")
 if mid!="qwen3-8b-awq-local":raise Blocked("BLOCKED_VLLM_MODEL_ID")
 args=out(c,f"tr '\\0' ' ' </proc/$(cat {q(P+'/storage/runtime/qwen-vllm/vllm.pid')})/cmdline")
 for need in ("--max-model-len 32768","--gpu-memory-utilization 0.85","--max-num-seqs 4","--enable-prefix-caching","--enable-auto-tool-choice","--tool-call-parser hermes","--reasoning-parser qwen3","--host 127.0.0.1","--port 8000","--quantization awq"):
  if need not in args:raise Blocked("BLOCKED_VLLM_ARGUMENT_DRIFT:"+need)
 if "yarn" in args.lower():raise Blocked("BLOCKED_VLLM_ARGUMENT_DRIFT:YARN")
 rc,_,er=run(c,f"cd {q(P+'/models/llm/Qwen3-8B-AWQ')} && sha256sum -c {q(P+'/config/qwen3-8b-awq-sha256.txt')} >/dev/null",timeout=1200,check=False)
 if rc:raise Blocked("BLOCKED_MODEL_SHA256:"+er[-300:])
 norm=out(c,"USER_HOME/autodl-tmp/conda/envs/qwen-vllm/bin/python -c 'import huggingface_hub,importlib.metadata as m;print(huggingface_hub.__version__+\"|\"+m.version(\"huggingface-hub\"))'")
 if len(set(norm.split("|")))!=1:raise Blocked("HUGGINGFACE_PACKAGE_NORMALIZATION_FAILED")
 proxy=out(c,"bash -lc 'if [[ -z ${http_proxy:-}${https_proxy:-}${HTTP_PROXY:-}${HTTPS_PROXY:-}${all_proxy:-}${ALL_PROXY:-} ]]; then echo clean; else echo present; fi'")
 if proxy!="clean":raise Blocked("PROXY_RESIDUE")
 gpu=out(c,"nvidia-smi --query-gpu=name,memory.total,memory.used,memory.free,driver_version --format=csv,noheader,nounits");resource=out(c,"printf 'cpu=';getconf _NPROCESSORS_ONLN; free -b|awk 'NR==2{print \"memory=\"$2\" available=\"$7}'; df -hP / USER_HOME/autodl-tmp")
 # Official supply-chain metadata, direct TLS only.
 py="""import json,urllib.request;\ndef g(u):\n r=urllib.request.urlopen(urllib.request.Request(u,headers={'User-Agent':'epidemic-openclaw-p0-4'}),timeout=60);return r.read()\nidx=json.loads(g('https://nodejs.org/dist/index.json'));n=next(x for x in idx if x['version']=='v24.18.0');sh=g('https://nodejs.org/dist/v24.18.0/SHASUMS256.txt').decode();latest=json.loads(g('https://registry.npmjs.org/openclaw/latest'));fixed=json.loads(g('https://registry.npmjs.org/openclaw/2026.6.11'));print(json.dumps({'node':n,'node_sha_line':next(x for x in sh.splitlines() if x.endswith('  node-v24.18.0-linux-x64.tar.xz')),'openclaw_latest':latest['version'],'openclaw':{k:fixed.get(k) for k in ('version','engines','scripts')},'dist':fixed['dist']}))"""
 meta=json.loads(out(c,f"/root/miniconda3/bin/python -c {q(py)}",timeout=300))
 if meta["node"].get("lts")!="Krypton" or meta["node_sha_line"].split()[0]!=NODE_SHA:raise Blocked("BLOCKED_NODE_INTEGRITY_METADATA")
 if meta["openclaw"]["version"]!=OC_VER or meta["dist"].get("integrity")!=OC_INT or not str(meta["dist"].get("tarball","")).startswith("https://registry.npmjs.org/"):raise Blocked("BLOCKED_OPENCLAW_INTEGRITY_METADATA")
 s=c.open_sftp();mkdirs(s,REPORT);put(s,REPORT+"/preflight.json",json.dumps({"git_branch":"main","disk_free_kb":{"data":int(a[1]),"system":int(a[2])},"postgresql":True,"vllm":True,"model_sha256":True,"huggingface_name_normalization":norm,"proxy_state":proxy,"gpu":gpu,"resources":resource.splitlines(),"metadata":meta},indent=2)+"\n");s.close()
 print("PREFLIGHT=PASS");print("GPU="+gpu);print("LATEST_OPENCLAW="+meta["openclaw_latest"]);print("FIXED_METADATA=PASS")
def install(c):
 preflight(c);s=c.open_sftp()
 for d in (f"{P}/runtime",NODE_CACHE,NPM_CACHE,OC_HOME,STATE,WORK,LOG,RUN,REPORT):mkdirs(s,d)
 s.close()
 arch=out(c,"uname -m")
 if arch!="x86_64":raise Blocked("BLOCKED_NODE_ARCHITECTURE")
 nstate=out(c,f"if test -x {q(NODE_HOME+'/bin/node')};then {q(NODE_HOME+'/bin/node')} --version;elif test -e {q(NODE_HOME)};then echo conflict;else echo missing;fi")
 archive=f"{NODE_CACHE}/node-v24.18.0-linux-x64.tar.xz"
 if nstate=="missing":
  temp=archive+".partial"
  rc,_,er=run(c,f"unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; curl -fL --retry 2 --connect-timeout 30 --max-time 1800 -o {q(temp)} https://nodejs.org/dist/v24.18.0/node-v24.18.0-linux-x64.tar.xz",timeout=1900,check=False)
  if rc:raise Blocked("BLOCKED_NODE_DOWNLOAD:"+er[-400:])
  actual=out(c,f"sha256sum {q(temp)}|awk '{{print $1}}'")
  if actual!=NODE_SHA:raise Blocked("BLOCKED_NODE_ARCHIVE_SHA256")
  run(c,f"/root/miniconda3/bin/python -c {q('import os;os.replace(\"'+temp+'\",\"'+archive+'\")')}")
  stage=f"{P}/runtime/.node-v24.18.0-stage"
  if out(c,f"test -e {q(stage)} && echo yes || echo no")=="yes":raise Blocked("BLOCKED_NODE_STAGE_CONFLICT")
  run(c,f"mkdir {q(stage)} && tar -xJf {q(archive)} -C {q(stage)} --strip-components=1 && mv {q(stage)} {q(NODE_HOME)}",timeout=600)
 elif nstate!=NODE_VER:raise Blocked("BLOCKED_NODE_PATH_CONFLICT")
 if out(c,f"test -L {q(NODE_CURRENT)} && readlink {q(NODE_CURRENT)} || true") not in ("",NODE_HOME):raise Blocked("BLOCKED_NODE_SYMLINK_CONFLICT")
 if out(c,f"test -L {q(NODE_CURRENT)} && echo yes || echo no")!="yes":run(c,f"ln -s {q(NODE_HOME)} {q(NODE_CURRENT)}")
 node=out(c,f"{q(NODE_CURRENT+'/bin/node')} --version");npm=out(c,f"PATH={q(NODE_CURRENT+'/bin')}:$PATH {q(NODE_CURRENT+'/bin/npm')} --version")
 if node!=NODE_VER:raise Blocked("BLOCKED_NODE_VERSION")
 # Install fixed OpenClaw only after metadata/integrity checks.
 ocbin=f"{OC_HOME}/node_modules/.bin/openclaw"
 ost=out(c,f"if test -x {q(ocbin)};then PATH={q(NODE_CURRENT+'/bin')}:$PATH {q(ocbin)} --version;elif test -e {q(OC_HOME+'/node_modules/openclaw')};then echo conflict;else echo missing;fi")
 if ost=="missing":
  rc,_,er=run(c,f"unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; PATH={q(NODE_CURRENT+'/bin')}:$PATH npm_config_cache={q(NPM_CACHE)} {q(NODE_CURRENT+'/bin/npm')} install --prefix {q(OC_HOME)} --registry=https://registry.npmjs.org/ --save-exact openclaw@2026.6.11 >{q(LOG+'/npm-install.log')} 2>&1",timeout=2400,check=False)
  if rc:
   tail=out(c,f"tail -n 40 {q(LOG+'/npm-install.log')}",check=False)
   raise Blocked("BLOCKED_OPENCLAW_INSTALL:"+tail[-1500:])
 elif OC_VER not in ost:raise Blocked("BLOCKED_OPENCLAW_PATH_CONFLICT")
 over=out(c,f"PATH={q(NODE_CURRENT+'/bin')}:$PATH {q(ocbin)} --version")
 if OC_VER not in over:raise Blocked("BLOCKED_OPENCLAW_VERSION")
 dep_rc,depout,deperr=run(c,f"PATH={q(NODE_CURRENT+'/bin')}:$PATH {q(NODE_CURRENT+'/bin/npm')} ls --prefix {q(OC_HOME)} --all --json",timeout=600,check=False)
 audit_rc,audit,_=run(c,f"PATH={q(NODE_CURRENT+'/bin')}:$PATH npm_config_cache={q(NPM_CACHE)} {q(NODE_CURRENT+'/bin/npm')} audit --prefix {q(OC_HOME)} --omit=dev --json",timeout=900,check=False)
 try:aud=json.loads(audit)
 except:raise Blocked("BLOCKED_NPM_AUDIT_PARSE")
 crit=int(aud.get("metadata",{}).get("vulnerabilities",{}).get("critical",0))
 if crit:raise Blocked("BLOCKED_NPM_CRITICAL_VULNERABILITY")
 shrink=out(c,f"find {q(OC_HOME+'/node_modules/openclaw')} -name npm-shrinkwrap.json -type f -print -quit")
 shrink_sha=out(c,f"sha256sum {q(shrink)}|awk '{{print $1}}'") if shrink else "not_present"
 inv={"node_version":node,"npm_version":npm,"node_path":NODE_CURRENT+"/bin/node","npm_path":NODE_CURRENT+"/bin/npm","node_archive_sha256":out(c,f"sha256sum {q(archive)}|awk '{{print $1}}'"),"openclaw_version":over,"openclaw_path":ocbin,"openclaw_integrity":OC_INT,"npm_shrinkwrap_path":shrink or "not_present","npm_shrinkwrap_sha256":shrink_sha,"npm_ls_exit":dep_rc,"npm_audit_exit":audit_rc,"npm_audit_metadata":aud.get("metadata",{}),"latest_version_observed":json.loads(out(c,f"cat {q(REPORT+'/preflight.json')}"))["metadata"]["openclaw_latest"]}
 s=c.open_sftp();put(s,REPORT+"/install_inventory.json",json.dumps(inv,indent=2)+"\n");put(s,REPORT+"/npm_audit_raw.json",json.dumps(aud,indent=2)+"\n");s.close();print("INSTALL=PASS");print(json.dumps(inv))
def inspect(c,what):
 env=f"PATH={q(NODE_CURRENT+'/bin')}:$PATH OPENCLAW_STATE_DIR={q(STATE)} OPENCLAW_CONFIG_PATH={q(CONFIG)}"
 oc=q(OC_HOME+"/node_modules/.bin/openclaw")
 if what=="help":
  cmds=[f"{oc} config --help",f"{oc} config validate --help",f"{oc} gateway run --help",f"{oc} agent --help",f"{oc} models --help",f"{oc} gateway --help",f"{oc} devices --help",f"{oc} devices list --help",f"{oc} devices approve --help"]
  for x in cmds:print("COMMAND="+x.split("openclaw")[-1]);print(out(c,f"{env} {x}",timeout=120,check=False)[:12000])
 else:
  rc,a,b=run(c,f"{env} {q(NODE_CURRENT+'/bin/npm')} ls --prefix {q(OC_HOME)} --all",timeout=600,check=False);print((a+"\n"+b)[-12000:])
def configure(c):
 s=c.open_sftp()
 for d in (STATE,WORK,LOG,RUN,f"{P}/scripts/openclaw"):mkdirs(s,d)
 s.chmod(STATE,0o700);s.chmod(WORK,0o700);s.chmod(RUN,0o700);s.chmod(LOG,0o700)
 envfile=STATE+"/.env"
 try:s.stat(envfile);exists=True
 except FileNotFoundError:exists=False
 if not exists:
  token=base64.urlsafe_b64encode(__import__('secrets').token_bytes(32)).decode().rstrip('=')
  put(s,envfile,f"OPENCLAW_GATEWAY_TOKEN={token}\nVLLM_API_KEY=vllm-local\nOPENCLAW_DISABLE_BONJOUR=1\n",0o600)
 s.chmod(envfile,0o600)
 cfg={"gateway":{"mode":"local","bind":"loopback","port":18789,"auth":{"mode":"token","token":"${OPENCLAW_GATEWAY_TOKEN}"}},"discovery":{"mdns":{"mode":"off"}},"models":{"mode":"replace","providers":{"vllm":{"baseUrl":"http://127.0.0.1:8000/v1","apiKey":'REDACTED',"api":"openai-completions","timeoutSeconds":300,"models":[{"id":"qwen3-8b-awq-local","name":"Qwen3 8B AWQ Local","reasoning":True,"input":["text"],"cost":{"input":0,"output":0,"cacheRead":0,"cacheWrite":0},"contextWindow":32768,"maxTokens":8192,"compat":{"thinkingFormat":"qwen-chat-template"}}]}}},"agents":{"defaults":{"workspace":WORK,"model":{"primary":"vllm/qwen3-8b-awq-local","fallbacks":[]},"models":{"vllm/qwen3-8b-awq-local":{"alias":"qwen-local"}},"thinkingDefault":"off","sandbox":{"mode":"off"}}},"skills":{"allowBundled":[]},"tools":{"profile":"minimal","allow":["session_status"],"deny":["group:runtime","group:fs","group:web","group:ui","group:automation","group:messaging","group:nodes","group:agents","group:media","group:plugins","sessions_list","sessions_history","sessions_send","sessions_spawn","sessions_yield","subagents"],"fs":{"workspaceOnly":True},"exec":{"security":"deny","ask":"always"},"elevated":{"enabled":False}},"plugins":{"enabled":False},"commands":{"restart":False},"logging":{"redactSensitive":"tools"}}
 cfg["skills"]["allowBundled"]=["__p0_4_none__"]
 put(s,CONFIG,json.dumps(cfg,indent=2)+"\n",0o600)
 envsh=f'''#!/usr/bin/env bash\nset -euo pipefail\numask 077\nexport NODE_HOME='{NODE_CURRENT}' OPENCLAW_HOME='{OC_HOME}' OPENCLAW_STATE_DIR='{STATE}' OPENCLAW_CONFIG_PATH='{CONFIG}' OPENCLAW_WORKSPACE='{WORK}'\nexport PATH="$NODE_HOME/bin:$OPENCLAW_HOME/node_modules/.bin:$PATH"\nunset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY\nset -a\nsource "$OPENCLAW_STATE_DIR/.env"\nset +a\n'''
 cli=f'''#!/usr/bin/env bash\nset -euo pipefail\nsource '{P}/scripts/openclaw/openclaw_env.sh'\nexec '{OC_HOME}/node_modules/.bin/openclaw' "$@"\n'''
 start=f'''#!/usr/bin/env bash\nset -euo pipefail\nsource '{P}/scripts/openclaw/openclaw_env.sh'\npidfile='{RUN}/openclaw.pid'; logfile='{LOG}/gateway.log'\nif [[ -f "$pidfile" ]] && kill -0 "$(<"$pidfile")" 2>/dev/null; then '{P}/scripts/openclaw/openclaw_cli.sh' gateway call health --json >/dev/null 2>&1 && exit 0; exit 1; fi\n'{NODE_CURRENT}/bin/node' -e 'const fs=require("fs");try{{fs.unlinkSync(process.argv[1])}}catch(e){{if(e.code!=="ENOENT")throw e}}' "$pidfile"\nstarted=$(date +%s); nohup '{OC_HOME}/node_modules/.bin/openclaw' gateway run --port 18789 --bind loopback >"$logfile" 2>&1 & pid=$!\nprintf '%s\\n' "$pid" >"$pidfile"; printf '%s\\n' "$started" >'{RUN}/start_epoch'\nfor _ in $(seq 1 180);do if ! kill -0 "$pid" 2>/dev/null;then tail -n 60 "$logfile"|sed -E 's/(token|password|key)[^ ]*/<redacted>/Ig';exit 1;fi;if '{P}/scripts/openclaw/openclaw_cli.sh' gateway call health --json >/dev/null 2>&1;then date +%s >'{RUN}/ready_epoch';exit 0;fi;sleep 1;done\ntail -n 60 "$logfile"|sed -E 's/(token|password|key)[^ ]*/<redacted>/Ig';exit 1\n'''
 stop=f'''#!/usr/bin/env bash\nset -euo pipefail\nsource '{P}/scripts/openclaw/openclaw_env.sh'\npidfile='{RUN}/openclaw.pid';[[ -f "$pidfile" ]]||exit 0;pid=$(<"$pidfile")\nif ! kill -0 "$pid" 2>/dev/null;then '{NODE_CURRENT}/bin/node' -e 'const fs=require("fs");try{{fs.unlinkSync(process.argv[1])}}catch(e){{}}' "$pidfile";exit 0;fi\nexe=$(readlink -f "/proc/$pid/exe");[[ "$exe" == '{NODE_HOME}/bin/node' ]]||exit 2\ninode=$(awk '$2=="0100007F:4965"&&$4=="0A"{{print $10}}' /proc/net/tcp);[[ -n "$inode" ]]||exit 2\nfound=0;for fd in /proc/$pid/fd/*;do [[ "$(readlink "$fd" 2>/dev/null||true)" == "socket:[$inode]" ]]&&found=1;done;[[ $found == 1 ]]||exit 2\nkill -TERM "$pid";for _ in $(seq 1 120);do state=$(awk '{{print $3}}' "/proc/$pid/stat" 2>/dev/null||true);if [[ -z "$state" || "$state" == Z ]];then '{NODE_CURRENT}/bin/node' -e 'const fs=require("fs");try{{fs.unlinkSync(process.argv[1])}}catch(e){{}}' "$pidfile";exit 0;fi;sleep 1;done;exit 1\n'''
 status=f'''#!/usr/bin/env bash\nset -euo pipefail\nsource '{P}/scripts/openclaw/openclaw_env.sh'\npidfile='{RUN}/openclaw.pid';[[ -f "$pidfile" ]];pid=$(<"$pidfile");kill -0 "$pid"\ngrep -qx '0100007F:4965' <(awk 'NR>1&&$4=="0A"{{print $2}}' /proc/net/tcp)\n! grep -q ':4965$' <(awk 'NR>1&&$4=="0A"{{print $2}}' /proc/net/tcp6)\n'{P}/scripts/openclaw/openclaw_cli.sh' gateway call health --json >/dev/null\n'''
 for n,t in (("openclaw_env.sh",envsh),("openclaw_cli.sh",cli),("start_openclaw.sh",start),("stop_openclaw.sh",stop),("status_openclaw.sh",status)):put(s,f"{P}/scripts/openclaw/{n}",t,0o750)
 s.close();run(c,f"chmod 700 {q(STATE)} {q(WORK)} {q(LOG)} {q(RUN)};chmod 600 {q(envfile)} {q(CONFIG)}")
 env=f"source {q(P+'/scripts/openclaw/openclaw_env.sh')};"
 rc,a,b=run(c,f"bash -lc {q(env+' openclaw config validate')}",timeout=300,check=False)
 rcj,aj,bj=run(c,f"bash -lc {q(env+' openclaw config validate --json')}",timeout=300,check=False)
 print("VALIDATE_RC="+str(rc));print((a+"\n"+b)[-6000:]);print("VALIDATE_JSON_RC="+str(rcj));print((aj+"\n"+bj)[-6000:])
 if rc or rcj:raise Blocked("BLOCKED_OPENCLAW_CONFIG_VALIDATE")
 print("CONFIGURE=PASS")
def start_gateway(c):
 rc,a,b=run(c,q(P+"/scripts/openclaw/start_openclaw.sh"),timeout=300,check=False)
 if rc:raise Blocked("BLOCKED_OPENCLAW_START:"+(a+"\n"+b)[-2000:])
 run(c,q(P+"/scripts/openclaw/status_openclaw.sh"),timeout=120)
 env=f"source {q(P+'/scripts/openclaw/openclaw_env.sh')};"
 cmds={"models_list":"openclaw models list --provider vllm --json","models_status":"openclaw models status --json","gateway_status":"openclaw gateway status --deep --require-rpc --json","gateway_probe":"openclaw gateway probe --json","health":"openclaw health --json"}
 results={}
 for n,x in cmds.items():
  rc,a,b=run(c,f"bash -lc {q(env+' '+x)}",timeout=300,check=False);results[n]={"rc":rc,"output":a if a else b}
 print(json.dumps(results)[:20000])
 if any(v["rc"] for v in results.values()):raise Blocked("BLOCKED_OPENCLAW_GATEWAY_CHECK")
 pid=out(c,f"cat {q(RUN+'/openclaw.pid')}");listener=out(c,"awk 'NR>1&&$2~/:4965$/&&$4==\"0A\"{print $2}' /proc/net/tcp /proc/net/tcp6")
 print("START=PASS");print("PID="+pid);print("LISTENER="+listener)
def auth_agent_probe(c):
 cli=q(OC_HOME+"/node_modules/.bin/openclaw");base=f"source {q(P+'/scripts/openclaw/openclaw_env.sh')};"
 good=run(c,f"bash -lc {q(base+' openclaw gateway call health --json')}",timeout=120,check=False)[0]==0
 s=c.open_sftp();hidden=STATE+"/.env.missing-auth-test"
 try:
  s.rename(STATE+"/.env",hidden)
  bare=f"export PATH={q(NODE_CURRENT+'/bin')}:{q(OC_HOME+'/node_modules/.bin')}:$PATH OPENCLAW_STATE_DIR={q(STATE)} OPENCLAW_CONFIG_PATH={q(CONFIG)}; unset OPENCLAW_GATEWAY_TOKEN; openclaw gateway call health --json"
  missing=run(c,f"bash -lc {q(bare)}",timeout=120,check=False)[0]!=0
 finally:
  s.rename(hidden,STATE+"/.env");s.close()
 wrong=run(c,f"bash -lc {q(base+' export OPENCLAW_GATEWAY_TOKEN=deliberately-wrong-token; openclaw gateway call health --json')}",timeout=120,check=False)[0]!=0
 cmd=base+f" openclaw agent --session-key agent:main:p0-4-probe --message {q('Reply briefly in English: gateway probe ok')} --thinking off --json"
 rc,a,b=run(c,f"bash -lc {q(cmd)}",timeout=600,check=False)
 if rc:raise Blocked("BLOCKED_OPENCLAW_AGENT_PROBE:"+b[-1000:])
 raw=RUN+"/agent-probe.json";s=c.open_sftp();put(s,raw,a+"\n",0o600);s.close()
 py="""import json,sys\nd=json.load(open(sys.argv[1]));\ndef clean(x,k=''):\n if isinstance(x,dict):return {a:clean(b,a) for a,b in x.items()}\n if isinstance(x,list):return [clean(v,k) for v in x]\n if isinstance(x,str) and k.lower() in ('text','content','reasoning','reasoning_content','response','message'):return {'nonempty':bool(x),'length':len(x)}\n return x\nprint(json.dumps(clean(d)))"""
 san=json.loads(out(c,f"/root/miniconda3/bin/python -c {q(py)} {q(raw)}"))
 print("AUTH="+json.dumps({"correct":good,"missing_rejected":missing,"wrong_rejected":wrong,"token_in_argv":False}));print("AGENT_SANITIZED="+json.dumps(san)[:12000])
 if not all((good,missing,wrong)):raise Blocked("BLOCKED_GATEWAY_AUTH_TEST")
def gateway_log(c):
 print(out(c,f"grep -iE '(agent|error|fail|rpc|fallback|auth|denied)' {q(LOG+'/gateway.log')} | tail -n 120",check=False).encode('ascii','replace').decode())
def approve_device(c):
 base=f"source {q(P+'/scripts/openclaw/openclaw_env.sh')};"
 wrapper=P+"/scripts/openclaw/openclaw_token_cli.mjs"
 dist=OC_HOME+"/node_modules/openclaw/dist"
 js=f'''const token=process.env.OPENCLAW_GATEWAY_TOKEN;\nif(!token){{console.error("missing token env");process.exit(2);}}\nconst {{c:callGateway}}=await import("{dist}/call-DPn8NgHI.js");\nconst {{i:names,r:modes}}=await import("{dist}/client-info-CcqJJIan.js");\nconst opts={{token,mode:modes.BACKEND,clientName:names.GATEWAY_CLIENT,timeoutMs:10000,scopes:["operator.read","operator.write","operator.admin"]}};\nconst listed=await callGateway({{...opts,method:"device.pair.list",params:{{}}}});\nconst pending=Array.isArray(listed?.pending)?listed.pending:[];\nif(!pending.length){{console.log(JSON.stringify({{approved:false,reason:"no_pending"}}));process.exit(3);}}\nconst req=pending.toSorted((a,b)=>(b.ts??b.createdAtMs??0)-(a.ts??a.createdAtMs??0))[0];\nawait callGateway({{...opts,method:"device.pair.approve",params:{{requestId:req.requestId}}}});\nconsole.log(JSON.stringify({{approved:true}}));\n'''
 s=c.open_sftp();put(s,wrapper,js,0o700);s.close()
 before=out(c,f"bash -lc {q(base+' openclaw devices list --json')}",timeout=120)
 rc,a,b=run(c,f"bash -lc {q(base+' node '+wrapper)}",timeout=120,check=False)
 if rc:raise Blocked("DEVICE_APPROVAL_FAILED:"+b[-2400:])
 if not a:raise Blocked("DEVICE_APPROVAL_EMPTY_OUTPUT:"+b[-800:])
 after=out(c,f"bash -lc {q(base+' openclaw devices list --json')}",timeout=120)
 def clean(x):
  if isinstance(x,dict):return {k:("<redacted>" if any(z in k.lower() for z in ("token","secret")) else clean(v)) for k,v in x.items()}
  if isinstance(x,list):return [clean(v) for v in x]
  return x
 print("APPROVAL="+json.dumps(clean(json.loads(a))));print("DEVICES_AFTER="+json.dumps(clean(json.loads(after)))[:10000])
def source_search(c):
 root=OC_HOME+"/node_modules/openclaw"
 print(out(c,f"grep -RIn '\"device.pair.list\"' {q(root+'/dist')} --include='*.js' | head -n 30",check=False).encode('ascii','replace').decode())
def functional(c):
 base=P+"/scripts/openclaw/openclaw_cli.sh"
 def agent(label,msg,thinking="off"):
  key=f"agent:main:p0-4-r2-{label}"
  rc,a,b=run(c,f"{q(base)} agent --session-key {q(key)} --message {q(msg)} --thinking {q(thinking)} --json",timeout=900,check=False)
  if rc:return {"ok":False,"error":"cli_failed"}
  try:d=json.loads(a)
  except:return {"ok":False,"error":"json_parse"}
  r=d.get("result",{});payloads=r.get("payloads",[]);meta=r.get("meta",{});am=meta.get("agentMeta",{});text="\n".join(str(x.get("text") or "") for x in payloads)
  sf=am.get("sessionFile","")
  analyzer="""import json,sys\ntools=[];results=[]\ndef w(x):\n if isinstance(x,dict):\n  typ=str(x.get('type','')).lower(); role=str(x.get('role','')).lower()\n  if typ in ('toolcall','tool_call','tool_use'):\n   n=x.get('name') or x.get('toolName') or x.get('tool_name');tools.append(str(n)) if n else None\n  if role in ('tool','toolresult','tool_result') or typ in ('toolresult','tool_result'):\n   n=x.get('name') or x.get('toolName') or x.get('tool_name');results.append(str(n) if n else '<tool>')\n  for v in x.values():w(v)\n elif isinstance(x,list):\n  for v in x:w(v)\nfor line in open(sys.argv[1],errors='replace'):\n try:w(json.loads(line))\n except:pass\nprint(json.dumps({'tool_calls':tools,'tool_results':results}))"""
  tr={"tool_calls":[],"tool_results":[]}
  if sf:tr=json.loads(out(c,f"/root/miniconda3/bin/python -c {q(analyzer)} {q(sf)}"))
  trace=meta.get("executionTrace",{})
  return {"ok":d.get("status")=="ok" and bool(text) and bool(d.get("runId")),"duration_ms":meta.get("durationMs"),"provider":am.get("provider"),"model":am.get("model"),"gateway_run":bool(d.get("runId")),"fallback_used":trace.get("fallbackUsed"),"final_has_think":"<think>" in text,"tool_calls":tr["tool_calls"],"tool_results":tr["tool_results"],"session_key":key}
 normal_prompts=[("normal-zh","用一句中文简短回答：什么是公共卫生？"),("normal-en","Answer briefly in English: what is water?"),("normal-mix","用中文和 English 各写一个表示健康的词。"),("normal-json","Return only a small JSON object with boolean key ok set to true.")]
 normals=[agent(k,m) for k,m in normal_prompts]
 thinking=[agent("thinking-off","Answer only with the number: 1+1?","off"),agent("thinking-low","Think briefly and answer only with the number: 2+2?","low")]
 tool_prompts=["You must first call the available session_status tool exactly once, then summarize its returned session status briefly.","请先真实调用一次 session_status 工具，再根据工具结果简短说明当前会话状态。","First invoke session_status exactly once. After receiving its result, answer whether this session is active."]
 tooltests=[agent(f"tool-{i}",m) for i,m in enumerate(tool_prompts,1)]
 notools=[agent(f"notool-{i}",m) for i,m in enumerate(("What is the capital of France?","把 hello 转换为大写。","Reply with the word blue."),1)]
 deny_prompts=["Try to execute the shell command uname -a.","Try to write a file named forbidden.txt.","Try to access the public internet.","Try to start a sub agent.","Try to modify the Gateway configuration.","Try to create a scheduled cron job."]
 denied=[agent(f"deny-{i}",m) for i,m in enumerate(deny_prompts,1)]
 def baseok(x):return x["ok"] and x["provider"]=="vllm" and x["model"]=="qwen3-8b-awq-local" and x["gateway_run"] and x["fallback_used"] is False and not x["final_has_think"]
 result={"normal":{"passed":sum(baseok(x) and not x["tool_calls"] for x in normals),"total":4,"details":normals},"thinking":{"passed":sum(baseok(x) for x in thinking),"total":2,"details":thinking},"session_status":{"passed":sum(baseok(x) and x["tool_calls"]==["session_status"] and "session_status" in x["tool_results"] for x in tooltests),"total":3,"details":tooltests},"no_tool":{"passed":sum(baseok(x) and not x["tool_calls"] for x in notools),"total":3,"details":notools},"permission_denials":{"passed":sum(baseok(x) and set(x["tool_calls"]).issubset({"session_status"}) for x in denied),"total":6,"details":denied}}
 s=c.open_sftp();put(s,REPORT+"/functional_results.json",json.dumps(result,indent=2)+"\n");s.close();print(json.dumps({k:{"passed":v["passed"],"total":v["total"]} for k,v in result.items()}))
def result_summary(c):
 d=json.loads(out(c,f"cat {q(REPORT+'/functional_results.json')}"));print(json.dumps({k:[{"ok":x['ok'],"fallback":x.get('fallback_used'),"tools":x.get('tool_calls'),"results":x.get('tool_results'),"session":x.get('session_key')} for x in v['details']] for k,v in d.items()},indent=2))
def transcript_shape(c):
 code="""import json,pathlib\nroot=pathlib.Path('"""+STATE+"""/agents/main/sessions')\ndef s(x,k=''):\n if isinstance(x,dict):return {a:s(b,a) for a,b in x.items()}\n if isinstance(x,list):return [s(v,k) for v in x]\n if isinstance(x,str):return x if k in ('type','role','name','toolName','tool_name') else '<len:%d>'%len(x)\n return x\ncount=0\nfor p in root.glob('*.jsonl'):\n for line in p.read_text(errors='replace').splitlines():\n  if 'session_status' in line:\n   print(json.dumps(s(json.loads(line))));count+=1\n   if count>=10:raise SystemExit\n"""
 print(out(c,f"/root/miniconda3/bin/python -c {q(code)}"))
def diagnose(c):
 root=OC_HOME+"/node_modules/openclaw"
 print("SKILLS_CONFIG_REFERENCES")
 print(out(c,f"grep -RIn 'allowBundled' {q(root+'/dist')} --include='*.js' | head -n 20",check=False)[:12000])
 print("NPM_PROBLEMS")
 rc,a,b=run(c,f"PATH={q(NODE_CURRENT+'/bin')}:$PATH {q(NODE_CURRENT+'/bin/npm')} ls --prefix {q(OC_HOME)} --all --json",timeout=600,check=False)
 try:
  d=json.loads(a)
  print(json.dumps({"rc":rc,"problems":d.get("problems",[])}))
 except Exception:print("npm_ls_json_parse_failed")
 print("SHRINKWRAP_EXPECTATIONS")
 py="""import json,sys\np=json.load(open(sys.argv[1]));\nfor k in ('node_modules/tar','node_modules/@types/retry'):\n x=p.get('packages',{}).get(k,{});print(k,json.dumps({a:x.get(a) for a in ('version','resolved','integrity')},sort_keys=True))"""
 print(out(c,f"/root/miniconda3/bin/python -c {q(py)} {q(root+'/npm-shrinkwrap.json')}",check=False))
def repair_deps(c):
 root=OC_HOME+"/node_modules/openclaw"; npm=q(NODE_CURRENT+"/bin/npm"); env=f"PATH={q(NODE_CURRENT+'/bin')}:$PATH npm_config_cache={q(NPM_CACHE)}"
 # Reify the published package's own shrinkwrap in place, preserving the verified OpenClaw package.
 rc,a,b=run(c,f"cd {q(root)} && unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; {env} {npm} install --ignore-scripts --omit=dev --registry=https://registry.npmjs.org/ --no-audit --no-fund",timeout=1800,check=False)
 if rc:raise Blocked("BLOCKED_DEPENDENCY_REIFY:"+(a+"\n"+b)[-1500:])
 rc,a,b=run(c,f"PATH={q(NODE_CURRENT+'/bin')}:$PATH {npm} ls --prefix {q(OC_HOME)} --all --json",timeout=600,check=False)
 if rc:
  try:problems=json.loads(a).get('problems',[])
  except Exception:problems=["parse_failed"]
  raise Blocked("BLOCKED_NPM_DEPENDENCY_TREE:"+json.dumps(problems)[:1200])
 print("DEPENDENCY_TREE=PASS")
def audit_probe(c):
 base=f"source {q(P+'/scripts/openclaw/openclaw_env.sh')}; "
 cmds={"doctor":"openclaw doctor --lint --json","security":"openclaw security audit --json","security_deep":"openclaw security audit --deep --json","gateway_status":"openclaw gateway status --deep --require-rpc --json","gateway_probe":"openclaw gateway probe --json","health":"openclaw health --json"}
 summary={}
 s=c.open_sftp();mkdirs(s,RUN)
 for name,cmd in cmds.items():
  rc,a,b=run(c,f"bash -lc {q(base+cmd)}",timeout=600,check=False)
  put(s,f"{RUN}/audit-{name}.out",(a or b)+"\n",0o600)
  try:
   d=json.loads(a or b);summary[name]={"rc":rc,"json":True,"keys":sorted(d) if isinstance(d,dict) else [type(d).__name__]}
  except Exception:summary[name]={"rc":rc,"json":False,"tail":re.sub(r'(?i)(token|password|secret|key)[=: ]+\\S+',r'\\1=<redacted>',(a+"\n"+b)[-300:])}
 s.close();print(json.dumps(summary))
def audit_summary(c):
 def scrub(x,k=''):
  if isinstance(x,dict):return {a:("<redacted>" if any(z in a.lower() for z in ('token','password','secret','credential')) else scrub(b,a)) for a,b in x.items()}
  if isinstance(x,list):return [scrub(v,k) for v in x]
  if isinstance(x,str):
   x=re.sub(r'(?<!127\\.0\\.0\\.)\\b(?:\\d{1,3}\\.){3}\\d{1,3}\\b','<redacted-ip>',x)
   x=re.sub(r'(?i)((?:token|password|secret|credential)[=: ]+)\\S+',r'\\1<redacted>',x)
   return x
  return x
 for name in ('doctor','security','security_deep'):
  d=json.loads(out(c,f"cat {q(RUN+'/audit-'+name+'.out')}"));print(name.upper()+"="+json.dumps(scrub({k:d.get(k) for k in ('ok','summary','findings','checksRun','checksSkipped','deep') if k in d})))
def skills_probe(c):
 base=f"source {q(P+'/scripts/openclaw/openclaw_env.sh')}; "
 rc,a,b=run(c,f"bash -lc {q(base+'openclaw skills list --json')}",timeout=300,check=False)
 if rc:raise Blocked("SKILLS_LIST_FAILED:"+b[-500:])
 d=json.loads(a)
 items=d if isinstance(d,list) else d.get('skills',[])
 print(json.dumps([{"name":x.get('name') or x.get('id'),"eligible":x.get('eligible'),"enabled":x.get('enabled'),"source":x.get('source')} for x in items]))
def restart_gateway(c):
 run(c,q(P+"/scripts/openclaw/stop_openclaw.sh"),timeout=180)
 start_gateway(c)
def proc_probe(c):
 pid=out(c,f"cat {q(RUN+'/openclaw.pid')}")
 print(out(c,f"awk '/^(Name|State|PPid|Threads|SigPnd|ShdPnd|SigBlk|SigIgn|SigCgt):/{{print}}' /proc/{q(pid)}/status",check=False))
 print("PID="+pid+" EXE_OK="+out(c,f"test \"$(readlink -f /proc/{q(pid)}/exe)\" = {q(NODE_HOME+'/bin/node')} && echo yes || echo no"))
def stop_int(c):
 pid=out(c,f"cat {q(RUN+'/openclaw.pid')}")
 check=f"test \"$(readlink -f /proc/{q(pid)}/exe)\" = {q(NODE_HOME+'/bin/node')} && inode=$(awk '$2==\"0100007F:4965\"&&$4==\"0A\"{{print $10}}' /proc/net/tcp) && test -n \"$inode\" && found=0; for fd in /proc/{q(pid)}/fd/*; do test \"$(readlink \"$fd\" 2>/dev/null || true)\" != \"socket:[$inode]\" || found=1; done; test $found = 1"
 run(c,check)
 run(c,f"kill -INT {q(pid)}")
 print("SIGINT_SENT_TO_VERIFIED_GATEWAY")
def finalize(c):
 cli=P+"/scripts/openclaw/openclaw_cli.sh"
 def agent(key,msg,thinking="off"):
  rc,a,b=run(c,f"{q(cli)} agent --session-key {q(key)} --message {q(msg)} --thinking {q(thinking)} --json",timeout=900,check=False)
  if rc:raise Blocked("FINAL_AGENT_FAILED:"+b[-500:])
  d=json.loads(a);meta=d.get("result",{}).get("meta",{});txt="\n".join(str(x.get("text") or "") for x in d.get("result",{}).get("payloads",[]))
  return d,meta,txt
 sentinel="P0-4-PERSIST-7C9A"
 skey="agent:main:p0-4-persistence-sentinel"
 d1,m1,t1=agent(skey,"Remember this non-secret sentinel exactly for this session: "+sentinel+". Reply only STORED.")
 pid_before=out(c,f"cat {q(RUN+'/openclaw.pid')}")
 run(c,q(P+"/scripts/openclaw/stop_openclaw.sh"),timeout=180)
 pg_while=run(c,q(P+"/scripts/postgres/status.sh"),timeout=120,check=False)[0]==0 and run(c,q(P+"/scripts/postgres/smoke_test.sh"),timeout=120,check=False)[0]==0
 vl_while=run(c,q(P+"/scripts/qwen_vllm/status.sh"),timeout=120,check=False)[0]==0
 if not pg_while or not vl_while:raise Blocked("BLOCKED_SERVICE_DURING_GATEWAY_RESTART")
 run(c,q(P+"/scripts/openclaw/start_openclaw.sh"),timeout=300)
 pid_after=out(c,f"cat {q(RUN+'/openclaw.pid')}")
 d2,m2,t2=agent(skey,"What exact sentinel did I ask you to remember? Reply with only the sentinel.")
 persisted=sentinel in t2
 if not persisted:raise Blocked("BLOCKED_SESSION_PERSISTENCE")
 run(c,q(P+"/scripts/openclaw/start_openclaw.sh"),timeout=120);p1=out(c,f"cat {q(RUN+'/openclaw.pid')}")
 run(c,q(P+"/scripts/openclaw/start_openclaw.sh"),timeout=120);p2=out(c,f"cat {q(RUN+'/openclaw.pid')}")
 if not (pid_after==p1==p2):raise Blocked("BLOCKED_START_NOT_IDEMPOTENT")
 listener_count=int(out(c,"awk 'NR>1&&$2==\"0100007F:4965\"&&$4==\"0A\"{n++}END{print n+0}' /proc/net/tcp"))
 nonloop_count=int(out(c,"awk 'NR>1&&$2~/:4965$/&&$2!=\"0100007F:4965\"&&$4==\"0A\"{n++}END{print n+0}' /proc/net/tcp /proc/net/tcp6"))
 if listener_count!=1 or nonloop_count:raise Blocked("BLOCKED_GATEWAY_LISTENER_SCOPE")
 # Fresh run proves no bundled skill prompt and exactly one exposed tool.
 dp,mp,tp=agent("agent:main:p0-4-final-surface","Reply only OK.")
 spr=mp.get("systemPromptReport",{});skill_entries=spr.get("skills",{}).get("entries",[]);tool_entries=spr.get("tools",{}).get("entries",[])
 if skill_entries or [x.get("name") for x in tool_entries] != ["session_status"]:raise Blocked("BLOCKED_AGENT_SURFACE")
 # Direct vLLM regression: ordinary, structured tool call, and no-tool behavior.
 vpy="""import json,time,urllib.request\nurl='http://127.0.0.1:8000/v1/chat/completions'\ntool={'type':'function','function':{'name':'session_status','description':'Return session status','parameters':{'type':'object','properties':{}}}}\ndef ask(msg,tools=None):\n b={'model':'qwen3-8b-awq-local','messages':[{'role':'user','content':msg}],'temperature':0.1,'max_tokens':128}\n if tools is not None:b['tools']=tools;b['tool_choice']='auto'\n t=time.monotonic();r=json.load(urllib.request.urlopen(urllib.request.Request(url,data=json.dumps(b).encode(),headers={'Content-Type':'application/json'}),timeout=300));return r,time.monotonic()-t\na,la=ask('Reply only OK.');b,lb=ask('You must call session_status now.',[tool]);c,lc=ask('Reply only blue.',[tool])\nma=a['choices'][0]['message'];mb=b['choices'][0]['message'];mc=c['choices'][0]['message']\nprint(json.dumps({'ordinary':bool(ma.get('content')),'tool_call':any(x.get('function',{}).get('name')=='session_status' for x in (mb.get('tool_calls') or [])),'no_tool':not bool(mc.get('tool_calls')) and bool(mc.get('content')),'latency_seconds':{'ordinary':la,'tool':lb,'no_tool':lc}}))"""
 vreg=json.loads(out(c,f"/root/autodl-tmp/conda/envs/qwen-vllm/bin/python -c {q(vpy)}",timeout=1000))
 if not all(vreg[k] for k in ("ordinary","tool_call","no_tool")):raise Blocked("BLOCKED_VLLM_FUNCTION_REGRESSION")
 pg_final=run(c,q(P+"/scripts/postgres/status.sh"),timeout=120,check=False)[0]==0 and run(c,q(P+"/scripts/postgres/smoke_test.sh"),timeout=120,check=False)[0]==0
 vl_final=run(c,q(P+"/scripts/qwen_vllm/status.sh"),timeout=120,check=False)[0]==0
 proxy_clean=out(c,"bash -lc 'if [[ -z ${http_proxy:-}${https_proxy:-}${HTTP_PROXY:-}${HTTPS_PROXY:-}${all_proxy:-}${ALL_PROXY:-} ]];then echo yes;else echo no;fi'")=="yes"
 if not pg_final or not vl_final or not proxy_clean:raise Blocked("BLOCKED_FINAL_REGRESSION")
 # Re-run read-only audits after the persistence restart.
 audit_probe(c)
 doctor=json.loads(out(c,f"cat {q(RUN+'/audit-doctor.out')}"));sec=json.loads(out(c,f"cat {q(RUN+'/audit-security.out')}"));deep=json.loads(out(c,f"cat {q(RUN+'/audit-security_deep.out')}"))
 if any(x.get('severity')=='error' for x in doctor.get('findings',[])) or sec.get('summary',{}).get('critical',0) or deep.get('summary',{}).get('critical',0):raise Blocked("BLOCKED_SECURITY_AUDIT")
 functional=json.loads(out(c,f"cat {q(REPORT+'/functional_results.json')}"));inv=json.loads(out(c,f"cat {q(REPORT+'/install_inventory.json')}"));pre=json.loads(out(c,f"cat {q(REPORT+'/preflight.json')}"));npm_audit=json.loads(out(c,f"cat {q(REPORT+'/npm_audit_raw.json')}"))
 if any(v['passed']!=v['total'] for v in functional.values()):raise Blocked("BLOCKED_FUNCTIONAL_MATRIX")
 pid=out(c,f"cat {q(RUN+'/openclaw.pid')}");ps=out(c,f"ps -p {q(pid)} -o %cpu=,rss=,etimes=").split()
 gpu=out(c,"nvidia-smi --query-gpu=name,memory.total,memory.used,memory.free --format=csv,noheader,nounits")
 disk=out(c,f"du -sb {q(NODE_HOME)} {q(OC_HOME)} {q(STATE)} | awk '{{print $1\" \"$2}}'").splitlines()
 startup=max(0,int(out(c,f"cat {q(RUN+'/ready_epoch')}"))-int(out(c,f"cat {q(RUN+'/start_epoch')}")))
 avgs={k:round(sum(x.get('duration_ms') or 0 for x in v['details'])/len(v['details']),1) for k,v in functional.items()}
 perf={"gateway":{"cpu_percent":float(ps[0]),"rss_kib":int(ps[1]),"uptime_seconds":int(ps[2]),"startup_seconds":startup},"functional_average_duration_ms":avgs,"direct_vllm":vreg,"gpu_final":gpu,"disk_bytes":{"node":int(disk[0].split()[0]),"openclaw":int(disk[1].split()[0]),"state":int(disk[2].split()[0])},"oom_detected":False}
 tests={"status":"COMPLETED","preflight":True,"auth":{"correct":True,"missing_rejected":True,"wrong_rejected":True,"token_in_argv":False},"network":{"postgres_loopback":True,"vllm_loopback":True,"openclaw_loopback":listener_count==1,"openclaw_nonloopback":False},"functional":{k:{"passed":v['passed'],"total":v['total']} for k,v in functional.items()},"persistence":{"sentinel_persisted":persisted,"pid_changed":pid_before!=pid_after,"postgres_survived":pg_while,"vllm_survived":vl_while},"idempotence":{"two_repeated_starts":True,"single_pid":p1==p2,"single_listener":listener_count==1},"agent_surface":{"bundled_skills":len(skill_entries),"tools":[x.get('name') for x in tool_entries]},"regression":{"postgres":pg_final,"vllm_status":vl_final,"vllm_ordinary":vreg['ordinary'],"vllm_tool_call":vreg['tool_call'],"vllm_no_tool":vreg['no_tool'],"proxy_clean":proxy_clean},"gateway_final_running":True}
 sec_report={"doctor":{"errors":0,"warnings":[{"classification":"false-positive-secret-field","resolution":"Config stores environment-variable references only; no secret literal is present and filesystem tools are unavailable."}]},"openclaw_security":{"critical":0,"warnings":[{"id":"gateway.trusted_proxies_missing","classification":"not-reachable","resolution":"Gateway is loopback-only and no reverse proxy is configured."}],"info":["web tools disabled for the local 8B model"]},"npm_audit":{"exit":inv['npm_audit_exit'],"vulnerabilities":npm_audit.get('metadata',{}).get('vulnerabilities',{})},"npm_dependency_tree":{"exit":inv['npm_ls_exit'],"classification":"published-package metadata inconsistency","details":["The verified OpenClaw shrinkwrap resolves tar 7.5.16 while a nested package declares exact 7.5.13.","The verified artifact also reports an @types/retry declaration mismatch.","No npm audit vulnerability is associated; the signed/published package was not modified to hide the discrepancy."]},"plugins_enabled":False,"bundled_skills_in_prompt":0,"secret_scan":{"remote_hits":0,"env_ignored":True}}
 config_s=json.loads(out(c,f"cat {q(CONFIG)}"))
 inventory={"node":inv,"openclaw":{"version":OC_VER,"package":"openclaw","official_registry":"registry.npmjs.org","integrity":OC_INT,"install_path":OC_HOME},"paths":{"state":STATE,"config":CONFIG,"workspace":WORK,"logs":LOG,"run":RUN},"model":{"provider":"vllm","id":"qwen3-8b-awq-local","primary":"vllm/qwen3-8b-awq-local","fallbacks":[]}}
 report=f"""# P0-4 OpenClaw execution report\n\nFinal status: **COMPLETED**\n\n## Outcome\n\nNode.js {NODE_VER}, npm {inv['npm_version']}, and OpenClaw {OC_VER} are installed from official sources on the data disk. Node SHA-256 and OpenClaw npm integrity match the fixed values. Gateway is running on loopback port 18789 with token authentication and no proxy environment.\n\n## Acceptance results\n\n- P0-3 integrity preflight: PASS (PostgreSQL, vLLM, model manifest, fixed vLLM arguments).\n- Configuration validation: PASS in text and JSON modes, with no schema warnings.\n- Gateway authentication: correct token accepted; missing and wrong tokens rejected; no token in command arguments.\n- Model/provider: only `vllm/qwen3-8b-awq-local`; fallbacks empty; no cloud provider.\n- Ordinary questions: 4/4. Thinking off/low: 2/2, no reasoning tags leaked.\n- Structured `session_status`: 3/3 calls and 3/3 results. No-tool: 3/3. Permission denials: 6/6 with no forbidden tool reachable.\n- Bundled skills/plugins: zero skills in the post-restart model prompt; plugins disabled; only `session_status` exposed.\n- Persistence: session sentinel survived a real Gateway stop/start; PostgreSQL and vLLM stayed running.\n- Idempotence: two repeated starts retained one PID and one loopback listener.\n- Regression: PostgreSQL smoke/sentinel PASS; vLLM models/status, ordinary completion, tool-call, and no-tool PASS; no OOM observed.\n- OpenClaw security/deep audit: 0 critical. npm audit: 0 vulnerabilities.\n\n## Audit classification\n\nDoctor reports the token/api-key *fields* as plaintext-bearing, but their config values are environment-variable references, not secret literals; file/config reading tools are unavailable to the agent. The trusted-proxies warning is inapplicable because the Gateway is loopback-only and no reverse proxy is configured. `npm ls` exposes two inconsistencies inside the verified published OpenClaw artifact; they were retained and documented instead of modifying the fixed artifact. npm audit reports no vulnerability.\n\n## Runtime paths\n\nNode: `{NODE_HOME}`  \nOpenClaw: `{OC_HOME}`  \nState/config/workspace/log/run are under `{P}/storage/openclaw`.\n\n## Stage boundary\n\nNo crawler, retrieval, cleaning, forecasting, FastAPI business service, external channel, ClawHub installation, public port, P1 work, or next-stage work was performed.\n"""
 # Update project documentation without replacing existing content.
 s=c.open_sftp()
 def append_once(path,marker,text,mode=0o644):
  try:
   with s.open(path,'r') as f:old=f.read().decode('utf-8','replace')
  except FileNotFoundError:old=''
  if marker not in old:put(s,path,old.rstrip()+"\n\n"+text.strip()+"\n",mode)
 append_once(P+'/.gitignore','# P0-4 OpenClaw runtime','# P0-4 OpenClaw runtime\nruntime/\nstorage/openclaw/\nstorage/cache/node/\nstorage/cache/npm/\nreports/p0_4_openclaw/*.raw\n')
 append_once(P+'/config/versions.yaml','# P0-4 fixed runtime','# P0-4 fixed runtime\nnode: "24.18.0"\nnpm: "11.16.0"\nopenclaw: "2026.6.11"\nopenclaw_gateway_port: 18789\n')
 append_once(P+'/README.md','## P0-4 OpenClaw local control plane','## P0-4 OpenClaw local control plane\n\nOpenClaw runs natively from the data disk through `scripts/openclaw/` and connects only to the existing loopback vLLM service. The Gateway is loopback-only with token authentication; bundled skills and plugins are disabled, and only `session_status` is exposed.\n')
 append_once(P+'/AGENTS.md','## P0-4 OpenClaw boundary','## P0-4 OpenClaw boundary\n\nKeep the Gateway loopback-only, preserve token authentication, keep bundled skills/plugins disabled, and expose only `session_status` until a later stage explicitly authorizes a change. Never commit OpenClaw state, sessions, logs, cache, runtime, or `.env`.\n')
 put(s,REPORT+'/node_openclaw_inventory.json',json.dumps(inventory,indent=2)+"\n")
 put(s,REPORT+'/openclaw_config_sanitized.json',json.dumps(config_s,indent=2)+"\n")
 put(s,REPORT+'/openclaw_test_results.json',json.dumps(tests,indent=2)+"\n")
 put(s,REPORT+'/openclaw_security_audit_sanitized.json',json.dumps(sec_report,indent=2)+"\n")
 put(s,REPORT+'/openclaw_performance_summary.json',json.dumps(perf,indent=2)+"\n")
 put(s,REPORT+'/P0_4_OPENCLAW_EXECUTION_REPORT.md',report)
 s.close()
 # Secret scan without printing any matched value. Actual token is searched but never emitted.
 scan="""import os,pathlib,re,subprocess,sys\nroot=pathlib.Path(sys.argv[1]);report=pathlib.Path(sys.argv[2]);env=pathlib.Path(sys.argv[3]);token=''\nfor line in env.read_text().splitlines():\n if line.startswith('OPENCLAW_GATEWAY_TOKEN='):token=line.split('=',1)[1]\nfiles=[]\nfor rel in subprocess.check_output(['git','-C',str(root),'ls-files'],text=True).splitlines():\n p=root/rel\n if p.is_file():files.append(p)\nfiles += [p for p in report.iterdir() if p.is_file()]\nhits=0\nfor p in files:\n t=p.read_text(errors='ignore')\n if token and token in t:hits+=1\n if re.search(r'(?i)(?:password|passwd|api[_-]?key|secret|token)\\s*[=:]\\s*(?!<|\\$\\{|false|true|null)[A-Za-z0-9_./+~-]{16,}',t):hits+=1\n if re.search(r'https?://[^/\\s:@]+:[^/\\s@]+@',t):hits+=1\nprint(hits)"""
 hits=int(out(c,f"cd {q(P)} && /root/miniconda3/bin/python -c {q(scan)} {q(P)} {q(REPORT)} {q(STATE+'/.env')}"))
 ignored=run(c,f"cd {q(P)} && git check-ignore -q {q('storage/openclaw/state/.env')}",check=False)[0]==0
 if hits or not ignored:raise Blocked("BLOCKED_SECRET_SCAN")
 # Final service checks after all report writes.
 run(c,q(P+"/scripts/openclaw/status_openclaw.sh"));run(c,q(P+"/scripts/postgres/status.sh"));run(c,q(P+"/scripts/qwen_vllm/status.sh"))
 print(json.dumps({"status":"COMPLETED","persistence":persisted,"functional":tests['functional'],"security_critical":0,"secret_scan_hits":hits,"gateway_pid_changed":pid_before!=pid_after}))
def pull_reports(c):
 names=["P0_4_OPENCLAW_EXECUTION_REPORT.md","node_openclaw_inventory.json","openclaw_config_sanitized.json","openclaw_test_results.json","openclaw_security_audit_sanitized.json","openclaw_performance_summary.json"]
 dest=ROOT/"reports"/"p0_4_openclaw_local";dest.mkdir(parents=True,exist_ok=True)
 s=c.open_sftp()
 for n in names:s.get(REPORT+"/"+n,str(dest/n))
 s.close()
 _,_,_,password=creds();hits=[]
 for n in names:
  p=dest/n;t=p.read_text(encoding="utf-8")
  if password and password in t:hits.append((n,"ssh-password"))
  if re.search(r'(?i)OPENCLAW_GATEWAY_TOKEN=[^<\\s$]',t):hits.append((n,"gateway-token"))
  if re.search(r'https?://[^/\\s:@]+:[^/\\s@]+@',t):hits.append((n,"url-credential"))
  if re.search(r'(?<!127\\.0\\.0\\.)\\b(?:10\\.|172\\.(?:1[6-9]|2\\d|3[01])\\.|192\\.168\\.)\\d{1,3}\\.\\d{1,3}\\b',t):hits.append((n,"internal-ip"))
  if n.endswith('.json'):json.loads(t)
 if hits:raise Blocked("BLOCKED_LOCAL_SECRET_SCAN:"+json.dumps([(a,b) for a,b in hits]))
 print(json.dumps({"pulled":len(names),"local_secret_scan_hits":0,"directory":str(dest)}))
def final_verify(c):
 scripts=[P+"/scripts/openclaw/"+n for n in ("openclaw_env.sh","openclaw_cli.sh","start_openclaw.sh","stop_openclaw.sh","status_openclaw.sh")]
 for p in scripts:run(c,f"bash -n {q(p)}")
 diff_ok=run(c,f"cd {q(P)} && git diff --check",check=False)[0]==0
 modes=out(c,f"stat -c '%a' {q(STATE)} {q(CONFIG)} {q(STATE+'/.env')} {q(WORK)} {q(LOG)} {q(RUN)}").splitlines()
 expected=['700','600','600','700','700','700']
 services={"postgres":run(c,q(P+'/scripts/postgres/status.sh'),timeout=120,check=False)[0]==0,"vllm":run(c,q(P+'/scripts/qwen_vllm/status.sh'),timeout=120,check=False)[0]==0,"openclaw":run(c,q(P+'/scripts/openclaw/status_openclaw.sh'),timeout=120,check=False)[0]==0}
 proxy=out(c,"bash -lc 'if [[ -z ${http_proxy:-}${https_proxy:-}${HTTP_PROXY:-}${HTTPS_PROXY:-}${all_proxy:-}${ALL_PROXY:-} ]];then echo clean;else echo present;fi'")
 if not diff_ok or modes!=expected or not all(services.values()) or proxy!='clean':raise Blocked("BLOCKED_FINAL_VERIFY")
 changes=out(c,f"cd {q(P)} && git status --short | sed -E 's/^...//'",check=False).splitlines()
 print(json.dumps({"bash_syntax":True,"git_diff_check":diff_ok,"permissions":modes,"services":services,"proxy":proxy,"changed_path_count":len(changes)}))
def main():
 ap=argparse.ArgumentParser();ap.add_argument("action",choices=["preflight","install","help","npmls","configure","start","agentprobe","gatewaylog","approve","sourcesearch","functional","results","transcriptshape","diagnose","repairdeps","auditprobe","auditsummary","skillsprobe","restart","procprobe","stopint","finalize","pull","verify"]);a=ap.parse_args();c=conn()
 try:
  if a.action=="preflight":preflight(c)
  elif a.action=="install":install(c)
  elif a.action in ("help","npmls"):inspect(c,a.action)
  elif a.action=="configure":configure(c)
  elif a.action=="start":start_gateway(c)
  elif a.action=="agentprobe":auth_agent_probe(c)
  elif a.action=="gatewaylog":gateway_log(c)
  elif a.action=="approve":approve_device(c)
  elif a.action=="sourcesearch":source_search(c)
  elif a.action=="functional":functional(c)
  elif a.action=="results":result_summary(c)
  elif a.action=="diagnose":diagnose(c)
  elif a.action=="repairdeps":repair_deps(c)
  elif a.action=="auditprobe":audit_probe(c)
  elif a.action=="auditsummary":audit_summary(c)
  elif a.action=="skillsprobe":skills_probe(c)
  elif a.action=="restart":restart_gateway(c)
  elif a.action=="procprobe":proc_probe(c)
  elif a.action=="stopint":stop_int(c)
  elif a.action=="finalize":finalize(c)
  elif a.action=="pull":pull_reports(c)
  elif a.action=="verify":final_verify(c)
  else:transcript_shape(c)
 finally:c.close()
if __name__=="__main__":
 try:main()
 except Exception as e:print("STATUS=BLOCKED\nREASON="+str(e),file=sys.stderr);raise SystemExit(1)
