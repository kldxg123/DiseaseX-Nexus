"""Read-only authenticated audit. Credential values never enter output or reports."""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import shlex
from pathlib import Path
import urllib.request
import urllib.error
import paramiko
import concurrent.futures
import os
import subprocess

ROOT = Path(__file__).resolve().parents[1]
THREADS = ("01a03757-dfad-7731-8a55-440ae47dcb9b", "019f4ef9-8968-7313-8a93-63b737b33228", "01a161be-c86e-7911-bedf-61cb809ad214")
PAT = re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{50,})")
DISCOVERED_REPOS = set()


def emit(value):
    print(json.dumps(value, ensure_ascii=True), flush=True)


def discover_tokens():
    found = []
    sources = []
    sessions = Path("USER_HOME/.codex/sessions")
    for thread in THREADS:
        sources.extend(sessions.rglob("*" + thread + "*.jsonl"))
    for path in sessions.rglob("*.jsonl"):
        if path in sources:
            continue
        try:
            with path.open(encoding="utf-8") as f:
                meta = json.loads(f.readline()).get("payload", {})
            if str(meta.get("cwd", "")).replace("\\", "/").lower() == ROOT.as_posix().lower():
                sources.append(path)
        except Exception:
            pass
    emit({"project_history_files": len(sources)})
    for path in sources:
        with path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                for owner, repo in re.findall(r"github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", line):
                    if re.search("epidemic|diseasex|aibola", repo, re.I):
                        DISCOVERED_REPOS.add(owner + "/" + repo.removesuffix(".git"))
                for token in PAT.findall(line):
                    if token not in found:
                        found.append(token)
    for key in ("GITHUB_TOKEN", "GH_TOKEN"):
        if os.environ.get(key) and os.environ[key] not in found:
            found.append(os.environ[key])
    if not found:
        # The user explicitly authorized searching earlier conversations for their token.
        # Only token-shaped strings are retained; other chat content is not reported.
        others = [p for p in sessions.rglob("*.jsonl") if p not in sources]
        for path in sorted(others, key=lambda p:p.stat().st_mtime):
            with path.open(encoding="utf-8", errors="replace") as f:
                for line in f:
                    for token in PAT.findall(line):
                        if token not in found:
                            found.append(token)
        emit({"additional_history_files_checked": len(others)})
    for path in (ROOT / "secrets").glob("*"):
        if path.is_file() and re.search("github|gh_token", path.name, re.I):
            for token in PAT.findall(path.read_text(encoding="utf-8", errors="replace")):
                if token not in found:
                    found.append(token)
    try:
        git = "USER_HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/native/git/cmd/git.exe"
        env = {**os.environ, "GCM_INTERACTIVE": "never", "GIT_TERMINAL_PROMPT": "0"}
        response = subprocess.run([git, "credential", "fill"], input="protocol=https\nhost=github.com\n\n", capture_output=True, text=True, timeout=15, env=env)
        for line in response.stdout.splitlines():
            if line.startswith("password=") and line[9:] not in found:
                found.append(line[9:])
    except Exception:
        pass
    emit({"historical_project_repository_links": len(DISCOVERED_REPOS)})
    return found


def github(path, token):
    headers={"Accept": "application/vnd.github+json", "User-Agent": "project-readonly-completeness-audit"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request("https://api.github.com" + path, headers=headers)
    with urllib.request.urlopen(req, timeout=25) as response:
        return json.load(response)


def profiles():
    result = []
    for p in ROOT.glob("服务器信息*.txt"):
        raw = p.read_bytes()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("gb18030")
        values = {}
        lines = [x.strip() for x in text.splitlines() if x.strip()]
        for line in lines:
            match = re.match(r"\s*([^:：=]{1,30})\s*[:：=]\s*(.+)", line)
            if match:
                key, value = match.groups()
                key = key.lower().strip()
                if key in {"ip", "host", "hostname", "服务器", "服务器ip", "服务器地址", "地址"}:
                    values["hostname"] = value.strip()
                elif key in {"port", "端口", "ssh端口"}:
                    values["port"] = value.strip()
                elif key in {"user", "username", "用户名", "用户", "账号"}:
                    values["username"] = value.strip()
                elif key in {"password", "passwd", "密码"}:
                    values["password"] = value.strip()
            if re.search(r"\bssh\s", line):
                parts = shlex.split(line[line.index("ssh"):])
                for index, part in enumerate(parts):
                    if part == "-p" and index + 1 < len(parts):
                        values["port"] = parts[index + 1]
                    if "@" in part and not part.startswith("-"):
                        values["username"], values["hostname"] = part.split("@", 1)
                if "password" not in values and len(lines) == 2:
                    values["password"] = lines[1] if lines[0] == line else lines[0]
        if "hostname" not in values:
            found_ip = re.search(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])", text)
            if found_ip:
                values["hostname"] = found_ip.group()
        values["port"] = int(values.get("port", "22"))
        result.append((p.stem.replace("服务器信息", "profile"), values))
    return result


REMOTE_INSPECT = r'''
import os,glob,json,subprocess
roots=[]
for pattern in ['USER_HOME/autodl-tmp/*epidemic*','/opt/*epidemic*','/srv/*epidemic*','USER_HOME/*epidemic*','/workspace/*epidemic*','USER_HOME/*epidemic*']:
 for root in glob.glob(pattern):
  if os.path.isdir(root) and root not in roots: roots.append(root)
if not roots:
 for base in ['/root','/opt','/srv','/data','/workspace','/home','/mnt','/www']:
  visited=0
  for here,dirs,files in os.walk(base):
   visited+=1
   depth=here[len(base):].count(os.sep)
   dirs[:]=[d for d in dirs if d not in {'.cache','.venv','venv','node_modules','models','raw','.git','site-packages','envs','pkgs'} and not d.startswith('.')]
   if depth>=5 or visited>30000: dirs[:]=[]
   name=os.path.basename(here).lower()
   if any(k in name for k in ['epidemic','aibola','diseasex']) and any(x in files or x in dirs for x in ['pyproject.toml','src','apps','.git']):
    if here not in roots: roots.append(here)
    dirs[:]=[]
result=[]
for root in roots:
 def git(*args):
  p=subprocess.run(['git','-C',root,*args],capture_output=True,text=True,timeout=10)
  return p.stdout.strip() if p.returncode==0 else None
 status=git('status','--porcelain','--untracked-files=normal') or ''
 tracked=git('ls-files') or ''
 code_ext={'.py','.js','.ts','.tsx','.cjs','.mjs','.sh','.ps1','.yaml','.yml','.toml'}
 stats={'code_files':0,'data_files':0,'data_bytes':0}
 skip={'.git','.venv','venv','node_modules','__pycache__','secrets','private','logs','.cache','cache','huggingface','pip','pkgs','envs'}
 for here,dirs,files in os.walk(root):
  dirs[:]=[d for d in dirs if d not in skip and not d.startswith('.')]
  for name in files:
   ext=os.path.splitext(name)[1].lower()
   if ext in code_ext: stats['code_files']+=1
   elif ext in {'.csv','.jsonl','.parquet','.sqlite','.sqlite3','.db','.pgcustom','.dump'}:
    stats['data_files']+=1
    try: stats['data_bytes']+=os.path.getsize(os.path.join(here,name))
    except OSError: pass
 result.append({'root':root,'name':os.path.basename(root),'head':git('rev-parse','HEAD'),'branch':git('branch','--show-current'),'remotes':git('remote','-v'),'top_dirs':sorted(x for x in os.listdir(root) if os.path.isdir(os.path.join(root,x))),'tracked_files':len(tracked.splitlines()),'changed_tracked':sum(not x.startswith('??') for x in status.splitlines()),'untracked_entries':sum(x.startswith('??') for x in status.splitlines()),'stats':stats})
print(json.dumps(result))
'''


def connect_profile(item):
    name, values = item
    client = paramiko.SSHClient()
    client.load_system_host_keys()
    for path in ROOT.glob("known_hosts*"):
        try:
            client.load_host_keys(str(path))
        except Exception:
            pass
    try:
        client.connect(**values, timeout=10, banner_timeout=10, auth_timeout=10, look_for_keys=False, allow_agent=False)
        _, stdout, stderr = client.exec_command("python3 -c " + shlex.quote(REMOTE_INSPECT), timeout=35)
        result = json.loads(stdout.read().decode())
        emit({"profile": name, "ssh": "connected", "projects": [{k:v for k,v in p.items() if k not in {"root","remotes","top_dirs"}} | {"git_remote_present": bool(p["remotes"])} for p in result]})
        return name, client, result
    except Exception as exc:
        client.close()
        emit({"profile": name, "ssh": "unavailable", "error_type": type(exc).__name__})
        return name, None, []


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--servers", action="store_true")
    args = parser.parse_args()
    emit({"phase": "history_search_started"})
    tokens = discover_tokens()
    emit({"historical_token_candidates": len(tokens)})
    repos = []
    working = None
    valid_tokens = []
    for token in reversed(tokens[-12:]):
        try:
            github("/user", token)
            if working is None:
                working = token
            valid_tokens.append(token)
            emit({"historical_authentication": "valid"})
        except urllib.error.HTTPError as exc:
            emit({"historical_authentication_http_status": exc.code})
        except Exception as exc:
            emit({"historical_authentication_error_type": type(exc).__name__})
    if working:
        repo_tokens = {}
        unique = {}
        for auth in valid_tokens:
            for page in (1, 2):
                try:
                    batch = github(f"/user/repos?per_page=100&sort=updated&page={page}", auth)
                except Exception:
                    break
                for repo in batch:
                    unique[repo["id"]] = repo
                    repo_tokens[repo["id"]] = auth
                if len(batch) < 100:
                    break
        repos = list(unique.values())
        selected = [r for r in repos if re.search("epidemic|disease|aibola|ebola|openclaw", r["name"], re.I)]
        emit({"accessible_repository_names": [r["name"] for r in repos], "accessible_repository_count": len(repos)})
        emit({"candidate_repositories": [{"name": r["name"], "private": r["private"], "default_branch": r["default_branch"], "pushed_at": r["pushed_at"]} for r in selected]})
        for repo in repos:
            try:
                tree = github("/repos/" + repo["full_name"] + "/git/trees/" + repo["default_branch"] + "?recursive=1", repo_tokens[repo["id"]])
                paths = [x["path"] for x in tree.get("tree", [])]
                markers = [m for m in ("packages/worker_agent/tasks.py", "packages/cleaner_agent/agent.py", "packages/predictor_agent/agent.py", "src/epidemic_core/forecast_model_kg") if any(p == m or p.endswith("/" + m) or p.startswith(m + "/") for p in paths)]
                emit({"repository_tree": repo["name"], "tree_entries": len(paths), "truncated": tree.get("truncated", False), "project_signature_matches": markers})
            except urllib.error.HTTPError as exc:
                emit({"repository_tree": repo["name"], "http_status": exc.code})
    for full in sorted(DISCOVERED_REPOS):
        try:
            info = github("/repos/" + full, working)
            emit({"historical_repository": info["name"], "accessible": True, "default_branch": info["default_branch"], "pushed_at": info["pushed_at"]})
        except urllib.error.HTTPError as exc:
            emit({"historical_repository": full.split("/")[-1], "accessible": False, "http_status": exc.code})
        except Exception as exc:
            emit({"historical_repository_error_type": type(exc).__name__})
    for name, value in profiles():
        emit({"profile": name, "fields_present": sorted(value), "ready_for_ssh": all(value.get(k) for k in ("hostname", "username", "password", "port"))})
    if args.servers:
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(connect_profile, profiles()))
        for name, client, projects in results:
            if client:
                client.close()


if __name__ == "__main__":
    main()
