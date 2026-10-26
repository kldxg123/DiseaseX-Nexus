from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import a100_p1_11 as p111

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "remote_snapshots"
FILES = [
    "src/epidemic_core/discovery/review_app.py",
    "src/epidemic_core/dashboard/app.py",
    "migrations/versions/0027_p1_14c_v2_preview_path_grants.py",
    "migrations/versions/0026_p1_12_preview_view_grants.py",
]


def main() -> None:
    client, _ = p111.p110r3.previous.p19.a100.connect()
    try:
        _data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        sftp = client.open_sftp()
        try:
            for rel in FILES:
                target = OUT / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                sftp.get(f"{project}/{rel}", str(target))
                print(target)
        finally:
            sftp.close()
    finally:
        client.close()


if __name__ == "__main__":
    main()
