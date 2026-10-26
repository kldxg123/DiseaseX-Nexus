from pathlib import Path

import psycopg

env: dict[str, str] = {}
for line in Path("USER_HOME/projects/openclaw/runtime/secrets/vm201_runtime.env").read_text().splitlines():
    if "=" in line and not line.strip().startswith("#"):
        key, value = line.split("=", 1)
        env[key] = value

conn = psycopg.connect("postgresql://postgres:" + env["PG_SUPERPASS"] + "@127.0.0.1:55432/epidemic_openclaw")
for row in conn.execute(
    """
    SELECT l.artifact_sha256, COUNT(*)
    FROM epidemic_p1_12.trusted_observation_lineage l
    JOIN epidemic_p1_12.trusted_source_contracts c ON c.id = l.source_contract_id
    WHERE c.source_key = 'cdc_fluview_ilinet_state_weekly'
    GROUP BY l.artifact_sha256
    ORDER BY COUNT(*) DESC
    LIMIT 10
    """
):
    print("LINEAGE_SHA", row)
for row in conn.execute(
    """
    SELECT f.id, f.canonical_url, f.storage_key, f.byte_size
    FROM epidemic_p1_12.trusted_fetch_artifacts f
    JOIN epidemic_p1_12.trusted_source_contracts c ON c.id = f.source_contract_id
    WHERE c.source_key = 'cdc_fluview_ilinet_state_weekly'
    ORDER BY f.retrieved_at DESC
    LIMIT 5
    """
):
    print("FETCH", row)
conn.close()
