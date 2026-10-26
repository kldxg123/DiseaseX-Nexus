#!/usr/bin/env python3
"""Phase A1 read-only identity capture for kg_r3c. Writes 01_ACTIVE_IDENTITY.json."""
import json, os, subprocess, sys, hashlib, datetime

ROOT = "USER_HOME/projects/openclaw"
PSQL = os.path.join(ROOT, "runtime/postgres18x/bin/psql")
SOCK = os.path.join(ROOT, "runtime/postgres-run")
PORT = "55432"
REPORT_DIR = sys.argv[1]

def sh(cmd, cwd=ROOT, timeout=120):
    p = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return {"cmd": cmd if "PASSWORD" not in cmd.upper() else "<redacted-cmd>",
            "rc": p.returncode, "stdout": p.stdout.strip(), "stderr": p.stderr.strip()[-2000:]}

def psql(sql, db="epidemic_openclaw", timeout=120):
    return sh(f'"{PSQL}" -h "{SOCK}" -p {PORT} -U postgres -d {db} -tAX -c "{sql}"', timeout=timeout)

def psql_rows(sql, db="epidemic_openclaw"):
    r = psql(sql, db)
    if r["rc"] != 0:
        return {"error": r["stderr"]}
    return [l.split("|") for l in r["stdout"].splitlines() if l]

out = {}
out["captured_at_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
out["host"] = sh("hostname")["stdout"]
out["user"] = sh("whoami")["stdout"]
out["uname"] = sh("uname -a")["stdout"]

# git
out["git"] = {
    "root": sh("git rev-parse --show-toplevel")["stdout"],
    "branch": sh("git branch --show-current")["stdout"],
    "head": sh("git rev-parse HEAD")["stdout"],
    "status_porcelain_head": sh("git status --porcelain | head -40")["stdout"].splitlines(),
    "worktrees": sh("git worktree list")["stdout"].splitlines(),
}

# python env
venv_py = os.path.join(ROOT, ".venv/bin/python")
out["python"] = {
    "venv_python": sh(f'"{venv_py}" --version')["stdout"],
    "pip_freeze_sha256": hashlib.sha256(
        sh(f'"{venv_py}" -m pip freeze 2>/dev/null | sort')["stdout"].encode()).hexdigest(),
    "pip_freeze_count": len(sh(f'"{venv_py}" -m pip freeze 2>/dev/null')["stdout"].splitlines()),
}

# alembic
out["alembic"] = {
    "heads": sh(f'"{venv_py}" -m alembic heads 2>&1')["stdout"].splitlines(),
    "db_version": psql_rows("select version_num from public.alembic_version") if psql("select 1 from information_schema.tables where table_schema='public' and table_name='alembic_version'")["stdout"] == "1" else "no public.alembic_version",
}

# postgres server info
out["postgres"] = {
    "version": psql("show server_version")["stdout"],
    "data_checksums": psql("show data_checksums")["stdout"],
    "databases": [r[0] for r in psql_rows("select datname from pg_database where not datistemplate order by 1") if isinstance(r, list)],
    "schemas": [r[0] for r in psql_rows("select schema_name from information_schema.schemata order by 1") if isinstance(r, list)],
}

# table counts per schema
counts = {}
for sch in ["epidemic", "epidemic_kg", "epidemic_ebola", "epidemic_p1_12", "epidemic_preview", "public"]:
    rows = psql_rows(f"select count(*) from information_schema.tables where table_schema='{sch}'")
    counts[sch] = rows[0][0] if rows and isinstance(rows, list) else "?"
out["schema_table_counts"] = counts

# services
out["services"] = {
    "uvicorn": sh("ps -o pid,ppid,etime,args -p 2710768 2>/dev/null")["stdout"].splitlines(),
    "postgres_pid": sh("pgrep -f 'postgres -D runtime/postgres-data' | head -3")["stdout"].splitlines(),
    "listeners": sh("ss -ltnp 2>/dev/null | grep -E '8020|55432'")["stdout"].splitlines(),
    "dashboard_pid_file": sh("cat runtime/dashboard.pid 2>/dev/null")["stdout"],
}

# health endpoints
out["health"] = {
    "healthz": sh("curl -s -o /dev/null -w '%{http_code}' --max-time 10 http://127.0.0.1:8020/healthz"),
    "readyz": sh("curl -s -o /dev/null -w '%{http_code}' --max-time 10 http://127.0.0.1:8020/readyz"),
    "root_page": sh("curl -s -o /dev/null -w '%{http_code}' --max-time 10 http://127.0.0.1:8020/"),
    "kg_page": sh("curl -s -o /dev/null -w '%{http_code}' --max-time 10 'http://127.0.0.1:8020/forecast-model-knowledge-graph'"),
}

with open(os.path.join(REPORT_DIR, "01_ACTIVE_IDENTITY.json"), "w") as f:
    json.dump(out, f, indent=2, ensure_ascii=False)
print("identity written")
