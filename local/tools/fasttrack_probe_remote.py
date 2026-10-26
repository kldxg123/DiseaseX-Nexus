from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


PROJECT = Path(os.environ.get("PROJECT", ".")).resolve()
DATA_ROOT = PROJECT.parent
PGDATA = PROJECT / "storage" / "database" / "postgresql18" / "data"
POSTGRES_BIN = DATA_ROOT / "conda" / "envs" / "postgresql18" / "bin"


def run(argv: list[str], timeout: int = 30, cwd: Path | None = None) -> dict:
    try:
        p = subprocess.run(argv, cwd=str(cwd or PROJECT), text=True, capture_output=True, timeout=timeout)
        return {"argv": argv, "rc": p.returncode, "stdout": p.stdout[:8000], "stderr": p.stderr[:4000]}
    except Exception as exc:
        return {"argv": argv, "error": type(exc).__name__, "message": str(exc)[:1000]}


def main() -> None:
    payload = {
        "project": str(PROJECT),
        "pwd_P": run(["pwd", "-P"]),
        "whoami": run(["whoami"]),
        "id": run(["id"]),
        "git_head": run(["git", "rev-parse", "HEAD"]),
        "git_status": run(["git", "status", "--porcelain=v2", "--untracked-files=all"]),
        "top_files": run(["bash", "-lc", "find . -maxdepth 2 -type f | sed 's#^./##' | sort | head -200"]),
        "top_dirs": run(["bash", "-lc", "find . -maxdepth 3 -type d | sed 's#^./##' | sort | head -300"]),
        "pg_ctl_status": run([str(POSTGRES_BIN / "pg_ctl"), "-D", str(PGDATA), "status"], timeout=60),
        "pg_controldata": run([str(POSTGRES_BIN / "pg_controldata"), str(PGDATA)], timeout=60),
        "port_5432": run(["bash", "-lc", "ss -ltnp 2>/dev/null | grep ':5432\\b' || true"]),
        "ports_project": run(["bash", "-lc", "ss -ltnp 2>/dev/null | grep -E ':(8010|8020|8021|8120|8121)\\b' || true"]),
        "python_envs": run(["bash", "-lc", "ls -1 ../conda/envs 2>/dev/null || true"]),
        "package_files": run(["bash", "-lc", "find . -maxdepth 3 \\( -name pyproject.toml -o -name package.json -o -name alembic.ini -o -name docker-compose.yml -o -name '*.service' \\) -print | sort"]),
        "scripts": run(["bash", "-lc", "find scripts -maxdepth 3 -type f 2>/dev/null | sort | head -300"]),
        "apps": run(["bash", "-lc", "find . -maxdepth 4 -type f \\( -name '*dashboard*' -o -name '*api*' -o -name '*forecast*' -o -name '*ingest*' -o -name '*flunet*' \\) | sort | head -300"]),
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
