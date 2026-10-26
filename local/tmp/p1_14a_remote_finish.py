from __future__ import annotations

import base64
import hashlib
import json
import re
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import a100_p1_12 as p112  # noqa: E402


def redact(text: str, project: str, data_root: str = "") -> str:
    text = text.replace(project, "[project]")
    if data_root:
        text = text.replace(data_root, "[data-root]")
    text = re.sub(r"postgresql(?:\+\w+)?://[^\s'\"]+", "[DATABASE_URL_REDACTED]", text)
    text = re.sub(r"(?i)(password|token|secret|api[_-]?key)(\s*[=:]\s*)\S+", r"\1\2[REDACTED]", text)
    return text


def run_checked(a100, client, project: str, command: str, timeout: int = 300, data_root: str = "") -> dict[str, object]:
    rc, out, err = a100.run(client, f"bash -lc {shlex.quote(command)}", timeout=timeout)
    safe = redact((out or "") + ("\n" + err if err else ""), project, data_root)
    return {"returncode": rc, "output": safe.strip(), "ok": rc == 0}


def upload_screenshots(a100, client, remote_dir: str) -> list[dict[str, object]]:
    uploaded: list[dict[str, object]] = []
    local_dirs = [ROOT / "tmp" / "p1_14a_screenshots", ROOT / "tmp" / "p1_14a_screenshots_auth"]
    sftp = client.open_sftp()
    try:
        for local_dir in local_dirs:
            if not local_dir.exists():
                continue
            for local in sorted(local_dir.glob("*.png")):
                remote = f"{remote_dir}/{local.name}"
                tmp = f"{remote}.tmp"
                sftp.put(str(local), tmp)
                sftp.chmod(tmp, 0o640)
                sftp.posix_rename(tmp, remote)
                data = local.read_bytes()
                uploaded.append(
                    {
                        "name": local.name,
                        "bytes": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "remote": remote,
                    }
                )
    finally:
        sftp.close()
    return uploaded


def main() -> None:
    a100 = p112.p111.p110r3.previous.p19.a100
    client, gate = a100.connect()
    try:
        data_root, project = a100.paths(client)
        evidence_root = "/tmp/p1_14a_ebola_demo"
        screenshots_dir = f"{evidence_root}/screenshots"
        mkdir = run_checked(a100, client, project, f"mkdir -p {shlex.quote(screenshots_dir)} && chmod 700 {shlex.quote(evidence_root)} {shlex.quote(screenshots_dir)}", data_root=data_root)
        uploads = upload_screenshots(a100, client, screenshots_dir)
        final_cmd = r'''
set -euo pipefail
PROJECT="$1"
cd "$PROJECT"
python3 - <<'PY'
import hashlib, json, os, subprocess, urllib.request
from pathlib import Path
project=Path.cwd()
files=[
 "src/epidemic_core/p1_12/dashboard_app.py",
 "src/epidemic_core/discovery/review_app.py",
 "src/epidemic_core/p1_12/review_app.py",
]
def sha(path):
    p=project/path
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
def cmd(args, timeout=60):
    p=subprocess.run(args,cwd=project,text=True,capture_output=True,timeout=timeout)
    out=(p.stdout or "") + ("\n"+p.stderr if p.stderr else "")
    return {"rc":p.returncode,"out":out.strip()}
status=cmd(["git","status","--short","-uall"])
diff_names=cmd(["git","diff","--name-status","--"]+files)
diff_check=cmd(["git","diff","--check","--"]+files)
heads=cmd(["bash","-lc","../conda/envs/epidemic-core/bin/python -m alembic heads 2>/dev/null || ./storage/conda/envs/epidemic-core/bin/python -m alembic heads 2>/dev/null || alembic heads"],120)
ready={}
for name,url in {
 "dashboard_ready":"http://127.0.0.1:8020/readyz",
 "review_ready":"http://127.0.0.1:8021/readyz",
}.items():
    try:
        with urllib.request.urlopen(url,timeout=10) as r:
            raw=r.read(200000)
            parsed=None
            try: parsed=json.loads(raw.decode())
            except Exception: parsed=raw.decode(errors="replace")[:800]
            ready[name]={"status":r.status,"body":parsed}
    except Exception as exc:
        ready[name]={"error":type(exc).__name__,"message":str(exc)[:200]}
pid_cmd=cmd(["bash","-lc","ps -eo pid,comm,args | grep -E 'postgres|vllm|dashboard|review|uvicorn|gunicorn' | grep -v grep | sed -E 's/[[:space:]]+/ /g' | head -80 | sed 's#USER_HOME ]*/autodl-tmp#[data-root]#g'"])
print(json.dumps({
 "project_basename": project.name,
 "target_identity_verified": bool(%r),
 "head": cmd(["git","rev-parse","HEAD"]).get("out"),
 "branch": cmd(["git","branch","--show-current"]).get("out"),
 "dirty_count": len([l for l in status["out"].splitlines() if l.strip()]),
 "target_file_sha256": {f: sha(Path(f)) for f in files},
 "target_diff_name_status": diff_names,
 "target_diff_check": diff_check,
 "alembic_heads": heads,
 "http": ready,
 "service_process_sample": pid_cmd,
},ensure_ascii=False,default=str))
PY
''' % (gate.get("stable_second_connection"),)
        final = run_checked(a100, client, project, f"bash -s -- {shlex.quote(project)} <<'REMOTE'\n{final_cmd}\nREMOTE", timeout=300, data_root=data_root)
        print(json.dumps({"mkdir": mkdir, "screenshots_uploaded": uploads, "final": final}, ensure_ascii=False, indent=2))
    finally:
        client.close()


if __name__ == "__main__":
    main()
