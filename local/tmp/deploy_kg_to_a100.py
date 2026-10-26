"""Upload the verified forecast-model knowledge graph v1 module to the A100 dashboard runtime.

Reads SSH credentials from the local server-info file without printing them.
Deploys only additive files; does not touch database or existing code.
"""
from __future__ import annotations

import posixpath
import sys
from pathlib import Path

import paramiko

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import migrate_epidemic_to_a100 as migration  # noqa: E402

STAGE = Path(__file__).resolve().parent.parent / "kg_deploy_staging"
REMOTE_RT = "autodl-tmp/epidemic-dashboard-runtime"  # relative to remote $HOME

FILES = [
    ("src/epidemic_core/forecast_model_kg/__init__.py", f"{REMOTE_RT}/src/epidemic_core/forecast_model_kg/__init__.py"),
    ("src/epidemic_core/forecast_model_kg/loader.py", f"{REMOTE_RT}/src/epidemic_core/forecast_model_kg/loader.py"),
    ("src/epidemic_core/forecast_model_kg/engine.py", f"{REMOTE_RT}/src/epidemic_core/forecast_model_kg/engine.py"),
    ("src/epidemic_core/forecast_model_kg/service.py", f"{REMOTE_RT}/src/epidemic_core/forecast_model_kg/service.py"),
    ("src/epidemic_core/forecast_model_kg/dashboard_app.py", f"{REMOTE_RT}/src/epidemic_core/forecast_model_kg/dashboard_app.py"),
    ("src/epidemic_core/forecast_model_kg/r3r.py", f"{REMOTE_RT}/src/epidemic_core/forecast_model_kg/r3r.py"),
    ("src/epidemic_core/forecast_model_kg/r4_shadow.py", f"{REMOTE_RT}/src/epidemic_core/forecast_model_kg/r4_shadow.py"),
    ("src/epidemic_core/forecast_model_kg/r4r2_semantic.py", f"{REMOTE_RT}/src/epidemic_core/forecast_model_kg/r4r2_semantic.py"),
    ("src/epidemic_core/forecast_model_kg/r4r3_review_input.py", f"{REMOTE_RT}/src/epidemic_core/forecast_model_kg/r4r3_review_input.py"),
    ("src/epidemic_core/dashboard/static/forecast-model-kg.js", f"{REMOTE_RT}/src/epidemic_core/dashboard/static/forecast-model-kg.js"),
    ("src/epidemic_core/dashboard/static/forecast-model-kg.css", f"{REMOTE_RT}/src/epidemic_core/dashboard/static/forecast-model-kg.css"),
    ("reference/forecast_model_knowledge_graph.v1.json", f"{REMOTE_RT}/reference/forecast_model_knowledge_graph.v1.json"),
]


def main() -> int:
    creds = migration.credentials(migration.TARGET_INFO)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(creds.host, port=creds.port, username=creds.user, password=creds.password, timeout=30)
    try:
        stdin, stdout, stderr = client.exec_command("printf '%s' \"$HOME\"")
        home = stdout.read().decode().strip()
        sftp = client.open_sftp()
        uploaded = []
        for local_rel, remote_rel in FILES:
            remote_path = posixpath.join(home, remote_rel)
            parent = posixpath.dirname(remote_path)
            stdin, stdout, stderr = client.exec_command(f"mkdir -p {parent!r}")
            if stdout.channel.recv_exit_status() != 0:
                raise RuntimeError(f"MKDIR_FAILED:{remote_rel}")
            sftp.put(str(STAGE / local_rel), remote_path)
            uploaded.append(remote_rel)
        sftp.close()
        print(f"UPLOADED {len(uploaded)} files")
        for item in uploaded:
            print(" -", item)
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
