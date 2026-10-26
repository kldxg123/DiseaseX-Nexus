from __future__ import annotations

import json

from epidemic_core.kg_qwen.pipeline import TRACK_F_KEY
from epidemic_core.kg_r3c.dashboard_app import write_conn


def first(row):
    return next(iter(row.values())) if hasattr(row, "values") else row[0]


def main() -> None:
    with write_conn() as conn:
        conn.execute("begin transaction isolation level repeatable read read only")
        registry = first(
            conn.execute(
                "select count(*) from epidemic_kg.kg_qwen_heldout_registry where track_key=%s",
                (TRACK_F_KEY,),
            ).fetchone()
        )
        conn.rollback()
    print(json.dumps({"track_key": TRACK_F_KEY, "registry_count": int(registry)}, sort_keys=True))


if __name__ == "__main__":
    main()
