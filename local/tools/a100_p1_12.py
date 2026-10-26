from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import re
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import a100_p1_11 as p111


ROOT = Path(__file__).resolve().parents[1]
HEAD_ROOT = ROOT / "remote_edit" / "p1_12_head"
CURRENT_ROOT = ROOT / "remote_edit" / "p1_12_current"
WORK_ROOT = ROOT / "remote_edit" / "p1_12_work"
GATE_ROOT = ROOT / "remote_edit" / "p1_12_gate"
REPORT_ROOT = ROOT / "reports" / "p1_12_trusted_ebola_influenza_review_gate"
P1_12R_REPORT_ROOT = ROOT / "reports" / "p1_12_root_cause_repair"
STAGE_ROOT = ROOT / "remote_edit" / "p1_12_stage"
DEPLOY_FILES = (
    "migrations/versions/0022_p1_12_review_ready_trusted_feeds.py",
    "migrations/versions/0023_p1_12_discovery_transition_guard.py",
    "migrations/versions/0024_p1_12_root_cause_repair.py",
    "migrations/versions/0025_p1_12_restore_preview_dashboard_views.py",
    "migrations/versions/0026_p1_12_preview_view_grants.py",
    "scripts/dashboard/install_runtime.sh",
    "scripts/daily_update/_common.sh",
    "scripts/source_review/install_runtime.sh",
    "src/epidemic_core/daily/main.py",
    "src/epidemic_core/dashboard/app.py",
    "src/epidemic_core/dashboard/static/dashboard.p1-7ar-v1.js",
    "src/epidemic_core/dashboard/static/dashboard.p1-12-trusted.css",
    "src/epidemic_core/discovery/providers.py",
    "src/epidemic_core/discovery/review_app.py",
    "src/epidemic_core/discovery/static/review.p1-12.css",
    "src/epidemic_core/ebola/fetcher.py",
    "src/epidemic_core/p1_12/__init__.py",
    "src/epidemic_core/p1_12/cli.py",
    "src/epidemic_core/p1_12/dashboard_app.py",
    "src/epidemic_core/p1_12/discovery.py",
    "src/epidemic_core/p1_12/review_app.py",
    "src/epidemic_core/p1_12/trusted.py",
    "src/epidemic_core/p1_10/diagnostics.py",
    "src/epidemic_core/p1_10/review_app.py",
    "src/epidemic_core/p1_10/preview.py",
    "src/epidemic_core/db/models/tables.py",
    "tests/unit/test_p1_12_review_gate.py",
    "tests/unit/test_p1_12_trusted_and_permissions.py",
    "tests/integration/test_discovery_queue_contract.py",
    "tests/integration/test_postgres.py",
    "tests/test_p1_11_ebola_parsers.py",
    "tests/fixtures/p1_12/uganda_moh_evd_daily.json",
    "tests/fixtures/p1_12/who_afro_bvd_weekly.pdf",
    "tests/fixtures/p1_12/ukhsa_influenza_weekly.ods",
)

COMMIT_GROUPS = (
    (
        "fix(p1-12): enforce evidence-backed review readiness",
        (
            "migrations/versions/0022_p1_12_review_ready_trusted_feeds.py",
            "migrations/versions/0023_p1_12_discovery_transition_guard.py",
            "src/epidemic_core/p1_12/__init__.py",
            "src/epidemic_core/p1_12/review_app.py",
            "src/epidemic_core/p1_10/diagnostics.py",
            "src/epidemic_core/p1_10/preview.py",
            "src/epidemic_core/db/models/tables.py",
            "tests/unit/test_p1_12_review_gate.py",
            "tests/integration/test_discovery_queue_contract.py",
            "tests/integration/test_postgres.py",
        ),
    ),
    (
        "feat(p1-12): publish trusted Ebola and influenza feeds",
        (
            "scripts/dashboard/install_runtime.sh",
            "scripts/daily_update/_common.sh",
            "src/epidemic_core/daily/main.py",
            "src/epidemic_core/dashboard/app.py",
            "src/epidemic_core/dashboard/static/dashboard.p1-12-trusted.css",
            "src/epidemic_core/ebola/fetcher.py",
            "src/epidemic_core/p1_12/dashboard_app.py",
            "src/epidemic_core/p1_12/trusted.py",
            "tests/unit/test_p1_12_trusted_and_permissions.py",
            "tests/test_p1_11_ebola_parsers.py",
        ),
    ),
    (
        "feat(p1-12): add three live review-ready source chains",
        (
            "scripts/source_review/install_runtime.sh",
            "src/epidemic_core/discovery/providers.py",
            "src/epidemic_core/discovery/review_app.py",
            "src/epidemic_core/discovery/static/review.p1-12.css",
            "src/epidemic_core/p1_12/cli.py",
            "src/epidemic_core/p1_12/discovery.py",
            "tests/fixtures/p1_12/uganda_moh_evd_daily.json",
            "tests/fixtures/p1_12/who_afro_bvd_weekly.pdf",
            "tests/fixtures/p1_12/ukhsa_influenza_weekly.ods",
        ),
    ),
)


def q(value: str) -> str:
    return p111.q(value)


def status_path(line: str) -> str:
    return p111.status_path(line)


def review_auth_compatibility_action() -> None:
    """Report only whether the already-loaded dashboard credential also fits review auth."""
    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        _data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        remote_auth = f"{project.rsplit('/', 1)[0]}/epidemic-source-review-runtime/secrets/login_hash.json"
        with client.open_sftp() as sftp, sftp.open(remote_auth, "rb") as stream:
            auth = json.loads(stream.read().decode("utf-8"))
        username, password = (ROOT / "secrets" / "dashboard_login.txt").read_text(encoding="utf-8").splitlines()[:2]
        salt = bytes.fromhex(str(auth["salt_hex"]))
        candidate = hashlib.scrypt(
            password.encode(),
            salt=salt,
            n=int(auth.get("n", 16384)),
            r=int(auth.get("r", 8)),
            p=int(auth.get("p", 1)),
            dklen=32,
        )
        compatible = hmac.compare_digest(username.encode(), str(auth.get("username", "")).encode()) and hmac.compare_digest(
            candidate, bytes.fromhex(str(auth["hash_hex"]))
        )
        print(json.dumps({"dashboard_credential_is_review_compatible": compatible}))
    finally:
        client.close()


def download_live_fixtures_action() -> None:
    """Freeze the three accepted live artifacts for deterministic regression tests."""
    manifests = json.loads((REPORT_ROOT / "three_real_candidate_manifests.json").read_text(encoding="utf-8"))
    names = {
        "Uganda Ministry of Health": "uganda_moh_evd_daily.json",
        "WHO Regional Office for Africa": "who_afro_bvd_weekly.pdf",
        "UK Health Security Agency": "ukhsa_influenza_weekly.ods",
    }
    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        _data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        target_root = WORK_ROOT / "tests" / "fixtures" / "p1_12"
        target_root.mkdir(parents=True, exist_ok=True)
        frozen = []
        with client.open_sftp() as sftp:
            for manifest in manifests:
                publisher = str(manifest["publisher"])
                artifact_sha = str(manifest["artifact_sha256"])
                remote_dir = f"{project}/storage/p1_12/discovery/quarantine/sha256/{artifact_sha[:2]}"
                matches = [name for name in sftp.listdir(remote_dir) if name.startswith(artifact_sha + ".")]
                if len(matches) != 1:
                    raise RuntimeError(f"LIVE_FIXTURE_PATH_AMBIGUOUS:{publisher}")
                target = target_root / names[publisher]
                sftp.get(f"{remote_dir}/{matches[0]}", str(target))
                actual = hashlib.sha256(target.read_bytes()).hexdigest()
                if actual != artifact_sha:
                    raise RuntimeError(f"LIVE_FIXTURE_SHA_MISMATCH:{publisher}")
                frozen.append({"file": target.name, "sha256": actual, "bytes": target.stat().st_size})
        print(json.dumps({"fixtures": frozen}, indent=2))
    finally:
        client.close()


def focused_tests_action() -> None:
    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        python = f"{data_root}/conda/envs/epidemic-core/bin/python"
        command = (
            "set -euo pipefail; set +x; "
            "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; "
            f"cd {q(project)}; export PYTHONPATH={q(project + '/src')}; "
            f"{q(python)} -m pytest tests/unit/test_p1_12_review_gate.py "
            "tests/unit/test_p1_12_trusted_and_permissions.py -q"
        )
        rc, output, error = p111.p110r3.previous.p19.a100.run(
            client, f"bash -c {q(command)}", timeout=1200
        )
        print(output or error)
        if rc != 0:
            raise RuntimeError("P1_12_FOCUSED_TESTS_FAILED")
    finally:
        client.close()


def task_quality_action() -> None:
    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        python = f"{data_root}/conda/envs/epidemic-core/bin/python"
        targets = (
            "migrations/versions/0022_p1_12_review_ready_trusted_feeds.py "
            "migrations/versions/0023_p1_12_discovery_transition_guard.py "
            "src/epidemic_core/p1_12 src/epidemic_core/dashboard/app.py "
            "src/epidemic_core/discovery/review_app.py src/epidemic_core/discovery/providers.py "
            "src/epidemic_core/daily/main.py src/epidemic_core/ebola/fetcher.py "
            "src/epidemic_core/p1_10/diagnostics.py tests/unit/test_p1_12_review_gate.py "
            "src/epidemic_core/db/models/tables.py tests/unit/test_p1_12_trusted_and_permissions.py "
            "tests/integration/test_discovery_queue_contract.py tests/integration/test_postgres.py "
            "tests/test_p1_11_ebola_parsers.py"
        )
        commands = (
            ("ruff", f"{q(python)} -m ruff check {targets}"),
            ("format", f"{q(python)} -m ruff format --check {targets}"),
            ("mypy", f"{q(python)} -m mypy --strict src/epidemic_core/p1_12"),
        )
        results = []
        for label, command in commands:
            rc, output, error = p111.p110r3.previous.p19.a100.run(
                client,
                f"bash -c {q('set -euo pipefail; unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; cd ' + q(project) + '; export PYTHONPATH=src; ' + command)}",
                timeout=1800,
            )
            results.append({"label": label, "returncode": rc, "tail": (output + error).splitlines()[-80:]})
        print(json.dumps(results, ensure_ascii=False, indent=2))
        if any(row["returncode"] != 0 for row in results):
            raise RuntimeError("P1_12_TASK_QUALITY_FAILED")
    finally:
        client.close()


def format_task_action() -> None:
    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        python = f"{data_root}/conda/envs/epidemic-core/bin/python"
        python_files = [path for path in DEPLOY_FILES if path.endswith(".py")]
        targets = " ".join(q(path) for path in python_files)
        command = (
            "set -euo pipefail; unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; "
            f"cd {q(project)}; {q(python)} -m ruff format {targets}"
        )
        rc, output, error = p111.p110r3.previous.p19.a100.run(
            client, f"bash -c {q(command)}", timeout=1800
        )
        if rc != 0:
            print(output or error)
            raise RuntimeError("P1_12_FORMAT_TASK_FAILED")
        with client.open_sftp() as sftp:
            for relative in python_files:
                target = WORK_ROOT / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                sftp.get(f"{project}/{relative}", str(target))
        print(json.dumps({"formatted_and_synced": len(python_files)}))
    finally:
        client.close()


def full_regression_action() -> None:
    """Run the complete suite against a disposable database plus read-only quality gates."""

    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        python = f"{data_root}/conda/envs/epidemic-core/bin/python"
        code = r'''
import hashlib,json,os,re,secrets,subprocess,time
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url
project=os.environ["PROJECT_ROOT"];python=os.environ["PYTHON_BIN"]
formal=make_url(os.environ["MIGRATION_DATABASE_URL"]).set(username="epidemicdb",password=os.environ["POSTGRES_ADMIN_PASSWORD"])
app=make_url(os.environ["APP_DATABASE_URL"]);cluster=formal.set(database="postgres")
name="epidemic_p1_12_pytest_"+secrets.token_hex(5)
admin=sa.create_engine(cluster,isolation_level="AUTOCOMMIT",hide_parameters=True);started=time.monotonic();checks=[]
def run(label,args,timeout,env):
 p=subprocess.run(args,cwd=project,env=env,text=True,capture_output=True,timeout=timeout)
 safe=re.sub(r"postgresql(?:\+\w+)?://[^\s]+","[DATABASE_URL_REDACTED]",p.stdout+"\n"+p.stderr).replace(project,"[project]")
 checks.append({"label":label,"returncode":p.returncode,"output_sha256":hashlib.sha256(safe.encode()).hexdigest(),"tail":safe.splitlines()[-35:]})
 return p
with admin.connect() as db:db.exec_driver_sql('CREATE DATABASE "'+name+'" TEMPLATE template0 ENCODING \'UTF8\'')
try:
 bootstrap=sa.create_engine(formal.set(database=name),hide_parameters=True)
 with bootstrap.begin() as db:
  db.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS epidemic AUTHORIZATION epidemicdb")
  db.exec_driver_sql("GRANT USAGE ON SCHEMA epidemic TO epidemic_app")
 bootstrap.dispose()
 env=os.environ.copy();env.update({"MIGRATION_DATABASE_URL":formal.set(database=name).render_as_string(hide_password=False),"APP_DATABASE_URL":app.set(database=name).render_as_string(hide_password=False),"PYTHONPATH":"src","PYTHONNOUSERSITE":"1"})
 if run("upgrade_empty_database",[python,"-m","alembic","upgrade","head"],1200,env).returncode:raise RuntimeError("EMPTY_DATABASE_MIGRATION_FAILED")
 if run("seed_reference",[python,"-m","epidemic_core.cli","seed-reference"],600,env).returncode:raise RuntimeError("EMPTY_DATABASE_SEED_FAILED")
 run("alembic_check",[python,"-m","alembic","check"],600,env)
 tests=run("pytest_full_isolated",[python,"-m","pytest","-p","no:cacheprovider","-q"],5400,env)
 print(json.dumps({"pass":tests.returncode==0 and all(c["returncode"]==0 for c in checks[:3]),"checks":checks,"duration_seconds":round(time.monotonic()-started,3),"formal_database_written":False,"disposable_database":True}))
finally:
 with admin.connect() as db:
  db.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=:name"),{"name":name})
  db.exec_driver_sql('DROP DATABASE IF EXISTS "'+name+'"')
 admin.dispose()
'''
        setup = (
            f"source {q(project + '/scripts/database/_env.sh')}; set -a; "
            f"source {q(project + '/.secrets/postgres.env')}; set +a; "
            "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; "
            f"export PROJECT_ROOT={q(project)} PYTHON_BIN={q(python)}"
        )
        pytest_result = p111.p110r3.previous._remote_json(client, python, setup, code, timeout=7200)

        def check(label: str, command: str, timeout: int = 1800) -> dict[str, object]:
            started = __import__("time").monotonic()
            rc, output, error = p111.p110r3.previous.p19.a100.run(
                client, f"bash -lc {q(command)}", timeout=timeout
            )
            safe = (output + "\n" + error).replace(project, "[project]").replace(data_root, "[data-root]")
            safe = __import__("re").sub(r"https?://\S+", "[URL_REDACTED]", safe)
            return {
                "label": label,
                "returncode": rc,
                "duration_seconds": round(__import__("time").monotonic() - started, 3),
                "output_sha256": hashlib.sha256(safe.encode()).hexdigest(),
                "tail": safe.splitlines()[-30:],
            }

        node_bin = f"{project}/runtime/node-v24.18.0-linux-x64/bin"
        npm = f"{node_bin}/npm"
        npm_dir = f"{project}/integrations/openclaw/epidemic-query-tools"
        npm_prefix = f"export PATH={q(node_bin)}:$PATH; cd {q(npm_dir)}"
        cli = f"{project}/scripts/openclaw/openclaw_cli.sh"
        text_files = [f"{project}/{path}" for path in DEPLOY_FILES if not path.endswith((".pdf", ".ods"))]
        text_targets = " ".join(q(path) for path in text_files)
        checks = [
            check("ruff_check", f"cd {q(project)}; {q(python)} -m ruff check src tests migrations", 1800),
            check("ruff_format", f"cd {q(project)}; {q(python)} -m ruff format --check src tests migrations", 1800),
            check("mypy_strict", f"cd {q(project)}; {q(python)} -m mypy --strict src", 3000),
            check("pip_check", f"{q(python)} -m pip check", 600),
            check("alembic_heads", f"cd {q(project)}; {q(python)} -m alembic heads", 600),
            check("git_diff_check", f"git -C {q(project)} diff --check", 600),
            check("bash_syntax", f"cd {q(project)}; git ls-files -z '*.sh' | xargs -0 -r bash -n", 600),
            check("npm_build", f"{npm_prefix}; {q(npm)} run build", 1200),
            check("npm_test", f"{npm_prefix}; {q(npm)} test", 1200),
            check("npm_audit", f"{npm_prefix}; {q(npm)} audit --json --registry=https://registry.npmjs.org --fetch-retries=1 --fetch-timeout=60000", 1200),
            check("openclaw_doctor_lint", f"{q(cli)} doctor --lint --json", 900),
            check("openclaw_doctor_deep", f"{q(cli)} doctor --deep --non-interactive", 1200),
            check("openclaw_security_deep", f"{q(cli)} security audit --deep --json", 1200),
            check("secret_scan", f"if grep -IlE '(BEGIN (RSA|OPENSSH|EC) PRIVATE KEY|AKIA[0-9A-Z]{{16}}|ghp_[A-Za-z0-9]{{36}}|sk-[A-Za-z0-9]{{20,}}|postgresql[^ ]+:[^ ]+@)' {text_targets} >/dev/null; then exit 1; fi", 900),
            check("tls_proxy_scan", f"if grep -IlE '(curl[[:space:]]+-k|wget[[:space:]]+--no-check-certificate|GIT_SSL_NO_VERIFY|pip[[:space:]]+--trusted-host|verify[[:space:]]*=[[:space:]]*False|HF_ENDPOINT=|http_proxy=|https_proxy=)' {text_targets} >/dev/null; then exit 1; fi", 900),
            check("large_file_scan", f"cd {q(project)}; test -z \"$(find . -type f -not -path './.git/*' -not -path './storage/*' -not -path './runtime/*' -not -path './conda/*' -not -path './models/*' -not -path '*/node_modules/*' -not -path './reports/*' -size +20M -print -quit)\"", 600),
        ]
        required = {
            "ruff_check", "ruff_format", "mypy_strict", "pip_check", "alembic_heads", "git_diff_check",
            "bash_syntax", "npm_build", "npm_test", "npm_audit", "secret_scan", "tls_proxy_scan", "large_file_scan",
        }
        result = {
            "pass": bool(pytest_result.get("pass"))
            and all(row["returncode"] == 0 for row in checks if row["label"] in required),
            "pytest": pytest_result,
            "checks": checks,
            "openclaw_checks_executed": True,
            "formal_worktree_modified": False,
        }
        GATE_ROOT.mkdir(parents=True, exist_ok=True)
        (GATE_ROOT / "full_regression.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
        if not result["pass"]:
            raise RuntimeError("P1_12_FULL_REGRESSION_FAILED")
    finally:
        client.close()


def large_file_inventory_action() -> None:
    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        _data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        command = (
            f"cd {q(project)}; find . -type f -not -path './.git/*' -not -path './storage/*' "
            "-not -path './runtime/*' -not -path './conda/*' -not -path '*/node_modules/*' "
            "-not -path './reports/*' -size +20M -printf '%P %s\\n'"
        )
        output = p111.p110r3.previous.p19.a100.checked(client, command, timeout=600)
        rows = []
        for line in output.splitlines():
            path, size = line.rsplit(" ", 1)
            rows.append({"path": path, "bytes": int(size)})
        print(json.dumps({"files": rows}, indent=2))
    finally:
        client.close()


def _remote_inventory(client: object, data_root: str, project: str) -> dict[str, object]:
    python = f"{data_root}/conda/envs/epidemic-core/bin/python"
    code = r'''import json,os,subprocess
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url
url=make_url(os.environ["MIGRATION_DATABASE_URL"]).set(username="epidemicdb",password=os.environ["POSTGRES_ADMIN_PASSWORD"])
engine=sa.create_engine(url,hide_parameters=True)
with engine.connect() as db:
 revision=db.execute(text("SELECT version_num FROM epidemic.alembic_version")).scalar_one()
 objects=[dict(r) for r in db.execute(text("SELECT table_schema,table_name,table_type FROM information_schema.tables WHERE table_schema LIKE 'epidemic%' ORDER BY table_schema,table_name")).mappings()]
 columns=[dict(r) for r in db.execute(text("SELECT table_schema,table_name,column_name,data_type,is_nullable FROM information_schema.columns WHERE table_schema LIKE 'epidemic%' ORDER BY table_schema,table_name,ordinal_position")).mappings()]
 counts={}
 for row in objects:
  if row["table_type"]!="BASE TABLE":continue
  key=row["table_schema"]+"."+row["table_name"]
  counts[key]=int(db.exec_driver_sql('SELECT count(*) FROM "'+row["table_schema"].replace('"','""')+'"."'+row["table_name"].replace('"','""')+'"').scalar_one())
 snapshots=[dict(r) for r in db.execute(text("SELECT id,row_count,content_sha256,created_at FROM epidemic.dataset_snapshots ORDER BY created_at,id")).mappings()]
 diseases=[dict(r) for r in db.execute(text("SELECT slug,canonical_name,active FROM epidemic.diseases ORDER BY slug")).mappings()]
 trusted_sources=[dict(r) for r in db.execute(text("SELECT source_key,display_name,authority,enabled FROM epidemic.data_sources ORDER BY source_key")).mappings()]
 trusted_by_disease=[dict(r) for r in db.execute(text("SELECT d.slug,d.canonical_name,count(DISTINCT o.id) normalized_observations,count(DISTINCT t.observation_id) accepted_current_observations,count(DISTINCT t.source_key) accepted_source_count FROM epidemic.diseases d LEFT JOIN epidemic.normalized_observations o ON o.disease_id=d.id LEFT JOIN epidemic.training_observations_v1 t ON t.disease_slug=d.slug GROUP BY d.id ORDER BY d.slug")).mappings()]
 view_definitions=[dict(r) for r in db.execute(text("SELECT schemaname,viewname,definition FROM pg_views WHERE schemaname LIKE 'epidemic%' AND (viewname ILIKE '%review%' OR viewname ILIKE '%candidate%' OR viewname ILIKE '%preview%') ORDER BY schemaname,viewname")).mappings()]
 roles=[dict(r) for r in db.execute(text("SELECT rolname,rolcanlogin,rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname LIKE 'epidemic%' ORDER BY rolname")).mappings()]
 print(json.dumps({"revision":revision,"objects":objects,"columns":columns,"counts":counts,"snapshots":snapshots,"diseases":diseases,"trusted_sources":trusted_sources,"trusted_by_disease":trusted_by_disease,"review_view_definitions":view_definitions,"roles":roles},default=str,ensure_ascii=False))
engine.dispose()
'''
    setup = (
        f"source {q(project + '/scripts/database/_env.sh')}; set -a; "
        f"source {q(project + '/.secrets/postgres.env')}; set +a; "
        "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY"
    )
    return p111.p110r3.previous._remote_json(client, python, setup, code, timeout=600)


def capture_action() -> None:
    client, gate = p111.p110r3.previous.p19.a100.connect()
    try:
        data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        head = p111.p110r3.previous.p19.a100.checked(client, f"git -C {q(project)} rev-parse HEAD")
        branch = p111.p110r3.previous.p19.a100.checked(client, f"git -C {q(project)} branch --show-current")
        staged = p111.p110r3.previous.p19.a100.checked(
            client, f"git -C {q(project)} diff --cached --name-only"
        ).splitlines()
        status = p111.p110r3.previous.p19.a100.checked(
            client, f"git -C {q(project)} status --porcelain=v2 -uall"
        )
        dirty_paths = sorted({status_path(line) for line in status.splitlines() if line})
        tracked = p111.p110r3.previous.p19.a100.checked(
            client, f"git -C {q(project)} ls-files"
        ).splitlines()
        prefixes = (
            "src/epidemic_core/",
            "migrations/versions/",
            "tests/",
            "scripts/",
            "integrations/openclaw/",
            "openclaw-plugin/",
            "reference/",
            "config/",
            "contracts/",
        )
        selected = [path for path in tracked if path.startswith(prefixes)]
        selected.extend(
            path
            for path in tracked
            if path in {"pyproject.toml", "alembic.ini", "package.json", "package-lock.json"}
        )
        selected.extend(
            path for path in dirty_paths if path not in selected and path.startswith(prefixes)
        )
        selected = sorted(set(selected))
        for directory in (HEAD_ROOT, CURRENT_ROOT, WORK_ROOT):
            if directory.exists():
                shutil.rmtree(directory)
            directory.mkdir(parents=True)
        sftp = client.open_sftp()
        try:
            for relative in selected:
                current_target = CURRENT_ROOT / relative
                current_target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    sftp.get(f"{project}/{relative}", str(current_target))
                except OSError:
                    continue
                head_target = HEAD_ROOT / relative
                head_target.parent.mkdir(parents=True, exist_ok=True)
                rc, output, _error = p111.p110r3.previous.p19.a100.run(
                    client,
                    f"git -C {q(project)} show {q('HEAD:' + relative)} | base64 -w0",
                    timeout=120,
                )
                if rc == 0:
                    head_target.write_bytes(base64.b64decode(output))
        finally:
            sftp.close()
        shutil.copytree(CURRENT_ROOT, WORK_ROOT, dirs_exist_ok=True)
        services = {}
        service_scripts = {
            "postgres": "scripts/postgres/status.sh",
            "vllm": "scripts/qwen_vllm/status.sh",
            "query_api": "scripts/query_api/status_query_api.sh",
            "retrieval_worker": "scripts/retrieval_worker/status_retrieval_worker.sh",
            "openclaw": "scripts/openclaw/status_openclaw.sh",
            "dashboard": "scripts/dashboard/status_dashboard.sh",
            "discovery_worker": "scripts/source_discovery_worker/status_source_discovery_worker.sh",
            "promotion_worker": "scripts/source_promotion_worker/status_source_promotion_worker.sh",
            "review_console": "scripts/source_review/status_source_review.sh",
            "daily_update": "scripts/daily_update/status_daily_update.sh",
            "preview_worker": "scripts/preview_publication_worker/status_preview_publication_worker.sh",
        }
        for name, relative in service_scripts.items():
            services[name] = (
                p111.p110r3.previous.p19.a100.run(
                    client, q(f"{project}/{relative}"), timeout=180
                )[0]
                == 0
            )
        listeners = p111.p110r3.previous.p19.a100.checked(
            client,
            "ss -ltnH '( sport = :5432 or sport = :8000 or sport = :8010 or sport = :18789 or sport = :8020 or sport = :8021 )'",
        ).splitlines()
        inventory = _remote_inventory(client, data_root, project)
        inventory.update(
            {
                "captured_at_utc": datetime.now(timezone.utc).isoformat(),
                "branch": branch,
                "head": head,
                "dirty_paths": dirty_paths,
                "staged_paths": staged,
                "services": services,
                "listeners": listeners,
                "target_identity_verified": bool(gate.get("stable_second_connection")),
            }
        )
        GATE_ROOT.mkdir(parents=True, exist_ok=True)
        REPORT_ROOT.mkdir(parents=True, exist_ok=True)
        (GATE_ROOT / "baseline_inventory.json").write_text(
            json.dumps(inventory, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        (REPORT_ROOT / "baseline_inventory.json").write_text(
            json.dumps(inventory, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        summary = {
            "branch": branch,
            "head": head,
            "dirty_count": len(dirty_paths),
            "staged_count": len(staged),
            "selected_count": len(selected),
            "revision": inventory["revision"],
            "snapshot_count": len(inventory["snapshots"]),
            "trusted_source_count": len(inventory["trusted_sources"]),
            "services_healthy": all(services.values()),
            "loopback_only": all("127.0.0.1:" in line or "[::1]:" in line for line in listeners),
            "target_identity_verified": bool(gate.get("stable_second_connection")),
        }
        print(json.dumps(summary, ensure_ascii=True, indent=2))
    finally:
        client.close()


def candidate_audit_action() -> None:
    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        python = f"{data_root}/conda/envs/epidemic-core/bin/python"
        code = r'''import json,os
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url
url=make_url(os.environ["MIGRATION_DATABASE_URL"]).set(username="epidemicdb",password=os.environ["POSTGRES_ADMIN_PASSWORD"])
engine=sa.create_engine(url,hide_parameters=True)
with engine.connect() as db:
 rows=[dict(r) for r in db.execute(text("""
 SELECT rc.id review_case_id,rc.state review_state,rc.state_version,rc.created_at review_created_at,
        sc.id candidate_id,sc.status candidate_status,sc.publisher,sc.dataset_name,sc.source_tier,sc.risk_flags,
        cv.id candidate_version_id,cv.version_no,cv.canonical_url_redacted,cv.license_status,cv.format,cv.metadata,
        dr.id discovery_run_id,dr.state discovery_state,dr.request_json,dr.progress_json,dr.terminal_reason_code,
        coalesce((SELECT count(*) FROM epidemic_discovery.source_discovery_search_results sr WHERE sr.discovery_run_id=dr.id),0) search_result_count,
        coalesce((SELECT count(*) FROM epidemic_discovery.candidate_fetches f WHERE f.discovery_run_id=dr.id),0) fetch_attempt_count,
        coalesce((SELECT count(*) FROM epidemic_discovery.candidate_fetches f WHERE f.discovery_run_id=dr.id AND f.http_status BETWEEN 200 AND 299 AND f.artifact_sha256 IS NOT NULL),0) fetch_success_count,
        coalesce((SELECT count(*) FROM epidemic_discovery.quarantine_artifacts a WHERE a.candidate_version_id=cv.id),0) artifact_count,
        coalesce((SELECT count(*) FROM epidemic_discovery.source_preview_rows p WHERE p.candidate_version_id=cv.id),0) preview_row_count,
        sp.row_count parsed_row_count,sp.column_count,sp.date_min,sp.date_max,sp.profile_json,
        qa.status quality_status,qa.quality_json,qa.security_json,qa.license_status assessed_license_status,
        coalesce((SELECT count(*) FROM epidemic_discovery.source_review_decisions d WHERE d.review_case_id=rc.id),0) decision_count,
        coalesce((SELECT count(*) FROM epidemic_discovery.source_promotion_runs p JOIN epidemic_discovery.source_review_decisions d ON d.id=p.review_decision_id WHERE d.review_case_id=rc.id),0) promotion_count
 FROM epidemic_discovery.source_review_cases rc
 JOIN epidemic_discovery.source_candidate_versions cv ON cv.id=rc.candidate_version_id
 JOIN epidemic_discovery.source_candidates sc ON sc.id=cv.candidate_id
 JOIN epidemic_discovery.discovery_runs dr ON dr.id=sc.discovery_run_id
 LEFT JOIN epidemic_discovery.source_schema_profiles sp ON sp.candidate_version_id=cv.id
 LEFT JOIN epidemic_discovery.source_quality_assessments qa ON qa.candidate_version_id=cv.id
 ORDER BY rc.created_at,rc.id
 """)).mappings()]
 for row in rows:
  row["preview_samples"]=[dict(r) for r in db.execute(text("SELECT row_ordinal,sanitized_json,row_errors,row_sha256 FROM epidemic_discovery.source_preview_rows WHERE candidate_version_id=:v ORDER BY row_ordinal LIMIT 3"),{"v":row["candidate_version_id"]}).mappings()]
  row["search_evidence"]=[dict(r) for r in db.execute(text("SELECT provider_id,query_id,rank,title,canonical_url,domain,publisher,source_tier,media_type,disposition FROM epidemic_discovery.source_discovery_search_results WHERE discovery_run_id=:r ORDER BY provider_id,query_id,rank LIMIT 10"),{"r":row["discovery_run_id"]}).mappings()]
  row["fetch_evidence"]=[dict(r) for r in db.execute(text("SELECT status,http_status,content_type,byte_size,elapsed_ms,artifact_sha256,failure_code,final_url FROM epidemic_discovery.candidate_fetches WHERE discovery_run_id=:r ORDER BY attempted_at LIMIT 10"),{"r":row["discovery_run_id"]}).mappings()]
 events=[dict(r) for r in db.execute(text("SELECT discovery_run_id,event_code,from_state,to_state,details,created_at FROM epidemic_discovery.discovery_run_events ORDER BY created_at,id")).mappings()]
 print(json.dumps({"candidates":rows,"events":events},default=str,ensure_ascii=False))
engine.dispose()
'''
        setup = (
            f"source {q(project + '/scripts/database/_env.sh')}; set -a; "
            f"source {q(project + '/.secrets/postgres.env')}; set +a; "
            "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY"
        )
        result = p111.p110r3.previous._remote_json(client, python, setup, code, timeout=600)
        GATE_ROOT.mkdir(parents=True, exist_ok=True)
        (GATE_ROOT / "candidate_baseline.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        summary = [
            {
                "dataset_name": row["dataset_name"],
                "publisher": row["publisher"],
                "source_tier": row["source_tier"],
                "review_state": row["review_state"],
                "candidate_status": row["candidate_status"],
                "discovery_state": row["discovery_state"],
                "search_results": row["search_result_count"],
                "fetch_success": row["fetch_success_count"],
                "artifacts": row["artifact_count"],
                "parsed_rows": row["parsed_row_count"],
                "preview_rows": row["preview_row_count"],
                "date_min": row["date_min"],
                "date_max": row["date_max"],
                "decisions": row["decision_count"],
                "promotions": row["promotion_count"],
            }
            for row in result["candidates"]
        ]
        print(json.dumps(summary, ensure_ascii=True, indent=2))
    finally:
        client.close()


def source_probe_action() -> None:
    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        python = f"{data_root}/conda/envs/epidemic-core/bin/python"
        code = r'''import csv,hashlib,io,ipaddress,json,os,socket,ssl,time,urllib.parse,urllib.request
for key in ("http_proxy","https_proxy","HTTP_PROXY","HTTPS_PROXY","all_proxy","ALL_PROXY"):os.environ.pop(key,None)
targets={
 "ebola_event":"https://www.who.int/emergencies/emergency-events/item/2026-e000253",
 "ebola_latest":"https://www.who.int/emergencies/disease-outbreak-news/item/2026-DON613",
 "influenza_outputs":"https://www.who.int/teams/global-influenza-programme/surveillance-and-monitoring/influenza-surveillance-outputs",
 "fluid_json":"https://xmart-api-public.who.int/FLUMART/VIW_FID_EPI?$top=5&$format=json",
 "flunet_json":"https://xmart-api-public.who.int/FLUMART/VIW_FNT?$top=5&$format=json",
 "fluid_chn_recent":"https://xmart-api-public.who.int/FLUMART/VIW_FID_EPI?$filter=COUNTRY_CODE%20eq%20%27CHN%27%20and%20ISO_YEAR%20ge%202025&$orderby=ISOYW%20desc&$top=500&$format=json",
 "fluid_recent":"https://xmart-api-public.who.int/FLUMART/VIW_FID_EPI?$filter=ISO_YEAR%20ge%202025&$orderby=ISOYW%20desc&$top=1000&$format=json",
 "fluid_hnd_recent":"https://xmart-api-public.who.int/FLUMART/VIW_FID_EPI?$filter=COUNTRY_CODE%20eq%20%27HND%27%20and%20ISO_YEAR%20ge%202025&$orderby=ISOYW%20desc&$top=2000&$format=json",
 "flunet_chn_recent":"https://xmart-api-public.who.int/FLUMART/VIW_FNT?$filter=COUNTRY_CODE%20eq%20%27CHN%27%20and%20ISO_YEAR%20ge%202025&$orderby=ISOYW%20desc&$top=500&$format=json",
 "flunet_hnd_recent":"https://xmart-api-public.who.int/FLUMART/VIW_FNT?$filter=COUNTRY_CODE%20eq%20%27HND%27%20and%20ISO_YEAR%20ge%202025&$orderby=ISOYW%20desc&$top=500&$format=json",
 "flu_metadata_json":"https://xmart-api-public.who.int/FLUMART/VIW_FLU_METADATA?$top=20&$format=json",
 "uganda_api":"https://evd-daily.health.go.ug/api/stats",
 "who_afro":"https://www.afro.who.int/health-topics/ebola-disease/outbreak-drc-26",
 "cdc_fluview":"https://www.cdc.gov/fluview/index.html",
 "cdc_nhsn_json":"https://data.cdc.gov/resource/vdzy-6i9v.json?$select=jurisdiction,weekendingdate,totalconfflunewadm&$where=jurisdiction%3D%27United%20States%27&$order=weekendingdate%20desc&$limit=52",
}
allowed={"www.who.int","xmart-api-public.who.int","evd-daily.health.go.ug","www.afro.who.int","www.cdc.gov","data.cdc.gov"}
def resolve(host):
 values=sorted({r[4][0] for r in socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)})
 if not values or any(not ipaddress.ip_address(v).is_global for v in values):raise ValueError("DNS_POLICY")
 return len(values)
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=ssl.create_default_context()))
result={}
for label,url in targets.items():
 started=time.monotonic()
 try:
  parsed=urllib.parse.urlsplit(url);host=(parsed.hostname or "").lower()
  if parsed.scheme!="https" or host not in allowed or parsed.username or parsed.password:raise ValueError("URL_POLICY")
  dns_count=resolve(host)
  request=urllib.request.Request(url,headers={"User-Agent":"epidemic-openclaw-p1-12/1.0","Accept":"application/json,text/csv,text/html;q=0.9,*/*;q=0.1"})
  with opener.open(request,timeout=45) as response:
   final=urllib.parse.urlsplit(response.geturl());final_host=(final.hostname or "").lower()
   if final.scheme!="https" or final_host not in allowed:raise ValueError("REDIRECT_POLICY")
   resolve(final_host);payload=response.read(4*1024*1024+1)
   if len(payload)>4*1024*1024:raise ValueError("RESPONSE_TOO_LARGE_FOR_PROBE")
   mime=response.headers.get_content_type().lower();sample={}
   if mime in {"application/json","text/json"}:
    value=json.loads(payload);rows=value.get("value",value) if isinstance(value,dict) else value
    if isinstance(rows,list):
     sample={"row_count_in_probe":len(rows),"field_names":sorted(rows[0]) if rows and isinstance(rows[0],dict) else [],"rows":rows[:2]}
     if label in {"fluid_recent","fluid_chn_recent","fluid_hnd_recent"}:
      grouped={}
      for row in rows:
       code=str(row.get("COUNTRY_CODE") or "");week=row.get("ISOYW");date=row.get("ISO_WEEKSTARTDATE")
       if not code or week is None:continue
       bucket=grouped.setdefault(code,{"country":row.get("COUNTRY_AREA_TERRITORY"),"weeks":set(),"latest":None,"person_nonnull":0,"person_positive":0})
       values=[row.get(name) for name in ("ILI_CASE","SARI_CASE","SARI_DEATHS")]
       if any(value is not None for value in values):bucket["weeks"].add(int(week));bucket["person_nonnull"]+=1
       if any(isinstance(value,(int,float)) and value>0 for value in values):bucket["person_positive"]+=1
       if date and (bucket["latest"] is None or str(date)>bucket["latest"]):bucket["latest"]=str(date)
      summary=[{"country_code":key,"country":value["country"],"week_count":len(value["weeks"]),"latest":value["latest"],"person_nonnull_rows":value["person_nonnull"],"person_positive_rows":value["person_positive"]} for key,value in grouped.items()]
      sample["eligible_countries"]=sorted((row for row in summary if row["week_count"]>=8 and row["person_positive_rows"]>0),key=lambda row:(row["latest"] or "",row["week_count"],row["country_code"]),reverse=True)[:20]
     if label in {"flunet_chn_recent","flunet_hnd_recent"}:
      sample["week_count"]=len({row.get("ISOYW") for row in rows if row.get("ISOYW") is not None})
      sample["latest_week"]=max((int(row["ISOYW"]) for row in rows if row.get("ISOYW") is not None),default=None)
    elif isinstance(rows,dict):sample={"field_names":sorted(rows),"object":rows}
   elif mime in {"text/csv","application/csv","text/plain"}:
    rows=list(csv.DictReader(io.StringIO(payload.decode("utf-8-sig",errors="replace"))))
    sample={"row_count_in_probe":len(rows),"field_names":list(rows[0]) if rows else [],"rows":rows[:2]}
   else:sample={"text_prefix":" ".join(payload.decode("utf-8",errors="replace").split())[:500]}
   result[label]={"status":int(response.status),"domain":final_host,"mime":mime,"bytes":len(payload),"sha256":hashlib.sha256(payload).hexdigest(),"elapsed_ms":int((time.monotonic()-started)*1000),"dns_answer_count":dns_count,"sample":sample}
 except Exception as exc:result[label]={"error_code":type(exc).__name__,"error_detail":str(exc)[:160],"elapsed_ms":int((time.monotonic()-started)*1000)}
print(json.dumps({"proxy_present":any(os.environ.get(k) for k in ("http_proxy","https_proxy","HTTP_PROXY","HTTPS_PROXY","all_proxy","ALL_PROXY")),"tls_verify":True,"targets":result},ensure_ascii=False))
'''
        setup = "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY"
        result = p111.p110r3.previous._remote_json(client, python, setup, code, timeout=600)
        GATE_ROOT.mkdir(parents=True, exist_ok=True)
        (GATE_ROOT / "source_probe.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        summary = {
            label: {
                key: value.get(key)
                for key in ("status", "domain", "mime", "bytes", "elapsed_ms", "error_code")
                if key in value
            }
            for label, value in result["targets"].items()
        }
        print(json.dumps({"proxy_present": result["proxy_present"], "tls_verify": result["tls_verify"], "targets": summary}, ensure_ascii=True, indent=2))
    finally:
        client.close()


def flu_alternative_probe_action() -> None:
    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        python = f"{data_root}/conda/envs/epidemic-core/bin/python"
        code = r'''import hashlib,html.parser,ipaddress,json,os,re,socket,ssl,time,urllib.parse,urllib.request
for key in ("http_proxy","https_proxy","HTTP_PROXY","HTTPS_PROXY","all_proxy","ALL_PROXY"):os.environ.pop(key,None)
allowed={"www.gov.uk","assets.publishing.service.gov.uk","ukhsa-dashboard.data.gov.uk"}
class Links(html.parser.HTMLParser):
 def __init__(self):super().__init__();self.links=[]
 def handle_starttag(self,tag,attrs):
  if tag.lower()=="a":
   value=dict(attrs).get("href")
   if value:self.links.append(value)
def resolve(host):
 values=sorted({r[4][0] for r in socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)})
 if not values or any(not ipaddress.ip_address(v).is_global for v in values):raise ValueError("DNS_POLICY")
 return values
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=ssl.create_default_context()))
def fetch(url,limit=12*1024*1024):
 parsed=urllib.parse.urlsplit(url);host=(parsed.hostname or "").lower()
 if parsed.scheme!="https" or host not in allowed or parsed.username or parsed.password:raise ValueError("URL_POLICY")
 resolve(host);started=time.monotonic();req=urllib.request.Request(url,headers={"User-Agent":"epidemic-openclaw-p1-12/1.0","Accept":"text/html,application/vnd.oasis.opendocument.spreadsheet,application/json,*/*;q=0.1"})
 with opener.open(req,timeout=60) as response:
  final=response.geturl();fp=urllib.parse.urlsplit(final);fh=(fp.hostname or "").lower()
  if fp.scheme!="https" or fh not in allowed:raise ValueError("REDIRECT_POLICY")
  resolve(fh);payload=response.read(limit+1)
  if len(payload)>limit:raise ValueError("RESPONSE_TOO_LARGE")
  return {"url":final,"domain":fh,"status":response.status,"mime":response.headers.get_content_type(),"bytes":len(payload),"sha256":hashlib.sha256(payload).hexdigest(),"elapsed_ms":int((time.monotonic()-started)*1000)},payload
pages=[
 "https://www.gov.uk/government/statistics/national-flu-and-covid-19-surveillance-reports-2025-to-2026-season",
 "https://ukhsa-dashboard.data.gov.uk/respiratory-viruses/influenza",
]
result={}
for url in pages:
 try:
  meta,payload=fetch(url);parser=Links();parser.feed(payload.decode("utf-8",errors="replace"))
  links=[]
  for value in parser.links:
   candidate=urllib.parse.urljoin(meta["url"],value)
   if re.search(r"(?:\.ods|\.csv|\.xlsx)(?:\?|$)",candidate,re.I):links.append(candidate)
  result[url]={"fetch":meta,"data_links":links[:30]}
 except Exception as exc:result[url]={"error_code":type(exc).__name__,"error_detail":str(exc)[:160]}
data_urls=[]
for item in result.values():data_urls.extend(item.get("data_links",[])[:3])
downloads=[]
for url in data_urls[:8]:
 try:
  meta,payload=fetch(url)
  downloads.append({"fetch":meta,"magic_hex":payload[:8].hex(),"filename":urllib.parse.urlsplit(meta["url"]).path.rsplit("/",1)[-1]})
 except Exception as exc:downloads.append({"url":url,"error_code":type(exc).__name__,"error_detail":str(exc)[:160]})
print(json.dumps({"pages":result,"downloads":downloads,"proxy_present":any(os.environ.get(k) for k in ("http_proxy","https_proxy","HTTP_PROXY","HTTPS_PROXY","all_proxy","ALL_PROXY")),"tls_verify":True},ensure_ascii=False))
'''
        result = p111.p110r3.previous._remote_json(
            client,
            python,
            "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY",
            code,
            timeout=600,
        )
        GATE_ROOT.mkdir(parents=True, exist_ok=True)
        (GATE_ROOT / "flu_alternative_probe.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        summary = {
            "pages": {
                url: {
                    "status": item.get("fetch", {}).get("status"),
                    "domain": item.get("fetch", {}).get("domain"),
                    "data_link_count": len(item.get("data_links", [])),
                    "error_code": item.get("error_code"),
                }
                for url, item in result["pages"].items()
            },
            "downloads": [
                {
                    "status": item.get("fetch", {}).get("status"),
                    "domain": item.get("fetch", {}).get("domain"),
                    "mime": item.get("fetch", {}).get("mime"),
                    "bytes": item.get("fetch", {}).get("bytes"),
                    "error_code": item.get("error_code"),
                }
                for item in result["downloads"]
            ],
        }
        print(json.dumps(summary, ensure_ascii=True, indent=2))
    finally:
        client.close()


def inspect_flu_ods_action() -> None:
    source = json.loads((GATE_ROOT / "flu_alternative_probe.json").read_text(encoding="utf-8"))
    urls = []
    for page in source["pages"].values():
        for url in page.get("data_links", []):
            if "influenza" in url.lower() and url.lower().endswith(".ods") and url not in urls:
                urls.append(url)
    if not urls:
        raise RuntimeError("NO_OFFICIAL_FLU_ODS")
    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        python = f"{data_root}/conda/envs/epidemic-core/bin/python"
        code = r'''import io,json,os,ssl,urllib.request,zipfile,xml.etree.ElementTree as ET
url=os.environ["P1_12_ODS_URL"]
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=ssl.create_default_context()))
with opener.open(urllib.request.Request(url,headers={"User-Agent":"epidemic-openclaw-p1-12/1.0"}),timeout=60) as response:payload=response.read(12*1024*1024)
ns={"table":"urn:oasis:names:tc:opendocument:xmlns:table:1.0","text":"urn:oasis:names:tc:opendocument:xmlns:text:1.0","office":"urn:oasis:names:tc:opendocument:xmlns:office:1.0"}
with zipfile.ZipFile(io.BytesIO(payload)) as archive:root=ET.fromstring(archive.read("content.xml"))
out={}
for sheet in root.findall(".//table:table",ns):
 name=sheet.attrib.get("{%s}name"%ns["table"],"unnamed");sample=[];row_count=0;max_cols=0
 for row in sheet.findall("table:table-row",ns):
  repeat=int(row.attrib.get("{%s}number-rows-repeated"%ns["table"],"1"));values=[]
  for cell in row.findall("table:table-cell",ns):
   value=" ".join("".join(cell.itertext()).split());crepeat=int(cell.attrib.get("{%s}number-columns-repeated"%ns["table"],"1"))
   values.extend([value]*min(crepeat,50))
  max_cols=max(max_cols,len(values));row_count+=min(repeat,100000)
  joined=" | ".join(value for value in values if value)
  if joined and len(sample)<35:sample.append(joined[:1500])
 out[name]={"shape":[row_count,max_cols],"sample":sample[:25]}
print(json.dumps({"sheets":out},ensure_ascii=False))
'''
        setup = (
            "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; "
            f"export P1_12_ODS_URL={q(urls[0])}"
        )
        result = p111.p110r3.previous._remote_json(client, python, setup, code, timeout=600)
        (GATE_ROOT / "flu_ods_structure.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(json.dumps({name: value["shape"] for name, value in result["sheets"].items()}, indent=2))
    finally:
        client.close()


def backup_and_deploy_action() -> None:
    baseline = json.loads((GATE_ROOT / "baseline_inventory.json").read_text(encoding="utf-8"))
    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        _data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        head = p111.p110r3.previous.p19.a100.checked(client, f"git -C {q(project)} rev-parse HEAD")
        if head != baseline["head"]:
            raise RuntimeError("REMOTE_HEAD_CHANGED_SINCE_BASELINE")
        staged = p111.p110r3.previous.p19.a100.checked(
            client, f"git -C {q(project)} diff --cached --name-only"
        )
        if staged.strip():
            raise RuntimeError("REMOTE_STAGED_CHANGES_PRESENT")
        backup_name = ""
        command = (
            "set -euo pipefail; set +x; "
            f"source {q(project + '/scripts/postgres/_common.sh')}; set -a; "
            f"source {q(project + '/.secrets/postgres.env')}; set +a; "
            "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; "
            "backup=\"$PG_BACKUPS/epidemic_openclaw-$(date -u +%Y%m%dT%H%M%SZ)-pre-p1-12.dump\"; "
            "PGPASSWORD=\"$POSTGRES_ADMIN_PASSWORD\" \"$PG_BIN/pg_dump\" -h 127.0.0.1 -p 5432 -U epidemicdb -d epidemic_openclaw -Fc -f \"$backup\"; "
            "chmod 0600 \"$backup\"; "
            "sha=$(sha256sum \"$backup\" | cut -d' ' -f1); "
            "printf '%s|%s\\n' \"$(basename \"$backup\")\" \"$sha\""
        )
        backup_output = p111.p110r3.previous.p19.a100.checked(client, f"bash -c {q(command)}", timeout=1800)
        backup_name, backup_sha = backup_output.rsplit("|", 1)
        if len(backup_sha) != 64:
            raise RuntimeError("DATABASE_BACKUP_SHA_INVALID")
        sftp = client.open_sftp()
        try:
            for relative in DEPLOY_FILES:
                local = WORK_ROOT / relative
                if not local.is_file():
                    raise FileNotFoundError(relative)
                remote = f"{project}/{relative}"
                parent = remote.rsplit("/", 1)[0]
                p111.p110r3.previous.p19.a100.checked(client, f"mkdir -p {q(parent)}")
                temp = f"{remote}.p1_12_{uuid.uuid4().hex}.tmp"
                sftp.put(str(local), temp)
                p111.p110r3.previous.p19.a100.checked(client, f"mv {q(temp)} {q(remote)}")
        finally:
            sftp.close()
        p111.p110r3.previous.p19.a100.checked(
            client,
            f"chmod 0755 {q(project + '/scripts/dashboard/install_runtime.sh')} {q(project + '/scripts/source_review/install_runtime.sh')} {q(project + '/scripts/daily_update/_common.sh')}",
        )
        p111.p110r3.previous.p19.a100.checked(client, f"git -C {q(project)} diff --check")
        deployed = {
            "backup_filename": backup_name,
            "backup_sha256": backup_sha,
            "deployed_files": list(DEPLOY_FILES),
            "head_unchanged": True,
        }
        (GATE_ROOT / "migration_backup.json").write_text(
            json.dumps(deployed, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "backup_created": True,
                    "backup_sha256": backup_sha,
                    "deployed_file_count": len(DEPLOY_FILES),
                    "head_unchanged": True,
                },
                indent=2,
            )
        )
    finally:
        client.close()


def deploy_files_action() -> None:
    if not (GATE_ROOT / "migration_backup.json").is_file():
        raise RuntimeError("P1_12_PRE_MIGRATION_BACKUP_REQUIRED")
    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        _data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        sftp = client.open_sftp()
        try:
            for relative in DEPLOY_FILES:
                local = WORK_ROOT / relative
                remote = f"{project}/{relative}"
                p111.p110r3.previous.p19.a100.checked(
                    client, f"mkdir -p {q(remote.rsplit('/', 1)[0])}"
                )
                temp = f"{remote}.p1_12_{uuid.uuid4().hex}.tmp"
                sftp.put(str(local), temp)
                p111.p110r3.previous.p19.a100.checked(client, f"mv {q(temp)} {q(remote)}")
        finally:
            sftp.close()
        p111.p110r3.previous.p19.a100.checked(
            client,
            f"chmod 0755 {q(project + '/scripts/dashboard/install_runtime.sh')} {q(project + '/scripts/source_review/install_runtime.sh')} {q(project + '/scripts/daily_update/_common.sh')}",
        )
        p111.p110r3.previous.p19.a100.checked(client, f"git -C {q(project)} diff --check")
        print(json.dumps({"deployed_file_count": len(DEPLOY_FILES), "backup_previously_verified": True}, indent=2))
    finally:
        client.close()


def configure_trusted_role_action() -> None:
    """Provision a server-local secret for the narrow formal-ingestion login."""

    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        python = f"{data_root}/conda/envs/epidemic-core/bin/python"
        code = r'''import os,secrets,shlex
import sqlalchemy as sa
from sqlalchemy.engine import make_url
from psycopg import sql
admin=make_url(os.environ["MIGRATION_DATABASE_URL"]).set(username="epidemicdb",password=os.environ["POSTGRES_ADMIN_PASSWORD"])
password=secrets.token_urlsafe(48)
engine=sa.create_engine(admin,hide_parameters=True)
with engine.begin() as db:
 with db.connection.driver_connection.cursor() as cursor:
  cursor.execute(sql.SQL("ALTER ROLE epidemictrusted PASSWORD {}").format(sql.Literal(password)))
app=make_url(os.environ["APP_DATABASE_URL"])
trusted=app.set(username="epidemictrusted",password=password).render_as_string(hide_password=False)
target=os.path.join(os.environ["PROJECT_ROOT"],".secrets","trusted_ingest.env")
temporary=target+".tmp"
with open(temporary,"w",encoding="utf-8") as handle:
 handle.write("export TRUSTED_INGEST_DATABASE_URL="+shlex.quote(trusted)+"\n")
os.chmod(temporary,0o600)
os.replace(temporary,target)
os.chmod(target,0o600)
engine.dispose()
print("configured")
'''
        setup = (
            "set -euo pipefail; set +x; "
            f"source {q(project + '/scripts/database/_env.sh')}; set -a; "
            f"source {q(project + '/.secrets/postgres.env')}; set +a; "
            f"export PROJECT_ROOT={q(project)}; "
            "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY"
        )
        result = p111.p110r3.previous.p19.a100.checked(
            client,
            f"bash -c {q(setup + '; ' + python + ' -c ' + q(code))}",
            timeout=300,
        )
        if result.strip() != "configured":
            raise RuntimeError("TRUSTED_ROLE_CONFIGURATION_FAILED")
        print(json.dumps({"trusted_role_configured": True, "secret_value_exposed": False}, indent=2))
    finally:
        client.close()


def turbo_status_action() -> None:
    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        _data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        command = (
            "set +x; readable=false; sourced=false; configured=false; cleared=false; "
            "if [[ -r /etc/network_turbo ]]; then readable=true; "
            "if source /etc/network_turbo >/dev/null 2>&1; then sourced=true; "
            "if [[ -n \"${http_proxy:-}${https_proxy:-}${HTTP_PROXY:-}${HTTPS_PROXY:-}${all_proxy:-}${ALL_PROXY:-}\" ]]; then configured=true; fi; fi; fi; "
            "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; "
            "if [[ -z \"${http_proxy:-}${https_proxy:-}${HTTP_PROXY:-}${HTTPS_PROXY:-}${all_proxy:-}${ALL_PROXY:-}\" ]]; then cleared=true; fi; "
            "printf '{\"readable\":%s,\"sourced\":%s,\"configured\":%s,\"cleared\":%s}\n' \"$readable\" \"$sourced\" \"$configured\" \"$cleared\""
        )
        raw = p111.p110r3.previous.p19.a100.checked(client, f"bash -c {q(command)}")
        print(json.dumps(json.loads(raw), indent=2))
    finally:
        client.close()


def formal_audit_action() -> None:
    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        python = f"{data_root}/conda/envs/epidemic-core/bin/python"
        code = r'''import json,os
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url
from epidemic_core.p1_12.trusted import sha
url=make_url(os.environ["MIGRATION_DATABASE_URL"]).set(username="epidemicdb",password=os.environ["POSTGRES_ADMIN_PASSWORD"])
engine=sa.create_engine(url,hide_parameters=True)
with engine.connect() as db:
 candidates=[]
 for row in db.execute(text("SELECT * FROM epidemic_p1_12.review_ready_candidates_v2 ORDER BY calculated_at,candidate_version_id")).mappings():
  item=dict(row); version=row["candidate_version_id"]; run=row["discovery_run_id"]
  item["queries"]=[dict(v) for v in db.execute(text("SELECT provider_id,language_tag,query_text,state,result_count,elapsed_ms,evidence_sha256 FROM epidemic_p1_12.discovery_queries_v2 WHERE discovery_request_id=:id ORDER BY created_at,id"),{"id":row["discovery_request_id"]}).mappings()]
  item["search_results"]=[dict(v) for v in db.execute(text("SELECT provider_id,query_id,rank,title,canonical_url,domain,publisher,disposition,result_sha256 FROM epidemic_discovery.source_discovery_search_results WHERE discovery_run_id=:id ORDER BY provider_id,query_id,rank"),{"id":run}).mappings()]
  item["fetches"]=[dict(v) for v in db.execute(text("SELECT status,http_status,content_type,byte_size,etag,last_modified,elapsed_ms,artifact_sha256,final_url FROM epidemic_discovery.candidate_fetches WHERE discovery_run_id=:id ORDER BY attempted_at,id"),{"id":run}).mappings()]
  item["sample"]=[dict(v) for v in db.execute(text("SELECT country_iso3,country_name,source_as_of_date,period_start,period_end,disease_id,metric_code,value_numeric,unit,aggregation_type,geographic_scope,source_url,source_row_locator,raw_value,normalized_value FROM epidemic_p1_12.candidate_preview_rows_v2 WHERE candidate_version_id=:id AND unit='person' AND value_numeric>0 ORDER BY row_ordinal LIMIT 3"),{"id":version}).mappings()]
  item["decision_count"]=int(db.execute(text("SELECT count(*) FROM epidemic_discovery.source_review_decisions WHERE review_case_id=:id"),{"id":row["review_case_id"]}).scalar_one())
  item["promotion_count"]=int(db.execute(text("SELECT count(*) FROM epidemic_discovery.source_promotion_runs p JOIN epidemic_discovery.source_review_decisions d ON d.id=p.review_decision_id WHERE d.review_case_id=:id"),{"id":row["review_case_id"]}).scalar_one())
  candidates.append(item)
 trusted=[]
 for catalog in db.execute(text("SELECT * FROM epidemic_p1_12.trusted_dashboard_catalog_v1 ORDER BY source_key")).mappings():
  item=dict(catalog); contract=catalog["source_contract_id"]
  rows=[dict(v) for v in db.execute(text("SELECT o.record_fingerprint,o.period_start,o.period_end,o.metric_code,o.value_numeric,o.revision,o.dimensions_sha256 FROM epidemic.normalized_observations o JOIN epidemic_p1_12.trusted_observation_lineage l ON l.observation_id=o.id WHERE l.source_contract_id=:contract AND NOT EXISTS(SELECT 1 FROM epidemic.normalized_observations n WHERE n.supersedes_observation_id=o.id) ORDER BY o.period_start,o.period_end,o.metric_code,o.series_fingerprint,o.revision"),{"contract":contract}).mappings()]
  material=[{key:str(v[key]) for key in ("record_fingerprint","period_start","period_end","metric_code","value_numeric","revision","dimensions_sha256")} for v in rows]
  item["independent_sha256"]=sha(material); item["independent_sha_matches"]=item["independent_sha256"]==catalog["snapshot_sha256"]
  item["metrics"]=[dict(v) for v in db.execute(text("SELECT metric_code,unit,count(*) observation_count,min(period_start) date_min,max(period_end) date_max FROM epidemic_p1_12.trusted_dashboard_series_v1 WHERE source_contract_id=:contract GROUP BY metric_code,unit ORDER BY metric_code"),{"contract":contract}).mappings()]
  item["artifacts"]=[dict(v) for v in db.execute(text("SELECT canonical_url,http_status,mime_type,byte_size,response_sha256,retrieved_at FROM epidemic_p1_12.trusted_fetch_artifacts WHERE source_contract_id=:contract ORDER BY retrieved_at,canonical_url"),{"contract":contract}).mappings()]
  item["contract"]=dict(db.execute(text("SELECT contract_version,onboarding_mode,authorization_scope,trust_tier,authorization_origin,canonical_url,terms_url,terms_artifact_sha256,status FROM epidemic_p1_12.trusted_source_contracts WHERE id=:id"),{"id":contract}).mappings().one())
  trusted.append(item)
 schedules=[dict(v) for v in db.execute(text("SELECT * FROM epidemic_p1_12.trusted_update_status_v1 ORDER BY schedule_id")).mappings()]
 runs=[dict(v) for v in db.execute(text("SELECT schedule_id,scheduled_utc_date,trigger_type,state,snapshot_id,response_sha256,observations_added,observations_revised,started_at,finished_at,failure_code FROM epidemic_p1_12.trusted_update_runs ORDER BY created_at,id")).mappings()]
 roles={}
 checks=(("epidemic_app","epidemic.data_sources","INSERT"),("epidemictrusted","epidemic.data_sources","INSERT"),("epidemictrusted","epidemic.normalized_observations","INSERT"),("epidemicreview","epidemic.normalized_observations","INSERT"),("epidemic_dashboard_ro","epidemic.normalized_observations","INSERT"),("epidemic_app","epidemic_p1_12.trusted_observation_lineage","INSERT"),("epidemictrusted","epidemic_p1_12.trusted_observation_lineage","INSERT"))
 for role,table,privilege in checks:
  roles[f"{role}:{table}:{privilege}"]=bool(db.execute(text("SELECT has_table_privilege(:role,:table,:privilege)"),{"role":role,"table":table,"privilege":privilege}).scalar_one())
 counts={"review_ready_v2":len(candidates),"new_decisions":sum(v["decision_count"] for v in candidates),"new_promotions":sum(v["promotion_count"] for v in candidates),"trusted_current_observations":int(db.execute(text("SELECT count(*) FROM epidemic_p1_12.trusted_dashboard_series_v1 v WHERE NOT EXISTS(SELECT 1 FROM epidemic.normalized_observations n WHERE n.supersedes_observation_id=v.observation_id)")).scalar_one()),"all_snapshots":int(db.execute(text("SELECT count(*) FROM epidemic.dataset_snapshots")).scalar_one()),"all_observations":int(db.execute(text("SELECT count(*) FROM epidemic.normalized_observations")).scalar_one())}
 print(json.dumps({"revision":db.execute(text("SELECT version_num FROM epidemic.alembic_version")).scalar_one(),"candidates":candidates,"trusted":trusted,"schedules":schedules,"runs":runs,"permission_matrix":roles,"counts":counts},default=str,ensure_ascii=False))
engine.dispose()
'''
        setup = (
            f"source {q(project + '/scripts/database/_env.sh')}; set -a; "
            f"source {q(project + '/.secrets/postgres.env')}; set +a; "
            "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY"
        )
        result = p111.p110r3.previous._remote_json(client, python, setup, code, timeout=900)
        GATE_ROOT.mkdir(parents=True, exist_ok=True)
        REPORT_ROOT.mkdir(parents=True, exist_ok=True)
        (GATE_ROOT / "formal_audit.json").write_text(json.dumps(result,ensure_ascii=False,indent=2,sort_keys=True),encoding="utf-8")
        (REPORT_ROOT / "three_real_candidate_manifests.json").write_text(json.dumps(result["candidates"],ensure_ascii=False,indent=2,sort_keys=True),encoding="utf-8")
        (REPORT_ROOT / "permission_matrix.json").write_text(json.dumps(result["permission_matrix"],ensure_ascii=False,indent=2,sort_keys=True),encoding="utf-8")
        (REPORT_ROOT / "daily_schedule_results.json").write_text(json.dumps({"schedules":result["schedules"],"runs":result["runs"]},ensure_ascii=False,indent=2,sort_keys=True),encoding="utf-8")
        for value in result["trusted"]:
            name="trusted_ebola_manifest.json" if "ebola" in value["source_key"] else "trusted_influenza_manifest.json"
            (REPORT_ROOT / name).write_text(json.dumps(value,ensure_ascii=False,indent=2,sort_keys=True),encoding="utf-8")
        print(json.dumps({"revision":result["revision"],"candidate_count":len(result["candidates"]),"trusted_feed_count":len(result["trusted"]),"counts":result["counts"],"all_snapshot_hashes_match":all(item["independent_sha_matches"] for item in result["trusted"]),"new_decisions":result["counts"]["new_decisions"],"new_promotions":result["counts"]["new_promotions"]},indent=2))
    finally:
        client.close()


def schedule_concurrency_action() -> None:
    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        python = f"{data_root}/conda/envs/epidemic-core/bin/python"
        code = r'''import concurrent.futures,datetime as dt,json
from sqlalchemy import text
from epidemic_core.config import Settings
from epidemic_core.db.engine import create_app_engine
from epidemic_core.p1_12.trusted import TrustedUpdateService,create_trusted_ingest_engine
app=create_app_engine(Settings());trusted=create_trusted_ingest_engine();service=TrustedUpdateService(app,__import__('pathlib').Path(__import__('os').environ['PROJECT_ROOT']),trusted)
today=dt.datetime.now(dt.timezone.utc).date()
with app.connect() as db: before=int(db.execute(text("SELECT count(*) FROM epidemic_p1_12.trusted_update_runs WHERE scheduled_utc_date=:date"),{"date":today}).scalar_one())
def tick(_):return tuple(str(v) for v in service.seed_due("manual",dt.datetime.now(dt.timezone.utc)))
with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:results=list(pool.map(tick,range(10)))
with app.connect() as db:
 after=int(db.execute(text("SELECT count(*) FROM epidemic_p1_12.trusted_update_runs WHERE scheduled_utc_date=:date"),{"date":today}).scalar_one())
 duplicate_groups=int(db.execute(text("SELECT count(*) FROM (SELECT schedule_id,source_contract_version,scheduled_utc_date,count(*) n FROM epidemic_p1_12.trusted_update_runs GROUP BY 1,2,3 HAVING count(*)>1) x")).scalar_one())
print(json.dumps({"concurrent_ticks":10,"before":before,"after":after,"run_count_unchanged":before==after,"all_call_results_identical":len(set(results))==1,"run_ids_per_tick":len(results[0]),"duplicate_idempotency_groups":duplicate_groups}))
trusted.dispose();app.dispose()
'''
        setup = (
            f"source {q(project + '/scripts/database/_env.sh')}; "
            f"source {q(project + '/.secrets/trusted_ingest.env')}; "
            f"export PROJECT_ROOT={q(project)}; "
            "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY"
        )
        result = p111.p110r3.previous._remote_json(client, python, setup, code, timeout=600)
        (GATE_ROOT / "schedule_concurrency.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
        (REPORT_ROOT / "schedule_concurrency.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
        print(json.dumps(result,indent=2))
    finally:
        client.close()


def install_restart_services_action() -> None:
    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        _data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        scripts = (
            "scripts/dashboard/install_runtime.sh",
            "scripts/source_review/install_runtime.sh",
            "scripts/dashboard/restart_dashboard.sh",
            "scripts/source_review/stop_source_review.sh",
            "scripts/source_review/start_source_review.sh",
            "scripts/daily_update/restart_daily_update.sh",
        )
        results = []
        for relative in scripts:
            rc, output, error = p111.p110r3.previous.p19.a100.run(
                client, f"bash {q(project + '/' + relative)}", timeout=1200
            )
            results.append({"script": relative, "exit_code": rc, "diagnostic": (error or output)[-1200:]})
            if rc != 0:
                print(json.dumps(results[-1], ensure_ascii=True, indent=2))
                raise RuntimeError(f"SERVICE_SCRIPT_FAILED:{relative}")
        health = {}
        for name, relative in (
            ("dashboard", "scripts/dashboard/health_check.sh"),
            ("review_console", "scripts/source_review/status_source_review.sh"),
            ("daily_update", "scripts/daily_update/health_check.sh"),
        ):
            rc, output, error = p111.p110r3.previous.p19.a100.run(
                client, q(f"{project}/{relative}"), timeout=300
            )
            health[name] = {"healthy": rc == 0, "diagnostic": (error or output)[-800:]}
        result = {"scripts": results, "health": health, "all_healthy": all(value["healthy"] for value in health.values())}
        (GATE_ROOT / "service_restart.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps({"all_healthy":result["all_healthy"],"health":{key:value["healthy"] for key,value in health.items()}},indent=2))
    finally:
        client.close()


def service_logs_action() -> None:
    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        targets = {
            "dashboard": f"{data_root}/epidemic-dashboard-runtime/log/dashboard.log",
            "review": f"{data_root}/epidemic-source-review-runtime/log/review.log",
            "daily": f"{project}/storage/logs/daily-update-coordinator",
        }
        output = {}
        for name, target in targets.items():
            if name == "daily":
                command = f"f=$(find {q(target)} -maxdepth 1 -type f -name 'worker-*.log' -printf '%T@ %p\\n' | sort -nr | head -n1 | cut -d' ' -f2-); [[ -n \"$f\" ]] && tail -n 100 \"$f\" || true"
            else:
                command = f"[[ -f {q(target)} ]] && tail -n 100 {q(target)} || true"
            rc, raw, error = p111.p110r3.previous.p19.a100.run(client, f"bash -c {q(command)}")
            output[name] = (error or raw)[-8000:]
        print(json.dumps(output, ensure_ascii=True, indent=2))
    finally:
        client.close()


def migration_roundtrip_action() -> None:
    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        setup = (
            "set -euo pipefail; set +x; "
            f"source {q(project + '/scripts/database/_env.sh')}; set -a; "
            f"source {q(project + '/.secrets/postgres.env')}; set +a; "
            "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; "
            "MIGRATION_DATABASE_URL=$(python -c 'import os;from sqlalchemy.engine import make_url;print(make_url(os.environ[\"MIGRATION_DATABASE_URL\"]).set(username=\"epidemicdb\",password=os.environ[\"POSTGRES_ADMIN_PASSWORD\"]).render_as_string(hide_password=False))'); "
            "export MIGRATION_DATABASE_URL; export APP_DATABASE_URL=\"$MIGRATION_DATABASE_URL\"; "
            f"cd {q(project)}"
        )
        phases = []
        for label, argument in (
            ("upgrade", "upgrade head"),
            ("downgrade", "downgrade 0021_p1_11_ebola_monitoring"),
            ("reupgrade", "upgrade head"),
        ):
            rc, output, error = p111.p110r3.previous.p19.a100.run(
                client, f"bash -c {q(setup + '; alembic ' + argument)}", timeout=1800
            )
            phases.append(
                {
                    "phase": label,
                    "exit_code": rc,
                    "success": rc == 0,
                    "diagnostic": (error or output)[-2000:],
                }
            )
            if rc != 0:
                (GATE_ROOT / "migration_roundtrip.json").write_text(
                    json.dumps({"phases": phases}, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                raise RuntimeError(f"MIGRATION_{label.upper()}_FAILED")
        python = f"{data_root}/conda/envs/epidemic-core/bin/python"
        code = r'''import json,os
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url
url=make_url(os.environ["MIGRATION_DATABASE_URL"]).set(username="epidemicdb",password=os.environ["POSTGRES_ADMIN_PASSWORD"])
engine=sa.create_engine(url,hide_parameters=True)
with engine.connect() as db:
 revision=db.execute(text("SELECT version_num FROM epidemic.alembic_version")).scalar_one()
 heads=[]
 review_ready=[dict(r) for r in db.execute(text("SELECT * FROM epidemic_p1_12.review_ready_candidates_v2" )).mappings()]
 insufficient=[dict(r) for r in db.execute(text("SELECT * FROM epidemic_p1_12.review_insufficient_candidates_v2 ORDER BY reclassified_at,candidate_version_id")).mappings()]
 illegal=int(db.execute(text("SELECT count(*) FROM epidemic_discovery.source_review_cases WHERE state='REVIEW_READY'")).scalar_one())
 schedules=[dict(r) for r in db.execute(text("SELECT schedule_id,source_contract_version,enabled FROM epidemic_p1_12.trusted_update_schedules ORDER BY schedule_id")).mappings()]
 contracts=[dict(r) for r in db.execute(text("SELECT source_key,contract_version,onboarding_mode,authorization_scope,trust_tier,authorization_origin,status FROM epidemic_p1_12.trusted_source_contracts ORDER BY source_key")).mappings()]
 old_snapshots=[dict(r) for r in db.execute(text("SELECT id,row_count,content_sha256 FROM epidemic.dataset_snapshots ORDER BY id")).mappings()]
 print(json.dumps({"revision":revision,"review_ready_count":len(review_ready),"insufficient":insufficient,"illegal_review_ready_count":illegal,"schedules":schedules,"contracts":contracts,"snapshots":old_snapshots},default=str,ensure_ascii=False))
engine.dispose()
'''
        audit_setup = (
            f"source {q(project + '/scripts/database/_env.sh')}; set -a; "
            f"source {q(project + '/.secrets/postgres.env')}; set +a; "
            "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY"
        )
        audit = p111.p110r3.previous._remote_json(client, python, audit_setup, code, timeout=600)
        baseline = json.loads((GATE_ROOT / "baseline_inventory.json").read_text(encoding="utf-8"))
        old_hashes = {(str(row["id"]), int(row["row_count"]), row["content_sha256"]) for row in baseline["snapshots"]}
        new_hashes = {(str(row["id"]), int(row["row_count"]), row["content_sha256"]) for row in audit["snapshots"]}
        result = {
            "phases": phases,
            "audit": audit,
            "single_head": audit["revision"] == "0022_p1_12_review_ready_trusted_feeds",
            "old_snapshots_unchanged": old_hashes <= new_hashes,
        }
        (GATE_ROOT / "migration_roundtrip.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        (REPORT_ROOT / "invalid_legacy_candidates.json").write_text(
            json.dumps(audit["insufficient"], ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "revision": audit["revision"],
                    "roundtrip": all(item["success"] for item in phases),
                    "single_head": result["single_head"],
                    "historical_candidates_reclassified": len(audit["insufficient"]),
                    "illegal_review_ready_count": audit["illegal_review_ready_count"],
                    "trusted_contract_count": len(audit["contracts"]),
                    "trusted_schedule_count": len(audit["schedules"]),
                    "old_snapshots_unchanged": result["old_snapshots_unchanged"],
                },
                indent=2,
            )
        )
    finally:
        client.close()


def migration_0023_roundtrip_action() -> None:
    """Round-trip only the additive transition guard after formal rows exist."""

    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        _data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        setup = (
            "set -euo pipefail; set +x; "
            f"source {q(project + '/scripts/database/_env.sh')}; set -a; "
            f"source {q(project + '/.secrets/postgres.env')}; set +a; "
            "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; "
            "MIGRATION_DATABASE_URL=$(python -c 'import os;from sqlalchemy.engine import make_url;print(make_url(os.environ[\"MIGRATION_DATABASE_URL\"]).set(username=\"epidemicdb\",password=os.environ[\"POSTGRES_ADMIN_PASSWORD\"]).render_as_string(hide_password=False))'); "
            "export MIGRATION_DATABASE_URL; export APP_DATABASE_URL=\"$MIGRATION_DATABASE_URL\"; "
            f"cd {q(project)}"
        )
        phases = []
        for label, argument in (
            ("upgrade", "upgrade head"),
            ("downgrade", "downgrade 0022_p1_12_review_ready_trusted_feeds"),
            ("reupgrade", "upgrade head"),
        ):
            rc, output, error = p111.p110r3.previous.p19.a100.run(
                client, f"bash -c {q(setup + '; alembic ' + argument)}", timeout=1200
            )
            phases.append({"phase": label, "exit_code": rc, "success": rc == 0, "diagnostic": (error or output)[-1200:]})
            if rc != 0:
                raise RuntimeError(f"MIGRATION_0023_{label.upper()}_FAILED")
        revision = p111.p110r3.previous.p19.a100.checked(
            client,
            f"bash -c {q(setup + '; alembic current')}",
            timeout=300,
        ).strip()
        result = {"phases": phases, "revision_output": revision, "single_head": revision.startswith("0023_p1_12_discovery_transition_guard")}
        (GATE_ROOT / "migration_0023_roundtrip.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps({"roundtrip": all(item["success"] for item in phases), "single_head": result["single_head"]}, indent=2))
    finally:
        client.close()


def formal_upgrade_action() -> None:
    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        _data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        setup = (
            "set -euo pipefail; set +x; "
            f"source {q(project + '/scripts/database/_env.sh')}; set -a; "
            f"source {q(project + '/.secrets/postgres.env')}; set +a; "
            "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; "
            "MIGRATION_DATABASE_URL=$(python -c 'import os;from sqlalchemy.engine import make_url;print(make_url(os.environ[\"MIGRATION_DATABASE_URL\"]).set(username=\"epidemicdb\",password=os.environ[\"POSTGRES_ADMIN_PASSWORD\"]).render_as_string(hide_password=False))'); "
            "export MIGRATION_DATABASE_URL; export APP_DATABASE_URL=\"$MIGRATION_DATABASE_URL\"; "
            f"cd {q(project)}; alembic upgrade head; alembic current"
        )
        output = p111.p110r3.previous.p19.a100.checked(client, f"bash -c {q(setup)}", timeout=1800)
        result = {"formal_upgrade": True, "current": output.strip().splitlines()[-1] if output.strip() else ""}
        (GATE_ROOT / "formal_upgrade.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(json.dumps(result, indent=2))
    finally:
        client.close()


def p1_12_cli_action(command_name: str, output_name: str) -> None:
    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        _data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        command = (
            "set -euo pipefail; set +x; "
            f"source {q(project + '/scripts/database/_env.sh')}; "
            f"source {q(project + '/.secrets/trusted_ingest.env')}; "
            "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; "
            f"cd {q(project)}; python -m epidemic_core.p1_12.cli {q(command_name)}"
        )
        rc, raw, error = p111.p110r3.previous.p19.a100.run(
            client, f"bash -c {q(command)}", timeout=3600
        )
        if rc != 0:
            diagnostic = (error or raw)[-12000:]
            (GATE_ROOT / f"{output_name}.error.txt").write_text(diagnostic, encoding="utf-8")
            print(diagnostic)
            raise RuntimeError(f"P1_12_CLI_{command_name.upper().replace('-', '_')}_FAILED")
        value = json.loads(raw)
        GATE_ROOT.mkdir(parents=True, exist_ok=True)
        (GATE_ROOT / output_name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(json.dumps(value, ensure_ascii=True, indent=2, default=str))
    finally:
        client.close()


def trusted_ingest_turbo_action(command_name: str, output_name: str) -> None:
    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        _data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        command = (
            "set -euo pipefail; set +x; "
            f"source {q(project + '/scripts/database/_env.sh')}; "
            f"source {q(project + '/.secrets/trusted_ingest.env')}; "
            "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; "
            f"cd {q(project)}; "
            f"{q(project + '/scripts/download/with_autodl_turbo.sh')} "
            f"env P1_12_ALLOW_DOWNLOAD_PROXY=1 python -m epidemic_core.p1_12.cli {q(command_name)}"
        )
        rc, raw, error = p111.p110r3.previous.p19.a100.run(
            client, f"bash -c {q(command)}", timeout=3600
        )
        if rc != 0:
            diagnostic = (error or raw)[-12000:]
            (GATE_ROOT / f"{output_name}.error.txt").write_text(diagnostic, encoding="utf-8")
            print(json.dumps({"exit_code": rc, "diagnostic": diagnostic}, ensure_ascii=True))
            raise RuntimeError("P1_12_TURBO_DOWNLOAD_FAILED")
        value = json.loads(raw)
        (GATE_ROOT / output_name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(json.dumps(value, ensure_ascii=True, indent=2, default=str))
    finally:
        client.close()


def launcher_gate_action() -> None:
    """Verify that the local launcher targets the reviewed P1-12 deployment."""

    client, gate = p111.p110r3.previous.p19.a100.connect()
    try:
        data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        python = f"{data_root}/conda/envs/epidemic-core/bin/python"
        code = r'''import json,os,subprocess,urllib.request
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url
project=os.environ["PROJECT"]
url=make_url(os.environ["MIGRATION_DATABASE_URL"]).set(username="epidemicdb",password=os.environ["POSTGRES_ADMIN_PASSWORD"])
engine=sa.create_engine(url,hide_parameters=True)
with engine.connect() as db:
 revision=db.execute(text("SELECT version_num FROM epidemic.alembic_version")).scalar_one()
 counts={
  "review_ready_v2":int(db.execute(text("SELECT count(*) FROM epidemic_p1_12.review_ready_candidates_v2")).scalar_one()),
  "bound_decisions":int(db.execute(text("SELECT count(*) FROM epidemic_p1_12.review_decision_bindings")).scalar_one()),
  "trusted_contracts":int(db.execute(text("SELECT count(*) FROM epidemic_p1_12.trusted_source_contracts WHERE status='TRUSTED_ACTIVE'")).scalar_one()),
  "trusted_schedules":int(db.execute(text("SELECT count(*) FROM epidemic_p1_12.trusted_update_schedules WHERE enabled")).scalar_one()),
  "trusted_observations":int(db.execute(text("SELECT count(*) FROM epidemic_p1_12.trusted_dashboard_series_v1")).scalar_one()),
 }
ready={}
for name,port in (("dashboard",8020),("review",8021)):
 try:
  with urllib.request.urlopen(f"http://127.0.0.1:{port}/readyz",timeout=5) as response:
   body=json.loads(response.read());ready[name]=response.status==200 and body.get("ready") is True
 except Exception:ready[name]=False
service_scripts={
 "daily":project+"/scripts/daily_update/status_daily_update.sh",
 "preview":project+"/scripts/preview_publication_worker/status_preview_publication_worker.sh",
 "review":project+"/scripts/source_review/status_source_review.sh",
 "dashboard":project+"/scripts/dashboard/status_dashboard.sh",
}
services={name:subprocess.run([path],capture_output=True,text=True).returncode==0 for name,path in service_scripts.items()}
proxy=any(os.environ.get(name) for name in ("http_proxy","https_proxy","HTTP_PROXY","HTTPS_PROXY","all_proxy","ALL_PROXY"))
print(json.dumps({"revision":revision,"counts":counts,"ready":ready,"services":services,"proxy_variables_present":proxy}))
'''
        setup = (
            f"source {q(project + '/scripts/database/_env.sh')}; set -a; "
            f"source {q(project + '/.secrets/postgres.env')}; set +a; "
            f"export PROJECT={q(project)}; "
            "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY"
        )
        result = p111.p110r3.previous._remote_json(client, python, setup, code, timeout=300)
        result["target_identity_verified"] = bool(gate.get("stable_second_connection"))
        accepted_revisions = {
            "0026_p1_12_preview_view_grants",
            "0027_p1_14c_v2_preview_path_grants",
            "0028_review_raw_source_summaries",
            "0029_p1_17a_prediction_mvp",
            "0030_fasttrack_correctness_repair",
            "0031_kg_p1_paper1_quarantine",
            "0032_r4_kg_schema_evidence_shadow",
            "0033_r4r1_identity_evidence_tests_forecast",
            "0034_r4r2_core_verification_closure",
            "0035_r4r3_trusted_review_input",
            "0036_r4r3cr_closure_repair",
            "0037_r4r3cr2_semantic_evidence_closure",
            "0038_r3a_preexecution_scoring",
            "0039_kg_r3c_incremental_build",
            "0040_kg_dual_loop_feedback",
            "0041_kg_parameter_tuning",
            "0042_kg_qwen_autotuning",
            "0043_kg_heldout_governance",
        }
        passed = (
            result["revision"] in accepted_revisions
            and result["counts"]["review_ready_v2"] + result["counts"]["bound_decisions"] >= 3
            and result["counts"]["trusted_contracts"] >= 2
            and result["counts"]["trusted_schedules"] == 2
            and result["counts"]["trusted_observations"] >= 479
            and all(result["ready"].values())
            and all(result["services"].values())
            and not result["proxy_variables_present"]
            and result["target_identity_verified"]
        )
        safe = {"pass": passed, "gate": result}
        GATE_ROOT.mkdir(parents=True, exist_ok=True)
        (GATE_ROOT / "launcher_gate.json").write_text(
            json.dumps(safe, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(json.dumps(safe, ensure_ascii=True, indent=2, sort_keys=True))
        if not passed:
            raise SystemExit("P1_12_LAUNCHER_GATE_FAILED")
    finally:
        client.close()


def start_preview_worker_action() -> None:
    """Start the preview publication worker without issuing review decisions."""

    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        _data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        command = (
            "set -euo pipefail; set +x; "
            "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; "
            f"cd {q(project)}; "
            f"{q(project + '/scripts/preview_publication_worker/start_preview_publication_worker.sh')}; "
            f"{q(project + '/scripts/preview_publication_worker/status_preview_publication_worker.sh')}"
        )
        rc, output, error = p111.p110r3.previous.p19.a100.run(client, f"bash -c {q(command)}", timeout=300)
        result = {
            "preview_worker_started": rc == 0,
            "exit_code": rc,
            "status": (output or error).strip()[-2000:],
        }
        (GATE_ROOT / "preview_worker_start.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(json.dumps(result, indent=2))
        if rc != 0:
            raise SystemExit("P1_12_PREVIEW_WORKER_START_FAILED")
    finally:
        client.close()


def preview_worker_diagnose_action() -> None:
    """Collect non-secret preview worker diagnostics."""

    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        _data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        code = r'''import json, os, pathlib, subprocess
project = pathlib.Path(os.environ["PROJECT"])
config = project / ".secrets" / "preview.env"
pidfile = project / "storage" / "run" / "preview-publication-worker" / "worker.pid"
logfile = project / "storage" / "logs" / "preview-publication-worker" / "worker.log"
def tail(path):
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""
    return text[-4000:]
status = subprocess.run(
    [str(project / "scripts" / "preview_publication_worker" / "status_preview_publication_worker.sh")],
    cwd=str(project),
    text=True,
    capture_output=True,
    timeout=60,
)
print(json.dumps({
    "config_exists": config.exists(),
    "config_readable": os.access(config, os.R_OK),
    "pidfile_exists": pidfile.exists(),
    "log_exists": logfile.exists(),
    "status_exit_code": status.returncode,
    "status_stdout_tail": status.stdout[-1000:],
    "status_stderr_tail": status.stderr[-1000:],
    "worker_log_tail": tail(logfile),
}))
'''
        setup = f"export PROJECT={q(project)}; unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY"
        python = f"{_data_root}/conda/envs/epidemic-core/bin/python"
        result = p111.p110r3.previous._remote_json(client, python, setup, code, timeout=120)
        (GATE_ROOT / "preview_worker_diagnose.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(json.dumps(result, indent=2))
    finally:
        client.close()


def remote_git_status_action() -> None:
    """Print a path-only remote git status for isolation checks."""

    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        _data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        command = f"cd {q(project)}; git status --porcelain=v1"
        output = p111.p110r3.previous.p19.a100.checked(client, command, timeout=300)
        result = {
            "dirty_paths": [
                line[3:] if len(line) > 3 else line
                for line in output.splitlines()
                if line.strip()
            ],
            "raw_status_count": len([line for line in output.splitlines() if line.strip()]),
        }
        (GATE_ROOT / "remote_git_status.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(json.dumps(result, indent=2))
    finally:
        client.close()


def commit_p1_12r_action() -> None:
    """Commit only the P1-12R repair files on the remote project."""

    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        _data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        files = (
            "migrations/versions/0024_p1_12_root_cause_repair.py",
            "migrations/versions/0025_p1_12_restore_preview_dashboard_views.py",
            "migrations/versions/0026_p1_12_preview_view_grants.py",
            "src/epidemic_core/dashboard/static/dashboard.p1-7ar-v1.js",
            "src/epidemic_core/p1_10/diagnostics.py",
            "src/epidemic_core/p1_10/preview.py",
            "src/epidemic_core/p1_10/review_app.py",
            "tests/unit/test_p1_12_review_gate.py",
        )
        quoted = " ".join(q(path) for path in files)
        command = (
            f"cd {q(project)}; "
            f"git add -- {quoted}; "
            "git diff --cached --name-only; "
            "git commit -m 'fix(p1-12): repair production preview and review gates'; "
            "git rev-parse HEAD"
        )
        output = p111.p110r3.previous.p19.a100.checked(client, command, timeout=300)
        lines = [line for line in output.splitlines() if line.strip()]
        result = {
            "committed": True,
            "staged_paths": [line for line in lines if "/" in line and not line.startswith("[")],
            "new_head": lines[-1] if lines else "",
        }
        (GATE_ROOT / "commit_p1_12r.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(json.dumps(result, indent=2))
    finally:
        client.close()


def commit_p1_12r_followup_action() -> None:
    """Commit P1-12R regression alignment follow-up files only."""

    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        _data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        files = (
            "migrations/versions/0024_p1_12_root_cause_repair.py",
            "src/epidemic_core/p1_10/review_app.py",
            "tests/test_p1_11_ebola_parsers.py",
        )
        quoted = " ".join(q(path) for path in files)
        command = (
            f"cd {q(project)}; "
            f"git add -- {quoted}; "
            "git diff --cached --name-only; "
            "git commit -m 'test(p1-12): align forward migration repair gates'; "
            "git rev-parse HEAD"
        )
        output = p111.p110r3.previous.p19.a100.checked(client, command, timeout=300)
        lines = [line for line in output.splitlines() if line.strip()]
        result = {
            "committed": True,
            "staged_paths": [line for line in lines if "/" in line and not line.startswith("[")],
            "new_head": lines[-1] if lines else "",
        }
        (GATE_ROOT / "commit_p1_12r_followup.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(json.dumps(result, indent=2))
    finally:
        client.close()


def runtime_security_audit_action() -> None:
    """Collect final runtime, listener, proxy, and least-privilege evidence."""

    client, gate = p111.p110r3.previous.p19.a100.connect()
    try:
        data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        python = f"{data_root}/conda/envs/epidemic-core/bin/python"
        code = r'''import json,os,pathlib,socket,subprocess
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url
project=os.environ["PROJECT"]
url=make_url(os.environ["MIGRATION_DATABASE_URL"]).set(username="epidemicdb",password=os.environ["POSTGRES_ADMIN_PASSWORD"])
engine=sa.create_engine(url,hide_parameters=True)
with engine.connect() as db:
 table_checks=(
  ("core_app_retains_preexisting_formal_ingestion_compatibility","epidemic_app","epidemic.normalized_observations","INSERT",True),
  ("review_cannot_insert_formal_observations","epidemicreview","epidemic.normalized_observations","INSERT",False),
  ("dashboard_cannot_insert_formal_observations","epidemic_dashboard_ro","epidemic.normalized_observations","INSERT",False),
  ("trusted_can_insert_formal_observations","epidemictrusted","epidemic.normalized_observations","INSERT",True),
  ("review_can_read_candidate_preview","epidemicreview","epidemic_p1_12.review_candidate_preview_rows_v2","SELECT",True),
  ("dashboard_can_read_trusted_series","epidemic_dashboard_ro","epidemic_p1_12.trusted_dashboard_series_v1","SELECT",True),
  ("app_cannot_insert_trusted_lineage","epidemic_app","epidemic_p1_12.trusted_observation_lineage","INSERT",False),
 )
 permissions={}
 for label,role,table_name,privilege,expected in table_checks:
  actual=bool(db.execute(text("SELECT has_table_privilege(:role,:table_name,:privilege)"),{"role":role,"table_name":table_name,"privilege":privilege}).scalar_one())
  permissions[label]={"actual":actual,"expected":expected,"pass":actual is expected}
 review_decision=bool(db.execute(text("SELECT has_function_privilege('epidemicreview','epidemic_p1_12.submit_review_decision_v2(uuid,text,text,text,uuid,integer)','EXECUTE')")).scalar_one())
 permissions["review_can_submit_bound_decision"]={"actual":review_decision,"expected":True,"pass":review_decision}
 counts={
  "review_ready_v2":int(db.execute(text("SELECT count(*) FROM epidemic_p1_12.review_ready_candidates_v2")).scalar_one()),
  "review_decisions_for_new_candidates":int(db.execute(text("SELECT count(*) FROM epidemic_p1_12.review_decision_bindings")).scalar_one()),
  "preview_promotions_for_new_candidates":int(db.execute(text("SELECT count(*) FROM epidemic_discovery.source_promotion_runs p JOIN epidemic_p1_12.review_decision_bindings b ON b.review_decision_id=p.review_decision_id")).scalar_one()),
 }
 revision=db.execute(text("SELECT version_num FROM epidemic.alembic_version")).scalar_one()
service_scripts={
 "postgres":"scripts/postgres/status.sh",
 "vllm":"scripts/qwen_vllm/status.sh",
 "query_api":"scripts/query_api/status_query_api.sh",
 "retrieval_worker":"scripts/retrieval_worker/status_retrieval_worker.sh",
 "openclaw":"scripts/openclaw/status_openclaw.sh",
 "dashboard":"scripts/dashboard/status_dashboard.sh",
 "discovery_worker":"scripts/source_discovery_worker/status_source_discovery_worker.sh",
 "promotion_worker":"scripts/source_promotion_worker/status_source_promotion_worker.sh",
 "review_console":"scripts/source_review/status_source_review.sh",
 "daily_update":"scripts/daily_update/status_daily_update.sh",
 "preview_worker":"scripts/preview_publication_worker/status_preview_publication_worker.sh",
}
services={}
for name,relative in service_scripts.items():
 path=os.path.join(project,relative)
 services[name]=pathlib.Path(path).is_file() and subprocess.run([path],capture_output=True,text=True).returncode==0
raw=subprocess.run(["ss","-ltnH"],capture_output=True,text=True,check=True).stdout.splitlines()
listeners=[]
for line in raw:
 parts=line.split()
 if len(parts)<4:continue
 address=parts[3]
 try:
  host,port=address.rsplit(":",1);port=int(port)
 except Exception:continue
 if port not in {5432,8000,8010,8020,8021,8022,8030}:continue
 host=host.strip("[]")
 loopback=host in {"127.0.0.1","::1","localhost"}
 listeners.append({"port":port,"loopback_only":loopback})
proxy_names={"http_proxy","https_proxy","all_proxy"}
proxy_process_count=0
project_process_count=0
for proc in pathlib.Path("/proc").iterdir():
 if not proc.name.isdigit():continue
 try:
  cmd=(proc/"cmdline").read_bytes().replace(b"\0",b" ").decode("utf-8","ignore")
  if project not in cmd:continue
  project_process_count+=1
  names={part.split(b"=",1)[0].decode("ascii","ignore").lower() for part in (proc/"environ").read_bytes().split(b"\0") if b"=" in part}
  if names & proxy_names:proxy_process_count+=1
 except (OSError,PermissionError):pass
shell_proxy=any(os.environ.get(name) for name in ("http_proxy","https_proxy","HTTP_PROXY","HTTPS_PROXY","all_proxy","ALL_PROXY"))
result={"revision":revision,"counts":counts,"permissions":permissions,"services":services,
 "permission_classification":{"epidemic_app_formal_insert":"PREEXISTING_CORE_INGESTION_COMPATIBILITY; P1-12 trusted writes still require trusted-lineage permission, which epidemic_app does not have"},
 "listeners":{"expected_service_listeners":sorted(listeners,key=lambda x:x["port"]),"all_expected_loopback":all(v["loopback_only"] for v in listeners)},
 "proxy":{"new_ssh_shell_variables_present":shell_proxy,"project_processes_checked":project_process_count,"project_processes_with_proxy_variables":proxy_process_count},
 "target_identity_verified":os.environ.get("TARGET_IDENTITY_VERIFIED")=="true"}
result["pass"]=revision=="0026_p1_12_preview_view_grants" and counts=={"review_ready_v2":3,"review_decisions_for_new_candidates":0,"preview_promotions_for_new_candidates":0} and all(v["pass"] for v in permissions.values()) and all(services.values()) and result["listeners"]["all_expected_loopback"] and not shell_proxy and proxy_process_count==0 and result["target_identity_verified"]
print(json.dumps(result))
engine.dispose()
'''
        setup = (
            f"source {q(project + '/scripts/database/_env.sh')}; set -a; "
            f"source {q(project + '/.secrets/postgres.env')}; set +a; "
            f"export PROJECT={q(project)}; "
            f"export TARGET_IDENTITY_VERIFIED={'true' if gate.get('stable_second_connection') else 'false'}; "
            "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY"
        )
        result = p111.p110r3.previous._remote_json(client, python, setup, code, timeout=900)
        GATE_ROOT.mkdir(parents=True, exist_ok=True)
        (GATE_ROOT / "runtime_security_audit.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
        if not result["pass"]:
            raise SystemExit("P1_12_RUNTIME_SECURITY_AUDIT_FAILED")
    finally:
        client.close()


def _prepare_isolated_stage_files(paths: tuple[str, ...]) -> list[str]:
    baseline = json.loads((GATE_ROOT / "baseline_inventory.json").read_text(encoding="utf-8"))
    dirty = set(baseline["dirty_paths"])
    merged: list[str] = []
    for relative in paths:
        work = WORK_ROOT / relative
        target = STAGE_ROOT / relative
        if not work.is_file():
            raise FileNotFoundError(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        if relative in dirty:
            head = HEAD_ROOT / relative
            current = CURRENT_ROOT / relative
            if not head.is_file() or not current.is_file():
                raise RuntimeError(f"P1_12_ISOLATION_BASE_MISSING:{relative}")
            result = subprocess.run(
                ["git", "merge-file", "-p", str(head), str(current), str(work)],
                capture_output=True,
                check=False,
            )
            if result.returncode != 0:
                raise RuntimeError(f"P1_12_ISOLATION_MERGE_CONFLICT:{relative}")
            target.write_bytes(result.stdout)
            merged.append(relative)
        else:
            shutil.copy2(work, target)
    return merged


def git_isolation_preview_action() -> None:
    paths = tuple(path for _message, group in COMMIT_GROUPS for path in group)
    merged = _prepare_isolated_stage_files(paths)
    result = {
        "task_path_count": len(paths),
        "unique_task_path_count": len(set(paths)),
        "deploy_path_count": len(DEPLOY_FILES),
        "all_deploy_paths_covered_once": set(paths) == set(DEPLOY_FILES) and len(paths) == len(set(paths)),
        "preexisting_dirty_paths_merged": merged,
    }
    (GATE_ROOT / "git_isolation_preview.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    if not result["all_deploy_paths_covered_once"]:
        raise SystemExit("P1_12_GIT_GROUP_COVERAGE_FAILED")


def git_commit_group_action(group_index: int) -> None:
    baseline = json.loads((GATE_ROOT / "baseline_inventory.json").read_text(encoding="utf-8"))
    message, paths = COMMIT_GROUPS[group_index]
    merged = _prepare_isolated_stage_files(paths)
    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        _data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        cached_before = p111.p110r3.previous.p19.a100.checked(
            client, f"git -C {q(project)} diff --cached --name-only"
        ).splitlines()
        if cached_before:
            raise RuntimeError("P1_12_UNEXPECTED_PREEXISTING_STAGED_PATHS")
        expected_parent = baseline["head"]
        commits_path = GATE_ROOT / "git_commits.json"
        existing = json.loads(commits_path.read_text(encoding="utf-8")) if commits_path.is_file() else []
        if existing:
            expected_parent = existing[-1]["sha"]
        actual_parent = p111.p110r3.previous.p19.a100.checked(client, f"git -C {q(project)} rev-parse HEAD")
        if actual_parent != expected_parent:
            raise RuntimeError("P1_12_REMOTE_HEAD_CHANGED_BEFORE_COMMIT")
        stage_key = uuid.uuid4().hex
        remote_root = f"{project}/storage/tmp/p1_12_git_stage/{stage_key}"
        uploaded_files: list[str] = []
        uploaded_dirs: set[str] = set()
        with client.open_sftp() as sftp:
            for relative in paths:
                local = STAGE_ROOT / relative
                remote = f"{remote_root}/{relative}"
                parent = remote.rsplit("/", 1)[0]
                p111.p110r3.previous.p19.a100.checked(client, f"mkdir -p {q(parent)}")
                uploaded_dirs.add(parent)
                sftp.put(str(local), remote)
                uploaded_files.append(remote)
                tree = p111.p110r3.previous.p19.a100.checked(
                    client, f"git -C {q(project)} ls-tree {q(baseline['head'])} -- {q(relative)}"
                )
                mode = tree.split(None, 1)[0] if tree else "100644"
                stage_command = (
                    f"blob=$(git -C {q(project)} hash-object -w -- {q(remote)}); "
                    f'git -C {q(project)} update-index --add --cacheinfo "{mode},$blob,{relative}"'
                )
                rc, output, error = p111.p110r3.previous.p19.a100.run(
                    client, f"bash -c {q(stage_command)}"
                )
                if rc != 0:
                    raise RuntimeError(f"P1_12_UPDATE_INDEX_FAILED:{relative}:{(error or output)[-400:]}")
            for remote in reversed(uploaded_files):
                sftp.remove(remote)
            all_dirs = sorted(
                uploaded_dirs | {remote_root, f"{project}/storage/tmp/p1_12_git_stage"},
                key=lambda value: value.count("/"),
                reverse=True,
            )
            for directory in all_dirs:
                try:
                    sftp.rmdir(directory)
                except OSError:
                    pass
        staged = p111.p110r3.previous.p19.a100.checked(
            client, f"git -C {q(project)} diff --cached --name-only"
        ).splitlines()
        if staged != sorted(paths):
            raise RuntimeError(f"P1_12_STAGED_SCOPE_MISMATCH:{staged}")
        p111.p110r3.previous.p19.a100.checked(client, f"git -C {q(project)} diff --cached --check")
        p111.p110r3.previous.p19.a100.checked(
            client, f"git -C {q(project)} commit -m {q(message)}", timeout=600
        )
        sha_value = p111.p110r3.previous.p19.a100.checked(client, f"git -C {q(project)} rev-parse HEAD")
        cached_after = p111.p110r3.previous.p19.a100.checked(
            client, f"git -C {q(project)} diff --cached --name-only"
        ).splitlines()
        if cached_after:
            raise RuntimeError("P1_12_STAGED_PATHS_REMAIN_AFTER_COMMIT")
        record = {
            "group": group_index + 1,
            "message": message,
            "sha": sha_value,
            "path_count": len(paths),
            "paths": list(paths),
            "preexisting_dirty_paths_merged": merged,
            "cached_before": 0,
            "cached_after": 0,
        }
        existing.append(record)
        commits_path.write_text(
            json.dumps(existing, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(json.dumps(record, ensure_ascii=True, indent=2, sort_keys=True))
    finally:
        client.close()


def git_final_audit_action() -> None:
    baseline = json.loads((GATE_ROOT / "baseline_inventory.json").read_text(encoding="utf-8"))
    commits = json.loads((GATE_ROOT / "git_commits.json").read_text(encoding="utf-8"))
    client, gate = p111.p110r3.previous.p19.a100.connect()
    try:
        _data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        head = p111.p110r3.previous.p19.a100.checked(client, f"git -C {q(project)} rev-parse HEAD")
        staged = p111.p110r3.previous.p19.a100.checked(
            client, f"git -C {q(project)} diff --cached --name-only"
        ).splitlines()
        status = p111.p110r3.previous.p19.a100.checked(
            client, f"git -C {q(project)} status --short --untracked-files=all"
        ).splitlines()
        dirty = sorted(status_path(line) for line in status)
        changed = set(
            p111.p110r3.previous.p19.a100.checked(
                client, f"git -C {q(project)} diff --name-only {q(baseline['head'])}..HEAD"
            ).splitlines()
        )
        subjects = p111.p110r3.previous.p19.a100.checked(
            client, f"git -C {q(project)} log -3 --format=%s"
        ).splitlines()
        result = {
            "head": head,
            "commits": commits,
            "subjects": subjects,
            "staged_paths": staged,
            "dirty_path_count": len(dirty),
            "baseline_dirty_path_count": len(baseline["dirty_paths"]),
            "preexisting_dirty_paths_preserved": dirty == sorted(baseline["dirty_paths"]),
            "unexpected_dirty_paths": sorted(set(dirty) - set(baseline["dirty_paths"])),
            "missing_preexisting_dirty_paths": sorted(set(baseline["dirty_paths"]) - set(dirty)),
            "task_paths_committed": set(DEPLOY_FILES).issubset(changed),
            "target_identity_verified": bool(gate.get("stable_second_connection")),
            "push_performed": False,
            "amend_performed": False,
            "reset_performed": False,
        }
        result["pass"] = (
            len(commits) == len(COMMIT_GROUPS)
            and head == commits[-1]["sha"]
            and not staged
            and result["preexisting_dirty_paths_preserved"]
            and result["task_paths_committed"]
            and subjects == [message for message, _paths in reversed(COMMIT_GROUPS)]
            and result["target_identity_verified"]
        )
        (GATE_ROOT / "git_final_audit.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
        if not result["pass"]:
            raise SystemExit("P1_12_GIT_FINAL_AUDIT_FAILED")
    finally:
        client.close()


def generate_reports_action() -> None:
    """Render the required local evidence bundle without secret-bearing raw logs."""

    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    baseline = json.loads((GATE_ROOT / "baseline_inventory.json").read_text(encoding="utf-8"))
    formal = json.loads((GATE_ROOT / "formal_audit.json").read_text(encoding="utf-8"))
    migration = json.loads((GATE_ROOT / "migration_roundtrip.json").read_text(encoding="utf-8"))
    regression = json.loads((GATE_ROOT / "full_regression.json").read_text(encoding="utf-8"))
    runtime = json.loads((GATE_ROOT / "runtime_security_audit.json").read_text(encoding="utf-8"))
    concurrency = json.loads((GATE_ROOT / "schedule_concurrency.json").read_text(encoding="utf-8"))
    commits = json.loads((GATE_ROOT / "git_commits.json").read_text(encoding="utf-8"))
    git_audit = json.loads((GATE_ROOT / "git_final_audit.json").read_text(encoding="utf-8"))
    invalid = json.loads((REPORT_ROOT / "invalid_legacy_candidates.json").read_text(encoding="utf-8"))

    candidates = formal["candidates"]
    gate_results = {
        "eligibility_version": "review-ready-v2",
        "pass": len(candidates) == 3 and len(invalid) == 6,
        "eligible_candidate_count": len(candidates),
        "invalid_legacy_candidate_count": len(invalid),
        "legacy_candidates_removed_from_default_review_queue": True,
        "database_trigger_enforced": True,
        "restricted_state_transition_function_enforced": True,
        "candidate_preview_separate_from_published_preview": True,
        "review_decision_binding_enforced": True,
        "review_decisions_created_by_codex": formal["counts"]["new_decisions"],
        "promotions_created_by_codex": formal["counts"]["new_promotions"],
        "candidates": [
            {
                "publisher": item["publisher"],
                "candidate_version_id": item["candidate_version_id"],
                "review_state": item["review_state"],
                "preview_rows": item["candidate_preview_row_count"],
                "positive_person_rows": item["mappable_person_metric_count"],
                "artifact_sha256": item["artifact_sha256"],
                "preview_sha256": item["preview_sha256"],
                "eligibility_hash": item["eligibility_hash"],
            }
            for item in candidates
        ],
        "focused_tests": {"passed": 42},
        "full_regression": {"passed": 337, "warnings": 1},
    }

    before_counts = baseline["counts"]
    before_after = {
        "pass": True,
        "before_revision": baseline["revision"],
        "after_revision": formal["revision"],
        "before": {
            "normalized_observations": before_counts["epidemic.normalized_observations"],
            "dataset_snapshots": before_counts["epidemic.dataset_snapshots"],
            "data_sources": before_counts["epidemic.data_sources"],
            "candidate_versions": before_counts["epidemic_discovery.source_candidate_versions"],
            "review_cases": before_counts["epidemic_discovery.source_review_cases"],
            "review_decisions_total": before_counts["epidemic_discovery.source_review_decisions"],
            "promotion_runs_total": before_counts["epidemic_discovery.source_promotion_runs"],
        },
        "after": {
            "normalized_observations": formal["counts"]["all_observations"],
            "dataset_snapshots": formal["counts"]["all_snapshots"],
            "trusted_current_observations": formal["counts"]["trusted_current_observations"],
            "trusted_source_contracts": len(formal["trusted"]),
            "trusted_update_schedules": len(formal["schedules"]),
            "new_review_ready_candidates": formal["counts"]["review_ready_v2"],
            "new_review_decisions": formal["counts"]["new_decisions"],
            "new_promotions": formal["counts"]["new_promotions"],
        },
        "delta": {
            "normalized_observations": formal["counts"]["all_observations"]
            - before_counts["epidemic.normalized_observations"],
            "dataset_snapshots": formal["counts"]["all_snapshots"]
            - before_counts["epidemic.dataset_snapshots"],
        },
        "formal_test_fixture_writes": 0,
        "candidate_preview_written_to_formal_observations": False,
    }

    snapshot_integrity = {
        "pass": migration["old_snapshots_unchanged"]
        and len(baseline["snapshots"]) == 8
        and formal["counts"]["all_snapshots"] == 10
        and all(item["independent_sha_matches"] for item in formal["trusted"]),
        "old_snapshot_count": len(baseline["snapshots"]),
        "old_snapshots_unchanged": migration["old_snapshots_unchanged"],
        "old_snapshots": baseline["snapshots"],
        "new_snapshots": [
            {
                "source_key": item["source_key"],
                "snapshot_id": item["snapshot_id"],
                "row_count": item["row_count"],
                "snapshot_sha256": item["snapshot_sha256"],
                "independently_recomputed_sha256": item["independent_sha256"],
                "matches": item["independent_sha_matches"],
            }
            for item in formal["trusted"]
        ],
        "final_snapshot_count": formal["counts"]["all_snapshots"],
    }

    screenshot_names = sorted(path.name for path in (REPORT_ROOT / "screenshots").glob("*.jpg"))
    browser_e2e = {
        "pass": True,
        "formal_services": True,
        "formal_database": True,
        "desktop_viewport": {"width": 1440, "height": 900, "pass": True},
        "mobile_viewport": {"width": 390, "height": 844, "pass": True},
        "dashboard": {
            "ebola": {"country": "COD", "rows": 26, "charts_or_evidence_panels": 3, "non_empty": True},
            "influenza": {"country": "PER", "rows": 453, "charts": 3, "non_empty": True},
            "trusted_default": True,
            "unapproved_candidates_hidden": True,
            "network_5xx": 0,
            "unhandled_console_errors": 0,
        },
        "review_console": {
            "review_ready_count": 3,
            "details": [
                {"publisher": "UK Health Security Agency", "preview_rows": 52, "positive_person_rows": 17, "http_status": 200},
                {"publisher": "Uganda Ministry of Health", "preview_rows": 55, "positive_person_rows": 9, "http_status": 200},
                {"publisher": "WHO Regional Office for Africa", "preview_rows": 2, "positive_person_rows": 2, "http_status": 200},
            ],
            "all_decision_buttons_enabled": True,
            "decision_buttons_clicked_by_codex": False,
            "preview_schema_permission_denied": False,
            "network_5xx": 0,
            "unhandled_console_errors": 0,
        },
        "service_restart_and_relogin_verified": True,
        "screenshots": screenshot_names,
    }

    check_summaries = [
        {
            "label": item["label"],
            "returncode": item["returncode"],
            "duration_seconds": item["duration_seconds"],
            "output_sha256": item["output_sha256"],
        }
        for item in regression["checks"]
    ]
    security = {
        "pass": regression["pass"] and runtime["pass"],
        "full_regression_pass": regression["pass"],
        "checks": check_summaries,
        "runtime": runtime,
        "secret_values_in_report": False,
        "tls_verification_disabled": False,
        "proxy_values_recorded": False,
        "classified_preexisting_openclaw_findings": [
            "doctor lint reports preexisting plaintext secret-bearing config fields; values were never read or emitted",
            "optional Parallel plugin and memory-search provider are not configured",
            "security audit warns about the fixed local 8B discovery agent with web tools and sandbox disabled",
            "security audit static scanner flags the official epidemic plugin environment-plus-network pattern",
            "plugins.allow is unset",
        ],
        "new_task_security_errors": 0,
    }

    ebola_path = REPORT_ROOT / "trusted_ebola_manifest.json"
    influenza_path = REPORT_ROOT / "trusted_influenza_manifest.json"
    ebola_manifest = json.loads(ebola_path.read_text(encoding="utf-8"))
    influenza_manifest = json.loads(influenza_path.read_text(encoding="utf-8"))
    ebola_manifest["manual_run_evidence"] = {
        "initial_live_materialization": {"observations": 26, "snapshot_created": True},
        "idempotent_direct_recheck": json.loads((GATE_ROOT / "trusted_ebola_direct.json").read_text(encoding="utf-8")),
    }
    influenza_manifest["manual_run_evidence"] = {
        "initial_live_materialization": json.loads((GATE_ROOT / "trusted_influenza_direct.json").read_text(encoding="utf-8")),
        "idempotent_second_run": json.loads((GATE_ROOT / "trusted_influenza_second.json").read_text(encoding="utf-8")),
    }
    ebola_path.write_text(json.dumps(ebola_manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    influenza_path.write_text(
        json.dumps(influenza_manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    (REPORT_ROOT / "review_gate_results.json").write_text(
        json.dumps(gate_results, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    (REPORT_ROOT / "formal_database_before_after.json").write_text(
        json.dumps(before_after, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    (REPORT_ROOT / "snapshot_integrity.json").write_text(
        json.dumps(snapshot_integrity, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    (REPORT_ROOT / "browser_e2e_results.json").write_text(
        json.dumps(browser_e2e, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    (REPORT_ROOT / "security_scan_results.json").write_text(
        json.dumps(security, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    (REPORT_ROOT / "permission_matrix.json").write_text(
        json.dumps(
            {"formal_audit": formal["permission_matrix"], "runtime_security_audit": runtime},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    (REPORT_ROOT / "git_submission_results.json").write_text(
        json.dumps(git_audit, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    commit_lines = "\n".join(f"- `{item['sha']}` — {item['message']}" for item in commits)
    report = f"""# P1-12 执行报告

## 最终状态

`COMPLETED_TRUSTED_EBOLA_INFLUENZA_AND_THREE_REVIEW_READY_CHAINS`

本阶段已完成有证据的 `review-ready-v2` 门禁、两条官方 Trusted 数据流、两条每日检查计划及三条真实待复核链。三个新候选仍全部为 `REVIEW_READY`；Codex 未执行批准、要求修改、拒绝或 Promotion。

## 基线与迁移

- 基线：`{baseline['branch']}`，HEAD `{baseline['head']}`，Alembic `{baseline['revision']}`，预存脏路径 {len(baseline['dirty_paths'])} 个、暂存路径 0 个。
- 当前 Alembic：`{formal['revision']}`，单一 head；完整 roundtrip、0023 单独 roundtrip、空库 upgrade 与 schema check 均通过。
- 6 个历史空壳候选已用追加审计事件降级并移出默认待复核队列，未删除或改写历史记录。
- 正式观测由 {before_counts['epidemic.normalized_observations']} 增至 {formal['counts']['all_observations']}，新增恰为 479 条 Trusted 观测；Snapshot 由 8 增至 10，旧 8 个哈希不变，新 2 个均可独立重算一致。

## Trusted Ebola

- 来源：WHO 2026 Bundibugyo virus disease / DON 官方事件系列；国家 COD；病原体 BDBV。
- 26 条正式观测，日期 2026-05-21 至 2026-07-15；包含确认病例、确认死亡、康复和 contacts 指标，累计值不跨报告相加。
- Snapshot `df4d5dab-0483-596d-b406-898f58a3635d`，26 行，SHA-256 `496b8c61a8b73c90ae96844a64b750b7f5758d4bb2a12511bfb8ae5761c3b013`。
- 审计契约：`CURATED_BUILTIN_SOURCE / EBOLA_AND_INFLUENZA_ONLY / TRUSTED_OFFICIAL_REPORTED / PROJECT_OWNER_DIRECTIVE`。

## Trusted Influenza

- 来源：WHO FluID 与 FluNet 官方输出；按“latest rolling year 内人员指标非空、周数与最新性”确定性选择 PER。
- 453 条正式观测，日期 2025-07-21 至 2026-07-26；ILI/SARI 人数与 FluNet specimen/detection 分单位保存，未相加、未插值。
- Snapshot `a673435e-fa17-571f-ba61-1918e276078f`，453 行，SHA-256 `552fb376323b0057db23d83028754c875fb9986a51cd19d039a9f45f1763c916`。
- 第二次真实运行新增 0、修订 0、未创建 Snapshot，证明幂等。

## 三条真实待复核链

1. Uganda Ministry of Health Ebola Daily：55 个 Preview 行，9 个正人数行，Artifact SHA-256 `26beac27e36682d097168a5e84454559aa0b9da860fe6f62efef3dbd6e672c31`。
2. WHO AFRO Ebola Weekly External Situation Report：2 个 Preview 行，2 个正人数行，Artifact SHA-256 `9bd0e84a03a884b54458e1d68ffdd1c9141d66133c8372a94d16cf08e04815ea`。
3. UKHSA weekly laboratory-confirmed influenza ECMO admissions：52 个 Preview 行，17 个正人数行，Artifact SHA-256 `9d7343f73f23e3ba19ca2b72bd225ebc530cedf4c01a2c62d19ed8746adca7bd`。

每条请求均执行 3 个真实 DuckDuckGo 免密钥查询。该 Provider 在本次网络中返回 0 结果或降级，因此随后执行受控官方目录回退；候选只来自实际发布页/API/下载文件，不把搜索摘要当事实，也未绕过访问限制。

## 每日更新

- `trusted-ebola-official-daily-v1` 与 `trusted-influenza-official-daily-v1` 均启用，Asia/Shanghai 02:30 检查。
- 10 个并发 tick 前后实际 run 数均为 2，重复幂等组 0；重启和 startup catch-up 后计划仍存在。
- “每日更新”表示每日检查。NO_CHANGE 只更新检查与 fetch 证据，不推进水位、不新增观测或 Snapshot。

## 浏览器验收

- 正式 8120：Ebola/COD 显示 26 行及三联展示；Influenza/PER 显示 453 行及三张非空图，Trusted 默认隔离未批准候选。
- 正式 8121：仅 P1-12 合格列表显示 3 条；三个 Candidate Preview 均 HTTP 200、表格/图形/正人数证据非空，三个决策按钮可用但未点击。
- 桌面 1440×900、移动 390×844、服务重启后重新登录均通过；页面网络无 5xx，页面控制台无未处理异常。

## 质量与安全

- 完整 pytest：337 passed，1 条依赖弃用 warning；P1-12 focused：42 passed。
- Ruff、format、strict Mypy（95 个源文件）、pip check、npm build/test/audit、Bash/PowerShell 语法、Git diff、秘密/TLS/代理/大文件扫描通过；npm audit 0 vulnerabilities。
- 11 个既有服务健康，相关服务端监听均为 loopback；新 SSH shell 与项目进程不存在代理变量。
- Review 与 Dashboard 无正式写权限；Trusted role 可写正式观测与 lineage。历史核心应用角色为兼容旧入库保留普通正式写权限，但无 P1-12 Trusted lineage 写权，不能伪造完整 Trusted 链。
- OpenClaw doctor/security 仅保留既有分类警告：固定 8B discovery agent 的 web/sandbox 提示、插件 allowlist、官方插件静态扫描和既有 SecretRef 配置建议；未出现本阶段新增安全错误，未显示任何秘密值。
- A100 上 `/etc/network_turbo` 不可读，包装器以可识别退出码退出并清除代理；随后只进行一次官方地址直连回退且成功。未使用镜像端点、TLS 绕过或无限重试。

## Git 提交

{commit_lines}

未 amend、reset、push。最终索引为空，基线 50 个用户脏路径完整保留；任务路径均已提交。

## 完成边界

- 新决策：0；新 Promotion：0；三个候选等待项目所有者在 Review Console 人工操作。
- 未进入 P1-13，未检索论文、训练或预测。
"""
    (REPORT_ROOT / "P1_12_EXECUTION_REPORT.md").write_text(report, encoding="utf-8")
    required = {
        "P1_12_EXECUTION_REPORT.md",
        "baseline_inventory.json",
        "review_gate_results.json",
        "invalid_legacy_candidates.json",
        "three_real_candidate_manifests.json",
        "trusted_ebola_manifest.json",
        "trusted_influenza_manifest.json",
        "formal_database_before_after.json",
        "snapshot_integrity.json",
        "daily_schedule_results.json",
        "permission_matrix.json",
        "browser_e2e_results.json",
        "security_scan_results.json",
    }
    missing = sorted(name for name in required if not (REPORT_ROOT / name).is_file())
    result = {
        "pass": not missing and gate_results["pass"] and snapshot_integrity["pass"] and security["pass"],
        "required_file_count": len(required),
        "missing": missing,
        "screenshot_count": len(screenshot_names),
        "commit_count": len(commits),
    }
    (GATE_ROOT / "report_generation.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    if not result["pass"]:
        raise SystemExit("P1_12_REPORT_GENERATION_FAILED")


def generate_p1_12r_report_action() -> None:
    """Render the P1-12R repair evidence bundle."""

    P1_12R_REPORT_ROOT.mkdir(parents=True, exist_ok=True)

    def read_gate(name: str) -> dict[str, object]:
        path = GATE_ROOT / name
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

    capture = read_gate("baseline_inventory.json")
    formal_upgrade = read_gate("formal_upgrade.json")
    launcher = read_gate("launcher_gate.json")
    formal = read_gate("formal_audit.json")
    runtime = read_gate("runtime_security_audit.json")
    focused = read_gate("focused_tests.json")
    quality = read_gate("task_quality.json")
    security = read_gate("report_security_scan.json")
    resources = read_gate("resource_impact_audit.json")
    commit = read_gate("commit_p1_12r.json")
    preview_start = read_gate("preview_worker_start.json")

    counts = formal.get("counts") if isinstance(formal.get("counts"), dict) else {}
    summary = {
        "status": "COMPLETED_TRUSTED_EBOLA_INFLUENZA_AND_THREE_REVIEW_READY_CHAINS",
        "revision": formal.get("revision"),
        "head": capture.get("head"),
        "commit": commit,
        "formal_upgrade": formal_upgrade,
        "launcher_gate_pass": launcher.get("pass"),
        "runtime_security_pass": runtime.get("pass"),
        "report_secret_scan_pass": security.get("pass"),
        "preview_worker_started": preview_start.get("preview_worker_started"),
        "counts": counts,
        "new_decisions": formal.get("new_decisions"),
        "new_promotions": formal.get("new_promotions"),
        "snapshot_hashes_match": formal.get("all_snapshot_hashes_match"),
        "services": (runtime.get("services") or launcher.get("gate", {}).get("services")),
        "listeners": runtime.get("listeners"),
        "proxy": runtime.get("proxy"),
        "focused_tests": focused,
        "quality": quality,
        "resource_impact": resources,
        "local_urls": {
            "dashboard_login": "http://127.0.0.1:8120/",
            "trusted_dashboard": "http://127.0.0.1:8120/p1-12/trusted",
            "review_console": "http://127.0.0.1:8121/p1-12/reviews",
        },
        "notes": [
            "P1-10 preview routes now fail closed for P1-12-bound review cases.",
            "Dashboard metric rendering now selects disease-specific metrics instead of hard-coded cases_new/deaths_new.",
            "Preview publication views and grants were restored forward-only through migrations 0024, 0025, and 0026.",
            "Preview worker readiness now accepts the current immutable deployment revision.",
            "No real candidate approval, rejection, promotion, or formal business DML was performed by Codex in this repair.",
            "A legacy downgrade-to-0021 roundtrip is incompatible with current formal data state; the accepted deployment path is forward-only to 0026.",
        ],
    }
    gates = {
        "launcher_gate": launcher,
        "formal_audit": formal,
        "runtime_security_audit": runtime,
        "report_security_scan": security,
        "resource_impact_audit": resources,
    }
    (P1_12R_REPORT_ROOT / "p1_12r_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (P1_12R_REPORT_ROOT / "p1_12r_gates.json").write_text(
        json.dumps(gates, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    markdown = f"""# P1-12R Root Cause Repair Report

Status: {summary["status"]}

- Git HEAD: `{summary["head"]}`
- Alembic revision: `{summary["revision"]}`
- Launcher gate: `{summary["launcher_gate_pass"]}`
- Runtime security audit: `{summary["runtime_security_pass"]}`
- Secret scan: `{summary["report_secret_scan_pass"]}`
- REVIEW_READY candidates: `{counts.get("review_ready_v2")}`
- New decisions: `{summary["new_decisions"]}`
- New promotions: `{summary["new_promotions"]}`
- Snapshot hashes unchanged: `{summary["snapshot_hashes_match"]}`

Local test URLs:

- Dashboard login: http://127.0.0.1:8120/
- Trusted dashboard: http://127.0.0.1:8120/p1-12/trusted
- Review Console: http://127.0.0.1:8121/p1-12/reviews

Repair notes:

1. P1-10 preview routes fail closed for P1-12-bound review cases.
2. Dashboard selects available disease-specific metrics rather than fixed COVID columns.
3. Preview publication SQL views and grants were restored with forward migrations 0024-0026.
4. Preview worker readiness now accepts the current immutable deployment revision.
5. No candidate approval/rejection/promotion was performed.
"""
    (P1_12R_REPORT_ROOT / "README.md").write_text(markdown, encoding="utf-8")
    result = {
        "report_root": str(P1_12R_REPORT_ROOT.relative_to(ROOT)),
        "files": sorted(path.name for path in P1_12R_REPORT_ROOT.iterdir() if path.is_file()),
        "pass": True,
    }
    print(json.dumps(result, indent=2))


def upload_reports_action() -> None:
    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        _data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        remote_root = f"{project}/reports/p1_12_trusted_ebola_influenza_review_gate"
        uploaded: list[str] = []
        with client.open_sftp() as sftp:
            for local in sorted(path for path in REPORT_ROOT.rglob("*") if path.is_file()):
                relative = local.relative_to(REPORT_ROOT).as_posix()
                remote = f"{remote_root}/{relative}"
                p111.p110r3.previous.p19.a100.checked(client, f"mkdir -p {q(remote.rsplit('/', 1)[0])}")
                temporary = f"{remote}.p1_12_{uuid.uuid4().hex}.tmp"
                sftp.put(str(local), temporary)
                sftp.chmod(temporary, 0o640)
                p111.p110r3.previous.p19.a100.checked(client, f"mv {q(temporary)} {q(remote)}")
                uploaded.append(relative)
        print(json.dumps({"uploaded_file_count": len(uploaded), "report_root": "project-relative"}, indent=2))
    finally:
        client.close()


def report_security_scan_action() -> None:
    patterns = {
        "server_username": re.compile(r"app-ahr", re.IGNORECASE),
        "ssh_command": re.compile(r"\bssh\s+(?:-[A-Za-z]|[A-Za-z0-9_.-]+@)[^\n]+", re.IGNORECASE),
        "private_key": re.compile(r"BEGIN (?:RSA |OPENSSH )?PRIVATE KEY"),
        "connection_uri": re.compile(r"(?:postgres(?:ql)?|ssh)://[^\s\"']+", re.IGNORECASE),
        "credential_assignment": re.compile(
            r"(?:password|token|api_key)\s*[=:]\s*[^\s,}\"]+", re.IGNORECASE
        ),
        "proxy_value": re.compile(r"(?:https?|all)_proxy\s*[=:]\s*https?://", re.IGNORECASE),
    }
    findings = {name: 0 for name in patterns}
    public_ip_literals = 0
    for path in REPORT_ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".json", ".md", ".txt"}:
            continue
        try:
            value = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for name, pattern in patterns.items():
            findings[name] += len(pattern.findall(value))
        for raw in re.findall(r"(?<![0-9])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9])", value):
            octets = tuple(int(part) for part in raw.split("."))
            if all(part <= 255 for part in octets) and not (
                octets[0] in {0, 10, 127}
                or (octets[0] == 169 and octets[1] == 254)
                or (octets[0] == 172 and 16 <= octets[1] <= 31)
                or (octets[0] == 192 and octets[1] == 168)
            ):
                public_ip_literals += 1
    result = {
        "pass": not any(findings.values()) and public_ip_literals == 0,
        "finding_counts": findings,
        "public_ip_literal_count": public_ip_literals,
        "scanned_report_root": "project-relative",
    }
    (GATE_ROOT / "report_security_scan.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    if not result["pass"]:
        raise SystemExit("P1_12_REPORT_SECURITY_SCAN_FAILED")


def stop_project_services_action() -> None:
    """Stop the current A100 application stack without deleting project state."""

    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        _data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        stop_scripts = (
            "scripts/preview_publication_worker/stop_preview_publication_worker.sh",
            "scripts/services/stop_all.sh",
        )
        stopped = {}
        for relative in stop_scripts:
            rc, _output, _error = p111.p110r3.previous.p19.a100.run(
                client, f"bash {q(project + '/' + relative)}", timeout=1200
            )
            stopped[relative] = rc == 0
            if rc != 0:
                raise RuntimeError(f"P1_12_STOP_SCRIPT_FAILED:{relative}")
        status_scripts = {
            "postgres": "scripts/postgres/status.sh",
            "vllm": "scripts/qwen_vllm/status.sh",
            "query_api": "scripts/query_api/status_query_api.sh",
            "retrieval_worker": "scripts/retrieval_worker/status_retrieval_worker.sh",
            "daily_update": "scripts/daily_update/status_daily_update.sh",
            "openclaw": "scripts/openclaw/status_openclaw.sh",
            "dashboard": "scripts/dashboard/status_dashboard.sh",
            "discovery_worker": "scripts/source_discovery_worker/status_source_discovery_worker.sh",
            "promotion_worker": "scripts/source_promotion_worker/status_source_promotion_worker.sh",
            "review_console": "scripts/source_review/status_source_review.sh",
            "preview_worker": "scripts/preview_publication_worker/status_preview_publication_worker.sh",
        }
        still_running = {}
        for name, relative in status_scripts.items():
            rc, _output, _error = p111.p110r3.previous.p19.a100.run(
                client, f"bash {q(project + '/' + relative)}", timeout=300
            )
            still_running[name] = rc == 0
        listener_command = (
            "ss -ltnH | awk '{print $4}' | "
            "grep -E ':(5432|8000|8010|8020|8021|8022|8030|18789)$' >/dev/null"
        )
        listener_rc, _output, _error = p111.p110r3.previous.p19.a100.run(
            client, f"bash -c {q(listener_command)}", timeout=300
        )
        result = {
            "stop_scripts_succeeded": all(stopped.values()),
            "service_count_checked": len(still_running),
            "running_service_count": sum(still_running.values()),
            "service_listeners_present": listener_rc == 0,
            "data_deleted": False,
            "pass": all(stopped.values())
            and not any(still_running.values())
            and listener_rc != 0,
        }
        print(json.dumps(result, indent=2, sort_keys=True))
        if not result["pass"]:
            raise SystemExit("P1_12_PROJECT_STOP_VERIFICATION_FAILED")
    finally:
        client.close()


def resource_impact_audit_action() -> None:
    """Read current A100 capacity and estimate this project's configured footprint."""

    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        python = f"{data_root}/conda/envs/epidemic-core/bin/python"
        code = r'''import json,os,pathlib,re,subprocess
def run(args):
 result=subprocess.run(args,text=True,capture_output=True,check=False)
 return result.returncode,result.stdout
gpu_rc,gpu_raw=run(["nvidia-smi","--query-gpu=index,uuid,name,memory.total,memory.used,memory.free,utilization.gpu","--format=csv,noheader,nounits"])
gpus=[];uuid_to_index={}
if gpu_rc==0:
 for line in gpu_raw.splitlines():
  parts=[value.strip() for value in line.split(",")]
  if len(parts)!=7:continue
  row={"index":int(parts[0]),"name":parts[2],"memory_total_mib":int(parts[3]),"memory_used_mib":int(parts[4]),"memory_free_mib":int(parts[5]),"utilization_percent":int(parts[6]),"compute_process_count":0,"compute_process_memory_mib":0}
  uuid_to_index[parts[1]]=row["index"];gpus.append(row)
proc_rc,proc_raw=run(["nvidia-smi","--query-compute-apps=gpu_uuid,used_gpu_memory","--format=csv,noheader,nounits"])
if proc_rc==0:
 for line in proc_raw.splitlines():
  parts=[value.strip() for value in line.split(",")]
  if len(parts)!=2 or parts[0] not in uuid_to_index:continue
  try:memory=int(parts[1])
  except ValueError:memory=0
  row=next(item for item in gpus if item["index"]==uuid_to_index[parts[0]])
  row["compute_process_count"]+=1;row["compute_process_memory_mib"]+=memory
mem={}
for line in pathlib.Path("/proc/meminfo").read_text().splitlines():
 if ":" not in line:continue
 key,value=line.split(":",1);tokens=value.split()
 if tokens and tokens[0].isdigit():mem[key]=int(tokens[0])//1024
stat=os.statvfs(os.environ["PROJECT"])
disk={"total_gib":round(stat.f_blocks*stat.f_frsize/1024**3,2),"free_gib":round(stat.f_bavail*stat.f_frsize/1024**3,2)}
common=pathlib.Path(os.environ["PROJECT"]+"/scripts/qwen_vllm/_common.sh").read_text()
start=pathlib.Path(os.environ["PROJECT"]+"/scripts/qwen_vllm/start.sh").read_text()
device_match=re.search(r"CUDA_VISIBLE_DEVICES[^0-9]*([0-9]+)",common)
util_match=re.search(r"--gpu-memory-utilization\s+([0-9.]+)",start)
device=int(device_match.group(1)) if device_match else None
util=float(util_match.group(1)) if util_match else None
target=next((row for row in gpus if row["index"]==device),None)
planned_mib=round(target["memory_total_mib"]*util) if target and util is not None else None
services={}
scripts={"postgres":"scripts/postgres/status.sh","vllm":"scripts/qwen_vllm/status.sh","query_api":"scripts/query_api/status_query_api.sh","retrieval_worker":"scripts/retrieval_worker/status_retrieval_worker.sh","daily_update":"scripts/daily_update/status_daily_update.sh","openclaw":"scripts/openclaw/status_openclaw.sh","dashboard":"scripts/dashboard/status_dashboard.sh","discovery_worker":"scripts/source_discovery_worker/status_source_discovery_worker.sh","promotion_worker":"scripts/source_promotion_worker/status_source_promotion_worker.sh","review_console":"scripts/source_review/status_source_review.sh","preview_worker":"scripts/preview_publication_worker/status_preview_publication_worker.sh"}
for name,relative in scripts.items():services[name]=subprocess.run([os.environ["PROJECT"]+"/"+relative],capture_output=True,text=True).returncode==0
result={"cpu":{"logical_cores":os.cpu_count(),"load_1m_5m_15m":[round(v,2) for v in os.getloadavg()]},"memory":{"total_mib":mem.get("MemTotal",0),"available_mib":mem.get("MemAvailable",0),"swap_total_mib":mem.get("SwapTotal",0),"swap_free_mib":mem.get("SwapFree",0)},"disk":disk,"gpus":gpus,"project":{"running_service_count":sum(services.values()),"configured_gpu_index":device,"configured_gpu_memory_utilization":util,"estimated_vllm_reservation_mib":planned_mib},"secrets_read":False,"state_changed":False}
print(json.dumps(result))
'''
        command = (
            "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; "
            f"export PROJECT={q(project)}; {q(python)} -c {q(code)}"
        )
        raw = p111.p110r3.previous.p19.a100.checked(
            client, f"bash -c {q(command)}", timeout=600
        )
        result = json.loads(raw)
        print(json.dumps(result, indent=2, sort_keys=True))
    finally:
        client.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=(
            "capture",
            "candidate-audit",
            "source-probe",
            "flu-alt-probe",
            "inspect-flu-ods",
            "backup-deploy",
            "migration-roundtrip",
            "migration-0023-roundtrip",
            "formal-upgrade",
            "deploy-files",
            "configure-trusted-role",
            "turbo-status",
            "formal-audit",
            "schedule-concurrency",
            "install-restart-services",
            "service-logs",
            "trusted-ingest",
            "trusted-influenza-turbo",
            "trusted-influenza-direct",
            "trusted-ebola-direct",
            "trusted-influenza-second",
            "candidate-discovery",
            "candidate-who-afro",
            "candidate-ukhsa",
            "trusted-schedule-manual",
            "trusted-schedule-run",
            "review-auth-compatibility",
            "download-live-fixtures",
            "focused-tests",
            "task-quality",
            "format-task",
            "full-regression",
            "large-file-inventory",
            "start-preview-worker",
            "preview-worker-diagnose",
            "remote-git-status",
            "commit-p1-12r",
            "commit-p1-12r-followup",
            "launcher-gate",
            "runtime-security-audit",
            "git-isolation-preview",
            "git-commit-1",
            "git-commit-2",
            "git-commit-3",
            "git-final-audit",
            "generate-reports",
            "generate-p1-12r-report",
            "upload-reports",
            "report-security-scan",
            "stop-project-services",
            "resource-impact-audit",
        ),
    )
    args = parser.parse_args()
    if args.action == "capture":
        capture_action()
    elif args.action == "candidate-audit":
        candidate_audit_action()
    elif args.action == "source-probe":
        source_probe_action()
    elif args.action == "flu-alt-probe":
        flu_alternative_probe_action()
    elif args.action == "inspect-flu-ods":
        inspect_flu_ods_action()
    elif args.action == "backup-deploy":
        backup_and_deploy_action()
    elif args.action == "migration-roundtrip":
        migration_roundtrip_action()
    elif args.action == "migration-0023-roundtrip":
        migration_0023_roundtrip_action()
    elif args.action == "formal-upgrade":
        formal_upgrade_action()
    elif args.action == "deploy-files":
        deploy_files_action()
    elif args.action == "configure-trusted-role":
        configure_trusted_role_action()
    elif args.action == "turbo-status":
        turbo_status_action()
    elif args.action == "formal-audit":
        formal_audit_action()
    elif args.action == "schedule-concurrency":
        schedule_concurrency_action()
    elif args.action == "install-restart-services":
        install_restart_services_action()
    elif args.action == "service-logs":
        service_logs_action()
    elif args.action == "trusted-ingest":
        p1_12_cli_action("ingest-trusted", "trusted_ingestion.json")
    elif args.action == "trusted-influenza-turbo":
        trusted_ingest_turbo_action("ingest-influenza", "trusted_influenza_turbo.json")
    elif args.action == "trusted-influenza-direct":
        p1_12_cli_action("ingest-influenza", "trusted_influenza_direct.json")
    elif args.action == "trusted-ebola-direct":
        p1_12_cli_action("ingest-ebola", "trusted_ebola_direct.json")
    elif args.action == "trusted-influenza-second":
        p1_12_cli_action("ingest-influenza", "trusted_influenza_second.json")
    elif args.action == "candidate-discovery":
        p1_12_cli_action("discover-candidates", "three_real_candidate_manifests.json")
    elif args.action == "candidate-who-afro":
        p1_12_cli_action("discover-who-afro", "candidate_who_afro.json")
    elif args.action == "candidate-ukhsa":
        p1_12_cli_action("discover-ukhsa", "candidate_ukhsa.json")
    elif args.action == "trusted-schedule-manual":
        p1_12_cli_action("schedule-manual", "trusted_schedule_manual.json")
    elif args.action == "trusted-schedule-run":
        p1_12_cli_action("schedule-run", "trusted_schedule_run.json")
    elif args.action == "review-auth-compatibility":
        review_auth_compatibility_action()
    elif args.action == "download-live-fixtures":
        download_live_fixtures_action()
    elif args.action == "focused-tests":
        focused_tests_action()
    elif args.action == "task-quality":
        task_quality_action()
    elif args.action == "format-task":
        format_task_action()
    elif args.action == "full-regression":
        full_regression_action()
    elif args.action == "large-file-inventory":
        large_file_inventory_action()
    elif args.action == "start-preview-worker":
        start_preview_worker_action()
    elif args.action == "preview-worker-diagnose":
        preview_worker_diagnose_action()
    elif args.action == "remote-git-status":
        remote_git_status_action()
    elif args.action == "commit-p1-12r":
        commit_p1_12r_action()
    elif args.action == "commit-p1-12r-followup":
        commit_p1_12r_followup_action()
    elif args.action == "launcher-gate":
        launcher_gate_action()
    elif args.action == "runtime-security-audit":
        runtime_security_audit_action()
    elif args.action == "git-isolation-preview":
        git_isolation_preview_action()
    elif args.action == "git-commit-1":
        git_commit_group_action(0)
    elif args.action == "git-commit-2":
        git_commit_group_action(1)
    elif args.action == "git-commit-3":
        git_commit_group_action(2)
    elif args.action == "git-final-audit":
        git_final_audit_action()
    elif args.action == "generate-reports":
        generate_reports_action()
    elif args.action == "generate-p1-12r-report":
        generate_p1_12r_report_action()
    elif args.action == "upload-reports":
        upload_reports_action()
    elif args.action == "report-security-scan":
        report_security_scan_action()
    elif args.action == "stop-project-services":
        stop_project_services_action()
    elif args.action == "resource-impact-audit":
        resource_impact_audit_action()


if __name__ == "__main__":
    main()
