from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import a100_p1_11 as p111


def main() -> None:
    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        _data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        code = r"""
from pathlib import Path
import json
import os
import pwd

root = Path(os.environ["PROJECT"]) / "artifacts"
dirs = sorted([p for p in root.glob("p1_18_r0_postgres_cpu_runtime_*") if p.is_dir()], key=lambda p: p.stat().st_mtime)
if not dirs:
    print(json.dumps({"latest": None, "files": []}, ensure_ascii=False))
else:
    latest = dirs[-1]
    files = sorted(str(p.relative_to(latest)) for p in latest.rglob("*") if p.is_file())
    bin_roots = [Path(os.environ["PROJECT"]).parent / "conda", Path(os.environ["PROJECT"]).parent, Path(os.environ["PROJECT"])]
    postgres_bin_dirs = []
    seen_bin_dirs = set()
    for bin_root in bin_roots:
        if not bin_root.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(bin_root, topdown=True):
            current = Path(dirpath)
            try:
                depth = len(current.relative_to(bin_root).parts)
            except ValueError:
                depth = 99
            dirnames[:] = [d for d in dirnames if d not in {"models", "node_modules", "p1_18_runtime", "artifacts", "storage", ".git", "__pycache__"}]
            if depth >= 5:
                dirnames[:] = []
            if {"postgres", "pg_ctl", "psql"}.issubset(set(filenames)):
                key = str(current)
                if key not in seen_bin_dirs:
                    seen_bin_dirs.add(key)
                    postgres_bin_dirs.append(key)
    summary = {
        "latest": latest.name,
        "file_count": len(files),
        "last_files": files[-40:],
        "has_execution_state": (latest / "EXECUTION_STATE.json").exists(),
        "has_gate_results": (latest / "gate_results.json").exists(),
        "has_final_report": (latest / "FINAL_REPORT.md").exists(),
        "report_zip_exists": (Path(os.environ["PROJECT"]) / "reports" / f"{latest.name}.zip").exists(),
        "report_zip_sha_exists": (Path(os.environ["PROJECT"]) / "reports" / f"{latest.name}.zip.sha256").exists(),
        "postgres_bin_dirs": postgres_bin_dirs,
        "epidemicdb_uid": pwd.getpwnam("epidemicdb").pw_uid if "epidemicdb" in {p.pw_name for p in pwd.getpwall()} else None,
    }
    print(json.dumps(summary, ensure_ascii=False))
"""
        command = (
            "cd "
            + p111.q(project)
            + "; PROJECT="
            + p111.q(project)
            + " python3 -c "
            + p111.q(code)
        )
        rc, out, err = p111.p110r3.previous.p19.a100.run(client, command, timeout=60)
        if rc:
            raise SystemExit(err)
        print(out)
    finally:
        client.close()


if __name__ == "__main__":
    main()
