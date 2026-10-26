from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import a100_p1_11 as p111


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "remote_edit" / "three_disease_current"
FILES = [
    "src/epidemic_core/dashboard/app.py",
    "src/epidemic_core/api/app.py",
    "src/epidemic_core/api/routes/retrievals.py",
    "src/epidemic_core/p1_12/trusted.py",
    "src/epidemic_core/p1_12/dashboard.py",
    "src/epidemic_core/p1_12/__init__.py",
    "src/epidemic_core/p1_9/promotion.py",
    "src/epidemic_core/p1_9/dashboard_app.py",
    "src/epidemic_core/forecasting/__init__.py",
    "src/epidemic_core/forecasting/models.py",
    "src/epidemic_core/forecasting/features.py",
    "src/epidemic_core/forecasting/backtest.py",
    "src/epidemic_core/forecasting/publish.py",
    "src/epidemic_core/forecasting/trainer.py",
    "migrations/versions/0030_fasttrack_correctness_repair.py",
    "migrations/versions/0001_p1_1_p1_1_domain_storage.py",
    "migrations/versions/0026_p1_12_preview_view_grants.py",
    "migrations/versions/0029_fasttrack_forecast.py",
    "pyproject.toml",
    "README.md",
    "AGENTS.md",
]


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True, exist_ok=True)
    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        _data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        sftp = client.open_sftp()
        copied = []
        missing = []
        try:
            for rel in FILES:
                target = OUT / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    sftp.get(f"{project}/{rel}", str(target))
                    copied.append(rel)
                except OSError:
                    missing.append(rel)
        finally:
            sftp.close()
        print({"out": str(OUT), "copied": copied, "missing": missing})
    finally:
        client.close()


if __name__ == "__main__":
    main()
