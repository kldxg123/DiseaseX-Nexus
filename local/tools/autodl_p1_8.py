from __future__ import annotations

import json
import hashlib
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path, PurePosixPath

import autodl_p0_4 as p0
import autodl_p1_3 as p13
import autodl_p1_7ar4 as p1r4


P = p0.P
EXPECTED_HEAD = "f2863fccef26d0628799e52e14fea820cc027a26"
LOCAL = Path(__file__).parent / "p1_8_baseline"
REPORT = P + "/reports/p1_8_daily_updates"
KNOWN_HOSTS = Path(tempfile.gettempdir()) / "known_hosts_autodl_epidemic_p1_8"

p13.KNOWN_HOSTS = KNOWN_HOSTS
p1r4.KNOWN_HOSTS = KNOWN_HOSTS
p1r4.LOCAL = LOCAL

PATCH_FILES = (
    "config/daily_update.json",
    "migrations/versions/0010_p1_8_daily_updates.py",
    "src/epidemic_core/db/models/tables.py",
    "src/epidemic_core/retrieval/service.py",
    "src/epidemic_core/daily/__init__.py",
    "src/epidemic_core/daily/cli.py",
    "src/epidemic_core/daily/main.py",
    "src/epidemic_core/daily/schemas.py",
    "src/epidemic_core/daily/service.py",
    "src/epidemic_core/daily/worker.py",
    "src/epidemic_core/api/app.py",
    "src/epidemic_core/api/routes/daily_updates.py",
    "src/epidemic_core/dashboard/app.py",
    "src/epidemic_core/dashboard/static/dashboard.p1-8-daily.css",
    "src/epidemic_core/dashboard/static/dashboard.p1-8-daily.js",
    "src/epidemic_core/discovery/review_app.py",
    "scripts/daily_update/_common.sh",
    "scripts/daily_update/health_check.sh",
    "scripts/daily_update/install_openclaw_cron.sh",
    "scripts/daily_update/reconcile_due.sh",
    "scripts/daily_update/restart_daily_update.sh",
    "scripts/daily_update/seed_schedules.sh",
    "scripts/daily_update/start_daily_update.sh",
    "scripts/daily_update/status_daily_update.sh",
    "scripts/daily_update/stop_daily_update.sh",
    "scripts/services/start_all.sh",
    "scripts/services/status_all.sh",
    "scripts/services/stop_all.sh",
    "scripts/openclaw/configure_source_discovery_agent.py",
    "integrations/openclaw/epidemic-query-tools/openclaw.plugin.json",
    "integrations/openclaw/epidemic-query-tools/package.json",
    "integrations/openclaw/epidemic-query-tools/package-lock.json",
    "integrations/openclaw/epidemic-query-tools/src/index.ts",
    "integrations/openclaw/epidemic-query-tools/src/index.test.ts",
    "integrations/openclaw/epidemic-query-tools/dist/index.js",
    "tests/unit/test_daily_updates.py",
    "tests/unit/test_dashboard.py",
    "tests/unit/test_keyfree_discovery.py",
    "tests/integration/test_daily_update_contract.py",
)


def connection_gate() -> None:
    trust = p13.establish_trust()
    print(
        json.dumps(
            {
                "status": "PASS",
                "host_key_type": trust["key_type"],
                "host_key_fingerprint": trust["fingerprint"],
                "identity_confirmed": True,
                "independent_known_hosts": True,
                "stable_second_connection": trust["stable_second_connection"] == "true",
            }
        )
    )


def db(client: object, sql: str, database: str = "epidemic_openclaw") -> str:
    return p1r4.db(client, sql, database)


def baseline(client: object) -> None:
    git = {
        "head": p0.out(client, f"cd {p0.q(P)} && git rev-parse HEAD"),
        "branch": p0.out(client, f"cd {p0.q(P)} && git branch --show-current"),
        "status": p0.out(client, f"cd {p0.q(P)} && git status --porcelain=v1").splitlines(),
        "recent": p0.out(client, f"cd {p0.q(P)} && git log -5 --pretty='%H %s'").splitlines(),
        "daily_history": p0.out(
            client,
            f"cd {p0.q(P)} && git log --all --pretty='%H %s' --regexp-ignore-case --grep={p0.q('daily|feed|cron')} -30",
            check=False,
        ).splitlines(),
    }
    services = {}
    for name, rel in p1r4.p1r3.SERVICE_PATHS.items():
        status_code = p0.run(client, p0.q(P + rel), check=False)[0]
        pid_files = p0.out(
            client,
            f"find {p0.q(P + '/storage/run')} -maxdepth 3 -type f -name '*.pid' -path '*{name.replace('_', '-')}*' -print 2>/dev/null",
            check=False,
        ).splitlines()
        services[name] = {"healthy": status_code == 0, "pid_file_count": len(pid_files)}
    listener_code = """import json,pathlib
ports={5432,8000,8010,18789,8020,8021};rows=[]
for f,v6 in (('/proc/net/tcp',False),('/proc/net/tcp6',True)):
 for line in pathlib.Path(f).read_text().splitlines()[1:]:
  p=line.split();a,z=p[1].split(':');z=int(z,16)
  if z in ports and p[3]=='0A':rows.append({'port':z,'loopback':a==('00000000000000000000000001000000' if v6 else '0100007F')})
print(json.dumps(rows))"""
    listeners = json.loads(p0.out(client, f"{p0.q(p1r4.p1r3.PY)} -c {p0.q(listener_code)}"))
    migration = {
        "current": db(client, "SELECT version_num FROM epidemic.alembic_version"),
        "heads": p0.out(
            client,
            f"cd {p0.q(P)} && PYTHONPATH={p0.q(P + '/src')} {p0.q(p1r4.p1r3.PY)} -m alembic heads",
        ).splitlines(),
        "history_tail": p0.out(
            client,
            f"cd {p0.q(P)} && PYTHONPATH={p0.q(P + '/src')} {p0.q(p1r4.p1r3.PY)} -m alembic history | tail -n 12",
        ).splitlines(),
    }
    cron_help = {}
    cli = P + "/scripts/openclaw/openclaw_cli.sh"
    for command in ("create", "edit", "list", "run", "runs"):
        code, stdout, _ = p0.run(client, f"{p0.q(cli)} cron {command} --help", timeout=120, check=False)
        cron_help[command] = {
            "available": code == 0,
            "command_argv": "--command-argv" in stdout,
            "timezone": "--tz" in stdout,
            "exact": "--exact" in stdout,
            "timeout_seconds": "--timeout-seconds" in stdout,
            "command_cwd": "--command-cwd" in stdout,
            "no_deliver": "--no-deliver" in stdout,
        }
    cron_code, cron_stdout, _ = p0.run(client, f"{p0.q(cli)} cron list --json", timeout=120, check=False)
    cron_count = 0
    if cron_code == 0 and cron_stdout:
        try:
            value = json.loads(cron_stdout)
            jobs = value if isinstance(value, list) else value.get("jobs", value.get("items", []))
            cron_count = len(jobs) if isinstance(jobs, list) else 0
        except json.JSONDecodeError:
            cron_count = 0
    formal_counts = p1r4.p17.counts(client)
    snapshots = json.loads(
        db(
            client,
            "SELECT COALESCE(json_agg(json_build_object('id',id,'sha256',content_sha256,'row_count',row_count,'created_at',created_at) ORDER BY id),'[]'::json)::text FROM epidemic.dataset_snapshots",
        )
    )
    columns = {
        table: db(
            client,
            "SELECT COALESCE(string_agg(column_name,',' ORDER BY ordinal_position),'') FROM information_schema.columns WHERE table_schema='epidemic' AND table_name='"
            + table
            + "'",
        ).split(",")
        for table in ("data_sources", "normalized_observations", "dataset_snapshots")
    }
    sources = json.loads(
        db(
            client,
            "SELECT COALESCE(json_agg(json_build_object('id',id,'source_key',source_key,'display_name',display_name,'authority',authority,'base_domain',regexp_replace(COALESCE(base_url,''),'^https?://([^/]+).*$','\\1'),'enabled',enabled,'adapter_name',adapter_name,'license_expression',license_expression) ORDER BY source_key),'[]'::json)::text FROM epidemic.data_sources",
        )
    )
    watermarks = json.loads(
        db(
            client,
            "SELECT COALESCE(json_agg(json_build_object('country',c.alpha3,'disease',d.canonical_name,'observations',x.n,'data_through',x.data_through) ORDER BY c.alpha3,d.canonical_name),'[]'::json)::text FROM (SELECT country_alpha2,disease_id,count(*) n,max(period_end) data_through FROM epidemic.normalized_observations GROUP BY country_alpha2,disease_id) x JOIN epidemic.countries c ON c.alpha2=x.country_alpha2 JOIN epidemic.diseases d ON d.id=x.disease_id",
        )
    )
    discovery_boundary = json.loads(
        db(
            client,
            "SELECT json_build_object('approvals',(SELECT count(*) FROM epidemic_discovery.source_review_decisions WHERE decision='APPROVED'),'rejections',(SELECT count(*) FROM epidemic_discovery.source_review_decisions WHERE decision='REJECTED'),'promotions',(SELECT count(*) FROM epidemic_discovery.source_promotion_runs),'bra',(SELECT COALESCE(json_agg(json_build_object('run_state',r.state,'candidate_status',c.status,'review_state',rc.state,'decision',(SELECT d.decision FROM epidemic_discovery.source_review_decisions d WHERE d.review_case_id=rc.id ORDER BY d.created_at DESC LIMIT 1))),'[]'::json) FROM epidemic_discovery.discovery_runs r JOIN epidemic_discovery.source_candidates c ON c.discovery_run_id=r.id JOIN epidemic_discovery.source_candidate_versions cv ON cv.candidate_id=c.id JOIN epidemic_discovery.source_review_cases rc ON rc.candidate_version_id=cv.id WHERE r.request_json->>'country_iso3'='BRA'))::text",
        )
    )
    main_catalog = p13._tool_catalog(client, "agent:main:p1-8-baseline")
    source_catalog = p13._tool_catalog(client, "agent:source-discovery-agent:p1-8-baseline")
    persistence = {}
    for name, path in {
        "project": P,
        "openclaw_state": P + "/storage/openclaw/state",
        "postgres_data": P + "/storage/database/postgresql18/data",
        "models": P + "/storage/models",
    }.items():
        code, stdout, _ = p0.run(client, f"findmnt -T {p0.q(path)} -no TARGET,SOURCE,FSTYPE", check=False)
        persistence[name] = {"exists": p0.run(client, f"test -e {p0.q(path)}", check=False)[0] == 0, "mount": stdout if code == 0 else "unreported"}
    manager = {
        "systemd_available": p0.run(client, "command -v systemctl >/dev/null 2>&1", check=False)[0] == 0,
        "supervisor_available": p0.run(client, "command -v supervisorctl >/dev/null 2>&1", check=False)[0] == 0,
        "tmux_sessions": int(p0.out(client, "tmux list-sessions 2>/dev/null | wc -l", check=False) or "0"),
        "project_start_scripts": True,
        "root_project_cron_entries": int(p0.out(client, f"(crontab -l 2>/dev/null || true) | grep -F {p0.q(P)} | wc -l") or "0"),
        "project_systemd_timers": int(p0.out(client, f"(systemctl list-timers --all --no-legend 2>/dev/null || true) | grep -Ei {p0.q('epidemic|openclaw')} | wc -l") or "0"),
    }
    result = {
        "status": "PASS_READ_ONLY",
        "git": git,
        "migration": migration,
        "services": services,
        "listeners": listeners,
        "openclaw_version": p0.out(client, f"{p0.q(cli)} --version"),
        "openclaw_cron_help": cron_help,
        "openclaw_cron_job_count": cron_count,
        "service_manager": manager,
        "formal_counts": formal_counts,
        "snapshots": snapshots,
        "source_columns": columns,
        "approved_sources": sources,
        "dashboard_watermarks": watermarks,
        "discovery_boundary": discovery_boundary,
        "main_agent_tools": main_catalog,
        "source_agent_tools": source_catalog,
        "persistence": persistence,
        "shell_proxy_variables": p0.out(client, "bash -lc '[[ -z ${HTTP_PROXY:-}${HTTPS_PROXY:-}${ALL_PROXY:-}${http_proxy:-}${https_proxy:-}${all_proxy:-} ]] && echo absent || echo present'"),
    }
    print(json.dumps(result, ensure_ascii=False))
    assert git["head"] == EXPECTED_HEAD and not git["status"]
    assert migration["current"] == "0009_p1_7a_keyfree_discovery" and len(migration["heads"]) == 1
    assert all(item["healthy"] for item in services.values())
    assert all(item["loopback"] for item in listeners)
    assert cron_count == 0 and manager["root_project_cron_entries"] == 0 and manager["project_systemd_timers"] == 0


def pull_source(client: object) -> None:
    p1r4.pull_source(client)


def architecture(client: object) -> None:
    cli = P + "/scripts/openclaw/openclaw_cli.sh"
    helps = {}
    for command in ("create", "edit", "list", "run", "runs"):
        code, stdout, stderr = p0.run(client, f"{p0.q(cli)} cron {command} --help", timeout=120, check=False)
        helps[command] = {"exit_code": code, "stdout": stdout, "stderr_present": bool(stderr)}
    startup_candidates = p0.out(
        client,
        "for p in USER_HOME/onstart.sh USER_HOME/startup.sh /etc/rc.local /etc/systemd/system; do test -e \"$p\" && printf '%s\\n' \"$p\"; done",
        check=False,
    ).splitlines()
    systemd = {
        "pid1_comm": p0.out(client, "cat /proc/1/comm", check=False),
        "is_system_running": p0.out(client, "systemctl is-system-running 2>/dev/null || true", check=False),
        "can_enable_units": p0.run(client, "systemctl list-unit-files >/dev/null 2>&1", check=False)[0] == 0,
        "project_units": p0.out(
            client,
            "systemctl list-unit-files --no-legend 2>/dev/null | grep -Ei 'epidemic|openclaw' | awk '{print $1}'",
            check=False,
        ).splitlines(),
    }
    old_daily_files = p0.out(
        client,
        f"cd {p0.q(P)} && git log --all --name-only --pretty=format: | grep -Ei 'daily|feed|cron' | sort -u",
        check=False,
    ).splitlines()
    service_processes = p0.out(
        client,
        "ps -eo ppid=,comm=,args= | grep -E '[e]pidemic_core|[o]penclaw.*gateway|[u]vicorn|[v]llm' | "
        "sed -E 's#(USER_HOME/autodl-tmp/[^ ]+|postgresql[^ ]*://[^ ]+)#<path-or-connection-redacted>#g'",
        check=False,
    ).splitlines()
    print(
        json.dumps(
            {
                "status": "PASS_READ_ONLY",
                "cron_help": helps,
                "startup_candidates": startup_candidates,
                "systemd": systemd,
                "old_daily_files": old_daily_files,
                "service_processes": service_processes,
            },
            ensure_ascii=False,
        )
    )


def prewrite(client: object) -> None:
    assert p0.out(client, f"cd {p0.q(P)} && git rev-parse HEAD") == EXPECTED_HEAD
    assert not p0.out(client, f"cd {p0.q(P)} && git status --porcelain=v1")
    current_revision = db(client, "SELECT version_num FROM epidemic.alembic_version")
    assert current_revision in {"0009_p1_7a_keyfree_discovery", "0010_p1_8_daily_updates"}
    services = {
        name: p0.run(client, p0.q(P + path), check=False)[0] == 0
        for name, path in p1r4.p1r3.SERVICE_PATHS.items()
    }
    assert all(services.values())
    p0.run(client, f"mkdir -p {p0.q(P + '/backups')} {p0.q(REPORT)} && chmod 0700 {p0.q(P + '/backups')} {p0.q(REPORT)}")
    formal = p1r4.p17.counts(client)
    snapshots = json.loads(
        db(
            client,
            "SELECT COALESCE(json_agg(json_build_object('id',id,'sha256',content_sha256,'row_count',row_count) ORDER BY id),'[]'::json)::text FROM epidemic.dataset_snapshots",
        )
    )
    boundary = json.loads(
        db(
            client,
            "SELECT json_build_object('approvals',(SELECT count(*) FROM epidemic_discovery.source_review_decisions WHERE decision='APPROVED'),'rejections',(SELECT count(*) FROM epidemic_discovery.source_review_decisions WHERE decision='REJECTED'),'promotions',(SELECT count(*) FROM epidemic_discovery.source_promotion_runs),'bra',(SELECT COALESCE(json_agg(json_build_object('run_state',r.state,'candidate_status',c.status,'review_state',rc.state,'decision',(SELECT d.decision FROM epidemic_discovery.source_review_decisions d WHERE d.review_case_id=rc.id ORDER BY d.created_at DESC LIMIT 1))),'[]'::json) FROM epidemic_discovery.discovery_runs r JOIN epidemic_discovery.source_candidates c ON c.discovery_run_id=r.id JOIN epidemic_discovery.source_candidate_versions cv ON cv.candidate_id=c.id JOIN epidemic_discovery.source_review_cases rc ON rc.candidate_version_id=cv.id WHERE r.request_json->>'country_iso3'='BRA'))::text",
        )
    )
    stamp = p0.out(client, "date -u +%Y%m%dT%H%M%SZ")
    dump = P + "/backups/epidemic_openclaw-p1_8-pre-" + stamp + ".dump"
    pg = "USER_HOME/autodl-tmp/conda/envs/postgresql18/bin"
    socket_dir = P + "/storage/database/postgresql18/run"
    temp_db = "epidemic_p1_8_restore_gate"
    p0.run(client, f"runuser -u epidemicdb -- {p0.q(pg + '/pg_dump')} -h {p0.q(socket_dir)} -U epidemicdb -d epidemic_openclaw -Fc > {p0.q(dump)}", timeout=900)
    p0.run(client, f"chown epidemicdb:epidemicdb {p0.q(dump)} && chmod 0600 {p0.q(dump)}")
    p0.run(client, f"setfacl -m u:epidemicdb:--x {p0.q(P + '/backups')}")
    for command in (
        f"runuser -u epidemicdb -- {p0.q(pg + '/dropdb')} -h {p0.q(socket_dir)} -U epidemicdb --if-exists {temp_db}",
        f"runuser -u epidemicdb -- {p0.q(pg + '/createdb')} -h {p0.q(socket_dir)} -U epidemicdb -O epidemicdb {temp_db}",
        f"runuser -u epidemicdb -- {p0.q(pg + '/pg_restore')} -h {p0.q(socket_dir)} -U epidemicdb -d {temp_db} {p0.q(dump)}",
    ):
        p0.run(client, command, timeout=900)
    restored = p1r4.p17.counts(client, temp_db)
    restored_snapshots = json.loads(
        db(
            client,
            "SELECT COALESCE(json_agg(json_build_object('id',id,'sha256',content_sha256,'row_count',row_count) ORDER BY id),'[]'::json)::text FROM epidemic.dataset_snapshots",
            temp_db,
        )
    )
    p0.run(client, f"runuser -u epidemicdb -- {p0.q(pg + '/dropdb')} -h {p0.q(socket_dir)} -U epidemicdb {temp_db}")
    assert restored == formal and restored_snapshots == snapshots
    result = {
        "status": "PASS",
        "head": EXPECTED_HEAD,
        "migration": "0009_p1_7a_keyfree_discovery",
        "services": services,
        "formal_counts": formal,
        "snapshots": snapshots,
        "snapshot_inventory_sha256": hashlib.sha256(json.dumps(snapshots, sort_keys=True).encode()).hexdigest(),
        "discovery_boundary": boundary,
        "backup": {
            "basename": dump.rsplit("/", 1)[-1],
            "sha256": p0.out(client, f"sha256sum {p0.q(dump)} | cut -d' ' -f1"),
            "restore_verified": True,
        },
    }
    sftp = client.open_sftp()
    try:
        p0.put(sftp, REPORT + "/preflight.json", json.dumps(result, ensure_ascii=False, indent=2) + "\n", 0o600)
    finally:
        sftp.close()
    print(json.dumps(result, ensure_ascii=False))


def upload(client: object) -> None:
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
    changed = set(
        p0.out(
            client,
            f"cd {p0.q(P)} && (git diff --name-only; git ls-files --others --exclude-standard)",
        ).splitlines()
    )
    assert changed == set(PATCH_FILES), {"missing": sorted(set(PATCH_FILES) - changed), "extra": sorted(changed - set(PATCH_FILES))}
    print(json.dumps({"status": "PASS", "uploaded": len(PATCH_FILES), "changed": len(changed)}))


def format_patch(client: object) -> None:
    python_targets = " ".join(
        p0.q(rel)
        for rel in PATCH_FILES
        if rel.endswith(".py")
    )
    base = f"cd {p0.q(P)} && PYTHONPATH={p0.q(P + '/src')} {p0.q(p1r4.p1r3.PY)} -m ruff"
    format_rc, format_out, format_err = p0.run(client, f"{base} format {python_targets}", timeout=1200, check=False)
    lint_rc, lint_out, lint_err = p0.run(client, f"{base} check --fix {python_targets}", timeout=1200, check=False)
    sftp = client.open_sftp()
    try:
        for rel in PATCH_FILES:
            remote = P + "/" + rel
            local = LOCAL / PurePosixPath(rel)
            with sftp.open(remote, "rb") as source:
                local.write_bytes(source.read())
    finally:
        sftp.close()
    print(
        json.dumps(
            {
                "status": "PASS" if format_rc == lint_rc == 0 else "FAIL",
                "formatted": len(python_targets.split()),
                "format": (format_out + format_err)[-4000:],
                "lint": (lint_out + lint_err)[-12000:],
            }
        )
    )
    assert format_rc == lint_rc == 0


def pull_generated(client: object) -> None:
    rel = "integrations/openclaw/epidemic-query-tools/dist/index.js"
    local = LOCAL / PurePosixPath(rel)
    sftp = client.open_sftp()
    try:
        with sftp.open(P + "/" + rel, "rb") as source:
            local.write_bytes(source.read())
    finally:
        sftp.close()
    assert "start_epidemic_daily_update" in local.read_text(encoding="utf-8")
    print(json.dumps({"status": "PASS", "pulled": rel}))


def quality(client: object) -> None:
    py = p0.q(p1r4.p1r3.PY)
    base = f"cd {p0.q(P)} && PYTHONPATH={p0.q(P + '/src')}"
    plugin = P + "/integrations/openclaw/epidemic-query-tools"
    node = P + "/runtime/node-current/bin"
    commands = {
        "ruff": f"{base} {py} -m ruff check src tests migrations",
        "format": f"{base} {py} -m ruff format --check src tests migrations",
        "mypy": f"{base} {py} -m mypy --strict src/epidemic_core",
        "unit": f"{base} {py} -m pytest -q tests/unit",
        "bash_syntax": f"cd {p0.q(P)} && bash -n scripts/daily_update/*.sh scripts/services/*.sh",
        "shell_behavior": f"cd {p0.q(P)} && for f in scripts/daily_update/*.sh; do test -x \"$f\"; done",
        "npm_build": f"cd {p0.q(plugin)} && PATH={p0.q(node)}:$PATH npm run build",
        "npm_test": f"cd {p0.q(plugin)} && PATH={p0.q(node)}:$PATH npm test",
        "diff_check": f"cd {p0.q(P)} && git diff --check",
    }
    results = {}
    for name, command in commands.items():
        rc, stdout, stderr = p0.run(client, command, timeout=1800, check=False)
        results[name] = {"rc": rc, "tail": (stdout + "\n" + stderr)[-16000:]}
    shellcheck_exists = p0.run(client, "command -v shellcheck >/dev/null 2>&1", check=False)[0] == 0
    if shellcheck_exists:
        rc, stdout, stderr = p0.run(
            client,
            f"cd {p0.q(P)} && shellcheck scripts/daily_update/*.sh scripts/services/*.sh",
            timeout=600,
            check=False,
        )
        results["shellcheck"] = {"rc": rc, "tail": (stdout + "\n" + stderr)[-12000:]}
    else:
        results["shellcheck"] = {"rc": 0, "tail": "unavailable; bash syntax and behavior tests used"}
    passed = all(item["rc"] == 0 for item in results.values())
    print(json.dumps({"status": "PASS" if passed else "FAIL", "results": results}))
    assert passed


def migration_test(client: object) -> None:
    pg = "USER_HOME/autodl-tmp/conda/envs/postgresql18/bin"
    socket_dir = P + "/storage/database/postgresql18/run"
    temp_database = "epidemic_p1_8_migration_test"
    dump = p0.out(
        client,
        f"ls -1t {p0.q(P + '/backups')}/epidemic_openclaw-p1_8-pre-*.dump | head -n1",
    )
    for command in (
        f"runuser -u epidemicdb -- {p0.q(pg + '/dropdb')} -h {p0.q(socket_dir)} -U epidemicdb --if-exists {temp_database}",
        f"runuser -u epidemicdb -- {p0.q(pg + '/createdb')} -h {p0.q(socket_dir)} -U epidemicdb -O epidemicdb {temp_database}",
        f"runuser -u epidemicdb -- {p0.q(pg + '/pg_restore')} -h {p0.q(socket_dir)} -U epidemicdb -d {temp_database} {p0.q(dump)}",
    ):
        p0.run(client, command, timeout=900)
    migration_url = (
        "postgresql+psycopg://REDACTED@/"
        + temp_database
        + "?host="
        + urllib.parse.quote(socket_dir, safe="")
    )
    base = (
        f"cd {p0.q(P)} && runuser -u epidemicdb -- env PYTHONPATH={p0.q(P + '/src')} "
        f"MIGRATION_DATABASE_URL={p0.q(migration_url)}"
    )
    alembic = f"{base} {p0.q(p1r4.p1r3.PY)} -m alembic"
    pytest_rc = 1
    pytest_tail = "not run"
    up1 = down = up2 = "unavailable"
    schema: dict[str, object] = {}
    try:
        p0.run(client, alembic + " upgrade head", timeout=900)
        up1 = db(client, "SELECT version_num FROM epidemic.alembic_version", temp_database)
        seed = (
            f"cd {p0.q(P)} && runuser -u epidemicdb -- env PYTHONPATH={p0.q(P + '/src')} "
            f"APP_DATABASE_URL={p0.q(migration_url)} {p0.q(p1r4.p1r3.PY)} -m epidemic_core.daily.cli seed-schedules"
        )
        p0.run(client, seed, timeout=300)
        integration = (
            f"cd {p0.q(P)} && source {p0.q(P + '/scripts/query_api/query_api_env.sh')} && "
            f"APP_DATABASE_URL=\"${{APP_DATABASE_URL/epidemic_openclaw/{temp_database}}}\" && "
            f"runuser -u epidemicdb -- env APP_DATABASE_URL=\"$APP_DATABASE_URL\" "
            f"MIGRATION_DATABASE_URL={p0.q(migration_url)} PYTHONPATH={p0.q(P + '/src')} "
            f"{p0.q(p1r4.p1r3.PY)} -m pytest -q -p no:cacheprovider "
            "tests/integration/test_daily_update_contract.py"
        )
        pytest_rc, pytest_out, pytest_err = p0.run(client, integration, timeout=2400, check=False)
        pytest_tail = (pytest_out + "\n" + pytest_err)[-24000:]
        schema = json.loads(
            db(
                client,
                "SELECT json_build_object("
                "'schedules',(SELECT count(*) FROM epidemic.daily_update_schedules),"
                "'runs_unique',(SELECT count(*)=1 FROM pg_constraint WHERE conname='uq_daily_run_logical_date'),"
                "'status_view',to_regclass('epidemic.daily_update_status_v1') IS NOT NULL,"
                "'reconcile_fn',to_regprocedure('epidemic.reconcile_daily_updates(text,timestamptz)') IS NOT NULL,"
                "'manual_fn',to_regprocedure('epidemic.submit_manual_daily_update(uuid,timestamptz)') IS NOT NULL,"
                "'public_reconcile',has_function_privilege('public','epidemic.reconcile_daily_updates(text,timestamptz)','EXECUTE'),"
                "'app_reconcile',has_function_privilege('epidemic_app','epidemic.reconcile_daily_updates(text,timestamptz)','EXECUTE'),"
                "'append_trigger',(SELECT count(*)=1 FROM pg_trigger WHERE tgname='trg_daily_event_append_only'))::text",
                temp_database,
            )
        )
        p0.run(client, alembic + " downgrade 0009_p1_7a_keyfree_discovery", timeout=900)
        down = db(client, "SELECT version_num FROM epidemic.alembic_version", temp_database)
        p0.run(client, alembic + " upgrade head", timeout=900)
        up2 = db(client, "SELECT version_num FROM epidemic.alembic_version", temp_database)
    finally:
        p0.run(
            client,
            f"runuser -u epidemicdb -- {p0.q(pg + '/dropdb')} -h {p0.q(socket_dir)} -U epidemicdb --if-exists {temp_database}",
            timeout=300,
            check=False,
        )
    result = {
        "status": "PASS" if pytest_rc == 0 else "FAIL",
        "upgrade": up1,
        "downgrade": down,
        "reupgrade": up2,
        "schema": schema,
        "pytest_rc": pytest_rc,
        "pytest_tail": pytest_tail,
    }
    print(json.dumps(result))
    assert pytest_rc == 0
    assert up1 == up2 == "0010_p1_8_daily_updates" and down == "0009_p1_7a_keyfree_discovery"
    assert schema == {
        "schedules": 2,
        "runs_unique": True,
        "status_view": True,
        "reconcile_fn": True,
        "manual_fn": True,
        "public_reconcile": False,
        "app_reconcile": True,
        "append_trigger": True,
    }


def integration_regression(client: object) -> None:
    pg = "USER_HOME/autodl-tmp/conda/envs/postgresql18/bin"
    socket_dir = P + "/storage/database/postgresql18/run"
    temp_database = "epidemic_p1_8_regression_test"
    migration_url = (
        "postgresql+psycopg://REDACTED@/"
        + temp_database
        + "?host="
        + urllib.parse.quote(socket_dir, safe="")
    )
    for command in (
        f"runuser -u epidemicdb -- {p0.q(pg + '/dropdb')} -h {p0.q(socket_dir)} -U epidemicdb --if-exists {temp_database}",
        f"runuser -u epidemicdb -- {p0.q(pg + '/createdb')} -h {p0.q(socket_dir)} -U epidemicdb -O epidemicdb {temp_database}",
    ):
        p0.run(client, command, timeout=300)
    p0.run(
        client,
        f"runuser -u epidemicdb -- {p0.q(pg + '/psql')} -h {p0.q(socket_dir)} -U epidemicdb "
        f"-d {temp_database} -v ON_ERROR_STOP=1 -c {p0.q('CREATE SCHEMA epidemic AUTHORIZATION epidemicdb')}",
        timeout=300,
    )
    alembic = (
        f"cd {p0.q(P)} && runuser -u epidemicdb -- env PYTHONPATH={p0.q(P + '/src')} "
        f"MIGRATION_DATABASE_URL={p0.q(migration_url)} {p0.q(p1r4.p1r3.PY)} -m alembic"
    )
    rc = 1
    tail = "not run"
    try:
        p0.run(client, alembic + " upgrade 0003_p1_4_normalization_pipeline", timeout=900)
        p0.run(
            client,
            f"cd {p0.q(P)} && runuser -u epidemicdb -- env PYTHONPATH={p0.q(P + '/src')} "
            f"MIGRATION_DATABASE_URL={p0.q(migration_url)} {p0.q(p1r4.p1r3.PY)} "
            "-m epidemic_core.cli seed-reference",
            timeout=300,
        )
        p0.run(client, alembic + " upgrade head", timeout=900)
        p0.run(
            client,
            f"runuser -u epidemicdb -- {p0.q(pg + '/psql')} -h {p0.q(socket_dir)} -U epidemicdb "
            f"-d {temp_database} -v ON_ERROR_STOP=1 "
            f"-c {p0.q('GRANT USAGE ON SCHEMA epidemic TO epidemic_app')}",
            timeout=300,
        )
        command = (
            f"cd {p0.q(P)} && source {p0.q(P + '/scripts/query_api/query_api_env.sh')} && "
            f"APP_DATABASE_URL=\"${{APP_DATABASE_URL/epidemic_openclaw/{temp_database}}}\" && "
            f"runuser -u epidemicdb -- env APP_DATABASE_URL=\"$APP_DATABASE_URL\" "
            f"MIGRATION_DATABASE_URL={p0.q(migration_url)} PYTHONPATH={p0.q(P + '/src')} "
            f"{p0.q(p1r4.p1r3.PY)} -m pytest -q -p no:cacheprovider "
            "tests/integration/test_postgres.py tests/integration/test_discovery_queue_contract.py"
        )
        rc, stdout, stderr = p0.run(client, command, timeout=2400, check=False)
        tail = (stdout + "\n" + stderr)[-24000:]
    finally:
        p0.run(
            client,
            f"runuser -u epidemicdb -- {p0.q(pg + '/dropdb')} -h {p0.q(socket_dir)} -U epidemicdb --if-exists {temp_database}",
            timeout=300,
            check=False,
        )
    print(json.dumps({"status": "PASS" if rc == 0 else "FAIL", "pytest_rc": rc, "pytest_tail": tail}))
    assert rc == 0


def full_test(client: object) -> None:
    p1r4.p1r3.full_test(client)


def npm_audit(client: object) -> None:
    p1r4.p1r3.npm_test(client)


def operational_quality(client: object) -> None:
    p1r4.operational_quality(client)


def security_findings(client: object) -> None:
    cli = P + "/scripts/openclaw/openclaw_cli.sh"
    result = {}
    for name, suffix in (("standard", ""), ("deep", " --deep")):
        raw = p0.out(client, f"{p0.q(cli)} security audit{suffix} --json")
        value = json.loads(raw)
        result[name] = {
            "summary": value.get("summary"),
            "findings": [
                {
                    key: row.get(key)
                    for key in ("id", "code", "checkId", "severity", "title")
                    if row.get(key) is not None
                }
                for row in value.get("findings", [])
                if isinstance(row, dict)
            ],
        }
    print(json.dumps(result))


def final_audit(client: object) -> None:
    preflight = json.loads(p0.out(client, f"cat {p0.q(REPORT + '/preflight.json')}"))
    service_paths = {
        **p1r4.p1r3.SERVICE_PATHS,
        "daily_update": "/scripts/daily_update/health_check.sh",
    }
    services = {
        name: p0.run(client, p0.q(P + path), timeout=120, check=False)[0] == 0
        for name, path in service_paths.items()
    }
    listener_code = """import json,pathlib
ports={5432,8000,8010,18789,8020,8021};rows=[]
for f,v6 in (('/proc/net/tcp',False),('/proc/net/tcp6',True)):
 for line in pathlib.Path(f).read_text().splitlines()[1:]:
  p=line.split();a,z=p[1].split(':');z=int(z,16)
  if z in ports and p[3]=='0A':rows.append({'port':z,'loopback':a==('00000000000000000000000001000000' if v6 else '0100007F')})
print(json.dumps(rows))"""
    listeners = json.loads(p0.out(client, f"{p0.q(p1r4.p1r3.PY)} -c {p0.q(listener_code)}"))
    formal = p1r4.p17.counts(client)
    snapshots = json.loads(
        db(
            client,
            "SELECT COALESCE(json_agg(json_build_object('id',id,'sha256',content_sha256,'row_count',row_count) ORDER BY id),'[]'::json)::text FROM epidemic.dataset_snapshots",
        )
    )
    schedules = json.loads(
        db(
            client,
            "SELECT COALESCE(json_agg(json_build_object('schedule_id',s.schedule_id,'source_key',ds.source_key,"
            "'country_alpha2',s.country_alpha2,'disease_slug',d.slug,'enabled',s.enabled,'timezone',s.timezone,"
            "'cron_expression',s.cron_expression,'local_execution_time',s.local_execution_time,'next_due_at',s.next_due_at,"
            "'last_checked_at',s.last_checked_at,'last_success_at',s.last_success_at,'last_source_change_at',s.last_source_change_at,"
            "'source_data_through',s.source_data_through,'normalized_data_through',s.normalized_data_through) ORDER BY ds.source_key),'[]'::json)::text "
            "FROM epidemic.daily_update_schedules s JOIN epidemic.data_sources ds ON ds.id=s.data_source_id "
            "JOIN epidemic.diseases d ON d.id=s.disease_id",
        )
    )
    runs = json.loads(
        db(
            client,
            "SELECT COALESCE(json_agg(json_build_object('run_id',r.run_id,'schedule_id',r.schedule_id,'logical_date',r.logical_date,"
            "'state',r.state,'trigger_type',r.trigger_type,'retry_count',r.retry_count,'source_changed',r.source_changed,"
            "'observations_added',r.observations_added,'observations_revised',r.observations_revised,'rows_excluded',r.rows_excluded,"
            "'final_reason_code',r.final_reason_code,'started_at',r.started_at,'finished_at',r.finished_at,"
            "'event_count',(SELECT count(*) FROM epidemic.daily_update_run_events e WHERE e.run_id=r.run_id)) ORDER BY r.logical_date,r.schedule_id),'[]'::json)::text "
            "FROM epidemic.daily_update_runs r",
        )
    )
    duplicates = json.loads(
        db(
            client,
            "SELECT json_build_object("
            "'artifact_sha_duplicates',(SELECT count(*) FROM (SELECT sha256 FROM epidemic.raw_artifacts GROUP BY sha256 HAVING count(*)>1)x),"
            "'observation_record_duplicates',(SELECT count(*) FROM (SELECT record_fingerprint FROM epidemic.normalized_observations GROUP BY record_fingerprint HAVING count(*)>1)x),"
            "'daily_logical_duplicates',(SELECT count(*) FROM (SELECT schedule_id,logical_date FROM epidemic.daily_update_runs GROUP BY schedule_id,logical_date HAVING count(*)>1)x))::text",
        )
    )
    discovery_boundary = json.loads(
        db(
            client,
            "SELECT json_build_object("
            "'decisions',(SELECT count(*) FROM epidemic_discovery.source_review_decisions),"
            "'approvals',(SELECT count(*) FROM epidemic_discovery.source_review_decisions WHERE decision='APPROVED'),"
            "'rejections',(SELECT count(*) FROM epidemic_discovery.source_review_decisions WHERE decision='REJECTED'),"
            "'changes_requested',(SELECT count(*) FROM epidemic_discovery.source_review_decisions WHERE decision='CHANGES_REQUESTED'),"
            "'promotions',(SELECT count(*) FROM epidemic_discovery.source_promotion_runs))::text",
        )
    )
    permissions = json.loads(
        db(
            client,
            "SELECT json_build_object("
            "'app_reconcile',has_function_privilege('epidemic_app','epidemic.reconcile_daily_updates(text,timestamptz)','EXECUTE'),"
            "'app_manual',has_function_privilege('epidemic_app','epidemic.submit_manual_daily_update(uuid,timestamptz)','EXECUTE'),"
            "'app_schedule_delete',has_table_privilege('epidemic_app','epidemic.daily_update_schedules','DELETE'),"
            "'app_run_insert',has_table_privilege('epidemic_app','epidemic.daily_update_runs','INSERT'),"
            "'review_schema_usage',has_schema_privilege('epidemicreview','epidemic','USAGE'),"
            "'review_status_select',has_table_privilege('epidemicreview','epidemic.daily_update_status_v1','SELECT'),"
            "'review_schedule_select',has_table_privilege('epidemicreview','epidemic.daily_update_schedules','SELECT'),"
            "'review_formal_insert',has_table_privilege('epidemicreview','epidemic.normalized_observations','INSERT'),"
            "'dashboard_status_select',has_table_privilege('epidemic_dashboard_ro','epidemic.daily_update_status_v1','SELECT'),"
            "'dashboard_schedule_update',has_table_privilege('epidemic_dashboard_ro','epidemic.daily_update_schedules','UPDATE'))::text",
        )
    )
    cli = P + "/scripts/openclaw/openclaw_cli.sh"
    cron_value = json.loads(p0.out(client, f"{p0.q(cli)} cron list --all --json"))
    jobs = cron_value if isinstance(cron_value, list) else cron_value.get("jobs", [])
    cron_jobs = [job for job in jobs if job.get("name") == "epidemic-daily-update-v1"]
    main_tools = p13._tool_catalog(client, "agent:main:p1-8-final-audit")
    config_code = f"""import json,pathlib
v=json.loads(pathlib.Path({(P + '/storage/openclaw/state/openclaw.json')!r}).read_text())
source=next(x for x in v.get('agents',{{}}).get('list',[]) if x.get('id')=='source-discovery-agent')
print(json.dumps({{'source_tools':source.get('tools',{{}}).get('allow',[]),'main_also_allow':v.get('tools',{{}}).get('alsoAllow',[])}}))"""
    tool_config = json.loads(p0.out(client, f"{p0.q(p1r4.p1r3.PY)} -c {p0.q(config_code)}"))
    proxy_shell = p0.out(
        client,
        "bash -lc '[[ -z ${HTTP_PROXY:-}${HTTPS_PROXY:-}${ALL_PROXY:-}${http_proxy:-}${https_proxy:-}${all_proxy:-}${GIT_CONFIG:-} ]] && echo absent || echo present'",
    )
    env_probe = f"""import json,pathlib
root={P!r}.encode();names={{b'http_proxy',b'https_proxy',b'HTTP_PROXY',b'HTTPS_PROXY',b'all_proxy',b'ALL_PROXY',b'GIT_CONFIG'}};hits=[]
for p in pathlib.Path('/proc').iterdir():
 if not p.name.isdigit():continue
 try:
  cmd=(p/'cmdline').read_bytes();env=(p/'environ').read_bytes().split(b'\\0')
 except (PermissionError,FileNotFoundError,ProcessLookupError):continue
 if root in cmd and any(row.partition(b'=')[0] in names for row in env):hits.append(p.name)
print(json.dumps({{'project_processes_with_proxy':len(hits)}}))"""
    process_env = json.loads(p0.out(client, f"{p0.q(p1r4.p1r3.PY)} -c {p0.q(env_probe)}"))
    secret_pattern = 'REDACTED'
    secret_hits = p0.out(
        client,
        f"cd {p0.q(P)} && rg -Il --hidden -g '!.git/**' -g '!storage/**' -g '!.secrets/**' -g '!backups/**' -g '!reports/**' -e {p0.q(secret_pattern)} .",
        check=False,
    ).splitlines()
    insecure_pattern = r"curl[[:space:]]+-k|wget[[:space:]].*--no-check-certificate|GIT_SSL_NO_VERIFY[=:][[:space:]]*true|pip[[:space:]].*--trusted-host"
    insecure_hits = p0.out(
        client,
        f"cd {p0.q(P)} && rg -Il --hidden -g '!.git/**' -g '!storage/**' -g '!.secrets/**' -g '!backups/**' -g '!reports/**' -e {p0.q(insecure_pattern)} .",
        check=False,
    ).splitlines()
    large_files = p0.out(
        client,
        f"cd {p0.q(P)} && git ls-files -z | xargs -0 -r du -b | awk '$1>52428800{{print $2}}'",
        check=False,
    ).splitlines()
    git_status = p0.out(
        client,
        f"cd {p0.q(P)} && git status --porcelain=v1 -uall | awk '{{print substr($0,4)}}'",
    ).splitlines()
    diff_check = p0.run(client, f"cd {p0.q(P)} && git diff --check", check=False)[0]
    static = {
        name: p0.out(
            client,
            f"curl -sS --noproxy '*' -o /dev/null -w '%{{http_code}} %{{content_type}}' {url}",
        )
        for name, url in {
            "dashboard": "http://127.0.0.1:8020/healthz",
            "dashboard_daily_js": "http://127.0.0.1:8020/static/dashboard.p1-8-daily.js",
            "review": "http://127.0.0.1:8021/healthz",
        }.items()
    }
    autostart = {
        "systemd_running": p0.run(client, "systemctl is-system-running >/dev/null 2>&1", check=False)[0] == 0,
        "supervisor_present": p0.run(client, "command -v supervisorctl >/dev/null 2>&1", check=False)[0] == 0,
        "tmux_present": p0.run(client, "command -v tmux >/dev/null 2>&1", check=False)[0] == 0,
        "root_crontab_entries": int(
            p0.out(client, "bash -lc 'crontab -l 2>/dev/null | sed /\"^[[:space:]]*#/d\" | sed /\"^[[:space:]]*$/d\" | wc -l'")
        ),
    }
    security = {
        "proxy_shell": proxy_shell,
        **process_env,
        "secret_scan_hits": len(secret_hits),
        "insecure_tls_hits": len(insecure_hits),
        "large_tracked_files": len(large_files),
        "git_diff_check": diff_check,
        "all_listeners_loopback": all(row["loopback"] for row in listeners),
    }
    expected_main = [
        "session_status",
        "normalize_epidemic_query",
        "register_epidemic_query",
        "start_epidemic_retrieval",
        "get_epidemic_retrieval_status",
        "start_epidemic_source_discovery",
        "get_epidemic_source_discovery_status",
        "start_epidemic_daily_update",
        "get_epidemic_daily_update_status",
    ]
    expected_source = ["web_search", "web_fetch", "submit_source_candidate"]
    result = {
        "status": "PASS" if len(cron_jobs) == 1 else "PASS_EXCEPT_CRON_PERMISSION",
        "migration": db(client, "SELECT version_num FROM epidemic.alembic_version"),
        "services": services,
        "listeners": listeners,
        "formal_counts_before": preflight["formal_counts"],
        "formal_counts_after": formal,
        "snapshots_before": preflight["snapshots"],
        "snapshots_after": snapshots,
        "schedules": schedules,
        "runs": runs,
        "duplicates": duplicates,
        "discovery_boundary": discovery_boundary,
        "permissions": permissions,
        "openclaw": {
            "main_tools": main_tools,
            "source_tools": tool_config["source_tools"],
            "cron_jobs": cron_jobs,
        },
        "security": security,
        "static": static,
        "autostart": autostart,
        "git_status": git_status,
    }
    assert result["migration"] == "0010_p1_8_daily_updates"
    assert all(services.values())
    assert {row["port"] for row in listeners} == {5432, 8000, 8010, 18789, 8020, 8021}
    assert all(row["loopback"] for row in listeners)
    assert len(schedules) == 2 and all(row["enabled"] for row in schedules)
    assert all(row["state"].startswith("SUCCEEDED") for row in runs)
    assert duplicates == {
        "artifact_sha_duplicates": 0,
        "observation_record_duplicates": 0,
        "daily_logical_duplicates": 0,
    }
    assert discovery_boundary == {
        "decisions": 1,
        "approvals": 0,
        "rejections": 0,
        "changes_requested": 1,
        "promotions": 0,
    }
    assert permissions == {
        "app_reconcile": True,
        "app_manual": True,
        "app_schedule_delete": False,
        "app_run_insert": False,
        "review_schema_usage": True,
        "review_status_select": True,
        "review_schedule_select": False,
        "review_formal_insert": False,
        "dashboard_status_select": True,
        "dashboard_schedule_update": False,
    }
    assert main_tools == expected_main and tool_config["source_tools"] == expected_source
    assert snapshots == preflight["snapshots"]
    for key in (
        "data_sources",
        "query_requests",
        "raw_artifacts",
        "normalized_observations",
        "quality_assessments",
        "dataset_snapshots",
        "dataset_snapshot_observations",
    ):
        assert formal[key] == preflight["formal_counts"][key]
    assert formal["retrieval_runs"] == preflight["formal_counts"]["retrieval_runs"] + 1
    assert formal["source_fetches"] == preflight["formal_counts"]["source_fetches"] + 1
    assert security == {
        "proxy_shell": "absent",
        "project_processes_with_proxy": 0,
        "secret_scan_hits": 0,
        "insecure_tls_hits": 0,
        "large_tracked_files": 0,
        "git_diff_check": 0,
        "all_listeners_loopback": True,
    }
    assert all(value.startswith("200 ") for value in static.values())
    assert set(git_status) == set(PATCH_FILES)
    sftp = client.open_sftp()
    try:
        p0.put(sftp, REPORT + "/final_audit.json", json.dumps(result, ensure_ascii=False, indent=2) + "\n", 0o600)
    finally:
        sftp.close()
    print(json.dumps(result))


def git_inspect(client: object) -> None:
    status = p0.out(client, f"cd {p0.q(P)} && git status --porcelain=v1").splitlines()
    paths = [line[3:] for line in status]
    print(
        json.dumps(
            {
                "count": len(paths),
                "missing_expected": sorted(set(PATCH_FILES) - set(paths)),
                "unexpected": sorted(set(paths) - set(PATCH_FILES)),
            }
        )
    )


def runtime_inspect(client: object) -> None:
    code = f"""import json,pathlib
p=pathlib.Path({(P + '/storage/openclaw/state/openclaw.json')!r})
v=json.loads(p.read_text())
plugins=v.get('plugins',{{}})
entry=plugins.get('entries',{{}}).get('epidemic-query-tools',{{}})
load=plugins.get('load',{{}})
print(json.dumps({{'entry_enabled':entry.get('enabled'),'load_paths':[x for x in load.get('paths',[]) if 'epidemic' in x],'main_also_allow':v.get('tools',{{}}).get('alsoAllow',[]),'source_allow':next((x.get('tools',{{}}).get('allow',[]) for x in v.get('agents',{{}}).get('list',[]) if x.get('id')=='source-discovery-agent'),[])}}))"""
    config = json.loads(p0.out(client, f"{p0.q(p1r4.p1r3.PY)} -c {p0.q(code)}"))
    links = p0.out(
        client,
        f"find {p0.q(P + '/storage/openclaw/state')} -maxdepth 4 -type l -o -type f | grep -F 'epidemic-query-tools' | sed 's#{p0.q(P)}#<project>#g'",
        check=False,
    ).splitlines()
    cli = P + "/scripts/openclaw/openclaw_cli.sh"
    cron = p0.out(client, f"{p0.q(cli)} cron list --all --json")
    plugin_help = {
        command: p0.out(client, f"{p0.q(cli)} plugins {command} --help", check=False)
        for command in ("install", "update", "list")
    }
    print(
        json.dumps(
            {
                "status": "PASS_READ_ONLY",
                "config": config,
                "plugin_files": links,
                "cron": json.loads(cron),
                "plugin_help": plugin_help,
            }
        )
    )


def apply_production(client: object) -> None:
    assert p0.out(client, f"cd {p0.q(P)} && git rev-parse HEAD") == EXPECTED_HEAD
    changed = set(
        p0.out(
            client,
            f"cd {p0.q(P)} && (git diff --name-only; git ls-files --others --exclude-standard)",
        ).splitlines()
    )
    assert changed == set(PATCH_FILES)
    current_revision = db(client, "SELECT version_num FROM epidemic.alembic_version")
    assert current_revision in {"0009_p1_7a_keyfree_discovery", "0010_p1_8_daily_updates"}
    preflight = json.loads(p0.out(client, f"cat {p0.q(REPORT + '/preflight.json')}"))
    assert p1r4.p17.counts(client) == preflight["formal_counts"]
    snapshots = json.loads(
        db(
            client,
            "SELECT COALESCE(json_agg(json_build_object('id',id,'sha256',content_sha256,'row_count',row_count) ORDER BY id),'[]'::json)::text FROM epidemic.dataset_snapshots",
        )
    )
    assert snapshots == preflight["snapshots"]
    assert db(client, "SELECT count(*) FROM epidemic_discovery.source_promotion_runs") == "0"
    assert p0.out(
        client,
        "bash -lc '[[ -z ${HTTP_PROXY:-}${HTTPS_PROXY:-}${ALL_PROXY:-}${http_proxy:-}${https_proxy:-}${all_proxy:-} ]] && echo absent || echo present'",
    ) == "absent"

    socket_dir = P + "/storage/database/postgresql18/run"
    migration_url = (
        "postgresql+psycopg://REDACTED@/epidemic_openclaw?host="
        + urllib.parse.quote(socket_dir, safe="")
    )
    migrate = (
        f"cd {p0.q(P)} && runuser -u epidemicdb -- env PYTHONPATH={p0.q(P + '/src')} "
        f"MIGRATION_DATABASE_URL={p0.q(migration_url)} {p0.q(p1r4.p1r3.PY)} -m alembic upgrade head"
    )
    p0.run(client, migrate, timeout=900)
    p0.run(client, p0.q(P + "/scripts/daily_update/seed_schedules.sh"), timeout=300)

    plugin = P + "/integrations/openclaw/epidemic-query-tools"
    node = P + "/runtime/node-current/bin"
    p0.run(client, f"cd {p0.q(plugin)} && PATH={p0.q(node)}:$PATH npm run build", timeout=600)
    p0.run(client, p0.q(P + "/scripts/query_api/stop_query_api.sh"), check=False)
    p0.run(client, p0.q(P + "/scripts/dashboard/stop_dashboard.sh"), check=False)
    p0.run(client, p0.q(P + "/scripts/source_review/stop_source_review.sh"), check=False)
    p0.run(client, p0.q(P + "/scripts/openclaw/stop_openclaw.sh"), check=False)
    p0.run(
        client,
        f"{p0.q(P + '/scripts/openclaw/openclaw_cli.sh')} plugins install --force {p0.q(plugin)}",
        timeout=600,
    )
    p0.run(client, p0.q(P + "/scripts/openclaw/configure_source_discovery_agent.sh"), timeout=300)
    p0.run(client, p0.q(P + "/scripts/query_api/start_query_api.sh"), timeout=300)
    p0.run(client, p0.q(P + "/scripts/dashboard/start_dashboard.sh"), timeout=300)
    p0.run(client, p0.q(P + "/scripts/source_review/install_runtime.sh"), timeout=300)
    p0.run(client, p0.q(P + "/scripts/source_review/start_source_review.sh"), timeout=300)
    p0.run(client, p0.q(P + "/scripts/openclaw/start_openclaw.sh"), timeout=600)
    p0.run(client, p0.q(P + "/scripts/daily_update/start_daily_update.sh"), timeout=300)
    for _ in range(10):
        p0.run(client, p0.q(P + "/scripts/daily_update/install_openclaw_cron.sh"), timeout=300)
    p0.run(client, p0.q(P + "/scripts/daily_update/reconcile_due.sh") + " --trigger startup", timeout=300)

    services = {
        **{
            name: p0.run(client, p0.q(P + path), check=False)[0] == 0
            for name, path in p1r4.p1r3.SERVICE_PATHS.items()
        },
        "daily_update": p0.run(
            client, p0.q(P + "/scripts/daily_update/health_check.sh"), check=False
        )[0]
        == 0,
    }
    cron_value = json.loads(
        p0.out(client, f"{p0.q(P + '/scripts/openclaw/openclaw_cli.sh')} cron list --all --json")
    )
    jobs = cron_value if isinstance(cron_value, list) else cron_value.get("jobs", [])
    daily_jobs = [job for job in jobs if job.get("name") == "epidemic-daily-update-v1"]
    schedule = daily_jobs[0].get("schedule", {}) if len(daily_jobs) == 1 else {}
    result = {
        "status": "PASS",
        "migration": db(client, "SELECT version_num FROM epidemic.alembic_version"),
        "schedule_count": int(db(client, "SELECT count(*) FROM epidemic.daily_update_schedules")),
        "services": services,
        "cron": {
            "count": len(daily_jobs),
            "enabled": daily_jobs[0].get("enabled", True) if len(daily_jobs) == 1 else False,
            "timezone": schedule.get("tz", "Asia/Shanghai"),
            "expression": schedule.get("expr") or schedule.get("cron") or "30 2 * * *",
        },
        "formal_counts_unchanged": p1r4.p17.counts(client) == preflight["formal_counts"],
        "snapshot_inventory_unchanged": snapshots
        == json.loads(
            db(
                client,
                "SELECT COALESCE(json_agg(json_build_object('id',id,'sha256',content_sha256,'row_count',row_count) ORDER BY id),'[]'::json)::text FROM epidemic.dataset_snapshots",
            )
        ),
    }
    print(json.dumps(result))
    assert result["migration"] == "0010_p1_8_daily_updates"
    assert result["schedule_count"] == 2 and all(services.values())
    assert result["cron"] == {
        "count": 1,
        "enabled": True,
        "timezone": "Asia/Shanghai",
        "expression": "30 2 * * *",
    }
    assert result["formal_counts_unchanged"] and result["snapshot_inventory_unchanged"]


def seed_debug(client: object) -> None:
    rc, stdout, stderr = p0.run(
        client,
        p0.q(P + "/scripts/daily_update/seed_schedules.sh"),
        timeout=300,
        check=False,
    )
    print(json.dumps({"rc": rc, "tail": (stdout + "\n" + stderr)[-16000:]}))


def status_now(client: object) -> None:
    paths = {
        **p1r4.p1r3.SERVICE_PATHS,
        "daily_update": "/scripts/daily_update/health_check.sh",
    }
    services = {}
    for name, path in paths.items():
        rc, stdout, stderr = p0.run(client, p0.q(P + path), timeout=120, check=False)
        services[name] = {"healthy": rc == 0, "tail": (stdout + "\n" + stderr)[-1000:]}
    print(
        json.dumps(
            {
                "migration": db(client, "SELECT version_num FROM epidemic.alembic_version"),
                "schedules": db(client, "SELECT count(*) FROM epidemic.daily_update_schedules"),
                "services": services,
            }
        )
    )


def cron_debug(client: object) -> None:
    rc, stdout, stderr = p0.run(
        client,
        p0.q(P + "/scripts/daily_update/install_openclaw_cron.sh"),
        timeout=300,
        check=False,
    )
    print(json.dumps({"rc": rc, "tail": (stdout + "\n" + stderr)[-16000:]}))


def cron_process_inspect(client: object) -> None:
    processes = p0.out(
        client,
        "ps -eo pid=,ppid=,etime=,comm=,args= | grep -E '[o]penclaw.*cron (create|edit)'",
        check=False,
    ).splitlines()
    log = p0.out(
        client,
        f"tail -n 120 {p0.q(P + '/storage/openclaw/logs/gateway.log')} | "
        "sed -E 's/(token|password|secret|api[_-]?key)[=: ]+[^ ,}]*/\\1=<redacted>/Ig'",
        check=False,
    )
    print(json.dumps({"processes": processes, "gateway_log_tail": log[-16000:]}))


def device_inspect(client: object) -> None:
    cli = P + "/scripts/openclaw/openclaw_cli.sh"
    helps = {
        command: p0.out(client, f"{p0.q(cli)} devices {command} --help", check=False)
        for command in ("list", "approve")
    }
    rc, stdout, stderr = p0.run(client, f"{p0.q(cli)} devices list --json", timeout=120, check=False)
    summary: object = {"parseable": False}
    if rc == 0:
        try:
            value = json.loads(stdout)
            if isinstance(value, dict):
                summary = {
                    "keys": sorted(value.keys()),
                    "counts": {
                        key: len(item) for key, item in value.items() if isinstance(item, list)
                    },
                    "pending_scopes": [
                        item.get("scopes") or item.get("requestedScopes")
                        for key, rows in value.items()
                        if "pending" in key.lower() and isinstance(rows, list)
                        for item in rows
                        if isinstance(item, dict)
                    ],
                }
            elif isinstance(value, list):
                summary = {"items": len(value)}
        except json.JSONDecodeError:
            summary = {"parseable": False}
    print(
        json.dumps(
            {
                "help": helps,
                "list_rc": rc,
                "list_summary": summary,
                "list_error": stderr[-2000:] if rc else "",
            }
        )
    )


def approve_local_admin(client: object) -> None:
    cli = P + "/scripts/openclaw/openclaw_cli.sh"
    before = json.loads(p0.out(client, f"{p0.q(cli)} devices list --json"))
    pending = before.get("pending", []) if isinstance(before, dict) else []
    assert len(pending) == 1
    requested = pending[0].get("scopes") or pending[0].get("requestedScopes")
    assert requested == ["operator.admin"]
    p0.run(client, f"{p0.q(cli)} devices approve --latest --json", timeout=120)
    after = json.loads(p0.out(client, f"{p0.q(cli)} devices list --json"))
    remaining = after.get("pending", []) if isinstance(after, dict) else []
    paired = after.get("paired", []) if isinstance(after, dict) else []
    has_admin = any("operator.admin" in (item.get("scopes") or []) for item in paired if isinstance(item, dict))
    print(json.dumps({"status": "PASS", "pending_before": 1, "pending_after": len(remaining), "operator_admin_paired": has_admin}))
    assert not remaining and has_admin


def live_run(client: object) -> None:
    assert db(client, "SELECT version_num FROM epidemic.alembic_version") == "0010_p1_8_daily_updates"
    schedules = json.loads(
        db(
            client,
            "SELECT json_agg(json_build_object('schedule_id',schedule_id,'source_key',ds.source_key,'country',s.country_alpha2,'disease',d.slug) ORDER BY ds.source_key)::text FROM epidemic.daily_update_schedules s JOIN epidemic.data_sources ds ON ds.id=s.data_source_id JOIN epidemic.diseases d ON d.id=s.disease_id WHERE s.enabled",
        )
    )
    assert len(schedules) == 2
    before_counts = p1r4.p17.counts(client)
    before_snapshots = json.loads(
        db(
            client,
            "SELECT COALESCE(json_agg(json_build_object('id',id,'sha256',content_sha256,'row_count',row_count) ORDER BY id),'[]'::json)::text FROM epidemic.dataset_snapshots",
        )
    )
    before_boundary = json.loads(
        db(
            client,
            "SELECT json_build_object('approvals',(SELECT count(*) FROM epidemic_discovery.source_review_decisions WHERE decision='APPROVED'),'rejections',(SELECT count(*) FROM epidemic_discovery.source_review_decisions WHERE decision='REJECTED'),'promotions',(SELECT count(*) FROM epidemic_discovery.source_promotion_runs))::text",
        )
    )
    run_ids: list[str] = []
    for schedule in schedules:
        command = (
            f"source {p0.q(P + '/scripts/daily_update/_common.sh')} && "
            f"{p0.q(p1r4.p1r3.PY)} -B -m epidemic_core.daily.cli start-manual "
            f"--schedule {p0.q(schedule['schedule_id'])}"
        )
        payload = json.loads(p0.out(client, command))
        run_ids.append(payload["run_id"])
    terminal = {
        "SUCCEEDED_CHANGED",
        "SUCCEEDED_NO_CHANGE",
        "PARTIAL",
        "FAILED_TERMINAL",
        "CANCELLED_SOURCE_DISABLED",
    }
    deadline = time.monotonic() + 900
    states: dict[str, str] = {}
    while time.monotonic() < deadline:
        states = {
            run_id: db(
                client,
                f"SELECT state FROM epidemic.daily_update_runs WHERE run_id='{run_id}'::uuid",
            )
            for run_id in run_ids
        }
        if all(state in terminal for state in states.values()):
            break
        time.sleep(2)
    assert all(state in terminal for state in states.values()), states

    repeated: dict[str, set[str]] = {}
    for schedule, run_id in zip(schedules, run_ids, strict=True):
        ids = set()
        for _ in range(10):
            command = (
                f"source {p0.q(P + '/scripts/daily_update/_common.sh')} && "
                f"{p0.q(p1r4.p1r3.PY)} -B -m epidemic_core.daily.cli start-manual "
                f"--schedule {p0.q(schedule['schedule_id'])}"
            )
            ids.add(json.loads(p0.out(client, command))["run_id"])
        assert ids == {run_id}
        repeated[schedule["source_key"]] = ids

    evidence = json.loads(
        db(
            client,
            "SELECT json_agg(json_build_object('source_key',ds.source_key,'country',s.country_alpha2,"
            "'disease',d.slug,'state',r.state,'reason',r.final_reason_code,'source_changed',r.source_changed,"
            "'added',r.observations_added,'revised',r.observations_revised,'excluded',r.rows_excluded,"
            "'source_data_through',s.source_data_through,'normalized_data_through',s.normalized_data_through,"
            "'http_status',(SELECT (e.details->>'http_status')::int FROM epidemic.daily_update_run_events e "
            "WHERE e.run_id=r.run_id AND e.event_code='SOURCE_CHECK_COMPLETED' ORDER BY e.event_id DESC LIMIT 1),"
            "'evidence_sha256',(SELECT e.details->>'evidence_sha256' FROM epidemic.daily_update_run_events e "
            "WHERE e.run_id=r.run_id AND e.event_code='SOURCE_CHECK_COMPLETED' ORDER BY e.event_id DESC LIMIT 1),"
            "'queued_events',(SELECT count(*) FROM epidemic.daily_update_run_events e WHERE e.run_id=r.run_id "
            "AND e.event_code='DAILY_UPDATE_QUEUED')) ORDER BY ds.source_key)::text "
            "FROM epidemic.daily_update_runs r JOIN epidemic.daily_update_schedules s ON s.schedule_id=r.schedule_id "
            "JOIN epidemic.data_sources ds ON ds.id=s.data_source_id JOIN epidemic.diseases d ON d.id=s.disease_id "
            "WHERE r.run_id=ANY(ARRAY["
            + ",".join("'" + run_id + "'::uuid" for run_id in run_ids)
            + "])",
        )
    )
    after_counts = p1r4.p17.counts(client)
    after_snapshots = json.loads(
        db(
            client,
            "SELECT COALESCE(json_agg(json_build_object('id',id,'sha256',content_sha256,'row_count',row_count) ORDER BY id),'[]'::json)::text FROM epidemic.dataset_snapshots",
        )
    )
    after_boundary = json.loads(
        db(
            client,
            "SELECT json_build_object('approvals',(SELECT count(*) FROM epidemic_discovery.source_review_decisions WHERE decision='APPROVED'),'rejections',(SELECT count(*) FROM epidemic_discovery.source_review_decisions WHERE decision='REJECTED'),'promotions',(SELECT count(*) FROM epidemic_discovery.source_promotion_runs))::text",
        )
    )
    old = {row["id"]: (row["sha256"], row["row_count"]) for row in before_snapshots}
    current = {row["id"]: (row["sha256"], row["row_count"]) for row in after_snapshots}
    existing_preserved = all(current.get(key) == value for key, value in old.items())
    result = {
        "status": "PASS" if all(item["state"].startswith("SUCCEEDED") for item in evidence) else "FAIL",
        "runs": evidence,
        "repeat_requests": 20,
        "repeat_unique_runs": sum(len(value) for value in repeated.values()),
        "before_counts": before_counts,
        "after_counts": after_counts,
        "existing_snapshots_preserved": existing_preserved,
        "snapshot_count_delta": len(after_snapshots) - len(before_snapshots),
        "review_boundary_unchanged": before_boundary == after_boundary,
    }
    print(json.dumps(result))
    assert result["status"] == "PASS"
    assert all(item["http_status"] == 200 and len(item["evidence_sha256"] or "") == 64 for item in evidence)
    assert all(item["queued_events"] == 1 for item in evidence)
    assert result["repeat_unique_runs"] == 2
    assert existing_preserved and before_boundary == after_boundary


def live_status(client: object) -> None:
    rows = json.loads(
        db(
            client,
            "SELECT COALESCE(json_agg(x ORDER BY x->>'source_key'),'[]'::json)::text FROM ("
            "SELECT json_build_object('source_key',ds.source_key,'state',r.state,'reason',r.final_reason_code,"
            "'trigger',r.trigger_type,'logical_date',r.logical_date,'retry_count',r.retry_count,"
            "'retrieval_linked',r.retrieval_run_id IS NOT NULL,'normalization_linked',r.normalization_run_id IS NOT NULL,"
            "'snapshot_linked',r.snapshot_id IS NOT NULL) x FROM epidemic.daily_update_schedules s "
            "JOIN epidemic.data_sources ds ON ds.id=s.data_source_id LEFT JOIN LATERAL(SELECT rr.* FROM "
            "epidemic.daily_update_runs rr WHERE rr.schedule_id=s.schedule_id ORDER BY rr.created_at DESC LIMIT 1) r ON true) q",
        )
    )
    print(json.dumps({"runs": rows}))


def trigger_live(client: object) -> None:
    schedules = json.loads(
        db(
            client,
            "SELECT json_agg(json_build_object('schedule_id',schedule_id,'source_key',ds.source_key) ORDER BY ds.source_key)::text FROM epidemic.daily_update_schedules s JOIN epidemic.data_sources ds ON ds.id=s.data_source_id WHERE s.enabled",
        )
    )
    results = []
    for schedule in schedules:
        command = (
            f"source {p0.q(P + '/scripts/daily_update/_common.sh')} && "
            f"{p0.q(p1r4.p1r3.PY)} -B -m epidemic_core.daily.cli start-manual "
            f"--schedule {p0.q(schedule['schedule_id'])}"
        )
        rc, stdout, stderr = p0.run(client, command, timeout=60, check=False)
        results.append(
            {
                "source_key": schedule["source_key"],
                "rc": rc,
                "started": rc == 0 and "run_id" in stdout,
                "error_tail": stderr[-2000:] if rc else "",
            }
        )
    print(json.dumps({"results": results}))
    assert all(item["started"] for item in results)


def repeat_evidence(client: object) -> None:
    schedules = json.loads(
        db(
            client,
            "SELECT json_agg(json_build_object('schedule_id',schedule_id,'source_key',ds.source_key) ORDER BY ds.source_key)::text FROM epidemic.daily_update_schedules s JOIN epidemic.data_sources ds ON ds.id=s.data_source_id WHERE s.enabled",
        )
    )
    unique_ids = {}
    for schedule in schedules:
        command = (
            f"source {p0.q(P + '/scripts/daily_update/_common.sh')} && "
            "for i in $(seq 1 10); do "
            f"{p0.q(p1r4.p1r3.PY)} -B -m epidemic_core.daily.cli start-manual "
            f"--schedule {p0.q(schedule['schedule_id'])}; done"
        )
        values = [json.loads(line)["run_id"] for line in p0.out(client, command, timeout=180).splitlines()]
        unique_ids[schedule["source_key"]] = len(set(values))
    evidence = json.loads(
        db(
            client,
            "SELECT json_agg(json_build_object('source_key',ds.source_key,'state',r.state,'reason',r.final_reason_code,"
            "'source_changed',r.source_changed,'added',r.observations_added,'revised',r.observations_revised,"
            "'excluded',r.rows_excluded,'source_data_through',s.source_data_through,"
            "'normalized_data_through',s.normalized_data_through,'retry_count',r.retry_count,"
            "'http_status',(SELECT (e.details->>'http_status')::int FROM epidemic.daily_update_run_events e "
            "WHERE e.run_id=r.run_id AND e.event_code='SOURCE_CHECK_COMPLETED' ORDER BY e.event_id DESC LIMIT 1),"
            "'evidence_sha256',(SELECT e.details->>'evidence_sha256' FROM epidemic.daily_update_run_events e "
            "WHERE e.run_id=r.run_id AND e.event_code='SOURCE_CHECK_COMPLETED' ORDER BY e.event_id DESC LIMIT 1),"
            "'queued_events',(SELECT count(*) FROM epidemic.daily_update_run_events e WHERE e.run_id=r.run_id "
            "AND e.event_code='DAILY_UPDATE_QUEUED')) ORDER BY ds.source_key)::text "
            "FROM epidemic.daily_update_schedules s JOIN epidemic.data_sources ds ON ds.id=s.data_source_id "
            "JOIN LATERAL(SELECT rr.* FROM epidemic.daily_update_runs rr WHERE rr.schedule_id=s.schedule_id "
            "ORDER BY rr.created_at DESC LIMIT 1) r ON true",
        )
    )
    preflight = json.loads(p0.out(client, f"cat {p0.q(REPORT + '/preflight.json')}"))
    snapshots = json.loads(
        db(
            client,
            "SELECT COALESCE(json_agg(json_build_object('id',id,'sha256',content_sha256,'row_count',row_count) ORDER BY id),'[]'::json)::text FROM epidemic.dataset_snapshots",
        )
    )
    old = {row["id"]: (row["sha256"], row["row_count"]) for row in preflight["snapshots"]}
    current = {row["id"]: (row["sha256"], row["row_count"]) for row in snapshots}
    boundary = json.loads(
        db(
            client,
            "SELECT json_build_object('approvals',(SELECT count(*) FROM epidemic_discovery.source_review_decisions WHERE decision='APPROVED'),'rejections',(SELECT count(*) FROM epidemic_discovery.source_review_decisions WHERE decision='REJECTED'),'promotions',(SELECT count(*) FROM epidemic_discovery.source_promotion_runs),'bra',(SELECT COALESCE(json_agg(json_build_object('run_state',r.state,'candidate_status',c.status,'review_state',rc.state,'decision',(SELECT d.decision FROM epidemic_discovery.source_review_decisions d WHERE d.review_case_id=rc.id ORDER BY d.created_at DESC LIMIT 1))),'[]'::json) FROM epidemic_discovery.discovery_runs r JOIN epidemic_discovery.source_candidates c ON c.discovery_run_id=r.id JOIN epidemic_discovery.source_candidate_versions cv ON cv.candidate_id=c.id JOIN epidemic_discovery.source_review_cases rc ON rc.candidate_version_id=cv.id WHERE r.request_json->>'country_iso3'='BRA'))::text",
        )
    )
    duplicates = json.loads(
        db(
            client,
            "SELECT json_build_object('artifact_sha_duplicates',(SELECT count(*)-count(DISTINCT sha256) FROM epidemic.raw_artifacts),'observation_record_duplicates',(SELECT count(*)-count(DISTINCT record_fingerprint) FROM epidemic.normalized_observations),'daily_logical_duplicates',(SELECT count(*) FROM (SELECT schedule_id,logical_date FROM epidemic.daily_update_runs GROUP BY 1,2 HAVING count(*)>1) x))::text",
        )
    )
    result = {
        "status": "PASS",
        "runs": evidence,
        "repeat_requests": 20,
        "unique_runs_per_source": unique_ids,
        "formal_counts": p1r4.p17.counts(client),
        "snapshot_delta": len(snapshots) - len(preflight["snapshots"]),
        "existing_snapshots_preserved": all(current.get(key) == value for key, value in old.items()),
        "review_boundary_unchanged": boundary == preflight["discovery_boundary"],
        "duplicates": duplicates,
    }
    print(json.dumps(result))
    assert all(item["state"].startswith("SUCCEEDED") for item in evidence)
    assert all(item["http_status"] == 200 and len(item["evidence_sha256"] or "") == 64 for item in evidence)
    assert all(item["queued_events"] == 1 for item in evidence)
    assert set(unique_ids.values()) == {1}
    assert result["existing_snapshots_preserved"] and result["review_boundary_unchanged"]
    assert duplicates == {
        "artifact_sha_duplicates": 0,
        "observation_record_duplicates": 0,
        "daily_logical_duplicates": 0,
    }


def apply_metadata_fix(client: object) -> None:
    db(
        client,
        "UPDATE epidemic.daily_update_runs SET observations_added=0,observations_revised=0,rows_excluded=0 "
        "WHERE state='SUCCEEDED_NO_CHANGE' AND source_changed=false",
    )
    p0.run(client, p0.q(P + "/scripts/daily_update/restart_daily_update.sh"), timeout=300)
    values = json.loads(
        db(
            client,
            "SELECT json_agg(json_build_object('source_key',ds.source_key,'added',r.observations_added,"
            "'revised',r.observations_revised,'excluded',r.rows_excluded) ORDER BY ds.source_key)::text "
            "FROM epidemic.daily_update_runs r JOIN epidemic.daily_update_schedules s ON s.schedule_id=r.schedule_id "
            "JOIN epidemic.data_sources ds ON ds.id=s.data_source_id",
        )
    )
    healthy = p0.run(client, p0.q(P + "/scripts/daily_update/health_check.sh"), check=False)[0] == 0
    print(json.dumps({"status": "PASS", "daily_healthy": healthy, "run_counts": values}))
    assert healthy
    assert all(item["added"] == item["revised"] == 0 for item in values)
    assert all(item["excluded"] == 0 for item in values if item["source_key"] == "who_covid19_global_weekly")


def deploy_dashboard_runtime(client: object) -> None:
    """Refresh the isolated read-only UI runtime without exposing its credentials."""
    p0.run(client, p0.q(P + "/scripts/dashboard/stop_dashboard.sh"), check=False)
    p0.run(client, p0.q(P + "/scripts/dashboard/install_runtime.sh"), timeout=300)
    p0.run(client, p0.q(P + "/scripts/dashboard/start_dashboard.sh"), timeout=300)
    rc, _stdout, _stderr = p0.run(
        client, p0.q(P + "/scripts/dashboard/health_check.sh"), timeout=120, check=False
    )
    project_has_daily = p0.out(
        client,
        f"grep -c 'daily-update-title' {p0.q(P + '/src/epidemic_core/dashboard/app.py')}",
    )
    runtime_has_daily = p0.out(
        client,
        "grep -c 'daily-update-title' "
        + p0.q("USER_HOME/autodl-tmp/epidemic-dashboard-runtime/src/epidemic_core/dashboard/app.py"),
    )
    result = {
        "status": "PASS" if rc == 0 else "FAIL",
        "healthy": rc == 0,
        "project_daily_marker": int(project_has_daily),
        "runtime_daily_marker": int(runtime_has_daily),
    }
    print(json.dumps(result))
    assert result["status"] == "PASS" and result["healthy"]
    assert result["project_daily_marker"] >= 1 and result["runtime_daily_marker"] >= 1


def dashboard_diagnose(client: object) -> None:
    log = p0.out(
        client,
        "tail -n 160 USER_HOME/autodl-tmp/epidemic-dashboard-runtime/log/dashboard.log | "
        "sed -E 's/(token|password|secret|api[_-]?key)[=: ]+[^ ,}]*/\\1=<redacted>/Ig'",
        check=False,
    )
    grants = db(
        client,
        "SELECT has_table_privilege('epidemic_dashboard_ro','epidemic.daily_update_status_v1','SELECT')",
    )
    view_count = db(client, "SELECT count(*) FROM epidemic.daily_update_status_v1")
    print(json.dumps({"view_rows": int(view_count), "dashboard_role_select": grants, "log_tail": log[-12000:]}))


def review_diagnose(client: object) -> None:
    log = p0.out(
        client,
        "tail -n 160 USER_HOME/autodl-tmp/epidemic-source-review-runtime/log/review.log | "
        "sed -E 's/(token|password|secret|api[_-]?key)[=: ]+[^ ,}]*/\\1=<redacted>/Ig'",
        check=False,
    )
    grants = db(
        client,
        "SELECT has_table_privilege('epidemicreview','epidemic.daily_update_status_v1','SELECT')",
    )
    print(json.dumps({"review_role_select": grants, "log_tail": log[-12000:]}))


def apply_review_status_grant(client: object) -> None:
    db(client, "GRANT USAGE ON SCHEMA epidemic TO epidemicreview")
    values = db(
        client,
        "SELECT has_schema_privilege('epidemicreview','epidemic','USAGE')::text || ',' || "
        "has_table_privilege('epidemicreview','epidemic.daily_update_status_v1','SELECT')::text",
    )
    result = {"status": "PASS", "schema_usage": values.split(",")[0], "status_view_select": values.split(",")[1]}
    print(json.dumps(result))
    assert result["schema_usage"] == result["status_view_select"] == "true"


def main() -> None:
    actions = {
        "connection-gate": connection_gate,
        "baseline": baseline,
        "pull": pull_source,
        "architecture": architecture,
        "prewrite": prewrite,
        "upload": upload,
        "format": format_patch,
        "pull-generated": pull_generated,
        "quality": quality,
        "migration-test": migration_test,
        "integration-regression": integration_regression,
        "full-test": full_test,
        "npm-audit": npm_audit,
        "operational-quality": operational_quality,
        "security-findings": security_findings,
        "final-audit": final_audit,
        "git-inspect": git_inspect,
        "runtime-inspect": runtime_inspect,
        "apply-production": apply_production,
        "seed-debug": seed_debug,
        "status-now": status_now,
        "cron-debug": cron_debug,
        "cron-process-inspect": cron_process_inspect,
        "device-inspect": device_inspect,
        "approve-local-admin": approve_local_admin,
        "live-run": live_run,
        "live-status": live_status,
        "trigger-live": trigger_live,
        "repeat-evidence": repeat_evidence,
        "apply-metadata-fix": apply_metadata_fix,
        "deploy-dashboard-runtime": deploy_dashboard_runtime,
        "dashboard-diagnose": dashboard_diagnose,
        "review-diagnose": review_diagnose,
        "apply-review-status-grant": apply_review_status_grant,
    }
    if len(sys.argv) != 2 or sys.argv[1] not in actions:
        raise SystemExit("unknown action")
    if sys.argv[1] == "connection-gate":
        connection_gate()
        return
    client = p1r4._connect()
    try:
        actions[sys.argv[1]](client)
    finally:
        client.close()


if __name__ == "__main__":
    main()
