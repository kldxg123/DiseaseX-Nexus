from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import a100_p1_12 as p112  # noqa: E402


SCRIPT = r'''
set -euo pipefail
PROJECT="$1"
cd "$PROJECT"
source scripts/source_discovery_worker/_common.sh >/dev/null 2>&1
"$PYTHON" - <<'PY'
import json,os,sqlalchemy as sa
from sqlalchemy import text
engine=sa.create_engine(os.environ["APP_DATABASE_URL"],hide_parameters=True,pool_pre_ping=True)
with engine.begin() as conn:
    run_ids=[r[0] for r in conn.execute(text("SELECT id FROM epidemic_discovery.discovery_runs WHERE policy_version='p1-14b-static-historical-v1'")).all()]
    if run_ids:
        conn.execute(text("UPDATE epidemic_discovery.candidate_fetches SET status='FAILED',completed_at=now(),failure_code='BLOCKED_PREVIEW_ONLY_WRITE_PATH' WHERE discovery_run_id=ANY(:ids) AND status='STARTED'"),{"ids":run_ids})
        conn.execute(text("UPDATE epidemic_discovery.discovery_runs SET state='FAILED_TERMINAL',updated_at=now(),terminal_reason_code='BLOCKED_PREVIEW_ONLY_WRITE_PATH',last_error_code='BLOCKED_PREVIEW_ONLY_WRITE_PATH' WHERE id=ANY(:ids) AND state<>'FAILED_TERMINAL'"),{"ids":run_ids})
with engine.connect() as conn:
    print(json.dumps({"runs":[dict(r) for r in conn.execute(text("SELECT id,state,terminal_reason_code,last_error_code FROM epidemic_discovery.discovery_runs WHERE policy_version='p1-14b-static-historical-v1'")).mappings()],"fetches":[dict(r) for r in conn.execute(text("SELECT status,failure_code FROM epidemic_discovery.candidate_fetches WHERE discovery_run_id=ANY(:ids)"),{"ids":run_ids}).mappings()]},default=str))
PY
'''


def main() -> None:
    a100 = p112.p111.p110r3.previous.p19.a100
    client, _gate = a100.connect()
    try:
        data_root, project = a100.paths(client)
        sftp = client.open_sftp()
        remote = "/tmp/p1_14b_ebola_expansion/mark_failed.sh"
        try:
            with sftp.open(remote + ".tmp", "wb") as h:
                h.write(SCRIPT.encode("utf-8"))
            sftp.chmod(remote + ".tmp", 0o700)
            sftp.posix_rename(remote + ".tmp", remote)
        finally:
            sftp.close()
        rc, out, err = a100.run(client, f"bash {shlex.quote(remote)} {shlex.quote(project)}", timeout=300)
        safe = ((out or "") + ("\n" + err if err else "")).replace(project, "[project]").replace(data_root, "[data-root]")
        safe = re.sub(r"postgresql(?:\+\w+)?://[^\s'\"<>]+", "[DATABASE_URL_REDACTED]", safe)
        print(json.dumps({"returncode": rc, "output": safe}, ensure_ascii=False, indent=2))
    finally:
        client.close()


if __name__ == "__main__":
    main()
