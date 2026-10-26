from __future__ import annotations

import json

from epidemic_core.kg_qwen.pipeline import TRACK_E_KEY
from epidemic_core.kg_r3c.dashboard_app import write_conn


def main() -> None:
    with write_conn() as conn:
        conn.execute("begin transaction isolation level repeatable read read only")
        names = [
            "epidemic.normalized_observations",
            "epidemic.forecast_runs",
            "epidemic_kg.kg_api_audit_log",
        ]
        existing = {
            name: conn.execute("select to_regclass(%s)::text", (name,)).fetchone()[0]
            for name in names
        }
        registry_count = conn.execute(
            "select count(*) from epidemic_kg.kg_qwen_heldout_registry where track_key=%s",
            (TRACK_E_KEY,),
        ).fetchone()[0]
        conn.rollback()
    print(json.dumps({"tables": existing, "track_registry_count": int(registry_count)}, sort_keys=True))


if __name__ == "__main__":
    main()
