from __future__ import annotations

import json

from epidemic_core.kg_qwen.pipeline import TRACK_E_KEY
from epidemic_core.kg_r3c.dashboard_app import write_conn


def first_value(row):
    return next(iter(row.values())) if hasattr(row, "values") else row[0]


def main() -> None:
    with write_conn() as conn:
        conn.execute("begin transaction isolation level repeatable read read only")
        names = [
            "epidemic.normalized_observations",
            "epidemic.forecast_runs",
            "epidemic_kg.kg_api_audit_log",
        ]
        existing = {
            name: first_value(conn.execute("select to_regclass(%s)::text", (name,)).fetchone())
            for name in names
        }
        registry_count = first_value(
            conn.execute(
                "select count(*) from epidemic_kg.kg_qwen_heldout_registry where track_key=%s",
                (TRACK_E_KEY,),
            ).fetchone()
        )
        conn.rollback()
    print(json.dumps({"tables": existing, "track_registry_count": int(registry_count)}, sort_keys=True))


if __name__ == "__main__":
    main()
