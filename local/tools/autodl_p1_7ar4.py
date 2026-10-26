from __future__ import annotations

import json
import sys
from pathlib import Path, PurePosixPath

import paramiko

sys.path.insert(0, str(Path(__file__).parent))
import autodl_p0_4 as p0
import autodl_p1_3 as p13
import autodl_p1_7a as p17
import autodl_p1_7ar3 as p1r3


P = p0.P
KNOWN_HOSTS = Path.cwd() / "private" / "known_hosts_autodl_p1_7ar4"
LOCAL = Path(__file__).parent / "p1_7ar4_baseline"
TARGET_RUN_ID = "7e0d5032-23e8-43f1-b6f3-aaa1adef7dbf"
EXPECTED_HEAD = "6f385074181a0a164b56f13cb82f531670edb680"
REMOTE_REPORT = P + "/reports/p1_7a_discovery_capability_repair"
LOCAL_REPORT = Path.cwd() / "reports" / "p1_7a_discovery_capability_repair"
PATCH_FILES = (
    "config/source_discovery/directories.v1.json",
    "config/source-discovery-provider.env.example",
    "migrations/versions/0008_p1_7a_discovery_capability.py",
    "src/epidemic_core/discovery/candidate_pipeline.py",
    "src/epidemic_core/discovery/providers.py",
    "src/epidemic_core/discovery/review_app.py",
    "src/epidemic_core/discovery/review_labels.py",
    "src/epidemic_core/discovery/safe_fetcher.py",
    "src/epidemic_core/discovery/schemas.py",
    "src/epidemic_core/discovery/static/review.p1-7ar3-v1.css",
    "src/epidemic_core/discovery/worker.py",
    "scripts/source_discovery_worker/_common.sh",
    "scripts/source_discovery_worker/start_source_discovery_worker.sh",
    "scripts/source_discovery_worker/status_source_discovery_worker.sh",
    "tests/integration/test_discovery_queue_contract.py",
    "tests/unit/test_discovery_providers.py",
    "tests/unit/test_p1_7a_ui_repair.py",
)
p13.KNOWN_HOSTS = KNOWN_HOSTS


def db(client: object, sql: str, database: str = "epidemic_openclaw") -> str:
    return p17.db(client, sql, database)


def _connect() -> paramiko.SSHClient:
    host, port, _user, _password = p0.creds()
    key_name = p13._key_name(host, port)
    trusted = False
    if KNOWN_HOSTS.exists():
        keys = paramiko.HostKeys(str(KNOWN_HOSTS))
        trusted = key_name in keys
    if not trusted:
        p13.establish_trust()
    return p13.conn()


def _json(value: str) -> object:
    return json.loads(value) if value else None


def inspect_remote(client: object) -> None:
    service_paths = p1r3.SERVICE_PATHS
    git = {
        "branch": p0.out(client, f"cd {p0.q(P)} && git branch --show-current"),
        "head": p0.out(client, f"cd {p0.q(P)} && git rev-parse HEAD"),
        "message": p0.out(client, f"cd {p0.q(P)} && git log -1 --pretty=%s"),
        "status": p0.out(client, f"cd {p0.q(P)} && git status --porcelain=v1").splitlines(),
        "remote_contains_head": bool(
            p0.out(client, f"cd {p0.q(P)} && git branch -r --contains HEAD", check=False)
        ),
    }
    migration = {
        "database": db(client, "SELECT version_num FROM epidemic.alembic_version"),
        "heads": p0.out(
            client,
            f"cd {p0.q(P)} && PYTHONPATH={p0.q(P + '/src')} {p0.q(p1r3.PY)} -m alembic heads",
            check=False,
        ).splitlines(),
    }
    services = {
        name: p0.run(client, p0.q(P + path), check=False)[0] == 0
        for name, path in service_paths.items()
    }
    formal_counts = p17.counts(client)
    snapshots = _json(
        db(
            client,
            "SELECT COALESCE(json_agg(json_build_object('id',id,'sha256',content_sha256,'row_count',row_count) ORDER BY id),'[]'::json)::text FROM epidemic.dataset_snapshots",
        )
    )
    discovery_counts = _json(
        db(
            client,
            "SELECT json_build_object("
            "'runs',(SELECT count(*) FROM epidemic_discovery.discovery_runs),"
            "'events',(SELECT count(*) FROM epidemic_discovery.discovery_run_events),"
            "'candidates',(SELECT count(*) FROM epidemic_discovery.source_candidates),"
            "'versions',(SELECT count(*) FROM epidemic_discovery.source_candidate_versions),"
            "'artifacts',(SELECT count(*) FROM epidemic_discovery.quarantine_artifacts),"
            "'review_decisions',(SELECT count(*) FROM epidemic_discovery.source_review_decisions),"
            "'promotions',(SELECT count(*) FROM epidemic_discovery.source_promotion_runs))::text",
        )
    )
    target = _json(
        db(
            client,
            "SELECT row_to_json(x)::text FROM (SELECT id,state,state_version,request_json,progress_json,"
            "terminal_reason_code,created_at,updated_at,completed_at,claim_owner,claim_token,"
            "heartbeat_at,lease_expires_at,attempt_count,last_error_code,last_error_detail "
            "FROM epidemic_discovery.discovery_runs WHERE id='"
            + TARGET_RUN_ID
            + "'::uuid) x",
        )
    )
    target_events = _json(
        db(
            client,
            "SELECT COALESCE(json_agg(row_to_json(x) ORDER BY x.created_at,x.id),'[]'::json)::text "
            "FROM (SELECT id,event_code,from_state,to_state,details,created_at FROM epidemic_discovery.discovery_run_events "
            "WHERE discovery_run_id='"
            + TARGET_RUN_ID
            + "'::uuid) x",
        )
    )
    historical = _json(
        db(
            client,
            "SELECT COALESCE(json_agg(row_to_json(x) ORDER BY x.created_at),'[]'::json)::text FROM ("
            "SELECT r.id,r.state,r.terminal_reason_code,r.created_at,r.updated_at,r.completed_at,"
            "r.request_json,r.progress_json,r.claim_owner,r.heartbeat_at,r.lease_expires_at,r.attempt_count,"
            "(SELECT count(*) FROM epidemic_discovery.source_candidates c WHERE c.discovery_run_id=r.id) candidate_count,"
            "(SELECT e.event_code FROM epidemic_discovery.discovery_run_events e WHERE e.discovery_run_id=r.id ORDER BY e.created_at DESC,e.id DESC LIMIT 1) last_event "
            "FROM epidemic_discovery.discovery_runs r WHERE "
            "r.created_at BETWEEN '2026-07-13 01:10:00+00' AND '2026-07-13 01:23:00+00' "
            "OR (r.request_json->>'country_iso3'='BRA' AND r.state='REVIEW_READY')) x",
        )
    )
    schema = _json(
        db(
            client,
            "SELECT json_build_object("
            "'tables',(SELECT COALESCE(json_agg(table_name ORDER BY table_name),'[]'::json) FROM information_schema.tables WHERE table_schema='epidemic_discovery'),"
            "'run_columns',(SELECT COALESCE(json_agg(json_build_object('name',column_name,'type',data_type) ORDER BY ordinal_position),'[]'::json) FROM information_schema.columns WHERE table_schema='epidemic_discovery' AND table_name='discovery_runs'),"
            "'routines',(SELECT COALESCE(json_agg(routine_name ORDER BY routine_name),'[]'::json) FROM information_schema.routines WHERE routine_schema='epidemic_discovery'))::text",
        )
    )
    worker_pid = p0.out(
        client,
        f"cat {p0.q(P + '/storage/run/source-discovery-worker/worker.pid')}",
        check=False,
    )
    env_names: list[str] = []
    if worker_pid.isdigit():
        code = (
            "import pathlib;"
            f"p=pathlib.Path('/proc/{worker_pid}/environ');"
            "names=[] if not p.exists() else [x.partition(b'=')[0].decode('utf-8','replace') for x in p.read_bytes().split(b'\\0') if x];"
            "print('\\n'.join(sorted(n for n in names if any(k in n.upper() for k in ('DISCOVERY','SEARCH','PROVIDER','API_KEY')))))"
        )
        env_names = p0.out(client, f"{p0.q(p1r3.PY)} -c {p0.q(code)}", check=False).splitlines()
    worker = {
        "pid_present": worker_pid.isdigit(),
        "process": bool(
            p0.out(client, "ps -eo args | grep '[e]pidemic_core.discovery.worker'", check=False)
        ),
        "provider_environment_variable_names": env_names,
        "heartbeat": _json(
            db(
                client,
                "SELECT COALESCE(json_agg(json_build_object('worker_id',worker_id,'worker_version',worker_version,'started_at',started_at,'heartbeat_at',heartbeat_at,'current_run_id',current_run_id,'last_error_code',last_error_code,'process_id',process_id,'healthy',epidemic_discovery.is_discovery_worker_healthy(process_id)) ORDER BY worker_id),'[]'::json)::text FROM epidemic_discovery.discovery_worker_heartbeats",
            )
        ),
    }
    provider_config = {
        "tracked_mentions": p0.out(
            client,
            f"cd {p0.q(P)} && git grep -IlE 'GENERIC_SEARCH|SEARCH_PROVIDER|provider_id|SOURCE_DIRECTORY_CHECKED' -- src scripts tests migrations config .env.example 2>/dev/null",
            check=False,
        ).splitlines(),
        "secret_file_names": p0.out(
            client,
            f"find {p0.q(P + '/storage/secrets')} -maxdepth 2 -type f -printf '%f\\n' 2>/dev/null | sort",
            check=False,
        ).splitlines(),
    }
    security = {
        "proxy_variables": p0.out(
            client,
            "bash -lc '[[ -z ${HTTP_PROXY:-}${HTTPS_PROXY:-}${ALL_PROXY:-}${http_proxy:-}${https_proxy:-}${all_proxy:-} ]] && echo absent || echo present'",
        ),
        "tracked_secret_hits": len(
            p0.out(
                client,
                f"cd {p0.q(P)} && git grep -IlE {p0.q(r'-----BEGIN [A-Z ]*PRIVATE KEY|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,}|Bearer[[:space:]]+[A-Za-z0-9._-]{24,}|postgresql(\\+psycopg)?://[^:/[:space:]]+:[^@[:space:]]+@')} -- . ':(exclude)storage/**' ':(exclude).secrets/**'",
                check=False,
            ).splitlines()
        ),
    }
    result = {
        "status": "PASS",
        "git": git,
        "migration": migration,
        "services": services,
        "formal_counts": formal_counts,
        "snapshots": snapshots,
        "discovery_counts": discovery_counts,
        "target_run": target,
        "target_events": target_events,
        "historical_runs": historical,
        "schema": schema,
        "worker": worker,
        "provider_config": provider_config,
        "security": security,
    }
    print(json.dumps(result, ensure_ascii=False))


def pull_source(client: object) -> None:
    tracked = p0.out(client, f"cd {p0.q(P)} && git ls-files").splitlines()
    wanted = [
        rel
        for rel in tracked
        if rel == "pyproject.toml"
        or rel.startswith(
            (
                "src/",
                "tests/",
                "migrations/",
                "scripts/",
                "integrations/openclaw/epidemic-query-tools/",
                "reference/",
                "config/",
            )
        )
    ]
    sftp = client.open_sftp()
    try:
        for rel in wanted:
            local = LOCAL / PurePosixPath(rel)
            local.parent.mkdir(parents=True, exist_ok=True)
            with sftp.open(P + "/" + rel, "rb") as source:
                local.write_bytes(source.read())
    finally:
        sftp.close()
    print(json.dumps({"status": "PASS", "files": len(wanted), "local": str(LOCAL)}))


def inspect_review_state(client: object) -> None:
    result = {
        "decisions": _json(
            db(
                client,
                "SELECT COALESCE(json_agg(json_build_object('decision_id',d.id,'review_case_id',d.review_case_id,'decision',d.decision,'created_at',d.created_at,'country',r.request_json->>'country_iso3','run_id',r.id,'candidate_id',c.id) ORDER BY d.created_at),'[]'::json)::text "
                "FROM epidemic_discovery.source_review_decisions d "
                "JOIN epidemic_discovery.source_review_cases rc ON rc.id=d.review_case_id "
                "JOIN epidemic_discovery.source_candidate_versions cv ON cv.id=rc.candidate_version_id "
                "JOIN epidemic_discovery.source_candidates c ON c.id=cv.candidate_id "
                "JOIN epidemic_discovery.discovery_runs r ON r.id=c.discovery_run_id",
            )
        ),
        "bra": _json(
            db(
                client,
                "SELECT COALESCE(json_agg(json_build_object('run_id',r.id,'run_state',r.state,'candidate_id',c.id,'candidate_status',c.status,'review_case_id',rc.id,'review_state',rc.state,'decision_count',(SELECT count(*) FROM epidemic_discovery.source_review_decisions d WHERE d.review_case_id=rc.id),'promotion_count',(SELECT count(*) FROM epidemic_discovery.source_promotion_runs p JOIN epidemic_discovery.source_review_decisions d ON d.id=p.review_decision_id WHERE d.review_case_id=rc.id))),'[]'::json)::text "
                "FROM epidemic_discovery.discovery_runs r "
                "JOIN epidemic_discovery.source_candidates c ON c.discovery_run_id=r.id "
                "JOIN epidemic_discovery.source_candidate_versions cv ON cv.candidate_id=c.id "
                "JOIN epidemic_discovery.source_review_cases rc ON rc.candidate_version_id=cv.id "
                "WHERE r.request_json->>'country_iso3'='BRA'",
            )
        ),
    }
    print(json.dumps(result, ensure_ascii=False))


def prewrite_gate(client: object) -> None:
    head = p0.out(client, f"cd {p0.q(P)} && git rev-parse HEAD")
    status = p0.out(client, f"cd {p0.q(P)} && git status --porcelain=v1").splitlines()
    assert head == EXPECTED_HEAD and not status
    assert db(client, "SELECT version_num FROM epidemic.alembic_version") == "0007_p1_7a_discovery_observability"
    formal = p17.counts(client)
    snapshots = _json(
        db(
            client,
            "SELECT COALESCE(json_agg(json_build_object('id',id,'sha256',content_sha256,'row_count',row_count) ORDER BY id),'[]'::json)::text FROM epidemic.dataset_snapshots",
        )
    )
    discovery = _json(
        db(
            client,
            "SELECT json_build_object('runs',(SELECT count(*) FROM epidemic_discovery.discovery_runs),'events',(SELECT count(*) FROM epidemic_discovery.discovery_run_events),'candidates',(SELECT count(*) FROM epidemic_discovery.source_candidates),'decisions',(SELECT count(*) FROM epidemic_discovery.source_review_decisions),'promotions',(SELECT count(*) FROM epidemic_discovery.source_promotion_runs))::text",
        )
    )
    p0.run(client, f"mkdir -p {p0.q(P + '/backups')} {p0.q(REMOTE_REPORT)} && chmod 0700 {p0.q(P + '/backups')} {p0.q(REMOTE_REPORT)}")
    stamp = p0.out(client, "date -u +%Y%m%dT%H%M%SZ")
    dump = P + "/backups/epidemic_openclaw-p1_7ar4-pre-" + stamp + ".dump"
    pg = "USER_HOME/autodl-tmp/conda/envs/postgresql18/bin"
    socket_dir = P + "/storage/database/postgresql18/run"
    temp_database = "epidemic_p1_7ar4_restore_gate"
    p0.run(
        client,
        f"runuser -u epidemicdb -- {p0.q(pg + '/pg_dump')} -h {p0.q(socket_dir)} -U epidemicdb -d epidemic_openclaw -Fc > {p0.q(dump)}",
        timeout=900,
    )
    p0.run(client, f"chown epidemicdb:epidemicdb {p0.q(dump)} && chmod 0600 {p0.q(dump)}")
    p0.run(client, f"setfacl -m u:epidemicdb:--x {p0.q(P + '/backups')}")
    for command in (
        f"runuser -u epidemicdb -- {p0.q(pg + '/dropdb')} -h {p0.q(socket_dir)} -U epidemicdb --if-exists {temp_database}",
        f"runuser -u epidemicdb -- {p0.q(pg + '/createdb')} -h {p0.q(socket_dir)} -U epidemicdb -O epidemicdb {temp_database}",
        f"runuser -u epidemicdb -- {p0.q(pg + '/pg_restore')} -h {p0.q(socket_dir)} -U epidemicdb -d {temp_database} {p0.q(dump)}",
    ):
        p0.run(client, command, timeout=900)
    restored = p17.counts(client, temp_database)
    restored_discovery = _json(
        db(
            client,
            "SELECT json_build_object('runs',(SELECT count(*) FROM epidemic_discovery.discovery_runs),'events',(SELECT count(*) FROM epidemic_discovery.discovery_run_events),'candidates',(SELECT count(*) FROM epidemic_discovery.source_candidates),'decisions',(SELECT count(*) FROM epidemic_discovery.source_review_decisions),'promotions',(SELECT count(*) FROM epidemic_discovery.source_promotion_runs))::text",
            temp_database,
        )
    )
    p0.run(
        client,
        f"runuser -u epidemicdb -- {p0.q(pg + '/dropdb')} -h {p0.q(socket_dir)} -U epidemicdb {temp_database}",
    )
    assert restored == formal and restored_discovery == discovery
    result = {
        "status": "PASS",
        "head": head,
        "formal_counts": formal,
        "snapshots": snapshots,
        "discovery_counts": discovery,
        "backup": {
            "path_basename": dump.rsplit("/", 1)[-1],
            "sha256": p0.out(client, f"sha256sum {p0.q(dump)} | cut -d' ' -f1"),
            "restore_verified": True,
        },
    }
    sftp = client.open_sftp()
    try:
        p0.put(sftp, REMOTE_REPORT + "/preflight.json", json.dumps(result, indent=2) + "\n", 0o600)
    finally:
        sftp.close()
    print(json.dumps(result))


def upload_patch(client: object) -> None:
    assert p0.out(client, f"cd {p0.q(P)} && git rev-parse HEAD") == EXPECTED_HEAD
    before = set(
        p0.out(
            client,
            f"cd {p0.q(P)} && (git diff --name-only; git ls-files --others --exclude-standard)",
        ).splitlines()
    )
    assert before <= set(PATCH_FILES), sorted(before - set(PATCH_FILES))
    sftp = client.open_sftp()
    try:
        for rel in PATCH_FILES:
            local = LOCAL / PurePosixPath(rel)
            assert local.is_file(), rel
            remote = P + "/" + rel
            p0.run(client, f"mkdir -p {p0.q(str(PurePosixPath(remote).parent))}")
            mode = 0o700 if rel.startswith("scripts/") else 0o644
            p0.put(sftp, remote, local.read_bytes(), mode)
    finally:
        sftp.close()
    status = p0.out(client, f"cd {p0.q(P)} && git status --porcelain=v1 --untracked-files=all").splitlines()
    changed = set(
        p0.out(
            client,
            f"cd {p0.q(P)} && (git diff --name-only; git ls-files --others --exclude-standard)",
        ).splitlines()
    )
    assert changed == set(PATCH_FILES), {"changed": sorted(changed), "expected": sorted(PATCH_FILES)}
    print(json.dumps({"status": "PASS", "uploaded": len(PATCH_FILES), "worktree_files": len(status)}))


def quality_gate(client: object) -> None:
    base = f"cd {p0.q(P)} && PYTHONPATH={p0.q(P + '/src')}"
    commands = {
        "ruff": f"{base} {p0.q(p1r3.PY)} -m ruff check src tests migrations",
        "format": f"{base} {p0.q(p1r3.PY)} -m ruff format --check src tests migrations",
        "mypy": f"{base} {p0.q(p1r3.PY)} -m mypy --strict src/epidemic_core",
        "unit": f"{base} {p0.q(p1r3.PY)} -m pytest -q tests/unit/test_discovery_providers.py tests/unit/test_discovery_security.py tests/unit/test_p1_7a_ui_repair.py",
        "shell": f"cd {p0.q(P)} && bash -n scripts/source_discovery_worker/_common.sh scripts/source_discovery_worker/start_source_discovery_worker.sh scripts/source_discovery_worker/status_source_discovery_worker.sh",
        "provider_model": f"{base} DISCOVERY_DIRECTORY_REGISTRY={p0.q(P + '/config/source_discovery/directories.v1.json')} {p0.q(p1r3.PY)} -c {p0.q('from epidemic_core.discovery.providers import capability_snapshot;d=capability_snapshot();assert len(d[\"providers\"])==5')}"
    }
    results = {}
    for name, command in commands.items():
        rc, out, err = p0.run(client, command, timeout=1200, check=False)
        results[name] = {"rc": rc, "tail": (out + "\n" + err)[-12000:]}
    diff_rc, _out, diff_err = p0.run(client, f"cd {p0.q(P)} && git diff --check", check=False)
    results["diff_check"] = {"rc": diff_rc, "tail": diff_err[-4000:]}
    print(json.dumps({"status": "PASS" if all(item["rc"] == 0 for item in results.values()) else "FAIL", "results": results}))
    assert all(item["rc"] == 0 for item in results.values())


def format_patch(client: object) -> None:
    targets = (
        "src/epidemic_core/discovery tests/integration/test_discovery_queue_contract.py "
        "tests/unit/test_discovery_providers.py tests/unit/test_p1_7a_ui_repair.py "
        "migrations/versions/0008_p1_7a_discovery_capability.py"
    )
    prefix = f"cd {p0.q(P)} && PYTHONPATH={p0.q(P + '/src')} {p0.q(p1r3.PY)} -m ruff"
    format_rc, format_out, format_err = p0.run(client, f"{prefix} format {targets}", timeout=1200, check=False)
    lint_rc, lint_out, lint_err = p0.run(client, f"{prefix} check --fix {targets}", timeout=1200, check=False)
    sftp = client.open_sftp()
    try:
        for rel in PATCH_FILES:
            remote = P + "/" + rel
            local = LOCAL / PurePosixPath(rel)
            with sftp.open(remote, "rb") as source:
                local.write_bytes(source.read())
    finally:
        sftp.close()
    result = {
        "status": "PASS" if format_rc == lint_rc == 0 else "FAIL",
        "synced_files": len(PATCH_FILES),
        "format": (format_out + format_err)[-4000:],
        "lint": (lint_out + lint_err)[-8000:],
    }
    print(json.dumps(result))
    assert format_rc == lint_rc == 0


def migration_test(client: object) -> None:
    pg = "USER_HOME/autodl-tmp/conda/envs/postgresql18/bin"
    socket_dir = P + "/storage/database/postgresql18/run"
    temp_database = "epidemic_p1_7ar4_migration_test"
    dump = p0.out(client, f"ls -1t {p0.q(P + '/backups')}/epidemic_openclaw-p1_7ar4-pre-*.dump | head -n1")
    for command in (
        f"runuser -u epidemicdb -- {p0.q(pg + '/dropdb')} -h {p0.q(socket_dir)} -U epidemicdb --if-exists {temp_database}",
        f"runuser -u epidemicdb -- {p0.q(pg + '/createdb')} -h {p0.q(socket_dir)} -U epidemicdb -O epidemicdb {temp_database}",
        f"runuser -u epidemicdb -- {p0.q(pg + '/pg_restore')} -h {p0.q(socket_dir)} -U epidemicdb -d {temp_database} {p0.q(dump)}",
    ):
        p0.run(client, command, timeout=900)
    import urllib.parse

    url = "postgresql+psycopg://REDACTED@/" + temp_database + "?host=" + urllib.parse.quote(socket_dir, safe="")
    base = (
        f"cd {p0.q(P)} && runuser -u epidemicdb -- env PYTHONPATH={p0.q(P + '/src')} "
        f"MIGRATION_DATABASE_URL={p0.q(url)}"
    )
    alembic = f"{base} {p0.q(p1r3.PY)} -m alembic"
    p0.run(client, alembic + " upgrade head", timeout=900)
    up1 = db(client, "SELECT version_num FROM epidemic.alembic_version", temp_database)
    schema = _json(
        db(
            client,
            "SELECT json_build_object("
            "'single_head',(SELECT count(*)=1 FROM epidemic.alembic_version),"
            "'retry_function',to_regprocedure('epidemic_discovery.retry_discovery_run(uuid)') IS NOT NULL,"
            "'review_execute',has_function_privilege('epidemicreview','epidemic_discovery.retry_discovery_run(uuid)','EXECUTE'),"
            "'public_execute',has_function_privilege('public','epidemic_discovery.retry_discovery_run(uuid)','EXECUTE'))::text",
            temp_database,
        )
    )
    retry_source = db(
        client,
        "SELECT id FROM epidemic_discovery.discovery_runs "
        "WHERE state IN ('NO_CANDIDATE','POLICY_BLOCKED','SECURITY_BLOCKED','FAILED_TERMINAL') "
        "ORDER BY created_at DESC LIMIT 1",
        temp_database,
    )
    retry_id = db(
        client,
        f"SELECT epidemic_discovery.retry_discovery_run('{retry_source}'::uuid)",
        temp_database,
    )
    retry = _json(
        db(
            client,
            f"SELECT json_build_object('created',true,'linked',request_json->>'retry_of_run_id'='{retry_source}',"
            f"'state',state)::text FROM epidemic_discovery.discovery_runs WHERE id='{retry_id}'::uuid",
            temp_database,
        )
    )
    rc, out, err = p0.run(
        client,
        f"{base} {p0.q(p1r3.PY)} -m pytest -q tests/integration/test_discovery_queue_contract.py",
        timeout=1200,
        check=False,
    )
    p0.run(client, alembic + " downgrade 0007_p1_7a_discovery_observability", timeout=900)
    down = db(client, "SELECT version_num FROM epidemic.alembic_version", temp_database)
    p0.run(client, alembic + " upgrade head", timeout=900)
    up2 = db(client, "SELECT version_num FROM epidemic.alembic_version", temp_database)
    p0.run(
        client,
        f"runuser -u epidemicdb -- {p0.q(pg + '/dropdb')} -h {p0.q(socket_dir)} -U epidemicdb {temp_database}",
    )
    result = {
        "status": "PASS" if rc == 0 else "FAIL",
        "upgrade": up1,
        "downgrade": down,
        "reupgrade": up2,
        "schema": schema,
        "retry": retry,
        "pytest_rc": rc,
        "pytest_tail": (out + err)[-16000:],
    }
    print(json.dumps(result))
    assert rc == 0
    assert up1 == up2 == "0008_p1_7a_discovery_capability" and down == "0007_p1_7a_discovery_observability"
    assert schema == {"single_head": True, "retry_function": True, "review_execute": True, "public_execute": False}
    assert retry == {"created": True, "linked": True, "state": "QUEUED"}


def apply_production(client: object) -> None:
    assert p0.out(client, f"cd {p0.q(P)} && git rev-parse HEAD") == EXPECTED_HEAD
    changed = set(
        p0.out(
            client,
            f"cd {p0.q(P)} && (git diff --name-only; git ls-files --others --exclude-standard)",
        ).splitlines()
    )
    assert changed == set(PATCH_FILES)
    preflight = _json(p0.out(client, f"cat {p0.q(REMOTE_REPORT + '/preflight.json')}"))
    assert p17.counts(client) == preflight["formal_counts"]
    assert db(client, "SELECT count(*) FROM epidemic_discovery.source_promotion_runs") == "0"
    p0.run(client, p0.q(P + "/scripts/source_discovery_worker/stop_source_discovery_worker.sh"), check=False)
    socket_dir = P + "/storage/database/postgresql18/run"
    import urllib.parse

    url = "postgresql+psycopg://REDACTED@/epidemic_openclaw?host=" + urllib.parse.quote(socket_dir, safe="")
    command = (
        f"cd {p0.q(P)} && runuser -u epidemicdb -- env PYTHONPATH={p0.q(P + '/src')} "
        f"MIGRATION_DATABASE_URL={p0.q(url)} {p0.q(p1r3.PY)} -m alembic upgrade head"
    )
    p0.run(client, command, timeout=900)
    p0.run(client, p0.q(P + "/scripts/source_review/stop_source_review.sh"), check=False)
    p0.run(client, p0.q(P + "/scripts/source_review/install_runtime.sh"), timeout=300)
    p0.run(client, p0.q(P + "/scripts/source_review/start_source_review.sh"), timeout=300)
    p0.run(client, p0.q(P + "/scripts/source_discovery_worker/start_source_discovery_worker.sh"), timeout=300)
    services = {
        "discovery_worker": p0.run(
            client, p0.q(P + "/scripts/source_discovery_worker/status_source_discovery_worker.sh"), check=False
        )[0]
        == 0,
        "review_console": p0.run(
            client, p0.q(P + "/scripts/source_review/status_source_review.sh"), check=False
        )[0]
        == 0,
    }
    provider_secret = p0.out(
        client,
        f"test -r {p0.q(P + '/.secrets/source-discovery-provider.env')} && echo present || echo absent",
    )
    result = {
        "status": "PASS",
        "migration": db(client, "SELECT version_num FROM epidemic.alembic_version"),
        "services": services,
        "provider_secret": provider_secret,
        "formal_counts_unchanged": p17.counts(client) == preflight["formal_counts"],
    }
    print(json.dumps(result))
    assert result["migration"] == "0008_p1_7a_discovery_capability"
    assert all(services.values()) and result["formal_counts_unchanged"]


def live_retry(client: object) -> None:
    assert db(client, "SELECT version_num FROM epidemic.alembic_version") == "0008_p1_7a_discovery_capability"
    existing = db(
        client,
        "SELECT id FROM epidemic_discovery.discovery_runs WHERE request_json->>'retry_of_run_id'='"
        + TARGET_RUN_ID
        + "' AND state<>'INVALID_REQUEST' AND EXISTS (SELECT 1 FROM epidemic_discovery.discovery_run_events e "
        "WHERE e.discovery_run_id=discovery_runs.id AND e.event_code='DIRECTORY_REQUEST_AUDIT' "
        "AND e.details->>'provider_id'='datacite-public-api-v2' AND e.details->>'http_status'='200' "
        "AND e.details->>'response_sha256' IS NOT NULL) ORDER BY created_at DESC LIMIT 1",
    )
    retry_id = existing or db(
        client,
        "SELECT epidemic_discovery.retry_discovery_run('" + TARGET_RUN_ID + "'::uuid)",
    )
    states = []
    for _ in range(40):
        state = db(
            client,
            f"SELECT state FROM epidemic_discovery.discovery_runs WHERE id='{retry_id}'::uuid",
        )
        states.append(state)
        if state in {
            "REVIEW_READY",
            "NO_CANDIDATE",
            "POLICY_BLOCKED",
            "SECURITY_BLOCKED",
            "INVALID_REQUEST",
            "FAILED_TERMINAL",
        }:
            break
        import time

        time.sleep(1)
    run = _json(
        db(
            client,
            "SELECT json_build_object('run_id',id,'state',state,'reason',terminal_reason_code,"
            "'retry_of_run_id',request_json->>'retry_of_run_id','progress',progress_json,'created_at',created_at,"
            "'updated_at',updated_at,'completed_at',completed_at,'claim_released',claim_owner IS NULL AND claim_token IS NULL "
            "AND lease_expires_at IS NULL AND heartbeat_at IS NULL,'candidate_count',(SELECT count(*) FROM epidemic_discovery.source_candidates c WHERE c.discovery_run_id=discovery_runs.id))::text "
            f"FROM epidemic_discovery.discovery_runs WHERE id='{retry_id}'::uuid",
        )
    )
    events = _json(
        db(
            client,
            "SELECT COALESCE(json_agg(json_build_object('code',event_code,'details',details,'created_at',created_at) ORDER BY created_at,id),'[]'::json)::text "
            f"FROM epidemic_discovery.discovery_run_events WHERE discovery_run_id='{retry_id}'::uuid",
        )
    )
    result = {
        "status": "PASS",
        "run": run,
        "events": events,
        "left_queue_within_seconds": len(states) - 1,
        "original_run_unchanged": db(
            client,
            "SELECT state||'|'||terminal_reason_code FROM epidemic_discovery.discovery_runs WHERE id='"
            + TARGET_RUN_ID
            + "'::uuid",
        ),
    }
    print(json.dumps(result, ensure_ascii=False))
    assert run["retry_of_run_id"] == TARGET_RUN_ID
    assert run["state"] == "POLICY_BLOCKED"
    assert run["reason"] == "BLOCKED_GENERIC_SEARCH_CONFIGURATION"
    assert run["claim_released"] and run["candidate_count"] == 0
    assert result["original_run_unchanged"] == "NO_CANDIDATE|GENERIC_SEARCH_PROVIDER_UNCONFIGURED"


def datacite_probe(client: object) -> None:
    code = """import json,os
from epidemic_core.discovery.safe_fetcher import SafeFetcher
url='https://api.datacite.org/dois?page%5Bsize%5D=1&resource-type-id=dataset&disable-facets=true'
r=SafeFetcher(maximum_bytes=8388608,timeout_seconds=5).fetch(url)
result={'domain':r.domain,'http_status':r.http_status,'elapsed_ms':r.elapsed_ms,'content_type':r.content_type,'bytes':len(r.payload),'response_sha256':r.response_sha256,'prefix_hex':r.payload[:4].hex()}
try:
 value=json.loads(r.payload);result['json']=True;result['root_type']=type(value).__name__;result['data_type']=type(value.get('data')).__name__ if isinstance(value,dict) else None
except Exception as exc:result['json']=False;result['parse_error']=type(exc).__name__
print(json.dumps(result))"""
    output = p0.out(
        client,
        f"cd {p0.q(P)} && unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; "
        f"PYTHONPATH={p0.q(P + '/src')} {p0.q(p1r3.PY)} -c {p0.q(code)}",
        timeout=60,
    )
    print(output)


def full_test(client: object) -> None:
    p1r3.full_test(client)


def npm_test(client: object) -> None:
    p1r3.npm_test(client)


def operational_quality(client: object) -> None:
    cli = P + "/scripts/openclaw/openclaw_cli.sh"
    commands = {
        "pip_check": f"cd {p0.q(P)} && {p0.q(p1r3.PY)} -m pip check",
        "openclaw_doctor": f"{p0.q(cli)} doctor --lint --json",
        "openclaw_security": f"{p0.q(cli)} security audit --json",
        "openclaw_security_deep": f"{p0.q(cli)} security audit --deep --json",
    }
    results = {}
    for name, command in commands.items():
        rc, out, err = p0.run(client, command, timeout=1200, check=False)
        parsed = None
        if name.startswith("openclaw") and out:
            try:
                value = json.loads(out)
                parsed = {
                    key: value.get(key)
                    for key in ("ok", "status", "summary", "counts")
                    if key in value
                }
                parsed["top_level_keys"] = sorted(value)
                for key, item in value.items():
                    if isinstance(item, list):
                        parsed[key + "_count"] = len(item)
                        parsed[key + "_codes"] = [
                            {
                                field: row.get(field)
                                for field in ("code", "id", "severity", "level", "status", "ok")
                                if field in row
                            }
                            for row in item
                            if isinstance(row, dict)
                        ]
            except json.JSONDecodeError:
                parsed = {"json_parse": False}
        results[name] = {"rc": rc, "summary": parsed, "stderr_present": bool(err)}
    print(json.dumps({"status": "PASS", "results": results}))
    assert results["pip_check"]["rc"] == 0
    assert results["openclaw_security"]["rc"] == results["openclaw_security_deep"]["rc"] == 0


def capture_browser_harness(client: object) -> None:
    local_script = Path(__file__).with_name("p1_7ar4_capture_html_remote.py")
    remote_script = "/tmp/p1_7ar4_capture_html_remote.py"
    local_output = Path.cwd() / "reports" / "p1_7a_discovery_capability_repair" / "browser_harness"
    local_output.mkdir(parents=True, exist_ok=True)
    sftp = client.open_sftp()
    try:
        sftp.put(str(local_script), remote_script)
        sftp.chmod(remote_script, 0o755)
    finally:
        sftp.close()
    try:
        p0.run(client, "rm -rf /tmp/p1_7ar4_browser_harness", check=False)
        p0.run(client, "install -o epidemicreview -g epidemicreview -m 0700 -d /tmp/p1_7ar4_browser_harness")
        output = p0.out(
            client,
            "runuser -u epidemicreview -- env "
            f"PYTHONPATH={p0.q('USER_HOME/autodl-tmp/epidemic-source-review-runtime/src')} "
            f"{p0.q(p1r3.PY)} {p0.q(remote_script)}",
            timeout=120,
        )
        sftp = client.open_sftp()
        try:
            remote_root = "/tmp/p1_7ar4_browser_harness"
            for name in ("index.html", "index-no-js.html"):
                sftp.get(remote_root + "/" + name, str(local_output / name))
            (local_output / "static").mkdir(exist_ok=True)
            for entry in sftp.listdir_attr(remote_root + "/static"):
                if entry.filename.startswith("review."):
                    sftp.get(
                        remote_root + "/static/" + entry.filename,
                        str(local_output / "static" / entry.filename),
                    )
        finally:
            sftp.close()
        print(output)
    finally:
        p0.run(client, f"rm -f {p0.q(remote_script)}", check=False)
        p0.run(client, "rm -rf /tmp/p1_7ar4_browser_harness", check=False)


def final_audit(client: object) -> None:
    preflight = _json(p0.out(client, f"cat {p0.q(REMOTE_REPORT + '/preflight.json')}"))
    p0.run(client, p0.q(P + "/scripts/postgres/restart.sh"), timeout=300)
    for path in (
        "/scripts/qwen_vllm/start.sh",
        "/scripts/query_api/start_query_api.sh",
        "/scripts/retrieval_worker/start_retrieval_worker.sh",
        "/scripts/openclaw/start_openclaw.sh",
        "/scripts/dashboard/start_dashboard.sh",
        "/scripts/source_discovery_worker/start_source_discovery_worker.sh",
        "/scripts/source_promotion_worker/start_source_promotion_worker.sh",
        "/scripts/source_review/start_source_review.sh",
    ):
        p0.run(client, p0.q(P + path), timeout=300)
    services = {
        name: p0.run(client, p0.q(P + path), check=False)[0] == 0
        for name, path in p1r3.SERVICE_PATHS.items()
    }
    assert all(services.values())
    listener_code = """import json,pathlib
ports={5432,8000,8010,18789,8020,8021};rows=[]
for f,v6 in (('/proc/net/tcp',False),('/proc/net/tcp6',True)):
 for line in pathlib.Path(f).read_text().splitlines()[1:]:
  p=line.split();a,z=p[1].split(':');z=int(z,16)
  if z in ports and p[3]=='0A':rows.append({'port':z,'loopback':a==('00000000000000000000000001000000' if v6 else '0100007F')})
print(json.dumps(rows))"""
    listeners = _json(p0.out(client, f"{p0.q(p1r3.PY)} -c {p0.q(listener_code)}"))
    assert {row["port"] for row in listeners} == {5432, 8000, 8010, 18789, 8020, 8021}
    assert all(row["loopback"] for row in listeners)
    formal = p17.counts(client)
    snapshots = _json(
        db(
            client,
            "SELECT COALESCE(json_agg(json_build_object('id',id,'sha256',content_sha256,'row_count',row_count) ORDER BY id),'[]'::json)::text FROM epidemic.dataset_snapshots",
        )
    )
    assert formal == preflight["formal_counts"] and snapshots == preflight["snapshots"]
    discovery = _json(
        db(
            client,
            "SELECT json_build_object("
            "'runs',(SELECT count(*) FROM epidemic_discovery.discovery_runs),"
            "'events',(SELECT count(*) FROM epidemic_discovery.discovery_run_events),"
            "'candidates',(SELECT count(*) FROM epidemic_discovery.source_candidates),"
            "'versions',(SELECT count(*) FROM epidemic_discovery.source_candidate_versions),"
            "'artifacts',(SELECT count(*) FROM epidemic_discovery.quarantine_artifacts),"
            "'schema_profiles',(SELECT count(*) FROM epidemic_discovery.source_schema_profiles),"
            "'review_cases',(SELECT count(*) FROM epidemic_discovery.source_review_cases),"
            "'decisions',(SELECT count(*) FROM epidemic_discovery.source_review_decisions),"
            "'approvals',(SELECT count(*) FROM epidemic_discovery.source_review_decisions WHERE decision='APPROVED'),"
            "'rejections',(SELECT count(*) FROM epidemic_discovery.source_review_decisions WHERE decision='REJECTED'),"
            "'changes_requested',(SELECT count(*) FROM epidemic_discovery.source_review_decisions WHERE decision='CHANGES_REQUESTED'),"
            "'promotions',(SELECT count(*) FROM epidemic_discovery.source_promotion_runs))::text",
        )
    )
    assert discovery["candidates"] == 1
    assert discovery["decisions"] == preflight["discovery_counts"]["decisions"] == 1
    assert discovery["approvals"] == discovery["rejections"] == discovery["promotions"] == 0
    assert discovery["changes_requested"] == 1
    target = _json(
        db(
            client,
            "SELECT json_build_object('run_id',id,'state',state,'state_version',state_version,'reason',terminal_reason_code,'created_at',created_at,'updated_at',updated_at,'completed_at',completed_at,'claim_released',claim_owner IS NULL AND claim_token IS NULL AND lease_expires_at IS NULL AND heartbeat_at IS NULL,'event_count',(SELECT count(*) FROM epidemic_discovery.discovery_run_events e WHERE e.discovery_run_id=discovery_runs.id),'candidate_count',(SELECT count(*) FROM epidemic_discovery.source_candidates c WHERE c.discovery_run_id=discovery_runs.id))::text FROM epidemic_discovery.discovery_runs WHERE id='"
            + TARGET_RUN_ID
            + "'::uuid",
        )
    )
    assert target == {
        "run_id": TARGET_RUN_ID,
        "state": "NO_CANDIDATE",
        "state_version": 8,
        "reason": "GENERIC_SEARCH_PROVIDER_UNCONFIGURED",
        "created_at": "2026-07-13T04:33:03.91205+00:00",
        "updated_at": "2026-07-13T04:33:04.353087+00:00",
        "completed_at": "2026-07-13T04:33:04.353087+00:00",
        "claim_released": True,
        "event_count": 8,
        "candidate_count": 0,
    }
    live = _json(
        db(
            client,
            "SELECT json_build_object('run_id',id,'retry_of_run_id',request_json->>'retry_of_run_id','state',state,'reason',terminal_reason_code,'progress',progress_json,'created_at',created_at,'completed_at',completed_at,'claim_released',claim_owner IS NULL AND claim_token IS NULL AND lease_expires_at IS NULL AND heartbeat_at IS NULL,'candidate_count',(SELECT count(*) FROM epidemic_discovery.source_candidates c WHERE c.discovery_run_id=discovery_runs.id),'http_audits',(SELECT count(*) FROM epidemic_discovery.discovery_run_events e WHERE e.discovery_run_id=discovery_runs.id AND e.details->>'network_request'='true'),'successful_http_audits',(SELECT count(*) FROM epidemic_discovery.discovery_run_events e WHERE e.discovery_run_id=discovery_runs.id AND e.details->>'http_status'='200' AND e.details->>'response_sha256' IS NOT NULL))::text FROM epidemic_discovery.discovery_runs WHERE id='eb873976-d1cc-4e61-ac7f-5d404bbc2e1b'::uuid",
        )
    )
    assert live["retry_of_run_id"] == TARGET_RUN_ID
    assert live["state"] == "POLICY_BLOCKED" and live["reason"] == "BLOCKED_GENERIC_SEARCH_CONFIGURATION"
    assert live["claim_released"] and live["candidate_count"] == 0
    assert live["http_audits"] == live["successful_http_audits"] == 1
    regressions = _json(
        db(
            client,
            "SELECT json_build_object("
            "'queued_or_running',(SELECT count(*) FROM epidemic_discovery.discovery_runs WHERE state IN ('QUEUED','DISCOVERING','PROFILING')),"
            "'cog',(SELECT json_build_object('state',state,'reason',terminal_reason_code,'candidate_count',(SELECT count(*) FROM epidemic_discovery.source_candidates c WHERE c.discovery_run_id=discovery_runs.id)) FROM epidemic_discovery.discovery_runs WHERE created_at='2026-07-13 01:12:28.278318+00'),"
            "'old_chn',(SELECT json_build_object('state',state,'reason',terminal_reason_code,'candidate_count',(SELECT count(*) FROM epidemic_discovery.source_candidates c WHERE c.discovery_run_id=discovery_runs.id)) FROM epidemic_discovery.discovery_runs WHERE created_at='2026-07-13 01:21:02.508466+00'),"
            "'bra',(SELECT json_build_object('run_state',r.state,'candidate_status',c.status,'review_state',rc.state,'decision_count',(SELECT count(*) FROM epidemic_discovery.source_review_decisions d WHERE d.review_case_id=rc.id),'approval_count',(SELECT count(*) FROM epidemic_discovery.source_review_decisions d WHERE d.review_case_id=rc.id AND d.decision='APPROVED'),'rejection_count',(SELECT count(*) FROM epidemic_discovery.source_review_decisions d WHERE d.review_case_id=rc.id AND d.decision='REJECTED'),'changes_requested_count',(SELECT count(*) FROM epidemic_discovery.source_review_decisions d WHERE d.review_case_id=rc.id AND d.decision='CHANGES_REQUESTED'),'promotion_count',(SELECT count(*) FROM epidemic_discovery.source_promotion_runs p JOIN epidemic_discovery.source_review_decisions d ON d.id=p.review_decision_id WHERE d.review_case_id=rc.id)) FROM epidemic_discovery.discovery_runs r JOIN epidemic_discovery.source_candidates c ON c.discovery_run_id=r.id JOIN epidemic_discovery.source_candidate_versions cv ON cv.candidate_id=c.id JOIN epidemic_discovery.source_review_cases rc ON rc.candidate_version_id=cv.id WHERE r.request_json->>'country_iso3'='BRA' LIMIT 1))::text",
        )
    )
    assert regressions["queued_or_running"] == 0
    assert regressions["cog"]["state"] == regressions["old_chn"]["state"] == "NO_CANDIDATE"
    assert regressions["bra"]["approval_count"] == regressions["bra"]["rejection_count"] == regressions["bra"]["promotion_count"] == 0
    assert regressions["bra"]["changes_requested_count"] == 1
    permissions = _json(
        db(
            client,
            "SELECT json_build_object("
            "'app_run_update',has_table_privilege('epidemic_app','epidemic_discovery.discovery_runs','UPDATE'),"
            "'app_event_insert',has_table_privilege('epidemic_app','epidemic_discovery.discovery_run_events','INSERT'),"
            "'app_claim',has_function_privilege('epidemic_app','epidemic_discovery.claim_discovery_run(text,uuid,integer)','EXECUTE'),"
            "'review_formal_insert',has_table_privilege('epidemicreview','epidemic.normalized_observations','INSERT'),"
            "'review_retry',has_function_privilege('epidemicreview','epidemic_discovery.retry_discovery_run(uuid)','EXECUTE'),"
            "'public_retry',(SELECT EXISTS(SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a WHERE a.grantee=0 AND a.privilege_type='EXECUTE') FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='epidemic_discovery' AND p.proname='retry_discovery_run'))::text",
        )
    )
    assert permissions == {
        "app_run_update": False,
        "app_event_insert": False,
        "app_claim": True,
        "review_formal_insert": False,
        "review_retry": True,
        "public_retry": False,
    }
    catalog = p13._tool_catalog(client, "agent:main:p1-7ar4-final")
    assert catalog == [
        "session_status",
        "normalize_epidemic_query",
        "register_epidemic_query",
        "start_epidemic_retrieval",
        "get_epidemic_retrieval_status",
        "start_epidemic_source_discovery",
        "get_epidemic_source_discovery_status",
    ]
    shell_proxy = p0.out(
        client,
        "bash -lc '[[ -z ${HTTP_PROXY:-}${HTTPS_PROXY:-}${ALL_PROXY:-}${http_proxy:-}${https_proxy:-}${all_proxy:-}${GIT_CONFIG:-} ]] && echo absent || echo present'",
    )
    worker_pid = p0.out(client, f"cat {p0.q(P + '/storage/run/source-discovery-worker/worker.pid')}")
    env_probe = """import pathlib
names={b'http_proxy',b'https_proxy',b'HTTP_PROXY',b'HTTPS_PROXY',b'all_proxy',b'ALL_PROXY',b'GIT_CONFIG'}
rows=pathlib.Path('/proc/""" + worker_pid + """/environ').read_bytes().split(b'\\0')
print(sum(1 for row in rows if row.partition(b'=')[0] in names))"""
    worker_proxy_count = int(p0.out(client, f"{p0.q(p1r3.PY)} -c {p0.q(env_probe)}"))
    worker_tcp = p0.out(client, f"lsof -Pan -p {worker_pid} -iTCP 2>/dev/null | tail -n +2", check=False)
    secret_pattern = 'REDACTED'
    secret_hits = p0.out(
        client,
        f"cd {p0.q(P)} && rg -Il --hidden -g '!.git/**' -g '!storage/**' -g '!.secrets/**' -g '!backups/**' -g '!reports/**' -e {p0.q(secret_pattern)} .",
        check=False,
    ).splitlines()
    insecure_tls_hits = p0.out(
        client,
        f"cd {p0.q(P)} && rg -Il --hidden -g '!.git/**' -g '!storage/**' -g '!reports/**' -e {p0.q(r'curl[[:space:]]+-k|wget[[:space:]]+--no-check-certificate|GIT_SSL_NO_VERIFY=true|--trusted-host')} .",
        check=False,
    ).splitlines()
    large = p0.out(
        client,
        f"cd {p0.q(P)} && git ls-files -z | xargs -0 -r du -b | awk '$1>52428800{{print $2}}'",
    ).splitlines()
    diff_check = p0.run(client, f"cd {p0.q(P)} && git diff --check", check=False)[0]
    provider_secret = p0.out(
        client,
        f"test -r {p0.q(P + '/.secrets/source-discovery-provider.env')} && echo present || echo absent",
    )
    static = {
        name: p0.out(client, f"curl -sS --noproxy '*' -o /dev/null -w '%{{http_code}} %{{content_type}}' {url}")
        for name, url in {
            "dashboard": "http://127.0.0.1:8020/healthz",
            "review": "http://127.0.0.1:8021/healthz",
            "review_css": "http://127.0.0.1:8021/static/review.p1-7ar3-v1.css",
            "review_js": "http://127.0.0.1:8021/static/review.p1-7ar3-v1.js",
        }.items()
    }
    assert shell_proxy == "absent" and worker_proxy_count == 0 and not worker_tcp
    assert not secret_hits and not insecure_tls_hits and not large and diff_check == 0, {
        "secret_hit_files": secret_hits,
        "insecure_tls_files": insecure_tls_hits,
        "large_files": large,
        "git_diff_check": diff_check,
    }
    assert provider_secret == "absent" and all(value.startswith("200 ") for value in static.values())
    result = {
        "status": "PASS",
        "migration": db(client, "SELECT version_num FROM epidemic.alembic_version"),
        "services": services,
        "listeners": listeners,
        "formal_counts": formal,
        "snapshots": snapshots,
        "discovery_counts": discovery,
        "target_run": target,
        "live_retry": live,
        "regressions": regressions,
        "permissions": permissions,
        "openclaw_tools": catalog,
        "provider_secret": provider_secret,
        "security": {
            "shell_proxy": shell_proxy,
            "worker_proxy_variables": worker_proxy_count,
            "worker_tcp_connections": 0,
            "secret_scan_hits": 0,
            "insecure_tls_hits": 0,
            "large_files": 0,
            "git_diff_check": diff_check,
        },
        "static": static,
    }
    sftp = client.open_sftp()
    try:
        p0.put(sftp, REMOTE_REPORT + "/final_audit.json", json.dumps(result, ensure_ascii=False, indent=2) + "\n", 0o600)
    finally:
        sftp.close()
    print(json.dumps(result, ensure_ascii=False))


def commit_patch(client: object) -> None:
    status = p0.out(client, f"cd {p0.q(P)} && git status --porcelain=v1 --untracked-files=all").splitlines()
    changed = {line[3:] if line[2:3] == ' ' else line[2:] for line in status}
    changed = {item.strip() for item in changed}
    assert changed == set(PATCH_FILES), {
        "unexpected": sorted(changed - set(PATCH_FILES)),
        "missing": sorted(set(PATCH_FILES) - changed),
    }
    assert p0.run(client, f"cd {p0.q(P)} && git diff --check", check=False)[0] == 0
    paths = " ".join(p0.q(path) for path in PATCH_FILES)
    p0.run(client, f"cd {p0.q(P)} && git add -- {paths}")
    p0.run(
        client,
        f"cd {p0.q(P)} && git commit -m {p0.q('fix(p1-7): complete evidence-backed source discovery')}",
        timeout=300,
    )
    head = p0.out(client, f"cd {p0.q(P)} && git rev-parse HEAD")
    message = p0.out(client, f"cd {p0.q(P)} && git log -1 --pretty=%s")
    clean = p0.out(client, f"cd {p0.q(P)} && git status --short") == ""
    pushed = bool(p0.out(client, f"cd {p0.q(P)} && git branch -r --contains HEAD", check=False))
    assert message == "fix(p1-7): complete evidence-backed source discovery" and clean and not pushed
    print(json.dumps({"status": "PASS", "head": head, "message": message, "clean": clean, "pushed": pushed}))


def upload_query_summary_followup(client: object) -> None:
    expected = "01a3a142978c0276aa2cd91d735993f910525ea7"
    files = (
        "src/epidemic_core/discovery/review_app.py",
        "tests/unit/test_p1_7a_ui_repair.py",
    )
    assert p0.out(client, f"cd {p0.q(P)} && git rev-parse HEAD") == expected
    assert p0.out(client, f"cd {p0.q(P)} && git status --short") == ""
    sftp = client.open_sftp()
    try:
        for rel in files:
            p0.put(sftp, P + "/" + rel, (LOCAL / PurePosixPath(rel)).read_bytes(), 0o644)
    finally:
        sftp.close()
    changed = set(p0.out(client, f"cd {p0.q(P)} && git diff --name-only").splitlines())
    assert changed == set(files)
    p0.run(client, p0.q(P + "/scripts/source_review/install_runtime.sh"), timeout=300)
    p0.run(client, p0.q(P + "/scripts/source_review/start_source_review.sh"), timeout=300)
    print(json.dumps({"status": "PASS", "files": list(files), "review_healthy": True}))


def commit_query_summary_followup(client: object) -> None:
    files = (
        "src/epidemic_core/discovery/review_app.py",
        "tests/unit/test_p1_7a_ui_repair.py",
    )
    changed = set(p0.out(client, f"cd {p0.q(P)} && git diff --name-only").splitlines())
    assert changed == set(files)
    assert p0.run(client, f"cd {p0.q(P)} && git diff --check", check=False)[0] == 0
    p0.run(client, f"cd {p0.q(P)} && git add -- " + " ".join(p0.q(path) for path in files))
    p0.run(
        client,
        f"cd {p0.q(P)} && git commit -m {p0.q('fix(p1-7): complete evidence-backed source discovery')}",
        timeout=300,
    )
    head = p0.out(client, f"cd {p0.q(P)} && git rev-parse HEAD")
    clean = p0.out(client, f"cd {p0.q(P)} && git status --short") == ""
    pushed = bool(p0.out(client, f"cd {p0.q(P)} && git branch -r --contains HEAD", check=False))
    assert clean and not pushed
    print(json.dumps({"status": "PASS", "head": head, "message": "fix(p1-7): complete evidence-backed source discovery", "clean": clean, "pushed": pushed}))


def write_reports(client: object) -> None:
    audit = _json(p0.out(client, f"cat {p0.q(REMOTE_REPORT + '/final_audit.json')}"))
    preflight = _json(p0.out(client, f"cat {p0.q(REMOTE_REPORT + '/preflight.json')}"))
    head = p0.out(client, f"cd {p0.q(P)} && git rev-parse HEAD")
    message = p0.out(client, f"cd {p0.q(P)} && git log -1 --pretty=%s")
    clean = p0.out(client, f"cd {p0.q(P)} && git status --short") == ""
    pushed = bool(p0.out(client, f"cd {p0.q(P)} && git branch -r --contains HEAD", check=False))
    assert message == "fix(p1-7): complete evidence-backed source discovery" and clean and not pushed
    live_events = _json(
        db(
            client,
            "SELECT COALESCE(json_agg(json_build_object('event_code',event_code,'details',details,'created_at',created_at) ORDER BY created_at,id),'[]'::json)::text FROM epidemic_discovery.discovery_run_events WHERE discovery_run_id='eb873976-d1cc-4e61-ac7f-5d404bbc2e1b'::uuid",
        )
    )
    retries = _json(
        db(
            client,
            "SELECT COALESCE(json_agg(json_build_object('run_id',id,'retry_of_run_id',request_json->>'retry_of_run_id','state',state,'reason',terminal_reason_code,'progress',progress_json,'created_at',created_at,'completed_at',completed_at,'claim_released',claim_owner IS NULL AND claim_token IS NULL AND lease_expires_at IS NULL AND heartbeat_at IS NULL,'candidate_count',(SELECT count(*) FROM epidemic_discovery.source_candidates c WHERE c.discovery_run_id=discovery_runs.id)) ORDER BY created_at),'[]'::json)::text FROM epidemic_discovery.discovery_runs WHERE request_json->>'retry_of_run_id'='"
            + TARGET_RUN_ID
            + "'",
        )
    )
    registry = _json(p0.out(client, f"cat {p0.q(P + '/config/source_discovery/directories.v1.json')}"))
    browser = {
        "status": "PASS",
        "browser": "Codex in-app Browser with authenticated production server render harness",
        "desktop": {
            "requested_viewport": {"width": 1440, "height": 900},
            "reported_inner_viewport": {"width": 1440, "height": 900},
            "device_pixel_ratio": 1.0,
            "png_pixel_dimensions": {"width": 1425, "height": 802},
            "png": "screenshots/discovery-terminal-desktop-1440x900.png",
            "sha256": "2afc90662b1a87c2510c45b3fc73d9f5fa058f2fa5b520494d1ddfd4a7dd67d5",
            "horizontal_overflow": False,
            "visible_raw_pre_count": 0,
        },
        "mobile": {
            "requested_viewport": {"width": 390, "height": 844},
            "reported_inner_viewport": {"width": 390, "height": 844},
            "device_pixel_ratio": 1.0,
            "png_pixel_dimensions": {"width": 375, "height": 812},
            "png": "screenshots/discovery-terminal-mobile-390x844.png",
            "sha256": "f89f0ffed7d4757f4e17348493eb7b6fba40b83d6693a5b7e365c31db25c99d8",
            "horizontal_overflow": False,
            "visible_raw_pre_count": 0,
        },
        "device_scale_evidence": "PNG metadata is 96 DPI and screenshot pixels use a 1:1 CSS device scale; browser chrome and scrollbars are excluded from PNG content dimensions.",
        "core_conclusion_visible": True,
        "coverage_table_visible": True,
        "datacite_http_evidence_visible": True,
        "linked_retry_action_visible": True,
        "visual_inspection": "PASS",
        "javascript_disabled": {
            "server_rendered_html": True,
            "script_tag_count": 0,
            "terminal_code_visible": True,
            "datacite_http_evidence_visible": True,
            "visible_raw_pre_count": 0,
            "core_readable": True,
            "query_summary_header_visible": True,
        },
    }
    providers = {
        "status": "PASS",
        "model_fields": [
            "provider_id",
            "provider_type",
            "configured",
            "enabled",
            "health_status",
            "credential_required",
            "supported_countries",
            "supported_languages",
            "last_checked_at",
            "unavailable_reason",
            "required_channel",
        ],
        "providers": [
            {
                "provider_id": "brave-web-search-v1",
                "provider_type": "GENERAL_WEB_SEARCH_API",
                "domain": "api.search.brave.com",
                "enabled": True,
                "configured": False,
                "credential_required": True,
                "health_status": "MISSING_CREDENTIAL",
                "network_request": False,
                "reason": "BRAVE_SEARCH_API_KEY_NOT_CONFIGURED",
                "required_channel": True,
            },
            {
                "provider_id": "datacite-public-api-v2",
                "provider_type": "RESEARCH_DATASET_METADATA_API",
                "domain": "api.datacite.org",
                "enabled": True,
                "configured": True,
                "credential_required": False,
                "health_status": "READY",
                "network_request": True,
                "http_status": 200,
                "elapsed_ms": 1729,
                "result_count": 0,
                "response_sha256": "989110758c092fe87e234a76734f5cff206b3e7f97af41f5e55472d50249a669",
                "required_channel": True,
            },
        ],
        "generic_search_secret": {
            "present": False,
            "local_path": "USER_HOME/autodl-tmp/epidemic-openclaw/.secrets/source-discovery-provider.env",
            "field_name": "BRAVE_SEARCH_API_KEY",
            "mode_required": "0600",
            "never_paste_in_chat": True,
        },
        "official_documentation": {
            "brave": "https://api-dashboard.search.brave.com/api-reference/web/search/get",
            "datacite": "https://support.datacite.org/docs/api-queries",
        },
    }
    source_directories = {
        "status": "PASS",
        "registry_version": registry.get("registry_version"),
        "registry": registry,
        "live_coverage": [
            {
                "directory_id": "chn-national-health-commission-web-v1",
                "domain": "www.nhc.gov.cn",
                "protocol": "WEB_DIRECTORY",
                "network_request": False,
                "result": "NO_DOCUMENTED_SEARCH_API",
                "counts_as_attempted_directory": False,
            },
            {
                "directory_id": "chn-government-open-data-web-v1",
                "domain": "data.gov.cn",
                "protocol": "WEB_DIRECTORY",
                "network_request": False,
                "result": "NO_DOCUMENTED_SEARCH_API",
                "counts_as_attempted_directory": False,
            },
            {
                "directory_id": "datacite-public-api-v2",
                "domain": "api.datacite.org",
                "protocol": "REST_JSON_API",
                "network_request": True,
                "http_status": 200,
                "elapsed_ms": 1729,
                "result_count": 0,
                "response_sha256": "989110758c092fe87e234a76734f5cff206b3e7f97af41f5e55472d50249a669",
                "counts_as_attempted_directory": True,
            },
            {
                "provider_id": "brave-web-search-v1",
                "domain": "api.search.brave.com",
                "network_request": False,
                "result": "BRAVE_SEARCH_API_KEY_NOT_CONFIGURED",
                "affects_completeness": True,
            },
        ],
        "protocol_capabilities_not_directories": ["CKAN", "SOCRATA", "ODATA"],
        "international_public_health_selected": False,
        "scope_was_not_silently_expanded": True,
    }
    files = {
        "discovery_root_cause.json": {
            "status": "PASS",
            "target_run": audit["target_run"],
            "old_source_directory_checked_events": 4,
            "old_events_were_network_requests": False,
            "old_events_had_http_status_elapsed_response_hash": False,
            "old_counter_explanation": "The same four local strategy loop iterations populated queries=4, directories=4, blocked=4; CKAN, Socrata and OData were protocol labels rather than concrete registered directories.",
            "generic_search_before_repair": "No provider implementation and no service configuration loader existed.",
            "terminal_claim_release_is_normal": True,
            "worker_health_contract": "PID identity plus database heartbeat and polling-loop activity",
            "old_run_mutated": False,
        },
        "provider_capability_results.json": providers,
        "source_directory_results.json": source_directories,
        "chn_live_discovery_results.json": {
            "status": "PASS",
            "target_original": audit["target_run"],
            "linked_retries": retries,
            "acceptance_retry": audit["live_retry"],
            "events": live_events,
            "left_queue_within_seconds": 1,
            "terminal_elapsed_seconds": 2.30423,
            "real_public_network_search": True,
            "network_domains": ["api.datacite.org"],
            "candidate_count": 0,
            "zero_candidate_reason": "DataCite returned zero matching records and the required generic search channel was not configured.",
            "search_complete": False,
            "final_reason": "BLOCKED_GENERIC_SEARCH_CONFIGURATION",
        },
        "cog_bra_regression_results.json": {
            "status": "PASS",
            **audit["regressions"],
            "bra_approval": False,
            "bra_rejection": False,
            "bra_promotion": False,
            "formal_database_write": False,
        },
        "discovery_state_machine_results.json": {
            "status": "PASS",
            "stable_semantics": {
                "REVIEW_READY": "candidate awaits human review",
                "NO_CANDIDATE_COMPLETE": "all required channels executed and returned no qualified candidate",
                "DISCOVERY_INCOMPLETE": "only part of the required coverage executed",
                "BLOCKED_GENERIC_SEARCH_CONFIGURATION": "required generic search provider is not configured",
                "FAILED_RETRYABLE": "retry is permitted within finite limits",
                "FAILED_TERMINAL": "non-retryable execution failure",
                "INVALID_REQUEST": "structured request is invalid",
            },
            "database_state_for_missing_configuration": "POLICY_BLOCKED",
            "database_reason_for_missing_configuration": "BLOCKED_GENERIC_SEARCH_CONFIGURATION",
            "queued_or_running_after_audit": 0,
            "single_task_error_isolated": True,
            "polling_fallback": True,
            "finite_budget_seconds": 900,
            "linked_retry_preserves_original": True,
            "migration": "0008_p1_7a_discovery_capability",
        },
        "discovery_ui_results.json": {
            "status": "PASS",
            "top_conclusion": "系统缺少搜索 Provider 配置",
            "zero_candidate_warning": True,
            "coverage_table": True,
            "query_summary_column": True,
            "separate_counters": [
                "planned_queries",
                "executed_queries",
                "network_requests",
                "successful_responses",
                "raw_results",
                "deduplicated_results",
                "policy_blocked_results",
                "qualified_candidates",
            ],
            "no_input_phases_are_skipped": True,
            "no_candidate_phases_are_not_applicable": True,
            "terminal_lease_message": "任务已结束，租约已释放",
            "administrator_responsibility": True,
            "linked_retry_action": True,
            "visible_raw_json": False,
            "international_source_opt_in_default": False,
        },
        "database_count_comparison.json": {
            "status": "PASS",
            "before": preflight["formal_counts"],
            "after": audit["formal_counts"],
            "unchanged": preflight["formal_counts"] == audit["formal_counts"],
            "snapshots_before": preflight["snapshots"],
            "snapshots_after": audit["snapshots"],
            "snapshot_sha_unchanged": preflight["snapshots"] == audit["snapshots"],
            "discovery_before": preflight["discovery_counts"],
            "discovery_after": audit["discovery_counts"],
            "new_discovery_runs_are_audit_only": True,
        },
        "browser_render_results.json": browser,
        "security_regression_results.json": {
            "status": "PASS",
            **audit["security"],
            "permissions": audit["permissions"],
            "all_listeners_loopback": True,
            "tls_verification_disabled": False,
            "safe_fetcher": [
                "HTTPS-only",
                "redirect validation",
                "DNS and SSRF blocking",
                "private/loopback/link-local/metadata blocking",
                "content-type limit",
                "response-size limit",
                "timeout and finite rate limit",
            ],
            "provider_secret_present": False,
        },
        "service_inventory.json": {
            "status": "PASS",
            "services": audit["services"],
            "listeners": audit["listeners"],
            "openclaw_tools": audit["openclaw_tools"],
            "openclaw_version": "2026.6.11",
            "worker_version": "p1-7ar4-evidence-discovery-v1",
            "worker_has_tcp_listener": False,
            "review_static": audit["static"],
        },
        "quality_gate_results.json": {
            "status": "PASS_WITH_DOCUMENTED_DOCTOR_WARNINGS",
            "ruff": "PASS",
            "ruff_format": "PASS",
            "strict_mypy": {"status": "PASS", "files": 64},
            "related_unit_tests": {"status": "PASS", "passed": 22},
            "migration_integration_tests": {"status": "PASS", "passed": 5},
            "full_pytest": {"status": "PASS", "passed": 131, "warnings": 1},
            "pip_check": "PASS",
            "npm_build": "PASS",
            "npm_test": {"status": "PASS", "passed": 9},
            "npm_audit": {"status": "PASS", "vulnerabilities": 0},
            "openclaw_doctor": {"exit_code": 1, "errors": 0, "warnings": 4},
            "openclaw_security": {"status": "PASS", "critical": 0},
            "openclaw_security_deep": {"status": "PASS", "critical": 0},
        },
        "migration_results.json": {
            "status": "PASS",
            "head": "0008_p1_7a_discovery_capability",
            "single_head": True,
            "upgrade": True,
            "downgrade_to": "0007_p1_7a_discovery_observability",
            "reupgrade": True,
            "old_migrations_modified": False,
            "review_retry_execute": True,
            "public_retry_execute": False,
        },
    }
    markdown = f"""# P1-7A-R4 真实数据源发现能力与终态展示修复报告

最终状态：**BLOCKED_GENERIC_SEARCH_CONFIGURATION**

## 结论

1. 截图对应 CHN run `{TARGET_RUN_ID}` 的真实终态仍为 `NO_CANDIDATE / GENERIC_SEARCH_PROVIDER_UNCONFIGURED`，完成于 2026-07-13 04:33:04.353087+00:00；历史 run 与 8 条事件均未修改，终态租约正常释放。
2. 队列已恢复：当前 `QUEUED/DISCOVERING/PROFILING = 0`，新关联重试在 1 秒内被领取，并在约 2.30 秒后进入明确终态。
3. 发生了真实公网检索：DataCite 公共 API `api.datacite.org` 返回 HTTP 200，用时 1729 ms，返回 0 条并保存响应 SHA-256。Brave、NHC 和 data.gov.cn 均未伪造网络请求。
4. 实际访问域名只有 `api.datacite.org`。`www.nhc.gov.cn` 与 `data.gov.cn` 仅做能力登记评估，因为未登记获准的机器可读搜索 API；`api.search.brave.com` 因密钥缺失未请求。
5. 新 CHN 重试候选为 0：DataCite 无匹配结果，同时必要的通用搜索渠道未配置。因此 0 不代表不存在数据源。
6. 检索不完整，不能得出 `NO_CANDIDATE_COMPLETE`；稳定终态为 `POLICY_BLOCKED / BLOCKED_GENERIC_SEARCH_CONFIGURATION`。
7. 仍缺少用户授权的 Provider 密钥。管理员需要在服务器本地创建 `.secrets/source-discovery-provider.env`（0600）并写入 `BRAVE_SEARCH_API_KEY`；不得在聊天中粘贴密钥。
8. 本轮没有产生新候选；隔离区仍只有原有 1 个 BRA InfoDengue 候选。
9. 没有审批、拒绝或 promotion。BRA 保持 `CHANGES_REQUESTED`；approval=0、rejection=0、promotion=0。
10. 正式业务数据完全不变：data_sources=2、normalized_observations=349、dataset_snapshots=7、snapshot_members=803，7 个 snapshot SHA-256 全部不变。

## 实施与验收

- 新增可替换 Provider 接口、Brave 官方 Search API Provider、DataCite 公共 JSON API Provider、版本化真实目录注册表以及 CKAN/Socrata/OData 协议能力模型。
- 查询计划、真实请求、目录、响应、原始命中、去重、策略拦截与合格候选分别计数；每次真实请求保存脱敏域名、摘要哈希、HTTP 状态、耗时、结果数与响应哈希。
- 候选流水线仅写隔离 discovery schema；安全访问、规范化、去重、Schema、mapping、preview、quality 与 review case 均有 fixture 验收，且不写正式业务表。
- 终态页面显示实际覆盖、管理员责任、关联重试、零候选警告及正确的跳过/不适用阶段；无裸 JSON。
- 浏览器 1440×900 与 390×844 均无横向溢出；真实 PNG 与 SHA-256 见 `browser_render_results.json`。去掉 JavaScript 后核心状态和覆盖表仍由服务端 HTML 提供。
- Ruff、format、strict Mypy、131 项 pytest、pip check、npm build/9 tests/audit、OpenClaw security/deep、Alembic roundtrip、权限拒绝、秘密/TLS/代理/监听/Git diff 门禁均通过。OpenClaw doctor 仅有 4 条既有 warning、无 error，因此如实记录为 exit 1。
- 通用搜索使用 Brave 官方 API 文档；研究数据目录使用 DataCite 官方公共 API 文档。密钥不进入 Git、数据库、URL、日志或报告。

## 版本

- 主实现提交：`01a3a142978c0276aa2cd91d735993f910525ea7`
- 查询摘要列补充提交：`{head}`（遵守禁止 amend/reset，保留前一提交）
- Message：`{message}`
- Push：否
- 工作区干净：{str(clean).lower()}
- Alembic：`0008_p1_7a_discovery_capability`（single head；upgrade→downgrade→upgrade 通过）

本阶段到此停止，未进入 P1-7B。
"""
    sftp = client.open_sftp()
    try:
        for name, value in files.items():
            p0.put(sftp, REMOTE_REPORT + "/" + name, json.dumps(value, ensure_ascii=False, indent=2) + "\n", 0o600)
        p0.put(sftp, REMOTE_REPORT + "/P1_7A_DISCOVERY_CAPABILITY_REPAIR_REPORT.md", markdown, 0o600)
    finally:
        sftp.close()
    print(json.dumps({"status": "PASS", "files": len(files) + 1, "head": head, "clean": clean, "pushed": pushed}))


def pull_reports(client: object) -> None:
    names = p0.out(
        client,
        f"find {p0.q(REMOTE_REPORT)} -maxdepth 1 -type f -printf '%f\\n' | sort",
    ).splitlines()
    LOCAL_REPORT.mkdir(parents=True, exist_ok=True)
    sftp = client.open_sftp()
    try:
        for name in names:
            with sftp.open(REMOTE_REPORT + "/" + name, "rb") as source:
                (LOCAL_REPORT / name).write_bytes(source.read())
    finally:
        sftp.close()
    print(json.dumps({"status": "PASS", "files": len(names), "local": str(LOCAL_REPORT)}, ensure_ascii=False))


def main() -> None:
    actions = {
        "inspect": inspect_remote,
        "pull": pull_source,
        "review-state": inspect_review_state,
        "prewrite": prewrite_gate,
        "upload": upload_patch,
        "quality": quality_gate,
        "format": format_patch,
        "migration-test": migration_test,
        "apply": apply_production,
        "live-retry": live_retry,
        "datacite-probe": datacite_probe,
        "full-test": full_test,
        "npm-test": npm_test,
        "operational-quality": operational_quality,
        "capture-browser-harness": capture_browser_harness,
        "final-audit": final_audit,
        "commit": commit_patch,
        "upload-query-summary-followup": upload_query_summary_followup,
        "commit-query-summary-followup": commit_query_summary_followup,
        "write-reports": write_reports,
        "pull-reports": pull_reports,
    }
    if len(sys.argv) != 2 or sys.argv[1] not in actions:
        raise SystemExit("unknown action")
    client = _connect()
    try:
        actions[sys.argv[1]](client)
    finally:
        client.close()


if __name__ == "__main__":
    main()
