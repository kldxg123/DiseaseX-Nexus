from __future__ import annotations

import json

from epidemic_core.kg_r3c.dbutil import connect
from epidemic_core.kg_qwen import pipeline


def main() -> None:
    with connect() as conn:
        conn.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        conn.execute("SET LOCAL statement_timeout = '30s'")
        conn.execute("SET LOCAL idle_in_transaction_session_timeout = '60s'")
        read_only = conn.execute("SHOW transaction_read_only").fetchone()[0]
        registry = conn.execute(
            """
            select track_key, status, count(*)
            from epidemic_kg.kg_qwen_heldout_registry
            group by track_key, status
            order by track_key, status
            """
        ).fetchall()
        track_d = pipeline.TRACKS[pipeline.TRACK_D_KEY]
        src = track_d["source_override"]
        metadata = conn.execute(
            """
            select count(distinct period_start), min(period_start), max(period_start),
                   count(*) filter (where value_numeric is null)
            from epidemic.normalized_observations
            where country_alpha2 = %s and disease_id = %s
              and metric_code = %s and granularity = %s
            """,
            (src["country_alpha2"], src["disease_id"], src["metric_code"], src["granularity"]),
        ).fetchone()
        payload = {
            "transaction_read_only": read_only,
            "registry": [
                {"track_key": row[0], "status": row[1], "count": int(row[2])}
                for row in registry
            ],
            "track_d_metadata_only": {
                "track_key": pipeline.TRACK_D_KEY,
                "distinct_periods": int(metadata[0]),
                "min_period": metadata[1].isoformat() if metadata[1] else None,
                "max_period": metadata[2].isoformat() if metadata[2] else None,
                "null_rows": int(metadata[3]),
                "hidden_values_read": False,
            },
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        conn.rollback()


if __name__ == "__main__":
    main()
