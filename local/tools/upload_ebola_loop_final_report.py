from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import a100_p1_11 as p111


ROOT = Path(__file__).resolve().parents[1]
FILES = [
    (
        ROOT / "reports/ebola_data_visualization_prediction_engineering_loop/FINAL_REPORT.md",
        "reports/ebola_data_visualization_prediction_engineering_loop/FINAL_REPORT.md",
    ),
    (
        ROOT / "reports/ebola_data_visualization_prediction_engineering_loop/browser_policy_blocker.json",
        "reports/ebola_data_visualization_prediction_engineering_loop/browser_policy_blocker.json",
    ),
    (
        ROOT / "reports/ebola_data_visualization_prediction_engineering_loop/human_review_gate_manifest.json",
        "reports/ebola_data_visualization_prediction_engineering_loop/human_review_gate_manifest.json",
    ),
    (
        ROOT / "reports/ebola_data_visualization_prediction_engineering_loop/HUMAN_REVIEW_ACTIONS.md",
        "reports/ebola_data_visualization_prediction_engineering_loop/HUMAN_REVIEW_ACTIONS.md",
    ),
]


def main() -> None:
    client, _gate = p111.p110r3.previous.p19.a100.connect()
    try:
        _data_root, project = p111.p110r3.previous.p19.a100.paths(client)
        sftp = client.open_sftp()
        try:
            for local, remote_rel in FILES:
                if local.exists():
                    sftp.put(str(local), f"{project}/{remote_rel}")
        finally:
            sftp.close()
        print(json.dumps({"uploaded": [remote for local, remote in FILES if local.exists()]}, ensure_ascii=False))
    finally:
        client.close()


if __name__ == "__main__":
    main()
