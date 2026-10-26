"""Execute the pre-registered Malaysia influenza Shadow track once."""

from __future__ import annotations

import json

from epidemic_core.kg_qwen.pipeline import TRACK_D_KEY, execute_autotuning
from epidemic_core.kg_r3c.dashboard_app import write_conn


def main() -> None:
    with write_conn() as conn:
        result = execute_autotuning(
            conn,
            actor="codex_track_d_20260813",
            idempotency_key="kq-trackD-qwen1-20260813",
            track_key=TRACK_D_KEY,
        )
        conn.commit()
    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
