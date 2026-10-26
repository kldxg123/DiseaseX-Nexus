from __future__ import annotations

import json

from epidemic_core.kg_qwen.pipeline import TRACK_E, TRACK_E_KEY, _load_frozen_csv_rows
from epidemic_core.kg_r3c.dashboard_app import write_conn


def main() -> None:
    with write_conn() as conn:
        conn.execute("begin transaction isolation level repeatable read read only")
        registry = conn.execute(
            "select count(*) from epidemic_kg.kg_qwen_heldout_registry where track_key=%s",
            (TRACK_E_KEY,),
        ).fetchone()[0]
        trusted = conn.execute("select count(*) from epidemic.normalized_observations").fetchone()[0]
        conn.rollback()
    rows = _load_frozen_csv_rows(TRACK_E["frozen_source"])
    payload = {
        "track_key": TRACK_E_KEY,
        "registry_count": int(registry),
        "trusted_count": int(trusted),
        "calendar_rows": len(rows),
        "observed_rows": sum(row.value is not None for row in rows),
        "hidden_values_emitted": False,
    }
    print(json.dumps(payload, sort_keys=True))


if __name__ == "__main__":
    main()
