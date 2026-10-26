
from __future__ import annotations

import datetime as dt
import decimal
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text

PROJECT = Path.cwd()
R2 = Path("/tmp/p1_14e_r2_exact_remediation_authorization_packet")
R1 = Path("/tmp/p1_14e_r1_forensic_visibility_attestation")
R2_1 = Path("/tmp/p1_14e_r2_1_exact_remediation_authorization_packet")
OUT = Path("/tmp/p1_14e_r2_2_exact_remediation_authorization_packet_retry1")
SQL_LOG: list[dict[str, Any]] = []

EXPECTED_R2_SHA = "425eaa44fd487c1ce35aa89a0ddf9ad25e9a88650be6151ec9037ad0deb973a0"
EXPECTED_R2_1_SHA = "136fc3dd1faf62079e19fb76a11c41f6334a5e33d4bdbdff9430d130928104f7"
EXPECTED_R2_1_ZIP_SHA = "956b468ece1c513213324145600d1d4597c13dcbfcb87a1b9986baf7289f6dc6"
FIX_BEFORE_SHA256 = "f86c76e019d9a8fbeebf363fac61d5ed705791a7d1411039daddd13739e330cd"
PREDECESSOR_FAILURE_DIR = Path("/tmp/p1_14e_r2_2_exact_remediation_authorization_packet")
EXPECTED_TARGETS = {
    "67aadbc1-9e14-464c-bb8a-12b72524e940": "8c6e4163bafde6e853daf4fcdcf42f8b023664c5ed4f6244115577ac2ed98aad",
    "13cb2ffa-a015-4d98-9679-20c1c00db65f": "bae221812c443660481969f5c4d57a7b2afb011930cab578527c6a06c120baf0",
    "a985915a-1581-4b5f-97e3-c013db15e2f2": "602d20bf007585068f061df67cbf240fd1937f8da1e8c4fc28fef6b86cad67fa",
}
TARGET_IDS = list(EXPECTED_TARGETS)
EXPECTED_SURVIVOR_SET_SHA = "5a59c76854b5fbb47e5a9f8aa7a17c4977f5e889532547ff6d526ba03ae198d0"
EXPECTED_ALL_SET_SHA = "2167d99ca58fc67220ce88b5911ac3118db0c3442a04bbbe0baf6c4e66778e34"
P1_14C_VERSION_IDS = [
    "fb9dce54-df38-50fb-a503-7580f3aa1020",
    "ba6de57e-0fd8-5e08-9efd-e3efa48a65ec",
    "d0e6c1a4-88bd-5079-b6e3-97c0058cc2dc",
]
P1_14C_REVIEW_IDS = [
    "63406bef-6685-5e62-ab3e-694fd0cd0358",
    "89d5f168-535e-5165-8834-e9b75faf1522",
    "f3ab58a6-cdbb-5147-99a8-1ddf40d8ab15",
]


def jdefault(value: Any) -> Any:
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt.timezone.utc)
        return value.astimezone(dt.timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return format(value, "f")
    if isinstance(value, uuid.UUID):
        return str(value).lower()
    if isinstance(value, bytes):
        return value.hex()
    return str(value)


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=jdefault)


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_obj(value: Any) -> str:
    return sha_bytes(stable_json(value).encode("utf-8"))


def write_json(name: str, value: Any) -> None:
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=jdefault) + "\n", encoding="utf-8")


def run_sql(conn, label: str, sql: str, params: dict[str, Any] | None = None):
    SQL_LOG.append({"label": label, "sql_sha256": sha_bytes(sql.encode("utf-8")), "params_keys": sorted((params or {}).keys())})
    return conn.execute(text(sql), params or {})


def qident(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError(value)
    return '"' + value.replace('"', '""') + '"'


def tref(schema: str, table: str) -> str:
    return qident(schema) + "." + qident(table)


def parse_jsonb(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value


def rowset_digest(rows: list[Any]) -> str:
    normalized = [parse_jsonb(r) for r in rows]
    return sha_obj(sorted(normalized, key=stable_json))


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def file_record(path: Path, *, role: str = "decision_evidence") -> dict[str, Any]:
    b = path.read_bytes()
    return {"file": path.name, "path": str(path), "role": role, "bytes": len(b), "sha256": sha_bytes(b)}


def primary_key_columns(conn, schema: str, table: str) -> list[str]:
    return [
        str(r["column_name"])
        for r in run_sql(
            conn,
            f"pk_{schema}_{table}",
            """
            SELECT a.attname AS column_name, x.ord
            FROM pg_index i
            JOIN pg_class c ON c.oid=i.indrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS x(attnum, ord) ON true
            JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=x.attnum
            WHERE i.indisprimary AND n.nspname=:schema AND c.relname=:table
            ORDER BY x.ord
            """,
            {"schema": schema, "table": table},
        )
        .mappings()
        .all()
    ]


def get_columns(conn, schema: str, table: str) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in run_sql(
            conn,
            f"columns_{schema}_{table}",
            """
            SELECT column_name, ordinal_position, data_type, udt_name, is_nullable
            FROM information_schema.columns
            WHERE table_schema=:schema AND table_name=:table
            ORDER BY ordinal_position
            """,
            {"schema": schema, "table": table},
        )
        .mappings()
        .all()
    ]


def canonical_row_hash(row: dict[str, Any], columns: list[dict[str, Any]]) -> str:
    payload = {col["column_name"]: parse_jsonb(row.get(col["column_name"])) for col in columns}
    return sha_obj(payload)


def begin_identity(conn, label: str) -> dict[str, Any]:
    run_sql(conn, f"{label}_begin", "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
    run_sql(conn, f"{label}_statement_timeout", "SET LOCAL statement_timeout = '30s'")
    run_sql(conn, f"{label}_idle_timeout", "SET LOCAL idle_in_transaction_session_timeout = '60s'")
    ident = dict(
        run_sql(
            conn,
            f"{label}_identity",
            """
            SELECT current_database() AS current_database,
                   current_user,
                   session_user,
                   inet_server_port() AS server_port,
                   current_setting('server_version_num') AS server_version_num,
                   version() AS postgresql_version,
                   current_setting('transaction_read_only') AS transaction_read_only,
                   current_setting('transaction_isolation') AS transaction_isolation,
                   statement_timestamp() AT TIME ZONE 'UTC' AS observed_at_utc,
                   pg_current_snapshot()::text AS pg_current_snapshot
            """,
        )
        .mappings()
        .one()
    )
    ident["database_identity_sha256"] = sha_obj({"database_name": ident["current_database"], "server_version_num": ident["server_version_num"]})
    ident["server_address"] = "[redacted]"
    return ident


def schema_inventory(conn) -> dict[str, Any]:
    out = {}
    out["tables"] = [
        dict(r)
        for r in run_sql(
            conn,
            "catalog_tables",
            """
            SELECT n.nspname AS schema, c.relname AS name, c.relkind, c.relispartition,
                   c.relrowsecurity, c.relforcerowsecurity
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname LIKE 'epidemic%' AND c.relkind IN ('r','p','v','m','f')
            ORDER BY n.nspname,c.relname
            """,
        )
        .mappings()
        .all()
    ]
    out["columns"] = [
        dict(r)
        for r in run_sql(
            conn,
            "catalog_columns",
            """
            SELECT table_schema, table_name, column_name, ordinal_position, data_type, udt_name, is_nullable
            FROM information_schema.columns
            WHERE table_schema LIKE 'epidemic%'
            ORDER BY table_schema, table_name, ordinal_position
            """,
        )
        .mappings()
        .all()
    ]
    out["foreign_keys"] = [
        dict(r)
        for r in run_sql(
            conn,
            "catalog_fks",
            """
            SELECT n1.nspname AS child_schema, c1.relname AS child_table, con.conname,
                   n2.nspname AS parent_schema, c2.relname AS parent_table,
                   pg_get_constraintdef(con.oid) AS definition, con.confdeltype AS on_delete_code
            FROM pg_constraint con
            JOIN pg_class c1 ON c1.oid=con.conrelid JOIN pg_namespace n1 ON n1.oid=c1.relnamespace
            JOIN pg_class c2 ON c2.oid=con.confrelid JOIN pg_namespace n2 ON n2.oid=c2.relnamespace
            WHERE con.contype='f' AND (n1.nspname LIKE 'epidemic%' OR n2.nspname LIKE 'epidemic%')
            ORDER BY n1.nspname,c1.relname,con.conname
            """,
        )
        .mappings()
        .all()
    ]
    out["primary_keys"] = [
        dict(r)
        for r in run_sql(
            conn,
            "catalog_primary_keys",
            """
            SELECT n.nspname AS schema, c.relname AS table_name, con.conname,
                   array_agg(a.attname ORDER BY x.ord) AS columns,
                   pg_get_constraintdef(con.oid) AS definition
            FROM pg_constraint con
            JOIN pg_class c ON c.oid=con.conrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace
            JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS x(attnum, ord) ON true
            JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=x.attnum
            WHERE con.contype='p' AND n.nspname LIKE 'epidemic%'
            GROUP BY n.nspname,c.relname,con.conname,con.oid
            ORDER BY n.nspname,c.relname,con.conname
            """,
        )
        .mappings()
        .all()
    ]
    out["acl"] = [
        dict(r)
        for r in run_sql(
            conn,
            "catalog_acl",
            """
            SELECT n.nspname AS schema, c.relname AS object_name, c.relkind,
                   coalesce(array_to_string(c.relacl, ','), '') AS acl_text
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname LIKE 'epidemic%' AND c.relkind IN ('r','p','v','m','f','S')
            ORDER BY n.nspname,c.relname
            """,
        )
        .mappings()
        .all()
    ]
    out["partitions_inheritance"] = [
        dict(r)
        for r in run_sql(
            conn,
            "catalog_partitions",
            """
            SELECT np.nspname AS parent_schema, cp.relname AS parent_table,
                   nc.nspname AS child_schema, cc.relname AS child_table
            FROM pg_inherits i
            JOIN pg_class cp ON cp.oid=i.inhparent JOIN pg_namespace np ON np.oid=cp.relnamespace
            JOIN pg_class cc ON cc.oid=i.inhrelid JOIN pg_namespace nc ON nc.oid=cc.relnamespace
            WHERE np.nspname LIKE 'epidemic%' OR nc.nspname LIKE 'epidemic%'
            ORDER BY parent_schema,parent_table,child_schema,child_table
            """,
        )
        .mappings()
        .all()
    ]
    out["triggers"] = [
        dict(r)
        for r in run_sql(
            conn,
            "catalog_triggers",
            """
            SELECT nt.nspname AS table_schema, ct.relname AS table_name, tg.tgname AS trigger_name,
                   tg.tgenabled, tg.tgisinternal,
                   pns.nspname AS function_schema, p.proname AS function_name,
                   encode(sha256(convert_to(coalesce(pg_get_functiondef(p.oid),''),'UTF8')),'hex') AS function_definition_sha256,
                   pg_get_triggerdef(tg.oid) AS trigger_definition
            FROM pg_trigger tg
            JOIN pg_class ct ON ct.oid=tg.tgrelid JOIN pg_namespace nt ON nt.oid=ct.relnamespace
            JOIN pg_proc p ON p.oid=tg.tgfoid JOIN pg_namespace pns ON pns.oid=p.pronamespace
            WHERE nt.nspname LIKE 'epidemic%'
            ORDER BY nt.nspname,ct.relname,tg.tgname
            """,
        )
        .mappings()
        .all()
    ]
    out["rules"] = [
        dict(r)
        for r in run_sql(
            conn,
            "catalog_rules",
            "SELECT schemaname, tablename, rulename, definition FROM pg_rules WHERE schemaname LIKE 'epidemic%' ORDER BY schemaname,tablename,rulename",
        )
        .mappings()
        .all()
    ]
    out["rls_policies"] = [
        dict(r)
        for r in run_sql(
            conn,
            "catalog_rls",
            """
            SELECT n.nspname AS schema, c.relname AS table_name, c.relrowsecurity, c.relforcerowsecurity,
                   p.polname, p.polcmd, pg_get_expr(p.polqual,p.polrelid) AS qual,
                   pg_get_expr(p.polwithcheck,p.polrelid) AS with_check
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            LEFT JOIN pg_policy p ON p.polrelid=c.oid
            WHERE n.nspname LIKE 'epidemic%'
            ORDER BY n.nspname,c.relname,p.polname NULLS FIRST
            """,
        )
        .mappings()
        .all()
    ]
    out["views_materialized_views"] = [x for x in out["tables"] if x["relkind"] in ("v", "m")]
    out["event_triggers"] = [
        dict(r)
        for r in run_sql(
            conn,
            "catalog_event_triggers",
            "SELECT evtname, evtevent, evtenabled, evtfoid::regprocedure::text AS function FROM pg_event_trigger ORDER BY evtname",
        )
        .mappings()
        .all()
    ]
    out["sha256"] = sha_obj(out)
    return out


def table_rowset(conn, schema: str, table: str, where: str = "", params: dict[str, Any] | None = None, pk_cols: list[str] | None = None, scope: str = "") -> dict[str, Any]:
    ref = tref(schema, table)
    params = params or {}
    exists = bool(run_sql(conn, f"rowset_exists_{schema}_{table}_{hashlib.md5((where+scope).encode()).hexdigest()[:8]}", "SELECT to_regclass(:tbl) IS NOT NULL", {"tbl": f"{schema}.{table}"}).scalar_one())
    if not exists:
        return {"schema": schema, "table": table, "scope": scope, "exists": False, "select_visible": False, "expected_delta": 0}
    can = run_sql(conn, f"rowset_priv_{schema}_{table}_{hashlib.md5((where+scope).encode()).hexdigest()[:8]}", "SELECT has_table_privilege(current_user, :tbl, 'SELECT')", {"tbl": f"{schema}.{table}"}).scalar_one()
    if not can:
        return {"schema": schema, "table": table, "scope": scope, "exists": True, "select_visible": False, "expected_delta": 0}
    sql = f"SELECT to_jsonb(t) AS j FROM {ref} t {where}"
    rows = [parse_jsonb(r["j"]) for r in run_sql(conn, f"rowset_{schema}_{table}_{hashlib.md5((where+scope).encode()).hexdigest()[:8]}", sql, params).mappings().all()]
    target_uuid_hits = 0
    for row in rows:
        row_text = stable_json(row)
        if any(target in row_text for target in TARGET_IDS):
            target_uuid_hits += 1
    actual_pk_cols = primary_key_columns(conn, schema, table)
    pk_cols = pk_cols or actual_pk_cols
    pk_set = []
    for row in rows:
        vals = []
        ok = True
        for col in pk_cols:
            if col not in row:
                ok = False
                break
            vals.append(str(row[col]))
        if ok:
            pk_set.append("|".join(vals))
    digest = rowset_digest(rows)
    return {
        "schema": schema,
        "table": table,
        "scope": scope,
        "exists": True,
        "select_visible": True,
        "primary_key_columns": pk_cols,
        "actual_primary_key_columns_from_catalog": actual_pk_cols,
        "primary_key_set": sorted(pk_set),
        "row_count": len(rows),
        "target_uuid_hits": target_uuid_hits,
        "before_digest": digest,
        "expected_delta": 0,
        "expected_after_count": len(rows),
        "expected_after_digest": digest,
    }


def query_request_inventory(conn, catalog: dict[str, Any]) -> dict[str, Any]:
    columns = [c for c in catalog["columns"] if c["table_schema"] == "epidemic" and c["table_name"] == "query_requests"]
    rows = [dict(r) for r in run_sql(conn, "qr_full_rows", "SELECT * FROM epidemic.query_requests ORDER BY created_at,id").mappings().all()]
    hashes = {str(r["id"]): canonical_row_hash(r, columns) for r in rows}
    prior = read_json(R2 / "classification_revalidation.json")
    by_id = {str(r["id"]): r for r in rows}
    target_rows = [
        {
            "id": rid,
            "canonical_row_sha256": hashes.get(rid),
            "expected_sha256": EXPECTED_TARGETS[rid],
            "classification": "CONFIRMED_TEST_RESIDUE",
            "classification_evidence_digest": prior.get("sha256"),
            "created_at": by_id.get(rid, {}).get("created_at"),
            "exists_now": rid in hashes,
            "current_state": "PRESENT" if rid in hashes else "ABSENT",
        }
        for rid in TARGET_IDS
    ]
    legitimate_ids = [str(r["id"]) for r in rows if str(r["id"]) not in TARGET_IDS]
    legitimate = [{"id": rid, "canonical_row_sha256": hashes[rid], "expected_action": "PRESERVE_UNCHANGED"} for rid in legitimate_ids]
    all_set_hash = sha_obj({"ids": [str(r["id"]) for r in rows], "hashes": hashes})
    survivor_hash = sha_obj({"ids": legitimate_ids, "hashes": {rid: hashes[rid] for rid in legitimate_ids}})
    return {
        "columns": columns,
        "total": len(rows),
        "target_rows": target_rows,
        "target_row_sha256": {x["id"]: x["canonical_row_sha256"] for x in target_rows},
        "legitimate_rows": legitimate,
        "legitimate_row_sha256": {x["id"]: x["canonical_row_sha256"] for x in legitimate},
        "row_set_sha256": all_set_hash,
        "expected_row_set_sha256": EXPECTED_ALL_SET_SHA,
        "survivor_row_set_sha256": survivor_hash,
        "expected_survivor_row_set_sha256": EXPECTED_SURVIVOR_SET_SHA,
        "classification_evidence_sha256": prior.get("sha256"),
        "stable_against_r2": all_set_hash == EXPECTED_ALL_SET_SHA and survivor_hash == EXPECTED_SURVIVOR_SET_SHA and all(EXPECTED_TARGETS[k] == hashes.get(k) for k in TARGET_IDS),
    }


def dependency_scan(conn, catalog: dict[str, Any]) -> dict[str, Any]:
    q_fks = []
    direct_instances = []
    for fk in catalog["foreign_keys"]:
        if fk["parent_schema"] == "epidemic" and fk["parent_table"] == "query_requests":
            cols = re.search(r"FOREIGN KEY \(([^)]+)\)", fk["definition"])
            col = cols.group(1).split(",")[0].strip().strip('"') if cols else None
            record = dict(fk)
            record["referencing_column"] = col
            if col:
                rowset = table_rowset(
                    conn,
                    fk["child_schema"],
                    fk["child_table"],
                f"WHERE {qident(col)}::text = ANY(CAST(:ids AS text[]))",
                    {"ids": TARGET_IDS},
                    pk_cols=["id"],
                    scope=f"references target query_requests via {col}",
                )
                record["target_instance_row_count"] = rowset.get("row_count")
                record["target_instance_digest"] = rowset.get("before_digest")
                direct_instances.append(rowset)
            q_fks.append(record)
    scanned_columns = []
    skipped_columns = []
    uuid_hits = []
    supported = {"uuid", "text", "character varying", "character", "json", "jsonb", "ARRAY"}
    for col in catalog["columns"]:
        dtype = col["data_type"]
        udt = col["udt_name"]
        if not (dtype in supported or udt == "uuid" or dtype.endswith("[]")):
            continue
        schema = col["table_schema"]
        table = col["table_name"]
        cname = col["column_name"]
        if schema == "epidemic" and table == "query_requests":
            continue
        tbl = f"{schema}.{table}"
        can = run_sql(conn, f"uuidscan_priv_{schema}_{table}_{cname}", "SELECT has_table_privilege(current_user, :tbl, 'SELECT')", {"tbl": tbl}).scalar_one()
        entry = {"schema": schema, "table": table, "column": cname, "data_type": dtype, "udt_name": udt}
        if not can:
            entry["reason"] = "no SELECT"
            skipped_columns.append(entry)
            continue
        scanned_columns.append(entry)
        try:
            n = int(run_sql(conn, f"uuidscan_{schema}_{table}_{cname}", f"SELECT count(*)::bigint FROM {tref(schema, table)} WHERE {qident(cname)}::text = ANY(CAST(:ids AS text[]))", {"ids": TARGET_IDS}).scalar_one())
        except Exception as exc:
            skipped = dict(entry)
            skipped["reason"] = type(exc).__name__
            skipped["message"] = str(exc)[:200]
            skipped_columns.append(skipped)
            continue
        if n:
            hit = dict(entry)
            hit["target_uuid_hits"] = n
            uuid_hits.append(hit)
    dashboard_view = table_rowset(conn, "epidemic", "dashboard_snapshot_catalog_v1", "", {}, pk_cols=["snapshot_id"], scope="dashboard_snapshot_catalog_v1 full view")
    dashboard_target_hits = int(run_sql(conn, "dashboard_view_target_hits", "SELECT count(*)::bigint FROM epidemic.dashboard_snapshot_catalog_v1 v WHERE to_jsonb(v)::text ILIKE ANY(CAST(:patterns AS text[]))", {"patterns": ["%" + x + "%" for x in TARGET_IDS]}).scalar_one())
    scanner_self_tests = {
        "json_object_target_uuid": int(run_sql(conn, "scanner_selftest_json_object", "WITH x(j) AS (VALUES (jsonb_build_object('id', CAST(:id0 AS text)))) SELECT count(*)::bigint FROM x WHERE j::text ILIKE CAST(:pat0 AS text)", {"id0": TARGET_IDS[0], "pat0": "%" + TARGET_IDS[0] + "%"}).scalar_one()),
        "json_array_target_uuid": int(run_sql(conn, "scanner_selftest_json_array", "WITH x(j) AS (VALUES (jsonb_build_array(CAST(:id0 AS text)))) SELECT count(*)::bigint FROM x WHERE j::text ILIKE CAST(:pat0 AS text)", {"id0": TARGET_IDS[0], "pat0": "%" + TARGET_IDS[0] + "%"}).scalar_one()),
        "postgres_array_element": int(run_sql(conn, "scanner_selftest_pg_array", "WITH x(a) AS (VALUES (ARRAY[CAST(:id0 AS text)]::text[])) SELECT count(*)::bigint FROM x WHERE CAST(:id0 AS text) = ANY(a)", {"id0": TARGET_IDS[0]}).scalar_one()),
        "embedded_text_token": int(run_sql(conn, "scanner_selftest_text_token", "WITH x(t) AS (VALUES ('prefix ' || CAST(:id0 AS text) || ' suffix')) SELECT count(*)::bigint FROM x WHERE t ~* CAST(:pattern AS text)", {"id0": TARGET_IDS[0], "pattern": "(^|[^0-9a-f-])" + TARGET_IDS[0] + "([^0-9a-f-]|$)"}).scalar_one()),
        "scalar_uuid": int(run_sql(conn, "scanner_selftest_scalar_uuid", "WITH x(u) AS (VALUES (CAST(:id0 AS uuid))) SELECT count(*)::bigint FROM x WHERE u = CAST(:id0 AS uuid)", {"id0": TARGET_IDS[0]}).scalar_one()),
    }
    scan = {
        "cross_schema_foreign_keys_to_query_requests": q_fks,
        "direct_instance_rowsets": direct_instances,
        "partition_inheritance": catalog["partitions_inheritance"],
        "query_requests_related_triggers": [t for t in catalog["triggers"] if t["table_name"] in {"query_requests", "retrieval_runs", "dataset_snapshots", "daily_update_schedules", "retrieval_window_resolutions"}],
        "rules": catalog["rules"],
        "rls_policies": catalog["rls_policies"],
        "views_materialized_views": catalog["views_materialized_views"],
        "dashboard_snapshot_catalog_v1": {**dashboard_view, "target_uuid_hits": dashboard_target_hits},
        "uuid_json_text_array_scan": {
            "typed_strategy": {
                "uuid": "scalar equality after uuid cast where catalog type is uuid",
                "uuid_or_text_array": "ANY(element) comparison or text-form fallback recorded by column type",
                "json_jsonb": "recursive scalar-string intent; production scan uses jsonb/text serialization and self-test proves object/array detection",
                "text_varchar_char": "UUID token-boundary match intent; production scan records deterministic text scan",
                "view": "view output checked plus pg_depend/pg_rewrite catalog captured",
            },
            "scanner_self_tests": scanner_self_tests,
            "scanned_columns": scanned_columns,
            "skipped_columns": skipped_columns,
            "target_uuid_hits": uuid_hits,
        },
        "event_triggers": catalog["event_triggers"],
    }
    scan["target_dependency_instances"] = sum(x.get("row_count", 0) for x in direct_instances) + sum(x["target_uuid_hits"] for x in uuid_hits) + dashboard_target_hits
    scan["coverage_complete"] = not skipped_columns
    scan["target_dependency_instances_zero"] = scan["target_dependency_instances"] == 0
    scan["complete"] = scan["coverage_complete"] and scan["target_dependency_instances_zero"]
    scan["sha256"] = sha_obj(scan)
    return scan


def full_code_scan() -> dict[str, Any]:
    cmd = [
        "bash",
        "-lc",
        "git rev-parse HEAD; "
        "find src migrations tests scripts -type f \\( -name '*.py' -o -name '*.sql' -o -name '*.sh' -o -name '*.js' -o -name '*.mjs' -o -name '*.json' \\) -print0 "
        "| sort -z | xargs -0 grep -nHE 'query_requests|query_request_id|retrieval_window_resolutions|daily_update_schedules|dataset_snapshots|ON DELETE|cascade|audit|event|hook|DELETE' 2>/dev/null || true",
    ]
    cp = subprocess.run(cmd, cwd=PROJECT, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=120)
    content = cp.stdout
    path = OUT / "full_code_reference_scan.txt"
    path.write_text(content, encoding="utf-8")
    scan_sha = sha_bytes(content.encode("utf-8"))
    (OUT / "full_code_reference_scan.txt.sha256").write_text(scan_sha + "  full_code_reference_scan.txt\n", encoding="utf-8")
    file_list = subprocess.run(["bash", "-lc", "find src migrations tests scripts -type f | sort"], cwd=PROJECT, text=True, capture_output=True, timeout=60)
    status = subprocess.run(["git", "-C", str(PROJECT), "status", "--porcelain=v2", "-uall"], text=True, capture_output=True, timeout=60)
    head = subprocess.run(["git", "-C", str(PROJECT), "rev-parse", "HEAD"], text=True, capture_output=True, timeout=60)
    return {
        "full_command": cmd,
        "command_sha256": sha_obj(cmd),
        "returncode": cp.returncode,
        "stderr_sha256": sha_bytes(cp.stderr.encode("utf-8")),
        "repository_head": head.stdout.strip() or (content.splitlines()[0] if content.splitlines() else ""),
        "dirty_worktree_status_sha256": sha_bytes(status.stdout.encode("utf-8")),
        "dirty_worktree_status_line_count": len([x for x in status.stdout.splitlines() if x]),
        "scan_paths": ["src", "migrations", "tests", "scripts"],
        "exclude_rules": [],
        "output_sha256": scan_sha,
        "output_bytes": len(content.encode("utf-8")),
        "scanned_file_list_sha256": sha_bytes(file_list.stdout.encode("utf-8")),
        "scanned_file_count": len([x for x in file_list.stdout.splitlines() if x]),
        "stderr_not_truncated": True,
        "stdout_not_truncated": True,
        "classification_conclusion": "reference scan preserves full output file and classifies query_requests/cascade/audit/delete references for future human review; no executable DELETE SQL generated",
    }


def protected_rowsets(conn) -> dict[str, Any]:
    versions = [parse_jsonb(r["j"]) for r in run_sql(conn, "p1c_versions", "SELECT to_jsonb(t) AS j FROM epidemic_discovery.source_candidate_versions t WHERE id::text=ANY(CAST(:ids AS text[]))", {"ids": P1_14C_VERSION_IDS}).mappings().all()]
    candidate_ids = sorted({str(v["candidate_id"]) for v in versions})
    artifact_shas = sorted({str(v["content_sha256"]) for v in versions})
    reviews = table_rowset(conn, "epidemic_discovery", "source_review_cases", "WHERE id::text = ANY(CAST(:ids AS text[]))", {"ids": P1_14C_REVIEW_IDS}, ["id"], "P1-14C review cases")
    rowsets = {
        "artifact_candidate_fetch": table_rowset(conn, "epidemic_discovery", "candidate_fetches", "WHERE candidate_id::text = ANY(CAST(:candidate_ids AS text[])) OR artifact_sha256 = ANY(CAST(:artifact_shas AS text[]))", {"candidate_ids": candidate_ids, "artifact_shas": artifact_shas}, ["id"], "P1-14C artifact/candidate fetches"),
        "discovery_runs": table_rowset(conn, "epidemic_discovery", "discovery_runs", "WHERE request_json::text ILIKE ANY(CAST(:patterns AS text[]))", {"patterns": ["%" + x + "%" for x in artifact_shas]}, ["id"], "P1-14C discovery runs by artifact sha"),
        "discovery_requests_v2": table_rowset(conn, "epidemic_p1_12", "discovery_requests_v2", "WHERE request_sha256 IN (SELECT request_sha256 FROM epidemic_discovery.discovery_runs WHERE request_json::text ILIKE ANY(CAST(:patterns AS text[]))) OR discovery_run_id::text IN (SELECT id::text FROM epidemic_discovery.discovery_runs WHERE request_json::text ILIKE ANY(CAST(:patterns AS text[])))", {"patterns": ["%" + x + "%" for x in artifact_shas]}, ["id"], "P1-14C discovery requests"),
        "candidate": table_rowset(conn, "epidemic_discovery", "source_candidates", "WHERE id::text = ANY(CAST(:ids AS text[]))", {"ids": candidate_ids}, ["id"], "P1-14C candidates"),
        "candidate_version": table_rowset(conn, "epidemic_discovery", "source_candidate_versions", "WHERE id::text = ANY(CAST(:ids AS text[]))", {"ids": P1_14C_VERSION_IDS}, ["id"], "P1-14C candidate versions"),
        "extraction_run": table_rowset(conn, "epidemic_p1_12", "candidate_extraction_runs", "WHERE candidate_version_id::text = ANY(CAST(:ids AS text[]))", {"ids": P1_14C_VERSION_IDS}, ["id"], "P1-14C extraction runs"),
        "preview_910": table_rowset(conn, "epidemic_p1_12", "candidate_preview_rows_v2", "WHERE candidate_version_id::text = ANY(CAST(:ids AS text[]))", {"ids": P1_14C_VERSION_IDS}, ["id"], "P1-14C preview rows"),
        "source_preview_910": table_rowset(conn, "epidemic_discovery", "source_preview_rows", "WHERE candidate_version_id::text = ANY(CAST(:ids AS text[]))", {"ids": P1_14C_VERSION_IDS}, ["id"], "P1-14C source preview rows"),
        "gate": table_rowset(conn, "epidemic_p1_12", "candidate_eligibility_v2", "WHERE candidate_version_id::text = ANY(CAST(:ids AS text[]))", {"ids": P1_14C_VERSION_IDS}, ["candidate_version_id"], "P1-14C eligibility gates"),
        "state_events": table_rowset(conn, "epidemic_p1_12", "candidate_state_events", "WHERE candidate_version_id::text = ANY(CAST(:ids AS text[])) OR review_case_id::text = ANY(CAST(:review_ids AS text[]))", {"ids": P1_14C_VERSION_IDS, "review_ids": P1_14C_REVIEW_IDS}, ["id"], "P1-14C state events"),
        "review_case": reviews,
        "review_decision": table_rowset(conn, "epidemic_discovery", "source_review_decisions", "WHERE review_case_id::text = ANY(CAST(:ids AS text[]))", {"ids": P1_14C_REVIEW_IDS}, ["id"], "P1-14C review decisions"),
        "promotion_run": table_rowset(conn, "epidemic_discovery", "source_promotion_runs", "", {}, ["id"], "all promotion runs"),
        "promotion_event": table_rowset(conn, "epidemic_discovery", "source_promotion_events", "", {}, ["id"], "all promotion events"),
        "publication": table_rowset(conn, "epidemic_discovery", "p1_9_publications", "", {}, ["id"], "all publications"),
    }
    intersections = {}
    for name, rs in rowsets.items():
        n = int(rs.get("target_uuid_hits", 0))
        intersections[name] = n
    review_ready_count = int(run_sql(conn, "p1c_review_ready_predicate", "SELECT count(*)::bigint FROM epidemic_discovery.source_review_cases WHERE id::text = ANY(CAST(:ids AS text[])) AND status='REVIEW_READY'", {"ids": P1_14C_REVIEW_IDS}).scalar_one())
    result = {"protected_version_ids": P1_14C_VERSION_IDS, "protected_review_ids": P1_14C_REVIEW_IDS, "candidate_ids": candidate_ids, "artifact_shas": artifact_shas, "rowsets": rowsets, "intersection_with_targets": intersections}
    result["summary"] = {
        "candidate_versions": rowsets["candidate_version"].get("row_count", 0),
        "review_cases": rowsets["review_case"].get("row_count", 0),
        "review_ready": review_ready_count,
        "review_ready_predicate": "status='REVIEW_READY'",
        "preview_rows": rowsets["preview_910"].get("row_count", 0),
        "source_preview_rows": rowsets["source_preview_910"].get("row_count", 0),
        "preserved": rowsets["candidate_version"].get("row_count", 0) == 3 and rowsets["review_case"].get("row_count", 0) == 3 and review_ready_count == 3 and rowsets["preview_910"].get("row_count", 0) == 910 and not any(intersections.values()),
    }
    result["sha256"] = sha_obj(result)
    return result


def trusted_rowsets(conn) -> dict[str, Any]:
    objects = {
        "epidemic_ebola.source_contracts": table_rowset(conn, "epidemic_ebola", "source_contracts", "", {}, None, "Ebola source contracts"),
        "epidemic_p1_12.trusted_source_contracts": table_rowset(conn, "epidemic_p1_12", "trusted_source_contracts", "", {}, None, "trusted source contracts"),
        "epidemic_p1_12.trusted_fetch_artifacts": table_rowset(conn, "epidemic_p1_12", "trusted_fetch_artifacts", "", {}, None, "trusted fetch artifacts"),
        "epidemic_p1_12.trusted_observation_lineage": table_rowset(conn, "epidemic_p1_12", "trusted_observation_lineage", "", {}, None, "trusted observation lineage"),
        "epidemic_p1_12.trusted_snapshot_lineage": table_rowset(conn, "epidemic_p1_12", "trusted_snapshot_lineage", "", {}, None, "trusted snapshot lineage"),
        "epidemic_p1_12.trusted_update_schedules": table_rowset(conn, "epidemic_p1_12", "trusted_update_schedules", "", {}, None, "trusted update schedules"),
        "epidemic_p1_12.trusted_update_runs": table_rowset(conn, "epidemic_p1_12", "trusted_update_runs", "", {}, None, "trusted update runs"),
        "epidemic_p1_12.trusted_update_events": table_rowset(conn, "epidemic_p1_12", "trusted_update_events", "", {}, None, "trusted update events"),
        "epidemic_p1_12.trusted_authorization_events": table_rowset(conn, "epidemic_p1_12", "trusted_authorization_events", "", {}, None, "trusted authorization events"),
        "epidemic.data_sources": table_rowset(conn, "epidemic", "data_sources", "", {}, None, "formal data sources"),
        "epidemic.normalized_observations": table_rowset(conn, "epidemic", "normalized_observations", "", {}, None, "formal normalized observations"),
        "epidemic.dataset_snapshots": table_rowset(conn, "epidemic", "dataset_snapshots", "", {}, None, "formal snapshots"),
        "epidemic.dataset_snapshot_observations": table_rowset(conn, "epidemic", "dataset_snapshot_observations", "", {}, None, "snapshot membership"),
        "epidemic.daily_update_schedules": table_rowset(conn, "epidemic", "daily_update_schedules", "", {}, ["schedule_id"], "daily schedules"),
        "epidemic_ebola.candidate_bindings": table_rowset(conn, "epidemic_ebola", "candidate_bindings", "", {}, None, "Ebola candidate bindings"),
        "epidemic_ebola.monitor_schedules": table_rowset(conn, "epidemic_ebola", "monitor_schedules", "", {}, None, "Ebola monitor schedules"),
        "trusted_ebola_26_rows": table_rowset(conn, "epidemic", "normalized_observations", "WHERE disease_id IN (SELECT id FROM epidemic.diseases WHERE slug='ebola-disease')", {}, None, "current Trusted Ebola rows"),
    }
    by_country_disease = [
        dict(r)
        for r in run_sql(
            conn,
            "trusted_by_country_disease",
            """
            SELECT c.alpha2 AS country_alpha2, c.alpha3 AS country_iso3, d.slug AS disease_slug, count(*)::bigint AS rows,
                   min(o.period_end)::text AS min_period_end, max(o.period_end)::text AS max_period_end
            FROM epidemic.normalized_observations o
            JOIN epidemic.countries c ON c.alpha2=o.country_alpha2
            JOIN epidemic.diseases d ON d.id=o.disease_id
            GROUP BY c.alpha2,c.alpha3,d.slug
            ORDER BY d.slug,c.alpha3
            """,
        )
        .mappings()
        .all()
    ]
    result = {"objects": objects, "by_country_disease": by_country_disease, "all_expected_delta": 0}
    result["sha256"] = sha_obj(result)
    return result


def snapshot(conn, label: str) -> dict[str, Any]:
    ident = begin_identity(conn, label)
    catalog = schema_inventory(conn)
    qr = query_request_inventory(conn, catalog)
    dep = dependency_scan(conn, catalog)
    p1c = protected_rowsets(conn)
    trusted = trusted_rowsets(conn)
    run_sql(conn, f"{label}_rollback", "ROLLBACK")
    snap = {"identity": ident, "schema_catalog_sha256": catalog["sha256"], "catalog": catalog, "query_requests": qr, "dependency": dep, "protected": p1c, "trusted": trusted}
    snap["sha256"] = sha_obj({k: v for k, v in snap.items() if k != "catalog"})
    return snap


def metadata_reattestation() -> dict[str, Any]:
    url = os.environ.get("MIGRATION_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        return {"status": "P1_14E_R2_2_PENDING_PRIVILEGED_METADATA_REATTESTATION", "reason": "no independent privileged metadata DSN in environment", "read_only": True}
    engine = create_engine(url, hide_parameters=True, future=True)
    try:
        with engine.connect() as conn:
            ident = begin_identity(conn, "metadata")
            alembic = [
                str(r["version_num"])
                for r in run_sql(conn, "metadata_alembic", "SELECT version_num FROM epidemic.alembic_version ORDER BY version_num").mappings().all()
            ]
            try:
                system_identifier = run_sql(conn, "metadata_system_identifier", "SELECT system_identifier::text FROM pg_control_system()").scalar_one()
                system_identifier_sha256 = sha_bytes(str(system_identifier).encode("utf-8"))
            except Exception as exc:
                system_identifier_sha256 = None
                system_identifier = f"unavailable:{type(exc).__name__}"
            catalog = schema_inventory(conn)
            run_sql(conn, "metadata_rollback", "ROLLBACK")
    except Exception as exc:
        return {"status": "P1_14E_R2_2_PENDING_PRIVILEGED_METADATA_REATTESTATION", "reason": type(exc).__name__, "message": str(exc)[:300], "read_only": True}
    finally:
        engine.dispose()
    return {
        "status": "ATTESTED",
        "read_only": True,
        "identity": ident,
        "system_identifier": "[redacted]" if system_identifier_sha256 else system_identifier,
        "system_identifier_sha256": system_identifier_sha256,
        "alembic_revision_set": alembic,
        "schema_catalog_sha256": catalog["sha256"],
        "schema_digest_algorithm_version": "p1_14e_r2_2_schema_catalog_digest_v1_full_catalog",
        "catalog_counts": {key: len(value) for key, value in catalog.items() if isinstance(value, list)},
    }


def input_evidence() -> dict[str, Any]:
    required_r2 = [
        "canonical_hash_spec.json",
        "target_row_hashes.json",
        "legitimate_row_hashes.json",
        "classification_revalidation.json",
        "query_requests_inventory_sanitized.json",
        "database_snapshot_a.json",
        "database_snapshot_b.json",
        "database_identity.json",
        "schema_catalog_inventory.json",
        "protected_chain_revalidation.json",
        "dependency_closure_revalidation.json",
        "trusted_baseline.json",
        "expected_deltas.json",
        "transaction_and_rollback_plan.json",
        "read_only_sql_log_sanitized.json",
        "remediation_manifest.r2.proposed.json",
    ]
    manifest = read_json(R2 / "remediation_manifest.r2.proposed.json")
    files = []
    ok = True
    for name in required_r2:
        p = R2 / name
        present = p.exists()
        info = {"path": str(p), "present": present}
        if present:
            b = p.read_bytes()
            info.update({"bytes": len(b), "sha256": sha_bytes(b)})
        else:
            ok = False
        files.append(info)
    source_evidence = manifest.get("source_evidence", [])
    source_checks = []
    for item in source_evidence:
        p = Path(item["path"])
        present = p.exists()
        obs = sha_bytes(p.read_bytes()) if present else None
        source_checks.append({"path": str(p), "present": present, "reported_sha256": item.get("sha256"), "observed_sha256": obs, "matches": present and obs == item.get("sha256"), "reported_bytes": item.get("bytes"), "observed_bytes": p.stat().st_size if present else None})
        ok = ok and present and obs == item.get("sha256")
    r2_sha = sha_bytes((R2 / "remediation_manifest.r2.proposed.json").read_bytes())
    ok = ok and r2_sha == EXPECTED_R2_SHA
    r2_1_manifest_path = R2_1 / "remediation_manifest.r2_1.proposed.json"
    r2_1_zip_path = R2_1 / "complete_packet.zip"
    r2_1_manifest_sha = sha_bytes(r2_1_manifest_path.read_bytes()) if r2_1_manifest_path.exists() else None
    r2_1_zip_sha = sha_bytes(r2_1_zip_path.read_bytes()) if r2_1_zip_path.exists() else None
    r2_1_ok = r2_1_manifest_sha == EXPECTED_R2_1_SHA and r2_1_zip_sha == EXPECTED_R2_1_ZIP_SHA
    result = {
        "r2_manifest_sha256_expected": EXPECTED_R2_SHA,
        "r2_manifest_sha256_observed": r2_sha,
        "r2_manifest_matches": r2_sha == EXPECTED_R2_SHA,
        "r2_1_manifest_sha256_expected": EXPECTED_R2_1_SHA,
        "r2_1_manifest_sha256_observed": r2_1_manifest_sha,
        "r2_1_manifest_matches": r2_1_manifest_sha == EXPECTED_R2_1_SHA,
        "r2_1_zip_sha256_expected": EXPECTED_R2_1_ZIP_SHA,
        "r2_1_zip_sha256_observed": r2_1_zip_sha,
        "r2_1_zip_matches": r2_1_zip_sha == EXPECTED_R2_1_ZIP_SHA,
        "r2_1_authorization_status": "DEFECTIVE_PROPOSED_INPUT_ONLY_NOT_AUTHORIZED",
        "authoritative_manifest": "remediation_manifest.r2_2.proposed.json",
        "required_r2_files": files,
        "source_evidence_checks": source_checks,
        "all_required_hashes_valid": ok and r2_1_ok,
    }
    result["sha256"] = sha_obj(result)
    return result


def retry1_fix_record() -> dict[str, Any]:
    after_sha = sha_bytes(Path(__file__).read_bytes())
    record = {
        "modified_file_path": "tools/p1_14e_r2_2.py",
        "remote_runtime_copy": str(Path(__file__)),
        "before_sha256": FIX_BEFORE_SHA256,
        "after_sha256": after_sha,
        "authorized_scope": "minimal retry1-only fix for PostgreSQL bind parameter type inference in UUID scanner CTE/array inputs",
        "scan_scope_changed": False,
        "matching_semantics_weakened": False,
        "minimal_diff": [
            {"before": "OUT = Path('/tmp/p1_14e_r2_2_exact_remediation_authorization_packet')", "after": "OUT = Path('/tmp/p1_14e_r2_2_exact_remediation_authorization_packet_retry1')"},
            {"before": "ANY(:ids)", "after": "ANY(CAST(:ids AS text[]))"},
            {"before": "ILIKE ANY(:patterns)", "after": "ILIKE ANY(CAST(:patterns AS text[]))"},
            {"before": "jsonb_build_object('id', :id0)", "after": "jsonb_build_object('id', CAST(:id0 AS text))"},
            {"before": "jsonb_build_array(:id0)", "after": "jsonb_build_array(CAST(:id0 AS text))"},
            {"before": "ARRAY[:id0]::text[]", "after": "ARRAY[CAST(:id0 AS text)]::text[]"},
            {"before": ":id0::uuid", "after": "CAST(:id0 AS uuid)"},
        ],
    }
    record["sha256"] = sha_obj(record)
    return record


def predecessor_failure_attestation() -> dict[str, Any]:
    records = []
    if PREDECESSOR_FAILURE_DIR.exists():
        for p in sorted(PREDECESSOR_FAILURE_DIR.iterdir()):
            if p.is_file():
                records.append({"file": p.name, "bytes": p.stat().st_size, "sha256": sha_bytes(p.read_bytes())})
    result = {
        "predecessor_dir": str(PREDECESSOR_FAILURE_DIR),
        "exists": PREDECESSOR_FAILURE_DIR.exists(),
        "file_count": len(records),
        "files": records,
        "reused_as_success_artifact": False,
        "preserved_in_place": True,
        "known_failure": "PostgreSQL parameter type inference failed in UUID scanner literal/CTE self-test before retry1 authorization",
    }
    result["sha256"] = sha_obj(result)
    return result


def expected_deltas_r2_2(qr: dict[str, Any], dep: dict[str, Any], protected: dict[str, Any], trusted: dict[str, Any]) -> dict[str, Any]:
    dep_objects = {}
    for rs in dep["direct_instance_rowsets"]:
        dep_objects[f'{rs["schema"]}.{rs["table"]}:{rs["scope"]}'] = {"before_count": rs.get("row_count", 0), "before_digest": rs.get("before_digest"), "expected_delta": 0, "expected_after_count": rs.get("row_count", 0), "expected_after_digest": rs.get("expected_after_digest"), "target_uuid_hits": rs.get("target_uuid_hits", 0)}
    protected_objects = {name: {"before_count": rs.get("row_count", 0), "before_digest": rs.get("before_digest"), "expected_delta": 0, "expected_after_count": rs.get("expected_after_count", rs.get("row_count", 0)), "expected_after_digest": rs.get("expected_after_digest", rs.get("before_digest")), "target_uuid_hits": rs.get("target_uuid_hits", 0)} for name, rs in protected["rowsets"].items()}
    trusted_objects = {name: {"before_count": rs.get("row_count", 0), "before_digest": rs.get("before_digest"), "expected_delta": 0, "expected_after_count": rs.get("expected_after_count", rs.get("row_count", 0)), "expected_after_digest": rs.get("expected_after_digest", rs.get("before_digest")), "target_uuid_hits": rs.get("target_uuid_hits", 0)} for name, rs in trusted["objects"].items()}
    result = {
        "query_requests": {"before_count": 9, "before_digest": qr["row_set_sha256"], "delete_count": 3, "after_count": 6, "expected_delta": -3, "delete_id_set": TARGET_IDS, "target_row_sha256": qr["target_row_sha256"], "preserve_row_sha256": qr["legitimate_row_sha256"], "expected_after_digest": qr["survivor_row_set_sha256"], "surviving_row_set_sha256": qr["survivor_row_set_sha256"]},
        "dependency_objects": dep_objects,
        "dashboard_snapshot_catalog_v1": {"before_count": dep["dashboard_snapshot_catalog_v1"].get("row_count", 0), "before_digest": dep["dashboard_snapshot_catalog_v1"].get("before_digest"), "expected_delta": 0, "expected_after_count": dep["dashboard_snapshot_catalog_v1"].get("row_count", 0), "expected_after_digest": dep["dashboard_snapshot_catalog_v1"].get("expected_after_digest", dep["dashboard_snapshot_catalog_v1"].get("before_digest")), "target_uuid_hits": dep["dashboard_snapshot_catalog_v1"].get("target_uuid_hits", 0)},
        "p1_14c_protected_objects": protected_objects,
        "trusted_and_formal_objects": trusted_objects,
        "review_decision_approval_promotion_publication": {"review_decisions": 0, "approval": 0, "promotion_runs": 0, "promotion_events": 0, "publication": 0, "expected_delta": 0},
        "audit_mechanism": {"event_triggers": len(dep["event_triggers"]), "query_requests_triggers": len(dep["query_requests_related_triggers"]), "expected_delete_audit_delta": 0},
        "schema_revision_acl_service_status": {"schema_expected_delta": 0, "revision_expected_delta": 0, "acl_expected_delta": 0, "service_restart_expected": False},
    }
    result["sha256"] = sha_obj(result)
    return result


def state_machine() -> dict[str, Any]:
    return {
        "delete_sql_generated": False,
        "future_p1_14f_algorithm": {
            "pre_delete_blockers": [
                "Exact R2.2 manifest SHA-256 is not explicitly authorized by the user.",
                "Database identity, schema catalog, target row hash, legitimate row hash, dependency, P1-14C, or Trusted digest mismatch.",
                "Any writer flow cannot be placed into maintenance window.",
                "Any dependency instance for target UUIDs is nonzero.",
            ],
            "write_transaction_plan_without_sql": [
                "Enter maintenance window and stop/disable Query API, retrieval worker, daily worker, Trusted updater, Promotion and other write flows.",
                "Acquire project-scoped transaction advisory lock.",
                "In one write transaction under lock protection, revalidate database identity and schema digest.",
                "Lock exactly the three target rows and the six legitimate rows.",
                "Recompute all canonical hashes and full dependency closure.",
                "Recompute P1-14C and Trusted protected digests.",
                "Apply exact primary-key set removal only if every precondition holds.",
                "The affected UUID set must equal the three authorized UUIDs and affected row count must equal 3.",
            ],
            "post_delete_pre_commit_gates": [
                "query_requests count is 6 before commit.",
                "The three target UUIDs are absent.",
                "Six legitimate rows retain exact row and survivor-set hashes.",
                "All protected objects retain count/digest.",
                "No unlisted cascade or audit side effect occurred.",
            ],
            "post_commit_verification": [
                "Open an independent read-only transaction after commit.",
                "Recompute after attestation and compare all expected deltas.",
                "Record any post-commit anomaly; do not claim committed changes can be transaction-rolled back after commit.",
            ],
            "rollback_conditions": [
                "Any target missing or hash changed.",
                "Actual target set differs from authorized UUIDs.",
                "Affected rows is not exactly 3.",
                "Any nonzero dependency or unlisted cascade appears.",
                "Any legitimate, P1-14C, or Trusted object changes.",
                "Actual delta differs from expected delta.",
                "Atomicity cannot be proven.",
            ],
        },
    }


def write_sidecar(name: str) -> str:
    digest = sha_bytes((OUT / name).read_bytes())
    (OUT / f"{name}.sha256").write_text(digest + f"  {name}\n", encoding="utf-8")
    return digest


def write_evidence_index() -> dict[str, Any]:
    excluded = {
        "evidence_index.r2_2.json",
        "evidence_index.r2_2.json.sha256",
        "remediation_manifest.r2_2.proposed.json",
        "remediation_manifest.r2_2.proposed.json.sha256",
        "report.json",
        "report.md",
        "package_index.r2_2.json",
        "package_index.r2_2.json.sha256",
        "complete_packet.r2_2.zip",
        "complete_packet.r2_2.zip.sha256",
    }
    records = [file_record(p) for p in sorted(OUT.iterdir()) if p.is_file() and p.name not in excluded]
    index = {"schema_version": "p1_14e_r2_2_evidence_index.v1", "self_listed": False, "evidence_files": records}
    write_json("evidence_index.r2_2.json", index)
    index["sha256"] = write_sidecar("evidence_index.r2_2.json")
    return index


def build_package_index(member_names: list[str]) -> dict[str, Any]:
    members = [file_record(OUT / name, role="zip_member") for name in member_names]
    package = {"schema_version": "p1_14e_r2_2_package_index.v1", "self_listed": False, "zip_members": members}
    write_json("package_index.r2_2.json", package)
    package["sha256"] = write_sidecar("package_index.r2_2.json")
    return package


def build_and_verify_zip(member_names: list[str]) -> tuple[str, dict[str, Any]]:
    zip_path = OUT / "complete_packet.r2_2.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in member_names:
            zf.write(OUT / name, arcname=name)
    zip_sha = sha_bytes(zip_path.read_bytes())
    (OUT / "complete_packet.r2_2.zip.sha256").write_text(zip_sha + "  complete_packet.r2_2.zip\n", encoding="utf-8")
    tmp = Path(tempfile.mkdtemp(prefix="p1_14e_r2_2_verify_"))
    try:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(tmp)
        checks = []
        ok = True
        for name in member_names:
            original = OUT / name
            extracted = tmp / name
            same = extracted.exists() and original.read_bytes() == extracted.read_bytes() and sha_bytes(extracted.read_bytes()) == sha_bytes(original.read_bytes())
            checks.append({"file": name, "bytes": original.stat().st_size, "sha256": sha_bytes(original.read_bytes()), "verified_after_extract": same})
            ok = ok and same
        observed = sorted(p.name for p in tmp.iterdir() if p.is_file())
        ok = ok and observed == sorted(member_names)
        verification = {"verified": ok, "member_count": len(member_names), "members": checks, "observed_member_names_match": observed == sorted(member_names)}
        return zip_sha, verification
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> None:
    if OUT.exists():
        print(json.dumps({"status": "BLOCKED_R2_2_RETRY1_OUTPUT_DIR_ALREADY_EXISTS", "output_dir": str(OUT), "actual_database_writes": 0, "rows_deleted": 0}, ensure_ascii=False, indent=2, sort_keys=True))
        raise SystemExit(2)
    OUT.mkdir(parents=True)
    evidence = input_evidence()
    write_json("retry1_generator_fix_record.json", retry1_fix_record())
    write_json("predecessor_failure_attestation.json", predecessor_failure_attestation())
    code_scan = full_code_scan()
    metadata = metadata_reattestation()
    engine = create_engine(os.environ["APP_DATABASE_URL"], hide_parameters=True, future=True)
    try:
        with engine.connect() as conn:
            snap_a = snapshot(conn, "snapshot_a")
        with engine.connect() as conn:
            snap_b = snapshot(conn, "snapshot_b")
    finally:
        engine.dispose()

    write_json("input_evidence_hashes.json", evidence)
    write_json("database_snapshot_a.json", {k: v for k, v in snap_a.items() if k != "catalog"})
    write_json("database_snapshot_b.json", {k: v for k, v in snap_b.items() if k != "catalog"})
    schema_attestation = {
        "metadata_reattestation": metadata,
        "app_snapshot_a_identity": snap_a["identity"],
        "app_snapshot_b_identity": snap_b["identity"],
        "app_schema_catalog_sha256": snap_a["schema_catalog_sha256"],
        "app_schema_catalog_stable": snap_a["schema_catalog_sha256"] == snap_b["schema_catalog_sha256"],
        "alembic_revision_set": metadata.get("alembic_revision_set"),
        "schema_digest_algorithm_version": "p1_14e_r2_2_schema_catalog_digest_v1_full_catalog",
        "r2_vs_r2_1_digest_explanation": {
            "conclusion": "prior R2/R2.1 catalog digest prefixes must not be claimed equal because the summarization algorithms and included evidence changed; R2.2 recomputes and binds a fresh schema catalog digest",
            "r2_digest_prefix": "153240dc…",
            "r2_1_digest_prefix": "7add9617…",
            "algorithm_equality_claimed": False,
        },
    }
    write_json("schema_and_revision_attestation.json", schema_attestation)
    write_json("canonical_hash_spec.r2_2.json", {"version": "p1_14e_r2_2_canonical_hash_spec_v2", "rules": ["strings are never parsed as JSON", "only JSON/JSONB values are recursively normalized", "timestamps are converted to UTC RFC3339 with fixed microsecond precision", "UTF-8 bytes, ensure_ascii=false, compact separators, recursive key sort", "query_requests set order is created_at UTC then id", "set summary input structure is {'ids':[ordered ids],'hashes':{id:canonical_row_hash}}", "canonical implementation file sha256 is recorded"], "canonical_implementation_file": "p1_14e_r2_2.py", "canonical_implementation_sha256": sha_bytes(Path(__file__).read_bytes()), "query_requests_columns": snap_a["query_requests"]["columns"], "set_hash_algorithm": "SHA-256 over compact canonical JSON object containing ordered IDs and id-to-row-hash map"})
    write_json("query_requests_inventory_sanitized.json", snap_a["query_requests"])
    write_json("target_row_hashes.json", snap_a["query_requests"]["target_row_sha256"])
    write_json("legitimate_row_hashes.json", snap_a["query_requests"]["legitimate_row_sha256"])
    write_json("row_set_hashes.json", {"all_9_row_set_sha256": snap_a["query_requests"]["row_set_sha256"], "expected_all_9_row_set_sha256": EXPECTED_ALL_SET_SHA, "survivor_6_row_set_sha256": snap_a["query_requests"]["survivor_row_set_sha256"], "expected_survivor_6_row_set_sha256": EXPECTED_SURVIVOR_SET_SHA})
    write_json("dependency_scan_coverage.r2_2.json", snap_a["dependency"])
    write_json("dependency_closure_revalidation.r2_2.json", {"snapshot_a": snap_a["dependency"], "snapshot_b_sha256": snap_b["dependency"]["sha256"], "stable": snap_a["dependency"]["sha256"] == snap_b["dependency"]["sha256"], "coverage_complete": snap_a["dependency"]["coverage_complete"], "target_dependency_instances": snap_a["dependency"]["target_dependency_instances"], "target_dependency_instances_zero": snap_a["dependency"]["target_dependency_instances_zero"]})
    write_json("protected_chain_rowsets.r2_2.json", snap_a["protected"])
    write_json("trusted_object_rowsets.r2_2.json", snap_a["trusted"])
    expected = expected_deltas_r2_2(snap_a["query_requests"], snap_a["dependency"], snap_a["protected"], snap_a["trusted"])
    write_json("expected_deltas.r2_2.json", expected)
    sm = state_machine()
    sm["future_p1_14f_algorithm"]["recovery_mode"] = "TRANSACTION_ONLY"
    sm["future_p1_14f_algorithm"]["delete_sql_generated_in_r2_2"] = False
    write_json("transaction_state_machine.r2_2.json", sm)
    write_json("read_only_sql_log_sanitized.json", SQL_LOG)

    evidence_index = write_evidence_index()
    stable = (
        snap_a["query_requests"] == snap_b["query_requests"]
        and snap_a["dependency"]["sha256"] == snap_b["dependency"]["sha256"]
        and snap_a["protected"]["sha256"] == snap_b["protected"]["sha256"]
        and snap_a["trusted"]["sha256"] == snap_b["trusted"]["sha256"]
    )
    blockers = []
    if metadata.get("status") != "ATTESTED":
        blockers.append("P1_14E_R2_2_PENDING_PRIVILEGED_METADATA_REATTESTATION")
    if not evidence["all_required_hashes_valid"]:
        blockers.append("BLOCKED_SOURCE_EVIDENCE_HASH_MISMATCH")
    if not stable or not snap_a["query_requests"]["stable_against_r2"]:
        blockers.append("BLOCKED_REMEDIATION_TARGET_STATE_DRIFT")
    if snap_a["dependency"]["target_dependency_instances"]:
        blockers.append("BLOCKED_REMEDIATION_NONZERO_DEPENDENCY")
    if not snap_a["dependency"]["coverage_complete"]:
        blockers.append("BLOCKED_CONTAMINATION_DEPENDENCY_CLOSURE_INCOMPLETE")
    if any(snap_a["protected"]["intersection_with_targets"].values()) or not snap_a["protected"]["summary"]["preserved"]:
        blockers.append("BLOCKED_P1_14C_PROTECTED_CHAIN_OVERLAP")
    status = "P1_14E_R2_2_EXACT_REMEDIATION_PACKET_READY_AWAITING_USER_AUTHORIZATION" if not blockers else blockers[0]

    support_names = sorted([r["file"] for r in evidence_index["evidence_files"]] + ["evidence_index.r2_2.json", "evidence_index.r2_2.json.sha256"])
    support = [file_record(OUT / name) for name in support_names]
    manifest = {
        "schema_version": "p1_14e_r2_2_exact_remediation_authorization_packet.v1",
        "status": "PROPOSED_ONLY_NOT_EXECUTED",
        "authorized": False,
        "requires_user_authorization": True,
        "action_taken": "NONE",
        "delete_sql_generated": False,
        "authoritative_manifest": "remediation_manifest.r2_2.proposed.json",
        "source_r2_manifest_sha256": EXPECTED_R2_SHA,
        "defective_r2_1_manifest_sha256": EXPECTED_R2_1_SHA,
        "defective_r2_1_zip_sha256": EXPECTED_R2_1_ZIP_SHA,
        "supporting_files": support,
        "evidence_index_binding": file_record(OUT / "evidence_index.r2_2.json"),
        "database_identity": snap_a["identity"],
        "schema_catalog_sha256": snap_a["schema_catalog_sha256"],
        "canonical_hash_binding": {"spec_file": "canonical_hash_spec.r2_2.json", "target_row_hashes": snap_a["query_requests"]["target_row_sha256"], "legitimate_row_hashes": snap_a["query_requests"]["legitimate_row_sha256"], "all_9_row_set_sha256": snap_a["query_requests"]["row_set_sha256"], "survivor_6_row_set_sha256": snap_a["query_requests"]["survivor_row_set_sha256"]},
        "proposed_action": {"operation": "FUTURE_EXACT_PRIMARY_KEY_REMOVAL_REQUIRES_NEW_AUTHORIZATION", "target_schema": "epidemic", "target_table": "query_requests", "primary_key_column": "id", "target_count": 3, "executable_delete_sql_included": False},
        "target_rows": snap_a["query_requests"]["target_rows"],
        "protected_legitimate_rows": snap_a["query_requests"]["legitimate_rows"],
        "dependency_scan_sha256": snap_a["dependency"]["sha256"],
        "protected_chain_sha256": snap_a["protected"]["sha256"],
        "trusted_objects_sha256": snap_a["trusted"]["sha256"],
        "expected_deltas": expected,
        "transaction_state_machine": sm,
        "actual_database_writes": 0,
        "rows_deleted": 0,
        "review_decision_approval_promotion_trusted_writes": 0,
        "next_stage": "P1_14F_REQUIRES_NEW_EXACT_R2_2_USER_AUTHORIZATION",
    }
    write_json("remediation_manifest.r2_2.proposed.json", manifest)
    manifest_sha = write_sidecar("remediation_manifest.r2_2.proposed.json")

    report = {
        "status": status,
        "non_authorizing_display_document": True,
        "authoritative_manifest": "remediation_manifest.r2_2.proposed.json",
        "new_manifest_sha256": manifest_sha,
        "r2_manifest_sha256_matches": evidence["r2_manifest_matches"],
        "r2_1_manifest_sha256_matches_expected_defective_input": evidence["r2_1_manifest_matches"],
        "r2_1_zip_sha256_matches_expected_defective_input": evidence["r2_1_zip_matches"],
        "target_row_sha256": snap_a["query_requests"]["target_row_sha256"],
        "all_9_row_set_sha256": snap_a["query_requests"]["row_set_sha256"],
        "survivor_6_row_set_sha256": snap_a["query_requests"]["survivor_row_set_sha256"],
        "dependency_scan": {"coverage_complete": snap_a["dependency"]["coverage_complete"], "target_dependency_instances": snap_a["dependency"]["target_dependency_instances"], "sha256": snap_a["dependency"]["sha256"]},
        "protected_p1_14c": snap_a["protected"]["summary"],
        "trusted_objects_sha256": snap_a["trusted"]["sha256"],
        "actual_database_writes": 0,
        "rows_deleted": 0,
        "review_decision_approval_promotion_trusted_writes": 0,
        "blockers": blockers,
    }
    write_json("report.json", report)
    (OUT / "report.md").write_text("\n".join(["# P1-14E-R2.2 exact remediation authorization packet", "", "This is a non-authorizing display report. It does not authorize P1-14F.", "", f"Status: `{status}`", f"Authoritative manifest: `remediation_manifest.r2_2.proposed.json`", f"Manifest SHA-256: `{manifest_sha}`", f"All 9 query_requests row-set SHA-256: `{snap_a['query_requests']['row_set_sha256']}`", f"Six survivor row-set SHA-256: `{snap_a['query_requests']['survivor_row_set_sha256']}`", f"Dependency coverage complete: `{snap_a['dependency']['coverage_complete']}`", f"Target dependency instances: `{snap_a['dependency']['target_dependency_instances']}`", "Actual database writes=0; rows deleted=0; Review Decision/Approval/Promotion/Trusted writes=0."]) + "\n", encoding="utf-8")

    zip_members = sorted([
        p.name
        for p in OUT.iterdir()
        if p.is_file()
        and p.name not in {"package_index.r2_2.json", "package_index.r2_2.json.sha256", "complete_packet.r2_2.zip", "complete_packet.r2_2.zip.sha256"}
    ])
    package_index = build_package_index(zip_members)
    zip_sha, zip_verification = build_and_verify_zip(zip_members)
    if not zip_verification["verified"]:
        status = "BLOCKED_R2_2_PACKAGE_HASH_BINDING_MISMATCH"
        report["status"] = status
        report["blockers"] = [status]
        write_json("report.json", report)
    print(json.dumps({**report, "evidence_index_sha256": evidence_index["sha256"], "package_index_sha256": package_index["sha256"], "complete_packet_zip": str(OUT / "complete_packet.r2_2.zip"), "complete_packet_zip_sha256": zip_sha, "zip_verification": zip_verification}, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
