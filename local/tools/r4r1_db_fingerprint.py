#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg


TABLES: list[tuple[str, str | None]] = [
    ("epidemic.data_sources", None),
    ("epidemic.raw_observations", None),
    ("epidemic.normalized_observations", None),
    ("epidemic.trusted_observations", None),
    ("epidemic.dataset_snapshots", None),
    ("epidemic.dataset_snapshot_observations", None),
    ("epidemic.forecast_runs", None),
    ("epidemic.forecast_values", None),
    ("epidemic.active_forecast_runs", None),
    ("epidemic_kg.kg_claims", None),
    ("epidemic_kg.kg_pending_claim_candidates", None),
    ("epidemic_kg.kg_document_evidence_status", None),
    ("epidemic_kg.kg_identity_quarantine", None),
]

FILTERS: list[tuple[str, str, str]] = [
    (
        "kg_claims_recommendation_eligible_true",
        "epidemic_kg.kg_claims",
        "coalesce(evidence_eligible,false)=true and coalesce(quarantine,false)=false",
    ),
    (
        "kg_pending_candidates_recommendation_eligible_true",
        "epidemic_kg.kg_pending_claim_candidates",
        "coalesce(recommendation_eligible,false)=true",
    ),
    (
        "paper1_participating_claims",
        "epidemic_kg.kg_claims",
        "coalesce(properties->>'paper1_participation','0')='1'",
    ),
]


def connect(dbname: str) -> psycopg.Connection:
    host = os.environ.get("PGHOST", "USER_HOME/projects/openclaw/runtime/postgres-run")
    user = os.environ.get("PGUSER", "postgres")
    port = os.environ.get("PGPORT", "55432")
    return psycopg.connect(f"dbname={dbname} user={user} host={host} port={port}")


def table_exists(cur: psycopg.Cursor, full_name: str) -> bool:
    schema, table = full_name.split(".", 1)
    cur.execute(
        """
        select 1
        from information_schema.tables
        where table_schema=%s and table_name=%s
        """,
        (schema, table),
    )
    return cur.fetchone() is not None


def hash_query(cur: psycopg.Cursor, full_name: str, where: str | None = None) -> dict[str, Any]:
    if not table_exists(cur, full_name):
        return {"exists": False, "count": None, "sha256": None}
    where_sql = f" where {where}" if where else ""
    sql = f"select to_jsonb(t)::text from {full_name} t{where_sql} order by to_jsonb(t)::text"
    digest = hashlib.sha256()
    count = 0
    cur.execute(sql)
    for (payload,) in cur:
        count += 1
        digest.update(payload.encode("utf-8", "surrogatepass"))
        digest.update(b"\n")
    return {"exists": True, "count": count, "sha256": digest.hexdigest()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dbname", required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    payload: dict[str, Any] = {
        "label": args.label,
        "dbname": args.dbname,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "transaction": {
            "isolation": "REPEATABLE READ",
            "read_only": True,
            "writes": 0,
        },
        "tables": {},
        "filtered_sets": {},
    }
    with connect(args.dbname) as conn:
        with conn.cursor() as cur:
            cur.execute("begin transaction isolation level repeatable read read only")
            cur.execute("set local statement_timeout = '30s'")
            cur.execute("show transaction_read_only")
            payload["transaction"]["show_transaction_read_only"] = cur.fetchone()[0]
            for table_name, where in TABLES:
                payload["tables"][table_name] = hash_query(cur, table_name, where)
            for name, table_name, where in FILTERS:
                payload["filtered_sets"][name] = hash_query(cur, table_name, where)
            cur.execute("rollback")

    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"status": "OK", "out": str(out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
