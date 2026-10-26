from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import a100_p1_11 as p111


ROOT = Path(__file__).resolve().parents[1]
LOCAL_SCRIPT = ROOT / "tools" / "p1_14e_r2_2_retry2.py"
LOCAL_REPORT_DIR = ROOT / "reports" / "p1_14e_r2_2_exact_remediation_authorization_packet_retry2_all_files"
REMOTE_OUT = "/tmp/p1_14e_r2_2_exact_remediation_authorization_packet_retry2"


def sanitize(value: str) -> str:
    value = re.sub(r"(?i)(password|token|secret|api[_-]?key)=\\S+", r"\\1=[redacted]", value)
    value = re.sub(r"postgresql(\\+\\w+)?://[^\\s'\\\"]+", "postgresql://[redacted]", value)
    return value


def sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run() -> None:
    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        python = f"{data_root}/conda/envs/epidemic-core/bin/python"
        remote_script = "/tmp/p1_14e_r2_2_runner_retry2.py"
        sftp = client.open_sftp()
        try:
            sftp.put(str(LOCAL_SCRIPT), remote_script)
        finally:
            sftp.close()
        command = (
            f"cd {p111.q(project)}; "
            f"set -a; source {p111.q(project + '/scripts/database/_env.sh')}; set +a; "
            "unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; "
            f"PROJECT_ROOT={p111.q(project)} exec {p111.q(python)} {p111.q(remote_script)}"
        )
        rc, out, err = p111.p110r3.previous.p19.a100.run(client, command, timeout=1800)
        print(sanitize(out))
        if rc:
            print(sanitize(err), file=sys.stderr)
            raise SystemExit(rc)
    finally:
        client.close()


def download() -> None:
    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        if LOCAL_REPORT_DIR.exists():
            shutil.rmtree(LOCAL_REPORT_DIR)
        LOCAL_REPORT_DIR.mkdir(parents=True)
        listing = p111.p110r3.previous.p19.a100.checked(
            client,
            "python3 -c "
            + p111.q(
                "from pathlib import Path; import json; "
                f"root=Path({REMOTE_OUT!r}); "
                "print(json.dumps(sorted([p.name for p in root.iterdir() if p.is_file()])))"
            ),
        )
        names = json.loads(listing)
        sftp = client.open_sftp()
        try:
            for name in names:
                sftp.get(f"{REMOTE_OUT}/{name}", str(LOCAL_REPORT_DIR / name))
        finally:
            sftp.close()
        manifest = []
        for path in sorted(LOCAL_REPORT_DIR.iterdir()):
            if path.is_file():
                manifest.append({"file": path.name, "bytes": path.stat().st_size, "sha256": sha_file(path)})
        (LOCAL_REPORT_DIR / "LOCAL_DOWNLOAD_MANIFEST.json").write_text(
            json.dumps({"remote_dir": REMOTE_OUT, "file_count": len(manifest), "files": manifest}, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(json.dumps({"local_dir": str(LOCAL_REPORT_DIR), "file_count": len(manifest), "local_manifest_sha256": sha_file(LOCAL_REPORT_DIR / "LOCAL_DOWNLOAD_MANIFEST.json")}, ensure_ascii=False, indent=2, sort_keys=True))
    finally:
        client.close()


def main() -> None:
    action = sys.argv[1] if len(sys.argv) > 1 else "run"
    if action == "run":
        run()
    elif action == "download":
        download()
    else:
        raise SystemExit(f"unknown action: {action}")


if __name__ == "__main__":
    main()
