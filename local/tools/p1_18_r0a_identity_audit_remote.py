from __future__ import annotations

import hashlib
import json
import os
import platform
import pwd
import grp
import re
import shutil
import stat
import subprocess
import sys
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT = Path(os.environ.get("PROJECT", ".")).resolve()
DATA_ROOT = PROJECT.parent
REPORTS = PROJECT / "reports"

P1_18_PARENT_RUN_ID = "p1_18_20260726T034421Z_7391aeea"
P1_18_PARENT_STATUS = "FLUNET_DEEP_FORECAST_PARTIAL_WITH_EXPLICIT_BLOCKERS"
P1_18_PARENT_ZIP_SHA_EXPECTED = "d8b3562f83b7f54c1224547af7bad0f289bba9b99143f436ec9a5ac88f5d4698"
R0_REPAIR_ID = "p1_18_r0_20260726T044944Z_c0128776"
R0_STATUS = "P1_18_R0_PARTIAL_WITH_EXPLICIT_BLOCKERS"
R0_ZIP_SHA_EXPECTED = "b0c87c77156ec56510e66c0f6cd30ae2232fb28934d065ded8aee47d8cc4a0e9"
GIT_HEAD_EXPECTED = "d1ba1dfae120c869bddb8d201025d6cd20f4a4ac"
SYSTEM_IDENTIFIER_EXPECTED = "7661099468773015393"
PGDATA_OWNER_UID_OBSERVED = 1004
HISTORICAL_SERVICE_USER = "epidemicdb"
HISTORICAL_SERVICE_UID = 999
CPU_RUNTIME = DATA_ROOT / "p1_18_runtime" / R0_REPAIR_ID / "venv"

AUDIT_ID = "p1_18_r0a_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
ARTIFACT = PROJECT / "artifacts" / f"p1_18_r0a_postgres_identity_audit_{AUDIT_ID}"
COMMANDS = ARTIFACT / "commands"
LOGS = ARTIFACT / "logs"

PGDATA = PROJECT / "storage" / "database" / "postgresql18" / "data"
POSTGRES_BIN = DATA_ROOT / "conda" / "envs" / "postgresql18" / "bin"
POSTGRES = POSTGRES_BIN / "postgres"
PG_CTL = POSTGRES_BIN / "pg_ctl"
PSQL = POSTGRES_BIN / "psql"
PG_CONTROLDATA = POSTGRES_BIN / "pg_controldata"

FATAL_BLOCKERS: set[str] = set()
COMMAND_INDEX: list[dict[str, Any]] = []


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(ARTIFACT))
    except ValueError:
        return str(path)


def redact(text: str) -> str:
    text = re.sub(r"postgresql(\+\w+)?://[^\s'\"\)]+", "postgresql://[redacted]", text)
    text = re.sub(r"(?i)\b(password|passwd|token|secret|api[_-]?key|cookie)=\S+", r"\1=[redacted]", text)
    text = re.sub(r"(?i)(Environment=.*?(password|token|secret|key).*?$)", "Environment=[redacted]", text, flags=re.M)
    return text


def command_env_summary(env: dict[str, str] | None = None) -> dict[str, Any]:
    merged = dict(os.environ)
    if env:
        merged.update(env)
    return {
        "PYTHONNOUSERSITE": merged.get("PYTHONNOUSERSITE"),
        "PYTHONDONTWRITEBYTECODE": merged.get("PYTHONDONTWRITEBYTECODE"),
        "CUDA_VISIBLE_DEVICES": merged.get("CUDA_VISIBLE_DEVICES"),
        "proxy_variables_present": any(merged.get(k) for k in ["http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"]),
    }


def run(command_id: str, argv: list[str], timeout: int = 60, env: dict[str, str] | None = None, cwd: Path | None = None) -> dict[str, Any]:
    stdout_path = COMMANDS / f"{command_id}.stdout.txt"
    stderr_path = COMMANDS / f"{command_id}.stderr.txt"
    record_path = COMMANDS / f"{command_id}.json"
    safe_env = os.environ.copy()
    if env:
        safe_env.update(env)
    started = now()
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd or PROJECT),
            env=safe_env,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        stdout = redact(proc.stdout)
        stderr = redact(proc.stderr)
        exit_code = proc.returncode
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        stdout = redact(exc.stdout or "")
        stderr = redact(exc.stderr or "") + "\nTIMEOUT"
        exit_code = 124
        timed_out = True
    except PermissionError as exc:
        stdout = ""
        stderr = f"PERMISSION_DENIED: {type(exc).__name__}"
        exit_code = 126
        timed_out = False
    except FileNotFoundError as exc:
        stdout = ""
        stderr = f"COMMAND_NOT_FOUND: {exc.filename}"
        exit_code = 127
        timed_out = False
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    record = {
        "command_id": command_id,
        "argv": argv,
        "cwd": str(cwd or PROJECT),
        "started_at_utc": started,
        "finished_at_utc": now(),
        "exit_code": exit_code,
        "timed_out": timed_out,
        "stdout_path": rel(stdout_path),
        "stderr_path": rel(stderr_path),
        "stdout_sha256": sha_bytes(stdout.encode()),
        "stderr_sha256": sha_bytes(stderr.encode()),
        "environment_summary": command_env_summary(env),
    }
    write_json(record_path, record)
    COMMAND_INDEX.append(record)
    return record


def read_stdout(record: dict[str, Any]) -> str:
    return (ARTIFACT / record["stdout_path"]).read_text(encoding="utf-8", errors="replace")


def read_text_if_allowed(path: Path, max_bytes: int = 1_000_000) -> tuple[str | None, str | None]:
    forbidden_parts = {".secrets", ".env", "postgres.env", "authorized_keys", "id_rsa", "id_ed25519", "known_hosts"}
    if any(part in forbidden_parts or part.endswith(".env") for part in path.parts):
        return None, "SKIPPED_SECRET_LIKE_PATH"
    try:
        if path.stat().st_size > max_bytes:
            return None, "SKIPPED_TOO_LARGE"
        return path.read_text(encoding="utf-8", errors="replace"), None
    except PermissionError:
        return None, "PERMISSION_DENIED"
    except OSError as exc:
        return None, type(exc).__name__


def stat_path(path: Path, follow: bool = False) -> dict[str, Any]:
    try:
        st = path.stat() if follow else path.lstat()
    except PermissionError:
        return {"path": str(path), "status": "PERMISSION_DENIED"}
    except FileNotFoundError:
        return {"path": str(path), "status": "NOT_FOUND"}
    except OSError as exc:
        return {"path": str(path), "status": type(exc).__name__}
    mode = stat.filemode(st.st_mode)
    result = {
        "path": str(path),
        "realpath": str(path.resolve()) if path.exists() else None,
        "status": "OK",
        "follow_symlink": follow,
        "is_symlink": path.is_symlink(),
        "uid": st.st_uid,
        "gid": st.st_gid,
        "mode_octal": oct(stat.S_IMODE(st.st_mode)),
        "mode_text": mode,
        "device": st.st_dev,
        "inode": st.st_ino,
        "ctime": st.st_ctime,
        "mtime": st.st_mtime,
        "size": st.st_size,
    }
    if path.is_symlink():
        try:
            result["symlink_target"] = os.readlink(path)
        except OSError as exc:
            result["symlink_target_error"] = type(exc).__name__
    return result


def parse_control_data(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def collect_integrity() -> dict[str, Any]:
    out = ARTIFACT / "parent_and_r0_integrity"
    out.mkdir(parents=True, exist_ok=True)
    parent_zip = REPORTS / f"p1_18_flunet_deep_forecast_{P1_18_PARENT_RUN_ID}.zip"
    r0_artifact = PROJECT / "artifacts" / f"p1_18_r0_postgres_cpu_runtime_{R0_REPAIR_ID}"
    r0_zip = REPORTS / f"p1_18_r0_postgres_cpu_runtime_{R0_REPAIR_ID}.zip"
    payload = {
        "parent_reference": {
            "p1_18_parent_run_id": P1_18_PARENT_RUN_ID,
            "p1_18_parent_status": P1_18_PARENT_STATUS,
            "p1_18_parent_zip_sha256_expected": P1_18_PARENT_ZIP_SHA_EXPECTED,
            "r0_repair_id": R0_REPAIR_ID,
            "r0_status": R0_STATUS,
            "r0_zip_sha256_expected": R0_ZIP_SHA_EXPECTED,
            "git_head_expected": GIT_HEAD_EXPECTED,
            "postgres_system_identifier_observed": SYSTEM_IDENTIFIER_EXPECTED,
            "historical_service_contract": f"{HISTORICAL_SERVICE_USER} / UID {HISTORICAL_SERVICE_UID}",
            "cpu_runtime": str(CPU_RUNTIME),
        },
        "parent_zip": {"path": str(parent_zip), "exists": parent_zip.exists()},
        "r0_artifact": {"path": str(r0_artifact), "exists": r0_artifact.exists()},
        "r0_zip": {"path": str(r0_zip), "exists": r0_zip.exists()},
    }
    if parent_zip.exists():
        payload["parent_zip"]["sha256"] = sha_file(parent_zip)
        payload["parent_zip"]["match"] = payload["parent_zip"]["sha256"] == P1_18_PARENT_ZIP_SHA_EXPECTED
    else:
        payload["parent_zip"]["sha256"] = "NOT_PRESENT_ON_SERVER"
        payload["parent_zip"]["match"] = False
    if r0_zip.exists():
        payload["r0_zip"]["sha256"] = sha_file(r0_zip)
        payload["r0_zip"]["match"] = payload["r0_zip"]["sha256"] == R0_ZIP_SHA_EXPECTED
    else:
        payload["r0_zip"]["sha256"] = "R0_ZIP_NOT_PRESENT_ON_SERVER"
        payload["r0_zip"]["match"] = None
    key_files = [
        r0_artifact / "EXECUTION_STATE.json",
        r0_artifact / "gate_results.json",
        r0_artifact / "FINAL_REPORT.md",
        r0_artifact / "cpu_runtime" / "runtime_result.json",
        r0_artifact / "postgres_discovery" / "postgres_discovery.json",
        r0_artifact / "wheelhouse_manifest.json",
    ]
    payload["r0_key_file_hashes"] = [
        {"path": str(p), "exists": p.exists(), "sha256": sha_file(p) if p.exists() else "NOT_PRESENT"}
        for p in key_files
    ]
    write_json(out / "artifact_integrity.json", payload)
    write_json(ARTIFACT / "baseline" / "parent_reference.json", payload["parent_reference"])
    write_json(ARTIFACT / "baseline" / "artifact_integrity.json", payload)
    if not payload["parent_zip"]["match"] or payload["r0_zip"].get("match") is False:
        FATAL_BLOCKERS.add("PARENT_OR_R0_SHA_MISMATCH")
    return payload


def collect_baseline() -> dict[str, Any]:
    b = ARTIFACT / "baseline"
    b.mkdir(parents=True, exist_ok=True)
    git_head = run("git_head_before", ["git", "rev-parse", "HEAD"])
    git_status = run("git_status_before", ["git", "status", "--porcelain=v2"])
    process = run("process_state_before", ["ps", "-eo", "pid,ppid,user,uid,gid,lstart,comm", "--sort=pid"])
    listeners = run("listeners_before", ["bash", "-lc", "ss -ltnp 2>/dev/null || true"])
    port = run("port_5432_before", ["bash", "-lc", "ss -ltnp 2>/dev/null | grep ':5432\\b' || true"])
    write_text(b / "git_head.txt", read_stdout(git_head))
    write_text(b / "git_status_before.txt", read_stdout(git_status))
    write_text(b / "process_state_before.txt", read_stdout(process))
    write_text(b / "listeners_before.txt", read_stdout(listeners))
    write_text(b / "port_5432_before.txt", read_stdout(port))
    dirty_lines = [ln for ln in read_stdout(git_status).splitlines() if ln.strip()]
    new_path_prefix = f"artifacts/p1_18_r0a_postgres_identity_audit_{AUDIT_ID}"
    write_json(
        b / "dirty_overlap_assessment.json",
        {
            "dirty_count_before": len(dirty_lines),
            "expected_new_artifact_prefix": new_path_prefix,
            "preexisting_dirty_paths": [ln.split()[-1] for ln in dirty_lines],
            "blocked": False,
            "reason": "R0A writes only a new audit artifact and report zip; it does not edit project source, API, Dashboard, database, or prior artifacts.",
        },
    )
    machine_id_path = Path("/etc/machine-id")
    if machine_id_path.exists():
        machine_text, err = read_text_if_allowed(machine_id_path, max_bytes=4096)
        machine_id_sha = sha_bytes((machine_text or "").strip().encode()) if machine_text is not None else err
    else:
        machine_id_sha = "NOT_FOUND"
    boot_context = {
        "collected_at_utc": now(),
        "project_path": str(PROJECT),
        "data_root": str(DATA_ROOT),
        "historical_prompt_root": "USER_HOME/autodl-tmp/epidemic-openclaw",
        "historical_prompt_root_status": stat_path(Path("USER_HOME/autodl-tmp/epidemic-openclaw")),
        "current_root_status": stat_path(PROJECT),
        "machine_id_sha256": machine_id_sha,
        "boot_id_sha256": sha_bytes(Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip().encode()) if Path("/proc/sys/kernel/random/boot_id").exists() else "NOT_FOUND",
        "platform": platform.platform(),
    }
    write_json(b / "boot_context.json", boot_context)
    return {"git_head": read_stdout(git_head).strip(), "dirty_before": dirty_lines, "port_5432_before": read_stdout(port), "process_before": read_stdout(process), "listeners_before": read_stdout(listeners)}


def cpu_runtime_manifest(prefix: str) -> dict[str, Any]:
    out = ARTIFACT / "cpu_runtime_immutability"
    out.mkdir(parents=True, exist_ok=True)
    vpy = CPU_RUNTIME / "bin" / "python"
    if not vpy.exists():
        return {"status": "BLOCKED", "blocker": "CPU_RUNTIME_CHANGED", "reason": "python missing", "runtime": str(CPU_RUNTIME)}
    env = {"PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1", "CUDA_VISIBLE_DEVICES": ""}
    code = r"""
import importlib.metadata as md
import json
import torch, numpy, pandas, scipy, safetensors
packages = sorted((d.metadata.get("Name", d.metadata["Name"]).lower(), d.version) for d in md.distributions())
forbidden = ["neuralforecast","ray","optuna","lightning","pytorch-lightning","triton","cupy"]
forbidden_present = [name for name, version in packages if name in forbidden or name.startswith("nvidia-") or name.startswith("cuda")]
payload = {
  "python_version": tuple(__import__("sys").version_info[:3]),
  "torch": torch.__version__,
  "numpy": numpy.__version__,
  "pandas": pandas.__version__,
  "scipy": scipy.__version__,
  "safetensors": safetensors.__version__,
  "torch_cuda_available": torch.cuda.is_available(),
  "torch_version_cuda": torch.version.cuda,
  "packages": packages,
  "forbidden_present": forbidden_present,
}
print(json.dumps(payload, sort_keys=True))
"""
    version_rec = run(f"cpu_runtime_manifest_{prefix}", [str(vpy), "-c", code], timeout=120, env=env)
    freeze_rec = run(f"cpu_runtime_pip_freeze_{prefix}", [str(vpy), "-m", "pip", "freeze"], timeout=120, env=env)
    payload: dict[str, Any]
    if version_rec["exit_code"] == 0:
        payload = json.loads(read_stdout(version_rec))
    else:
        payload = {"status": "BLOCKED", "exit_code": version_rec["exit_code"], "stderr_sha256": version_rec["stderr_sha256"]}
    freeze = read_stdout(freeze_rec)
    payload["pip_freeze_sha256"] = sha_bytes(freeze.encode())
    payload["pip_freeze_path"] = rel(COMMANDS / f"cpu_runtime_pip_freeze_{prefix}.stdout.txt")
    payload["runtime_path"] = str(CPU_RUNTIME)
    payload["runtime_path_stat"] = stat_path(CPU_RUNTIME)
    write_json(out / f"cpu_runtime_manifest_{prefix}.json", payload)
    if prefix == "before":
        write_json(ARTIFACT / "baseline" / "cpu_runtime_manifest_before.json", payload)
    return payload


def collect_current_identity(pgdata_gid: int | None) -> dict[str, Any]:
    out = ARTIFACT / "current_identity"
    out.mkdir(parents=True, exist_ok=True)
    records = {
        "id": run("id", ["id"]),
        "id_u": run("id_u", ["id", "-u"]),
        "id_g": run("id_g", ["id", "-g"]),
        "whoami": run("whoami", ["whoami"]),
    }
    lookups: dict[str, dict[str, Any]] = {}
    targets = ["root", "epidemicdb", "999", "1004", "app-ahr"]
    for target in targets:
        rec = run(f"getent_passwd_{target}", ["getent", "passwd", target])
        text = read_stdout(rec).strip()
        lookups[target] = {"exit_code": rec["exit_code"], "raw": text if rec["exit_code"] == 0 else "", "resolved": parse_passwd(text) if rec["exit_code"] == 0 else None}
    groups: dict[str, dict[str, Any]] = {}
    group_targets = ["999"]
    if pgdata_gid is not None:
        group_targets.append(str(pgdata_gid))
    for target in sorted(set(group_targets)):
        rec = run(f"getent_group_{target}", ["getent", "group", target])
        text = read_stdout(rec).strip()
        groups[target] = {"exit_code": rec["exit_code"], "raw": text if rec["exit_code"] == 0 else "", "resolved": parse_group(text) if rec["exit_code"] == 0 else None}
    nss_lines = []
    nss_path = Path("/etc/nsswitch.conf")
    text, err = read_text_if_allowed(nss_path, max_bytes=100_000)
    if text is None:
        nss_lines = [{"status": err}]
    else:
        for line in text.splitlines():
            if line.startswith("passwd:") or line.startswith("group:"):
                nss_lines.append(line)
    summary = {
        "current_login_name": read_stdout(records["whoami"]).strip(),
        "current_uid": int(read_stdout(records["id_u"]).strip() or -1),
        "current_gid": int(read_stdout(records["id_g"]).strip() or -1),
        "uid_999_resolution": lookups.get("999"),
        "uid_1004_resolution": lookups.get("1004"),
        "pgdata_gid_resolution": groups.get(str(pgdata_gid)) if pgdata_gid is not None else None,
        "epidemicdb_resolution": lookups.get("epidemicdb"),
        "app_ahr_resolution": lookups.get("app-ahr"),
        "nsswitch_passwd_group_lines": nss_lines,
        "nss_lookup_health": "PASS" if lookups["root"]["exit_code"] == 0 else "BLOCKED",
        "uid1004_conflict": bool(lookups.get("1004", {}).get("resolved") and lookups["1004"]["resolved"].get("name") not in {HISTORICAL_SERVICE_USER, "postgres"}),
    }
    write_json(out / "current_identity_summary.json", summary)
    return summary


def parse_passwd(line: str) -> dict[str, Any] | None:
    parts = line.split(":")
    if len(parts) < 7:
        return None
    return {"name": parts[0], "uid": int(parts[2]), "gid": int(parts[3]), "gecos_present": bool(parts[4]), "home": parts[5], "shell": parts[6]}


def parse_group(line: str) -> dict[str, Any] | None:
    parts = line.split(":")
    if len(parts) < 4:
        return None
    return {"name": parts[0], "gid": int(parts[2]), "member_count": len([x for x in parts[3].split(",") if x])}


def collect_namespace_mapping() -> dict[str, Any]:
    out = ARTIFACT / "namespace_mapping"
    out.mkdir(parents=True, exist_ok=True)
    ns_entries = {}
    lines = []
    for who, base in [("self", Path("/proc/self")), ("pid1", Path("/proc/1"))]:
        for ns in ["user", "mnt", "pid"]:
            path = base / "ns" / ns
            try:
                target = os.readlink(path)
            except PermissionError:
                target = "PERMISSION_DENIED"
            except OSError as exc:
                target = type(exc).__name__
            ns_entries[f"{who}_{ns}"] = target
            lines.append(f"{who} {ns}: {target}")
        for map_name in ["uid_map", "gid_map"]:
            src = base / map_name
            try:
                content = src.read_text(encoding="utf-8", errors="replace")
            except PermissionError:
                content = "PERMISSION_DENIED\n"
            except OSError as exc:
                content = f"{type(exc).__name__}\n"
            write_text(out / f"{who}_{map_name}.txt", content)
            ns_entries[f"{who}_{map_name}"] = content
    write_text(out / "self_namespace_links.txt", "\n".join(lines) + "\n")
    write_text(out / "pid1_namespace_links.txt", "\n".join(lines) + "\n")
    virt = run("systemd_detect_virt", ["systemd-detect-virt"], timeout=30)
    lsns = run("lsns", ["lsns", "-o", "NS,TYPE,NPROCS,PID,USER,COMMAND"], timeout=30)
    write_text(out / "container_detection.txt", read_stdout(virt) + "\n--- lsns sha256 ---\n" + lsns["stdout_sha256"] + "\n")
    uid_map = parse_uid_gid_map(ns_entries.get("self_uid_map", ""))
    gid_map = parse_uid_gid_map(ns_entries.get("self_gid_map", ""))
    pid1_uid_map = parse_uid_gid_map(ns_entries.get("pid1_uid_map", ""))
    same_user_ns = ns_entries.get("self_user") == ns_entries.get("pid1_user")
    same_mnt_ns = ns_entries.get("self_mnt") == ns_entries.get("pid1_mnt")
    same_pid_ns = ns_entries.get("self_pid") == ns_entries.get("pid1_pid")
    translation = {
        "self_user_namespace": ns_entries.get("self_user"),
        "pid1_user_namespace": ns_entries.get("pid1_user"),
        "self_and_pid1_same_user_namespace": same_user_ns,
        "self_and_pid1_same_mount_namespace": same_mnt_ns,
        "self_and_pid1_same_pid_namespace": same_pid_ns,
        "self_uid_map": uid_map,
        "self_gid_map": gid_map,
        "pid1_uid_map": pid1_uid_map,
        "uid_1004_outer_mapping": translate_id(1004, uid_map),
        "uid_999_outer_mapping": translate_id(999, uid_map),
        "gid_1004_outer_mapping": translate_id(1004, gid_map),
        "gid_999_outer_mapping": translate_id(999, gid_map),
        "initial_user_namespace_likely": uid_map == [{"inside": 0, "outside": 0, "length": 4294967295}],
    }
    write_json(out / "computed_uid_gid_translation.json", translation)
    return translation


def parse_uid_gid_map(text: str) -> list[dict[str, int]]:
    rows = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 3 and all(p.lstrip("-").isdigit() for p in parts):
            rows.append({"inside": int(parts[0]), "outside": int(parts[1]), "length": int(parts[2])})
    return rows


def translate_id(value: int, mapping: list[dict[str, int]]) -> dict[str, Any]:
    for row in mapping:
        start = row["inside"]
        end = row["inside"] + row["length"]
        if start <= value < end:
            return {"inside": value, "outside": row["outside"] + (value - start), "mapped": True}
    return {"inside": value, "outside": None, "mapped": False}


def collect_mount_identity(pgdata_real: Path) -> dict[str, Any]:
    out = ARTIFACT / "mount_identity"
    out.mkdir(parents=True, exist_ok=True)
    findmnt_json = run("findmnt_pgdata_json", ["findmnt", "-T", str(pgdata_real), "-J", "-o", "TARGET,SOURCE,FSTYPE,OPTIONS,VFS-OPTIONS,PROPAGATION"], timeout=30)
    findmnt_idmap = run("findmnt_pgdata_idmap", ["findmnt", "-T", str(pgdata_real), "-J", "-o", "TARGET,SOURCE,FSTYPE,OPTIONS,VFS-OPTIONS,PROPAGATION,ID-MAP"], timeout=30)
    mountinfo_path = Path("/proc/self/mountinfo")
    mount_lines = []
    selected_line = None
    if mountinfo_path.exists():
        text = mountinfo_path.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            parts = line.split()
            if len(parts) > 4:
                mount_point = parts[4].replace("\\040", " ")
                try:
                    if str(pgdata_real).startswith(str(Path(mount_point).resolve())):
                        mount_lines.append(line)
                except OSError:
                    continue
        if mount_lines:
            selected_line = max(mount_lines, key=lambda ln: len(ln.split()[4]))
    write_text(ARTIFACT / "namespace_mapping" / "relevant_mountinfo.txt", (selected_line or "") + "\n")
    write_text(out / "relevant_mountinfo.txt", (selected_line or "") + "\n")
    findmnt_payload = {}
    if findmnt_json["exit_code"] == 0:
        try:
            findmnt_payload["standard"] = json.loads(read_stdout(findmnt_json))
        except json.JSONDecodeError:
            findmnt_payload["standard_parse_error"] = True
    if findmnt_idmap["exit_code"] == 0:
        try:
            findmnt_payload["idmap"] = json.loads(read_stdout(findmnt_idmap))
        except json.JSONDecodeError:
            findmnt_payload["idmap_parse_error"] = True
    idmap_markers = []
    if selected_line and re.search(r"\bidmap(ped)?\b", selected_line):
        idmap_markers.append("mountinfo_idmap_marker")
    if findmnt_idmap["exit_code"] != 0:
        idmap_status = "IDMAPPED_MOUNT_STATUS_UNPROVEN"
        FATAL_BLOCKERS.add("MOUNT_IDMAP_STATUS_UNPROVEN")
    elif idmap_markers:
        idmap_status = "IDMAPPED_MOUNT_PRESENT"
        FATAL_BLOCKERS.add("MOUNT_IDMAP_STATUS_UNPROVEN")
    else:
        idmap_status = "NO_IDMAPPED_MOUNT_MARKER_OBSERVED_WITH_FINDMNT_IDMAP_SUPPORT"
    result = {
        "pgdata_realpath": str(pgdata_real),
        "findmnt": findmnt_payload,
        "findmnt_idmap_exit_code": findmnt_idmap["exit_code"],
        "selected_mountinfo_line_sha256": sha_bytes((selected_line or "").encode()),
        "selected_mountinfo_line": selected_line,
        "mount_line_count_matching_pgdata": len(mount_lines),
        "idmap_markers": idmap_markers,
        "idmapped_mount_status": idmap_status,
    }
    write_json(out / "mount_target.json", result)
    write_json(ARTIFACT / "namespace_mapping" / "mount_target.json", result)
    return result


def collect_postgres_identity() -> dict[str, Any]:
    out = ARTIFACT / "pgdata_ownership_inventory"
    out.mkdir(parents=True, exist_ok=True)
    binary_records = {
        "postgres": {"path": str(POSTGRES), "exists": POSTGRES.exists(), "sha256": sha_file(POSTGRES) if POSTGRES.exists() else "NOT_FOUND"},
        "pg_ctl": {"path": str(PG_CTL), "exists": PG_CTL.exists(), "sha256": sha_file(PG_CTL) if PG_CTL.exists() else "NOT_FOUND"},
        "psql": {"path": str(PSQL), "exists": PSQL.exists(), "sha256": sha_file(PSQL) if PSQL.exists() else "NOT_FOUND"},
        "pg_controldata": {"path": str(PG_CONTROLDATA), "exists": PG_CONTROLDATA.exists(), "sha256": sha_file(PG_CONTROLDATA) if PG_CONTROLDATA.exists() else "NOT_FOUND"},
    }
    pg_version = (PGDATA / "PG_VERSION").read_text(encoding="ascii", errors="replace").strip() if (PGDATA / "PG_VERSION").exists() else "NOT_FOUND"
    version_rec = run("postgres_version", [str(POSTGRES), "--version"], timeout=30) if POSTGRES.exists() else {"exit_code": 127}
    ctl_version_rec = run("pg_ctl_version", [str(PG_CTL), "--version"], timeout=30) if PG_CTL.exists() else {"exit_code": 127}
    status_rec = run("pg_ctl_status_only", [str(PG_CTL), "-D", str(PGDATA), "status"], timeout=60) if PG_CTL.exists() else {"exit_code": 127}
    control_rec = run("pg_controldata_readonly", [str(PG_CONTROLDATA), str(PGDATA)], timeout=60) if PG_CONTROLDATA.exists() else {"exit_code": 127}
    control = parse_control_data(read_stdout(control_rec)) if isinstance(control_rec, dict) and control_rec.get("exit_code") == 0 else {}
    pgdata_real = PGDATA.resolve()
    root_stat = stat_path(pgdata_real)
    pgdata_gid = root_stat.get("gid") if root_stat.get("status") == "OK" else None
    identity = {
        "exact_pgdata": str(pgdata_real),
        "pg_version": pg_version,
        "binary_records": binary_records,
        "postgres_version_stdout": read_stdout(version_rec) if isinstance(version_rec, dict) and "stdout_path" in version_rec else "",
        "pg_ctl_version_stdout": read_stdout(ctl_version_rec) if isinstance(ctl_version_rec, dict) and "stdout_path" in ctl_version_rec else "",
        "pg_ctl_status_exit": status_rec.get("exit_code") if isinstance(status_rec, dict) else None,
        "pg_ctl_status_stdout": read_stdout(status_rec) if isinstance(status_rec, dict) and "stdout_path" in status_rec else "",
        "system_identifier": control.get("Database system identifier"),
        "cluster_state": control.get("Database cluster state"),
        "recovery_signals": {"standby.signal": (pgdata_real / "standby.signal").exists(), "recovery.signal": (pgdata_real / "recovery.signal").exists()},
        "pgdata_root_stat": root_stat,
    }
    if identity["system_identifier"] != SYSTEM_IDENTIFIER_EXPECTED:
        FATAL_BLOCKERS.add("CLUSTER_SYSTEM_IDENTIFIER_MISMATCH")
    if "18.4" not in identity["postgres_version_stdout"]:
        FATAL_BLOCKERS.add("POSTGRES_BINARY_HASH_CHANGED")
    write_json(out / "postgres_physical_identity.json", identity)
    return {"identity": identity, "pgdata_real": pgdata_real, "pgdata_gid": pgdata_gid}


def collect_pgdata_inventory(pgdata_real: Path, pgdata_gid: int | None) -> dict[str, Any]:
    out = ARTIFACT / "pgdata_ownership_inventory"
    out.mkdir(parents=True, exist_ok=True)
    required = [
        pgdata_real,
        pgdata_real / "global" / "pg_control",
        pgdata_real / "base",
        pgdata_real / "global",
        pgdata_real / "pg_wal",
        pgdata_real / "pg_tblspc",
        pgdata_real / "postmaster.opts",
    ]
    stats = {str(p): stat_path(p) for p in required}
    acl_records = {}
    for p in required:
        if p.exists() or p.is_symlink():
            acl = run("getfacl_" + sha_bytes(str(p).encode())[:12], ["getfacl", "-pn", str(p)], timeout=30)
            acl_records[str(p)] = {"exit_code": acl["exit_code"], "stdout_sha256": acl["stdout_sha256"], "stderr_sha256": acl["stderr_sha256"]}
    histogram: dict[str, int] = {}
    exceptions: list[dict[str, Any]] = []
    symlinks: list[dict[str, Any]] = []
    expected_uid = stats[str(pgdata_real)].get("uid")
    expected_gid = stats[str(pgdata_real)].get("gid")
    for dirpath, dirnames, filenames in os.walk(pgdata_real, topdown=True, followlinks=False):
        current = Path(dirpath)
        names = [current] + [current / n for n in filenames]
        for path in names:
            s = stat_path(path)
            if s.get("status") != "OK":
                exceptions.append({"path": str(path), "reason": s.get("status")})
                continue
            key = f"{s['uid']}:{s['gid']}"
            histogram[key] = histogram.get(key, 0) + 1
            if expected_uid is not None and (s["uid"] != expected_uid or s["gid"] != expected_gid):
                exceptions.append({"path": str(path), "uid": s["uid"], "gid": s["gid"], "expected_uid": expected_uid, "expected_gid": expected_gid})
            if s.get("is_symlink"):
                symlinks.append(s)
        dirnames[:] = [d for d in dirnames if not (current / d).is_symlink()]
    if len(histogram) > 1 or exceptions:
        FATAL_BLOCKERS.add("MIXED_OR_UNEXPECTED_PGDATA_OWNERSHIP")
    write_json(out / "pgdata_owner_histogram.json", histogram)
    write_json(out / "pgdata_owner_exceptions.json", exceptions)
    write_json(out / "pgdata_required_path_stats.json", stats)
    write_json(out / "pgdata_acl_command_refs.json", acl_records)
    write_json(out / "pgdata_symlinks.json", symlinks)
    ancestors = []
    for parent in [pgdata_real, *pgdata_real.parents]:
        ancestors.append(stat_path(parent))
        if str(parent) == "/":
            break
    ancestors = list(reversed(ancestors))
    traversal = {
        "ancestors": ancestors,
        "uid_999_traversal_estimate": traversal_estimate(ancestors, 999, 999),
        "uid_1004_traversal_estimate": traversal_estimate(ancestors, 1004, pgdata_gid if pgdata_gid is not None else 1004),
    }
    if not traversal["uid_1004_traversal_estimate"]["can_traverse"]:
        FATAL_BLOCKERS.add("ANCESTOR_DIRECTORY_NOT_TRAVERSABLE")
    write_json(out / "ancestor_traversal_matrix.json", traversal)
    write_json(out / "runtime_path_permissions.json", {"cpu_runtime": stat_path(CPU_RUNTIME), "runtime_parents": [stat_path(p) for p in [CPU_RUNTIME, *CPU_RUNTIME.parents][:8]]})
    namei = run("namei_pgdata", ["namei", "-l", str(pgdata_real)], timeout=30)
    write_text(out / "namei_pgdata.txt", read_stdout(namei))
    return {"histogram": histogram, "exceptions": exceptions, "stats": stats, "symlinks": symlinks, "traversal": traversal}


def traversal_estimate(ancestors: list[dict[str, Any]], uid: int, gid: int) -> dict[str, Any]:
    decisions = []
    can = True
    for item in ancestors:
        if item.get("status") != "OK":
            decisions.append({"path": item.get("path"), "can_traverse": False, "reason": item.get("status")})
            can = False
            continue
        mode_int = int(str(item.get("mode_octal", "0o0")), 8)
        if item.get("uid") == uid:
            bit = bool(mode_int & stat.S_IXUSR)
            source = "owner"
        elif item.get("gid") == gid:
            bit = bool(mode_int & stat.S_IXGRP)
            source = "group"
        else:
            bit = bool(mode_int & stat.S_IXOTH)
            source = "other"
        decisions.append({"path": item.get("path"), "can_traverse": bit, "source": source, "mode": item.get("mode_octal"), "uid": item.get("uid"), "gid": item.get("gid")})
        can = can and bit
    return {"uid": uid, "gid_assumed": gid, "can_traverse": can, "decisions": decisions}


def collect_tablespace_scope(pgdata_real: Path) -> dict[str, Any]:
    out = ARTIFACT / "tablespace_and_symlink_scope"
    out.mkdir(parents=True, exist_ok=True)
    tbl_dir = pgdata_real / "pg_tblspc"
    tablespaces = []
    if tbl_dir.exists():
        for child in sorted(tbl_dir.iterdir()):
            item = stat_path(child)
            if child.is_symlink():
                try:
                    resolved = child.resolve(strict=True)
                    item["resolved_target"] = str(resolved)
                    item["resolved_stat"] = stat_path(resolved)
                    if not str(resolved).startswith(str(DATA_ROOT)):
                        item["scope_status"] = "OUTSIDE_DATA_ROOT"
                        FATAL_BLOCKERS.add("TABLESPACE_OR_SYMLINK_OUTSIDE_PROVEN_SCOPE")
                    else:
                        item["scope_status"] = "WITHIN_DATA_ROOT"
                except OSError as exc:
                    item["scope_status"] = f"UNRESOLVED:{type(exc).__name__}"
                    FATAL_BLOCKERS.add("TABLESPACE_OR_SYMLINK_OUTSIDE_PROVEN_SCOPE")
            tablespaces.append(item)
    config_scope = parse_postgres_config_scope(pgdata_real)
    socket_paths = config_scope.get("unix_socket_directories") or ["/tmp"]
    log_paths = config_scope.get("log_directory") or []
    wal_paths = [str(pgdata_real / "pg_wal")]
    result = {
        "tablespaces": tablespaces,
        "wal_paths": [{"path": p, "stat": stat_path(Path(p))} for p in wal_paths],
        "log_paths": [{"path": str((pgdata_real / p).resolve()) if not str(p).startswith("/") else str(p), "raw": p, "stat": stat_path((pgdata_real / p) if not str(p).startswith("/") else Path(p))} for p in log_paths],
        "socket_paths": [{"path": p, "stat": stat_path(Path(p))} for p in socket_paths],
        "postgres_config_scope": config_scope,
    }
    write_json(out / "tablespace_scope.json", result)
    return result


def parse_postgres_config_scope(pgdata_real: Path) -> dict[str, Any]:
    keys = {"unix_socket_directories", "log_directory", "external_pid_file", "data_directory"}
    result: dict[str, Any] = {"parsed_keys": {}, "read_status": {}}
    conf = pgdata_real / "postgresql.conf"
    text, err = read_text_if_allowed(conf, max_bytes=1_000_000)
    if text is None:
        result["read_status"][str(conf)] = err
        return result
    result["read_status"][str(conf)] = "OK"
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or "=" not in line:
            continue
        key, value = [x.strip() for x in line.split("=", 1)]
        if key in keys:
            value = value.strip("'\"")
            result["parsed_keys"][key] = value
    if "unix_socket_directories" in result["parsed_keys"]:
        result["unix_socket_directories"] = [x.strip() for x in result["parsed_keys"]["unix_socket_directories"].split(",") if x.strip()]
    if "log_directory" in result["parsed_keys"]:
        result["log_directory"] = [result["parsed_keys"]["log_directory"]]
    if "external_pid_file" in result["parsed_keys"]:
        result["external_pid_file"] = result["parsed_keys"]["external_pid_file"]
    return result


def collect_historical_evidence(pg_identity: dict[str, Any], current_identity: dict[str, Any], namespace: dict[str, Any]) -> dict[str, Any]:
    out = ARTIFACT / "historical_identity_evidence"
    out.mkdir(parents=True, exist_ok=True)
    terms = [
        SYSTEM_IDENTIFIER_EXPECTED,
        "epidemicdb",
        "UID 999",
        "uid 999",
        "uid=999",
        '"owner_uid": 999',
        '"owner_uid": 1004',
        "pgdata_owner_uid_observed",
        "postgresql18",
        "pg_controldata",
        "postmaster.opts",
    ]
    allowed_roots = [PROJECT / "artifacts", PROJECT / "reports", PROJECT / "scripts", PROJECT / "deployment"]
    evidence = []
    skipped = []
    allowed_suffixes = {".json", ".md", ".txt", ".sh", ".service", ".conf", ".log"}
    forbidden_dirs = {".git", ".secrets", "storage", "node_modules", "venv", "wheelhouse", "p1_18_runtime", "__pycache__"}
    for root in allowed_roots:
        if not root.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(root, topdown=True):
            current = Path(dirpath)
            dirnames[:] = [d for d in dirnames if d not in forbidden_dirs and not d.startswith(".")]
            if len(evidence) > 500:
                break
            for name in filenames:
                path = current / name
                if path.suffix.lower() not in allowed_suffixes:
                    continue
                text, err = read_text_if_allowed(path, max_bytes=1_000_000)
                if text is None:
                    skipped.append({"path": str(path), "reason": err})
                    continue
                hits = []
                lower = text.lower()
                for term in terms:
                    if term.lower() in lower:
                        hits.append(term)
                if not hits:
                    continue
                snippets = []
                for hit in hits[:6]:
                    idx = lower.find(hit.lower())
                    start = max(0, idx - 140)
                    end = min(len(text), idx + len(hit) + 140)
                    snippets.append(redact(text[start:end]).replace("\n", "\\n"))
                try:
                    st = path.stat()
                    mtime = st.st_mtime
                except OSError:
                    mtime = None
                evidence.append({
                    "source_path": str(path),
                    "source_sha256": sha_file(path),
                    "mtime": mtime,
                    "matched_terms": hits,
                    "snippets": snippets,
                    "evidence_type": classify_evidence(path, snippets),
                })
    systemd_evidence = collect_systemd_shadow_fields()
    postmaster_opts = collect_postmaster_opts()
    cluster_continuity = {
        "current_system_identifier": pg_identity.get("system_identifier"),
        "expected_system_identifier": SYSTEM_IDENTIFIER_EXPECTED,
        "matches_expected": pg_identity.get("system_identifier") == SYSTEM_IDENTIFIER_EXPECTED,
        "current_cluster_state": pg_identity.get("cluster_state"),
        "current_pgdata_owner_uid": pg_identity.get("pgdata_root_stat", {}).get("uid"),
        "postmaster_opts_owner": postmaster_opts.get("stat", {}).get("uid"),
    }
    original_uid999 = [e for e in evidence if e["evidence_type"] == "original_execution" and any("999" in s for s in e["snippets"])]
    original_uid1004 = [e for e in evidence if e["evidence_type"] == "original_execution" and any("1004" in s for s in e["snippets"])]
    narrative_uid999 = [e for e in evidence if e["evidence_type"] == "later_narrative" and any("999" in s for s in e["snippets"])]
    uid1004_conflict = bool(current_identity.get("uid1004_conflict"))
    if original_uid999 and original_uid1004:
        historical_identity = "CONFLICTING"
    elif original_uid999:
        historical_identity = "UID999"
    elif original_uid1004:
        historical_identity = "UID1004"
    else:
        historical_identity = "UNPROVEN"
    if historical_identity == "UNPROVEN":
        identity_outcome = "UID1004_CONFLICT" if uid1004_conflict else "INCONCLUSIVE_OR_NSS_UNAVAILABLE"
    elif historical_identity == "UID999" and uid1004_conflict:
        identity_outcome = "UID1004_CONFLICT"
    else:
        identity_outcome = "INCONCLUSIVE_OR_NSS_UNAVAILABLE"
    if identity_outcome in {"UID1004_CONFLICT"}:
        FATAL_BLOCKERS.add("UID_OR_GID_COLLISION")
    if historical_identity in {"UNPROVEN", "CONFLICTING"}:
        FATAL_BLOCKERS.add("HISTORICAL_IDENTITY_UNPROVEN_OR_CONFLICTING")
    result = {
        "search_terms": terms,
        "evidence": evidence,
        "skipped": skipped[:200],
        "systemd_shadow_fields": systemd_evidence,
        "postmaster_opts": postmaster_opts,
        "cluster_continuity_evidence": cluster_continuity,
        "host_or_namespace_transition_evidence": {
            "project_path": str(PROJECT),
            "historical_root_prompt": "USER_HOME/autodl-tmp/epidemic-openclaw",
            "namespace_translation": namespace,
        },
        "evidence_conflict_matrix": {
            "original_uid999_count": len(original_uid999),
            "original_uid1004_count": len(original_uid1004),
            "later_narrative_uid999_count": len(narrative_uid999),
            "uid1004_conflict": uid1004_conflict,
            "note": "Later prompts/reports repeating epidemicdb/999 are not treated as independent original proof.",
        },
        "historical_identity": historical_identity,
        "identity_outcome": identity_outcome,
    }
    write_json(out / "historical_identity_evidence.json", result)
    write_json(out / "cluster_continuity_evidence.json", cluster_continuity)
    write_json(out / "host_or_namespace_transition_evidence.json", result["host_or_namespace_transition_evidence"])
    write_json(out / "evidence_conflict_matrix.json", result["evidence_conflict_matrix"])
    return result


def classify_evidence(path: Path, snippets: list[str]) -> str:
    joined = "\n".join(snippets).lower()
    p = str(path).lower()
    if "gate_results" in p or "execution_state" in p or "final_report" in p:
        return "later_narrative"
    if "/commands/" in p:
        name = path.name.lower()
        original_stdout_prefixes = (
            "id",
            "id_u",
            "id_g",
            "whoami",
            "getent_passwd",
            "getent_group",
            "pg_controldata",
            "pg_ctl_status",
            "postgres_version",
            "pg_ctl_version",
            "namei",
        )
        if name.endswith(".stdout.txt") and name.startswith(original_stdout_prefixes):
            return "original_execution"
        return "supporting_file"
    if "postgres_discovery" in p or "postgres_recovery" in p:
        return "supporting_file"
    if "p0" in p and ("id " in joined or "uid" in joined or "pgdata" in joined):
        return "original_execution"
    if "prompt" in p or "report" in p:
        return "later_narrative"
    return "supporting_file"


def collect_systemd_shadow_fields() -> list[dict[str, Any]]:
    roots = [Path("/etc/systemd/system"), Path("/lib/systemd/system"), PROJECT / "deployment", PROJECT / "scripts"]
    rows = []
    patterns = ["epidemic", "postgres", "openclaw"]
    for root in roots:
        if not root.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(root, topdown=True):
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in {".secrets", "node_modules", "storage"}]
            for name in filenames:
                path = Path(dirpath) / name
                if not any(p in name.lower() or p in str(path).lower() for p in patterns):
                    continue
                text, err = read_text_if_allowed(path, max_bytes=200_000)
                if text is None:
                    rows.append({"source_path": str(path), "status": err})
                    continue
                fields = []
                for raw in text.splitlines():
                    stripped = raw.strip()
                    if stripped.startswith(("User=", "Group=", "ExecStart=", "WorkingDirectory=")):
                        fields.append(redact(stripped))
                    elif stripped.startswith("Environment="):
                        fields.append("Environment=[redacted]")
                if fields:
                    rows.append({"source_path": str(path), "source_sha256": sha_file(path), "fields": fields})
    return rows[:100]


def collect_postmaster_opts() -> dict[str, Any]:
    path = PGDATA / "postmaster.opts"
    text, err = read_text_if_allowed(path, max_bytes=100_000)
    payload = {"path": str(path), "exists": path.exists(), "stat": stat_path(path)}
    if text is None:
        payload["read_status"] = err
    else:
        payload["read_status"] = "OK"
        payload["sha256"] = sha_file(path)
        payload["redacted_excerpt"] = redact(text)[:1000]
    return payload


def create_remediation_manifest(
    pg_identity: dict[str, Any],
    current_identity: dict[str, Any],
    namespace: dict[str, Any],
    mount: dict[str, Any],
    inventory: dict[str, Any],
    tablespace: dict[str, Any],
    historical: dict[str, Any],
) -> dict[str, Any]:
    deterministic = not FATAL_BLOCKERS and historical["identity_outcome"] in {"MAPPED_IDENTITY_PROVEN", "NATIVE_UID1004_PROVEN", "OWNER_DRIFT_PROVEN"}
    if deterministic:
        proposed_action = "REQUIRES_R0B_EXACT_IDENTITY_REMEDIATION_AUTHORIZATION"
    else:
        proposed_action = "NONE"
        FATAL_BLOCKERS.add("REMEDIATION_SCOPE_NOT_DETERMINISTIC")
    manifest = {
        "schema_version": "p1_18_r0a.v1",
        "audit_id": AUDIT_ID,
        "physical_cluster_system_identifier": pg_identity.get("system_identifier"),
        "historical_identity": historical["historical_identity"],
        "identity_outcome": historical["identity_outcome"],
        "evidence_refs": [
            "parent_and_r0_integrity/artifact_integrity.json",
            "current_identity/current_identity_summary.json",
            "namespace_mapping/computed_uid_gid_translation.json",
            "mount_identity/mount_target.json",
            "pgdata_ownership_inventory/pgdata_owner_histogram.json",
            "historical_identity_evidence/historical_identity_evidence.json",
        ],
        "evidence_sha256": {},
        "proposed_execution_username": None,
        "proposed_uid": None,
        "proposed_gid": None,
        "proposed_namespace": None,
        "exact_pgdata": pg_identity.get("exact_pgdata"),
        "tablespaces": tablespace.get("tablespaces"),
        "wal_paths": tablespace.get("wal_paths"),
        "log_paths": tablespace.get("log_paths"),
        "socket_paths": tablespace.get("socket_paths"),
        "proposed_action": proposed_action,
        "remediation_scope_deterministic": deterministic,
        "filesystem_changes_required": False if proposed_action == "NONE" else None,
        "account_changes_required": False if proposed_action == "NONE" else None,
        "contract_update_required": historical["identity_outcome"] == "NATIVE_UID1004_PROVEN",
        "snapshot_required": True,
        "preconditions": [
            "R0B requires a new explicit user authorization citing this manifest SHA-256.",
            "No PostgreSQL start is authorized by R0A.",
            "No ownership/account mutation is authorized by R0A.",
        ],
        "expected_after_state": "No state changes in R0A; R0B must define exact expected state if authorized.",
        "rollback_plan": "Not applicable to R0A because proposed_action is NONE and action_taken is NONE.",
        "risks": sorted(FATAL_BLOCKERS),
        "unresolved_items": sorted(FATAL_BLOCKERS),
        "authorization_required": True,
        "action_taken": "NONE",
        "database_started": False,
        "delete_or_mutation_sql_generated": False,
    }
    for ref in manifest["evidence_refs"]:
        p = ARTIFACT / ref
        manifest["evidence_sha256"][ref] = sha_file(p) if p.exists() else "NOT_PRESENT"
    path = ARTIFACT / "proposed_remediation_manifest.json"
    write_json(path, manifest)
    manifest["manifest_sha256"] = sha_file(path)
    write_json(ARTIFACT / "decision" / "proposed_remediation_manifest.with_sha.json", manifest)
    return manifest


def gate(gate_id: str, status: str, evidence_paths: list[str], computed: Any, expected: Any, blocker: str | None = None, blocked_by: str | None = None) -> dict[str, Any]:
    return {
        "gate_id": gate_id,
        "status": status,
        "evidence_paths": evidence_paths,
        "computed_value": computed,
        "expected_value": expected,
        "blocker_code": blocker,
        "blocked_by": blocked_by,
    }


def collect_after() -> dict[str, Any]:
    process = run("process_state_after", ["ps", "-eo", "pid,ppid,user,uid,gid,lstart,comm", "--sort=pid"])
    listeners = run("listeners_after", ["bash", "-lc", "ss -ltnp 2>/dev/null || true"])
    port = run("port_5432_after", ["bash", "-lc", "ss -ltnp 2>/dev/null | grep ':5432\\b' || true"])
    git_status = run("git_status_after", ["git", "status", "--porcelain=v2"])
    write_text(ARTIFACT / "process_state_after.txt", read_stdout(process))
    write_text(ARTIFACT / "listeners_after.txt", read_stdout(listeners))
    write_text(ARTIFACT / "port_5432_after.txt", read_stdout(port))
    write_text(ARTIFACT / "git_status_after.txt", read_stdout(git_status))
    return {"process_after": read_stdout(process), "listeners_after": read_stdout(listeners), "port_5432_after": read_stdout(port), "git_status_after": read_stdout(git_status)}


def secret_scan() -> dict[str, Any]:
    hits = []
    patterns = [
        re.compile(r"postgresql(\+\w+)?://(?!\[redacted\])", re.I),
        re.compile(r"(?i)(password|token|secret|api[_-]?key|cookie)\s*[:=]\s*(?!\[redacted\])\S+"),
        re.compile(r"BEGIN RSA PRIVATE KEY|BEGIN OPENSSH PRIVATE KEY"),
    ]
    for path in ARTIFACT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".json", ".txt", ".md", ".log", ".sh"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if any(p.search(line) for p in patterns):
                hits.append({"path": rel(path), "line": i, "sha256": sha_bytes(line.encode())})
    result = {"passed": not hits, "hit_count": len(hits), "hits": hits[:100]}
    write_json(ARTIFACT / "secret_scan.json", result)
    if hits:
        FATAL_BLOCKERS.add("SECRET_SCAN_FAILED")
    return result


def package_artifact() -> dict[str, Any]:
    write_json(COMMANDS / "COMMAND_INDEX.json", COMMAND_INDEX)
    files = sorted([p for p in ARTIFACT.rglob("*") if p.is_file()])
    manifest = {
        "artifact": str(ARTIFACT),
        "files": [{"path": rel(p), "bytes": p.stat().st_size, "sha256": sha_file(p)} for p in files],
    }
    write_json(ARTIFACT / "PACKAGE_MANIFEST.json", manifest)
    zip_path = REPORTS / f"{ARTIFACT.name}.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in sorted([p for p in ARTIFACT.rglob("*") if p.is_file()]):
            zf.write(p, p.relative_to(ARTIFACT.parent))
    zip_sha = sha_file(zip_path)
    write_text(REPORTS / f"{ARTIFACT.name}.zip.sha256", f"{zip_sha}  {zip_path.name}\n")
    return {"zip_path": str(zip_path), "zip_sha256": zip_sha, "files": len(files), "bytes": sum(p.stat().st_size for p in files)}


def main() -> None:
    ARTIFACT.mkdir(parents=True, exist_ok=False)
    for d in [
        "parent_and_r0_integrity",
        "baseline",
        "cpu_runtime_immutability",
        "current_identity",
        "namespace_mapping",
        "mount_identity",
        "pgdata_ownership_inventory",
        "tablespace_and_symlink_scope",
        "historical_identity_evidence",
        "decision",
        "commands",
        "logs",
    ]:
        (ARTIFACT / d).mkdir(parents=True, exist_ok=True)
    write_json(ARTIFACT / "RUN_CONFIG.json", {"audit_id": AUDIT_ID, "project": str(PROJECT), "started_at_utc": now(), "mode": "STRICT_READ_ONLY_NO_DB_START_NO_SECRETS"})
    integrity = collect_integrity()
    baseline = collect_baseline()
    cpu_before = cpu_runtime_manifest("before")
    pg = collect_postgres_identity()
    current_identity = collect_current_identity(pg["pgdata_gid"])
    namespace = collect_namespace_mapping()
    mount = collect_mount_identity(pg["pgdata_real"])
    inventory = collect_pgdata_inventory(pg["pgdata_real"], pg["pgdata_gid"])
    tablespace = collect_tablespace_scope(pg["pgdata_real"])
    historical = collect_historical_evidence(pg["identity"], current_identity, namespace)
    manifest = create_remediation_manifest(pg["identity"], current_identity, namespace, mount, inventory, tablespace, historical)
    cpu_after = cpu_runtime_manifest("after")
    cpu_immutable = cpu_before.get("pip_freeze_sha256") == cpu_after.get("pip_freeze_sha256") and not cpu_after.get("forbidden_present") and cpu_after.get("torch_cuda_available") is False and cpu_after.get("torch_version_cuda") is None
    if not cpu_immutable:
        FATAL_BLOCKERS.add("CPU_RUNTIME_CHANGED")
    write_json(ARTIFACT / "cpu_runtime_immutability" / "cpu_runtime_comparison.json", {"before": cpu_before, "after": cpu_after, "immutable": cpu_immutable})
    after = collect_after()
    no_5432_change = not baseline["port_5432_before"].strip() and not after["port_5432_after"].strip()
    if not no_5432_change:
        FATAL_BLOCKERS.add("ACTIVE_POSTGRES_OR_5432_STATE_CHANGED")
    secret = secret_scan()
    gates = [
        gate("PARENT_AND_R0_ARTIFACT_INTEGRITY", "PASS" if integrity["parent_zip"].get("match") and integrity["r0_zip"].get("match") else "FAIL", ["parent_and_r0_integrity/artifact_integrity.json"], {"parent": integrity["parent_zip"], "r0": integrity["r0_zip"]}, "expected SHA matches", "PARENT_OR_R0_SHA_MISMATCH" if "PARENT_OR_R0_SHA_MISMATCH" in FATAL_BLOCKERS else None),
        gate("CODEBASE_IDENTITY", "PASS" if baseline["git_head"] == GIT_HEAD_EXPECTED else "FAIL", ["baseline/git_head.txt"], baseline["git_head"], GIT_HEAD_EXPECTED),
        gate("CPU_RUNTIME_REUSE_IMMUTABILITY", "PASS" if cpu_immutable else "FAIL", ["cpu_runtime_immutability/cpu_runtime_comparison.json"], cpu_after, "unchanged CPU runtime/no forbidden deps", "CPU_RUNTIME_CHANGED" if not cpu_immutable else None),
        gate("POSTGRES_BINARY_IDENTITY", "PASS" if "18.4" in pg["identity"].get("postgres_version_stdout", "") else "FAIL", ["pgdata_ownership_inventory/postgres_physical_identity.json"], pg["identity"].get("binary_records"), "PostgreSQL 18.4 binaries"),
        gate("POSTGRES_PHYSICAL_CLUSTER_IDENTITY", "PASS" if pg["identity"].get("system_identifier") == SYSTEM_IDENTIFIER_EXPECTED else "FAIL", ["pgdata_ownership_inventory/postgres_physical_identity.json"], pg["identity"].get("system_identifier"), SYSTEM_IDENTIFIER_EXPECTED, "CLUSTER_SYSTEM_IDENTIFIER_MISMATCH" if pg["identity"].get("system_identifier") != SYSTEM_IDENTIFIER_EXPECTED else None),
        gate("CURRENT_UID_GID_INVENTORY", "PASS", ["current_identity/current_identity_summary.json"], {"uid": current_identity.get("current_uid"), "gid": current_identity.get("current_gid")}, "collected"),
        gate("NSS_IDENTITY_RESOLUTION", "PASS" if current_identity.get("nss_lookup_health") == "PASS" else "BLOCKED", ["current_identity/current_identity_summary.json"], current_identity, "root/getent works; specified identities collected"),
        gate("USER_NAMESPACE_CHAIN", "PASS" if namespace.get("initial_user_namespace_likely") is True else "BLOCKED", ["namespace_mapping/computed_uid_gid_translation.json"], namespace, "initial or mapped namespace determined"),
        gate("MOUNT_IDMAP_DETERMINATION", "PASS" if mount.get("idmapped_mount_status", "").startswith("NO_IDMAPPED") else "BLOCKED", ["mount_identity/mount_target.json"], mount.get("idmapped_mount_status"), "idmapped mount status determined", "MOUNT_IDMAP_STATUS_UNPROVEN" if "MOUNT_IDMAP_STATUS_UNPROVEN" in FATAL_BLOCKERS else None),
        gate("PGDATA_NUMERIC_OWNERSHIP_CONSISTENCY", "PASS" if not inventory["exceptions"] and len(inventory["histogram"]) == 1 else "BLOCKED", ["pgdata_ownership_inventory/pgdata_owner_histogram.json", "pgdata_ownership_inventory/pgdata_owner_exceptions.json"], inventory["histogram"], "single numeric owner/gid across PGDATA", "MIXED_OR_UNEXPECTED_PGDATA_OWNERSHIP" if "MIXED_OR_UNEXPECTED_PGDATA_OWNERSHIP" in FATAL_BLOCKERS else None),
        gate("ANCESTOR_PATH_TRAVERSAL", "PASS" if inventory["traversal"]["uid_1004_traversal_estimate"]["can_traverse"] else "BLOCKED", ["pgdata_ownership_inventory/ancestor_traversal_matrix.json"], inventory["traversal"], "current observed owner can traverse ancestors", "ANCESTOR_DIRECTORY_NOT_TRAVERSABLE" if "ANCESTOR_DIRECTORY_NOT_TRAVERSABLE" in FATAL_BLOCKERS else None),
        gate("TABLESPACE_WAL_LOG_SOCKET_SCOPE", "PASS" if "TABLESPACE_OR_SYMLINK_OUTSIDE_PROVEN_SCOPE" not in FATAL_BLOCKERS else "BLOCKED", ["tablespace_and_symlink_scope/tablespace_scope.json"], tablespace, "all paths in proven scope"),
        gate("HISTORICAL_SERVICE_IDENTITY_EVIDENCE", "PASS" if historical["historical_identity"] in {"UID999", "UID1004"} else "BLOCKED", ["historical_identity_evidence/historical_identity_evidence.json"], historical["historical_identity"], "UID999 or UID1004 proven by original evidence", "HISTORICAL_IDENTITY_UNPROVEN_OR_CONFLICTING" if "HISTORICAL_IDENTITY_UNPROVEN_OR_CONFLICTING" in FATAL_BLOCKERS else None),
        gate("CLUSTER_CONTINUITY", "PASS" if historical["cluster_continuity_evidence"].get("matches_expected") else "FAIL", ["historical_identity_evidence/cluster_continuity_evidence.json"], historical["cluster_continuity_evidence"], "system identifier matches"),
        gate("IDENTITY_CONTRACT_DETERMINATION", "PASS" if historical["identity_outcome"] in {"MAPPED_IDENTITY_PROVEN", "NATIVE_UID1004_PROVEN", "OWNER_DRIFT_PROVEN"} else "BLOCKED", ["decision/proposed_remediation_manifest.with_sha.json"], historical["identity_outcome"], "safe branch determined", "UID_OR_GID_COLLISION" if historical["identity_outcome"] == "UID1004_CONFLICT" else "HISTORICAL_IDENTITY_UNPROVEN_OR_CONFLICTING"),
        gate("REMEDIATION_SCOPE_DETERMINISTIC", "PASS" if manifest["remediation_scope_deterministic"] else "BLOCKED", ["proposed_remediation_manifest.json"], manifest["proposed_action"], "unique safe proposal", "REMEDIATION_SCOPE_NOT_DETERMINISTIC" if not manifest["remediation_scope_deterministic"] else None),
        gate("NO_SYSTEM_OR_DATABASE_MUTATION", "PASS", ["RUN_CONFIG.json", "commands/COMMAND_INDEX.json"], "no start/stop/chown/user/db commands executed", "read-only audit only"),
        gate("PROCESS_LISTENER_INVARIANCE", "PASS" if no_5432_change else "FAIL", ["baseline/port_5432_before.txt", "port_5432_after.txt"], {"port_5432_before": baseline["port_5432_before"], "port_5432_after": after["port_5432_after"]}, "5432 remains not listening", "ACTIVE_POSTGRES_OR_5432_STATE_CHANGED" if not no_5432_change else None),
        gate("SECRET_SCAN", "PASS" if secret["passed"] else "FAIL", ["secret_scan.json"], secret["hit_count"], 0, "SECRET_SCAN_FAILED" if not secret["passed"] else None),
    ]
    write_json(ARTIFACT / "gate_results.json", gates)
    blockers = sorted(FATAL_BLOCKERS)
    primary_status = "P1_18_R0A_IDENTITY_AUDIT_COMPLETE_AWAITING_EXPLICIT_AUTHORIZATION" if manifest["remediation_scope_deterministic"] and not blockers else "P1_18_R0A_PARTIAL_WITH_EXPLICIT_BLOCKERS"
    state = {
        "audit_id": AUDIT_ID,
        "primary_status": primary_status,
        "candidate_followup_stage": "P1_18_R0B_EXACT_IDENTITY_REMEDIATION" if primary_status.endswith("AWAITING_EXPLICIT_AUTHORIZATION") else "NONE",
        "next_allowed_stage": "NONE",
        "authorization_required": True,
        "r1_allowed": False,
        "p1_19_allowed": False,
        "blockers": blockers,
        "historical_identity": historical["historical_identity"],
        "identity_outcome": historical["identity_outcome"],
        "proposed_action": manifest["proposed_action"],
        "remediation_manifest_sha256": manifest["manifest_sha256"],
        "cpu_runtime_immutable": cpu_immutable,
        "postgres_still_shutdown": pg["identity"].get("cluster_state") == "shut down",
        "port_5432_unchanged_not_listening": no_5432_change,
    }
    write_json(ARTIFACT / "EXECUTION_STATE.json", state)
    report = f"""# P1-18 R0A PostgreSQL identity forensic audit

Primary status: `{primary_status}`

Audit ID: `{AUDIT_ID}`

Parent P1-18/R0 artifact integrity: parent and R0 ZIP SHA checks completed.

Git HEAD: `{baseline['git_head']}`

CPU runtime immutable: `{cpu_immutable}`

Current user/UID/GID: `{current_identity.get('current_login_name')}` / `{current_identity.get('current_uid')}` / `{current_identity.get('current_gid')}`

Exact PGDATA: `{pg['identity'].get('exact_pgdata')}`

PostgreSQL system identifier: `{pg['identity'].get('system_identifier')}`

PostgreSQL cluster state: `{pg['identity'].get('cluster_state')}`

PGDATA owner histogram: `{inventory['histogram']}`

Historical identity: `{historical['historical_identity']}`

Identity outcome: `{historical['identity_outcome']}`

Proposed action: `{manifest['proposed_action']}`

Remediation manifest SHA-256: `{manifest['manifest_sha256']}`

Blockers: `{', '.join(blockers) if blockers else 'NONE'}`

No PostgreSQL start, system/account/filesystem ownership mutation, database write, API/Dashboard change, training, or R1 execution was performed.
"""
    write_text(ARTIFACT / "FINAL_REPORT.md", report)
    package = package_artifact()
    state["package"] = package
    write_json(ARTIFACT / "EXECUTION_STATE.json", state)
    print(json.dumps({"audit_id": AUDIT_ID, "primary_status": primary_status, "blockers": blockers, "artifact": str(ARTIFACT), "zip": package}, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
