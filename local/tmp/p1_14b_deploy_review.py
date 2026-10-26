from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import a100_p1_12 as p112  # noqa: E402


def main() -> None:
    a100 = p112.p111.p110r3.previous.p19.a100
    client, _gate = a100.connect()
    rel = "src/epidemic_core/discovery/review_app.py"
    local = ROOT / "remote_edit" / "p1_12_current" / rel
    try:
        data_root, project = a100.paths(client)
        sftp = client.open_sftp()
        remote = f"{project}/{rel}"
        tmp = f"{remote}.p1_14b_tmp"
        try:
            backup_dir = "/tmp/p1_14b_ebola_expansion/backups"
            a100.run(client, f"mkdir -p {shlex.quote(backup_dir)} && cp {shlex.quote(remote)} {shlex.quote(backup_dir + '/review_app.py.pre_deploy')}", timeout=60)
            sftp.put(str(local), tmp)
            sftp.chmod(tmp, 0o644)
            sftp.posix_rename(tmp, remote)
        finally:
            sftp.close()
        cmd = (
            f"cd {shlex.quote(project)} && "
            "bash -n scripts/source_review/start_source_review.sh && "
            "../conda/envs/epidemic-core/bin/python -m py_compile src/epidemic_core/discovery/review_app.py && "
            "scripts/source_review/install_runtime.sh && "
            "scripts/source_review/stop_source_review.sh && "
            "scripts/source_review/start_source_review.sh && "
            "sleep 2 && "
            "python3 - <<'PY'\n"
            "import json,urllib.request,hashlib,pathlib,subprocess\n"
            "out={}\n"
            "try:\n"
            " r=urllib.request.urlopen('http://127.0.0.1:8021/readyz',timeout=10); out['readyz']={'status':r.status,'body':json.loads(r.read().decode())}\n"
            "except Exception as e: out['readyz']={'error':repr(e)}\n"
            "out['sha256']=hashlib.sha256(pathlib.Path('src/epidemic_core/discovery/review_app.py').read_bytes()).hexdigest()\n"
            "p=subprocess.run(['bash','-lc',\"ps -eo pid,comm,args | grep '8021' | grep -v grep\"],text=True,capture_output=True)\n"
            "out['process']=p.stdout.strip()\n"
            "print(json.dumps(out))\n"
            "PY"
        )
        rc, out, err = a100.run(client, cmd, timeout=600)
        safe = ((out or "") + ("\n" + err if err else "")).replace(project, "[project]").replace(data_root, "[data-root]")
        safe = re.sub(r"postgresql(?:\+\w+)?://[^\s'\"<>]+", "[DATABASE_URL_REDACTED]", safe)
        print(json.dumps({"returncode": rc, "output": safe}, ensure_ascii=False, indent=2))
    finally:
        client.close()


if __name__ == "__main__":
    main()
