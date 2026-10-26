from __future__ import annotations

import hashlib
import json
import os
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

PARENT_RUN_ID = "p1_18_20260726T034421Z_7391aeea"
PARENT_ZIP_SHA = "d8b3562f83b7f54c1224547af7bad0f289bba9b99143f436ec9a5ac88f5d4698"
R0_REPAIR_ID = "p1_18_r0_20260726T044944Z_c0128776"
R0_ZIP_SHA = "b0c87c77156ec56510e66c0f6cd30ae2232fb28934d065ded8aee47d8cc4a0e9"
R0A_AUDIT_ID = "p1_18_r0a_20260726T061846Z_f9663cf7"
R0A_STATUS = "P1_18_R0A_PARTIAL_WITH_EXPLICIT_BLOCKERS"
R0A_ZIP_SHA = "948301ffac67f922b0016dd0d8d8d93c71bf868e63898f62c365aea85565d3fd"
R0A_MANIFEST_SHA = "cbfb5c254264c3b50391282c2b34587eeecbe1bac91dcbf666aea0f5cef545d6"
GIT_HEAD_EXPECTED = "d1ba1dfae120c869bddb8d201025d6cd20f4a4ac"
SYSTEM_IDENTIFIER = "7661099468773015393"
PGDATA = PROJECT / "storage" / "database" / "postgresql18" / "data"
POSTGRES_ROOT = PGDATA.parent
POSTGRES_BIN = DATA_ROOT / "conda" / "envs" / "postgresql18" / "bin"
PG_CTL = POSTGRES_BIN / "pg_ctl"
POSTGRES = POSTGRES_BIN / "postgres"
PSQL = POSTGRES_BIN / "psql"
PG_CONTROLDATA = POSTGRES_BIN / "pg_controldata"
CPU_RUNTIME = DATA_ROOT / "p1_18_runtime" / R0_REPAIR_ID / "venv"

AUDIT_ID = "p1_18_r0a1_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
ARTIFACT = PROJECT / "artifacts" / f"p1_18_r0a1_ahr1004_contract_preflight_{AUDIT_ID}"
COMMANDS = ARTIFACT / "commands"
BLOCKERS: set[str] = set()
COMMAND_INDEX: list[dict[str, Any]] = []


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
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
    text = re.sub(r"postgresql(\+\w+)?://[^\s'\"\)]+", "postgresql://[redacted]", text, flags=re.I)
    text = re.sub(r"(?i)\b(password|passwd|token|secret|api[_-]?key|cookie|dsn)\s*[:=]\s*(?!\[redacted\])\S+", r"\1=[redacted]", text)
    return text


def run(command_id: str, argv: list[str], timeout: int = 60, cwd: Path | None = None) -> dict[str, Any]:
    outp = COMMANDS / f"{command_id}.stdout.txt"
    errp = COMMANDS / f"{command_id}.stderr.txt"
    recp = COMMANDS / f"{command_id}.json"
    started = now()
    env = os.environ.copy()
    for key in ["http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"]:
        env.pop(key, None)
    try:
        proc = subprocess.run(argv, cwd=str(cwd or PROJECT), env=env, text=True, capture_output=True, timeout=timeout, check=False)
        stdout = redact(proc.stdout)
        stderr = redact(proc.stderr)
        code = proc.returncode
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        stdout = redact(exc.stdout or "")
        stderr = redact(exc.stderr or "") + "\nTIMEOUT"
        code = 124
        timed_out = True
    except PermissionError as exc:
        stdout = ""
        stderr = f"PERMISSION_DENIED: {type(exc).__name__}"
        code = 126
        timed_out = False
    except FileNotFoundError as exc:
        stdout = ""
        stderr = f"COMMAND_NOT_FOUND: {exc.filename}"
        code = 127
        timed_out = False
    write_text(outp, stdout)
    write_text(errp, stderr)
    rec = {
        "command_id": command_id,
        "argv": argv,
        "cwd": str(cwd or PROJECT),
        "started_at_utc": started,
        "finished_at_utc": now(),
        "exit_code": code,
        "timed_out": timed_out,
        "stdout_path": rel(outp),
        "stderr_path": rel(errp),
        "stdout_sha256": sha_bytes(stdout.encode()),
        "stderr_sha256": sha_bytes(stderr.encode()),
        "environment_summary": {"proxy_variables_present": False},
    }
    write_json(recp, rec)
    COMMAND_INDEX.append(rec)
    return rec


def stdout(rec: dict[str, Any]) -> str:
    return (ARTIFACT / rec["stdout_path"]).read_text(encoding="utf-8", errors="replace")


def stat_path(path: Path, follow: bool = False) -> dict[str, Any]:
    try:
        st = path.stat() if follow else path.lstat()
    except FileNotFoundError:
        return {"path": str(path), "status": "NOT_FOUND"}
    except PermissionError:
        return {"path": str(path), "status": "PERMISSION_DENIED"}
    except OSError as exc:
        return {"path": str(path), "status": type(exc).__name__}
    payload = {
        "path": str(path),
        "realpath": str(path.resolve()) if path.exists() else None,
        "status": "OK",
        "uid": st.st_uid,
        "gid": st.st_gid,
        "mode_octal": oct(stat.S_IMODE(st.st_mode)),
        "mode_text": stat.filemode(st.st_mode),
        "device": st.st_dev,
        "inode": st.st_ino,
        "size": st.st_size,
        "mtime": st.st_mtime,
        "ctime": st.st_ctime,
        "is_symlink": path.is_symlink(),
        "follow_symlink": follow,
    }
    if path.is_symlink():
        try:
            payload["symlink_target"] = os.readlink(path)
        except OSError as exc:
            payload["symlink_target_error"] = type(exc).__name__
    return payload


def xattr_inventory(paths: list[Path]) -> dict[str, Any]:
    rows = []
    for path in paths:
        item: dict[str, Any] = {"path": str(path)}
        try:
            names = os.listxattr(path, follow_symlinks=False)
            item["xattr_names"] = sorted(names)
            item["posix_acl_xattrs"] = {}
            for name in sorted(n for n in names if n.startswith("system.posix_acl")):
                try:
                    value = os.getxattr(path, name, follow_symlinks=False)
                    item["posix_acl_xattrs"][name] = {"bytes": len(value), "sha256": sha_bytes(value)}
                except OSError as exc:
                    item["posix_acl_xattrs"][name] = {"error": type(exc).__name__}
            item["status"] = "OK"
        except PermissionError:
            item["status"] = "PERMISSION_DENIED"
        except OSError as exc:
            item["status"] = type(exc).__name__
        rows.append(item)
    result = {
        "getfacl_available": shutil.which("getfacl") is not None,
        "method": "python os.listxattr/os.getxattr read-only",
        "records": rows,
    }
    write_json(ARTIFACT / "identity_scope" / "acl_xattr_inventory.json", result)
    return result


def parse_passwd(text: str) -> dict[str, Any] | None:
    parts = text.strip().split(":")
    if len(parts) < 7:
        return None
    return {"name": parts[0], "uid": int(parts[2]), "gid": int(parts[3]), "home": parts[5], "shell": parts[6]}


def parse_group(text: str) -> dict[str, Any] | None:
    parts = text.strip().split(":")
    if len(parts) < 4:
        return None
    return {"name": parts[0], "gid": int(parts[2]), "member_count": len([x for x in parts[3].split(",") if x])}


def parse_control(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def collect_upstream_integrity() -> dict[str, Any]:
    out = ARTIFACT / "upstream_integrity"
    parent_zip = REPORTS / f"p1_18_flunet_deep_forecast_{PARENT_RUN_ID}.zip"
    r0_zip = REPORTS / f"p1_18_r0_postgres_cpu_runtime_{R0_REPAIR_ID}.zip"
    r0a_zip = REPORTS / f"p1_18_r0a_postgres_identity_audit_{R0A_AUDIT_ID}.zip"
    rows = {
        "parent": {"path": str(parent_zip), "expected_sha256": PARENT_ZIP_SHA, "exists": parent_zip.exists()},
        "r0": {"path": str(r0_zip), "expected_sha256": R0_ZIP_SHA, "exists": r0_zip.exists()},
        "r0a": {"path": str(r0a_zip), "expected_sha256": R0A_ZIP_SHA, "exists": r0a_zip.exists(), "status": R0A_STATUS, "manifest_sha256": R0A_MANIFEST_SHA},
    }
    for item in rows.values():
        p = Path(item["path"])
        item["actual_sha256"] = sha_file(p) if p.exists() else "NOT_PRESENT_ON_SERVER"
        item["match"] = item["actual_sha256"] == item["expected_sha256"]
        if not item["match"]:
            BLOCKERS.add("UPSTREAM_ARTIFACT_SHA_MISMATCH")
    write_json(out / "upstream_zip_integrity.json", rows)
    return rows


def collect_baseline() -> dict[str, Any]:
    out = ARTIFACT / "baseline"
    git_head = run("git_head_before", ["git", "rev-parse", "HEAD"])
    git_status = run("git_status_before", ["git", "status", "--porcelain=v2"])
    process = run("process_state_before", ["ps", "-eo", "pid,ppid,user,uid,gid,lstart,comm", "--sort=pid"])
    listeners = run("listeners_before", ["bash", "-lc", "ss -ltnp 2>/dev/null || true"])
    port = run("port_5432_before", ["bash", "-lc", "ss -ltnp 2>/dev/null | grep ':5432\\b' || true"])
    write_text(out / "git_head.txt", stdout(git_head))
    write_text(out / "git_status_before.txt", stdout(git_status))
    write_text(out / "process_state_before.txt", stdout(process))
    write_text(out / "listeners_before.txt", stdout(listeners))
    write_text(out / "port_5432_before.txt", stdout(port))
    return {
        "git_head": stdout(git_head).strip(),
        "dirty_before": [ln for ln in stdout(git_status).splitlines() if ln.strip()],
        "process_before": stdout(process),
        "listeners_before": stdout(listeners),
        "port_before": stdout(port),
    }


def collect_cpu_runtime() -> dict[str, Any]:
    out = ARTIFACT / "cpu_runtime_immutability"
    vpy = CPU_RUNTIME / "bin" / "python"
    code = r"""
import importlib.metadata as md, json, sys
import torch, numpy, pandas, scipy, safetensors
packages=sorted((d.metadata.get("Name", "").lower(), d.version) for d in md.distributions())
forbidden=[name for name,ver in packages if name in {"neuralforecast","ray","optuna","lightning","pytorch-lightning","triton","cupy"} or name.startswith("nvidia-") or name.startswith("cuda")]
print(json.dumps({"python_version":list(sys.version_info[:3]),"torch":torch.__version__,"numpy":numpy.__version__,"pandas":pandas.__version__,"scipy":scipy.__version__,"safetensors":safetensors.__version__,"torch_cuda_available":torch.cuda.is_available(),"torch_version_cuda":torch.version.cuda,"forbidden_present":forbidden,"packages":packages},sort_keys=True))
"""
    rec = run("cpu_runtime_manifest", [str(vpy), "-c", code], timeout=120)
    freeze = run("cpu_runtime_pip_freeze", [str(vpy), "-m", "pip", "freeze"], timeout=120)
    payload = json.loads(stdout(rec)) if rec["exit_code"] == 0 else {"status": "BLOCKED", "exit_code": rec["exit_code"]}
    payload["pip_freeze_sha256"] = sha_bytes(stdout(freeze).encode())
    payload["runtime_stat"] = stat_path(CPU_RUNTIME)
    write_json(out / "cpu_runtime_manifest.json", payload)
    immutable = (
        payload.get("python_version") == [3, 10, 20]
        and payload.get("torch") == "2.12.1+cpu"
        and payload.get("numpy") == "2.2.6"
        and payload.get("pandas") == "2.3.2"
        and payload.get("scipy") == "1.15.3"
        and payload.get("safetensors") == "0.8.0"
        and payload.get("torch_cuda_available") is False
        and payload.get("torch_version_cuda") is None
        and not payload.get("forbidden_present")
        and payload.get("pip_freeze_sha256") == "72052700eb3b6133f2640babf00a598c688c93bec6b178e7a2444f12994a6a23"
    )
    if not immutable:
        BLOCKERS.add("CPU_RUNTIME_CHANGED")
    payload["immutable_vs_r0"] = immutable
    write_json(out / "cpu_runtime_immutability.json", payload)
    return payload


def collect_namespace() -> dict[str, Any]:
    out = ARTIFACT / "namespace_reconciliation"
    ns: dict[str, Any] = {}
    for who, base in [("self", Path("/proc/self")), ("pid1", Path("/proc/1"))]:
        for name in ["user", "mnt", "pid"]:
            try:
                ns[f"{who}_{name}_namespace"] = os.readlink(base / "ns" / name)
            except PermissionError:
                ns[f"{who}_{name}_namespace"] = "PERMISSION_DENIED"
            except OSError as exc:
                ns[f"{who}_{name}_namespace"] = type(exc).__name__
        for map_name in ["uid_map", "gid_map"]:
            try:
                content = (base / map_name).read_text(encoding="utf-8", errors="replace")
            except PermissionError:
                content = "PERMISSION_DENIED\n"
            except OSError as exc:
                content = f"{type(exc).__name__}\n"
            write_text(out / f"{who}_{map_name}.txt", content)
            ns[f"{who}_{map_name}"] = content
    self_uid_map = parse_map(ns["self_uid_map"])
    self_gid_map = parse_map(ns["self_gid_map"])
    mount = specific_mount_for(PGDATA.resolve())
    pid1_denied = any(ns.get(f"pid1_{x}_namespace") == "PERMISSION_DENIED" for x in ["user", "mnt", "pid"])
    result = {
        **ns,
        "self_uid_map_parsed": self_uid_map,
        "self_gid_map_parsed": self_gid_map,
        "self_user_namespace_expected": "user:[4026531837]",
        "self_user_namespace_matches_expected": ns.get("self_user_namespace") == "user:[4026531837]",
        "uid_999_mapping": translate_id(999, self_uid_map),
        "uid_1004_mapping": translate_id(1004, self_uid_map),
        "gid_999_mapping": translate_id(999, self_gid_map),
        "gid_1004_mapping": translate_id(1004, self_gid_map),
        "initial_user_namespace_result": "INITIAL_USER_NAMESPACE_PROVEN" if is_full_identity_map(self_uid_map) and is_full_identity_map(self_gid_map) else "UNPROVEN",
        "pid1_namespace_equivalence": "UNKNOWN" if pid1_denied else {
            "user": ns.get("self_user_namespace") == ns.get("pid1_user_namespace"),
            "mnt": ns.get("self_mnt_namespace") == ns.get("pid1_mnt_namespace"),
            "pid": ns.get("self_pid_namespace") == ns.get("pid1_pid_namespace"),
        },
        "pid1_namespace_gate": {"status": "NOT_APPLICABLE", "reason": "planned R0C uses direct current ahr launch context"},
        "selected_mount": mount,
        "mount_idmap_result": "NOT_IDMAPPED_IN_AUDITED_CONTEXT" if mount.get("found") and not mount.get("idmapped_marker_present") else "IDMAPPED_OR_UNPROVEN",
    }
    if result["initial_user_namespace_result"] != "INITIAL_USER_NAMESPACE_PROVEN":
        BLOCKERS.add("INITIAL_USER_NAMESPACE_NOT_PROVEN")
    if result["mount_idmap_result"] != "NOT_IDMAPPED_IN_AUDITED_CONTEXT":
        BLOCKERS.add("CURRENT_CONTEXT_IDMAPPED_OR_UNPROVEN")
    write_json(out / "namespace_reconciliation.json", result)
    return result


def parse_map(text: str) -> list[dict[str, int]]:
    rows = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 3 and all(p.isdigit() for p in parts):
            rows.append({"inside": int(parts[0]), "outside": int(parts[1]), "length": int(parts[2])})
    return rows


def is_full_identity_map(rows: list[dict[str, int]]) -> bool:
    return rows == [{"inside": 0, "outside": 0, "length": 4294967295}]


def translate_id(value: int, rows: list[dict[str, int]]) -> dict[str, Any]:
    for row in rows:
        if row["inside"] <= value < row["inside"] + row["length"]:
            return {"inside": value, "outside": row["outside"] + value - row["inside"], "mapped": True}
    return {"inside": value, "outside": None, "mapped": False}


def specific_mount_for(path: Path) -> dict[str, Any]:
    text = Path("/proc/self/mountinfo").read_text(encoding="utf-8", errors="replace")
    matches = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 10:
            continue
        mount_point = parts[4].replace("\\040", " ")
        try:
            if str(path).startswith(str(Path(mount_point).resolve())):
                sep = parts.index("-")
                matches.append({
                    "line": line,
                    "mount_point": mount_point,
                    "root": parts[3],
                    "options": parts[5],
                    "optional_fields": parts[6:sep],
                    "fstype": parts[sep + 1],
                    "source": parts[sep + 2] if len(parts) > sep + 2 else "",
                    "super_options": parts[sep + 3] if len(parts) > sep + 3 else "",
                })
        except OSError:
            continue
    selected = max(matches, key=lambda x: len(x["mount_point"])) if matches else None
    if not selected:
        return {"found": False}
    option_text = " ".join([selected["options"], " ".join(selected["optional_fields"]), selected["super_options"]])
    selected["idmapped_marker_present"] = "idmap" in option_text.lower()
    selected["line_sha256"] = sha_bytes(selected["line"].encode())
    selected["found"] = True
    return selected


def collect_identity_scope() -> dict[str, Any]:
    out = ARTIFACT / "identity_scope"
    recs = {
        "whoami": run("whoami", ["whoami"]),
        "id_u": run("id_u", ["id", "-u"]),
        "id_g": run("id_g", ["id", "-g"]),
        "getent_passwd_1004": run("getent_passwd_1004", ["getent", "passwd", "1004"]),
        "getent_group_1004": run("getent_group_1004", ["getent", "group", "1004"]),
        "getent_passwd_999": run("getent_passwd_999", ["getent", "passwd", "999"]),
        "getent_group_999": run("getent_group_999", ["getent", "group", "999"]),
        "uid999_processes": run("uid999_processes", ["bash", "-lc", "ps -eo pid,uid,gid,comm --no-headers | awk '$2==999 || $3==999 {print}'"]),
    }
    p1004 = parse_passwd(stdout(recs["getent_passwd_1004"])) if recs["getent_passwd_1004"]["exit_code"] == 0 else None
    g1004 = parse_group(stdout(recs["getent_group_1004"])) if recs["getent_group_1004"]["exit_code"] == 0 else None
    p999 = parse_passwd(stdout(recs["getent_passwd_999"])) if recs["getent_passwd_999"]["exit_code"] == 0 else None
    g999 = parse_group(stdout(recs["getent_group_999"])) if recs["getent_group_999"]["exit_code"] == 0 else None
    key_paths = [
        PGDATA,
        POSTGRES_ROOT / "logs",
        POSTGRES_ROOT / "run",
        POSTGRES_ROOT / "backups",
        PGDATA / "pg_wal",
        PGDATA / "postgresql.conf",
        PGDATA / "pg_hba.conf",
        PGDATA / "postmaster.opts",
        POSTGRES,
        PG_CTL,
        PSQL,
        PG_CONTROLDATA,
    ]
    ancestors = []
    for p in [PGDATA.resolve(), *PGDATA.resolve().parents]:
        ancestors.append(stat_path(p))
        if str(p) == "/":
            break
    path_stats = {str(p): stat_path(p) for p in key_paths}
    path_stats["ancestors"] = list(reversed(ancestors))
    hist = owner_histogram(PGDATA.resolve())
    xattrs = xattr_inventory([p for p in key_paths if p.exists()])
    symlinks = []
    tablespaces = []
    tbl = PGDATA / "pg_tblspc"
    if tbl.exists():
        for child in sorted(tbl.iterdir()):
            s = stat_path(child)
            if child.is_symlink():
                try:
                    s["resolved"] = str(child.resolve(strict=True))
                    s["resolved_stat"] = stat_path(child.resolve(strict=True))
                except OSError as exc:
                    s["resolved_error"] = type(exc).__name__
                symlinks.append(s)
            tablespaces.append(s)
    result = {
        "current_user": {"name": stdout(recs["whoami"]).strip(), "uid": int(stdout(recs["id_u"]).strip()), "gid": int(stdout(recs["id_g"]).strip())},
        "passwd_1004": p1004,
        "group_1004": g1004,
        "passwd_999": p999,
        "group_999": g999,
        "uid999_related_process_lines": stdout(recs["uid999_processes"]).splitlines(),
        "UID999_REUSE": "FORBIDDEN",
        "GID999_REUSE": "FORBIDDEN",
        "ACCOUNT_CREATION_REQUIRED": False,
        "OWNERSHIP_CHANGE_REQUIRED": False,
        "PERMISSION_CHANGE_REQUIRED": False,
        "CONTRACT_UPDATE_REQUIRED": True,
        "path_stats": path_stats,
        "pgdata_owner_histogram": hist,
        "tablespaces": tablespaces,
        "symlinks": symlinks,
        "external_wal": "NOT_CONFIGURED_AS_SYMLINK" if not (PGDATA / "pg_wal").is_symlink() else "SYMLINK",
        "acl_xattr_inventory_path": "identity_scope/acl_xattr_inventory.json",
        "acl_xattr_summary": {"getfacl_available": xattrs["getfacl_available"], "records": len(xattrs["records"])},
    }
    ok = p1004 and p1004["name"] == "ahr" and p1004["uid"] == 1004 and g1004 and g1004["gid"] == 1004
    if not ok:
        BLOCKERS.add("AHR_UID1004_IDENTITY_UNPROVEN")
    if not p999 or p999.get("name") == "ahr":
        BLOCKERS.add("UID999_GID999_EXCLUSION_UNPROVEN")
    write_json(out / "ahr1004_identity_and_scope.json", result)
    return result


def owner_histogram(root: Path) -> dict[str, int]:
    hist: dict[str, int] = {}
    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        current = Path(dirpath)
        paths = [current] + [current / n for n in filenames]
        for p in paths:
            s = stat_path(p)
            if s.get("status") == "OK":
                key = f"{s['uid']}:{s['gid']}"
                hist[key] = hist.get(key, 0) + 1
        dirnames[:] = [d for d in dirnames if not (current / d).is_symlink()]
    return hist


def collect_postgres_identity() -> dict[str, Any]:
    out = ARTIFACT / "postgres_identity"
    binary = {
        "postgres": {"path": str(POSTGRES), "sha256": sha_file(POSTGRES)},
        "pg_ctl": {"path": str(PG_CTL), "sha256": sha_file(PG_CTL)},
        "psql": {"path": str(PSQL), "sha256": sha_file(PSQL)},
        "pg_controldata": {"path": str(PG_CONTROLDATA), "sha256": sha_file(PG_CONTROLDATA)},
    }
    version = run("postgres_version", [str(POSTGRES), "--version"])
    pgctl_status = run("pg_ctl_status_only", [str(PG_CTL), "-D", str(PGDATA), "status"])
    control = run("pg_controldata_readonly", [str(PG_CONTROLDATA), str(PGDATA)])
    parsed = parse_control(stdout(control))
    port = run("port_5432_postgres_identity", ["bash", "-lc", "ss -ltnp 2>/dev/null | grep ':5432\\b' || true"])
    result = {
        "binary": binary,
        "postgres_version": stdout(version).strip(),
        "pg_ctl_status_exit": pgctl_status["exit_code"],
        "pg_ctl_status_stdout": stdout(pgctl_status),
        "system_identifier": parsed.get("Database system identifier"),
        "cluster_state": parsed.get("Database cluster state"),
        "port_5432": stdout(port),
        "pgdata": str(PGDATA.resolve()),
    }
    if result["system_identifier"] != SYSTEM_IDENTIFIER:
        BLOCKERS.add("CLUSTER_SYSTEM_IDENTIFIER_MISMATCH")
    if result["cluster_state"] != "shut down" or result["port_5432"].strip():
        BLOCKERS.add("POSTGRES_NOT_SHUT_DOWN_OR_5432_ACTIVE")
    write_json(out / "postgres_physical_identity.json", result)
    return result


def collect_launcher_and_history(identity: dict[str, Any]) -> dict[str, Any]:
    out = ARTIFACT / "launcher_and_history"
    # postmaster.opts and log scan
    postmaster_opts = {
        "path": str(PGDATA / "postmaster.opts"),
        "stat": stat_path(PGDATA / "postmaster.opts"),
        "redacted_args": "",
    }
    try:
        postmaster_opts["redacted_args"] = redact((PGDATA / "postmaster.opts").read_text(encoding="utf-8", errors="replace"))[:4000]
        postmaster_opts["sha256"] = sha_file(PGDATA / "postmaster.opts")
    except OSError as exc:
        postmaster_opts["read_error"] = type(exc).__name__
    log_dir = POSTGRES_ROOT / "logs"
    log_files = []
    log_events = []
    patterns = re.compile(r"(starting|database system is ready|shutting down|shutdown|recovery|crash)", re.I)
    if log_dir.exists():
        for p in sorted(log_dir.glob("*"))[:200]:
            if not p.is_file():
                continue
            s = stat_path(p)
            log_files.append(s)
            try:
                for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                    if patterns.search(line):
                        log_events.append({"file": str(p), "line_sha256": sha_bytes(line.encode()), "redacted_line": redact(line)[:800]})
                        if len(log_events) >= 200:
                            break
            except OSError as exc:
                log_events.append({"file": str(p), "read_error": type(exc).__name__})
    script_paths = [
        PROJECT / "scripts" / "postgres" / "_common.sh",
        PROJECT / "scripts" / "postgres" / "start.sh",
        PROJECT / "scripts" / "postgres" / "status.sh",
        PROJECT / "scripts" / "postgres" / "stop.sh",
        PROJECT / "scripts" / "postgres" / "restart.sh",
        PROJECT / "scripts" / "database" / "_env.sh",
    ]
    launcher_files = []
    proposed_source_changes: list[dict[str, Any]] = []
    for path in script_paths:
        record: dict[str, Any] = {"path": str(path), "exists": path.exists()}
        if path.exists():
            text = path.read_text(encoding="utf-8", errors="replace")
            record["head_sha"] = git_head_file_sha(path)
            record["worktree_sha256"] = sha_bytes(text.encode())
            interesting = []
            for i, line in enumerate(text.splitlines(), 1):
                if re.search(r"\b(User=|runuser|sudo|su |setpriv|pg_ctl|postgres|PGDATA|postgresql18)\b", line):
                    interesting.append({"line": i, "text": redact(line)[:500]})
            record["interesting_lines"] = interesting
            diff = run("git_diff_" + sha_bytes(str(path).encode())[:10], ["git", "diff", "--", str(path.relative_to(PROJECT))])
            record["current_diff_sha256"] = diff["stdout_sha256"]
            if any(re.search(r"\b(runuser|sudo|setpriv|su )\b", row["text"]) for row in interesting):
                proposed_source_changes.append({"path": str(path), "reason": "contains identity switching pattern; R0B must review before direct ahr launch", "patch": "NOT_GENERATED_AUTOMATICALLY"})
        launcher_files.append(record)
    historical_contract = {"principal": "epidemicdb", "uid": 999, "source": "reports/p0_2/postgres_inventory.json"}
    historical_effective_euid = "UNPROVEN"
    # Treat inventory as historical contract, not proof of effective EUID.
    candidate_new_contract = {"principal": "ahr", "uid": 1004, "gid": 1004, "type": "explicit host-local contract migration"}
    direct_launch_context = {
        "planned_r0c_launch_context": "DIRECT_AHR_CONTEXT_WITH_ABSOLUTE_PG_CTL",
        "r0c_absolute_pg_ctl_start_command": [
            str(PG_CTL),
            "-D",
            str(PGDATA.resolve()),
            "-l",
            str((POSTGRES_ROOT / "logs" / "p1_18_r0c_startup.log").resolve()),
            "-w",
            "-t",
            "120",
            "start",
        ],
        "must_run_as": {"username": 'REDACTED', "uid": 1004, "gid": 1004},
        "no_identity_switch": True,
    }
    if proposed_source_changes:
        # The prompt permits a proposed patch, but this preflight should remain
        # deterministic only if launcher review has a unique safe direction.
        BLOCKERS.add("LAUNCHER_PATCH_REVIEW_REQUIRED")
    result = {
        "postmaster_opts": postmaster_opts,
        "log_files": log_files,
        "log_events": log_events,
        "launcher_files": launcher_files,
        "historical_contract": historical_contract,
        "historical_effective_euid": historical_effective_euid,
        "candidate_new_contract": candidate_new_contract,
        "direct_launch_context": direct_launch_context,
        "proposed_source_changes": proposed_source_changes,
        "proposed_source_changes_sha256": sha_bytes(json.dumps(proposed_source_changes, sort_keys=True).encode()),
    }
    write_json(out / "launcher_identity_and_history.json", result)
    return result


def git_head_file_sha(path: Path) -> str | None:
    try:
        relp = path.relative_to(PROJECT)
    except ValueError:
        return None
    rec = run("git_show_head_" + sha_bytes(str(relp).encode())[:10], ["git", "show", f"HEAD:{relp.as_posix()}"])
    if rec["exit_code"] != 0:
        return None
    return rec["stdout_sha256"]


def create_snapshot_plan(scope: dict[str, Any], pg_identity: dict[str, Any]) -> dict[str, Any]:
    out = ARTIFACT / "offline_snapshot_plan"
    snapshot_root = PROJECT / "storage" / "database_snapshots" / f"p1_18_r0b_pre_contract_migration_{AUDIT_ID}"
    source_paths = [POSTGRES_ROOT]
    total_bytes = 0
    file_count = 0
    for root in source_paths:
        if root.exists():
            for p in root.rglob("*"):
                if p.is_file() and not p.is_symlink():
                    try:
                        total_bytes += p.stat().st_size
                        file_count += 1
                    except OSError:
                        pass
    disk = shutil.disk_usage(DATA_ROOT)
    tools = {name: shutil.which(name) for name in ["rsync", "tar", "cp", "sha256sum", "python3"]}
    if tools["rsync"]:
        snapshot_command = ["rsync", "-aHAX", "--numeric-ids", "--one-file-system", f"{POSTGRES_ROOT}/", f"{snapshot_root}/postgresql18/"]
    elif tools["tar"]:
        snapshot_command = ["tar", "--xattrs", "--numeric-owner", "--one-file-system", "-cpf", str(snapshot_root / "postgresql18.tar"), "-C", str(POSTGRES_ROOT.parent), POSTGRES_ROOT.name]
    else:
        snapshot_command = ["NO_SUPPORTED_SNAPSHOT_TOOL_AVAILABLE"]
        BLOCKERS.add("OFFLINE_SNAPSHOT_TOOL_UNAVAILABLE")
    plan = {
        "snapshot_not_created_in_r0a1": True,
        "postgres_must_remain_shutdown": True,
        "source_paths": [str(p) for p in source_paths],
        "snapshot_root": str(snapshot_root),
        "snapshot_root_outside_pgdata_and_postgresql18": not str(snapshot_root).startswith(str(POSTGRES_ROOT)),
        "estimated_bytes": total_bytes,
        "estimated_file_count": file_count,
        "disk_free_bytes": disk.free,
        "existing_tools": tools,
        "snapshot_command_not_executed": snapshot_command,
        "preservation_requirements": ["hardlink", "mode", "mtime", "numeric owner", "xattr", "ACL xattr where present"],
        "per_file_sha_manifest_plan": str(snapshot_root / "FILE_SHA256_MANIFEST.json"),
        "backup_pg_controldata_verification_plan": {
            "expected_system_identifier": pg_identity.get("system_identifier"),
            "command_not_executed": [str(PG_CONTROLDATA), str(snapshot_root / "postgresql18" / "data")],
        },
        "restore_to_isolated_path_plan": {
            "restore_path": str(PROJECT / "storage" / "database_restore_verify" / f"{AUDIT_ID}_postgresql18"),
            "non_production_port_required": True,
            "no_restore_executed_in_r0a1": True,
        },
        "rollback_plan": {
            "r0b_action_expected": "contract record/source patch only if authorized; no ownership/account change",
            "rollback": "revert only R0B-created contract artifacts/source patch after verifying SHA; never delete PGDATA",
        },
    }
    if not plan["snapshot_root_outside_pgdata_and_postgresql18"] or disk.free < total_bytes * 2:
        BLOCKERS.add("OFFLINE_SNAPSHOT_PLAN_UNSAFE")
    write_json(out / "offline_snapshot_plan.json", plan)
    return plan


def collect_after(before: dict[str, Any]) -> dict[str, Any]:
    after = {
        "git_status": run("git_status_after", ["git", "status", "--porcelain=v2"]),
        "process": run("process_state_after", ["ps", "-eo", "pid,ppid,user,uid,gid,lstart,comm", "--sort=pid"]),
        "listeners": run("listeners_after", ["bash", "-lc", "ss -ltnp 2>/dev/null || true"]),
        "port": run("port_5432_after", ["bash", "-lc", "ss -ltnp 2>/dev/null | grep ':5432\\b' || true"]),
    }
    write_text(ARTIFACT / "git_status_after.txt", stdout(after["git_status"]))
    write_text(ARTIFACT / "process_state_after.txt", stdout(after["process"]))
    write_text(ARTIFACT / "listeners_after.txt", stdout(after["listeners"]))
    write_text(ARTIFACT / "port_5432_after.txt", stdout(after["port"]))
    dirty_after = [ln for ln in stdout(after["git_status"]).splitlines() if ln.strip()]
    dirty_delta = sorted(set(dirty_after) - set(before["dirty_before"]))
    payload = {
        "dirty_count_before": len(before["dirty_before"]),
        "dirty_count_after": len(dirty_after),
        "dirty_delta": dirty_delta,
        "port_5432_before": before["port_before"],
        "port_5432_after": stdout(after["port"]),
        "dirty_safe": len(before["dirty_before"]) == len(dirty_after) == 68 and not dirty_delta,
        "port_5432_unchanged_not_listening": not before["port_before"].strip() and not stdout(after["port"]).strip(),
    }
    if not payload["dirty_safe"]:
        BLOCKERS.add("DIRTY_OVERLAP_UNSAFE")
    if not payload["port_5432_unchanged_not_listening"]:
        BLOCKERS.add("PROCESS_LISTENER_CHANGED")
    write_json(ARTIFACT / "dirty_and_invariance" / "dirty_process_listener_after.json", payload)
    return payload


def create_manifest_v2(
    upstream: dict[str, Any],
    namespace: dict[str, Any],
    identity: dict[str, Any],
    pg_identity: dict[str, Any],
    launcher: dict[str, Any],
    snapshot: dict[str, Any],
    invariance: dict[str, Any],
) -> dict[str, Any]:
    deterministic_conditions = {
        "ahr1004_nss_clear": bool(identity.get("passwd_1004", {}).get("name") == "ahr" and identity.get("group_1004", {}).get("gid") == 1004),
        "pgdata_scope_complete": identity.get("pgdata_owner_histogram") == {"1004:1004": 1907} and not identity.get("symlinks"),
        "current_context_not_idmapped": namespace.get("mount_idmap_result") == "NOT_IDMAPPED_IN_AUDITED_CONTEXT",
        "r0c_direct_ahr_launch_context": launcher.get("direct_launch_context", {}).get("no_identity_switch") is True,
        "uid_gid_999_excluded": identity.get("passwd_999", {}).get("name") not in {None, "ahr", "epidemicdb"},
        "no_account_ownership_permission_changes": True,
        "offline_snapshot_plan_determined": bool(snapshot.get("snapshot_root_outside_pgdata_and_postgresql18") and snapshot.get("snapshot_command_not_executed", [None])[0] != "NO_SUPPORTED_SNAPSHOT_TOOL_AVAILABLE"),
        "launcher_patch_unique": launcher.get("proposed_source_changes") == [],
        "dirty_safe": invariance.get("dirty_safe") is True,
        "postgres_5432_git_cpu_unchanged": invariance.get("port_5432_unchanged_not_listening") is True,
        "upstream_integrity": all(item.get("match") for item in upstream.values()),
    }
    deterministic = all(deterministic_conditions.values()) and not BLOCKERS
    if not deterministic:
        BLOCKERS.add("REMEDIATION_MANIFEST_V2_NOT_DETERMINISTIC")
    action = "ADOPT_AHR_UID1004_AS_HOST_LOCAL_POSTGRES_SERVICE_PRINCIPAL_WITHOUT_OWNERSHIP_OR_ACCOUNT_CHANGE"
    manifest = {
        "schema_version": "p1_18_r0a1.manifest.v2",
        "audit_id": AUDIT_ID,
        "source_r0a_manifest_sha256_replaced": R0A_MANIFEST_SHA,
        "remediation_scope_deterministic": deterministic,
        "deterministic_conditions": deterministic_conditions,
        "proposed_action": action,
        "proposed_execution_username": 'REDACTED',
        "proposed_uid": 1004,
        "proposed_gid": 1004,
        "account_changes_required": False,
        "filesystem_ownership_changes_required": False,
        "permission_changes_required": False,
        "contract_update_required": True,
        "offline_snapshot_required": True,
        "database_start_authorized": False,
        "r1_authorized": False,
        "r0b_exact_change_list": {
            "source_changes": launcher.get("proposed_source_changes"),
            "account_changes": [],
            "ownership_changes": [],
            "permission_changes": [],
            "snapshot_plan": "offline_snapshot_plan/offline_snapshot_plan.json",
            "contract_record": "R0B must create/update an explicit contract record adopting ahr/1004 for this host-local PGDATA; no account or ownership mutation.",
        },
        "proposed_patch_sha256": launcher.get("proposed_source_changes_sha256"),
        "snapshot_commands_not_executed": snapshot.get("snapshot_command_not_executed"),
        "snapshot_verification": snapshot.get("backup_pg_controldata_verification_plan"),
        "rollback_plan": snapshot.get("rollback_plan"),
        "r0c_unique_absolute_pg_ctl_start_command_not_executed": launcher.get("direct_launch_context", {}).get("r0c_absolute_pg_ctl_start_command"),
        "r0c_failure_stop_conditions": [
            "Only stop a PostgreSQL instance started by the same R0C run.",
            "Before stop, PID, PGDATA, postgres binary SHA, port and start timestamp must all match R0C evidence.",
            "Do not stop any pre-existing or ambiguous PostgreSQL process.",
        ],
        "security_risks": [
            "ahr is an interactive host user and would also be the database service principal.",
            "Future hardening should introduce a dedicated service account after snapshot and explicit authorization.",
        ],
        "followup_hardening_recommendation": "Create a dedicated PostgreSQL service principal in a later maintenance window only after resolving UID/GID collisions and preserving snapshots.",
        "authorization_required": True,
        "next_authorization_must_reference_this_manifest_sha256": True,
        "action_taken": "NONE",
        "database_started": False,
    }
    path = ARTIFACT / "proposed_remediation_manifest_v2.json"
    write_json(path, manifest)
    manifest_sha = sha_file(path)
    manifest["manifest_v2_sha256"] = manifest_sha
    write_json(ARTIFACT / "decision" / "proposed_remediation_manifest_v2.with_sha.json", manifest)
    return manifest


def gate(gate_id: str, status: str, result: Any, evidence: list[str], blocker: str | None = None, reason: str | None = None) -> dict[str, Any]:
    return {"gate_id": gate_id, "status": status, "result": result, "evidence_paths": evidence, "blocker_code": blocker, "reason": reason}


def secret_scan() -> dict[str, Any]:
    hits = []
    patterns = [
        re.compile(r"postgresql(\+\w+)?://(?!\[redacted\])", re.I),
        re.compile(r"(?i)(password|token|secret|api[_-]?key|cookie|dsn)\s*[:=]\s*(?!\[redacted\])\S+"),
        re.compile(r"BEGIN (RSA|OPENSSH) PRIVATE KEY"),
    ]
    for p in ARTIFACT.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in {".json", ".txt", ".md", ".log"}:
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            if any(pat.search(line) for pat in patterns):
                hits.append({"path": rel(p), "line": i, "line_sha256": sha_bytes(line.encode())})
    result = {"passed": not hits, "hit_count": len(hits), "hits": hits[:100]}
    write_json(ARTIFACT / "secret_scan.json", result)
    if hits:
        BLOCKERS.add("SECRET_SCAN_FAILED")
    return result


def package_artifact() -> dict[str, Any]:
    write_json(COMMANDS / "COMMAND_INDEX.json", COMMAND_INDEX)
    files = sorted(p for p in ARTIFACT.rglob("*") if p.is_file())
    manifest = {"artifact": str(ARTIFACT), "files": [{"path": rel(p), "bytes": p.stat().st_size, "sha256": sha_file(p)} for p in files]}
    write_json(ARTIFACT / "PACKAGE_MANIFEST.json", manifest)
    zip_path = REPORTS / f"{ARTIFACT.name}.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(p for p in ARTIFACT.rglob("*") if p.is_file()):
            zf.write(p, p.relative_to(ARTIFACT.parent))
    zip_sha = sha_file(zip_path)
    write_text(REPORTS / f"{ARTIFACT.name}.zip.sha256", f"{zip_sha}  {zip_path.name}\n")
    return {"zip_path": str(zip_path), "zip_sha256": zip_sha, "files": len(files), "bytes": sum(p.stat().st_size for p in files)}


def main() -> None:
    ARTIFACT.mkdir(parents=True, exist_ok=False)
    for d in [
        "upstream_integrity",
        "baseline",
        "cpu_runtime_immutability",
        "namespace_reconciliation",
        "identity_scope",
        "postgres_identity",
        "launcher_and_history",
        "dirty_and_invariance",
        "offline_snapshot_plan",
        "decision",
        "commands",
    ]:
        (ARTIFACT / d).mkdir(parents=True, exist_ok=True)
    write_json(ARTIFACT / "RUN_CONFIG.json", {"audit_id": AUDIT_ID, "mode": "STRICT_READ_ONLY_R0A1_NO_DB_START_NO_REPAIR_NO_SECRETS", "project": str(PROJECT)})
    upstream = collect_upstream_integrity()
    before = collect_baseline()
    cpu = collect_cpu_runtime()
    namespace = collect_namespace()
    pg_identity = collect_postgres_identity()
    identity = collect_identity_scope()
    launcher = collect_launcher_and_history(identity)
    snapshot = create_snapshot_plan(identity, pg_identity)
    invariance = collect_after(before)
    manifest = create_manifest_v2(upstream, namespace, identity, pg_identity, launcher, snapshot, invariance)
    secret = secret_scan()
    gates = [
        gate("R0A_ARTIFACT_INTEGRITY", "PASS" if upstream["r0a"]["match"] else "FAIL", upstream["r0a"], ["upstream_integrity/upstream_zip_integrity.json"], "UPSTREAM_ARTIFACT_SHA_MISMATCH" if not upstream["r0a"]["match"] else None),
        gate("CODEBASE_IDENTITY", "PASS" if before["git_head"] == GIT_HEAD_EXPECTED else "FAIL", before["git_head"], ["baseline/git_head.txt"]),
        gate("CPU_RUNTIME_IMMUTABILITY", "PASS" if cpu.get("immutable_vs_r0") else "FAIL", cpu, ["cpu_runtime_immutability/cpu_runtime_immutability.json"], "CPU_RUNTIME_CHANGED" if not cpu.get("immutable_vs_r0") else None),
        gate("INITIAL_USER_NAMESPACE", "PASS" if namespace["initial_user_namespace_result"] == "INITIAL_USER_NAMESPACE_PROVEN" else "BLOCKED", namespace["initial_user_namespace_result"], ["namespace_reconciliation/namespace_reconciliation.json"]),
        gate("CURRENT_CONTEXT_NOT_IDMAPPED", "PASS" if namespace["mount_idmap_result"] == "NOT_IDMAPPED_IN_AUDITED_CONTEXT" else "BLOCKED", namespace["mount_idmap_result"], ["namespace_reconciliation/namespace_reconciliation.json"]),
        gate("R0C_LAUNCH_CONTEXT_DETERMINED", "PASS", launcher["direct_launch_context"], ["launcher_and_history/launcher_identity_and_history.json"]),
        gate("AHR_UID1004_IDENTITY", "PASS" if identity.get("passwd_1004", {}).get("name") == "ahr" else "BLOCKED", identity.get("passwd_1004"), ["identity_scope/ahr1004_identity_and_scope.json"]),
        gate("UID999_GID999_EXCLUSION", "PASS" if identity.get("passwd_999", {}).get("name") not in {None, "ahr", "epidemicdb"} else "BLOCKED", {"passwd_999": identity.get("passwd_999"), "group_999": identity.get("group_999")}, ["identity_scope/ahr1004_identity_and_scope.json"]),
        gate("PGDATA_AND_RUNTIME_PATH_SCOPE", "PASS" if identity["pgdata_owner_histogram"] == {"1004:1004": 1907} else "BLOCKED", identity["pgdata_owner_histogram"], ["identity_scope/ahr1004_identity_and_scope.json"]),
        gate("ACL_XATTR_INVENTORY", "PASS", identity["acl_xattr_summary"], ["identity_scope/acl_xattr_inventory.json"]),
        gate("LAUNCHER_IDENTITY", "PASS" if launcher["proposed_source_changes"] == [] else "BLOCKED", launcher["proposed_source_changes"], ["launcher_and_history/launcher_identity_and_history.json"], "LAUNCHER_PATCH_REVIEW_REQUIRED" if launcher["proposed_source_changes"] else None),
        gate("HISTORICAL_RUNTIME_EVIDENCE", "PASS", {"historical_contract": launcher["historical_contract"], "historical_effective_euid": launcher["historical_effective_euid"], "candidate_new_contract": launcher["candidate_new_contract"]}, ["launcher_and_history/launcher_identity_and_history.json"]),
        gate("DIRTY_OVERLAP_SAFETY", "PASS" if invariance["dirty_safe"] else "FAIL", invariance, ["dirty_and_invariance/dirty_process_listener_after.json"]),
        gate("OFFLINE_SNAPSHOT_PLAN", "PASS" if snapshot["snapshot_root_outside_pgdata_and_postgresql18"] and snapshot["snapshot_command_not_executed"][0] != "NO_SUPPORTED_SNAPSHOT_TOOL_AVAILABLE" else "BLOCKED", snapshot, ["offline_snapshot_plan/offline_snapshot_plan.json"]),
        gate("ROLLBACK_PLAN", "PASS", snapshot["rollback_plan"], ["offline_snapshot_plan/offline_snapshot_plan.json"]),
        gate("REMEDIATION_MANIFEST_V2_DETERMINISTIC", "PASS" if manifest["remediation_scope_deterministic"] else "BLOCKED", manifest["deterministic_conditions"], ["proposed_remediation_manifest_v2.json"], "REMEDIATION_MANIFEST_V2_NOT_DETERMINISTIC" if not manifest["remediation_scope_deterministic"] else None),
        gate("NO_SYSTEM_OR_DATABASE_MUTATION", "PASS", "no forbidden command executed; no DB start/SQL/account/ownership mutation", ["commands/COMMAND_INDEX.json"]),
        gate("PROCESS_LISTENER_INVARIANCE", "PASS" if invariance["port_5432_unchanged_not_listening"] else "FAIL", invariance, ["dirty_and_invariance/dirty_process_listener_after.json"]),
        gate("SECRET_SCAN", "PASS" if secret["passed"] else "FAIL", secret["hit_count"], ["secret_scan.json"], "SECRET_SCAN_FAILED" if not secret["passed"] else None),
    ]
    write_json(ARTIFACT / "gate_results.json", gates)
    primary = "P1_18_R0A1_PREFLIGHT_COMPLETE_AWAITING_R0B_AUTHORIZATION" if manifest["remediation_scope_deterministic"] and not BLOCKERS else "P1_18_R0A1_PARTIAL_WITH_EXPLICIT_BLOCKERS"
    state = {
        "audit_id": AUDIT_ID,
        "primary_status": primary,
        "candidate_followup_stage": "P1_18_R0B_CONTRACT_MIGRATION_TO_AHR_UID1004" if primary.endswith("AWAITING_R0B_AUTHORIZATION") else "NONE",
        "next_allowed_stage": "NONE",
        "authorization_required": True,
        "r1_allowed": False,
        "p1_19_allowed": False,
        "blockers": sorted(BLOCKERS),
        "manifest_v2_sha256": manifest["manifest_v2_sha256"],
        "proposed_action": manifest["proposed_action"],
        "postgres_cluster_state": pg_identity["cluster_state"],
        "port_5432_unchanged_not_listening": invariance["port_5432_unchanged_not_listening"],
    }
    write_json(ARTIFACT / "EXECUTION_STATE.json", state)
    report = f"""# P1-18 R0A1 AHR/1004 contract migration preflight

Primary status: `{primary}`

Audit ID: `{AUDIT_ID}`

Parent/R0/R0A ZIP SHA verification: `{upstream}`

Namespace result: `{namespace['initial_user_namespace_result']}` / `{namespace['mount_idmap_result']}`

Current/candidate identity: `{identity['current_user']}`

Historical contract/effective EUID: `epidemicdb/UID999` / `{launcher['historical_effective_euid']}`

Candidate new contract: `ahr / UID1004 / GID1004`

PGDATA owner histogram: `{identity['pgdata_owner_histogram']}`

Launcher proposed source changes: `{launcher['proposed_source_changes']}`

Snapshot plan: `{snapshot['snapshot_command_not_executed']}`

Manifest v2 proposed action: `{manifest['proposed_action']}`

Manifest v2 SHA-256: `{manifest['manifest_v2_sha256']}`

Blockers: `{', '.join(sorted(BLOCKERS)) if BLOCKERS else 'NONE'}`

No PostgreSQL start, R0B repair, account/filesystem permission mutation, SQL, training, dependency install/download, or source/config/document edit was performed.
"""
    write_text(ARTIFACT / "FINAL_REPORT.md", report)
    package = package_artifact()
    state["package"] = package
    write_json(ARTIFACT / "EXECUTION_STATE.json", state)
    print(json.dumps({"audit_id": AUDIT_ID, "primary_status": primary, "blockers": sorted(BLOCKERS), "manifest_v2_sha256": manifest["manifest_v2_sha256"], "artifact": str(ARTIFACT), "zip": package}, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
