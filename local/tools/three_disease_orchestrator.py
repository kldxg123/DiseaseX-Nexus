from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import a100_p1_11 as p111


ROOT = Path(__file__).resolve().parents[1]


def sanitize(text: str) -> str:
    text = re.sub(r"(?i)(password|token|secret|api[_-]?key)(\s*[=:]\s*)\S+", r"\1\2[redacted]", text)
    text = re.sub(r"postgresql(?:\+\w+)?://[^\s'\"\]]+", "postgresql://[redacted]", text)
    text = re.sub(r"(?i)(authorization:\s*)\S+", r"\1[redacted]", text)
    return text


def run_remote_script(
    local_script: str,
    remote_name: str,
    *,
    with_db_secrets: bool,
    timeout: int,
    extra_env: dict[str, str] | None = None,
    extra_uploads: list[tuple[str, str]] | None = None,
) -> None:
    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        python = f"{data_root}/conda/envs/epidemic-core/bin/python"
        remote_script = f"/tmp/{remote_name}"
        sftp = client.open_sftp()
        try:
            for local_extra, remote_extra in extra_uploads or []:
                sftp.put(str(ROOT / local_extra), remote_extra)
            sftp.put(str(ROOT / local_script), remote_script)
        finally:
            sftp.close()
        env = ""
        if with_db_secrets:
            env = (
                f"source {p111.q(project + '/scripts/database/_env.sh')}; set -a; "
                f"source {p111.q(project + '/.secrets/postgres.env')}; set +a; "
            )
        if extra_env:
            env += "".join(f"export {key}={p111.q(value)}; " for key, value in extra_env.items())
        command = (
            f"cd {p111.q(project)}; "
            f"{env}"
            "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; "
            f"PROJECT={p111.q(project)} exec {p111.q(python)} {p111.q(remote_script)}"
        )
        rc, out, err = p111.p110r3.previous.p19.a100.run(client, command, timeout=timeout)
        if out:
            print(sanitize(out))
        if rc:
            if err:
                print(sanitize(err), file=sys.stderr)
            raise SystemExit(rc)
    finally:
        client.close()


def download_execution(execution_id: str) -> None:
    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        _data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        local_report = ROOT / "reports" / execution_id
        local_artifact = ROOT / "artifacts" / execution_id
        for target in (local_report, local_artifact):
            if target.exists():
                shutil.rmtree(target)
            target.mkdir(parents=True, exist_ok=True)
        listing = p111.p110r3.previous.p19.a100.checked(
            client,
            "python3 -c "
            + p111.q(
                "from pathlib import Path; import json, os; "
                f"project=Path({project!r}); eid={execution_id!r}; "
                "payload={kind:([str(p.relative_to(project/kind/eid)) for p in (project/kind/eid).rglob('*') if p.is_file()] if (project/kind/eid).exists() else []) for kind in ('reports','artifacts')}; "
                "print(json.dumps(payload))"
            ),
            timeout=120,
        )
        payload = json.loads(listing)
        sftp = client.open_sftp()
        try:
            for kind, local_root in (("reports", local_report), ("artifacts", local_artifact)):
                for rel in payload.get(kind, []):
                    target = local_root / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    sftp.get(f"{project}/{kind}/{execution_id}/{rel}", str(target))
            for suffix in (".zip", ".zip.sha256"):
                try:
                    sftp.get(f"{project}/reports/{execution_id}{suffix}", str(ROOT / "reports" / f"{execution_id}{suffix}"))
                except OSError:
                    pass
        finally:
            sftp.close()
        print(json.dumps({"execution_id": execution_id, "reports": len(payload.get("reports", [])), "artifacts": len(payload.get("artifacts", []))}, ensure_ascii=False))
    finally:
        client.close()


def download_two_wave(root_execution_id: str) -> None:
    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        data_root, _project = p111.p110r3.previous.p19.a100.paths(client)
        remote_root = f"{data_root}/epidemic-openclaw-evidence/{root_execution_id}"
        local_root = ROOT / "reports" / root_execution_id
        if local_root.exists():
            shutil.rmtree(local_root)
        local_root.mkdir(parents=True, exist_ok=True)
        listing = p111.p110r3.previous.p19.a100.checked(
            client,
            "python3 -c "
            + p111.q(
                "from pathlib import Path; import json; "
                f"root=Path({remote_root!r}); "
                "print(json.dumps([str(p.relative_to(root)) for p in root.rglob('*') if p.is_file()] if root.exists() else []))"
            ),
            timeout=120,
        )
        files = json.loads(listing)
        sftp = client.open_sftp()
        try:
            for rel in files:
                target = local_root / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                sftp.get(f"{remote_root}/{rel}", str(target))
        finally:
            sftp.close()
        print(json.dumps({"root_execution_id": root_execution_id, "files": len(files), "local_root": str(local_root)}, ensure_ascii=False))
    finally:
        client.close()


def download_two_wave_archives(root_execution_id: str) -> None:
    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        data_root, _project = p111.p110r3.previous.p19.a100.paths(client)
        remote_archives = f"{data_root}/epidemic-openclaw-evidence/{root_execution_id}/archives"
        local_archives = ROOT / "reports" / root_execution_id / "archives"
        local_archives.mkdir(parents=True, exist_ok=True)
        listing = p111.p110r3.previous.p19.a100.checked(
            client,
            "python3 -c "
            + p111.q(
                "from pathlib import Path; import json; "
                f"root=Path({remote_archives!r}); "
                "print(json.dumps([p.name for p in root.iterdir() if p.is_file()] if root.exists() else []))"
            ),
            timeout=120,
        )
        files = json.loads(listing)
        sftp = client.open_sftp()
        try:
            for name in files:
                sftp.get(f"{remote_archives}/{name}", str(local_archives / name))
        finally:
            sftp.close()
        print(json.dumps({"root_execution_id": root_execution_id, "archive_files": len(files), "local_archives": str(local_archives)}, ensure_ascii=False))
    finally:
        client.close()


def download_triptych_archives(root_execution_id: str) -> None:
    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        data_root, _project = p111.p110r3.previous.p19.a100.paths(client)
        remote_archives = f"{data_root}/epidemic-openclaw-evidence/{root_execution_id}/archives"
        local_archives = ROOT / "reports" / root_execution_id / "archives"
        local_archives.mkdir(parents=True, exist_ok=True)
        listing = p111.p110r3.previous.p19.a100.checked(
            client,
            "python3 -c "
            + p111.q(
                "from pathlib import Path; import json; "
                f"root=Path({remote_archives!r}); "
                "print(json.dumps([p.name for p in root.iterdir() if p.is_file()] if root.exists() else []))"
            ),
            timeout=120,
        )
        files = json.loads(listing)
        sftp = client.open_sftp()
        try:
            for name in files:
                sftp.get(f"{remote_archives}/{name}", str(local_archives / name))
        finally:
            sftp.close()
        print(json.dumps({"root_execution_id": root_execution_id, "archive_files": len(files), "local_archives": str(local_archives)}, ensure_ascii=False))
    finally:
        client.close()


def main() -> None:
    action = sys.argv[1] if len(sys.argv) > 1 else "schema-probe"
    if action == "schema-probe":
        run_remote_script("tools/three_disease_schema_probe_remote.py", "three_disease_schema_probe_remote.py", with_db_secrets=True, timeout=300)
    elif action == "compact-probe":
        run_remote_script("tools/three_disease_compact_probe_remote.py", "three_disease_compact_probe_remote.py", with_db_secrets=True, timeout=300)
    elif action == "source-probe":
        run_remote_script("tools/three_disease_source_probe_remote.py", "three_disease_source_probe_remote.py", with_db_secrets=False, timeout=300)
    elif action == "ecdc-probe":
        run_remote_script("tools/three_disease_ecdc_probe_remote.py", "three_disease_ecdc_probe_remote.py", with_db_secrets=False, timeout=300)
    elif action == "thl-probe":
        run_remote_script("tools/three_disease_thl_probe_remote.py", "three_disease_thl_probe_remote.py", with_db_secrets=False, timeout=300)
    elif action == "db-probe":
        run_remote_script("tools/three_disease_db_probe_remote.py", "three_disease_db_probe_remote.py", with_db_secrets=True, timeout=300)
    elif action == "repair-probe":
        run_remote_script("tools/three_disease_repair_probe_remote.py", "three_disease_repair_probe_remote.py", with_db_secrets=True, timeout=300)
    elif action == "http-probe":
        run_remote_script("tools/three_disease_http_probe_remote.py", "three_disease_http_probe_remote.py", with_db_secrets=False, timeout=300)
    elif action == "run":
        run_remote_script("tools/three_disease_e2e_remote.py", "three_disease_e2e_remote.py", with_db_secrets=True, timeout=14400)
    elif action == "repair-run":
        run_remote_script(
            "tools/three_disease_repair_remote.py",
            "three_disease_repair_remote.py",
            with_db_secrets=True,
            timeout=14400,
            extra_uploads=[("tools/three_disease_e2e_remote.py", "/tmp/three_disease_e2e_remote.py")],
        )
    elif action == "two-wave-m0":
        run_remote_script(
            "tools/two_wave_prod_control_remote.py",
            "two_wave_prod_control_remote.py",
            with_db_secrets=True,
            timeout=14400,
        )
    elif action == "two-wave-r1":
        run_remote_script(
            "tools/two_wave_prod_control_r1_remote.py",
            "two_wave_prod_control_r1_remote.py",
            with_db_secrets=True,
            timeout=21600,
            extra_uploads=[("tools/three_disease_e2e_remote.py", "/tmp/three_disease_e2e_remote.py")],
        )
    elif action == "trusted-triptych-only":
        run_remote_script(
            "tools/trusted_triptych_only_remote.py",
            "trusted_triptych_only_remote.py",
            with_db_secrets=True,
            timeout=21600,
            extra_uploads=[("tools/three_disease_e2e_remote.py", "/tmp/three_disease_e2e_remote.py")],
        )
    elif action == "triptych-auth-route-probe":
        run_remote_script(
            "tools/triptych_auth_route_probe_remote.py",
            "triptych_auth_route_probe_remote.py",
            with_db_secrets=False,
            timeout=300,
        )
    elif action == "triptych-source-snippet":
        run_remote_script(
            "tools/triptych_source_snippet_remote.py",
            "triptych_source_snippet_remote.py",
            with_db_secrets=False,
            timeout=300,
        )
    elif action == "triptych-clean-release-repair":
        run_remote_script(
            "tools/triptych_clean_release_repair_remote.py",
            "triptych_clean_release_repair_remote.py",
            with_db_secrets=True,
            timeout=21600,
        )
    elif action == "finalize":
        if len(sys.argv) < 3:
            raise SystemExit("usage: three_disease_orchestrator.py finalize <execution_id>")
        run_remote_script(
            "tools/three_disease_e2e_remote.py",
            "three_disease_e2e_remote.py",
            with_db_secrets=True,
            timeout=900,
            extra_env={"EXECUTION_ID": sys.argv[2], "RESUME_E2E_FINALIZE_ONLY": "1"},
        )
    elif action == "download":
        if len(sys.argv) < 3:
            raise SystemExit("usage: three_disease_orchestrator.py download <execution_id>")
        download_execution(sys.argv[2])
    elif action == "download-two-wave":
        if len(sys.argv) < 3:
            raise SystemExit("usage: three_disease_orchestrator.py download-two-wave <root_execution_id>")
        download_two_wave(sys.argv[2])
    elif action == "download-two-wave-archives":
        if len(sys.argv) < 3:
            raise SystemExit("usage: three_disease_orchestrator.py download-two-wave-archives <root_execution_id>")
        download_two_wave_archives(sys.argv[2])
    elif action == "download-triptych-archives":
        if len(sys.argv) < 3:
            raise SystemExit("usage: three_disease_orchestrator.py download-triptych-archives <root_execution_id>")
        download_triptych_archives(sys.argv[2])
    else:
        raise SystemExit(f"unknown action: {action}")


if __name__ == "__main__":
    main()
