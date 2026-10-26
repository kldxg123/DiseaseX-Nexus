from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path


PROJECT = Path(os.environ.get("PROJECT", ".")).resolve()


def run(args: list[str], timeout: int = 60) -> dict[str, object]:
    try:
        p = subprocess.run(args, cwd=PROJECT, text=True, capture_output=True, timeout=timeout)
        return {"returncode": p.returncode, "stdout": p.stdout[-8000:], "stderr": p.stderr[-4000:]}
    except Exception as exc:
        return {"error": type(exc).__name__, "message": str(exc)}


def main() -> None:
    py = os.environ.get("PYTHON") or os.environ.get("VIRTUAL_ENV_PYTHON") or "python"
    code = r"""
import json, importlib.util, sys, os
mods={}
for name in ['torch','neuralforecast','safetensors','numpy','pandas','sklearn']:
    spec=importlib.util.find_spec(name)
    mods[name]={'present': spec is not None}
    if spec is not None:
        try:
            mod=__import__(name)
            mods[name]['version']=getattr(mod,'__version__',None)
        except Exception as e:
            mods[name]['import_error']=type(e).__name__
print(json.dumps({'python':sys.version,'executable':sys.executable,'mods':mods,'cuda_visible_devices':os.environ.get('CUDA_VISIBLE_DEVICES')},ensure_ascii=False))
"""
    result = {
        "project": str(PROJECT),
        "database_scripts": sorted(str(p.relative_to(PROJECT)) for p in (PROJECT / "scripts" / "database").glob("*") if p.is_file()) if (PROJECT / "scripts" / "database").exists() else [],
        "dashboard_scripts": sorted(str(p.relative_to(PROJECT)) for p in (PROJECT / "scripts" / "dashboard").glob("*") if p.is_file()) if (PROJECT / "scripts" / "dashboard").exists() else [],
        "python_imports": run([py, "-c", code]),
        "postgres_ports": run(["bash", "-lc", "ss -ltnp 2>/dev/null | grep ':5432\\b' || true"]),
        "postgres_process": run(["bash", "-lc", "ps -eo pid,ppid,stat,etime,pcpu,pmem,comm,args | grep -E 'postgres|postmaster' | grep -v grep || true"]),
        "database_dirs": run(["bash", "-lc", "find storage -maxdepth 3 -type d \\( -iname '*postgres*' -o -iname '*pgsql*' -o -iname 'pgdata' \\) 2>/dev/null | sort | head -50"]),
        "which_pg_ctl": shutil.which("pg_ctl"),
        "which_psql": shutil.which("psql"),
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
