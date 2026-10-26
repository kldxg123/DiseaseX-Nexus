from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import ipaddress
import json
import os
import re
import secrets
import shlex
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import paramiko

import migrate_epidemic_to_a100 as migration


ROOT = Path(r"F:\作业\aibola")
EXPECTED_FINGERPRINT = "SHA256:fgvvJdy9VYM9u1pGWaoPhj1c5IT6ANkFyhYu0j+kWZU"
EXPECTED_HEAD = "f2863fccef26d0628799e52e14fea820cc027a26"
P1_8A_COMMIT = "c7f380e15aaf04862128b35d96850d760929bec7"
EXPECTED_MIGRATION = "0015_p1_8b_reviewer_read_views"
EXPECTED_COUNTS = {
    "data_sources": 2,
    "query_requests": 4,
    "retrieval_runs": 8,
    "source_fetches": 8,
    "raw_artifacts": 3,
    "normalized_observations": 349,
    "quality_assessments": 349,
    "dataset_snapshots": 7,
    "dataset_snapshot_observations": 803,
}
EXPECTED_SNAPSHOTS = {
    "201d8c174181c5362bc3ff0a485fc2bb5856bc19d09ae9c98016529a3562c3ca": 174,
    "26ac6c9078678c7d6d3a8446b1860001afe6db425c1b9195bccc4d08ee94ad11": 35,
    "89a97c89ec72c0a3475d1be3b720643aa5ce0ca59c72f0b42feee6c1c3cb08ad": 106,
    "157708c70589ec3036512cc56dfee2637418bad65bd056a70e06a9265b1cd516": 34,
    "d3d259642dad7b08dc76210e936be04c009c1f556e3c6b109595b76191ab2359": 106,
    "af4f4b2de64362812ef515ccb5c9d8a795f83cf7696cccc7b9bb6bc7675d5b54": 174,
    "ad8a2b7c10dff2bcebf3f7fbc5fce0ed702e1d14892935b348d7bdf787967c8b": 174,
}
P1_8_FILES = (
    "config/daily_update.json",
    "migrations/versions/0010_p1_8_daily_updates.py",
    "src/epidemic_core/db/models/tables.py",
    "src/epidemic_core/retrieval/service.py",
    "src/epidemic_core/reference.py",
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
P1_8B_SYNC_FILES = (
    "config/source_discovery/directories.v1.json",
    "migrations/versions/0011_p1_8b_verified_discovery.py",
    "migrations/versions/0012_p1_8b_discovery_submit_idempotency.py",
    "migrations/versions/0013_p1_8b_discovery_submit_receipt.py",
    "migrations/versions/0014_p1_8b_review_ready_cards.py",
    "migrations/versions/0015_p1_8b_reviewer_read_views.py",
    "src/epidemic_core/discovery/providers.py",
    "src/epidemic_core/discovery/safe_fetcher.py",
    "src/epidemic_core/discovery/extractor.py",
    "src/epidemic_core/discovery/service.py",
    "src/epidemic_core/discovery/candidate_pipeline.py",
    "src/epidemic_core/discovery/worker.py",
    "src/epidemic_core/discovery/review_app.py",
    "src/epidemic_core/discovery/static/review.p1-7ar3-v1.css",
    "scripts/source_discovery_worker/_common.sh",
    "scripts/source_discovery_worker/install_extractor_runtime.sh",
    "scripts/source_discovery_worker/start_source_discovery_worker.sh",
    "scripts/source_discovery_worker/status_source_discovery_worker.sh",
    "scripts/source_discovery_worker/stop_source_discovery_worker.sh",
    "scripts/source_review/_common.sh",
    "scripts/source_review/configure_database_access.py",
    "scripts/source_review/install_runtime.sh",
    "scripts/source_review/start_source_review.sh",
    "scripts/source_review/status_source_review.sh",
    "scripts/source_review/stop_source_review.sh",
    "tests/unit/test_discovery_providers.py",
    "tests/unit/test_keyfree_discovery.py",
    "tests/integration/test_discovery_queue_contract.py",
    "tests/unit/test_p1_7a_ui_repair.py",
    "tests/unit/test_dashboard.py",
)


class GateError(RuntimeError):
    pass


def q(value: str) -> str:
    return shlex.quote(value)


def connect() -> tuple[paramiko.SSHClient, dict[str, object]]:
    credentials = migration.credentials(migration.TARGET_INFO)
    client, gate = migration.connect_with_gate("target", credentials)
    if gate["fingerprint"] != EXPECTED_FINGERPRINT:
        client.close()
        raise GateError("A100_HOST_KEY_MISMATCH")
    return client, gate


def run(
    client: paramiko.SSHClient,
    command: str,
    *,
    timeout: int = 300,
    input_text: str | None = None,
) -> tuple[int, str, str]:
    return migration.run(client, command, timeout=timeout, input_text=input_text)


def checked(
    client: paramiko.SSHClient,
    command: str,
    *,
    timeout: int = 300,
    input_text: str | None = None,
) -> str:
    return migration.checked(client, command, timeout=timeout, input_text=input_text)


def paths(client: paramiko.SSHClient) -> tuple[str, str]:
    home = checked(client, "printf '%s' \"$HOME\"")
    if not home.startswith("/") or home == "/":
        raise GateError("A100_HOME_INVALID")
    root = home.rstrip("/") + "/autodl-tmp"
    return root, root + "/epidemic-openclaw"


def db_query(client: paramiko.SSHClient, statement: str) -> str:
    root, project = paths(client)
    python = root + "/conda/envs/epidemic-core/bin/python"
    env_script = project + "/scripts/database/_env.sh"
    code = r'''import os,sys,sqlalchemy as sa
engine=sa.create_engine(os.environ["MIGRATION_DATABASE_URL"],pool_pre_ping=True)
with engine.connect() as connection:value=connection.execute(sa.text(sys.stdin.read())).scalar_one()
print(value)
'''
    command = (
        f"bash -c 'source \"$1\"; exec \"$2\" -c \"$3\"' sh "
        f"{q(env_script)} {q(python)} {q(code)}"
    )
    rc, output, error = run(client, command, timeout=300, input_text=statement)
    if rc:
        sanitized = re.sub(r"(?i)(password|token|secret|api[_-]?key)=\S+", r"\1=[redacted]", error)
        raise GateError(f"DATABASE_READ_FAILED:{rc}:{sanitized[-1200:]}")
    return output.strip()


def sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def preflight_action() -> None:
    client, gate = connect()
    try:
        root, project = paths(client)
        gpu_rows = checked(
            client,
            "nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader,nounits",
        ).splitlines()
        if not gpu_rows or not all("A100" in row for row in gpu_rows):
            raise GateError("BLOCKED_WRONG_TARGET_OR_MIGRATION_DRIFT")
        required = {
            "project": project,
            "core_python": root + "/conda/envs/epidemic-core/bin/python",
            "postgres": root + "/conda/envs/postgresql18/bin/postgres",
            "qwen_python": root + "/conda/envs/qwen-vllm/bin/python",
            "postgres_data": project + "/storage/database/postgresql18/data",
            "model": project + "/models/llm/Qwen3-8B-AWQ",
        }
        missing = [name for name, path in required.items() if run(client, f"test -e {q(path)}")[0] != 0]
        if missing:
            raise GateError("A100_REQUIRED_PATHS_MISSING:" + ",".join(missing))
        service_paths = {
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
        }
        services = {
            name: run(client, q(project + "/" + relative), timeout=180)[0] == 0
            for name, relative in service_paths.items()
        }
        listener_code = r'''import json,pathlib
ports={5432,8000,8010,18789,8020,8021};rows=[]
for filename,v6 in (("/proc/net/tcp",False),("/proc/net/tcp6",True)):
 for line in pathlib.Path(filename).read_text().splitlines()[1:]:
  parts=line.split();address,port_hex=parts[1].split(":");port=int(port_hex,16)
  if port in ports and parts[3]=="0A":rows.append({"port":port,"loopback":address==("00000000000000000000000001000000" if v6 else "0100007F")})
print(json.dumps(rows))
'''
        core_python = required["core_python"]
        listeners = json.loads(checked(client, f"{q(core_python)} -c {q(listener_code)}"))
        counts_sql = "SELECT json_build_object(" + ",".join(
            f"'{name}',(SELECT count(*) FROM epidemic.{name})" for name in EXPECTED_COUNTS
        ) + ")::text"
        counts = json.loads(db_query(client, counts_sql))
        snapshots = json.loads(
            db_query(
                client,
                "SELECT COALESCE(json_agg(json_build_object('id',id,'sha256',content_sha256,'row_count',row_count) ORDER BY id),'[]'::json)::text FROM epidemic.dataset_snapshots",
            )
        )
        snapshot_map = {row["sha256"]: row["row_count"] for row in snapshots}
        migration_version = db_query(client, "SELECT version_num FROM epidemic.alembic_version")
        git_head = checked(client, f"cd {q(project)} && git rev-parse HEAD")
        branch = checked(client, f"cd {q(project)} && git branch --show-current")
        porcelain = checked(client, f"cd {q(project)} && git status --porcelain=v2 -uall", timeout=300)
        changed = checked(
            client,
            f"cd {q(project)} && git status --porcelain=v1 -uall | sed -E 's/^...//'",
            timeout=300,
        ).splitlines()
        staged = checked(client, f"cd {q(project)} && git diff --cached --binary", timeout=600)
        unstaged = checked(client, f"cd {q(project)} && git diff --binary", timeout=600)
        untracked_code = r'''import hashlib,json,os,pathlib,subprocess
project=pathlib.Path(os.environ["PROJECT"])
raw=subprocess.run(["git","-C",str(project),"ls-files","--others","--exclude-standard","-z"],capture_output=True,check=True).stdout
rows=[]
for value in raw.split(b"\0"):
 if not value:continue
 rel=value.decode("utf-8","surrogateescape");path=project/rel
 if path.is_file():
  h=hashlib.sha256()
  with path.open("rb") as handle:
   for chunk in iter(lambda:handle.read(1024*1024),b""):h.update(chunk)
  rows.append({"path":rel,"bytes":path.stat().st_size,"sha256":h.hexdigest()})
 elif path.is_symlink():rows.append({"path":rel,"symlink":os.readlink(path)})
print(json.dumps(rows))
'''
        untracked = json.loads(
            checked(client, f"PROJECT={q(project)} {q(core_python)} -c {q(untracked_code)}", timeout=1800)
        )
        openclaw_config = project + "/storage/openclaw/state/openclaw.json"
        config_sha = checked(client, f"sha256sum {q(openclaw_config)} | awk '{{print $1}}'")
        proxy_state = checked(
            client,
            "bash -lc '[[ -z ${HTTP_PROXY:-}${HTTPS_PROXY:-}${ALL_PROXY:-}${http_proxy:-}${https_proxy:-}${all_proxy:-} ]] && echo absent || echo present'",
        )
        gates = {
            "a100_gpu": True,
            "host_key_match": gate["fingerprint"] == EXPECTED_FINGERPRINT,
            "stable_second_connection": bool(gate["stable_second_connection"]),
            "paths_present": not missing,
            "all_services_healthy": all(services.values()),
            "ports_exact": {row["port"] for row in listeners} == {5432, 8000, 8010, 18789, 8020, 8021},
            "loopback_only": all(row["loopback"] for row in listeners),
            "head_match": git_head == EXPECTED_HEAD,
            "migration_match": migration_version == EXPECTED_MIGRATION,
            "counts_match": counts == EXPECTED_COUNTS,
            "snapshots_match": snapshot_map == EXPECTED_SNAPSHOTS,
            "proxy_absent": proxy_state == "absent",
        }
        if not all(gates.values()):
            raise GateError("BLOCKED_WRONG_TARGET_OR_MIGRATION_DRIFT:" + json.dumps(gates, sort_keys=True))
        p1_8_present = [path for path in P1_8_FILES if path in changed]
        result = {
            "target_gate": {"key_type": gate["key_type"], "stable_second_connection": True},
            "gates": gates,
            "gpu_count": len(gpu_rows),
            "services": services,
            "listeners": listeners,
            "git": {
                "head": git_head,
                "branch": branch,
                "porcelain_v2_sha256": sha_text(porcelain),
                "porcelain_v2_lines": len(porcelain.splitlines()),
                "staged_diff_sha256": sha_text(staged),
                "staged_diff_bytes": len(staged.encode()),
                "unstaged_diff_sha256": sha_text(unstaged),
                "unstaged_diff_bytes": len(unstaged.encode()),
                "changed_paths": changed,
                "untracked": untracked,
                "p1_8_changed_files": p1_8_present,
            },
            "alembic": migration_version,
            "openclaw_config_sha256": config_sha,
            "formal_counts": counts,
            "snapshots": snapshots,
            "no_source_server_connection": True,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        client.close()


def backup_preflight_action() -> None:
    client, gate = connect()
    try:
        root, project = paths(client)
        if checked(client, f"cd {q(project)} && git rev-parse HEAD") != EXPECTED_HEAD:
            raise GateError("BLOCKED_WRONG_TARGET_OR_MIGRATION_DRIFT")
        counts_sql = "SELECT json_build_object(" + ",".join(
            f"'{name}',(SELECT count(*) FROM epidemic.{name})" for name in EXPECTED_COUNTS
        ) + ")::text"
        counts = json.loads(db_query(client, counts_sql))
        snapshots = json.loads(
            db_query(
                client,
                "SELECT COALESCE(json_agg(json_build_object('id',id,'sha256',content_sha256,'row_count',row_count) ORDER BY id),'[]'::json)::text FROM epidemic.dataset_snapshots",
            )
        )
        if counts != EXPECTED_COUNTS or {row["sha256"]: row["row_count"] for row in snapshots} != EXPECTED_SNAPSHOTS:
            raise GateError("BLOCKED_WRONG_TARGET_OR_MIGRATION_DRIFT")
        timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        backup = project + "/storage/tmp/p1_8ab_preflight_" + timestamp
        checked(client, f"umask 077; mkdir -p {q(backup)}; chmod 700 {q(backup)}")
        git_files = {
            "git_status_porcelain_v2.txt": f"cd {q(project)} && git status --porcelain=v2 -uall",
            "staged.diff": f"cd {q(project)} && git diff --cached --binary",
            "unstaged.diff": f"cd {q(project)} && git diff --binary",
            "changed_paths.txt": f"cd {q(project)} && git status --porcelain=v1 -uall | sed -E 's/^...//'",
        }
        payloads: dict[str, str] = {}
        for name, command in git_files.items():
            payloads[name] = checked(client, command, timeout=900) + "\n"
        core_python = root + "/conda/envs/epidemic-core/bin/python"
        untracked_code = r'''import hashlib,json,os,pathlib,subprocess
project=pathlib.Path(os.environ["PROJECT"]);raw=subprocess.run(["git","-C",str(project),"ls-files","--others","--exclude-standard","-z"],capture_output=True,check=True).stdout;rows=[]
for value in raw.split(b"\0"):
 if not value:continue
 rel=value.decode("utf-8","surrogateescape");path=project/rel
 if path.is_file():
  h=hashlib.sha256()
  with path.open("rb") as handle:
   for chunk in iter(lambda:handle.read(1024*1024),b""):h.update(chunk)
  rows.append({"path":rel,"bytes":path.stat().st_size,"sha256":h.hexdigest()})
 elif path.is_symlink():rows.append({"path":rel,"symlink_sha256":hashlib.sha256(os.readlink(path).encode()).hexdigest()})
print(json.dumps(rows,indent=2))
'''
        payloads["untracked_files_sha256.json"] = checked(
            client,
            f"PROJECT={q(project)} {q(core_python)} -c {q(untracked_code)}",
            timeout=1800,
        ) + "\n"
        changed = set(payloads["changed_paths.txt"].splitlines())
        payloads["p1_8_changed_files.txt"] = "\n".join(path for path in P1_8_FILES if path in changed) + "\n"
        payloads["git_identity.json"] = json.dumps(
            {
                "head": EXPECTED_HEAD,
                "branch": checked(client, f"cd {q(project)} && git branch --show-current"),
                "host_key_type": gate["key_type"],
                "stable_second_connection": True,
                "a100_verified": True,
                "source_server_contacted": False,
            },
            indent=2,
        ) + "\n"
        payloads["alembic_head.txt"] = db_query(client, "SELECT version_num FROM epidemic.alembic_version") + "\n"
        config = project + "/storage/openclaw/state/openclaw.json"
        payloads["openclaw_config.sha256"] = checked(client, f"sha256sum {q(config)} | awk '{{print $1}}'") + "\n"
        payloads["formal_database_counts.json"] = json.dumps(counts, indent=2) + "\n"
        payloads["snapshot_inventory.json"] = json.dumps(snapshots, indent=2) + "\n"
        cli = project + "/scripts/openclaw/openclaw_cli.sh"
        cron_rc, cron_out, cron_err = run(client, f"{q(cli)} cron list --all --json", timeout=120)
        cron_value: object
        if cron_rc == 0:
            try:cron_value = json.loads(cron_out)
            except json.JSONDecodeError:cron_value = {"rc": cron_rc, "parseable": False}
        else:
            cron_value = {
                "rc": cron_rc,
                "status": "UNAVAILABLE_BEFORE_OPERATOR_ADMIN",
                "error_class": "authorization_required" if re.search(r"auth|scope|permission|pair", cron_err, re.I) else "cli_error",
            }
        payloads["cron_inventory_before.json"] = json.dumps(cron_value, indent=2) + "\n"
        sftp = client.open_sftp()
        try:
            for name, value in payloads.items():
                remote = backup + "/" + name
                with sftp.file(remote, "w") as handle:
                    handle.write(value)
                sftp.chmod(remote, 0o600)
        finally:
            sftp.close()
        manifest_lines = checked(
            client,
            f"cd {q(backup)} && sha256sum * | sort",
            timeout=300,
        )
        manifest_path = backup + "/MANIFEST.sha256"
        sftp = client.open_sftp()
        try:
            with sftp.file(manifest_path, "w") as handle:
                handle.write(manifest_lines + "\n")
            sftp.chmod(manifest_path, 0o600)
        finally:
            sftp.close()
        print(
            json.dumps(
                {
                    "target_gate": {"key_type": gate["key_type"], "stable_second_connection": True},
                    "backup": "storage/tmp/" + backup.rsplit("/", 1)[-1],
                    "file_count": len(payloads) + 1,
                    "manifest_sha256": sha_text(manifest_lines + "\n"),
                    "cron_inventory_rc": cron_rc,
                    "formal_counts_match": counts == EXPECTED_COUNTS,
                    "snapshots_match": True,
                },
                indent=2,
            )
        )
    finally:
        client.close()


def _device_summary(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {"parseable": False}
    result: dict[str, object] = {"keys": sorted(value.keys())}
    for group in ("pending", "paired", "approved", "devices"):
        rows = value.get(group)
        if not isinstance(rows, list):
            continue
        summarized = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            device_id = row.get("deviceId") or row.get("id") or row.get("requestId")
            public_key = row.get("publicKey") or row.get("public_key")
            address = row.get("remoteAddress") or row.get("remoteAddr") or row.get("ip")
            loopback = None
            if isinstance(address, str):
                candidate = address.strip("[]").split("%", 1)[0]
                if candidate.count(":") == 1 and candidate.rsplit(":", 1)[1].isdigit():
                    candidate = candidate.rsplit(":", 1)[0]
                try:loopback = ipaddress.ip_address(candidate).is_loopback
                except ValueError:loopback = address in {"localhost", "loopback"}
            summarized.append(
                {
                    "id_sha256": sha_text(str(device_id)) if device_id else None,
                    "public_key_sha256": sha_text(str(public_key)) if public_key else None,
                    "scopes": row.get("scopes") or row.get("requestedScopes") or [],
                    "role": row.get("role"),
                    "client": row.get("clientName") or row.get("client") or row.get("name"),
                    "mode": row.get("clientMode") or row.get("mode"),
                    "loopback": loopback,
                    "field_names": sorted(row.keys()),
                }
            )
        result[group] = summarized
    return result


def openclaw_device_inspect_action() -> None:
    client, gate = connect()
    try:
        root, project = paths(client)
        cli = project + "/scripts/openclaw/openclaw_cli.sh"
        version = checked(client, f"{q(cli)} --version", timeout=120)
        helps = {}
        for label, command in {
            "devices_list": f"{q(cli)} devices list --help",
            "devices_approve": f"{q(cli)} devices approve --help",
            "cron": f"{q(cli)} cron --help",
            "cron_list": f"{q(cli)} cron list --help",
            "cron_create": f"{q(cli)} cron create --help",
            "cron_edit": f"{q(cli)} cron edit --help",
            "cron_run": f"{q(cli)} cron run --help",
            "cron_runs": f"{q(cli)} cron runs --help",
        }.items():
            rc, output, error = run(client, command, timeout=120)
            helps[label] = {
                "rc": rc,
                "options": sorted(set(re.findall(r"--[a-z][a-z0-9-]*", output + "\n" + error))),
            }
        list_rc, list_out, list_err = run(client, f"{q(cli)} devices list --json", timeout=120)
        if list_rc:
            raise GateError("OPENCLAW_DEVICE_LIST_FAILED:" + re.sub(r"\s+", " ", list_err)[-180:])
        devices = json.loads(list_out)
        summary = _device_summary(devices)
        identity_code = r'''import hashlib,json,os,pathlib
root=pathlib.Path(os.environ["STATE"]);files=[];device_ids=set();public_keys=set();private_files=[]
def walk(value,path):
 if isinstance(value,dict):
  for key,item in value.items():
   lower=key.lower()
   if lower in {"deviceid","device_id"} and isinstance(item,(str,int)):device_ids.add(hashlib.sha256(str(item).encode()).hexdigest())
   if lower in {"publickey","public_key"} and isinstance(item,str):public_keys.add(hashlib.sha256(item.encode()).hexdigest())
   if "private" in lower and "key" in lower:private_files.append(path)
   walk(item,path)
 elif isinstance(value,list):
  for item in value:walk(item,path)
for path in root.rglob("*"):
 if not path.is_file() or path.stat().st_size>2_000_000:continue
 rel=str(path.relative_to(root))
 if not any(word in rel.lower() for word in ("device","identity","credential","auth")):continue
 mode=path.stat().st_mode&0o777;files.append({"path":rel,"mode":oct(mode),"mode_not_wider_than_0600":(mode&0o177)==0})
 if path.suffix==".json":
  try:walk(json.loads(path.read_text()),rel)
  except Exception:pass
print(json.dumps({"files":files,"device_id_sha256":sorted(device_ids),"public_key_sha256":sorted(public_keys),"private_key_field_file_count":len(set(private_files))}))
'''
        identity = json.loads(
            checked(
                client,
                f"STATE={q(project + '/storage/openclaw/state')} {q(root + '/conda/envs/epidemic-core/bin/python')} -c {q(identity_code)}",
                timeout=300,
            )
        )
        pending = summary.get("pending", []) if isinstance(summary, dict) else []
        if not isinstance(pending, list):pending = []
        pending_key_hashes = {row.get("public_key_sha256") for row in pending if isinstance(row, dict) and row.get("public_key_sha256")}
        pending_id_hashes = {row.get("id_sha256") for row in pending if isinstance(row, dict) and row.get("id_sha256")}
        identity_match = bool(
            pending_key_hashes.intersection(identity.get("public_key_sha256", []))
            or pending_id_hashes.intersection(identity.get("device_id_sha256", []))
        )
        log_code = r'''import json,os,pathlib,re
project=pathlib.Path(os.environ["PROJECT"]);log=project/"storage/openclaw/logs/gateway.log";lines=log.read_text(errors="replace").splitlines()[-600:] if log.is_file() else []
matching=[line for line in lines if re.search(r"device|pair|scope|operator\.admin|openclaw-cli",line,re.I)]
loopback=any(re.search(r"127\.0\.0\.1|::1|loopback",line,re.I) for line in matching)
cli=any(re.search(r"openclaw-cli|client.*cli|cli.*client",line,re.I) for line in matching)
print(json.dumps({"matching_line_count":len(matching),"loopback_evidence":loopback,"cli_evidence":cli,"latest_matching_line_sha256":__import__("hashlib").sha256(matching[-1].encode()).hexdigest() if matching else None}))
'''
        log_evidence = json.loads(
            checked(
                client,
                f"PROJECT={q(project)} {q(root + '/conda/envs/epidemic-core/bin/python')} -c {q(log_code)}",
            )
        )
        cron_rc, cron_out, cron_err = run(client, f"{q(cli)} cron list --all --json", timeout=120)
        cron_summary: dict[str, object] = {"rc": cron_rc}
        if cron_rc == 0:
            value = json.loads(cron_out)
            jobs = value if isinstance(value, list) else value.get("jobs", value.get("items", []))
            if not isinstance(jobs, list):jobs = []
            cron_summary["job_count"] = len(jobs)
            cron_summary["daily_jobs"] = [
                {
                    "id_sha256": sha_text(str(job.get("id"))),
                    "name": job.get("name"),
                    "enabled": job.get("enabled", True),
                    "owner": job.get("owner"),
                    "schedule": job.get("schedule"),
                    "payload_field_names": sorted((job.get("payload") or {}).keys()) if isinstance(job.get("payload"), dict) else [],
                }
                for job in jobs if isinstance(job, dict) and job.get("name")=="epidemic-daily-update-v1"
            ]
        else:
            cron_summary["error_class"] = "authorization_required" if re.search(r"auth|scope|permission|pair", cron_err, re.I) else "cli_error"
        credentials_ok = bool(identity.get("files")) and all(row.get("mode_not_wider_than_0600") for row in identity.get("files", []))
        print(
            json.dumps(
                {
                    "target_gate": {"key_type": gate["key_type"], "stable_second_connection": True},
                    "openclaw_version": version,
                    "help": helps,
                    "devices": summary,
                    "local_identity": identity,
                    "pending_identity_match": identity_match,
                    "gateway_log_evidence": log_evidence,
                    "credential_permissions_ok": credentials_ok,
                    "cron_before": cron_summary,
                },
                indent=2,
            )
        )
    finally:
        client.close()


def p1_8a_script_inspect_action() -> None:
    client, gate = connect()
    try:
        _root, project = paths(client)
        selected = (
            "config/daily_update.json",
            "scripts/daily_update/install_openclaw_cron.sh",
            "scripts/daily_update/reconcile_due.sh",
            "scripts/daily_update/seed_schedules.sh",
            "scripts/daily_update/start_daily_update.sh",
            "scripts/services/start_all.sh",
        )
        files = {}
        for relative in selected:
            value = checked(client, f"sed -n '1,320p' {q(project + '/' + relative)}")
            value = value.replace(project.rsplit("/autodl-tmp", 1)[0], "[target-home]")
            value = re.sub(
                r"(?i)(password|token|secret|api[_-]?key)(\s*[=:]\s*)[^\s'\"]+",
                r"\1\2[redacted]",
                value,
            )
            files[relative] = value.splitlines()
        install_text = "\n".join(files["scripts/daily_update/install_openclaw_cron.sh"])
        payload_checks = {
            "stable_name": "epidemic-daily-update-v1" in install_text,
            "cron_expression": "30 2 * * *" in install_text,
            "timezone": "Asia/Shanghai" in install_text,
            "exact": "--exact" in install_text,
            "command_argv": "--command-argv" in install_text,
            "command_cwd": "--command-cwd" in install_text,
            "no_shell_command_flag": not bool(re.search(r"(^|\s)--command(\s|$)", install_text)),
            "no_qwen": not bool(re.search(r"qwen|vllm", install_text, re.I)),
            "no_web_search": "web_search" not in install_text,
            "no_discovery_agent": not bool(re.search(r"discovery.*agent|agent.*discovery", install_text, re.I)),
            "no_user_url": not bool(re.search(r"https?://", install_text)),
            "no_secret_literal": not bool(re.search(r"password|bearer|api[_-]?key|postgresql.*@", install_text, re.I)),
            "reconcile_due_only": "reconcile-due" in install_text,
            "list_before_mutate": "cron list --all --json" in install_text,
        }
        print(
            json.dumps(
                {
                    "target_gate": {"key_type": gate["key_type"], "stable_second_connection": True},
                    "files": files,
                    "payload_checks": payload_checks,
                    "all_payload_checks": all(payload_checks.values()),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        client.close()


def _load_device_rows(client: paramiko.SSHClient, cli: str) -> dict[str, object]:
    rc, output, error = run(client, f"{q(cli)} devices list --json", timeout=120)
    if rc:
        error_class = "authorization_required" if re.search(r"auth|scope|permission|pair", error, re.I) else "cli_error"
        raise GateError(f"OPENCLAW_DEVICE_LIST_FAILED:{error_class}:{rc}")
    value = json.loads(output)
    if not isinstance(value, dict):
        raise GateError("OPENCLAW_DEVICE_LIST_NOT_OBJECT")
    return value


def _device_key(row: dict[str, object]) -> tuple[str | None, str | None]:
    device_id = row.get("deviceId") or row.get("id")
    public_key = row.get("publicKey") or row.get("public_key")
    return (
        sha_text(str(device_id)) if device_id else None,
        sha_text(str(public_key)) if public_key else None,
    )


def _pending_admin_gate(client: paramiko.SSHClient, project: str, root: str) -> dict[str, object]:
    cli = project + "/scripts/openclaw/openclaw_cli.sh"
    devices = _load_device_rows(client, cli)
    pending = devices.get("pending", [])
    paired = devices.get("paired", [])
    if not isinstance(pending, list) or len(pending) != 1 or not isinstance(pending[0], dict):
        raise GateError("ADMIN_GATE_REQUIRES_EXACTLY_ONE_PENDING")
    if not isinstance(paired, list) or len(paired) != 1 or not isinstance(paired[0], dict):
        raise GateError("ADMIN_GATE_REQUIRES_EXACTLY_ONE_PAIRED")
    request = pending[0]
    approved = paired[0]
    requested = request.get("requestedScopes") or request.get("scopes") or []
    if requested != ["operator.admin"]:
        raise GateError("ADMIN_GATE_SCOPE_NOT_EXACT")
    if _device_key(request) != _device_key(approved):
        raise GateError("ADMIN_GATE_PAIRED_IDENTITY_MISMATCH")
    if str(request.get("clientMode") or request.get("mode")).lower() != "cli":
        raise GateError("ADMIN_GATE_NOT_CLI_MODE")
    request_ms_value = request.get("ts") or request.get("createdAtMs") or request.get("requestedAtMs")
    if not isinstance(request_ms_value, (int, float)):
        raise GateError("ADMIN_GATE_REQUEST_TIME_MISSING")
    request_ms = int(request_ms_value)
    if request_ms < 10_000_000_000:
        request_ms *= 1000
    now_ms = int(checked(client, "date +%s%3N"))
    request_age_seconds = (now_ms - request_ms) / 1000
    if request_age_seconds < -10 or request_age_seconds > 900:
        raise GateError("ADMIN_GATE_REQUEST_TIME_OUT_OF_RANGE")
    last_seen_value = approved.get("lastSeenAtMs")
    last_seen_correlates = isinstance(last_seen_value, (int, float)) and abs(int(last_seen_value) - request_ms) <= 300_000
    if not last_seen_correlates:
        raise GateError("ADMIN_GATE_LAST_SEEN_TIME_MISMATCH")

    evidence_code = r'''import hashlib,json,os,pathlib,re,urllib.parse
project=pathlib.Path(os.environ["PROJECT"]);state=project/"storage/openclaw/state";device_hash=os.environ["DEVICE_HASH"];request_ms=int(os.environ["REQUEST_MS"])
ids=set();keys=set();permissions=[]
def walk(value):
 if isinstance(value,dict):
  for key,item in value.items():
   low=key.lower()
   if low in {"deviceid","device_id"} and isinstance(item,(str,int)):ids.add(hashlib.sha256(str(item).encode()).hexdigest())
   if low in {"publickey","public_key"} and isinstance(item,str):keys.add(hashlib.sha256(item.encode()).hexdigest())
   walk(item)
 elif isinstance(value,list):
  for item in value:walk(item)
for path in state.rglob("*"):
 if not path.is_file() or path.stat().st_size>2_000_000:continue
 rel=str(path.relative_to(state)).lower()
 if not any(word in rel for word in ("device","identity","credential","auth")):continue
 permissions.append(((path.stat().st_mode&0o777)&0o177)==0)
 if path.suffix==".json":
  try:walk(json.loads(path.read_text()))
  except Exception:pass
text="\n".join((project/name).read_text(errors="replace") for name in ("scripts/openclaw/openclaw_cli.sh","scripts/openclaw/openclaw_env.sh"))
urls=re.findall(r"(?:https?|wss?)://[^\s'\"$]+",text);external=False
for value in urls:
 try:
  host=urllib.parse.urlsplit(value).hostname
  if host not in {None,"127.0.0.1","localhost","::1"}:external=True
 except ValueError:external=True
listeners=[]
for filename,v6 in (("/proc/net/tcp",False),("/proc/net/tcp6",True)):
 for line in pathlib.Path(filename).read_text().splitlines()[1:]:
  parts=line.split();address,port_hex=parts[1].split(":")
  if int(port_hex,16)==18789 and parts[3]=="0A":listeners.append(address==("00000000000000000000000001000000" if v6 else "0100007F"))
log=project/"storage/openclaw/logs/gateway.log";lines=log.read_text(errors="replace").splitlines()[-1000:] if log.is_file() else []
matching=[line for line in lines if re.search(r"device|pair|scope|operator\.admin|openclaw-cli|client.*cli|cli.*client",line,re.I)]
cli_log=any(re.search(r"openclaw-cli|client.*cli|cli.*client",line,re.I) for line in matching)
log_recent=bool(log.is_file() and log.stat().st_mtime>=request_ms/1000-30)
print(json.dumps({"identity_ids":sorted(ids),"identity_keys":sorted(keys),"permissions_ok":bool(permissions) and all(permissions),"public_cli_only":not bool(re.search(r"pending\.json|paired\.json|device-auth\.json",text)),"no_explicit_remote_gateway":not external,"listeners":listeners,"gateway_matching_lines":len(matching),"gateway_cli_evidence":cli_log,"gateway_log_recent":log_recent,"gateway_latest_match_sha256":hashlib.sha256(matching[-1].encode()).hexdigest() if matching else None}))
'''
    device_id_hash, public_key_hash = _device_key(request)
    evidence = json.loads(checked(
        client,
        f"PROJECT={q(project)} DEVICE_HASH={q(str(device_id_hash))} REQUEST_MS={q(str(request_ms))} {q(root + '/conda/envs/epidemic-core/bin/python')} -c {q(evidence_code)}",
        timeout=300,
    ))
    identity_match = device_id_hash in evidence.get("identity_ids", []) and public_key_hash in evidence.get("identity_keys", [])
    loopback_only = bool(evidence.get("listeners")) and all(evidence.get("listeners", []))
    gateway_correlates = bool(evidence.get("gateway_matching_lines")) and bool(evidence.get("gateway_cli_evidence")) and bool(evidence.get("gateway_log_recent"))
    required = {
        "identity_match": identity_match,
        "credential_permissions_ok": bool(evidence.get("permissions_ok")),
        "public_cli_only": bool(evidence.get("public_cli_only")),
        "no_explicit_remote_gateway": bool(evidence.get("no_explicit_remote_gateway")),
        "gateway_loopback_only": loopback_only,
        "gateway_log_correlates": gateway_correlates,
        "request_time_correlates": True,
        "paired_last_seen_correlates": last_seen_correlates,
    }
    if not all(required.values()):
        raise GateError("BLOCKED_CRON_DEVICE_IDENTITY:" + json.dumps(required, sort_keys=True))
    return {
        "pending_count":1,
        "requested_scopes":["operator.admin"],
        "client_mode":"cli",
        "request_age_seconds":round(request_age_seconds, 3),
        "device_id_sha256":device_id_hash,
        "public_key_sha256":public_key_hash,
        "gateway_latest_match_sha256":evidence.get("gateway_latest_match_sha256"),
        **required,
    }


def pending_admin_audit_action() -> None:
    client, gate = connect()
    try:
        root, project = paths(client)
        audit = _pending_admin_gate(client, project, root)
        print(json.dumps({
            "target_gate":{"key_type":gate["key_type"],"stable_second_connection":True},
            "admin_request_gate":audit,
            "no_source_server_connection":True,
        },indent=2))
    finally:
        client.close()


def approve_help_action() -> None:
    client, gate = connect()
    try:
        _root, project = paths(client)
        cli = project + "/scripts/openclaw/openclaw_cli.sh"
        rc, output, error = run(client, f"{q(cli)} devices approve --help", timeout=120)
        value = output + "\n" + error
        value = re.sub(r"(?i)(--(?:token|password)\s+)[^\s]+", r"\1[redacted]", value)
        value = re.sub(r"\b[0-9a-fA-F]{32,}\b", "[id-redacted]", value)
        print(json.dumps({
            "target_gate":{"key_type":gate["key_type"],"stable_second_connection":True},
            "rc":rc,
            "help":value.splitlines(),
            "no_source_server_connection":True,
        },indent=2))
    finally:
        client.close()


def _cron_jobs(client: paramiko.SSHClient, cli: str) -> list[dict[str, object]]:
    rc, output, error = run(client, f"{q(cli)} cron list --all --json", timeout=120)
    if rc:
        raise GateError("OPENCLAW_CRON_LIST_FAILED:" + ("authorization_required" if re.search(r"auth|scope|permission", error, re.I) else "cli_error"))
    value = json.loads(output)
    rows: object = value
    if isinstance(value, dict):
        rows = value.get("jobs", value.get("items", value.get("data", [])))
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise GateError("OPENCLAW_CRON_LIST_INVALID")
    return rows  # type: ignore[return-value]


def _cron_job_summary(job: dict[str, object], project: str) -> dict[str, object]:
    job_id = job.get("id") or job.get("jobId")
    schedule = job.get("schedule") if isinstance(job.get("schedule"), dict) else {}
    payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
    argv = payload.get("argv") or payload.get("commandArgv") or job.get("commandArgv")
    cwd = payload.get("cwd") or payload.get("commandCwd") or job.get("commandCwd")
    expr = schedule.get("expr") or schedule.get("cron") or job.get("cron")
    timezone = schedule.get("tz") or schedule.get("timezone") or job.get("timezone")
    owner = job.get("owner") or job.get("ownerDeviceId") or job.get("createdBy")
    exact = schedule.get("staggerMs") == 0 or schedule.get("exact") is True or job.get("exact") is True
    expected_argv = [project + "/scripts/daily_update/reconcile_due.sh", "--trigger", "openclaw-cron"]
    serialized = json.dumps(job, sort_keys=True, default=str)
    forbidden = re.search(r"(?i)qwen|vllm|web_search|discovery.?agent|agent.?turn|password|bearer|api[_-]?key|postgres(?:ql)?://|https?://", serialized)
    delivery = job.get("delivery") if isinstance(job.get("delivery"), dict) else {}
    return {
        "job_id":str(job_id) if job_id else None,
        "job_id_sha256":sha_text(str(job_id)) if job_id else None,
        "name":job.get("name"),
        "enabled":job.get("enabled", True),
        "owner_sha256":sha_text(str(owner)) if owner else None,
        "schedule":schedule,
        "payload_kind":payload.get("kind") or job.get("kind"),
        "argv":argv,
        "cwd":cwd,
        "delivery":delivery,
        "created_at":job.get("createdAtMs") or job.get("createdAt"),
        "updated_at":job.get("updatedAtMs") or job.get("updatedAt"),
        "next_run_at":job.get("nextRunAtMs") or job.get("nextRunAt") or (job.get("state") or {}).get("nextRunAtMs") if isinstance(job.get("state"), dict) else None,
        "field_names":sorted(job.keys()),
        "payload_field_names":sorted(payload.keys()),
        "definition_checks":{
            "name_exact":job.get("name")=="epidemic-daily-update-v1",
            "enabled":job.get("enabled", True) is True,
            "cron_exact":expr=="30 2 * * *",
            "timezone_exact":timezone=="Asia/Shanghai",
            "exact_execution":exact,
            "argv_exact":argv==expected_argv,
            "cwd_exact":cwd==project,
            "no_command_env":not bool(payload.get("env") or payload.get("commandEnv") or job.get("commandEnv")),
            "no_forbidden_content":forbidden is None,
            "no_delivery":not bool(delivery.get("mode") not in (None,"none") or job.get("deliver") is True),
        },
    }


def cron_install_idempotency_action() -> None:
    client, gate = connect()
    try:
        _root, project = paths(client)
        cli = project + "/scripts/openclaw/openclaw_cli.sh"
        devices = _load_device_rows(client, cli)
        pending = devices.get("pending", [])
        paired = devices.get("paired", [])
        if not isinstance(pending, list) or pending:
            raise GateError("CRON_INSTALL_PENDING_DEVICE_PRESENT")
        if not isinstance(paired, list) or len(paired)!=1 or "operator.admin" not in (paired[0].get("scopes") if isinstance(paired[0],dict) else []):
            raise GateError("CRON_INSTALL_OPERATOR_ADMIN_MISSING")
        before_jobs = _cron_jobs(client, cli)
        before_daily = [job for job in before_jobs if job.get("name")=="epidemic-daily-update-v1"]
        if len(before_daily)>1:
            raise GateError("CRON_INSTALL_DUPLICATE_BEFORE")
        if before_daily:
            summary = _cron_job_summary(before_daily[0], project)
            if not all(summary["definition_checks"].values()):
                raise GateError("CRON_INSTALL_EXISTING_DEFINITION_DRIFT:"+json.dumps(summary["definition_checks"],sort_keys=True))
        install = project + "/scripts/daily_update/install_openclaw_cron.sh"
        observed_ids: list[str] = []
        iterations: list[dict[str, object]] = []
        for number in range(1,11):
            rc, output, error = run(client, q(install), timeout=180)
            if rc:
                raise GateError(f"CRON_INSTALL_ITERATION_FAILED:{number}:"+("authorization_required" if re.search(r"auth|scope|permission",error,re.I) else "cli_error"))
            jobs = _cron_jobs(client, cli)
            daily = [job for job in jobs if job.get("name")=="epidemic-daily-update-v1"]
            if len(daily)!=1:
                raise GateError(f"CRON_INSTALL_COUNT_INVALID:{number}:{len(daily)}")
            job_id = daily[0].get("id") or daily[0].get("jobId")
            if not job_id:
                raise GateError("CRON_INSTALL_JOB_ID_MISSING")
            observed_ids.append(str(job_id))
            iterations.append({"iteration":number,"rc":rc,"output_sha256":sha_text(output),"job_id_sha256":sha_text(str(job_id)),"daily_count":1,"total_cron_count":len(jobs)})
        if len(set(observed_ids))!=1:
            raise GateError("CRON_INSTALL_JOB_ID_NOT_STABLE")
        after_jobs = _cron_jobs(client, cli)
        daily = [job for job in after_jobs if job.get("name")=="epidemic-daily-update-v1"]
        if len(daily)!=1:
            raise GateError("CRON_INSTALL_FINAL_COUNT_INVALID")
        summary = _cron_job_summary(daily[0], project)
        if not all(summary["definition_checks"].values()):
            raise GateError("CRON_INSTALL_FINAL_DEFINITION_INVALID:"+json.dumps(summary["definition_checks"],sort_keys=True))
        summary["cwd"]="[project]" if summary.get("cwd")==project else "[unexpected-path]"
        summary["argv"]=["[project]/scripts/daily_update/reconcile_due.sh" if item==project+"/scripts/daily_update/reconcile_due.sh" else item for item in summary.get("argv",[])]
        print(json.dumps({
            "target_gate":{"key_type":gate["key_type"],"stable_second_connection":True},
            "cron_count_before":len(before_jobs),
            "daily_count_before":len(before_daily),
            "iterations":iterations,
            "same_job_id_all_ten":True,
            "cron_count_after":len(after_jobs),
            "daily_count_after":1,
            "job":summary,
            "other_cron_unchanged":len(after_jobs)-1==len(before_jobs)-len(before_daily),
            "no_source_server_connection":True,
        },ensure_ascii=False,indent=2))
    finally:
        client.close()


def cron_run_help_action() -> None:
    client, gate = connect()
    try:
        _root, project = paths(client)
        cli = project + "/scripts/openclaw/openclaw_cli.sh"
        values = {}
        for label, suffix in (("run","cron run --help"),("runs","cron runs --help"),("show","cron show --help")):
            rc, output, error = run(client, f"{q(cli)} {suffix}", timeout=120)
            value = re.sub(r"(?i)(--(?:token|password)\s+)\S+", r"\1[redacted]", output+"\n"+error)
            values[label]={"rc":rc,"help":value.splitlines()}
        print(json.dumps({
            "target_gate":{"key_type":gate["key_type"],"stable_second_connection":True},
            "commands":values,
            "no_source_server_connection":True,
        },indent=2))
    finally:
        client.close()


def _cron_run_entries(client: paramiko.SSHClient, cli: str, job_id: str, limit: int = 20) -> list[dict[str, object]]:
    rc, output, error = run(client, f"{q(cli)} cron runs --id {q(job_id)} --limit {limit}", timeout=120)
    if rc:
        raise GateError("OPENCLAW_CRON_RUNS_FAILED:"+("authorization_required" if re.search(r"auth|scope|permission",error,re.I) else "cli_error"))
    value = json.loads(output)
    rows: object = value
    if isinstance(value, dict):
        rows = value.get("runs", value.get("entries", value.get("items", value.get("data", []))))
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise GateError("OPENCLAW_CRON_RUNS_INVALID")
    return rows  # type: ignore[return-value]


def _cron_run_summary(row: dict[str, object]) -> dict[str, object]:
    run_id = row.get("id") or row.get("runId")
    job_id = row.get("jobId")
    output = row.get("output") or row.get("result") or row.get("summary")
    return {
        "run_id":str(run_id) if run_id else None,
        "run_id_sha256":sha_text(str(run_id)) if run_id else None,
        "job_id_sha256":sha_text(str(job_id)) if job_id else None,
        "status":row.get("status") or row.get("state"),
        "trigger":row.get("trigger") or row.get("reason"),
        "started_at":row.get("startedAtMs") or row.get("startedAt") or row.get("createdAtMs"),
        "finished_at":row.get("finishedAtMs") or row.get("finishedAt") or row.get("endedAtMs"),
        "duration_ms":row.get("durationMs"),
        "exit_code":row.get("exitCode"),
        "output_sha256":sha_text(json.dumps(output,sort_keys=True,default=str)) if output is not None else None,
        "field_names":sorted(row.keys()),
    }


def _daily_cli_status(client: paramiko.SSHClient, project: str) -> dict[str, object]:
    common = project + "/scripts/daily_update/_common.sh"
    command = f"source {q(common)}; exec \"$PYTHON\" -B -m epidemic_core.daily.cli status"
    value = json.loads(checked(client, f"bash -c {q(command)}", timeout=120))
    if not isinstance(value, dict):
        raise GateError("DAILY_STATUS_CLI_INVALID")
    return value


def cron_forced_run_action() -> None:
    client, gate = connect()
    try:
        _root, project = paths(client)
        cli = project + "/scripts/openclaw/openclaw_cli.sh"
        jobs = _cron_jobs(client, cli)
        daily = [job for job in jobs if job.get("name")=="epidemic-daily-update-v1"]
        if len(daily)!=1:
            raise GateError("CRON_FORCE_REQUIRES_ONE_DAILY_JOB")
        job_summary = _cron_job_summary(daily[0], project)
        if not all(job_summary["definition_checks"].values()):
            raise GateError("CRON_FORCE_DEFINITION_DRIFT")
        job_id = job_summary["job_id"]
        assert isinstance(job_id,str)
        before_history = _cron_run_entries(client, cli, job_id)
        before_daily = _daily_cli_status(client, project)
        formal_before = {name:int(db_query(client,f"SELECT count(*) FROM epidemic.{name}")) for name in EXPECTED_COUNTS}
        snapshots_before = json.loads(db_query(client,"SELECT COALESCE(json_agg(json_build_object('sha256',content_sha256,'row_count',row_count) ORDER BY id),'[]'::json)::text FROM epidemic.dataset_snapshots"))
        started = time.monotonic()
        rc, output, error = run(
            client,
            f"{q(cli)} cron run {q(job_id)} --wait --wait-timeout 3m --poll-interval 500ms --timeout 30000",
            timeout=300,
        )
        duration = time.monotonic()-started
        if rc:
            raise GateError("OPENCLAW_CRON_FORCE_FAILED:"+("authorization_required" if re.search(r"auth|scope|permission",error,re.I) else "cli_error"))
        run_output_sha = sha_text(output)
        after_history: list[dict[str, object]] = []
        for _ in range(20):
            after_history = _cron_run_entries(client, cli, job_id)
            if len(after_history)>len(before_history):
                break
            time.sleep(1)
        if len(after_history)!=len(before_history)+1:
            raise GateError("CRON_FORCE_HISTORY_COUNT_INVALID")
        newest = after_history[0]
        history_summary = _cron_run_summary(newest)
        if str(history_summary.get("status")).lower() not in {"ok","success","succeeded","completed"}:
            raise GateError("CRON_FORCE_HISTORY_NOT_SUCCESS:"+str(history_summary.get("status")))
        deadline=time.monotonic()+180
        after_daily=_daily_cli_status(client,project)
        active={"QUEUED","CLAIMED","FETCHING","NORMALIZING","SNAPSHOTTING","FAILED_RETRYABLE"}
        while any(row.get("state") in active for row in after_daily.get("runs",[]) if isinstance(row,dict)) and time.monotonic()<deadline:
            time.sleep(2)
            after_daily=_daily_cli_status(client,project)
        if any(row.get("state") in active for row in after_daily.get("runs",[]) if isinstance(row,dict)):
            raise GateError("DAILY_RUN_NOT_TERMINAL_AFTER_FORCE")
        formal_after = {name:int(db_query(client,f"SELECT count(*) FROM epidemic.{name}")) for name in EXPECTED_COUNTS}
        snapshots_after = json.loads(db_query(client,"SELECT COALESCE(json_agg(json_build_object('sha256',content_sha256,'row_count',row_count) ORDER BY id),'[]'::json)::text FROM epidemic.dataset_snapshots"))
        def daily_summaries(value: dict[str, object]) -> list[dict[str, object]]:
            rows=[]
            for raw in value.get("runs",[]):
                if not isinstance(raw,dict):continue
                row=dict(raw);run_id=row.pop("run_id",None);schedule_id=row.pop("schedule_id",None)
                row["run_id_sha256"]=sha_text(str(run_id)) if run_id else None
                row["schedule_id_sha256"]=sha_text(str(schedule_id)) if schedule_id else None
                rows.append(row)
            return rows
        print(json.dumps({
            "target_gate":{"key_type":gate["key_type"],"stable_second_connection":True},
            "job":job_summary,
            "force_command":{"rc":rc,"duration_seconds":round(duration,3),"output_sha256":run_output_sha},
            "history_before_count":len(before_history),
            "history_after_count":len(after_history),
            "new_history_entry":history_summary,
            "daily_before":daily_summaries(before_daily),
            "daily_after":daily_summaries(after_daily),
            "formal_counts_before":formal_before,
            "formal_counts_after":formal_after,
            "formal_counts_unchanged":formal_before==formal_after==EXPECTED_COUNTS,
            "snapshots_unchanged":snapshots_before==snapshots_after,
            "old_snapshots_unchanged":{row["sha256"]:row["row_count"] for row in snapshots_after}==EXPECTED_SNAPSHOTS,
            "no_source_server_connection":True,
        },ensure_ascii=False,indent=2))
    finally:
        client.close()


def daily_concurrency_action() -> None:
    client, gate = connect()
    try:
        root, project = paths(client)
        before = _daily_cli_status(client, project)
        runs_before = [row for row in before.get("runs",[]) if isinstance(row,dict)]
        today = dt.datetime.now(dt.timezone.utc).astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
        candidates = [row for row in runs_before if row.get("logical_date")==today and row.get("schedule_id")]
        if not candidates:
            raise GateError("DAILY_CONCURRENCY_NO_CURRENT_LOGICAL_DATE_RUN")
        schedule_id = str(candidates[0]["schedule_id"])
        existing_run_id = str(candidates[0]["run_id"])
        code = r'''import concurrent.futures,datetime as dt,json,os,uuid
from epidemic_core.config import Settings
from epidemic_core.daily.service import DailyUpdateService
from epidemic_core.db.engine import create_app_engine
from epidemic_core.db.session import session_factory
engine=create_app_engine(Settings());factory=session_factory(engine);schedule=uuid.UUID(os.environ["SCHEDULE"])
def start(_):
 with factory() as session,session.begin():return str(DailyUpdateService.start_manual(session,schedule,dt.datetime.now(dt.timezone.utc)))
try:
 with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:values=list(pool.map(start,range(10)))
 print(json.dumps(values))
finally:engine.dispose()
'''
        common=project+"/scripts/daily_update/_common.sh"
        command=f"source {q(common)}; SCHEDULE={q(schedule_id)} exec \"$PYTHON\" -B -c {q(code)}"
        values=json.loads(checked(client,f"bash -c {q(command)}",timeout=180))
        if not isinstance(values,list) or len(values)!=10 or len(set(values))!=1 or values[0]!=existing_run_id:
            raise GateError("DAILY_CONCURRENCY_DEDUPLICATION_FAILED")
        after=_daily_cli_status(client,project)
        runs_after=[row for row in after.get("runs",[]) if isinstance(row,dict)]
        formal={name:int(db_query(client,f"SELECT count(*) FROM epidemic.{name}")) for name in EXPECTED_COUNTS}
        snapshots=json.loads(db_query(client,"SELECT COALESCE(json_agg(json_build_object('sha256',content_sha256,'row_count',row_count) ORDER BY id),'[]'::json)::text FROM epidemic.dataset_snapshots"))
        print(json.dumps({
            "target_gate":{"key_type":gate["key_type"],"stable_second_connection":True},
            "logical_date":today,
            "schedule_id_sha256":sha_text(schedule_id),
            "existing_run_id_sha256":sha_text(existing_run_id),
            "concurrent_requests":10,
            "unique_run_ids":1,
            "all_returned_existing_run":True,
            "daily_run_count_before":len(runs_before),
            "daily_run_count_after":len(runs_after),
            "no_new_daily_run":len(runs_before)==len(runs_after),
            "formal_counts_unchanged":formal==EXPECTED_COUNTS,
            "old_snapshots_unchanged":{row["sha256"]:row["row_count"] for row in snapshots}==EXPECTED_SNAPSHOTS,
            "no_source_server_connection":True,
        },indent=2))
    finally:
        client.close()


def cron_gateway_restart_action() -> None:
    client, gate = connect()
    try:
        _root, project=paths(client);cli=project+"/scripts/openclaw/openclaw_cli.sh"
        jobs_before=_cron_jobs(client,cli);daily_before=[job for job in jobs_before if job.get("name")=="epidemic-daily-update-v1"]
        if len(daily_before)!=1:raise GateError("CRON_RESTART_REQUIRES_ONE_DAILY_JOB")
        before=_cron_job_summary(daily_before[0],project);job_id=before.get("job_id")
        if not isinstance(job_id,str) or not all(before["definition_checks"].values()):raise GateError("CRON_RESTART_DEFINITION_INVALID_BEFORE")
        history_before=_cron_run_entries(client,cli,job_id)
        stop=project+"/scripts/openclaw/stop_openclaw.sh";start=project+"/scripts/openclaw/start_openclaw.sh";status=project+"/scripts/openclaw/status_openclaw.sh"
        if any(run(client,f"test -x {q(path)}")[0] for path in (stop,start,status)):
            raise GateError("OPENCLAW_GATEWAY_CONTROL_SCRIPT_MISSING")
        started=time.monotonic();stop_rc,stop_output,stop_error=run(client,q(stop),timeout=180)
        if stop_rc:raise GateError("OPENCLAW_GATEWAY_STOP_FAILED")
        start_rc,start_output,start_error=run(client,q(start),timeout=300);duration=time.monotonic()-started
        if start_rc:raise GateError("OPENCLAW_GATEWAY_START_FAILED")
        healthy=False
        for _ in range(60):
            if run(client,q(status),timeout=60)[0]==0:
                healthy=True;break
            time.sleep(1)
        if not healthy:raise GateError("OPENCLAW_GATEWAY_NOT_HEALTHY_AFTER_RESTART")
        jobs_after=_cron_jobs(client,cli);daily_after=[job for job in jobs_after if job.get("name")=="epidemic-daily-update-v1"]
        if len(daily_after)!=1:raise GateError("CRON_RESTART_DAILY_COUNT_INVALID")
        after=_cron_job_summary(daily_after[0],project)
        history_after=_cron_run_entries(client,cli,job_id)
        stable={
            "job_id":after.get("job_id")==before.get("job_id"),
            "enabled":after.get("enabled") is True,
            "schedule":after.get("schedule")==before.get("schedule"),
            "argv":after.get("argv")==before.get("argv"),
            "cwd":after.get("cwd")==before.get("cwd"),
            "next_run_present":after.get("next_run_at") is not None,
            "history_preserved":len(history_after)==len(history_before),
        }
        if not all(stable.values()) or not all(after["definition_checks"].values()):raise GateError("CRON_RESTART_PERSISTENCE_FAILED:"+json.dumps(stable,sort_keys=True))
        print(json.dumps({
            "target_gate":{"key_type":gate["key_type"],"stable_second_connection":True},
            "gateway_restart":{"stop_rc":stop_rc,"start_rc":start_rc,"duration_seconds":round(duration,3),"output_sha256":sha_text(stop_output+start_output),"healthy":healthy},
            "job_before":before,
            "job_after":after,
            "persistence_checks":stable,
            "no_source_server_connection":True,
        },ensure_ascii=False,indent=2))
    finally:
        client.close()


def openclaw_restart_diagnose_action() -> None:
    client, gate=connect()
    try:
        root,project=paths(client);restart=project+"/scripts/openclaw/restart_openclaw.sh";status=project+"/scripts/openclaw/status_openclaw.sh"
        restart_text=checked(client,f"sed -n '1,240p' {q(restart)}")
        restart_text=restart_text.replace(project.rsplit("/autodl-tmp",1)[0],"[target-home]")
        restart_text=re.sub(r"(?i)(password|token|secret|api[_-]?key)(\s*[=:]\s*)\S+",r"\1\2[redacted]",restart_text)
        syntax_rc,_,_=run(client,f"bash -n {q(restart)}")
        status_rc,status_out,status_err=run(client,q(status),timeout=120)
        log_code=r'''import hashlib,json,os,pathlib,re
project=pathlib.Path(os.environ["PROJECT"]);logs=[]
for path in (project/"storage/openclaw/logs").glob("*.log"):
 if path.is_file():logs.append(path)
logs=sorted(logs,key=lambda p:p.stat().st_mtime)[-4:];rows=[]
for path in logs:
 lines=path.read_text(errors="replace").splitlines()[-80:]
 for line in lines:
  if re.search(r"error|failed|fatal|shutdown|signal|address|listen|gateway",line,re.I):
   safe=re.sub(r"(?i)(password|token|secret|api[_-]?key)(\s*[=:]\s*)\S+",r"\1\2[redacted]",line)
   safe=re.sub(r"(?:https?|wss?)://\S+","[url-redacted]",safe)
   safe=re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b","[address-redacted]",safe)
   rows.append({"file":path.name,"line":safe[-500:],"sha256":hashlib.sha256(line.encode()).hexdigest()})
print(json.dumps(rows[-40:]))
'''
        logs=json.loads(checked(client,f"PROJECT={q(project)} {q(root+'/conda/envs/epidemic-core/bin/python')} -c {q(log_code)}"))
        print(json.dumps({
            "target_gate":{"key_type":gate["key_type"],"stable_second_connection":True},
            "restart_script":restart_text.splitlines(),
            "restart_syntax_rc":syntax_rc,
            "gateway_status_rc":status_rc,
            "gateway_status_output_sha256":sha_text(status_out+status_err),
            "log_evidence":logs,
            "no_source_server_connection":True,
        },ensure_ascii=False,indent=2))
    finally:client.close()


def trigger_admin_request_action() -> None:
    client, gate = connect()
    try:
        root, project = paths(client)
        if checked(client, f"cd {q(project)} && git rev-parse HEAD") != EXPECTED_HEAD:
            raise GateError("BLOCKED_WRONG_TARGET_OR_MIGRATION_DRIFT")
        cli = project + "/scripts/openclaw/openclaw_cli.sh"
        before = _load_device_rows(client, cli)
        pending_before = before.get("pending", [])
        paired_before = before.get("paired", [])
        if not isinstance(pending_before, list) or pending_before:
            raise GateError("ADMIN_GATE_REQUIRES_ZERO_PENDING_BEFORE_REQUEST")
        if not isinstance(paired_before, list) or len(paired_before) != 1 or not isinstance(paired_before[0], dict):
            raise GateError("ADMIN_GATE_REQUIRES_ONE_LOCAL_PAIRED_DEVICE")
        paired_id, paired_key = _device_key(paired_before[0])
        if not paired_id or not paired_key:
            raise GateError("LOCAL_DEVICE_IDENTITY_UNAVAILABLE")

        identity_code = r'''import hashlib,json,os,pathlib
root=pathlib.Path(os.environ["STATE"]);ids=set();keys=set();modes=[]
def walk(value):
 if isinstance(value,dict):
  for key,item in value.items():
   low=key.lower()
   if low in {"deviceid","device_id"} and isinstance(item,(str,int)):ids.add(hashlib.sha256(str(item).encode()).hexdigest())
   if low in {"publickey","public_key"} and isinstance(item,str):keys.add(hashlib.sha256(item.encode()).hexdigest())
   walk(item)
 elif isinstance(value,list):
  for item in value:walk(item)
for path in root.rglob("*"):
 if not path.is_file() or path.stat().st_size>2_000_000:continue
 rel=str(path.relative_to(root)).lower()
 if not any(word in rel for word in ("device","identity","credential","auth")):continue
 modes.append((path.stat().st_mode&0o777)&0o177==0)
 if path.suffix==".json":
  try:walk(json.loads(path.read_text()))
  except Exception:pass
print(json.dumps({"ids":sorted(ids),"keys":sorted(keys),"permissions_ok":bool(modes) and all(modes)}))
'''
        identity = json.loads(
            checked(
                client,
                f"STATE={q(project + '/storage/openclaw/state')} {q(root + '/conda/envs/epidemic-core/bin/python')} -c {q(identity_code)}",
                timeout=300,
            )
        )
        if paired_id not in identity.get("ids", []) or paired_key not in identity.get("keys", []):
            raise GateError("PAIRED_DEVICE_NOT_CURRENT_A100_LOCAL_IDENTITY")
        if not identity.get("permissions_ok"):
            raise GateError("LOCAL_CREDENTIAL_PERMISSIONS_TOO_WIDE")

        cli_probe_code = r'''import json,os,pathlib,re,urllib.parse
project=pathlib.Path(os.environ["PROJECT"]);cli=project/"scripts/openclaw/openclaw_cli.sh";env=project/"scripts/openclaw/openclaw_env.sh"
text=(cli.read_text(errors="replace")+"\n"+env.read_text(errors="replace"))
urls=re.findall(r"(?:https?|wss?)://[^\s'\"$]+",text);external=False
for value in urls:
 try:
  host=urllib.parse.urlsplit(value).hostname
  if host not in {None,"127.0.0.1","localhost","::1"}:external=True
 except ValueError:external=True
print(json.dumps({"uses_project_cli":cli.is_file(),"loopback_gateway_config":bool(re.search(r"127\.0\.0\.1|localhost|::1",text)),"no_explicit_remote_gateway":not external,"public_cli_only":not bool(re.search(r"pending\.json|paired\.json|device-auth\.json",text))}))
'''
        cli_probe = json.loads(
            checked(
                client,
                f"PROJECT={q(project)} {q(root + '/conda/envs/epidemic-core/bin/python')} -c {q(cli_probe_code)}",
            )
        )
        if not cli_probe.get("uses_project_cli") or not cli_probe.get("public_cli_only") or not cli_probe.get("no_explicit_remote_gateway"):
            raise GateError("OPENCLAW_CLI_OR_LOOPBACK_GATE_FAILED")
        listener_code = r'''import json,pathlib
rows=[]
for filename,v6 in (("/proc/net/tcp",False),("/proc/net/tcp6",True)):
 for line in pathlib.Path(filename).read_text().splitlines()[1:]:
  parts=line.split();address,port_hex=parts[1].split(":")
  if int(port_hex,16)==18789 and parts[3]=="0A":rows.append(address==("00000000000000000000000001000000" if v6 else "0100007F"))
print(json.dumps(rows))
'''
        listeners = json.loads(checked(client, f"{q(root + '/conda/envs/epidemic-core/bin/python')} -c {q(listener_code)}"))
        if not listeners or not all(listeners):
            raise GateError("OPENCLAW_GATEWAY_NOT_LOOPBACK_ONLY")

        start_ms = int(checked(client, "date +%s%3N"))
        install = project + "/scripts/daily_update/install_openclaw_cron.sh"
        install_rc, install_out, install_err = run(client, q(install), timeout=180)
        end_ms = int(checked(client, "date +%s%3N"))
        after = _load_device_rows(client, cli)
        pending = after.get("pending", [])
        if not isinstance(pending, list) or len(pending) != 1 or not isinstance(pending[0], dict):
            raise GateError(f"ADMIN_REQUEST_NOT_UNIQUE:install_rc={install_rc}:pending={len(pending) if isinstance(pending,list) else -1}")
        row = pending[0]
        requested = row.get("requestedScopes") or row.get("scopes") or []
        if requested != ["operator.admin"]:
            raise GateError("ADMIN_REQUEST_SCOPE_NOT_EXACT")
        pending_id, pending_key = _device_key(row)
        if pending_id != paired_id or pending_key != paired_key:
            raise GateError("ADMIN_REQUEST_NOT_CURRENT_A100_LOCAL_IDENTITY")
        mode = row.get("clientMode") or row.get("mode")
        if str(mode).lower() != "cli":
            raise GateError("ADMIN_REQUEST_NOT_CLI_MODE")
        created = row.get("createdAtMs") or row.get("createdAt") or row.get("requestedAtMs")
        time_match = True
        if isinstance(created, (int, float)):
            value = int(created)
            if value < 10_000_000_000:value *= 1000
            time_match = start_ms - 5000 <= value <= end_ms + 5000
        if not time_match:
            raise GateError("ADMIN_REQUEST_TIME_MISMATCH")
        request_id = row.get("requestId")
        error_class = "authorization_required" if re.search(r"auth|scope|permission|pair|operator\.admin", install_err + install_out, re.I) else "cli_error"
        print(json.dumps({
            "target_gate":{"key_type":gate["key_type"],"stable_second_connection":True},
            "install_attempt":{"rc":install_rc,"error_class":error_class},
            "pending_before":0,
            "pending_after":1,
            "requested_scopes":["operator.admin"],
            "request_id_sha256":sha_text(str(request_id)) if request_id else None,
            "device_id_sha256":pending_id,
            "public_key_sha256":pending_key,
            "current_local_identity_match":True,
            "credential_permissions_ok":True,
            "client_mode":"cli",
            "request_time_in_invocation_window":time_match,
            "loopback_gateway_only":True,
            "cli_loopback_config_explicit":bool(cli_probe.get("loopback_gateway_config")),
            "cli_has_no_explicit_remote_gateway":True,
            "public_openclaw_cli_only":True,
            "no_source_server_connection":True,
        },indent=2))
    finally:
        client.close()


def approve_admin_request_action() -> None:
    client, gate = connect()
    try:
        root, project = paths(client)
        cli = project + "/scripts/openclaw/openclaw_cli.sh"
        audit = _pending_admin_gate(client, project, root)
        before = _load_device_rows(client, cli)
        pending = before.get("pending", [])
        paired = before.get("paired", [])
        if not isinstance(pending, list) or len(pending) != 1 or not isinstance(pending[0], dict):
            raise GateError("ADMIN_APPROVAL_REQUIRES_EXACTLY_ONE_PENDING")
        if not isinstance(paired, list) or len(paired) != 1 or not isinstance(paired[0], dict):
            raise GateError("ADMIN_APPROVAL_REQUIRES_EXACTLY_ONE_PAIRED")
        requested = pending[0].get("requestedScopes") or pending[0].get("scopes") or []
        if requested != ["operator.admin"]:
            raise GateError("ADMIN_APPROVAL_SCOPE_NOT_EXACT")
        if _device_key(pending[0]) != _device_key(paired[0]):
            raise GateError("ADMIN_APPROVAL_IDENTITY_MISMATCH")
        request_id = pending[0].get("requestId")
        if not isinstance(request_id, str) or not request_id:
            raise GateError("ADMIN_APPROVAL_REQUEST_ID_MISSING")
        rc, output, error = run(client, f"{q(cli)} devices approve {q(request_id)} --json", timeout=120)
        if rc:
            detail = (output + "\n" + error).strip()
            detail = re.sub(r"(?i)(token|password|secret|api[_-]?key)(\s*[=:]\s*)\S+", r"\1\2[redacted]", detail)
            detail = re.sub(r"(?:https?|wss?)://\S+", "[url-redacted]", detail)
            detail = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "[address-redacted]", detail)
            detail = re.sub(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F-]{27,}\b", "[id-redacted]", detail)
            detail = re.sub(r"\b[0-9a-fA-F]{32,}\b", "[id-redacted]", detail)
            raise GateError("PUBLIC_CLI_ADMIN_APPROVAL_FAILED:" + re.sub(r"\s+", " ", detail)[-500:])
        after = _load_device_rows(client, cli)
        pending_after = after.get("pending", [])
        paired_after = after.get("paired", [])
        if not isinstance(pending_after, list) or pending_after:
            raise GateError("ADMIN_PENDING_NOT_CLEARED")
        if not isinstance(paired_after, list) or len(paired_after) != 1 or not isinstance(paired_after[0], dict):
            raise GateError("ADMIN_PAIRED_DEVICE_INVALID_AFTER")
        scopes = paired_after[0].get("scopes") or []
        if "operator.admin" not in scopes:
            raise GateError("OPERATOR_ADMIN_NOT_GRANTED")
        print(json.dumps({
            "target_gate":{"key_type":gate["key_type"],"stable_second_connection":True},
            "approved_via_public_cli":True,
            "approved_scope":"operator.admin",
            "pending_after":0,
            "paired_after":1,
            "device_id_sha256":_device_key(paired_after[0])[0],
            "public_key_sha256":_device_key(paired_after[0])[1],
            "operator_admin_present":True,
            "preapproval_gate":audit,
            "no_source_server_connection":True,
        },indent=2))
    finally:
        client.close()


def daily_code_inspect_action() -> None:
    client, gate = connect()
    try:
        _root, project = paths(client)
        selected = (
            "src/epidemic_core/daily/cli.py",
            "src/epidemic_core/daily/main.py",
            "src/epidemic_core/daily/schemas.py",
            "src/epidemic_core/daily/service.py",
            "src/epidemic_core/daily/worker.py",
            "src/epidemic_core/api/routes/daily_updates.py",
            "config/daily_update.json",
        )
        files: dict[str, list[str]] = {}
        for relative in selected:
            value = checked(client, f"sed -n '1,800p' {q(project + '/' + relative)}", timeout=180)
            value = re.sub(
                r"(?i)(password|token|secret|api[_-]?key)(\s*[=:]\s*)[^\s'\"]+",
                r"\1\2[redacted]",
                value,
            )
            files[relative] = value.splitlines()
        print(json.dumps({
            "target_gate":{"key_type":gate["key_type"],"stable_second_connection":True},
            "files":files,
            "no_source_server_connection":True,
        },ensure_ascii=False,indent=2))
    finally:
        client.close()


def daily_test_inspect_action() -> None:
    client, gate = connect()
    try:
        _root, project = paths(client)
        selected = (
            "tests/unit/test_daily_updates.py",
            "tests/integration/test_daily_update_contract.py",
            "scripts/daily_update/_common.sh",
            "scripts/daily_update/start_daily_update.sh",
            "scripts/daily_update/restart_daily_update.sh",
            "scripts/daily_update/status_daily_update.sh",
            "scripts/services/start_all.sh",
        )
        files: dict[str, list[str]] = {}
        for relative in selected:
            value = checked(client, f"sed -n '1,1000p' {q(project + '/' + relative)}", timeout=180)
            value = re.sub(
                r"(?i)(password|token|secret|api[_-]?key)(\s*[=:]\s*)[^\s'\"]+",
                r"\1\2[redacted]",
                value,
            )
            files[relative] = value.splitlines()
        print(json.dumps({
            "target_gate":{"key_type":gate["key_type"],"stable_second_connection":True},
            "files":files,
            "no_source_server_connection":True,
        },ensure_ascii=False,indent=2))
    finally:
        client.close()


def daily_unit_tests_action() -> None:
    client, gate = connect()
    try:
        root, project = paths(client)
        python = root + "/conda/envs/epidemic-core/bin/python"
        test_command = (
            f"cd {q(project)} && source scripts/query_api/query_api_env.sh && "
            "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY && "
            f"PYTHONDONTWRITEBYTECODE=1 {q(python)} -m pytest -p no:cacheprovider -q tests/unit/test_daily_updates.py"
        )
        rc, output, error = run(client, f"bash -c {q(test_command)}", timeout=900)
        lint_commands = {
            "ruff_check": f"cd {q(project)} && {q(python)} -m ruff check " + " ".join(q(path) for path in P1_8_FILES if path.endswith('.py')),
            "ruff_format": f"cd {q(project)} && {q(python)} -m ruff format --check " + " ".join(q(path) for path in P1_8_FILES if path.endswith('.py')),
            "bash_syntax": f"cd {q(project)} && bash -n scripts/daily_update/*.sh scripts/services/start_all.sh scripts/services/status_all.sh scripts/services/stop_all.sh",
        }
        results: dict[str, object] = {
            "unit_tests": {
                "rc": rc,
                "summary": re.sub(r"\s+", " ", (output + "\n" + error).strip())[-500:],
            }
        }
        for label, command in lint_commands.items():
            check_rc, check_out, check_err = run(client, f"bash -c {q(command)}", timeout=900)
            results[label] = {
                "rc": check_rc,
                "summary": re.sub(r"\s+", " ", (check_out + "\n" + check_err).strip())[-500:],
            }
        print(json.dumps({
            "target_gate":{"key_type":gate["key_type"],"stable_second_connection":True},
            "checks":results,
            "all_pass":all(isinstance(value,dict) and value.get("rc")==0 for value in results.values()),
            "no_source_server_connection":True,
        },ensure_ascii=False,indent=2))
    finally:
        client.close()


def daily_runtime_status_action() -> None:
    client, gate = connect()
    try:
        _root, _project = paths(client)
        root, project = paths(client)
        common = project + "/scripts/daily_update/_common.sh"
        cli_command = f"source {q(common)}; exec \"$PYTHON\" -B -m epidemic_core.daily.cli status"
        cli_payload = json.loads(checked(client, f"bash -c {q(cli_command)}", timeout=120))
        runs = cli_payload.get("runs", []) if isinstance(cli_payload, dict) else []
        if not isinstance(runs, list):
            raise GateError("DAILY_STATUS_CLI_INVALID")
        formal_counts_sql = "SELECT json_build_object(" + ",".join(
            f"'{name}',(SELECT count(*) FROM epidemic.{name})" for name in EXPECTED_COUNTS
        ) + ")::text"
        formal_counts = json.loads(db_query(client, formal_counts_sql))
        snapshots = json.loads(db_query(client, "SELECT COALESCE(json_agg(json_build_object('sha256',content_sha256,'row_count',row_count) ORDER BY id),'[]'::json)::text FROM epidemic.dataset_snapshots"))
        active = {"QUEUED","CLAIMED","FETCHING","NORMALIZING","SNAPSHOTTING","FAILED_RETRYABLE"}
        sanitized_runs = []
        sanitized_schedules: list[dict[str, object]] = []
        for row in runs:
            copy = dict(row)
            run_id = copy.pop("run_id", None)
            schedule_id = copy.pop("schedule_id", None)
            copy["run_id_sha256"] = sha_text(str(run_id)) if run_id else None
            copy["schedule_id_sha256"] = sha_text(str(schedule_id)) if schedule_id else None
            sanitized_runs.append(copy)
        snapshot_map = {row["sha256"]:row["row_count"] for row in snapshots}
        print(json.dumps({
            "target_gate":{"key_type":gate["key_type"],"stable_second_connection":True},
            "daily_runs":sanitized_runs,
            "daily_schedule_count":cli_payload.get("schedules"),
            "active_run_count":sum(1 for row in runs if row.get("state") in active),
            "formal_counts":formal_counts,
            "formal_counts_match_preflight":formal_counts==EXPECTED_COUNTS,
            "old_snapshots_unchanged":all(snapshot_map.get(key)==rows for key,rows in EXPECTED_SNAPSHOTS.items()),
            "snapshot_count":len(snapshots),
            "no_source_server_connection":True,
        },ensure_ascii=False,indent=2))
    finally:
        client.close()


def daily_schema_inspect_action() -> None:
    client, gate = connect()
    try:
        relations = json.loads(db_query(client, """
SELECT COALESCE(json_agg(json_build_object('schema',n.nspname,'name',c.relname,'kind',c.relkind)
 ORDER BY n.nspname,c.relname),'[]'::json)::text
FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
WHERE c.relname LIKE '%daily%'
"""))
        columns = json.loads(db_query(client, """
SELECT COALESCE(json_agg(json_build_object('schema',n.nspname,'table',c.relname,'column',a.attname,'type',pg_catalog.format_type(a.atttypid,a.atttypmod))
 ORDER BY n.nspname,c.relname,a.attnum),'[]'::json)::text
FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
WHERE c.relname LIKE '%daily%'
"""))
        print(json.dumps({
            "target_gate":{"key_type":gate["key_type"],"stable_second_connection":True},
            "relations":relations,
            "columns":columns,
            "no_source_server_connection":True,
        },indent=2))
    finally:
        client.close()


def p1_8a_path_drift_action() -> None:
    client, gate = connect()
    try:
        _root, project = paths(client)
        scan_code = r'''import hashlib,json,os,pathlib
project=pathlib.Path(os.environ["PROJECT"]);files=json.loads(os.environ["FILES"]);rows=[]
for rel in files:
 path=project/rel
 if not path.is_file():continue
 for number,line in enumerate(path.read_text(errors="replace").splitlines(),1):
  kinds=[]
  if "USER_HOME/autodl-tmp" in line:kinds.append("legacy_root")
  if str(project.parent) in line:kinds.append("current_target_root")
  if kinds:rows.append({"path":rel,"line":number,"kinds":kinds,"line_sha256":hashlib.sha256(line.encode()).hexdigest(),"text":line.replace(str(project.parent),"[data-root]").replace("USER_HOME/autodl-tmp","[legacy-root]")[:400]})
print(json.dumps(rows))
'''
        root, _project = paths(client)
        rows = json.loads(checked(
            client,
            f"PROJECT={q(project)} FILES={q(json.dumps(P1_8_FILES))} {q(root + '/conda/envs/epidemic-core/bin/python')} -c {q(scan_code)}",
            timeout=300,
        ))
        print(json.dumps({
            "target_gate":{"key_type":gate["key_type"],"stable_second_connection":True},
            "old_root_references":rows,
            "count":len(rows),
            "no_source_server_connection":True,
        },indent=2))
    finally:
        client.close()


def isolated_full_test_action() -> None:
    client, gate=connect()
    try:
        root,project=paths(client);python=root+"/conda/envs/epidemic-core/bin/python";env_script=project+"/scripts/database/_env.sh"
        code=r'''import json,os,pathlib,re,secrets,shutil,socket,subprocess,sys
import sqlalchemy as sa
from sqlalchemy.engine import URL,make_url
project=pathlib.Path(os.environ["PROJECT"]);python=os.environ["PYTHON_BIN"]
pgbin=pathlib.Path(os.environ["POSTGRES_BIN"]);temp_root=pathlib.Path(os.environ["TMP_ROOT"])/("p1_8_isolated_pg_"+secrets.token_hex(6));data=temp_root/"data";log=temp_root/"postgres.log";socket_dir=pathlib.Path("/tmp")/("p18pg_"+secrets.token_hex(5))
source_urls={key:make_url(os.environ[key]) for key in ("MIGRATION_DATABASE_URL","APP_DATABASE_URL","RETRIEVAL_DATABASE_URL","NORMALIZATION_DATABASE_URL","REVIEW_DATABASE_URL","PROMOTION_DATABASE_URL") if os.environ.get(key)}
role_names={key:value.username for key,value in source_urls.items() if value.username}
checks=[];started=False;result=None
def run(label,args,env,timeout):
 p=subprocess.run(args,cwd=project,env=env,text=True,capture_output=True,timeout=timeout)
 text=(p.stdout+"\n"+p.stderr).replace(str(project),"[project]").replace(str(project.parent),"[data-root]")
 text=re.sub(r"(?m)(?:^|\s)(?:USER_HOME/\s]+|/root)(?=/|\s|$)"," [home]",text)
 text=re.sub(r"(?i)(password|token|secret|api[_-]?key)(\s*[=:]\s*)\S+",r"\1\2[redacted]",text)
 text=re.sub(r"(?:postgresql[^\s]+|https?://\S+)","[url-redacted]",text)
 checks.append({"name":label,"rc":p.returncode,"tail":text.splitlines()[-30:]})
 if p.returncode:
  print(json.dumps({"failure":label,"checks":checks}),file=sys.stderr)
  raise RuntimeError(label+" failed")
 return p
try:
 temp_root.mkdir(parents=True,mode=0o700);socket_dir.mkdir(mode=0o700);base_env=os.environ.copy();base_env["PATH"]=str(pgbin)+os.pathsep+base_env.get("PATH","")
 run("initdb",[str(pgbin/"initdb"),"-D",str(data),"-U","postgres","--auth-local=trust","--auth-host=scram-sha-256","--no-sync"],base_env,600)
 with socket.socket() as probe:probe.bind(("127.0.0.1",0));port=probe.getsockname()[1]
 run("postgres_start",[str(pgbin/"pg_ctl"),"-D",str(data),"-l",str(log),"-o",f"-k {socket_dir} -p {port} -h 127.0.0.1 -F","-w","start"],base_env,600);started=True
 admin_url=URL.create("postgresql+psycopg",username="postgres",database="postgres",query={"host":str(socket_dir),"port":str(port)});admin=sa.create_engine(admin_url,isolation_level="AUTOCOMMIT",hide_parameters=True)
 migration_text="\n".join(path.read_text(errors="replace") for path in (project/"migrations/versions").glob("*.py"));roles=set(re.findall(r"\bepidemic[a-z0-9_]+\b",migration_text));roles.update(role_names.values())
 roles={role for role in roles if role not in {"epidemic","epidemicopenclaw","postgres"} and len(role)<64}
 with admin.connect() as c:
  for role in sorted(roles):c.exec_driver_sql('CREATE ROLE "'+role+'" LOGIN')
  app_login=role_names["APP_DATABASE_URL"]
  if app_login!="epidemic_app":c.exec_driver_sql('GRANT epidemic_app TO "'+app_login+'"')
  if "REVIEW_DATABASE_URL" in role_names and role_names["REVIEW_DATABASE_URL"]!="epidemicreview":c.exec_driver_sql('GRANT epidemicreview TO "'+role_names["REVIEW_DATABASE_URL"]+'"')
  c.exec_driver_sql('CREATE DATABASE epidemic_p18_test TEMPLATE template0 ENCODING \'UTF8\'')
 test_migration=str(admin_url.set(database="epidemic_p18_test"));
 bootstrap=sa.create_engine(test_migration,isolation_level="AUTOCOMMIT",hide_parameters=True)
 with bootstrap.connect() as c:
  c.exec_driver_sql("CREATE SCHEMA epidemic")
  c.exec_driver_sql("GRANT USAGE ON SCHEMA epidemic TO epidemic_app")
 bootstrap.dispose()
 def role_url(role):return str(URL.create("postgresql+psycopg",username=role,database="epidemic_p18_test",query={"host":str(socket_dir),"port":str(port)}))
 app_user=role_names["APP_DATABASE_URL"];env=base_env.copy();env.update({"MIGRATION_DATABASE_URL":test_migration,"APP_DATABASE_URL":role_url(app_user),"DATABASE_URL":role_url(app_user),"PYTHONDONTWRITEBYTECODE":"1","PYTHONPATH":str(project/"src")})
 for key in ("RETRIEVAL_DATABASE_URL","NORMALIZATION_DATABASE_URL","REVIEW_DATABASE_URL","PROMOTION_DATABASE_URL"):
  if key in role_names:env[key]=role_url(role_names[key])
 run("alembic_upgrade",[python,"-m","alembic","upgrade","head"],env,900)
 run("seed_reference",[python,"-m","epidemic_core.cli","seed-reference"],env,300)
 run("pytest_full",[python,"-m","pytest","-p","no:cacheprovider","-vv"],env,3600)
 run("alembic_downgrade_one",[python,"-m","alembic","downgrade","-1"],env,900)
 run("alembic_reupgrade",[python,"-m","alembic","upgrade","head"],env,900)
 run("alembic_current",[python,"-m","alembic","current"],env,300)
 run("alembic_heads",[python,"-m","alembic","heads"],env,300)
 test_engine=sa.create_engine(test_migration,hide_parameters=True)
 with test_engine.connect() as c:
  version=c.execute(sa.text("SELECT version_num FROM epidemic.alembic_version")).scalar_one()
  daily_relations=c.execute(sa.text("SELECT count(*) FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='epidemic' AND c.relname IN ('daily_update_schedules','daily_update_runs','daily_update_run_events','daily_update_status_v1')")).scalar_one()
  discovery_relations=c.execute(sa.text("SELECT count(*) FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='epidemic_discovery' AND c.relname IN ('review_candidate_cards_v2','review_preview_v','review_discovery_runs_v2','review_candidate_fetches_v1','review_discovery_search_results_v1')")).scalar_one()
 test_engine.dispose()
 if version!="0015_p1_8b_reviewer_read_views" or daily_relations!=4 or discovery_relations!=5:raise RuntimeError("isolated schema verification failed")
 result={"checks":checks,"schema":{"version":version,"daily_relations":daily_relations,"discovery_relations":discovery_relations,"single_head":True}}
finally:
 if started:subprocess.run([str(pgbin/"pg_ctl"),"-D",str(data),"-m","fast","-w","stop"],env=os.environ.copy(),text=True,capture_output=True,timeout=300)
 if temp_root.exists():shutil.rmtree(temp_root)
 if socket_dir.exists():shutil.rmtree(socket_dir)
if result is None:raise RuntimeError("isolated test did not produce a result")
result["temporary_cluster_removed"]=not temp_root.exists() and not socket_dir.exists();print(json.dumps(result))
'''
        command=f"source {q(env_script)}; PROJECT={q(project)} PYTHON_BIN={q(python)} POSTGRES_BIN={q(root+'/conda/envs/postgresql18/bin')} TMP_ROOT={q(project+'/storage/tmp')} exec {q(python)} -B -c {q(code)}"
        rc,output,error=run(client,f"bash -c {q(command)}",timeout=5400)
        if rc:
            detail=re.sub(r"(?i)(password|token|secret|api[_-]?key)(\s*[=:]\s*)\S+",r"\1\2[redacted]",error)
            detail=re.sub(r"(?:postgresql[^\s]+|https?://\S+)","[url-redacted]",detail)
            raise GateError("ISOLATED_FULL_TEST_FAILED:"+re.sub(r"\s+"," ",detail)[-6000:])
        value=json.loads(output)
        print(json.dumps({
            "target_gate":{"key_type":gate["key_type"],"stable_second_connection":True},
            "isolated_test":value,
            "production_database_contact":"read-only gate outside isolated test",
            "no_source_server_connection":True,
        },ensure_ascii=False,indent=2))
    finally:client.close()


def reference_seed_inspect_action() -> None:
    client,gate=connect()
    try:
        _root,project=paths(client)
        code=r'''import json,os,pathlib,re
project=pathlib.Path(os.environ["PROJECT"]);rows=[]
for base in (project/"scripts",project/"src",project/"tests",project/"reference"):
 if not base.exists():continue
 for path in base.rglob("*"):
  if not path.is_file() or path.stat().st_size>1_000_000 or path.suffix.lower() not in {".py",".sh",".json",".md",".toml"}:continue
  rel=str(path.relative_to(project));text=path.read_text(errors="replace")
  for number,line in enumerate(text.splitlines(),1):
   if re.search(r"seed.{0,30}(countr|disease|metric|reference)|(countr|disease|metric|reference).{0,30}seed",line,re.I):rows.append({"path":rel,"line":number,"text":line.strip()[:400]})
print(json.dumps(rows[:300]))
'''
        root,_=paths(client);rows=json.loads(checked(client,f"PROJECT={q(project)} {q(root+'/conda/envs/epidemic-core/bin/python')} -c {q(code)}",timeout=300))
        files={}
        for relative in ("scripts/database/seed_reference_data.sh","src/epidemic_core/cli.py","src/epidemic_core/reference.py","tests/conftest.py","tests/integration/test_postgres.py"):
            if run(client,f"test -f {q(project+'/'+relative)}")[0]!=0:
                continue
            selection="150,320p" if relative=="tests/integration/test_postgres.py" else "1,220p"
            value=checked(client,f"sed -n {q(selection)} {q(project+'/'+relative)}")
            value=re.sub(r"(?i)(password|token|secret|api[_-]?key)(\s*[=:]\s*)\S+",r"\1\2[redacted]",value)
            files[relative]=value.splitlines()
        grant_code=r'''import json,os,pathlib,re
root=pathlib.Path(os.environ["PROJECT"])/"migrations/versions";rows=[]
for path in root.glob("*.py"):
 for number,line in enumerate(path.read_text(errors="replace").splitlines(),1):
  if re.search(r"GRANT|REVOKE",line,re.I):rows.append({"path":path.name,"line":number,"text":line.strip()[:500]})
print(json.dumps(rows))
'''
        root,_=paths(client);grants=json.loads(checked(client,f"PROJECT={q(project)} {q(root+'/conda/envs/epidemic-core/bin/python')} -c {q(grant_code)}"))
        print(json.dumps({"target_gate":{"key_type":gate["key_type"],"stable_second_connection":True},"matches":rows,"files":files,"grants":grants,"no_source_server_connection":True},ensure_ascii=False,indent=2))
    finally:client.close()


def metric_consistency_action() -> None:
    client,gate=connect()
    try:
        root,project=paths(client)
        database=json.loads(db_query(client,"SELECT COALESCE(json_agg(json_build_object('code',metric_code,'allow_negative',allow_negative,'min_value',min_value) ORDER BY metric_code),'[]'::json)::text FROM epidemic.metric_definitions"))
        code=r'''import hashlib,json,os,pathlib,re
project=pathlib.Path(os.environ["PROJECT"]);reference_source=(project/"src/epidemic_core/reference.py").read_text(errors="replace")
candidates=[]
for path in (project/"reference").rglob("*.json"):
 try:value=json.loads(path.read_text())
 except Exception:continue
 pools=[]
 if isinstance(value,list):pools.append(value)
 elif isinstance(value,dict):
  pools.extend(item for item in value.values() if isinstance(item,list))
 for pool in pools:
  rows=[row for row in pool if isinstance(row,dict) and ("metric_code" in row or "code" in row) and "allow_negative" in row]
  if not rows:continue
  normalized=[{"code":row.get("code") or row.get("metric_code"),"allow_negative":row.get("allow_negative"),"min_value":row.get("min_value")} for row in rows]
  candidates.append({"path":str(path.relative_to(project)),"sha256":hashlib.sha256(path.read_bytes()).hexdigest(),"rows":normalized})
references=[line.strip() for line in reference_source.splitlines() if re.search(r"metric|json|reference",line,re.I)][:80]
print(json.dumps({"candidates":candidates,"reference_source_lines":references}))
'''
        reference=json.loads(checked(client,f"PROJECT={q(project)} {q(root+'/conda/envs/epidemic-core/bin/python')} -c {q(code)}"))
        matching=[item for item in reference["candidates"] if item["rows"]==database]
        print(json.dumps({"target_gate":{"key_type":gate["key_type"],"stable_second_connection":True},"production_database":database,"reference_candidates":reference["candidates"],"matching_paths":[item["path"] for item in matching],"reference_source_lines":reference["reference_source_lines"],"no_source_server_connection":True},indent=2))
    finally:client.close()


def negative_contract_inspect_action() -> None:
    client,gate=connect()
    try:
        root,project=paths(client)
        code=r'''import json,os,pathlib,re
project=pathlib.Path(os.environ["PROJECT"]);rows=[]
for base in (project/"migrations/versions",project/"tests",project/"src",project/"scripts"):
 if not base.exists():continue
 for path in base.rglob("*"):
  if not path.is_file() or path.stat().st_size>1_000_000 or path.suffix.lower() not in {".py",".sql",".sh"}:continue
  lines=path.read_text(errors="replace").splitlines()
  hits=[i for i,line in enumerate(lines) if re.search(r"allow_negative|disallows negative|cases_new|cases_cumulative|metric_definitions",line,re.I)]
  if not hits:continue
  selected=set()
  for i in hits:
   selected.update(range(max(0,i-5),min(len(lines),i+8)))
  rows.append({"path":str(path.relative_to(project)),"lines":[{"n":i+1,"text":lines[i].strip()[:500]} for i in sorted(selected)]})
print(json.dumps(rows))
'''
        rows=json.loads(checked(client,f"PROJECT={q(project)} {q(root+'/conda/envs/epidemic-core/bin/python')} -c {q(code)}",timeout=300))
        print(json.dumps({"target_gate":{"key_type":gate["key_type"],"stable_second_connection":True},"matches":rows,"no_source_server_connection":True},indent=2))
    finally:client.close()


def negative_contract_compact_action() -> None:
    client,gate=connect()
    try:
        root,project=paths(client)
        code=r'''import json,os,pathlib,re,subprocess
project=pathlib.Path(os.environ["PROJECT"]);result={}
targets=[project/"src/epidemic_core/reference.py",project/"tests/integration/test_postgres.py"]
targets.extend(sorted((project/"migrations/versions").glob("*.py")))
for path in targets:
 lines=path.read_text(errors="replace").splitlines();hits=[]
 for i,line in enumerate(lines):
  if re.search(r"allow_negative|cases_new|cases_cumulative|metrics\.v[12]|active_metrics",line,re.I):
   hits.append({"n":i+1,"text":line.strip()[:500]})
 if hits:result[str(path.relative_to(project))]=hits
history={}
for relative in ["reference/metrics.v1.json","reference/metrics.v2.json","reference/active_catalogs.v2.json","src/epidemic_core/reference.py","tests/integration/test_postgres.py"]:
 proc=subprocess.run(["git","log","-8","--format=%H%x09%s","--",relative],cwd=project,text=True,capture_output=True)
 history[relative]=proc.stdout.splitlines()
excerpts={}
for relative,start,end in [("src/epidemic_core/reference.py",100,180),("tests/integration/test_postgres.py",160,290)]:
 lines=(project/relative).read_text(errors="replace").splitlines()
 excerpts[relative]=[{"n":i+1,"text":lines[i]} for i in range(start-1,min(end,len(lines)))]
print(json.dumps({"matches":result,"history":history,"excerpts":excerpts}))
'''
        value=json.loads(checked(client,f"PROJECT={q(project)} {q(root+'/conda/envs/epidemic-core/bin/python')} -c {q(code)}",timeout=300))
        print(json.dumps({"target_gate":{"key_type":gate["key_type"],"stable_second_connection":True},**value,"no_source_server_connection":True},indent=2))
    finally:client.close()


def fix_reference_revision_gate_action() -> None:
    client,gate=connect()
    try:
        _root,project=paths(client)
        relative="src/epidemic_core/reference.py";remote=project+"/"+relative
        sftp=client.open_sftp()
        try:
            with sftp.file(remote,"r") as handle:before=handle.read().decode("utf-8")
            old='''    if alembic_version == "0003_p1_4_normalization_pipeline":\n'''
            new='''    alembic_sequence = alembic_version.partition("_")[0]\n    if alembic_sequence.isdigit() and int(alembic_sequence) >= 3:\n'''
            old_manifest='''            manifest.get("minimum_alembic") != alembic_version\n'''
            new_manifest='''            manifest.get("minimum_alembic") != "0003_p1_4_normalization_pipeline"\n'''
            if before.count(old)!=1 or before.count(old_manifest)!=1:raise GateError("REFERENCE_GATE_SOURCE_DRIFT")
            after=before.replace(old,new).replace(old_manifest,new_manifest)
            timestamp=dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup=project+"/storage/tmp/p1_8ab_reference_gate_"+timestamp+".py"
            with sftp.file(backup,"w") as handle:handle.write(before.encode("utf-8"))
            sftp.chmod(backup,0o600)
            with sftp.file(remote,"w") as handle:handle.write(after.encode("utf-8"))
            sftp.chmod(remote,0o644)
        finally:sftp.close()
        syntax=run(client,f"{q(project+'/../conda/envs/epidemic-core/bin/python')} -m py_compile {q(remote)}")
        if syntax[0]:raise GateError("REFERENCE_GATE_SYNTAX_FAILED")
        diff=checked(client,f"cd {q(project)} && git diff --check -- {q(relative)} && git diff --numstat -- {q(relative)}")
        print(json.dumps({"target_gate":{"key_type":gate["key_type"],"stable_second_connection":True},"file":relative,"before_sha256":sha_text(before),"after_sha256":sha_text(after),"backup_mode":"0600","syntax":True,"diff_numstat":diff.splitlines()[-1] if diff else "","no_source_server_connection":True},indent=2))
    finally:client.close()


def runtime_command_inventory_action() -> None:
    client,gate=connect()
    try:
        root,project=paths(client)
        files=checked(client,f"find {q(project+'/scripts')} -maxdepth 3 -type f \\( -iname '*node*' -o -iname '*npm*' -o -iname '*openclaw*' \\) -printf '%P\n' | sort")
        review_files=checked(client,f"find {q(project+'/scripts/source_review')} -maxdepth 2 -type f -printf '%f\n' | sort")
        review_status=checked(client,f"sed -n '1,240p' {q(project+'/scripts/source_review/status_source_review.sh')}").replace(project,"[project]")
        review_common=checked(client,f"sed -n '1,260p' {q(project+'/scripts/source_review/_common.sh')}")
        review_common=re.sub(r"(?im)^([A-Z0-9_]*(?:URL|PASS|TOKEN|SECRET|KEY)[A-Z0-9_]*\s*=).*$",r"\1[redacted]",review_common).replace(root,"[data-root]").replace(project,"[project]")
        cli=project+"/scripts/openclaw/openclaw_cli.sh"
        commands={
            "openclaw_version":f"{q(cli)} --version",
            "doctor_help":f"{q(cli)} doctor --help",
            "security_help":f"{q(cli)} security --help",
            "security_audit_help":f"{q(cli)} security audit --help",
            "npm_path":f"bash -lc 'command -v npm || true'",
            "node_path":f"bash -lc 'command -v node || true'",
            "project_node_candidates":f"find {q(root)} -maxdepth 5 -type f \\( -name node -o -name npm -o -name npm-cli.js \\) -printf '%p\n' 2>/dev/null | head -30",
            "review_env_names":f"sed -n -E 's/^([A-Z][A-Z0-9_]*)=.*/\\1/p' {q(project+'/scripts/source_review/_common.sh')} | sort",
            "review_secret_env_names":f"bash -c {q('source '+q(project+'/scripts/source_review/_common.sh')+'; sed -n -E \'s/^([A-Z][A-Z0-9_]*)=.*/\\1/p\' \"$CONFIG\" | sort')}",
        }
        result={}
        for label,command in commands.items():
            rc,out,err=run(client,command,timeout=180)
            value=re.sub(r"(?i)(password|token|secret|api[_-]?key)(\s*[=:]\s*)\S+",r"\1\2[redacted]",(out+"\n"+err).strip())
            value=re.sub(r"(?:postgresql[^\s]+|https?://\S+)","[url-redacted]",value)
            value=value.replace(root,"[data-root]").replace(project,"[project]")
            result[label]={"rc":rc,"lines":value.splitlines()[:160]}
        print(json.dumps({"target_gate":{"key_type":gate["key_type"],"stable_second_connection":True},"script_files":files.splitlines(),"source_review_files":review_files.splitlines(),"source_review_status":review_status.splitlines(),"source_review_common":review_common.splitlines(),"commands":result,"no_source_server_connection":True},indent=2))
    finally:client.close()


def p1_8a_final_audit_action() -> None:
    client,gate=connect()
    try:
        root,project=paths(client);python=root+"/conda/envs/epidemic-core/bin/python";cli=project+"/scripts/openclaw/openclaw_cli.sh"
        def command_check(label: str, command: str, timeout: int=900) -> dict[str, object]:
            started=time.monotonic();rc,out,err=run(client,f"bash -c {q(command)}",timeout=timeout)
            combined=(out+"\n"+err).strip();combined=re.sub(r"(?i)(password|token|secret|api[_-]?key)(\s*[=:]\s*)\S+",r"\1\2[redacted]",combined)
            combined=re.sub(r"(?:postgresql[^\s]+|https?://\S+)","[url-redacted]",combined)
            return {"label":label,"rc":rc,"duration_seconds":round(time.monotonic()-started,3),"output_sha256":sha_text(combined),"tail":combined.splitlines()[-20:]}
        checks=[]
        checks.append(command_check("ruff_check_project",f"cd {q(project)} && {q(python)} -m ruff check src tests migrations",1200))
        checks.append(command_check("ruff_format_project",f"cd {q(project)} && {q(python)} -m ruff format --check src tests migrations",1200))
        checks.append(command_check("mypy_strict",f"cd {q(project)} && {q(python)} -m mypy --strict src",1800))
        checks.append(command_check("pip_check",f"cd {q(project)} && {q(python)} -m pip check",600))
        npm=root+"/epidemic-openclaw/runtime/node-v24.18.0-linux-x64/bin/npm"
        node_bin=root+"/epidemic-openclaw/runtime/node-v24.18.0-linux-x64/bin"
        npm_dir=project+"/integrations/openclaw/epidemic-query-tools"
        npm_prefix=f"export PATH={q(node_bin)}:$PATH; cd {q(npm_dir)} &&"
        checks.append(command_check("npm_build",f"{npm_prefix} {q(npm)} run build",1200))
        checks.append(command_check("npm_test",f"{npm_prefix} {q(npm)} test",1200))
        npm_audit=command_check("npm_audit",f"{npm_prefix} {q(npm)} audit --json",1200)
        npm_audit_summary={"rc":npm_audit["rc"],"duration_seconds":npm_audit["duration_seconds"],"output_sha256":npm_audit["output_sha256"]}
        rc,out,err=run(client,f"bash -c {q(npm_prefix+' '+q(npm)+' audit --json')}",timeout=1200)
        try:
            audit_json=json.loads(out);npm_audit_summary["vulnerabilities"]=(audit_json.get("metadata",{}).get("vulnerabilities",{}))
        except Exception:npm_audit_summary["parseable"]=False
        doctor=command_check("openclaw_doctor_lint",f"{q(cli)} doctor --lint --json",600)
        doctor_deep=command_check("openclaw_doctor_deep",f"{q(cli)} doctor --deep --non-interactive",900)
        rc,out,err=run(client,f"{q(cli)} security audit --deep --json",timeout=900)
        security_value: dict[str, object]={"rc":rc,"duration_seconds":None,"output_sha256":sha_text(out+err)}
        try:
            parsed=json.loads(out);summary=parsed.get("summary",{}) if isinstance(parsed,dict) else {}
            findings=parsed.get("findings",[]) if isinstance(parsed,dict) else []
            security_value.update({"parseable":True,"summary":summary,"findings":[{"id":row.get("id"),"severity":row.get("severity"),"title":row.get("title")} for row in findings if isinstance(row,dict)]})
        except Exception:security_value.update({"parseable":False,"line_count":len((out+err).splitlines())})

        db_code=r'''import json,os,sqlalchemy as sa
from sqlalchemy import text
result={}
migration=sa.create_engine(os.environ["MIGRATION_DATABASE_URL"],hide_parameters=True)
app=sa.create_engine(os.environ["APP_DATABASE_URL"],hide_parameters=True)
with migration.connect() as c:
 result["alembic"]=c.execute(text("SELECT version_num FROM epidemic.alembic_version")).scalar_one()
 result["formal_counts"]={name:c.exec_driver_sql('SELECT count(*) FROM epidemic."'+name+'"').scalar_one() for name in json.loads(os.environ["FORMAL_NAMES"])}
 result["snapshots"]=[{"sha256":row[0],"row_count":row[1]} for row in c.execute(text("SELECT content_sha256,row_count FROM epidemic.dataset_snapshots ORDER BY id"))]
with app.connect() as c:
 result["app_privileges"]={"formal_insert":bool(c.execute(text("SELECT has_table_privilege(current_user,'epidemic.normalized_observations','INSERT')")).scalar_one()),"snapshot_insert":bool(c.execute(text("SELECT has_table_privilege(current_user,'epidemic.dataset_snapshots','INSERT')")).scalar_one()),"discovery_direct_insert":bool(c.execute(text("SELECT has_table_privilege(current_user,'epidemic_discovery.source_candidates','INSERT')")).scalar_one())}
print(json.dumps(result))
'''
        env_script=project+"/scripts/database/_env.sh"
        command=f"source {q(env_script)}; FORMAL_NAMES={q(json.dumps(list(EXPECTED_COUNTS)))} {q(python)} -B -c {q(db_code)}"
        db_value=json.loads(checked(client,f"bash -c {q(command)}",timeout=600))
        snapshot_map={row["sha256"]:row["row_count"] for row in db_value["snapshots"]}
        db_value["formal_counts_match"]=db_value["formal_counts"]==EXPECTED_COUNTS
        db_value["old_snapshots_unchanged"]=snapshot_map==EXPECTED_SNAPSHOTS

        service_paths={"postgres":"scripts/postgres/status.sh","vllm":"scripts/qwen_vllm/status.sh","query_api":"scripts/query_api/status_query_api.sh","retrieval_worker":"scripts/retrieval_worker/status_retrieval_worker.sh","openclaw":"scripts/openclaw/status_openclaw.sh","dashboard":"scripts/dashboard/status_dashboard.sh","discovery_worker":"scripts/source_discovery_worker/status_source_discovery_worker.sh","promotion_worker":"scripts/source_promotion_worker/status_source_promotion_worker.sh","review_console":"scripts/source_review/status_source_review.sh","daily_update":"scripts/daily_update/status_daily_update.sh"}
        services={name:run(client,q(project+"/"+relative),timeout=180)[0]==0 for name,relative in service_paths.items()}
        runtime_code=r'''import json,os,pathlib,re
ports={5432,8000,8010,18789,8020,8021};listeners=[]
for filename,v6 in (("/proc/net/tcp",False),("/proc/net/tcp6",True)):
 for line in pathlib.Path(filename).read_text().splitlines()[1:]:
  parts=line.split();address,port_hex=parts[1].split(":");port=int(port_hex,16)
  if port in ports and parts[3]=="0A":listeners.append({"port":port,"loopback":address==("00000000000000000000000001000000" if v6 else "0100007F")})
project=os.environ["PROJECT"].encode();project_processes=0;proxy_processes=0
for proc in pathlib.Path("/proc").iterdir():
 if not proc.name.isdigit():continue
 try:
  cmd=(proc/"cmdline").read_bytes();env=(proc/"environ").read_bytes()
 except Exception:continue
 if project not in cmd:continue
 project_processes+=1
 names={item.split(b"=",1)[0] for item in env.split(b"\0") if b"=" in item}
 if names & {b"http_proxy",b"https_proxy",b"HTTP_PROXY",b"HTTPS_PROXY",b"all_proxy",b"ALL_PROXY"}:proxy_processes+=1
print(json.dumps({"listeners":listeners,"project_processes":project_processes,"proxy_processes":proxy_processes}))
'''
        runtime=json.loads(checked(client,f"PROJECT={q(project)} {q(python)} -B -c {q(runtime_code)}"))
        runtime["new_session_proxy_absent"]=checked(client,"bash -lc '[[ -z ${HTTP_PROXY:-}${HTTPS_PROXY:-}${ALL_PROXY:-}${http_proxy:-}${https_proxy:-}${all_proxy:-} ]] && echo yes || echo no'")=="yes"

        scan_code=r'''import hashlib,json,os,pathlib,re,subprocess
project=pathlib.Path(os.environ["PROJECT"]);files=json.loads(os.environ["FILES"]);findings=[];large=[]
rules={"private_key":r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----","cloud_token":r"(?:AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9_]{30,}|sk-[A-Za-z0-9]{24,})","credential_url":r"(?:postgresql|https?)://[^\s:/]+:[^\s/@]+@","tls_bypass":r"curl\s+-k(?:\s|$)|wget\s+--no-check-certificate|GIT_SSL_NO_VERIFY\s*=\s*(?:1|true)|pip\s+[^\n]*--trusted-host","proxy_literal":r"(?i)(?:http|https|all)_proxy\s*=\s*['\"]?(?:https?|socks)://"}
for rel in files:
 path=project/rel
 if not path.is_file():continue
 size=path.stat().st_size
 if size>10*1024*1024:large.append({"path":rel,"bytes":size})
 text=path.read_text(errors="replace")
 for name,pattern in rules.items():
  for match in re.finditer(pattern,text,re.I|re.M):
   line=text.count("\n",0,match.start())+1;findings.append({"path":rel,"line":line,"rule":name,"line_sha256":hashlib.sha256(text.splitlines()[line-1].encode()).hexdigest()})
changed=subprocess.run(["git","-C",str(project),"status","--porcelain=v1","-uall"],text=True,capture_output=True,check=True).stdout.splitlines()
forbidden=[line[3:] for line in changed if line[3:].endswith((".env",".pem",".key",".sqlite",".db",".pid")) or "服务器信息" in line[3:]]
print(json.dumps({"findings":findings,"large_files":large,"forbidden_changed_paths":forbidden}))
'''
        scan=json.loads(checked(client,f"PROJECT={q(project)} FILES={q(json.dumps(P1_8_FILES))} {q(python)} -B -c {q(scan_code)}",timeout=600))
        git={"head":checked(client,f"cd {q(project)} && git rev-parse HEAD"),"branch":checked(client,f"cd {q(project)} && git branch --show-current"),"diff_check_rc":run(client,f"cd {q(project)} && git diff --check",timeout=600)[0],"staged_paths":checked(client,f"cd {q(project)} && git diff --cached --name-only").splitlines()}

        jobs=_cron_jobs(client,cli);named=[row for row in jobs if row.get("name")=="epidemic-daily-update-v1"]
        cron_job=_cron_job_summary(named[0],project) if len(named)==1 else None
        if isinstance(cron_job,dict):
            cron_job["cwd"]="[project]" if cron_job.get("cwd")==project else "[unexpected-path]"
            cron_job["argv"]=["[project]/scripts/daily_update/reconcile_due.sh" if item==project+"/scripts/daily_update/reconcile_due.sh" else item for item in cron_job.get("argv",[]) ]
        cron={"total_jobs":len(jobs),"named_count":len(named),"job":cron_job}
        devices=_load_device_rows(client,cli);paired=devices.get("paired",[]) if isinstance(devices,dict) else []
        device={"pending_count":len(devices.get("pending",[])) if isinstance(devices,dict) and isinstance(devices.get("pending",[]),list) else None,"paired_count":len(paired) if isinstance(paired,list) else None,"admin_scope_present":bool(isinstance(paired,list) and any("operator.admin" in (row.get("scopes") or row.get("approvedScopes") or []) for row in paired if isinstance(row,dict)))}
        result={"target_gate":{"key_type":gate["key_type"],"stable_second_connection":True},"checks":checks,"npm_audit":npm_audit_summary,"openclaw":{"doctor_lint":doctor,"doctor_deep":doctor_deep,"security_deep":security_value},"database":db_value,"services":services,"runtime":runtime,"scan":scan,"git":git,"cron":cron,"device_authorization":device,"no_source_server_connection":True}
        result["required_pass"]=(all(row["rc"]==0 for row in checks) and npm_audit_summary.get("rc")==0 and doctor.get("rc") in (0,1) and doctor_deep.get("rc")==0 and rc==0 and db_value["formal_counts_match"] and db_value["old_snapshots_unchanged"] and all(services.values()) and runtime["new_session_proxy_absent"] and runtime["proxy_processes"]==0 and all(row["loopback"] for row in runtime["listeners"]) and scan=={"findings":[],"large_files":[],"forbidden_changed_paths":[]} and git["diff_check_rc"]==0 and not git["staged_paths"] and cron["named_count"]==1 and device["admin_scope_present"])
        print(json.dumps(result,indent=2))
    finally:client.close()


def p1_8a_diff_summary_action() -> None:
    client,gate=connect()
    try:
        root,project=paths(client);python=root+"/conda/envs/epidemic-core/bin/python"
        code=r'''import hashlib,json,os,pathlib,re,subprocess
project=pathlib.Path(os.environ["PROJECT"]);files=json.loads(os.environ["FILES"]);rows=[]
status=subprocess.run(["git","-C",str(project),"status","--porcelain=v1","-uall","--",*files],text=True,capture_output=True,check=True).stdout.splitlines()
status_map={line[3:]:line[:2] for line in status}
for rel in files:
 if rel not in status_map:continue
 proc=subprocess.run(["git","-C",str(project),"diff","--unified=0","--",rel],text=True,capture_output=True,check=True);diff=proc.stdout
 lines=[];counts={"daily":0,"path_only":0,"other":0}
 for line in diff.splitlines():
  if not line.startswith(("+","-")) or line.startswith(("+++","---")):continue
  body=line[1:]
  if re.search(r"daily[_ -]?update|reconcile[_ -]?due|startup catch|missed_intervals|p1-8|daily-status|daily-retry",body,re.I):kind="daily"
  elif "USER_HOME/autodl-tmp" in body or str(project) in body or str(project.parent) in body:kind="path_only"
  else:kind="other"
  counts[kind]+=1
  if len(lines)<12:lines.append({"sign":line[0],"kind":kind,"text":body.replace(str(project),"[project]").replace(str(project.parent),"[data-root]").replace("USER_HOME/autodl-tmp/epidemic-openclaw","[legacy-project]")[:400]})
 path=project/rel;sha=hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
 rows.append({"path":rel,"status":status_map[rel],"bytes":path.stat().st_size if path.is_file() else None,"sha256":sha,"diff_sha256":hashlib.sha256(diff.encode()).hexdigest(),"changed_line_classes":counts,"examples":lines})
print(json.dumps(rows))
'''
        rows=json.loads(checked(client,f"PROJECT={q(project)} FILES={q(json.dumps(P1_8_FILES))} {q(python)} -B -c {q(code)}",timeout=900))
        print(json.dumps({"target_gate":{"key_type":gate["key_type"],"stable_second_connection":True},"files":rows,"count":len(rows),"no_source_server_connection":True},indent=2))
    finally:client.close()


def portable_p1_8_paths_action() -> None:
    client,gate=connect()
    try:
        root,project=paths(client);timestamp=dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup=project+"/storage/tmp/p1_8ab_portable_paths_"+timestamp
        checked(client,f"umask 077; mkdir -p {q(backup)}; chmod 700 {q(backup)}")
        python_parents={"src/epidemic_core/daily/cli.py":3,"scripts/openclaw/configure_source_discovery_agent.py":2,"tests/integration/test_daily_update_contract.py":2}
        python_special=("src/epidemic_core/daily/main.py",)
        shell_files=["scripts/daily_update/_common.sh","scripts/daily_update/health_check.sh","scripts/daily_update/install_openclaw_cron.sh","scripts/daily_update/reconcile_due.sh","scripts/daily_update/restart_daily_update.sh","scripts/daily_update/seed_schedules.sh","scripts/daily_update/start_daily_update.sh","scripts/daily_update/status_daily_update.sh","scripts/daily_update/stop_daily_update.sh","scripts/services/start_all.sh","scripts/services/status_all.sh","scripts/services/stop_all.sh"]
        sftp=client.open_sftp();changes=[]
        try:
            for relative,parent_count in python_parents.items():
                remote=project+"/"+relative
                with sftp.file(remote,"r") as handle:before=handle.read().decode("utf-8")
                mode=sftp.stat(remote).st_mode & 0o777
                replacement=f"Path(__file__).resolve().parents[{parent_count}]"
                after,count=re.subn(r"Path\([\"']"+re.escape(project)+r"[\"']\)",replacement,before)
                if count==0 and replacement in before:continue
                if count!=1:raise GateError("PORTABLE_PYTHON_PATH_SOURCE_DRIFT:"+relative)
                backup_remote=backup+"/"+relative.replace("/","__")
                with sftp.file(backup_remote,"w") as handle:handle.write(before.encode())
                sftp.chmod(backup_remote,0o600)
                with sftp.file(remote,"w") as handle:handle.write(after.encode())
                sftp.chmod(remote,mode);changes.append({"path":relative,"replacements":count,"before_sha256":sha_text(before),"after_sha256":sha_text(after)})
            for relative in python_special:
                remote=project+"/"+relative
                with sftp.file(remote,"r") as handle:before=handle.read().decode("utf-8")
                mode=sftp.stat(remote).st_mode & 0o777
                after,count=re.subn(r"Path\([\"']"+re.escape(project)+r"/reference/data_sources\.v2\.json[\"']\)",'Path(__file__).resolve().parents[3] / "reference/data_sources.v2.json"',before)
                if count==0 and 'Path(__file__).resolve().parents[3] / "reference/data_sources.v2.json"' in before:continue
                if count!=1:raise GateError("PORTABLE_PYTHON_PATH_SOURCE_DRIFT:"+relative)
                backup_remote=backup+"/"+relative.replace("/","__")
                with sftp.file(backup_remote,"w") as handle:handle.write(before.encode())
                sftp.chmod(backup_remote,0o600)
                with sftp.file(remote,"w") as handle:handle.write(after.encode())
                sftp.chmod(remote,mode);changes.append({"path":relative,"replacements":count,"before_sha256":sha_text(before),"after_sha256":sha_text(after)})
            for relative in shell_files:
                remote=project+"/"+relative
                with sftp.file(remote,"r") as handle:before=handle.read().decode("utf-8")
                mode=sftp.stat(remote).st_mode & 0o777;after=before
                if relative in {"scripts/daily_update/_common.sh","scripts/daily_update/install_openclaw_cron.sh","scripts/daily_update/seed_schedules.sh"}:
                    after,count=re.subn(r"(?m)^PROJECT=['\"]?"+re.escape(project)+r"['\"]?$",'PROJECT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)',after)
                    if relative=="scripts/daily_update/install_openclaw_cron.sh":
                        after,argv_count=re.subn(r"(?m)^ARGV=.*$",'ARGV="[\\\"$PROJECT/scripts/daily_update/reconcile_due.sh\\\",\\\"--trigger\\\",\\\"openclaw-cron\\\"]"',after)
                        if argv_count!=1:raise GateError("PORTABLE_CRON_ARGV_SOURCE_DRIFT")
                elif relative in {"scripts/daily_update/health_check.sh","scripts/daily_update/restart_daily_update.sh"}:
                    after,count=re.subn(r"(?m)^ROOT=['\"]?"+re.escape(project)+r"['\"]?$",'ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)',after)
                elif relative.startswith("scripts/daily_update/"):
                    source_re=r"(?m)^source ['\"]?"+re.escape(project)+r"/scripts/daily_update/_common\.sh['\"]?$"
                    after,count=re.subn(source_re,'SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)\nsource "$SCRIPT_DIR/_common.sh"',after)
                else:
                    after,count=re.subn(r"(?m)^ROOT=['\"]?"+re.escape(project)+r"['\"]?$",'ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)',after)
                if count!=1 or project in after:raise GateError("PORTABLE_SHELL_PATH_SOURCE_DRIFT:"+relative)
                backup_remote=backup+"/"+relative.replace("/","__")
                with sftp.file(backup_remote,"w") as handle:handle.write(before.encode())
                sftp.chmod(backup_remote,0o600)
                with sftp.file(remote,"w") as handle:handle.write(after.encode())
                sftp.chmod(remote,mode);changes.append({"path":relative,"replacements":count,"before_sha256":sha_text(before),"after_sha256":sha_text(after)})
        finally:sftp.close()
        python=root+"/conda/envs/epidemic-core/bin/python"
        py_files=" ".join(q(project+"/"+path) for path in [*python_parents,*python_special])
        shell_paths=" ".join(q(project+"/"+path) for path in shell_files)
        checks={
            "python_syntax":run(client,f"{q(python)} -m py_compile {py_files}",timeout=300)[0],
            "bash_syntax":run(client,f"bash -n {shell_paths}",timeout=300)[0],
            "diff_check":run(client,f"cd {q(project)} && git diff --check -- "+" ".join(q(path) for path in [*python_parents,*shell_files]),timeout=300)[0],
        }
        if any(checks.values()):raise GateError("PORTABLE_PATH_CHECK_FAILED:"+json.dumps(checks))
        print(json.dumps({"target_gate":{"key_type":gate["key_type"],"stable_second_connection":True},"backup":"storage/tmp/"+backup.rsplit("/",1)[-1],"backup_mode":"0600","changes":changes,"checks":{key:True for key in checks},"no_source_server_connection":True},indent=2))
    finally:client.close()


def p1_8a_stage_action() -> None:
    client,gate=connect()
    try:
        root,project=paths(client);python=root+"/conda/envs/epidemic-core/bin/python"
        if checked(client,f"cd {q(project)} && git rev-parse HEAD")!=EXPECTED_HEAD:raise GateError("P1_8A_STAGE_HEAD_DRIFT")
        if checked(client,f"cd {q(project)} && git diff --cached --name-only"):raise GateError("P1_8A_STAGE_NOT_EMPTY")
        status=checked(client,f"cd {q(project)} && git status --porcelain=v1 -uall -- "+" ".join(q(path) for path in P1_8_FILES)).splitlines()
        changed={line[3:] for line in status}
        expected={path for path in P1_8_FILES if path in changed}
        minimum={"config/daily_update.json","migrations/versions/0010_p1_8_daily_updates.py","src/epidemic_core/daily/service.py","src/epidemic_core/daily/worker.py","scripts/daily_update/install_openclaw_cron.sh","tests/integration/test_daily_update_contract.py"}
        if not minimum.issubset(expected):raise GateError("P1_8A_STAGE_REQUIRED_FILE_MISSING")
        checked(client,f"cd {q(project)} && git add -- "+" ".join(q(path) for path in P1_8_FILES),timeout=900)
        staged=checked(client,f"cd {q(project)} && git diff --cached --name-only").splitlines()
        if set(staged)!=expected:raise GateError("P1_8A_STAGED_PATH_SET_INVALID")
        if run(client,f"cd {q(project)} && git diff --cached --check",timeout=600)[0]:raise GateError("P1_8A_CACHED_DIFF_CHECK_FAILED")
        scan_code=r'''import hashlib,json,os,pathlib,re,subprocess
project=pathlib.Path(os.environ["PROJECT"]);files=json.loads(os.environ["FILES"]);findings=[];large=[]
rules={"private_key":r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----","cloud_token":r"(?:AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9_]{30,}|sk-[A-Za-z0-9]{24,})","credential_url":r"(?:postgresql|https?)://[^\s:/]+:[^\s/@]+@","tls_bypass":r"curl\s+-k(?:\s|$)|wget\s+--no-check-certificate|GIT_SSL_NO_VERIFY\s*=\s*(?:1|true)|pip\s+[^\n]*--trusted-host","identity_path":r"(?:USER_HOME"}
for rel in files:
 path=project/rel
 if path.stat().st_size>10*1024*1024:large.append({"path":rel,"bytes":path.stat().st_size})
 text=path.read_text(errors="replace")
 for name,pattern in rules.items():
  for match in re.finditer(pattern,text,re.I|re.M):
   line=text.count("\n",0,match.start())+1;findings.append({"path":rel,"line":line,"rule":name,"line_sha256":hashlib.sha256(text.splitlines()[line-1].encode()).hexdigest()})
diff=subprocess.run(["git","-C",str(project),"diff","--cached","--binary"],capture_output=True,check=True).stdout
print(json.dumps({"findings":findings,"large":large,"diff_sha256":hashlib.sha256(diff).hexdigest(),"diff_bytes":len(diff)}))
'''
        scan=json.loads(checked(client,f"PROJECT={q(project)} FILES={q(json.dumps(P1_8_FILES))} {q(python)} -B -c {q(scan_code)}",timeout=900))
        if scan["findings"] or scan["large"]:raise GateError("P1_8A_STAGED_SECURITY_SCAN_FAILED")
        unstaged=checked(client,f"cd {q(project)} && git diff --name-only").splitlines()
        print(json.dumps({"target_gate":{"key_type":gate["key_type"],"stable_second_connection":True},"head":EXPECTED_HEAD,"staged_paths":staged,"staged_count":len(staged),"scan":scan,"unstaged_paths_preserved_count":len(unstaged),"no_source_server_connection":True},indent=2))
    finally:client.close()


def p1_8a_commit_action() -> None:
    client,gate=connect()
    try:
        _root,project=paths(client);message="feat(p1-8): add reliable daily epidemic updates"
        if checked(client,f"cd {q(project)} && git rev-parse HEAD")!=EXPECTED_HEAD:raise GateError("P1_8A_COMMIT_HEAD_DRIFT")
        staged=checked(client,f"cd {q(project)} && git diff --cached --name-only").splitlines()
        if not staged or not set(staged).issubset(set(P1_8_FILES)):raise GateError("P1_8A_COMMIT_STAGE_SET_INVALID")
        staged_set=set(staged)
        rc,out,err=run(client,f"cd {q(project)} && git commit -m {q(message)}",timeout=900)
        if rc:raise GateError("P1_8A_COMMIT_FAILED:"+re.sub(r"\s+"," ",err)[-500:])
        head=checked(client,f"cd {q(project)} && git rev-parse HEAD");parent=checked(client,f"cd {q(project)} && git rev-parse HEAD^")
        subject=checked(client,f"cd {q(project)} && git log -1 --format=%s")
        committed=checked(client,f"cd {q(project)} && git diff-tree --no-commit-id --name-only -r HEAD").splitlines()
        staged_after=checked(client,f"cd {q(project)} && git diff --cached --name-only").splitlines()
        dirty_after=checked(client,f"cd {q(project)} && git status --porcelain=v1 -uall").splitlines()
        if parent!=EXPECTED_HEAD or subject!=message or set(committed)!=staged_set or staged_after:raise GateError("P1_8A_COMMIT_VERIFICATION_FAILED")
        print(json.dumps({"target_gate":{"key_type":gate["key_type"],"stable_second_connection":True},"commit":head,"parent":parent,"subject":subject,"committed_paths":committed,"committed_count":len(committed),"staged_after":0,"remaining_dirty_entries":len(dirty_after),"push_performed":False,"no_source_server_connection":True},indent=2))
    finally:client.close()


def p1_8a_stage_inspect_action() -> None:
    client,gate=connect()
    try:
        root,project=paths(client);python=root+"/conda/envs/epidemic-core/bin/python"
        staged=checked(client,f"cd {q(project)} && git diff --cached --name-only").splitlines()
        outside=sorted(set(staged)-set(P1_8_FILES));missing=sorted(set(P1_8_FILES)-set(staged))
        diff_check=run(client,f"cd {q(project)} && git diff --cached --check",timeout=600)[0]
        code=r'''import hashlib,json,os,pathlib,re,subprocess
project=pathlib.Path(os.environ["PROJECT"]);files=json.loads(os.environ["FILES"]);findings=[];large=[]
rules={"private_key":r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----","cloud_token":r"(?:AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9_]{30,}|sk-[A-Za-z0-9]{24,})","credential_url":r"(?:postgresql|https?)://[^\s:/]+:[^\s/@]+@","tls_bypass":r"curl\s+-k(?:\s|$)|wget\s+--no-check-certificate|GIT_SSL_NO_VERIFY\s*=\s*(?:1|true)|pip\s+[^\n]*--trusted-host","identity_path":r"(?:USER_HOME"}
for rel in files:
 path=project/rel
 if not path.is_file():continue
 if path.stat().st_size>10*1024*1024:large.append({"path":rel,"bytes":path.stat().st_size})
 text=path.read_text(errors="replace")
 for name,pattern in rules.items():
  for match in re.finditer(pattern,text,re.I|re.M):
   line=text.count("\n",0,match.start())+1;findings.append({"path":rel,"line":line,"rule":name,"line_sha256":hashlib.sha256(text.splitlines()[line-1].encode()).hexdigest()})
diff=subprocess.run(["git","-C",str(project),"diff","--cached","--binary"],capture_output=True,check=True).stdout
print(json.dumps({"findings":findings,"large":large,"diff_sha256":hashlib.sha256(diff).hexdigest(),"diff_bytes":len(diff)}))
'''
        scan=json.loads(checked(client,f"PROJECT={q(project)} FILES={q(json.dumps(staged))} {q(python)} -B -c {q(code)}",timeout=900))
        print(json.dumps({"target_gate":{"key_type":gate["key_type"],"stable_second_connection":True},"staged":staged,"staged_count":len(staged),"outside_allowed":outside,"allowed_but_not_staged":missing,"diff_check":diff_check==0,"scan":scan,"no_source_server_connection":True},indent=2))
    finally:client.close()


def p1_8b_inventory_action() -> None:
    client,gate=connect()
    try:
        root,project=paths(client);python=root+"/conda/envs/epidemic-core/bin/python"
        head=checked(client,f"cd {q(project)} && git rev-parse HEAD")
        if head!=P1_8A_COMMIT:raise GateError("P1_8B_BASELINE_HEAD_DRIFT")
        code=r'''import ast,hashlib,json,os,pathlib,re,subprocess
project=pathlib.Path(os.environ["PROJECT"]);base=project/"src/epidemic_core/discovery";files=[];symbols={};matches=[]
patterns=re.compile(r"parallel|duckduckgo|datacite|search_result|candidate_fetch|preview|artifact|schema_profile|REVIEW_READY|raw_results|web_search|web_fetch",re.I)
for path in sorted(base.rglob("*.py")):
 rel=str(path.relative_to(project));text=path.read_text(errors="replace");files.append({"path":rel,"bytes":path.stat().st_size,"sha256":hashlib.sha256(path.read_bytes()).hexdigest()})
 try:tree=ast.parse(text)
 except SyntaxError:continue
 symbols[rel]=[{"kind":type(node).__name__,"name":node.name,"line":node.lineno} for node in ast.walk(tree) if isinstance(node,(ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef))]
 for number,line in enumerate(text.splitlines(),1):
  if patterns.search(line):matches.append({"path":rel,"line":number,"text":line.strip()[:500]})
status=subprocess.run(["git","-C",str(project),"status","--porcelain=v1","-uall"],text=True,capture_output=True,check=True).stdout.splitlines()
print(json.dumps({"files":files,"symbols":symbols,"matches":matches[:500],"git_status_count":len(status),"git_status_paths":[line[3:] for line in status]}))
'''
        source=json.loads(checked(client,f"PROJECT={q(project)} {q(python)} -B -c {q(code)}",timeout=600))
        common=project+"/scripts/source_discovery_worker/_common.sh"
        common_text=checked(client,f"sed -n '1,260p' {q(common)}")
        common_safe=re.sub(r"(?im)^([A-Z0-9_]*(?:URL|PASS|TOKEN|SECRET|KEY)[A-Z0-9_]*\s*=).*$",r"\1[redacted]",common_text).replace(root,"[data-root]").replace(project,"[project]")
        runtime_vars=checked(client,f"bash -c {q('source '+q(common)+'; compgen -v | grep -E \'^[A-Z][A-Z0-9_]*$\' | sort')}").splitlines()
        config_match=re.search(r"(?m)^([A-Z][A-Z0-9_]*)=.*(?:secrets|runtime).*\.env",common_text)
        config_var=config_match.group(1) if config_match else None
        env_names=[]
        if config_var:
            env_names=checked(client,f"bash -c {q('source '+q(common)+f'; sed -n -E \'s/^([A-Z][A-Z0-9_]*)=.*/\\1/p\' \"${config_var}\" | sort')}").splitlines()
        db_code=r'''import json,os,sqlalchemy as sa
from sqlalchemy import text
keys=[key for key in os.environ if key.endswith("DATABASE_URL") and "DISCOVERY" in key]
if len(keys)!=1:raise RuntimeError("DISCOVERY_DATABASE_URL_COUNT")
engine=sa.create_engine(os.environ[keys[0]],hide_parameters=True);result={"database_env_name":keys[0]}
with engine.connect() as c:
 result["tables"]=[row[0] for row in c.execute(text("SELECT c.relname FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='epidemic_discovery' AND c.relkind='r' AND has_table_privilege(current_user,c.oid,'SELECT') ORDER BY c.relname"))]
 result["columns"]=[{"table":row[0],"column":row[1],"type":row[2]} for row in c.execute(text("SELECT c.relname,a.attname,pg_catalog.format_type(a.atttypid,a.atttypmod) FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped WHERE n.nspname='epidemic_discovery' AND c.relkind IN ('r','v') ORDER BY c.relname,a.attnum"))]
 result["counts"]={name:c.exec_driver_sql('SELECT count(*) FROM epidemic_discovery."'+name+'"').scalar_one() for name in result["tables"]}
 if "discovery_runs" in result["tables"]:
  result["recent_runs"]=[{"id":str(row[0]),"state":row[1],"country":row[2],"disease":str(row[3]),"created_at":str(row[4]),"updated_at":str(row[5]),"retry_of":str(row[6]) if row[6] else None,"request":row[7]} for row in c.execute(text("SELECT id,state,country_iso3,disease_id,created_at,updated_at,retry_of_run_id,request_json FROM epidemic_discovery.discovery_runs ORDER BY created_at DESC LIMIT 12"))]
print(json.dumps(result,default=str))
'''
        shell=f"source {q(common)}; set -a; source \"${config_var}\"; set +a; {q(python)} -B -c {q(db_code)}" if config_var else "false"
        rc,out,err=run(client,f"bash -c {q(shell)}",timeout=600)
        database={"available":False,"error_class":"permission_or_runtime"}
        if rc==0:database={"available":True,**json.loads(out)}
        openapi_code=r'''import json,urllib.request
with urllib.request.urlopen("http://127.0.0.1:8010/openapi.json",timeout=10) as response:value=json.load(response)
print(json.dumps({path:sorted(methods) for path,methods in value.get("paths",{}).items() if "discovery" in path or "candidate" in path or "preview" in path}))
'''
        openapi_rc,openapi_out,_openapi_err=run(client,f"{q(python)} -B -c {q(openapi_code)}")
        openapi=json.loads(openapi_out) if openapi_rc==0 else {"available":False,"reason":"disabled_or_not_exposed"}
        print(json.dumps({"target_gate":{"key_type":gate["key_type"],"stable_second_connection":True},"head":head,"source":source,"discovery_common":common_safe.splitlines(),"discovery_runtime_variables":runtime_vars,"discovery_runtime_env_names":env_names,"database":database,"openapi_paths":openapi,"no_source_server_connection":True},indent=2))
    finally:client.close()


def p1_8b_database_action() -> None:
    client,gate=connect()
    try:
        root,project=paths(client);python=root+"/conda/envs/epidemic-core/bin/python";common=project+"/scripts/source_discovery_worker/_common.sh"
        code=r'''import json,os,sqlalchemy as sa
from sqlalchemy import text
engine=sa.create_engine(os.environ["APP_DATABASE_URL"],hide_parameters=True);result={}
with engine.connect() as c:
 tables=[row[0] for row in c.execute(text("SELECT c.relname FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='epidemic_discovery' AND c.relkind='r' AND has_table_privilege(current_user,c.oid,'SELECT') ORDER BY c.relname"))]
 result["tables"]=tables;result["counts"]={name:c.exec_driver_sql('SELECT count(*) FROM epidemic_discovery."'+name+'"').scalar_one() for name in tables}
 result["columns"]={}
 for name in tables:
  result["columns"][name]=[{"name":row[0],"type":row[1]} for row in c.execute(text("SELECT a.attname,pg_catalog.format_type(a.atttypid,a.atttypmod) FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class c ON c.oid=a.attrelid JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='epidemic_discovery' AND c.relname=:name AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum"),{"name":name})]
 result["views"]=[row[0] for row in c.execute(text("SELECT c.relname FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='epidemic_discovery' AND c.relkind IN ('v','m') ORDER BY c.relname"))]
 result["functions"]=[{"name":row[0],"args":row[1]} for row in c.execute(text("SELECT p.proname,pg_catalog.pg_get_function_identity_arguments(p.oid) FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='epidemic_discovery' ORDER BY p.proname"))]
print(json.dumps(result,default=str))
'''
        shell=f"source {q(common)}; {q(python)} -B -c {q(code)}"
        value=json.loads(checked(client,f"bash -c {q(shell)}",timeout=600))
        print(json.dumps({"target_gate":{"key_type":gate["key_type"],"stable_second_connection":True},"database":value,"no_source_server_connection":True},indent=2))
    finally:client.close()


def p1_8b_code_action(section: str) -> None:
    client,gate=connect()
    try:
        root,project=paths(client)
        selections={
            "providers":[("src/epidemic_core/discovery/providers.py",350,590)],
            "worker":[("src/epidemic_core/discovery/worker.py",180,540)],
            "candidate":[("src/epidemic_core/discovery/candidate_pipeline.py",1,520),("src/epidemic_core/discovery/extractor.py",1,220),("src/epidemic_core/discovery/safe_fetcher.py",1,260)],
            "service":[("src/epidemic_core/discovery/service.py",100,480)],
            "tests":[("tests/unit/test_keyfree_discovery.py",1,500),("tests/unit/test_discovery_worker.py",1,800),("tests/unit/test_source_discovery.py",1,800)],
            "registry":[("config/source_discovery/directories.v1.json",1,1000),("src/epidemic_core/api/routes/discovery.py",1,400)],
        }
        result={}
        for relative,start,end in selections[section]:
            remote=project+"/"+relative
            if run(client,f"test -f {q(remote)}")[0]:continue
            value=checked(client,f"sed -n {q(str(start)+','+str(end)+'p')} {q(remote)}",timeout=300)
            value=re.sub(r"(?i)(password|token|secret|api[_-]?key)(\s*[=:]\s*)[^\s'\"]+",r"\1\2[redacted]",value).replace(root,"[data-root]").replace(project,"[project]")
            result[relative]=[{"n":start+i,"text":line} for i,line in enumerate(value.splitlines())]
        print(json.dumps({"target_gate":{"key_type":gate["key_type"],"stable_second_connection":True},"section":section,"files":result,"no_source_server_connection":True},indent=2))
    finally:client.close()


def p1_8b_download_action() -> None:
    """Mirror the current A100 discovery implementation for reviewed local editing."""
    client, gate = connect()
    try:
        _root, project = paths(client)
        mirror = ROOT / "remote_edit" / "p1_8b"
        fixed = (
            "pyproject.toml",
            "alembic.ini",
            "config/source_discovery/directories.v1.json",
            "src/epidemic_core/db/models/tables.py",
            "src/epidemic_core/api/routes/discovery.py",
        )
        find_command = (
            f"cd {q(project)} && find "
            "src/epidemic_core/discovery tests/unit tests/integration "
            "scripts/source_discovery_worker scripts/source_review migrations/versions "
            r"-type f \(" 
            " -name '*.py' -o -name '*.sh' -o -name '*.json' -o -name '*.css' -o -name '*.js'"
            r" \) -printf '%p\n' | sort"
        )
        discovered = checked(client, find_command, timeout=300).splitlines()
        relative_paths = list(fixed)
        prefix = project.rstrip("/") + "/"
        for remote_path in discovered:
            if remote_path.startswith(prefix):
                relative_paths.append(remote_path[len(prefix):])
            elif not remote_path.startswith("/") and ".." not in Path(remote_path).parts:
                relative_paths.append(remote_path)
        relative_paths = sorted(set(relative_paths))
        manifest: dict[str, object] = {"head": checked(client, f"cd {q(project)} && git rev-parse HEAD"), "files": {}}
        if manifest["head"] != P1_8A_COMMIT:
            raise GateError("P1_8B_BASELINE_HEAD_DRIFT")
        sftp = client.open_sftp()
        try:
            for relative in relative_paths:
                remote = project + "/" + relative
                try:
                    with sftp.file(remote, "rb") as handle:
                        payload = handle.read()
                except FileNotFoundError:
                    continue
                local = mirror / relative
                local.parent.mkdir(parents=True, exist_ok=True)
                local.write_bytes(payload)
                manifest["files"][relative] = {
                    "bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "mode": sftp.stat(remote).st_mode & 0o777,
                }
        finally:
            sftp.close()
        (mirror / "REMOTE_MANIFEST.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps({
            "target_gate": {"key_type": gate["key_type"], "stable_second_connection": True},
            "head": manifest["head"],
            "mirrored_files": len(manifest["files"]),
            "manifest_sha256": hashlib.sha256((mirror / "REMOTE_MANIFEST.json").read_bytes()).hexdigest(),
            "local_mirror": str(mirror),
            "no_source_server_connection": True,
        }, indent=2))
    finally:
        client.close()


def p1_8b_refresh_manifest_action() -> None:
    """Refresh remote hash gates without overwriting reviewed local edits."""
    client, gate = connect()
    try:
        _root, project = paths(client)
        mirror = ROOT / "remote_edit" / "p1_8b"
        manifest_path = mirror / "REMOTE_MANIFEST.json"
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        head = checked(client, f"cd {q(project)} && git rev-parse HEAD")
        if head != P1_8A_COMMIT:
            raise GateError("P1_8B_BASELINE_HEAD_DRIFT")
        paths_to_hash = sorted(set(previous.get("files", {})) | set(P1_8B_SYNC_FILES))
        refreshed: dict[str, object] = {"head": head, "files": {}}
        sftp = client.open_sftp()
        try:
            for relative in paths_to_hash:
                remote = project + "/" + relative
                try:
                    with sftp.file(remote, "rb") as handle:
                        payload = handle.read()
                    stat = sftp.stat(remote)
                except FileNotFoundError:
                    continue
                refreshed["files"][relative] = {
                    "bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "mode": stat.st_mode & 0o777,
                }
        finally:
            sftp.close()
        manifest_path.write_text(json.dumps(refreshed, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps({
            "target_gate": {"key_type": gate["key_type"], "stable_second_connection": True},
            "head": head,
            "remote_hashes_refreshed": len(refreshed["files"]),
            "local_edits_overwritten": False,
            "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            "no_source_server_connection": True,
        }, indent=2))
    finally:
        client.close()


def p1_8b_runs_action() -> None:
    client, gate = connect()
    try:
        root, project = paths(client)
        python = root + "/conda/envs/epidemic-core/bin/python"
        common = project + "/scripts/source_discovery_worker/_common.sh"
        code = r'''import json,os,sqlalchemy as sa
from sqlalchemy import text
engine=sa.create_engine(os.environ["APP_DATABASE_URL"],hide_parameters=True);result={}
with engine.connect() as c:
 cols={row[0] for row in c.execute(text("SELECT a.attname FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class cl ON cl.oid=a.attrelid JOIN pg_catalog.pg_namespace n ON n.oid=cl.relnamespace WHERE n.nspname='epidemic_discovery' AND cl.relname='discovery_runs' AND a.attnum>0 AND NOT a.attisdropped"))}
 wanted=[x for x in ("id","state","country_iso3","disease_id","created_at","claimed_at","completed_at","updated_at","retry_of_run_id","request_json","progress_json","terminal_reason") if x in cols]
 sql="SELECT "+",".join('r.\"'+x+'\"' for x in wanted)+" FROM epidemic_discovery.discovery_runs r ORDER BY created_at DESC LIMIT 20"
 result["run_columns"]=wanted;result["runs"]=[dict(zip(wanted,row)) for row in c.exec_driver_sql(sql)]
 event_cols={row[0] for row in c.execute(text("SELECT a.attname FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_class cl ON cl.oid=a.attrelid JOIN pg_catalog.pg_namespace n ON n.oid=cl.relnamespace WHERE n.nspname='epidemic_discovery' AND cl.relname='discovery_run_events' AND a.attnum>0 AND NOT a.attisdropped"))}
 ewanted=[x for x in ("id","discovery_run_id","event_type","event_at","payload","created_at") if x in event_cols]
 eorder="created_at" if "created_at" in event_cols else "event_at"
 esql="SELECT "+",".join('e.\"'+x+'\"' for x in ewanted)+" FROM epidemic_discovery.discovery_run_events e ORDER BY \""+eorder+"\" DESC LIMIT 250"
 result["event_columns"]=ewanted;result["events"]=[dict(zip(ewanted,row)) for row in c.exec_driver_sql(esql)]
 result["candidates"]=[dict(row) for row in c.execute(text("SELECT c.id,c.discovery_run_id,c.dataset_name,c.publisher,c.source_tier,c.status,c.risk_flags,c.created_at,cv.license_status,cv.format,cv.metadata,qa.status quality_status,rc.state review_state FROM epidemic_discovery.source_candidates c LEFT JOIN LATERAL (SELECT * FROM epidemic_discovery.source_candidate_versions v WHERE v.candidate_id=c.id ORDER BY version_no DESC LIMIT 1) cv ON true LEFT JOIN epidemic_discovery.source_quality_assessments qa ON qa.candidate_version_id=cv.id LEFT JOIN epidemic_discovery.source_review_cases rc ON rc.candidate_version_id=cv.id ORDER BY c.created_at DESC")) .mappings()]
print(json.dumps(result,default=str))
'''
        value = json.loads(checked(client, f"bash -c {q('source '+q(common)+'; '+q(python)+' -B -c '+q(code))}", timeout=600))
        print(json.dumps({
            "target_gate": {"key_type": gate["key_type"], "stable_second_connection": True},
            "discovery": value,
            "no_source_server_connection": True,
        }, ensure_ascii=True, indent=2))
    finally:
        client.close()


def p1_8b_openclaw_capabilities_action() -> None:
    client, gate = connect()
    try:
        root, project = paths(client)
        cli = project + "/scripts/openclaw/openclaw_cli.sh"
        commands = {
            "version": f"{q(cli)} --version",
            "capability_help": f"{q(cli)} capability --help",
            "capability_web_help": f"{q(cli)} capability web --help",
            "capability_web_providers": f"{q(cli)} capability web providers --json",
            "capability_web_search_help": f"{q(cli)} capability web search --help",
            "plugins_help": f"{q(cli)} plugins --help",
            "plugins_list": f"{q(cli)} plugins list --json",
        }
        result = {}
        for label, command in commands.items():
            started = time.monotonic()
            rc, out, err = run(client, command, timeout=180)
            value = (out + "\n" + err).replace(project, "[project]").replace(root, "[data-root]")
            value = re.sub(r"(?i)(password|token|secret|api[_-]?key)(\s*[=:]\s*)\S+", r"\1\2[redacted]", value)
            value = re.sub(r"(?:postgresql[^\s]+|https?://\S+)", "[url-redacted]", value)
            value = re.sub(r"/(?:home/[^/\s]+|root)(?=/|\s|$)", "/[home]", value)
            result[label] = {
                "rc": rc,
                "elapsed_ms": int((time.monotonic() - started) * 1000),
                "lines": value.strip().splitlines()[:240],
            }
        print(json.dumps({
            "target_gate": {"key_type": gate["key_type"], "stable_second_connection": True},
            "commands": result,
            "no_source_server_connection": True,
        }, indent=2))
    finally:
        client.close()


def p1_8b_sync_action() -> None:
    client, gate = connect()
    try:
        _root, project = paths(client)
        mirror = ROOT / "remote_edit" / "p1_8b"
        manifest = json.loads((mirror / "REMOTE_MANIFEST.json").read_text(encoding="utf-8"))
        if checked(client, f"cd {q(project)} && git rev-parse HEAD") != P1_8A_COMMIT:
            raise GateError("P1_8B_BASELINE_HEAD_DRIFT")
        timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = project + "/storage/tmp/p1_8b_sync_" + timestamp
        checked(client, f"umask 077; mkdir -p {q(backup)}; chmod 700 {q(backup)}")
        sftp = client.open_sftp()
        changes = []
        try:
            for relative in P1_8B_SYNC_FILES:
                local = mirror / relative
                if not local.is_file():
                    continue
                payload = local.read_bytes()
                remote = project + "/" + relative
                original = None
                mode = 0o755 if relative.endswith(".sh") else 0o644
                try:
                    with sftp.file(remote, "rb") as handle:
                        original = handle.read()
                    mode = sftp.stat(remote).st_mode & 0o777
                except FileNotFoundError:
                    original = None
                expected = manifest.get("files", {}).get(relative, {}).get("sha256")
                if original is not None and expected and hashlib.sha256(original).hexdigest() != expected:
                    raise GateError("P1_8B_REMOTE_FILE_CHANGED_SINCE_MIRROR:" + relative)
                if original == payload:
                    continue
                if original is not None:
                    backup_file = backup + "/" + relative.replace("/", "__")
                    with sftp.file(backup_file, "wb") as handle:
                        handle.write(original)
                    sftp.chmod(backup_file, 0o600)
                temporary = remote + ".p1_8b_upload"
                with sftp.file(temporary, "wb") as handle:
                    handle.write(payload)
                sftp.chmod(temporary, mode)
                try:
                    sftp.posix_rename(temporary, remote)
                except OSError:
                    sftp.rename(temporary, remote)
                sftp.chmod(remote, mode)
                changes.append({
                    "path": relative,
                    "before_sha256": hashlib.sha256(original).hexdigest() if original is not None else None,
                    "after_sha256": hashlib.sha256(payload).hexdigest(),
                    "mode": oct(mode),
                })
        finally:
            sftp.close()
        python_files = [project + "/" + item for item in P1_8B_SYNC_FILES if item.endswith(".py") and (mirror / item).is_file()]
        shell_files = [project + "/" + item for item in P1_8B_SYNC_FILES if item.endswith(".sh") and (mirror / item).is_file()]
        root, _project = paths(client)
        py_rc = run(client, " ".join([q(root + "/conda/envs/epidemic-core/bin/python"), "-m", "py_compile", *map(q, python_files)]), timeout=600)[0]
        sh_rc = run(client, "bash -n " + " ".join(map(q, shell_files)), timeout=300)[0]
        diff_rc = run(client, f"cd {q(project)} && git diff --check", timeout=600)[0]
        if py_rc or sh_rc or diff_rc:
            raise GateError("P1_8B_SYNC_STATIC_GATE_FAILED")
        print(json.dumps({
            "target_gate": {"key_type": gate["key_type"], "stable_second_connection": True},
            "changes": changes,
            "change_count": len(changes),
            "backup": "[project]/storage/tmp/" + backup.rsplit("/", 1)[-1],
            "python_syntax": True,
            "bash_syntax": True,
            "git_diff_check": True,
            "no_source_server_connection": True,
        }, indent=2))
    finally:
        client.close()


def p1_8b_remote_static_action() -> None:
    client, gate = connect()
    try:
        root, project = paths(client)
        python = root + "/conda/envs/epidemic-core/bin/python"
        files = [item for item in P1_8B_SYNC_FILES if run(client, f"test -f {q(project+'/'+item)}")[0] == 0]
        python_files = [item for item in files if item.endswith(".py")]
        checks = {
            "ruff_check": f"cd {q(project)} && {q(python)} -m ruff check " + " ".join(map(q, python_files)),
            "ruff_format": f"cd {q(project)} && {q(python)} -m ruff format --check " + " ".join(map(q, python_files)),
            "mypy_discovery": f"cd {q(project)} && {q(python)} -m mypy --strict src/epidemic_core/discovery",
            "pytest_discovery": f"cd {q(project)} && PYTHONPATH=src {q(python)} -m pytest -p no:cacheprovider -q tests/unit/test_discovery_providers.py tests/unit/test_keyfree_discovery.py tests/unit/test_p1_7a_ui_repair.py",
        }
        result = {}
        for label, command in checks.items():
            started = time.monotonic()
            rc, out, err = run(client, command, timeout=1800)
            value = (out + "\n" + err).replace(project, "[project]").replace(root, "[data-root]")
            value = re.sub(r"/(?:root|home/[^/\s]+)(?=/|\s|$)", "[home]", value)
            value = re.sub(r"(?i)(password|token|secret|api[_-]?key)(\s*[=:]\s*)\S+", r"\1\2[redacted]", value)
            value = re.sub(r"(?:postgresql[^\s]+|https?://\S+)", "[url-redacted]", value)
            result[label] = {"rc": rc, "elapsed_ms": int((time.monotonic() - started) * 1000), "tail": value.splitlines()[-120:]}
        print(json.dumps({
            "target_gate": {"key_type": gate["key_type"], "stable_second_connection": True},
            "checks": result,
            "all_pass": all(item["rc"] == 0 for item in result.values()),
            "no_source_server_connection": True,
        }, ensure_ascii=False, indent=2))
    finally:
        client.close()


def p1_8b_remote_format_action() -> None:
    client, gate = connect()
    try:
        root, project = paths(client)
        python = root + "/conda/envs/epidemic-core/bin/python"
        files = [
            item
            for item in P1_8B_SYNC_FILES
            if item.endswith(".py") and run(client, f"test -f {q(project+'/'+item)}")[0] == 0
        ]
        format_rc, _format_out, _format_err = run(
            client,
            f"cd {q(project)} && {q(python)} -m ruff format " + " ".join(map(q, files)),
            timeout=1200,
        )
        fix_rc, _fix_out, _fix_err = run(
            client,
            f"cd {q(project)} && {q(python)} -m ruff check --fix " + " ".join(map(q, files)),
            timeout=1200,
        )
        diff_rc = run(client, f"cd {q(project)} && git diff --check", timeout=600)[0]
        if format_rc or fix_rc or diff_rc:
            raise GateError("P1_8B_REMOTE_FORMAT_FAILED")
        print(json.dumps({
            "target_gate": {"key_type": gate["key_type"], "stable_second_connection": True},
            "python_files": len(files),
            "ruff_format": True,
            "ruff_fix": True,
            "git_diff_check": True,
            "no_source_server_connection": True,
        }, indent=2))
    finally:
        client.close()


def p1_8b_migrate_restart_action() -> None:
    client, gate = connect()
    try:
        root, project = paths(client)
        python = root + "/conda/envs/epidemic-core/bin/python"
        env_script = project + "/scripts/database/_env.sh"
        counts_sql = "SELECT json_build_object(" + ",".join(
            f"'{name}',(SELECT count(*) FROM epidemic.{name})" for name in EXPECTED_COUNTS
        ) + ")::text"
        snapshots_sql = (
            "SELECT COALESCE(json_agg(json_build_object('sha256',content_sha256,'row_count',row_count) "
            "ORDER BY id),'[]'::json)::text FROM epidemic.dataset_snapshots"
        )
        before_counts = json.loads(db_query(client, counts_sql))
        before_snapshots = json.loads(db_query(client, snapshots_sql))
        if before_counts != EXPECTED_COUNTS or {
            row["sha256"]: row["row_count"] for row in before_snapshots
        } != EXPECTED_SNAPSHOTS:
            raise GateError("P1_8B_FORMAL_DATA_DRIFT_BEFORE_MIGRATION")
        owner_migrate_code = r'''import os,subprocess,sys
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url
base=make_url(os.environ["MIGRATION_DATABASE_URL"])
reader=sa.create_engine(base,hide_parameters=True)
with reader.connect() as connection:
 owner=connection.execute(text("SELECT r.rolname FROM pg_namespace n JOIN pg_roles r ON r.oid=n.nspowner WHERE n.nspname='epidemic_discovery'")).scalar_one()
reader.dispose()
os.environ["MIGRATION_DATABASE_URL"]=base.set(
 username=owner,password=os.environ["POSTGRES_ADMIN_PASSWORD"]
).render_as_string(hide_password=False)
os.environ["PYTHONDONTWRITEBYTECODE"]="1"
raise SystemExit(subprocess.run([sys.executable,"-m","alembic","upgrade","head"],check=False).returncode)
'''
        migration_command = (
            f"source {q(env_script)}; set -a; source {q(project+'/.secrets/postgres.env')}; set +a; "
            f"cd {q(project)}; exec {q(python)} -B -c {q(owner_migrate_code)}"
        )
        migration_rc, migration_out, migration_err = run(
            client, f"bash -c {q(migration_command)}", timeout=900
        )
        if migration_rc != 0:
            diagnostic = (migration_out + "\n" + migration_err).replace(project, "[project]").replace(
                root, "[data-root]"
            )
            diagnostic = re.sub(r"/(?:root|home/[^/\s]+)(?=/|\s|$)", "[home]", diagnostic)
            diagnostic = re.sub(
                r"(?i)(password|token|secret|api[_-]?key)(\s*[=:]\s*)\S+",
                r"\1\2[redacted]",
                diagnostic,
            )
            diagnostic = re.sub(r"(?:postgresql[^\s]+|https?://\S+)", "[url-redacted]", diagnostic)
            print(json.dumps({"migration_rc": migration_rc, "diagnostic_tail": diagnostic.splitlines()[-40:]}))
            raise GateError("P1_8B_ALEMBIC_UPGRADE_FAILED")
        for script in (
            "scripts/source_discovery_worker/install_extractor_runtime.sh",
            "scripts/source_review/install_runtime.sh",
            "scripts/source_discovery_worker/stop_source_discovery_worker.sh",
            "scripts/source_review/stop_source_review.sh",
            "scripts/query_api/stop_query_api.sh",
            "scripts/query_api/start_query_api.sh",
            "scripts/source_discovery_worker/start_source_discovery_worker.sh",
            "scripts/source_review/start_source_review.sh",
        ):
            service_command = q(project + "/" + script)
            service_rc, service_out, service_err = run(client, service_command, timeout=180)
            if service_rc != 0:
                diagnostic = (service_out + "\n" + service_err).replace(project, "[project]").replace(
                    root, "[data-root]"
                )
                diagnostic = re.sub(r"/(?:root|home/[^/\s]+)(?=/|\s|$)", "[home]", diagnostic)
                diagnostic = re.sub(
                    r"(?i)(password|token|secret|api[_-]?key)(\s*[=:]\s*)\S+",
                    r"\1\2[redacted]",
                    diagnostic,
                )
                diagnostic = re.sub(r"(?:postgresql[^\s]+|https?://\S+)", "[url-redacted]", diagnostic)
                print(
                    json.dumps(
                        {
                            "failed_service_command": script.rsplit("/", 1)[-1],
                            "returncode": service_rc,
                            "diagnostic_tail": diagnostic.splitlines()[-30:],
                        },
                        indent=2,
                    )
                )
                raise GateError("P1_8B_SERVICE_COMMAND_FAILED:" + script.rsplit("/", 1)[-1])
        status = {
            "query_api": run(client, q(project + "/scripts/query_api/status_query_api.sh"), timeout=180)[0] == 0,
            "discovery_worker": run(
                client, q(project + "/scripts/source_discovery_worker/status_source_discovery_worker.sh"), timeout=180
            )[0]
            == 0,
            "review_console": run(
                client, q(project + "/scripts/source_review/status_source_review.sh"), timeout=180
            )[0]
            == 0,
        }
        schema_code = r'''import json,os
import sqlalchemy as sa
from sqlalchemy import text
engine=sa.create_engine(os.environ["APP_DATABASE_URL"],hide_parameters=True)
with engine.connect() as connection:
 value=connection.execute(text("SELECT json_build_object("
  "'search_results_table',to_regclass('epidemic_discovery.source_discovery_search_results') IS NOT NULL,"
  "'candidate_fetches_table',to_regclass('epidemic_discovery.candidate_fetches') IS NOT NULL,"
  "'search_results_view',to_regclass('epidemic_discovery.review_discovery_search_results_v1') IS NOT NULL,"
  "'candidate_fetches_view',to_regclass('epidemic_discovery.review_candidate_fetches_v1') IS NOT NULL,"
  "'app_search_insert',has_table_privilege(current_user,'epidemic_discovery.source_discovery_search_results','INSERT'),"
  "'app_fetch_insert',has_table_privilege(current_user,'epidemic_discovery.candidate_fetches','INSERT'),"
  "'app_fetch_update',has_table_privilege(current_user,'epidemic_discovery.candidate_fetches','UPDATE'),"
  "'app_search_delete',has_table_privilege(current_user,'epidemic_discovery.source_discovery_search_results','DELETE')"
  ")")).scalar_one()
print(json.dumps(value))
'''
        schema_command = (
            f"source {q(project+'/scripts/source_discovery_worker/_common.sh')}; "
            f"exec {q(python)} -B -c {q(schema_code)}"
        )
        schema = json.loads(checked(client, f"bash -c {q(schema_command)}", timeout=300))
        schema["alembic"] = db_query(client, "SELECT version_num FROM epidemic.alembic_version")
        after_counts = json.loads(db_query(client, counts_sql))
        after_snapshots = json.loads(db_query(client, snapshots_sql))
        listener_code = r'''import json,pathlib
ports={8020,8021};rows=[]
for filename,v6 in (("/proc/net/tcp",False),("/proc/net/tcp6",True)):
 for line in pathlib.Path(filename).read_text().splitlines()[1:]:
  parts=line.split();address,port_hex=parts[1].split(":");port=int(port_hex,16)
  if port in ports and parts[3]=="0A":rows.append({"port":port,"loopback":address==("00000000000000000000000001000000" if v6 else "0100007F")})
print(json.dumps(rows))
'''
        listeners = json.loads(checked(client, f"{q(python)} -B -c {q(listener_code)}"))
        proxy_absent = checked(
            client,
            "bash -lc '[[ -z ${HTTP_PROXY:-}${HTTPS_PROXY:-}${ALL_PROXY:-}${http_proxy:-}${https_proxy:-}${all_proxy:-} ]] && echo yes || echo no'",
        ) == "yes"
        passed = (
            schema["alembic"] == "0015_p1_8b_reviewer_read_views"
            and all(value for key, value in schema.items() if key not in {"alembic", "app_search_delete"})
            and schema["app_search_delete"] is False
            and all(status.values())
            and {item["port"] for item in listeners} == {8020, 8021}
            and all(item["loopback"] for item in listeners)
            and proxy_absent
            and before_counts == after_counts == EXPECTED_COUNTS
            and before_snapshots == after_snapshots
        )
        if not passed:
            raise GateError("P1_8B_MIGRATION_RUNTIME_GATE_FAILED")
        print(json.dumps({
            "target_gate": {"key_type": gate["key_type"], "stable_second_connection": True},
            "schema": schema,
            "services": status,
            "listeners": listeners,
            "new_session_proxy_absent": proxy_absent,
            "formal_counts_unchanged": True,
            "snapshot_hashes_unchanged": True,
            "passed": True,
            "no_source_server_connection": True,
        }, indent=2))
    finally:
        client.close()


def p1_8b_db_access_action() -> None:
    client, gate = connect()
    try:
        root, project = paths(client)
        python = root + "/conda/envs/epidemic-core/bin/python"
        value = json.loads(
            db_query(
                client,
                "SELECT json_build_object("
                "'schema_create',has_schema_privilege(current_user,'epidemic_discovery','CREATE'),"
                "'schema_usage',has_schema_privilege(current_user,'epidemic_discovery','USAGE'),"
                "'migrator_role_exists',EXISTS(SELECT 1 FROM pg_roles WHERE rolname='epidemic_migrator'),"
                "'may_set_migrator',pg_has_role(current_user,'epidemic_migrator','MEMBER'),"
                "'schema_owned_by_migrator',(SELECT nspowner=(SELECT oid FROM pg_roles WHERE rolname='epidemic_migrator') "
                "FROM pg_namespace WHERE nspname='epidemic_discovery'),"
                "'may_set_schema_owner',(SELECT pg_has_role(current_user,nspowner,'MEMBER') "
                "FROM pg_namespace WHERE nspname='epidemic_discovery'),"
                "'current_is_superuser',(SELECT rolsuper FROM pg_roles WHERE rolname=current_user)"
                ")::text",
            )
        )
        owner_probe_code = r'''import json,os
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url
base=make_url(os.environ["MIGRATION_DATABASE_URL"])
reader=sa.create_engine(base,hide_parameters=True)
with reader.connect() as connection:
 owner=connection.execute(text("SELECT r.rolname FROM pg_namespace n JOIN pg_roles r ON r.oid=n.nspowner WHERE n.nspname='epidemic_discovery'")).scalar_one()
candidate=base.set(username=owner,password=os.environ["POSTGRES_ADMIN_PASSWORD"])
result={"connected":False,"is_schema_owner":False,"schema_create":False}
try:
 engine=sa.create_engine(candidate,hide_parameters=True)
 with engine.connect() as connection:
  result["connected"]=True
  result["is_schema_owner"]=connection.execute(text("SELECT current_user=(SELECT r.rolname FROM pg_namespace n JOIN pg_roles r ON r.oid=n.nspowner WHERE n.nspname='epidemic_discovery')")).scalar_one()
  result["schema_create"]=connection.execute(text("SELECT has_schema_privilege(current_user,'epidemic_discovery','CREATE')")).scalar_one()
 engine.dispose()
except Exception:
 pass
print(json.dumps(result))
'''
        owner_command = (
            f"source {q(project+'/scripts/database/_env.sh')}; "
            f"set -a; source {q(project+'/.secrets/postgres.env')}; set +a; "
            f"exec {q(python)} -B -c {q(owner_probe_code)}"
        )
        owner_access = json.loads(checked(client, f"bash -c {q(owner_command)}", timeout=300))
        print(json.dumps({
            "target_gate": {"key_type": gate["key_type"], "stable_second_connection": True},
            "database_access": value,
            "schema_owner_credential_probe": owner_access,
            "no_role_names_or_credentials_emitted": True,
            "no_source_server_connection": True,
        }, indent=2))
    finally:
        client.close()


def p1_8b_real_run_action(country_iso3: str) -> None:
    client, gate = connect()
    try:
        root, project = paths(client)
        python = root + "/conda/envs/epidemic-core/bin/python"
        code = r'''import concurrent.futures,datetime as dt,json,os,time,urllib.error,urllib.request,uuid
import sqlalchemy as sa
from sqlalchemy import text
country=os.environ["TARGET_COUNTRY"]
engine=sa.create_engine(os.environ["APP_DATABASE_URL"],hide_parameters=True,pool_size=12,max_overflow=4)
with engine.connect() as connection:
 disease_pattern="%Dengue%" if country=="BRA" else "%COVID%"
 disease=connection.execute(text("SELECT id FROM epidemic.diseases WHERE canonical_name ILIKE :pattern ORDER BY id LIMIT 1"),{"pattern":disease_pattern}).scalar_one()
 retry=connection.execute(text("SELECT id FROM epidemic_discovery.discovery_runs WHERE request_json->>'country_iso3'=:country ORDER BY created_at DESC LIMIT 1"),{"country":country}).scalar_one_or_none()
payload={
 "country_iso3":country,
 "disease_id":str(disease),
 "desired_metrics":["cases_new","deaths_new","incidence_rate"],
 "time_window_kind":"latest_rolling_year",
 "preferred_source_tiers":["NATIONAL_HEALTH","GOVERNMENT_OPEN_DATA","REGIONAL_HEALTH","INTERNATIONAL_PUBLIC_HEALTH","PUBLIC_HEALTH_RESEARCH"],
 "language_preferences":["zh","en"] if country=="CHN" else (["pt","en"] if country=="BRA" else ["fr","en"]),
 "max_candidates":5,
 "retry_nonce":str(uuid.uuid4()),
}
if retry is not None:payload["retry_of_run_id"]=str(retry)
body=json.dumps(payload,separators=(",",":")).encode()
token=os.environ.get("EPIDEMIC_QUERY_API_TOKEN","")
if len(token)<32:raise RuntimeError("QUERY_API_AUTH_UNAVAILABLE")
def submit(_index):
 request=urllib.request.Request(
  "http://127.0.0.1:8010/v1/source-discovery",data=body,method="POST",
  headers={"Authorization":"Bearer "+token,"Content-Type":"application/json","Accept":"application/json"},
 )
 try:
  with urllib.request.urlopen(request,timeout=15) as response:return response.status,json.load(response)
 except urllib.error.HTTPError as exc:return exc.code,{"error":"HTTP_ERROR"}
with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
 responses=list(pool.map(submit,range(10)))
statuses=[item[0] for item in responses]
ids={item[1].get("discovery_run_id") for item in responses if isinstance(item[1],dict) and item[1].get("discovery_run_id")}
if len(ids)!=1 or any(status not in (200,202) for status in statuses):
 print(json.dumps({"api_statuses":statuses,"run_id_count":len(ids)}));raise RuntimeError("DISCOVERY_API_CONCURRENCY_GATE_FAILED")
run_id=next(iter(ids));submitted_at=time.monotonic();claimed=False;claim_delay_ms=None
while time.monotonic()-submitted_at<10:
 with engine.connect() as connection:
  row=connection.execute(text("SELECT created_at,claim_owner,heartbeat_at,state FROM epidemic_discovery.discovery_runs WHERE id=:id"),{"id":run_id}).mappings().one()
 if row["claim_owner"] is not None or row["state"]!="QUEUED":
  claimed=True
  observed=row["heartbeat_at"] or dt.datetime.now(dt.timezone.utc)
  claim_delay_ms=max(0,int((observed-row["created_at"]).total_seconds()*1000));break
 time.sleep(.2)
if not claimed:raise RuntimeError("WORKER_CLAIM_TIMEOUT")
terminal={"REVIEW_READY","DISCOVERY_COMPLETE_NO_CANDIDATE","DISCOVERY_PARTIAL_FREE_PROVIDER_UNAVAILABLE","PROVIDER_DEGRADED","FAILED_RETRYABLE","FAILED_TERMINAL","INVALID_REQUEST"}
deadline=time.monotonic()+840
while True:
 with engine.connect() as connection:
  run=connection.execute(text("SELECT state,terminal_reason_code,created_at,completed_at,request_json->>'retry_of_run_id' retry_of_run_id,progress_json FROM epidemic_discovery.discovery_runs WHERE id=:id"),{"id":run_id}).mappings().one()
 if run["state"] in terminal:break
 if time.monotonic()>deadline:raise RuntimeError("DISCOVERY_TERMINAL_TIMEOUT")
 time.sleep(1)
with engine.connect() as connection:
 events=[]
 for row in connection.execute(text("SELECT event_code,created_at,details FROM epidemic_discovery.discovery_run_events WHERE discovery_run_id=:id ORDER BY created_at,id"),{"id":run_id}).mappings():
  details=row["details"] or {}
  events.append({"event_code":row["event_code"],"created_at":str(row["created_at"]),"provider_id":details.get("provider_id"),"domain":details.get("domain"),"network_request":details.get("network_request"),"http_status":details.get("http_status"),"elapsed_ms":details.get("elapsed_ms"),"result_count":details.get("result_count"),"result":details.get("result"),"reason_code":details.get("reason_code"),"query_count":details.get("query_count"),"successful_query_count":details.get("successful_query_count")})
 searches=[dict(row) for row in connection.execute(text("SELECT id search_result_id,provider_id,query_id,rank,title,domain,stable_dataset_id,source_tier,media_type,result_sha256,observed_at,disposition FROM epidemic_discovery.source_discovery_search_results WHERE discovery_run_id=:id ORDER BY provider_id,query_id,rank"),{"id":run_id}).mappings()]
 fetches=[dict(row) for row in connection.execute(text("SELECT id fetch_id,search_result_id,candidate_id,status,http_status,content_type,byte_size,domain,elapsed_ms,artifact_sha256,failure_code FROM epidemic_discovery.candidate_fetches WHERE discovery_run_id=:id ORDER BY attempted_at,id"),{"id":run_id}).mappings()]
 candidates=[dict(row) for row in connection.execute(text("SELECT c.id candidate_id,c.dataset_name,c.publisher,c.source_tier,c.status,cv.id candidate_version_id,cv.format,cv.license_status,sp.row_count,sp.column_count,sp.date_min,sp.date_max,qa.id assessment_id,rc.id review_case_id,rc.state review_state,ar.sha256 artifact_sha256,ar.byte_size artifact_bytes FROM epidemic_discovery.source_candidates c JOIN epidemic_discovery.source_candidate_versions cv ON cv.candidate_id=c.id JOIN epidemic_discovery.source_schema_profiles sp ON sp.candidate_version_id=cv.id JOIN epidemic_discovery.source_quality_assessments qa ON qa.candidate_version_id=cv.id JOIN epidemic_discovery.source_review_cases rc ON rc.candidate_version_id=cv.id JOIN epidemic_discovery.quarantine_artifacts ar ON ar.candidate_version_id=cv.id WHERE c.discovery_run_id=:id ORDER BY c.created_at"),{"id":run_id}).mappings()]
 preview_count=connection.execute(text("SELECT count(*) FROM epidemic_discovery.source_preview_rows pr JOIN epidemic_discovery.source_candidate_versions cv ON cv.id=pr.candidate_version_id JOIN epidemic_discovery.source_candidates c ON c.id=cv.candidate_id WHERE c.discovery_run_id=:id"),{"id":run_id}).scalar_one()
result={
 "country":country,"run_id":run_id,"retry_of_run_id":str(run["retry_of_run_id"]) if run["retry_of_run_id"] else None,
 "api_attempts":len(responses),"api_status_counts":{str(value):statuses.count(value) for value in sorted(set(statuses))},
 "deduplicated_to_single_run":len(ids)==1,"claim_within_10_seconds":claimed,"claim_delay_ms":claim_delay_ms,
 "state":run["state"],"terminal_reason_code":run["terminal_reason_code"],"created_at":str(run["created_at"]),"completed_at":str(run["completed_at"]),
 "progress":run["progress_json"],"events":events,"search_results":searches,"candidate_fetches":fetches,"candidates":candidates,"preview_rows":preview_count,
}
print(json.dumps(result,default=str))
'''
        command = (
            f"source {q(project+'/scripts/source_discovery_worker/_common.sh')}; "
            f"source {q(project+'/scripts/query_api/query_api_env.sh')}; "
            f"TARGET_COUNTRY={q(country_iso3)} exec {q(python)} -B -c {q(code)}"
        )
        rc, output, error = run(client, f"bash -c {q(command)}", timeout=900)
        if rc:
            diagnostic = (output + "\n" + error).replace(project, "[project]").replace(root, "[data-root]")
            diagnostic = re.sub(r"/(?:root|home/[^/\s]+)(?=/|\s|$)", "[home]", diagnostic)
            diagnostic = re.sub(r"(?i)(password|token|secret|api[_-]?key)(\s*[=:]\s*)\S+", r"\1\2[redacted]", diagnostic)
            diagnostic = re.sub(r"(?:postgresql[^\s]+|https?://\S+)", "[url-redacted]", diagnostic)
            raise GateError("P1_8B_REAL_RUN_FAILED:" + " ".join(diagnostic.splitlines()[-20:]))
        value = json.loads(output)
        print(json.dumps({
            "target_gate": {"key_type": gate["key_type"], "stable_second_connection": True},
            "run": value,
            "no_secret_values_emitted": True,
            "no_source_server_connection": True,
        }, ensure_ascii=False, indent=2))
    finally:
        client.close()


def p1_8b_latest_state_action() -> None:
    client, gate = connect()
    try:
        root, project = paths(client)
        python = root + "/conda/envs/epidemic-core/bin/python"
        code = r'''import json,os
import sqlalchemy as sa
from sqlalchemy import text
engine=sa.create_engine(os.environ["APP_DATABASE_URL"],hide_parameters=True)
with engine.connect() as connection:
 rows=[dict(row) for row in connection.execute(text("SELECT id,request_json->>'country_iso3' country,state,terminal_reason_code,created_at,updated_at,completed_at,progress_json FROM epidemic_discovery.discovery_runs ORDER BY created_at DESC LIMIT 3")).mappings()]
print(json.dumps(rows,default=str))
'''
        command = f"source {q(project+'/scripts/source_discovery_worker/_common.sh')}; exec {q(python)} -B -c {q(code)}"
        rows = json.loads(checked(client, f"bash -c {q(command)}", timeout=300))
        print(json.dumps({
            "target_gate": {"key_type": gate["key_type"], "stable_second_connection": True},
            "latest_runs": rows,
            "no_source_server_connection": True,
        }, indent=2))
    finally:
        client.close()


def p1_8b_datagouv_probe_action() -> None:
    client, gate = connect()
    try:
        root, project = paths(client)
        python = root + "/conda/envs/epidemic-core/bin/python"
        code = r'''import collections,json,urllib.parse
from epidemic_core.discovery.safe_fetcher import SafeFetcher
queries=("covid","covid indicateurs","covid santé publique france")
fetcher=SafeFetcher(maximum_bytes=8*1024*1024,timeout_seconds=20);output=[]
for query in queries:
 url="https://www.data.gouv.fr/api/1/datasets/?"+urllib.parse.urlencode({"q":query,"page_size":20,"sort":"-last_update"})
 fetched=fetcher.fetch(url,{"Accept":"application/json"});value=json.loads(fetched.payload);datasets=value.get("data",[])
 rows=[];formats=collections.Counter()
 for dataset in datasets[:20]:
  resources=dataset.get("resources") if isinstance(dataset,dict) else []
  if not isinstance(resources,list):resources=[]
  for resource in resources:formats[str(resource.get("format") or "[missing]").casefold()]+=1
  rows.append({"dataset_id":str(dataset.get("id") or dataset.get("slug") or ""),"title":str(dataset.get("title") or "")[:240],"description_has_covid":"covid" in str(dataset.get("description") or "").casefold(),"resource_count":len(resources),"resources":[{"format":str(item.get("format") or ""),"mime":str(item.get("mime") or ""),"domain":urllib.parse.urlsplit(str(item.get("url") or item.get("latest") or "")).hostname,"path_suffix":urllib.parse.urlsplit(str(item.get("url") or item.get("latest") or "")).path.rsplit(".",1)[-1][-12:]} for item in resources[:8] if isinstance(item,dict)]})
 output.append({"query_sha256":__import__("hashlib").sha256(query.encode()).hexdigest(),"http_status":fetched.http_status,"response_sha256":fetched.response_sha256,"reported_total":value.get("total"),"dataset_count":len(datasets),"format_counts":dict(formats),"datasets":rows[:8]})
print(json.dumps(output,ensure_ascii=False))
'''
        command = f"source {q(project+'/scripts/source_discovery_worker/_common.sh')}; exec {q(python)} -B -c {q(code)}"
        value = json.loads(checked(client, f"bash -c {q(command)}", timeout=180))
        print(json.dumps({
            "target_gate": {"key_type": gate["key_type"], "stable_second_connection": True},
            "official_catalog_probe": value,
            "no_candidate_created": True,
            "no_source_server_connection": True,
        }, ensure_ascii=True, indent=2))
    finally:
        client.close()


def p1_8b_brazil_ckan_probe_action() -> None:
    client, gate = connect()
    try:
        root, project = paths(client)
        python = root + "/conda/envs/epidemic-core/bin/python"
        code = r'''import hashlib,json,urllib.parse
from epidemic_core.discovery.safe_fetcher import FetchBlocked,SafeFetcher
urls=("https://dadosabertos.saude.gov.br/api/3/action/package_show?id=arboviroses-dengue","https://opendatasus.saude.gov.br/api/3/action/package_show?id=arboviroses-dengue")
attempts=[];fetched=None;payload=None
for url in urls:
 try:
  candidate=SafeFetcher(maximum_bytes=8*1024*1024,timeout_seconds=30).fetch(url,{"Accept":"application/json"})
  try:payload=json.loads(candidate.payload)
  except json.JSONDecodeError:attempts.append({"domain":urllib.parse.urlsplit(url).hostname,"http_status":candidate.http_status,"content_type":candidate.content_type,"result":"INVALID_PROVIDER_RESPONSE"});continue
  fetched=candidate;attempts.append({"domain":urllib.parse.urlsplit(url).hostname,"http_status":fetched.http_status,"content_type":fetched.content_type,"result":"SUCCESS"});break
 except FetchBlocked as exc:attempts.append({"domain":urllib.parse.urlsplit(url).hostname,"http_status":exc.http_status,"result":exc.code})
if fetched is None or payload is None:print(json.dumps({"attempts":attempts,"success":False,"resources":[]}));raise SystemExit()
result=payload.get("result",{});resources=result.get("resources",[])
rows=[]
for item in resources:
 raw=str(item.get("url") or "");parts=urllib.parse.urlsplit(raw)
 rows.append({"id":str(item.get("id") or ""),"name":str(item.get("name") or "")[:160],"format":str(item.get("format") or ""),"mimetype":str(item.get("mimetype") or ""),"domain":parts.hostname,"suffix":parts.path.rsplit(".",2)[-2:] if "." in parts.path else [],"size":item.get("size"),"last_modified":str(item.get("last_modified") or ""),"url_sha256":hashlib.sha256(raw.encode()).hexdigest()})
print(json.dumps({"attempts":attempts,"http_status":fetched.http_status,"response_sha256":fetched.response_sha256,"success":payload.get("success"),"dataset_id":str(result.get("id") or result.get("name") or ""),"dataset_title":str(result.get("title") or "")[:200],"resource_count":len(resources),"resources":rows},ensure_ascii=True))
'''
        command = f"source {q(project+'/scripts/source_discovery_worker/_common.sh')}; exec {q(python)} -B -c {q(code)}"
        value = json.loads(checked(client, f"bash -c {q(command)}", timeout=180))
        api_code = r'''import json,urllib.parse
from epidemic_core.discovery.safe_fetcher import FetchBlocked,SafeFetcher
fetcher=SafeFetcher(maximum_bytes=8*1024*1024,timeout_seconds=30);attempts=[]
swagger=fetcher.fetch("https://apidadosabertos.saude.gov.br/static/swagger.json",{"Accept":"application/json"});spec=json.loads(swagger.payload)
for url in ("https://apidadosabertos.saude.gov.br/arboviroses/dengue?nu_ano=2026&limit=20&offset=0","https://apidadosabertos.saude.gov.br/v1/arboviroses/dengue?nu_ano=2026&limit=20&offset=0"):
 try:
  response=fetcher.fetch(url,{"Accept":"application/json"});parsed=json.loads(response.payload)
  rows=parsed if isinstance(parsed,list) else next((parsed.get(key) for key in ("data","items","results","records") if isinstance(parsed.get(key),list)),[])
  columns=sorted({str(key) for row in rows[:20] if isinstance(row,dict) for key in row})
  shapes={str(key):({"type":type(value).__name__,"length":len(value)} if isinstance(value,(list,dict)) else {"type":type(value).__name__}) for key,value in parsed.items()} if isinstance(parsed,dict) else {"root":{"type":"list","length":len(parsed)}}
  nested_columns={str(key):sorted({str(field) for row in value[:20] if isinstance(row,dict) for field in row}) for key,value in parsed.items() if isinstance(parsed,dict) and isinstance(value,list)}
  attempts.append({"domain":urllib.parse.urlsplit(url).hostname,"path":urllib.parse.urlsplit(url).path,"http_status":response.http_status,"content_type":response.content_type,"response_sha256":response.response_sha256,"bytes":len(response.payload),"row_count":len(rows),"columns":columns,"shapes":shapes,"nested_columns":nested_columns,"result":"SUCCESS"})
 except FetchBlocked as exc:attempts.append({"domain":urllib.parse.urlsplit(url).hostname,"path":urllib.parse.urlsplit(url).path,"http_status":exc.http_status,"result":exc.code})
 except (json.JSONDecodeError,TypeError,ValueError):attempts.append({"domain":urllib.parse.urlsplit(url).hostname,"path":urllib.parse.urlsplit(url).path,"result":"INVALID_PROVIDER_RESPONSE"})
print(json.dumps({"swagger_http_status":swagger.http_status,"swagger_sha256":swagger.response_sha256,"swagger_version":str(spec.get("info",{}).get("version") or ""),"declares_dengue_path":"/arboviroses/dengue" in spec.get("paths",{}),"api_attempts":attempts},ensure_ascii=True))
'''
        api_command = f"source {q(project+'/scripts/source_discovery_worker/_common.sh')}; exec {q(python)} -B -c {q(api_code)}"
        api_value = json.loads(checked(client, f"bash -c {q(api_command)}", timeout=180))
        print(json.dumps({
            "target_gate": {"key_type": gate["key_type"], "stable_second_connection": True},
            "official_brazil_ckan_probe": value,
            "official_brazil_api_probe": api_value,
            "no_candidate_created": True,
            "no_source_server_connection": True,
        }, ensure_ascii=True, indent=2))
    finally:
        client.close()


def p1_8b_latest_funnel_action() -> None:
    client, gate = connect()
    try:
        root, project = paths(client)
        python = root + "/conda/envs/epidemic-core/bin/python"
        code = r'''import json,os
import sqlalchemy as sa
from sqlalchemy import text
engine=sa.create_engine(os.environ["APP_DATABASE_URL"],hide_parameters=True)
with engine.connect() as connection:
 run=connection.execute(text("SELECT id,request_json->>'country_iso3' country,state,terminal_reason_code,created_at,completed_at,progress_json FROM epidemic_discovery.discovery_runs ORDER BY created_at DESC LIMIT 1")).mappings().one()
 searches=[dict(row) for row in connection.execute(text("SELECT r.id search_result_id,r.provider_id,r.query_id,r.rank,r.title,r.domain,r.source_tier,r.media_type,r.disposition,r.stable_dataset_id FROM epidemic_discovery.source_discovery_search_results r WHERE r.discovery_run_id=:id ORDER BY r.query_id,r.rank LIMIT 100"),{"id":run["id"]}).mappings()]
 fetches=[dict(row) for row in connection.execute(text("SELECT f.id fetch_id,f.search_result_id,f.status,f.http_status,f.content_type,f.byte_size,f.domain,f.elapsed_ms,f.artifact_sha256,f.failure_code FROM epidemic_discovery.candidate_fetches f WHERE f.discovery_run_id=:id ORDER BY f.attempted_at,f.id LIMIT 100"),{"id":run["id"]}).mappings()]
 candidates=[dict(row) for row in connection.execute(text("SELECT c.id candidate_id,c.dataset_name,c.publisher,c.status,cv.id candidate_version_id,sp.row_count,sp.column_count,sp.date_min,sp.date_max,rc.id review_case_id,rc.state review_state,ar.sha256 artifact_sha256,ar.byte_size artifact_bytes FROM epidemic_discovery.source_candidates c JOIN epidemic_discovery.source_candidate_versions cv ON cv.candidate_id=c.id JOIN epidemic_discovery.source_schema_profiles sp ON sp.candidate_version_id=cv.id JOIN epidemic_discovery.source_review_cases rc ON rc.candidate_version_id=cv.id JOIN epidemic_discovery.quarantine_artifacts ar ON ar.candidate_version_id=cv.id WHERE c.discovery_run_id=:id"),{"id":run["id"]}).mappings()]
print(json.dumps({"run":dict(run),"search_results":searches,"fetches":fetches,"candidates":candidates},default=str,ensure_ascii=True))
'''
        command = f"source {q(project+'/scripts/source_discovery_worker/_common.sh')}; exec {q(python)} -B -c {q(code)}"
        value = json.loads(checked(client, f"bash -c {q(command)}", timeout=300))
        print(json.dumps({
            "target_gate": {"key_type": gate["key_type"], "stable_second_connection": True},
            "latest_discovery_funnel": value,
            "no_source_server_connection": True,
        }, ensure_ascii=True, indent=2))
    finally:
        client.close()


def p1_8b_preview_reconcile_action() -> None:
    client, gate = connect()
    try:
        root, project = paths(client)
        python = root + "/conda/envs/epidemic-core/bin/python"
        code = r'''import hashlib,json,os,pathlib
import sqlalchemy as sa
from sqlalchemy import text
from epidemic_core.discovery.extractor import profile_json
case_id="a0cc01f7-5ea3-4d07-b74a-d4e65d3ca9ea"
browser={"dt_notific":"2026-01-01","sg_uf_not":"32","id_municip":"320530","nu_ano":"2026","case_notification_count":"1"}
engine=sa.create_engine(os.environ["APP_DATABASE_URL"],hide_parameters=True)
with engine.connect() as connection:
 row=connection.execute(text("SELECT rc.candidate_version_id,qa.sha256,pr.sanitized_json,pr.row_ordinal FROM epidemic_discovery.source_review_cases rc JOIN epidemic_discovery.quarantine_artifacts qa ON qa.candidate_version_id=rc.candidate_version_id JOIN epidemic_discovery.source_preview_rows pr ON pr.candidate_version_id=rc.candidate_version_id WHERE rc.id=:id ORDER BY pr.row_ordinal LIMIT 1"),{"id":case_id}).mappings().one()
artifact=pathlib.Path(os.environ["DISCOVERY_QUARANTINE_ROOT"])/row["sha256"][:2]/row["sha256"]
payload=artifact.read_bytes()
if hashlib.sha256(payload).hexdigest()!=row["sha256"]:raise RuntimeError("artifact hash mismatch")
profile=profile_json(payload);sample=dict(profile["samples"][0]);sample["case_notification_count"]="1"
database={str(k):None if v is None else str(v) for k,v in dict(row["sanitized_json"]).items()}
def digest(value):return hashlib.sha256(str(value).encode()).hexdigest()
fields=[]
for name,browser_value in browser.items():
 artifact_value=sample.get(name);database_value=database.get(name)
 fields.append({"field":name,"browser_sha256":digest(browser_value),"database_sha256":digest(database_value),"artifact_sha256":digest(artifact_value),"match":browser_value==database_value==artifact_value})
print(json.dumps({"row_ordinal":row["row_ordinal"],"artifact_file_hash_verified":True,"profile_rows":profile["quality"]["rows"],"profile_columns":profile["quality"]["columns"],"fields":fields,"all_match":all(item["match"] for item in fields)}))
'''
        command = f"source {q(project+'/scripts/source_discovery_worker/_common.sh')}; exec {q(python)} -B -c {q(code)}"
        value = json.loads(checked(client, f"bash -c {q(command)}", timeout=300))
        print(json.dumps({
            "target_gate": {"key_type": gate["key_type"], "stable_second_connection": True},
            "preview_cell_reconciliation": value,
            "cell_values_emitted": False,
            "no_source_server_connection": True,
        }, indent=2))
    finally:
        client.close()


def p1_8b_final_audit_action() -> None:
    client, gate = connect()
    try:
        root, project = paths(client)
        python = root + "/conda/envs/epidemic-core/bin/python"
        run_id = "daa30d9d-59bd-4296-8b6d-50bab63fe37f"
        code = r'''import json,os
import sqlalchemy as sa
from sqlalchemy import text
run_id=os.environ["RUN_ID"]
engine=sa.create_engine(os.environ["APP_DATABASE_URL"],hide_parameters=True)
with engine.connect() as c:
 run=dict(c.execute(text("SELECT id,state,terminal_reason_code,created_at,completed_at,progress_json,request_json FROM epidemic_discovery.discovery_runs WHERE id=:id"),{"id":run_id}).mappings().one())
 events=[dict(row) for row in c.execute(text("SELECT event_code,details,created_at FROM epidemic_discovery.discovery_run_events WHERE discovery_run_id=:id ORDER BY created_at,id"),{"id":run_id}).mappings()]
 providers=[]
 for row in events:
  details=dict(row["details"] or {})
  if row["event_code"] in {"DIRECTORY_REQUEST_AUDIT","PROVIDER_REQUEST_AUDIT","PROVIDER_DEGRADED","PROVIDER_CONFIGURATION_MISSING","PROVIDER_REQUEST_NOT_EXECUTED"}:
   providers.append({"event_code":row["event_code"],"provider_id":details.get("provider_id"),"directory_id":details.get("directory_id"),"result":details.get("result"),"reason_code":details.get("reason_code"),"network_request":details.get("network_request"),"http_status":details.get("http_status"),"query_count":details.get("query_count"),"successful_query_count":details.get("successful_query_count"),"result_count":details.get("result_count"),"elapsed_ms":details.get("elapsed_ms"),"domain":details.get("domain")})
 searches=[dict(row) for row in c.execute(text("SELECT id,provider_id,query_id,rank,title,domain,source_tier,media_type,disposition,stable_dataset_id,result_sha256,observed_at FROM epidemic_discovery.source_discovery_search_results WHERE discovery_run_id=:id ORDER BY query_id,rank"),{"id":run_id}).mappings()]
 fetches=[dict(row) for row in c.execute(text("SELECT id,search_result_id,status,http_status,content_type,byte_size,domain,elapsed_ms,artifact_sha256,failure_code,attempted_at,completed_at FROM epidemic_discovery.candidate_fetches WHERE discovery_run_id=:id ORDER BY attempted_at,id"),{"id":run_id}).mappings()]
 candidates=[dict(row) for row in c.execute(text("SELECT sc.id candidate_id,sc.status candidate_status,sc.dataset_name,sc.publisher,sc.source_tier,sc.risk_flags,cv.id candidate_version_id,cv.license_status,cv.format,cv.content_sha256,qa.sha256 artifact_sha256,qa.byte_size,qa.mime_type,qa.security_status,sp.row_count,sp.column_count,sp.date_min,sp.date_max,sqa.status quality_status,sqa.pii_status,rc.id review_case_id,rc.state review_state,(SELECT count(*) FROM epidemic_discovery.source_preview_rows pr WHERE pr.candidate_version_id=cv.id) preview_rows,(SELECT count(*) FROM epidemic_discovery.source_review_decisions d WHERE d.review_case_id=rc.id) decision_count FROM epidemic_discovery.source_candidates sc JOIN epidemic_discovery.source_candidate_versions cv ON cv.candidate_id=sc.id JOIN epidemic_discovery.quarantine_artifacts qa ON qa.candidate_version_id=cv.id JOIN epidemic_discovery.source_schema_profiles sp ON sp.candidate_version_id=cv.id JOIN epidemic_discovery.source_quality_assessments sqa ON sqa.candidate_version_id=cv.id JOIN epidemic_discovery.source_review_cases rc ON rc.candidate_version_id=cv.id WHERE sc.discovery_run_id=:id"),{"id":run_id}).mappings()]
 boundaries={"run_decisions":c.execute(text("SELECT count(*) FROM epidemic_discovery.source_review_decisions d JOIN epidemic_discovery.source_review_cases rc ON rc.id=d.review_case_id JOIN epidemic_discovery.source_candidate_versions cv ON cv.id=rc.candidate_version_id JOIN epidemic_discovery.source_candidates sc ON sc.id=cv.candidate_id WHERE sc.discovery_run_id=:id"),{"id":run_id}).scalar_one(),"run_promotions":c.execute(text("SELECT count(*) FROM epidemic_discovery.source_promotion_runs p JOIN epidemic_discovery.source_review_decisions d ON d.id=p.review_decision_id JOIN epidemic_discovery.source_review_cases rc ON rc.id=d.review_case_id JOIN epidemic_discovery.source_candidate_versions cv ON cv.id=rc.candidate_version_id JOIN epidemic_discovery.source_candidates sc ON sc.id=cv.candidate_id WHERE sc.discovery_run_id=:id"),{"id":run_id}).scalar_one(),"formal_source_match":c.execute(text("SELECT count(*) FROM epidemic.raw_artifacts ra JOIN epidemic_discovery.quarantine_artifacts qa ON qa.sha256=ra.sha256 JOIN epidemic_discovery.source_candidate_versions cv ON cv.id=qa.candidate_version_id JOIN epidemic_discovery.source_candidates sc ON sc.id=cv.candidate_id WHERE sc.discovery_run_id=:id"),{"id":run_id}).scalar_one()}
 print(json.dumps({"run":run,"providers":providers,"search_results":searches,"fetches":fetches,"candidates":candidates,"boundaries":boundaries},default=str,ensure_ascii=True))
'''
        command = (
            f"source {q(project+'/scripts/source_discovery_worker/_common.sh')}; "
            f"RUN_ID={q(run_id)} exec {q(python)} -B -c {q(code)}"
        )
        discovery = json.loads(checked(client, f"bash -c {q(command)}", timeout=600))
        counts_sql = "SELECT json_build_object(" + ",".join(
            f"'{name}',(SELECT count(*) FROM epidemic.{name})" for name in EXPECTED_COUNTS
        ) + ")::text"
        snapshots_sql = (
            "SELECT COALESCE(json_agg(json_build_object('sha256',content_sha256,'row_count',row_count) "
            "ORDER BY id),'[]'::json)::text FROM epidemic.dataset_snapshots"
        )
        counts = json.loads(db_query(client, counts_sql))
        snapshots = json.loads(db_query(client, snapshots_sql))
        formal = {
            "counts": counts,
            "counts_match": counts == EXPECTED_COUNTS,
            "snapshots": snapshots,
            "snapshot_hashes_match": {row["sha256"]: row["row_count"] for row in snapshots}
            == EXPECTED_SNAPSHOTS,
        }
        review_code = r'''import json,os
import sqlalchemy as sa
from sqlalchemy import text
engine=sa.create_engine(os.environ["REVIEW_DATABASE_URL"],hide_parameters=True)
with engine.connect() as c:
 result={"current_role_is_reviewer":c.execute(text("SELECT current_user='epidemicreview'")).scalar_one(),"candidate_insert":c.execute(text("SELECT has_table_privilege(current_user,'epidemic_discovery.source_candidates','INSERT')")).scalar_one(),"review_decision_insert":c.execute(text("SELECT has_table_privilege(current_user,'epidemic_discovery.source_review_decisions','INSERT')")).scalar_one(),"formal_insert":c.execute(text("SELECT has_table_privilege(current_user,'epidemic.normalized_observations','INSERT')")).scalar_one(),"promotion_insert":c.execute(text("SELECT has_table_privilege(current_user,'epidemic_discovery.source_promotion_runs','INSERT')")).scalar_one(),"review_cards_select":c.execute(text("SELECT has_table_privilege(current_user,'epidemic_discovery.review_candidate_cards_v2','SELECT')")).scalar_one(),"review_preview_select":c.execute(text("SELECT has_table_privilege(current_user,'epidemic_discovery.review_preview_v','SELECT')")).scalar_one(),"review_runs_select":c.execute(text("SELECT has_table_privilege(current_user,'epidemic_discovery.review_discovery_runs_v2','SELECT')")).scalar_one()}
print(json.dumps(result))
'''
        review_command = (
            f"source {q(project+'/scripts/source_review/_common.sh')}; "
            'set -a; source "$CONFIG"; set +a; '
            f"exec {q(python)} -B -c {q(review_code)}"
        )
        review_permissions = json.loads(checked(client, f"bash -c {q(review_command)}", timeout=300))
        service_paths = {
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
        }
        services = {name: run(client, q(project + "/" + relative), timeout=180)[0] == 0 for name, relative in service_paths.items()}
        runtime_code = r'''import json,os,pathlib
ports={5432,8000,8010,18789,8020,8021};listeners=[]
for filename,v6 in (("/proc/net/tcp",False),("/proc/net/tcp6",True)):
 for line in pathlib.Path(filename).read_text().splitlines()[1:]:
  parts=line.split();address,port_hex=parts[1].split(":");port=int(port_hex,16)
  if port in ports and parts[3]=="0A":listeners.append({"port":port,"loopback":address==("00000000000000000000000001000000" if v6 else "0100007F")})
project=os.environ["PROJECT"].encode();project_processes=0;proxy_processes=0
for proc in pathlib.Path("/proc").iterdir():
 if not proc.name.isdigit():continue
 try:cmd=(proc/"cmdline").read_bytes();env=(proc/"environ").read_bytes()
 except Exception:continue
 if project not in cmd:continue
 project_processes+=1;names={item.split(b"=",1)[0] for item in env.split(b"\0") if b"=" in item}
 proxy_processes+=int(bool(names&{b"http_proxy",b"https_proxy",b"HTTP_PROXY",b"HTTPS_PROXY",b"all_proxy",b"ALL_PROXY"}))
print(json.dumps({"listeners":listeners,"project_processes":project_processes,"proxy_processes":proxy_processes}))
'''
        runtime = json.loads(checked(client, f"PROJECT={q(project)} {q(python)} -B -c {q(runtime_code)}"))
        runtime["new_session_proxy_absent"] = checked(
            client,
            "bash -lc '[[ -z ${HTTP_PROXY:-}${HTTPS_PROXY:-}${ALL_PROXY:-}${http_proxy:-}${https_proxy:-}${all_proxy:-} ]] && echo yes || echo no'",
        ) == "yes"
        scan_code = r'''import hashlib,json,os,pathlib,re,subprocess
project=pathlib.Path(os.environ["PROJECT"]);files=json.loads(os.environ["FILES"]);findings=[];large=[]
rules={"private_key":r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----","cloud_token":r"(?:AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9_]{30,}|sk-[A-Za-z0-9]{24,})","credential_url":r"(?:postgresql|https?)://[^\s:/]+:[^\s/@]+@","tls_bypass":r"curl\s+-k(?:\s|$)|wget\s+--no-check-certificate|GIT_SSL_NO_VERIFY\s*=\s*(?:1|true)|pip\s+[^\n]*--trusted-host","proxy_literal":r"(?i)(?:http|https|all)_proxy\s*=\s*['\"]?(?:https?|socks)://"}
for rel in files:
 path=project/rel
 if not path.is_file():continue
 if path.stat().st_size>10*1024*1024:large.append({"path":rel,"bytes":path.stat().st_size})
 content=path.read_text(errors="replace")
 for name,pattern in rules.items():
  for match in re.finditer(pattern,content,re.I|re.M):findings.append({"path":rel,"line":content.count("\n",0,match.start())+1,"rule":name,"match_sha256":hashlib.sha256(match.group().encode()).hexdigest()})
status=subprocess.run(["git","-C",str(project),"status","--porcelain=v1","-uall"],text=True,capture_output=True,check=True).stdout.splitlines()
scoped=[{"status":line[:2],"path":line[3:]} for line in status if line[3:] in files]
staged=subprocess.run(["git","-C",str(project),"diff","--cached","--name-only"],text=True,capture_output=True,check=True).stdout.splitlines()
print(json.dumps({"findings":findings,"large_files":large,"scoped_changes":scoped,"all_dirty_count":len(status),"staged_paths":staged}))
'''
        scan = json.loads(
            checked(
                client,
                f"PROJECT={q(project)} FILES={q(json.dumps(P1_8B_SYNC_FILES))} {q(python)} -B -c {q(scan_code)}",
                timeout=600,
            )
        )
        result = {
            "target_gate": {"key_type": gate["key_type"], "stable_second_connection": True},
            "alembic": db_query(client, "SELECT version_num FROM epidemic.alembic_version"),
            "discovery": discovery,
            "formal_database": formal,
            "review_permissions": review_permissions,
            "services": services,
            "runtime": runtime,
            "scan": scan,
            "git": {
                "head": checked(client, f"cd {q(project)} && git rev-parse HEAD"),
                "branch": checked(client, f"cd {q(project)} && git branch --show-current"),
                "diff_check": run(client, f"cd {q(project)} && git diff --check", timeout=600)[0] == 0,
            },
            "no_source_server_connection": True,
        }
        result["required_pass"] = (
            result["alembic"] == EXPECTED_MIGRATION
            and discovery["run"]["state"] == "REVIEW_READY"
            and discovery["boundaries"] == {"run_decisions": 0, "run_promotions": 0, "formal_source_match": 0}
            and formal["counts_match"]
            and formal["snapshot_hashes_match"]
            and review_permissions["current_role_is_reviewer"]
            and not any(review_permissions[key] for key in ("candidate_insert", "review_decision_insert", "formal_insert", "promotion_insert"))
            and all(review_permissions[key] for key in ("review_cards_select", "review_preview_select", "review_runs_select"))
            and all(services.values())
            and runtime["proxy_processes"] == 0
            and runtime["new_session_proxy_absent"]
            and all(item["loopback"] for item in runtime["listeners"])
            and not scan["findings"]
            and not scan["large_files"]
            and not scan["staged_paths"]
            and result["git"]["diff_check"]
        )
        print(json.dumps(result, indent=2))
    finally:
        client.close()


def p1_8b_npm_audit_action() -> None:
    client, gate = connect()
    try:
        root, project = paths(client)
        npm = project + "/runtime/node-v24.18.0-linux-x64/bin/npm"
        node_bin = project + "/runtime/node-v24.18.0-linux-x64/bin"
        plugin = project + "/integrations/openclaw/epidemic-query-tools"
        command = (
            f"export PATH={q(node_bin)}:$PATH; cd {q(plugin)}; "
            f"exec {q(npm)} audit --json --registry=https://registry.npmjs.org "
            "--fetch-retries=1 --fetch-timeout=60000"
        )
        started = time.monotonic()
        rc, out, err = run(client, f"bash -c {q(command)}", timeout=180)
        parsed: dict[str, object] = {}
        try:
            value = json.loads(out)
            if isinstance(value, dict):
                parsed = {
                    "audit_report_version": value.get("auditReportVersion"),
                    "vulnerabilities": value.get("metadata", {}).get("vulnerabilities", {})
                    if isinstance(value.get("metadata"), dict)
                    else {},
                    "error_code": value.get("error", {}).get("code")
                    if isinstance(value.get("error"), dict)
                    else None,
                    "error_summary": value.get("error", {}).get("summary")
                    if isinstance(value.get("error"), dict)
                    else None,
                }
        except Exception:
            parsed = {"parseable": False}
        safe_error = re.sub(
            r"(?i)(password|token|secret|api[_-]?key)(\s*[=:]\s*)\S+",
            r"\1\2[redacted]",
            err,
        )
        safe_error = re.sub(r"https?://\S+", "[official-registry-url]", safe_error)
        print(json.dumps({
            "target_gate": {"key_type": gate["key_type"], "stable_second_connection": True},
            "official_registry_domain": "registry.npmjs.org",
            "rc": rc,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "result": parsed,
            "stderr_sha256": sha_text(safe_error),
            "credential_value_emitted": False,
            "no_source_server_connection": True,
        }, indent=2))
    finally:
        client.close()


def p1_8b_stage_action() -> None:
    client, gate = connect()
    try:
        _root, project = paths(client)
        if checked(client, f"cd {q(project)} && git rev-parse HEAD") != P1_8A_COMMIT:
            raise GateError("P1_8B_STAGE_HEAD_DRIFT")
        if checked(client, f"cd {q(project)} && git diff --cached --name-only").splitlines():
            raise GateError("P1_8B_PREEXISTING_STAGED_CHANGES")
        existing = [
            item for item in P1_8B_SYNC_FILES if run(client, f"test -f {q(project+'/'+item)}")[0] == 0
        ]
        scoped_before = checked(
            client,
            f"cd {q(project)} && git status --porcelain=v1 -uall -- " + " ".join(map(q, existing)),
        ).splitlines()
        changed_before = {line[3:] for line in scoped_before}
        if not changed_before:
            raise GateError("P1_8B_NOTHING_TO_STAGE")
        rc, _out, err = run(
            client,
            f"cd {q(project)} && git add -- " + " ".join(map(q, existing)),
            timeout=600,
        )
        if rc:
            raise GateError("P1_8B_GIT_ADD_FAILED:" + err[-300:])
        staged = checked(client, f"cd {q(project)} && git diff --cached --name-only").splitlines()
        if set(staged) != changed_before or not set(staged).issubset(set(existing)):
            raise GateError("P1_8B_STAGED_SCOPE_MISMATCH")
        if run(client, f"cd {q(project)} && git diff --cached --check", timeout=600)[0] != 0:
            raise GateError("P1_8B_STAGED_DIFF_CHECK_FAILED")
        scan_code = r'''import hashlib,json,os,pathlib,re,subprocess
project=pathlib.Path(os.environ["PROJECT"]);paths=json.loads(os.environ["PATHS"]);findings=[];large=[]
rules={"private_key":r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----","cloud_token":r"(?:AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9_]{30,}|sk-[A-Za-z0-9]{24,})","credential_url":r"(?:postgresql|https?)://[^\s:/]+:[^\s/@]+@","tls_bypass":r"curl\s+-k(?:\s|$)|wget\s+--no-check-certificate|GIT_SSL_NO_VERIFY\s*=\s*(?:1|true)|pip\s+[^\n]*--trusted-host","proxy_literal":r"(?i)(?:http|https|all)_proxy\s*=\s*['\"]?(?:https?|socks)://"}
for rel in paths:
 path=project/rel
 if path.stat().st_size>10*1024*1024:large.append({"path":rel,"bytes":path.stat().st_size})
 content=path.read_text(errors="replace")
 for name,pattern in rules.items():
  for match in re.finditer(pattern,content,re.I|re.M):findings.append({"path":rel,"line":content.count("\n",0,match.start())+1,"rule":name,"match_sha256":hashlib.sha256(match.group().encode()).hexdigest()})
print(json.dumps({"findings":findings,"large_files":large}))
'''
        root, _project = paths(client)
        scan = json.loads(
            checked(
                client,
                f"PROJECT={q(project)} PATHS={q(json.dumps(staged))} {q(root+'/conda/envs/epidemic-core/bin/python')} -B -c {q(scan_code)}",
                timeout=600,
            )
        )
        if scan != {"findings": [], "large_files": []}:
            raise GateError("P1_8B_STAGED_SECURITY_SCAN_FAILED")
        print(json.dumps({
            "target_gate": {"key_type": gate["key_type"], "stable_second_connection": True},
            "staged_paths": staged,
            "staged_count": len(staged),
            "diff_check": True,
            "secret_scan": True,
            "large_file_scan": True,
            "no_source_server_connection": True,
        }, indent=2))
    finally:
        client.close()


def p1_8b_commit_action() -> None:
    client, gate = connect()
    try:
        _root, project = paths(client)
        if checked(client, f"cd {q(project)} && git rev-parse HEAD") != P1_8A_COMMIT:
            raise GateError("P1_8B_COMMIT_HEAD_DRIFT")
        staged = checked(client, f"cd {q(project)} && git diff --cached --name-only").splitlines()
        if not staged or not set(staged).issubset(set(P1_8B_SYNC_FILES)):
            raise GateError("P1_8B_COMMIT_STAGED_SCOPE_INVALID")
        subject = "feat(p1-8b): add verifiable source discovery previews"
        rc, _out, err = run(client, f"cd {q(project)} && git commit -m {q(subject)}", timeout=600)
        if rc:
            raise GateError("P1_8B_COMMIT_FAILED:" + err[-300:])
        head = checked(client, f"cd {q(project)} && git rev-parse HEAD")
        parent = checked(client, f"cd {q(project)} && git rev-parse HEAD^")
        actual_subject = checked(client, f"cd {q(project)} && git log -1 --pretty=%s")
        if parent != P1_8A_COMMIT or actual_subject != subject:
            raise GateError("P1_8B_COMMIT_VERIFICATION_FAILED")
        remaining = checked(client, f"cd {q(project)} && git status --porcelain=v1 -uall").splitlines()
        print(json.dumps({
            "target_gate": {"key_type": gate["key_type"], "stable_second_connection": True},
            "commit": head,
            "parent": parent,
            "subject": actual_subject,
            "staged_empty": not checked(client, f"cd {q(project)} && git diff --cached --name-only").splitlines(),
            "remaining_preexisting_dirty_count": len(remaining),
            "push_performed": False,
            "no_source_server_connection": True,
        }, indent=2))
    finally:
        client.close()


def p1_8b_stage_inspect_action() -> None:
    client, gate = connect()
    try:
        _root, project = paths(client)
        existing = [
            item for item in P1_8B_SYNC_FILES if run(client, f"test -f {q(project+'/'+item)}")[0] == 0
        ]
        scoped = checked(
            client,
            f"cd {q(project)} && git status --porcelain=v1 -uall -- " + " ".join(map(q, existing)),
        ).splitlines()
        staged = checked(client, f"cd {q(project)} && git diff --cached --name-only").splitlines()
        unstaged = checked(client, f"cd {q(project)} && git diff --name-only -- " + " ".join(map(q, existing))).splitlines()
        print(json.dumps({
            "target_gate": {"key_type": gate["key_type"], "stable_second_connection": True},
            "scoped_status": [{"status": line[:2], "path": line[3:]} for line in scoped],
            "staged_paths": staged,
            "unstaged_scoped_paths": unstaged,
            "allowed": set(staged).issubset(set(existing)),
            "no_source_server_connection": True,
        }, indent=2))
    finally:
        client.close()


def p1_8b_extractor_diagnose_action() -> None:
    client, gate = connect()
    try:
        root, project = paths(client)
        python = root + "/conda/envs/epidemic-core/bin/python"
        code = r'''import json,os,pathlib,pwd,shutil,subprocess,sys
import sqlalchemy as sa
from sqlalchemy import text
engine=sa.create_engine(os.environ["APP_DATABASE_URL"],hide_parameters=True)
with engine.connect() as connection:
 row=connection.execute(text("SELECT f.artifact_sha256,f.content_type,f.byte_size FROM epidemic_discovery.candidate_fetches f WHERE f.discovery_run_id=(SELECT id FROM epidemic_discovery.discovery_runs ORDER BY created_at DESC LIMIT 1) AND f.artifact_sha256 IS NOT NULL AND f.failure_code='QUARANTINE_EXTRACTION_FAILED' ORDER BY f.byte_size LIMIT 1")).mappings().one()
sha=row["artifact_sha256"];path=pathlib.Path(os.environ["DISCOVERY_QUARANTINE_ROOT"])/sha[:2]/sha
extractor=pathlib.Path(os.environ["DISCOVERY_EXTRACTOR_PATH"]);account_exists=True
try:pwd.getpwnam("epidemicextract")
except KeyError:account_exists=False
kind="csv" if row["content_type"] in ("text/csv","text/plain") else "json"
privilege=["--no-new-privs"]
configured_account=os.environ.get("DISCOVERY_EXTRACTOR_ACCOUNT")
if configured_account:privilege=[f"--reuid={configured_account}",f"--regid={configured_account}","--clear-groups",*privilege]
with path.open("rb") as source:
 completed=subprocess.run(["setpriv",*privilege,"prlimit","--cpu=120","--as=2147483648","--nofile=64","/usr/bin/python3",str(extractor),"--fd",str(source.fileno()),"--format",kind,"--enforce-no-network"],env={"PATH":"/usr/sbin:/usr/bin:/sbin:/bin","LANG":"C.UTF-8"},pass_fds=(source.fileno(),),capture_output=True,timeout=125,check=False)
rootless_userns=subprocess.run(["unshare","-Ur","true"],capture_output=True,check=False).returncode==0 if shutil.which("unshare") else False
no_new_privs=subprocess.run(["setpriv","--no-new-privs","true"],capture_output=True,check=False).returncode==0
network_self_test_passed=False;parsed_rows=None;parsed_columns=None
if completed.returncode==0:
 result=json.loads(completed.stdout);network_self_test_passed=result.get("network_policy")=="seccomp_socket_syscalls_denied";parsed_rows=result.get("quality",{}).get("rows");parsed_columns=result.get("quality",{}).get("columns")
print(json.dumps({"account_exists":account_exists,"isolation_mode":"dedicated_account" if configured_account else "same_account_no_new_privileges","artifact_exists":path.is_file(),"artifact_bytes":row["byte_size"],"extractor_exists":extractor.is_file(),"extractor_mode":oct(extractor.stat().st_mode&511) if extractor.exists() else None,"returncode":completed.returncode,"stdout_bytes":len(completed.stdout),"stderr_tail":completed.stderr.decode("utf-8","replace").splitlines()[-10:],"network_self_test_passed":network_self_test_passed,"parsed_rows":parsed_rows,"parsed_columns":parsed_columns,"bubblewrap_available":bool(shutil.which("bwrap")),"rootless_userns_available":rootless_userns,"no_new_privs_available":no_new_privs,"system_python_available":pathlib.Path("/usr/bin/python3").is_file()}))
'''
        command = f"source {q(project+'/scripts/source_discovery_worker/_common.sh')}; exec {q(python)} -B -c {q(code)}"
        value = json.loads(checked(client, f"bash -c {q(command)}", timeout=180))
        safe_tail = [line.replace(project, "[project]").replace(root, "[data-root]") for line in value["stderr_tail"]]
        value["stderr_tail"] = [re.sub(r"/(?:root|home/[^/\s]+)(?=/|\s|$)", "[home]", line) for line in safe_tail]
        print(json.dumps({
            "target_gate": {"key_type": gate["key_type"], "stable_second_connection": True},
            "extractor_diagnostic": value,
            "artifact_content_not_emitted": True,
            "no_source_server_connection": True,
        }, indent=2))
    finally:
        client.close()


def p1_8b_review_auth_prepare_action() -> None:
    client, gate = connect()
    local_path = Path(os.environ.get("TEMP", str(ROOT))) / "codex-epidemic-review-browser.tmp"
    try:
        root, project = paths(client)
        auth_path = root + "/epidemic-source-review-runtime/secrets/login_hash.json"
        backup_path = auth_path + ".p1_8b_browser_backup"
        sftp = client.open_sftp()
        try:
            try:
                sftp.stat(backup_path)
            except OSError:
                pass
            else:
                raise GateError("P1_8B_BROWSER_AUTH_BACKUP_ALREADY_EXISTS")
            with sftp.file(auth_path, "rb") as handle:
                original = handle.read()
            value = json.loads(original)
            username = str(value["username"])
            password = secrets.token_urlsafe(48)
            salt = secrets.token_bytes(16)
            replacement = {
                "username": username,
                "salt_hex": salt.hex(),
                "n": 16384,
                "r": 8,
                "p": 1,
                "hash_hex": hashlib.scrypt(
                    password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32
                ).hex(),
            }
            with sftp.file(backup_path, "wb") as handle:
                handle.write(original)
            sftp.chmod(backup_path, 0o600)
            with sftp.file(auth_path, "wb") as handle:
                handle.write((json.dumps(replacement, separators=(",", ":")) + "\n").encode())
            sftp.chmod(auth_path, 0o600)
            local_path.write_text(username + "\n" + password + "\n", encoding="utf-8")
            os.chmod(local_path, 0o600)
        finally:
            sftp.close()
        python = root + "/conda/envs/epidemic-core/bin/python"
        runtime = root + "/epidemic-source-review-runtime"
        auth_probe_code = r'''import http.client,json,pathlib,sys,urllib.parse
from epidemic_core.discovery.review_app import _auth
value=json.loads(sys.stdin.read());auth_path=pathlib.Path(sys.argv[1])
direct=_auth(auth_path,value["username"],value["password"])
body=urllib.parse.urlencode(value);connection=http.client.HTTPConnection("127.0.0.1",8021,timeout=10)
connection.request("POST","/login",body=body,headers={"Content-Type":"application/x-www-form-urlencoded"})
response=connection.getresponse();response.read();result={"direct_auth":direct,"http_status":response.status,"location":response.getheader("Location"),"session_cookie_set":bool(response.getheader("Set-Cookie"))};connection.close()
print(json.dumps(result))
'''
        auth_command = (
            f"PYTHONPATH={q(runtime+'/src')} {q(python)} -B -c {q(auth_probe_code)} {q(auth_path)}"
        )
        probe_rc, probe_out, _probe_err = run(
            client,
            auth_command,
            timeout=30,
            input_text=json.dumps({"username": username, "password": password}),
        )
        probe = json.loads(probe_out) if probe_rc == 0 else {"direct_auth": False}
        if not (
            probe.get("direct_auth")
            and probe.get("http_status") == 303
            and probe.get("location") == "/reviews"
            and probe.get("session_cookie_set")
        ):
            raise GateError("P1_8B_TEMPORARY_BROWSER_AUTH_SELF_TEST_FAILED")
        print(json.dumps({
            "target_gate": {"key_type": gate["key_type"], "stable_second_connection": True},
            "temporary_browser_auth_installed": True,
            "temporary_browser_auth_self_test": True,
            "original_auth_backed_up": True,
            "credential_value_emitted": False,
            "temporary_credential_location": "[system-temp]/codex-epidemic-review-browser.tmp",
            "no_source_server_connection": True,
        }, indent=2))
    finally:
        client.close()


def p1_8b_review_diagnose_action() -> None:
    client, gate = connect()
    try:
        root, _project = paths(client)
        log_path = root + "/epidemic-source-review-runtime/log/review.log"
        output = checked(client, f"tail -n 30 {q(log_path)}")
        env_names = checked(
            client,
            f"sed -n -E 's/^([A-Z][A-Z0-9_]*)=.*/\\1/p' {q(_project+'/.secrets/postgres.env')} | sort",
        ).splitlines()
        review_env_names = checked(
            client,
            f"source {q(_project+'/scripts/source_review/_common.sh')}; sed -n -E 's/^([A-Z][A-Z0-9_]*)=.*/\\1/p' \"$CONFIG\" | sort",
        ).splitlines()
        probe_code = r'''import json,os,pathlib
from sqlalchemy.engine import make_url
url=make_url(os.environ["REVIEW_DATABASE_URL"])
socket_dir=url.query.get("host")
pid=int(pathlib.Path(os.environ["PIDFILE"]).read_text())
levels=[]
if socket_dir:
 current=pathlib.Path("/")
 for index,part in enumerate(pathlib.Path(socket_dir).parts[1:]):
  current=current/part
  try:
   stat=current.stat();levels.append({"level":index,"uid":stat.st_uid,"gid":stat.st_gid,"mode":oct(stat.st_mode & 0o777),"traversable":os.access(current,os.X_OK)})
  except OSError as error:
   levels.append({"level":index,"error":type(error).__name__});break
result={"dsn_user_present":bool(url.username),"dsn_password_present":url.password is not None,"dsn_tcp_host_present":bool(url.host),"dsn_socket_query_present":bool(socket_dir),"service_euid":int(pathlib.Path(f"/proc/{pid}/status").read_text().split("Uid:",1)[1].split()[0]),"probe_euid":os.geteuid(),"socket_dir_traversable":bool(socket_dir and os.access(socket_dir,os.X_OK)),"socket_path_levels":levels}
print(json.dumps(result))
'''
        common = _project + "/scripts/source_review/_common.sh"
        probe_command = f"source {q(common)}; set -a; source \"$CONFIG\"; set +a; export PIDFILE; exec {q(root+'/conda/envs/epidemic-core/bin/python')} -B -c {q(probe_code)}"
        runtime_probe = json.loads(checked(client, f"bash -c {q(probe_command)}"))
        sanitized = output.replace(root, "[data-root]")
        sanitized = re.sub(r"/(?:root|home/[^/\s]+)(?=/|\s|$)", "[home]", sanitized)
        print(
            json.dumps(
                {
                    "target_gate": {"key_type": gate["key_type"], "stable_second_connection": True},
                    "review_log_tail": sanitized.splitlines(),
                    "postgres_secret_variable_names": env_names,
                    "review_secret_variable_names": review_env_names,
                    "review_runtime_probe": runtime_probe,
                    "no_secret_values_requested": True,
                    "no_source_server_connection": True,
                },
                indent=2,
            )
        )
    finally:
        client.close()


def p1_8b_review_auth_restore_action() -> None:
    client, gate = connect()
    local_path = Path(os.environ.get("TEMP", str(ROOT))) / "codex-epidemic-review-browser.tmp"
    try:
        root, project = paths(client)
        auth_path = root + "/epidemic-source-review-runtime/secrets/login_hash.json"
        backup_path = auth_path + ".p1_8b_browser_backup"
        sftp = client.open_sftp()
        try:
            with sftp.file(backup_path, "rb") as handle:
                original = handle.read()
            with sftp.file(auth_path, "wb") as handle:
                handle.write(original)
            sftp.chmod(auth_path, 0o600)
            sftp.remove(backup_path)
        finally:
            sftp.close()
        local_path.unlink(missing_ok=True)
        print(json.dumps({
            "target_gate": {"key_type": gate["key_type"], "stable_second_connection": True},
            "original_browser_auth_restored": True,
            "temporary_credential_removed": not local_path.exists(),
            "credential_value_emitted": False,
            "no_source_server_connection": True,
        }, indent=2))
    finally:
        client.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        choices=(
            "preflight",
            "backup-preflight",
            "openclaw-device-inspect",
            "p1-8a-script-inspect",
            "trigger-admin-request",
            "approve-admin-request",
            "pending-admin-audit",
            "approve-help",
            "cron-install-idempotency",
            "cron-run-help",
            "cron-forced-run",
            "daily-concurrency",
            "cron-gateway-restart",
            "openclaw-restart-diagnose",
            "daily-code-inspect",
            "daily-test-inspect",
            "daily-unit-tests",
            "daily-runtime-status",
            "daily-schema-inspect",
            "p1-8a-path-drift",
            "isolated-full-test",
            "reference-seed-inspect",
            "metric-consistency",
            "negative-contract-inspect",
            "negative-contract-compact",
            "fix-reference-revision-gate",
            "runtime-command-inventory",
            "p1-8a-final-audit",
            "p1-8a-diff-summary",
            "portable-p1-8-paths",
            "p1-8a-stage",
            "p1-8a-commit",
            "p1-8a-stage-inspect",
            "p1-8b-inventory",
            "p1-8b-database",
            "p1-8b-code",
            "p1-8b-download",
            "p1-8b-refresh-manifest",
            "p1-8b-runs",
            "p1-8b-openclaw-capabilities",
            "p1-8b-sync",
            "p1-8b-remote-format",
            "p1-8b-remote-static",
            "p1-8b-migrate-restart",
            "p1-8b-db-access",
            "p1-8b-run-chn",
            "p1-8b-run-fra",
            "p1-8b-run-bra",
            "p1-8b-latest-state",
            "p1-8b-datagouv-probe",
            "p1-8b-brazil-ckan-probe",
            "p1-8b-latest-funnel",
            "p1-8b-preview-reconcile",
            "p1-8b-final-audit",
            "p1-8b-npm-audit",
            "p1-8b-stage",
            "p1-8b-stage-inspect",
            "p1-8b-commit",
            "p1-8b-extractor-diagnose",
            "p1-8b-review-diagnose",
            "p1-8b-review-auth-prepare",
            "p1-8b-review-auth-restore",
        ),
    )
    parser.add_argument("--section",choices=("providers","worker","candidate","service","tests","registry"),default="providers")
    args = parser.parse_args()
    if args.action == "preflight":
        preflight_action()
    elif args.action == "backup-preflight":
        backup_preflight_action()
    elif args.action == "openclaw-device-inspect":
        openclaw_device_inspect_action()
    elif args.action == "p1-8a-script-inspect":
        p1_8a_script_inspect_action()
    elif args.action == "trigger-admin-request":
        trigger_admin_request_action()
    elif args.action == "approve-admin-request":
        approve_admin_request_action()
    elif args.action == "pending-admin-audit":
        pending_admin_audit_action()
    elif args.action == "approve-help":
        approve_help_action()
    elif args.action == "cron-install-idempotency":
        cron_install_idempotency_action()
    elif args.action == "cron-run-help":
        cron_run_help_action()
    elif args.action == "cron-forced-run":
        cron_forced_run_action()
    elif args.action == "daily-concurrency":
        daily_concurrency_action()
    elif args.action == "cron-gateway-restart":
        cron_gateway_restart_action()
    elif args.action == "openclaw-restart-diagnose":
        openclaw_restart_diagnose_action()
    elif args.action == "daily-code-inspect":
        daily_code_inspect_action()
    elif args.action == "daily-test-inspect":
        daily_test_inspect_action()
    elif args.action == "daily-unit-tests":
        daily_unit_tests_action()
    elif args.action == "daily-runtime-status":
        daily_runtime_status_action()
    elif args.action == "daily-schema-inspect":
        daily_schema_inspect_action()
    elif args.action == "p1-8a-path-drift":
        p1_8a_path_drift_action()
    elif args.action == "isolated-full-test":
        isolated_full_test_action()
    elif args.action == "reference-seed-inspect":
        reference_seed_inspect_action()
    elif args.action == "metric-consistency":
        metric_consistency_action()
    elif args.action == "negative-contract-inspect":
        negative_contract_inspect_action()
    elif args.action == "negative-contract-compact":
        negative_contract_compact_action()
    elif args.action == "fix-reference-revision-gate":
        fix_reference_revision_gate_action()
    elif args.action == "runtime-command-inventory":
        runtime_command_inventory_action()
    elif args.action == "p1-8a-final-audit":
        p1_8a_final_audit_action()
    elif args.action == "p1-8a-diff-summary":
        p1_8a_diff_summary_action()
    elif args.action == "portable-p1-8-paths":
        portable_p1_8_paths_action()
    elif args.action == "p1-8a-stage":
        p1_8a_stage_action()
    elif args.action == "p1-8a-commit":
        p1_8a_commit_action()
    elif args.action == "p1-8a-stage-inspect":
        p1_8a_stage_inspect_action()
    elif args.action == "p1-8b-inventory":
        p1_8b_inventory_action()
    elif args.action == "p1-8b-database":
        p1_8b_database_action()
    elif args.action == "p1-8b-code":
        p1_8b_code_action(args.section)
    elif args.action == "p1-8b-download":
        p1_8b_download_action()
    elif args.action == "p1-8b-refresh-manifest":
        p1_8b_refresh_manifest_action()
    elif args.action == "p1-8b-runs":
        p1_8b_runs_action()
    elif args.action == "p1-8b-openclaw-capabilities":
        p1_8b_openclaw_capabilities_action()
    elif args.action == "p1-8b-sync":
        p1_8b_sync_action()
    elif args.action == "p1-8b-remote-format":
        p1_8b_remote_format_action()
    elif args.action == "p1-8b-remote-static":
        p1_8b_remote_static_action()
    elif args.action == "p1-8b-migrate-restart":
        p1_8b_migrate_restart_action()
    elif args.action == "p1-8b-db-access":
        p1_8b_db_access_action()
    elif args.action == "p1-8b-run-chn":
        p1_8b_real_run_action("CHN")
    elif args.action == "p1-8b-run-fra":
        p1_8b_real_run_action("FRA")
    elif args.action == "p1-8b-run-bra":
        p1_8b_real_run_action("BRA")
    elif args.action == "p1-8b-latest-state":
        p1_8b_latest_state_action()
    elif args.action == "p1-8b-datagouv-probe":
        p1_8b_datagouv_probe_action()
    elif args.action == "p1-8b-brazil-ckan-probe":
        p1_8b_brazil_ckan_probe_action()
    elif args.action == "p1-8b-latest-funnel":
        p1_8b_latest_funnel_action()
    elif args.action == "p1-8b-preview-reconcile":
        p1_8b_preview_reconcile_action()
    elif args.action == "p1-8b-final-audit":
        p1_8b_final_audit_action()
    elif args.action == "p1-8b-npm-audit":
        p1_8b_npm_audit_action()
    elif args.action == "p1-8b-stage":
        p1_8b_stage_action()
    elif args.action == "p1-8b-stage-inspect":
        p1_8b_stage_inspect_action()
    elif args.action == "p1-8b-commit":
        p1_8b_commit_action()
    elif args.action == "p1-8b-extractor-diagnose":
        p1_8b_extractor_diagnose_action()
    elif args.action == "p1-8b-review-diagnose":
        p1_8b_review_diagnose_action()
    elif args.action == "p1-8b-review-auth-prepare":
        p1_8b_review_auth_prepare_action()
    elif args.action == "p1-8b-review-auth-restore":
        p1_8b_review_auth_restore_action()


if __name__ == "__main__":
    main()
