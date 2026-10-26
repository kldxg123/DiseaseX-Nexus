from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import p1_15_orchestrator as orch  # noqa: E402


def download_tree(sftp, remote_root: str, local_root: Path, files: list[str]) -> None:
    if local_root.exists():
        raise SystemExit(f"local target already exists: {local_root}")
    local_root.mkdir(parents=True)
    for rel in files:
        target = local_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        sftp.get(f"{remote_root}/{rel}", str(target))


def main() -> None:
    client, _gate = orch.p111.p110r3.previous.p19.a100.connect()
    try:
        data_root, project = orch.p111.p110r3.previous.p19.a100.paths(client)
        python = f"{data_root}/conda/envs/epidemic-core/bin/python"
        remote_script = "/tmp/e2e_archive_finalization_remote.py"
        local_complete_evidence = ROOT / "reports" / "epidemic_fasttrack_correctness_repair_20260726T105104Z_e4d178f4_complete_evidence.zip"
        remote_complete_evidence = "/tmp/epidemic_fasttrack_complete_evidence_for_archive.zip"
        sftp = client.open_sftp()
        try:
            sftp.put(str(ROOT / "tools" / "e2e_archive_finalization_remote.py"), remote_script)
            if local_complete_evidence.exists():
                sftp.put(str(local_complete_evidence), remote_complete_evidence)
        finally:
            sftp.close()
        command = (
            f"cd {orch.p111.q(project)}; "
            f"source {orch.p111.q(project + '/scripts/database/_env.sh')}; set -a; "
            f"source {orch.p111.q(project + '/.secrets/postgres.env')}; set +a; "
            f"[ -f {orch.p111.q(project + '/scripts/dashboard/dashboard_env.sh')} ] && source {orch.p111.q(project + '/scripts/dashboard/dashboard_env.sh')}; "
            f"if [ -f {orch.p111.q(project + '/.secrets/dashboard.env')} ]; then set -a; source {orch.p111.q(project + '/.secrets/dashboard.env')}; set +a; fi; "
            "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; "
            f"PROJECT={orch.p111.q(project)} COMPLETE_EVIDENCE_ZIP_PATH={orch.p111.q(remote_complete_evidence)} exec {orch.p111.q(python)} {orch.p111.q(remote_script)}"
        )
        rc, out, err = orch.p111.p110r3.previous.p19.a100.run(client, command, timeout=1800)
        if rc:
            print(orch.sanitize(out))
            print(orch.sanitize(err), file=sys.stderr)
            raise SystemExit(rc)
        payload = json.loads(out.strip().splitlines()[-1])
        archive_run_id = payload["archive_run_id"]
        local_report = ROOT / "reports" / archive_run_id
        local_artifact = ROOT / "artifacts" / archive_run_id
        local_zip = ROOT / "reports" / f"{archive_run_id}.zip"
        local_sha = ROOT / "reports" / f"{archive_run_id}.zip.sha256"
        for target in [local_report, local_artifact, local_zip, local_sha]:
            if target.exists():
                raise SystemExit(f"local target already exists: {target}")
        listing_cmd = (
            "python3 -c "
            + orch.p111.q(
                "from pathlib import Path; import json; "
                f"project=Path({project!r}); name={archive_run_id!r}; "
                "roots={'report':project/'reports'/name, 'artifact':project/'artifacts'/name}; "
                "print(json.dumps({k:[str(p.relative_to(v)) for p in v.rglob('*') if p.is_file()] for k,v in roots.items()}))"
            )
        )
        listing = json.loads(orch.remote_run(client, listing_cmd, timeout=300))
        sftp = client.open_sftp()
        try:
            download_tree(sftp, f"{project}/reports/{archive_run_id}", local_report, listing["report"])
            download_tree(sftp, f"{project}/artifacts/{archive_run_id}", local_artifact, listing["artifact"])
            local_zip.parent.mkdir(parents=True, exist_ok=True)
            sftp.get(f"{project}/reports/{archive_run_id}.zip", str(local_zip))
            sftp.get(f"{project}/reports/{archive_run_id}.zip.sha256", str(local_sha))
        finally:
            sftp.close()
        print(
            json.dumps(
                {
                    "archive_run_id": archive_run_id,
                    "archive_status": payload["archive_status"],
                    "operational_status": payload["operational_status"],
                    "local_report_dir": str(local_report),
                    "local_artifact_dir": str(local_artifact),
                    "local_zip": str(local_zip),
                    "local_sha256": str(local_sha),
                    "zip_sha256": payload["zip_sha256"],
                    "remaining_gaps_count": payload["remaining_gaps_count"],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    finally:
        client.close()


if __name__ == "__main__":
    main()
