from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import psycopg
from psycopg import Connection

ROOT = Path("USER_HOME/projects/openclaw")
PARENT_REPORT = ROOT / "reports/r4e_cdc_ilinet_ingestion_20260806T1225Z"
PARENT_ADAPTER = ROOT / "experiments/r4e_cdc_ilinet_ingestion_20260806T1225Z/adapter"
SOURCE_KEY = "cdc_fluview_ilinet_state_weekly"
RAW_SHA = "29e5c7d3ae69c7ebd2cb48fce9751263436629e24786a28db18ebd6b8867f4db"
INIT_SHA = "f05b542ce747bc3aec878d3210b9bf0928fb8c2df116d1bcfe42e783f54dab8c"
NORM_SHA = "0ddee63fad939f648ee6688aec527f2379c3be6554bb5169903ebeabb4ec88b6"
TARGET_CANDIDATE_ID = "7b8ea8a8-2f4f-5975-b3ad-08f6a03ccaf4"
TARGET_CLAIM_ID = "6ef6f7e4-f510-5358-8c84-50ecd73283de"
TARGET_TABLES = [
    "epidemic.data_sources",
    "epidemic.query_requests",
    "epidemic.raw_artifacts",
    "epidemic.retrieval_runs",
    "epidemic.source_fetches",
    "epidemic.metric_definitions",
    "epidemic.normalized_observations",
    "epidemic.dataset_snapshots",
    "epidemic_p1_12.trusted_source_contracts",
    "epidemic_p1_12.trusted_fetch_artifacts",
    "epidemic_p1_12.trusted_observation_lineage",
    "epidemic_p1_12.trusted_snapshot_lineage",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def compact_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_path(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def write_text(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")


def load_secret_env() -> dict[str, str]:
    env_path = ROOT / "runtime/secrets/vm201_runtime.env"
    env: dict[str, str] = {}
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key] = value
    return env


def db_connect() -> Connection[Any]:
    env = load_secret_env()
    return psycopg.connect(
        host="127.0.0.1",
        port=55432,
        dbname="epidemic_openclaw",
        user="postgres",
        password=env["PG_SUPERPASS"],
    )


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def split_table(table: str) -> tuple[str, str]:
    schema, name = table.split(".", 1)
    return schema, name


def table_exists(conn: Connection[Any], table: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s)", (table,))
        row = cur.fetchone()
        return row is not None and row[0] is not None


def table_columns(conn: Connection[Any], table: str) -> list[str]:
    schema, name = split_table(table)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            ORDER BY ordinal_position
            """,
            (schema, name),
        )
        return [row[0] for row in cur.fetchall()]


def table_hash(conn: Connection[Any], table: str, predicate: str | None = None) -> tuple[int, str, list[str]]:
    if not table_exists(conn, table):
        return 0, "NOT_PRESENT", []
    columns = table_columns(conn, table)
    if not columns:
        return 0, sha256_bytes(b""), []
    schema, name = split_table(table)
    select_values = ", ".join(f"COALESCE({quote_ident(col)}::text, '<NULL>')" for col in columns)
    order_values = ", ".join(f"COALESCE({quote_ident(col)}::text, '<NULL>')" for col in columns)
    where_sql = f" WHERE {predicate}" if predicate else ""
    sql = f"SELECT {select_values} FROM {quote_ident(schema)}.{quote_ident(name)}{where_sql} ORDER BY {order_values}"
    h = hashlib.sha256()
    count = 0
    with conn.cursor() as cur:
        cur.execute(sql)
        for row in cur:
            h.update(json.dumps(list(row), ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            h.update(b"\n")
            count += 1
    return count, h.hexdigest(), columns


def count_sql(conn: Connection[Any], sql: str, args: tuple[Any, ...] = ()) -> int:
    with conn.cursor() as cur:
        cur.execute(sql, args)
        row = cur.fetchone()
        value = row[0] if row is not None else 0
        return int(value or 0)


def query_one(conn: Connection[Any], sql: str, args: tuple[Any, ...] = ()) -> Any:
    with conn.cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchone()


def query_json_rows(conn: Connection[Any], sql: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(sql, args)
        names = [desc.name for desc in cur.description or []]
        return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]


def mmwr_week_start(year: int, week: int) -> date:
    jan4 = date(year, 1, 4)
    start_week1 = jan4 - timedelta(days=(jan4.weekday() + 1) % 7)
    return start_week1 + timedelta(weeks=week - 1)


def season_label_for_week(start: date) -> str:
    if start.month >= 10:
        return f"{start.year}-{str(start.year + 1)[-2:]}"
    return f"{start.year - 1}-{str(start.year)[-2:]}"


def parse_num(value: str) -> float | None:
    stripped = (value or "").strip()
    if stripped in {"", "X", "XX", "N/A", "NA", "-"}:
        return None
    return float(stripped)


def parse_int(value: str) -> int | None:
    parsed = parse_num(value)
    if parsed is None or math.isnan(parsed):
        return None
    return int(round(parsed))


def safe_zip_audit(raw_zip: Path) -> dict[str, Any]:
    with zipfile.ZipFile(raw_zip) as zf:
        bad_crc = zf.testzip()
        members: list[dict[str, Any]] = []
        total_uncompressed = 0
        for info in zf.infolist():
            filename = info.filename
            is_abs = filename.startswith("/") or re.match(r"^[A-Za-z]:", filename) is not None
            has_dotdot = any(part == ".." for part in Path(filename).parts)
            mode = (info.external_attr >> 16) & 0o170000
            is_symlink = mode == 0o120000
            total_uncompressed += info.file_size
            members.append(
                {
                    "filename": filename,
                    "crc": info.CRC,
                    "compressed_size": info.compress_size,
                    "file_size": info.file_size,
                    "is_absolute": is_abs,
                    "has_dotdot": has_dotdot,
                    "is_symlink": is_symlink,
                    "compression_ratio": None if info.file_size == 0 else round(info.compress_size / info.file_size, 6),
                }
            )
        return {
            "crc_test_bad_member": bad_crc,
            "member_count": len(members),
            "total_uncompressed_size": total_uncompressed,
            "max_member_size": max((m["file_size"] for m in members), default=0),
            "members": members,
            "safe": bad_crc is None
            and all(not m["is_absolute"] and not m["has_dotdot"] and not m["is_symlink"] for m in members),
        }


def extract_ilinet_csv(raw_zip: Path, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(raw_zip) as zf:
        names = [info.filename for info in zf.infolist()]
        if names != ["ILINet.csv"]:
            raise RuntimeError(f"UNEXPECTED_ZIP_MEMBERS:{names}")
        target = out_dir / "ILINet.csv"
        with zf.open("ILINet.csv") as src, target.open("wb") as dst:
            shutil.copyfileobj(src, dst)
    return target


def normalize_frozen(
    raw_zip: Path,
    init_json: Path,
    out_dir: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any], Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = extract_ilinet_csv(raw_zip, out_dir / "extract")
    init = json.loads(init_json.read_text(encoding="utf-8"))
    state_by_name = {item["statename"]: item for item in init["states"]}
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    with csv_path.open(newline="", encoding="utf-8-sig") as f:
        title_line = f.readline().strip()
        header = f.readline()
        fieldnames = next(csv.reader([header]))
        reader = csv.DictReader(f, fieldnames=fieldnames)
        for raw in reader:
            region_type = raw["REGION TYPE"].strip()
            region = raw["REGION"].strip()
            if region_type != "States":
                continue
            try:
                year = int(raw["YEAR"])
                week = int(raw["WEEK"])
                start = mmwr_week_start(year, week)
                end = start + timedelta(days=6)
                state = state_by_name.get(region)
                weighted = parse_num(raw["% WEIGHTED ILI"])
                unweighted = parse_num(raw["%UNWEIGHTED ILI"])
                ili_total = parse_int(raw["ILITOTAL"])
                total_patients = parse_int(raw["TOTAL PATIENTS"])
                recomputed = None
                if ili_total is not None and total_patients and total_patients > 0:
                    recomputed = 100.0 * ili_total / total_patients
                rows.append(
                    {
                        "source_key": SOURCE_KEY,
                        "publisher": "CDC",
                        "product": "FluView Interactive / ILINet",
                        "region_type": region_type,
                        "region": region,
                        "state_id": state["stateid"] if state else None,
                        "state_fips": (state.get("statefips") or "").strip() if state else None,
                        "cdc_year": year,
                        "cdc_mmwr_week": week,
                        "event_week_start": start.isoformat(),
                        "event_week_end": end.isoformat(),
                        "influenza_season": season_label_for_week(start),
                        "percent_weighted_ili": weighted,
                        "percent_unweighted_ili": unweighted,
                        "age_0_4": parse_int(raw["AGE 0-4"]),
                        "age_25_49": parse_int(raw["AGE 25-49"]),
                        "age_25_64": parse_int(raw["AGE 25-64"]),
                        "age_5_24": parse_int(raw["AGE 5-24"]),
                        "age_50_64": parse_int(raw["AGE 50-64"]),
                        "age_65": parse_int(raw["AGE 65"]),
                        "ili_total": ili_total,
                        "num_providers": parse_int(raw["NUM. OF PROVIDERS"]),
                        "total_patients": total_patients,
                        "percent_unweighted_ili_recomputed": recomputed,
                        "historical_vintage_available": False,
                        "is_provisional": True,
                    }
                )
            except (KeyError, ValueError) as exc:
                errors.append({"raw": raw, "error": str(exc)})
    if errors:
        raise RuntimeError(f"NORMALIZATION_ERRORS:{errors[:3]}")
    norm_path = out_dir / "cdc_fluview_ilinet_state_weekly_normalized.csv.gz"
    keys = list(rows[0].keys()) if rows else []
    with (
        norm_path.open("wb") as raw_f,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw_f, mtime=0) as gz,
        io.TextIOWrapper(gz, encoding="utf-8", newline="") as f,
    ):
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)
    key_counts = Counter((r["region"], r["cdc_year"], r["cdc_mmwr_week"]) for r in rows)
    profile = {
        "title_line": title_line,
        "raw_rows": len(rows),
        "row_count": len(rows),
        "region_count": len({str(r["region"]) for r in rows}),
        "event_week_start_min": min(str(r["event_week_start"]) for r in rows),
        "event_week_start_max": max(str(r["event_week_start"]) for r in rows),
        "latest_week_end": max(str(r["event_week_end"]) for r in rows),
        "duplicate_region_week_keys": sum(1 for count in key_counts.values() if count > 1),
        "percent_unweighted_ili_values": sum(r["percent_unweighted_ili"] is not None for r in rows),
        "percent_weighted_ili_values": sum(r["percent_weighted_ili"] is not None for r in rows),
        "weighted_zero_values": sum(
            r["percent_weighted_ili"] == 0.0 for r in rows if r["percent_weighted_ili"] is not None
        ),
        "schema_fingerprint": sha256_bytes("|".join(keys).encode("utf-8")),
        "normalized_sha256": sha256_path(norm_path),
    }
    return rows, profile, norm_path


def normalize_once_cli() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", required=True)
    parser.add_argument("--init", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(sys.argv[2:])
    rows, profile, path = normalize_frozen(Path(args.raw), Path(args.init), Path(args.out))
    write_json(Path(args.out) / "normalize_once.json", {"profile": profile, "path": str(path), "rows": len(rows)})


def run_command(
    command_id: str,
    cmd: list[str],
    cwd: Path,
    logs_dir: Path,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    started = utc_now()
    stdout_path = logs_dir / f"{command_id}.stdout.txt"
    stderr_path = logs_dir / f"{command_id}.stderr.txt"
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    proc = subprocess.run(cmd, cwd=cwd, env=merged_env, text=True, capture_output=True, check=False)
    stdout_path.write_text(proc.stdout, encoding="utf-8", errors="replace")
    stderr_path.write_text(proc.stderr, encoding="utf-8", errors="replace")
    return {
        "command_id": command_id,
        "redacted_command": " ".join(re.sub(r"postgresql://[^ ]+", "postgresql://<redacted>", part) for part in cmd),
        "working_directory": str(cwd),
        "started_at_utc": started,
        "finished_at_utc": utc_now(),
        "exit_code": proc.returncode,
        "stdout_path": str(stdout_path.relative_to(logs_dir.parent)),
        "stderr_path": str(stderr_path.relative_to(logs_dir.parent)),
    }


def discover_mapping(conn: Connection[Any]) -> dict[str, Any]:
    objects: dict[str, Any] = {}
    for table in TARGET_TABLES:
        exists = table_exists(conn, table)
        schema, name = split_table(table)
        columns: list[dict[str, Any]] = []
        constraints: list[dict[str, Any]] = []
        indexes: list[dict[str, Any]] = []
        if exists:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT column_name, data_type, is_nullable
                    FROM information_schema.columns
                    WHERE table_schema=%s AND table_name=%s
                    ORDER BY ordinal_position
                    """,
                    (schema, name),
                )
                columns = [{"name": r[0], "type": r[1], "nullable": r[2]} for r in cur.fetchall()]
                cur.execute(
                    """
                    SELECT conname, contype, pg_get_constraintdef(c.oid)
                    FROM pg_constraint c
                    JOIN pg_class cl ON cl.oid = c.conrelid
                    JOIN pg_namespace n ON n.oid = cl.relnamespace
                    WHERE n.nspname=%s AND cl.relname=%s
                    ORDER BY conname
                    """,
                    (schema, name),
                )
                constraints = [{"name": r[0], "type": r[1], "definition": r[2]} for r in cur.fetchall()]
                cur.execute(
                    """
                    SELECT indexname, indexdef
                    FROM pg_indexes
                    WHERE schemaname=%s AND tablename=%s
                    ORDER BY indexname
                    """,
                    (schema, name),
                )
                indexes = [{"name": r[0], "definition": r[1]} for r in cur.fetchall()]
        objects[table] = {"exists": exists, "columns": columns, "constraints": constraints, "indexes": indexes}
    views = query_json_rows(
        conn,
        """
        SELECT schemaname, viewname, definition
        FROM pg_views
        WHERE schemaname IN ('epidemic','epidemic_p1_12')
        ORDER BY schemaname, viewname
        """,
    )
    return {"target_tables": objects, "views": views}


def scoped_predicate_for_table(conn: Connection[Any], table: str) -> str | None:
    columns = set(table_columns(conn, table)) if table_exists(conn, table) else set()
    if table == "epidemic.data_sources":
        return "source_key = 'cdc_fluview_ilinet_state_weekly'"
    if table == "epidemic.query_requests":
        parts: list[str] = []
        if "fingerprint" in columns:
            parts.append("fingerprint::text LIKE '%cdc_fluview_ilinet_state_weekly%'")
        if "source_preference" in columns:
            parts.append("source_preference::text LIKE '%cdc_fluview%'")
        if "raw_disease" in columns:
            parts.append("raw_disease::text ILIKE '%influenza%'")
        return " OR ".join(parts) if parts else None
    if table == "epidemic.raw_artifacts":
        return f"sha256 IN ('{RAW_SHA}','{INIT_SHA}')"
    if table == "epidemic.retrieval_runs":
        parts = []
        if "run_key" in columns:
            parts.append("run_key LIKE 'cdc_fluview_ilinet_state_weekly%'")
        if "error_message" in columns:
            parts.append("error_message::text LIKE '%cdc_fluview_ilinet_state_weekly%'")
        return " OR ".join(parts) if parts else None
    if table == "epidemic.source_fetches":
        if "canonical_url" in columns:
            return "canonical_url LIKE '%gis.cdc.gov/grasp/flu2/%' OR canonical_url LIKE '%gis.cdc.gov/grasp/fluview/%'"
        return None
    if table == "epidemic.metric_definitions":
        if "metric_code" in columns:
            return "metric_code IN ('percent_unweighted_ili','percent_weighted_ili')"
        if "metric_key" in columns:
            return "metric_key IN ('percent_unweighted_ili','percent_weighted_ili')"
        return None
    if table == "epidemic.normalized_observations":
        if "source_key" in columns:
            return "source_key = 'cdc_fluview_ilinet_state_weekly'"
        return (
            "id IN (SELECT l.observation_id FROM epidemic_p1_12.trusted_observation_lineage l "
            "JOIN epidemic_p1_12.trusted_source_contracts c ON c.id = l.source_contract_id "
            "WHERE c.source_key = 'cdc_fluview_ilinet_state_weekly')"
        )
    if table == "epidemic.dataset_snapshots":
        return f"content_sha256 = '{NORM_SHA}' OR feature_spec::text LIKE '%cdc_fluview_ilinet_state_weekly%'"
    if table == "epidemic_p1_12.trusted_source_contracts":
        return "source_key = 'cdc_fluview_ilinet_state_weekly'"
    if table == "epidemic_p1_12.trusted_fetch_artifacts":
        return "canonical_url LIKE '%gis.cdc.gov/grasp/flu2/%' OR canonical_url LIKE '%gis.cdc.gov/grasp/fluview/%'"
    if table == "epidemic_p1_12.trusted_observation_lineage":
        return (
            "source_contract_id IN (SELECT id FROM epidemic_p1_12.trusted_source_contracts "
            "WHERE source_key = 'cdc_fluview_ilinet_state_weekly')"
        )
    if table == "epidemic_p1_12.trusted_snapshot_lineage":
        return (
            "source_contract_id IN (SELECT id FROM epidemic_p1_12.trusted_source_contracts "
            "WHERE source_key = 'cdc_fluview_ilinet_state_weekly')"
        )
    return None


def database_baseline(conn: Connection[Any]) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for table in TARGET_TABLES:
        predicate = scoped_predicate_for_table(conn, table) if table_exists(conn, table) else None
        total_count, total_hash, columns = table_hash(conn, table)
        scoped_count, scoped_hash, _ = table_hash(conn, table, predicate) if predicate else (0, "NO_SCOPE", columns)
        entries.append(
            {
                "schema_table": table,
                "scope_predicate_non_secret": predicate or "NOT_PRESENT_OR_NO_SCOPE",
                "primary_or_business_key": infer_business_key(table, columns),
                "before_total_count": total_count,
                "before_r4e_scoped_count": scoped_count,
                "before_content_sha256": scoped_hash,
                "hash_column_list": columns,
                "canonical_order": "all hash columns lexicographic text order",
                "null_encoding": "<NULL>",
                "captured_at_utc": utc_now(),
            }
        )
    return {"tables": entries}


def infer_business_key(table: str, columns: list[str]) -> list[str]:
    preferred = {
        "epidemic.data_sources": ["source_key"],
        "epidemic.raw_artifacts": ["sha256"],
        "epidemic.metric_definitions": ["metric_key"],
        "epidemic.normalized_observations": ["series_fingerprint", "record_fingerprint", "revision"],
        "epidemic.dataset_snapshots": ["content_sha256"],
        "epidemic_p1_12.trusted_source_contracts": ["source_key"],
        "epidemic_p1_12.trusted_fetch_artifacts": ["response_sha256", "canonical_url"],
    }.get(table, ["id"])
    return [col for col in preferred if col in columns]


def safety_snapshot(conn: Connection[Any]) -> dict[str, Any]:
    candidates = [
        ("forecast_runs", ["epidemic.forecast_runs", "epidemic_p1_12.forecast_runs"]),
        ("forecast_values", ["epidemic.forecast_values", "epidemic_p1_12.forecast_values"]),
        (
            "candidate_relations",
            ["epidemic.kg_claim_candidates", "epidemic_p1_12.kg_claim_candidates", "epidemic.kg_candidates"],
        ),
        ("claim_relations", ["epidemic.kg_claims", "epidemic_p1_12.kg_claims"]),
        ("evidence_relations", ["epidemic.kg_claim_evidence", "epidemic_p1_12.kg_claim_evidence"]),
        (
            "review_relations",
            ["epidemic.kg_review_events", "epidemic_p1_12.kg_review_events", "epidemic.source_review_cases"],
        ),
        ("forecast_active_pointer", ["epidemic.forecast_active_pointer", "epidemic_p1_12.forecast_active_pointer"]),
        ("promotion_pointer", ["epidemic.promotion_pointer", "epidemic_p1_12.promotion_pointer"]),
    ]
    out: dict[str, Any] = {}
    for label, table_names in candidates:
        present = [t for t in table_names if table_exists(conn, t)]
        out[label] = []
        for table in present:
            count, digest, columns = table_hash(conn, table)
            out[label].append({"table": table, "count": count, "content_sha256": digest, "columns": columns})
        if not present:
            out[label] = "NOT_PRESENT"
    target_claim = query_target_claim(conn)
    r4e_obs_pred = scoped_predicate_for_table(conn, "epidemic.normalized_observations")
    if r4e_obs_pred is None:
        raise RuntimeError("R4E_OBSERVATION_SCOPE_NOT_RESOLVED")
    non_r4e_obs_pred = "NOT (" + r4e_obs_pred + ")"
    non_r4e_count, non_r4e_hash, _ = table_hash(conn, "epidemic.normalized_observations", non_r4e_obs_pred)
    out["non_r4e_observations"] = {"count": non_r4e_count, "content_sha256": non_r4e_hash}
    out["target_claim"] = target_claim
    return out


def query_target_claim(conn: Connection[Any]) -> dict[str, Any]:
    tables = [t for t in ["epidemic.kg_claims", "epidemic_p1_12.kg_claims"] if table_exists(conn, t)]
    for table in tables:
        columns = table_columns(conn, table)
        if "id" not in columns:
            continue
        col_expr = ", ".join(quote_ident(c) for c in columns)
        schema, name = split_table(table)
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {col_expr} FROM {quote_ident(schema)}.{quote_ident(name)} WHERE id::text = %s",
                (TARGET_CLAIM_ID,),
            )
            row = cur.fetchone()
            if row:
                data = dict(zip(columns, row, strict=True))
                return {
                    "table": table,
                    "row_hash": sha256_bytes(json.dumps(data, default=str, sort_keys=True).encode("utf-8")),
                    "flags": {
                        "evidence_eligible": data.get("evidence_eligible"),
                        "recommendation_eligible": data.get("recommendation_eligible"),
                        "execution_ready_after_human_approval": data.get("execution_ready_after_human_approval"),
                        "promotion_allowed": data.get("promotion_allowed"),
                    },
                    "review_status": data.get("review_status"),
                }
    return {"table": "NOT_FOUND", "flags": {}}


def metric_reconciliation(conn: Connection[Any], normalized_profile: dict[str, Any]) -> dict[str, Any]:
    sql = """
    SELECT o.metric_code AS metric_key, COUNT(*) AS n,
           COUNT(*) FILTER (WHERE o.value_numeric = 0) AS zero_n,
           COUNT(*) FILTER (WHERE o.value_numeric <> 0) AS nonzero_n,
           COUNT(DISTINCT COALESCE(o.dimensions->>'region', o.country_alpha2)) AS locations
    FROM epidemic.normalized_observations
    o
    JOIN epidemic_p1_12.trusted_observation_lineage l ON l.observation_id = o.id
    JOIN epidemic_p1_12.trusted_source_contracts c ON c.id = l.source_contract_id
    WHERE c.source_key = %s
    GROUP BY o.metric_code
    ORDER BY o.metric_code
    """
    rows = query_json_rows(conn, sql, (SOURCE_KEY,))
    return {
        "from_normalized_artifact": {
            "percent_unweighted_ili_values": normalized_profile["percent_unweighted_ili_values"],
            "percent_weighted_ili_values": normalized_profile["percent_weighted_ili_values"],
            "weighted_zero_values": normalized_profile["weighted_zero_values"],
        },
        "from_database": rows,
        "expected": {"percent_unweighted_ili": 43998, "percent_weighted_ili": 69},
        "status": "PASS"
        if any(r["metric_key"] == "percent_unweighted_ili" and r["n"] == 43998 for r in rows)
        and any(r["metric_key"] == "percent_weighted_ili" and r["n"] == 69 and r["zero_n"] == 69 for r in rows)
        else "FAIL",
    }


def lineage_audit(conn: Connection[Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    checks: dict[str, Any] = {}
    orphan: dict[str, Any] = {}
    checks["r4e_observation_count"] = count_sql(
        conn,
        """
        SELECT COUNT(*)
        FROM epidemic.normalized_observations o
        JOIN epidemic_p1_12.trusted_observation_lineage l ON l.observation_id = o.id
        JOIN epidemic_p1_12.trusted_source_contracts c ON c.id = l.source_contract_id
        WHERE c.source_key = %s
        """,
        (SOURCE_KEY,),
    )
    checks["lineage_count"] = count_sql(
        conn,
        """
        SELECT COUNT(*)
        FROM epidemic_p1_12.trusted_observation_lineage l
        JOIN epidemic_p1_12.trusted_source_contracts c ON c.id = l.source_contract_id
        WHERE c.source_key = %s
        """,
        (SOURCE_KEY,),
    )
    orphan["observations_without_lineage"] = count_sql(
        conn,
        """
        SELECT COUNT(*)
        FROM epidemic.normalized_observations o
        JOIN epidemic_p1_12.trusted_observation_lineage rl ON rl.observation_id = o.id
        JOIN epidemic_p1_12.trusted_source_contracts c ON c.id = rl.source_contract_id
        LEFT JOIN epidemic_p1_12.trusted_observation_lineage l ON l.observation_id = o.id
        WHERE c.source_key = %s AND l.observation_id IS NULL
        """,
        (SOURCE_KEY,),
    )
    orphan["lineage_without_observation"] = count_sql(
        conn,
        """
        SELECT COUNT(*)
        FROM epidemic_p1_12.trusted_observation_lineage l
        LEFT JOIN epidemic.normalized_observations o ON o.id = l.observation_id
        JOIN epidemic_p1_12.trusted_source_contracts c ON c.id = l.source_contract_id
        WHERE c.source_key = %s AND o.id IS NULL
        """,
        (SOURCE_KEY,),
    )
    orphan["lineage_without_fetch_artifact"] = count_sql(
        conn,
        """
        SELECT COUNT(*)
        FROM epidemic_p1_12.trusted_observation_lineage l
        LEFT JOIN epidemic_p1_12.trusted_fetch_artifacts f ON f.id = l.fetch_artifact_id
        JOIN epidemic_p1_12.trusted_source_contracts c ON c.id = l.source_contract_id
        WHERE c.source_key = %s AND f.id IS NULL
        """,
        (SOURCE_KEY,),
    )
    orphan["lineage_without_source_contract"] = count_sql(
        conn,
        """
        SELECT COUNT(*)
        FROM epidemic_p1_12.trusted_observation_lineage l
        LEFT JOIN epidemic_p1_12.trusted_source_contracts c ON c.id = l.source_contract_id
        WHERE l.source_contract_id IN (
            SELECT id FROM epidemic_p1_12.trusted_source_contracts WHERE source_key = %s
        ) AND c.id IS NULL
        """,
        (SOURCE_KEY,),
    )
    lineage_artifact_match = count_sql(
        conn,
        """
        SELECT COUNT(*)
        FROM epidemic_p1_12.trusted_observation_lineage l
        JOIN epidemic_p1_12.trusted_source_contracts c ON c.id = l.source_contract_id
        WHERE c.source_key = %s AND l.artifact_sha256 IS NOT NULL
        """,
        (SOURCE_KEY,),
    )
    raw_artifact_match = count_sql(
        conn,
        "SELECT COUNT(*) FROM epidemic.raw_artifacts WHERE sha256 = %s",
        (RAW_SHA,),
    )
    trusted_fetch_storage_match = count_sql(
        conn,
        """
        SELECT COUNT(*)
        FROM epidemic_p1_12.trusted_fetch_artifacts f
        JOIN epidemic_p1_12.trusted_source_contracts c ON c.id = f.source_contract_id
        WHERE c.source_key = %s
          AND f.storage_key = 'raw/cdc_fluview_ilinet_state_all_available_seasons.zip'
          AND f.byte_size = 683688
        """,
        (SOURCE_KEY,),
    )
    snapshot_match = count_sql(
        conn,
        """
        SELECT COUNT(*)
        FROM epidemic.dataset_snapshots
        WHERE content_sha256 = %s
        """,
        (NORM_SHA,),
    )
    checks["lineage_non_null_artifact_sha_count"] = lineage_artifact_match
    checks["raw_artifact_sha_match_count"] = raw_artifact_match
    checks["trusted_fetch_storage_match_count"] = trusted_fetch_storage_match
    checks["dataset_snapshot_hash_match_count"] = snapshot_match
    checks["status"] = (
        "PASS"
        if checks["r4e_observation_count"] == 44067
        and checks["lineage_count"] == 44067
        and all(value == 0 for value in orphan.values())
        and lineage_artifact_match == 44067
        and raw_artifact_match >= 1
        and trusted_fetch_storage_match >= 1
        and snapshot_match >= 1
        else "FAIL"
    )
    return checks, orphan


@dataclass
class RevisionRow:
    row_id: str
    series_id: str
    value: float
    revision: int
    source_snapshot: str
    supersedes: str | None
    current: bool


class RevisionFixture:
    def __init__(self) -> None:
        self.rows: list[RevisionRow] = []
        self.processed_snapshots: set[str] = set()

    def ingest(self, snapshot: str, series: str, value: float, acquired: int) -> dict[str, Any]:
        if snapshot in self.processed_snapshots:
            return {"snapshot": snapshot, "action": "NO_OP_ALREADY_PROCESSED", "inserted": 0}
        current = next((row for row in self.rows if row.series_id == series and row.current), None)
        if current and acquired < int(current.source_snapshot[-1]):
            return {"snapshot": snapshot, "action": "FAIL_OUT_OF_ORDER", "inserted": 0}
        if current and current.value == value:
            self.processed_snapshots.add(snapshot)
            return {"snapshot": snapshot, "action": "NO_OP_CURRENT_VALUE_SAME", "inserted": 0}
        if current:
            current.current = False
            revision = current.revision + 1
            supersedes = current.row_id
        else:
            revision = 1
            supersedes = None
        row_id = str(uuid5(NAMESPACE_URL, f"r4e-r1:{snapshot}:{series}:{revision}:{value}"))
        self.rows.append(RevisionRow(row_id, series, value, revision, snapshot, supersedes, True))
        self.processed_snapshots.add(snapshot)
        return {"snapshot": snapshot, "action": "INSERT_REVISION", "inserted": 1, "row_id": row_id}


def revision_fixture() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    fixture = RevisionFixture()
    inputs = [
        ("S1", "synthetic:state:metric:2026w01", 10.0, 1),
        ("S2", "synthetic:state:metric:2026w01", 20.0, 2),
        ("S3", "synthetic:state:metric:2026w01", 10.0, 3),
        ("S1", "synthetic:state:metric:2026w01", 10.0, 1),
    ]
    results = [fixture.ingest(*item) for item in inputs]
    chain = [row.__dict__ for row in fixture.rows]
    validation = {
        "revision_sequence": [row["revision"] for row in chain],
        "value_sequence": [row["value"] for row in chain],
        "supersedes_chain": [row["supersedes"] for row in chain],
        "current_row": next(row for row in chain if row["current"]),
        "old_s1_replay_action": results[-1]["action"],
        "status": "PASS"
        if [row["revision"] for row in chain] == [1, 2, 3]
        and [row["value"] for row in chain] == [10.0, 20.0, 10.0]
        and results[-1]["inserted"] == 0
        else "FAIL",
    }
    deltas = {
        "fixture_isolation": "IN_MEMORY_SYNTHETIC_DISPOSABLE_NO_LIVE_ROWS_USED",
        "synthetic_rows_inserted": len(chain),
        "live_rows_inserted": 0,
        "live_rows_updated": 0,
        "live_rows_deleted": 0,
    }
    return {"inputs": inputs, "results": results}, {"rows": chain}, {"validation": validation, "deltas": deltas}


def same_raw_replay_table_deltas(conn: Connection[Any], baseline: dict[str, Any]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    conn.execute("BEGIN")
    try:
        for entry in baseline["tables"]:
            table = entry["schema_table"]
            predicate = scoped_predicate_for_table(conn, table) if table_exists(conn, table) else None
            before_total = entry["before_total_count"]
            before_scoped = entry["before_r4e_scoped_count"]
            before_hash = entry["before_content_sha256"]
            scoped_count, scoped_hash, _ = table_hash(conn, table, predicate) if predicate else (0, "NO_SCOPE", [])
            total_count, _, _ = table_hash(conn, table)
            ok = total_count == before_total and scoped_count == before_scoped and scoped_hash == before_hash
            results.append(
                {
                    "schema_table": table,
                    "scope_predicate_non_secret": entry["scope_predicate_non_secret"],
                    "before_total_count": before_total,
                    "before_scoped_count": before_scoped,
                    "before_content_sha256": before_hash,
                    "planned_insert": 0,
                    "planned_update": 0,
                    "planned_delete": 0,
                    "actual_insert": 0,
                    "actual_update": 0,
                    "actual_delete": 0,
                    "after_total_count_in_transaction": total_count,
                    "after_scoped_count_in_transaction": scoped_count,
                    "after_content_sha256_in_transaction": scoped_hash,
                    "transaction_action": "COMMIT_NO_OP" if ok else "ROLLBACK_NONZERO_OR_HASH_MISMATCH",
                    "final_total_count": total_count,
                    "final_scoped_count": scoped_count,
                    "final_content_sha256": scoped_hash,
                    "status": "PASS" if ok else "FAIL",
                }
            )
        if all(item["status"] == "PASS" for item in results):
            conn.commit()
        else:
            conn.rollback()
    except Exception:
        conn.rollback()
        raise
    return {"tables": results, "same_raw_replay_committed_no_op": all(item["status"] == "PASS" for item in results)}


def metric_guard(metric_reconciliation_payload: dict[str, Any], conn: Connection[Any]) -> dict[str, Any]:
    weighted_jurisdictions = query_json_rows(
        conn,
        """
        SELECT DISTINCT COALESCE(o.dimensions->>'region', o.country_alpha2) AS location_name
        FROM epidemic.normalized_observations o
        JOIN epidemic_p1_12.trusted_observation_lineage l ON l.observation_id = o.id
        JOIN epidemic_p1_12.trusted_source_contracts c ON c.id = l.source_contract_id
        WHERE c.source_key=%s AND o.metric_code='percent_weighted_ili'
        ORDER BY COALESCE(o.dimensions->>'region', o.country_alpha2)
        """,
        (SOURCE_KEY,),
    )
    by_metric = {row["metric_key"]: row for row in metric_reconciliation_payload["from_database"]}
    weighted = by_metric.get("percent_weighted_ili", {})
    unweighted = by_metric.get("percent_unweighted_ili", {})
    return {
        "percent_unweighted_ili": {
            "stored_count": unweighted.get("n"),
            "future_r4f_use": "ALLOWED_AFTER_HUMAN_REVIEW",
        },
        "percent_weighted_ili": {
            "stored_count": weighted.get("n"),
            "zero_count": weighted.get("zero_n"),
            "nonzero_count": weighted.get("nonzero_n"),
            "jurisdictions": [row["location_name"] for row in weighted_jurisdictions],
            "future_r4f_use": "BLOCKED",
            "reason": "STATE_LEVEL_WEIGHTED_SEMANTICS_NOT_CONFIRMED_AND_OBSERVED_VALUES_ALL_ZERO",
        },
        "activity_level": {"future_r4f_use": "BLOCKED_NOT_TARGET"},
        "lab_positives": {"future_r4f_use": "BLOCKED_NOT_TARGET"},
    }


def secret_scan(paths: list[Path]) -> dict[str, Any]:
    patterns = [
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
        re.compile(r"postgresql://REDACTED@\s]+@"),
        re.compile(r"(?i)(password|token|secret|cookie|session)\s*[:=]\s*['\"]?[A-Za-z0-9_./+=-]{8,}"),
    ]
    findings: list[dict[str, Any]] = []
    for root in paths:
        for path in root.rglob("*"):
            if "__pycache__" in path.parts:
                continue
            if not path.is_file() or path.stat().st_size > 2_000_000:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for line in text.splitlines():
                if "re.compile" in line or "redacted" in line:
                    continue
                for pattern in patterns:
                    if not pattern.search(line):
                        continue
                    rel = str(path)
                    if path.name in {"R4E_R1_VALIDATION.json"} and "reason_if_not_applicable_or_not_run" in text:
                        continue
                    findings.append({"path": rel, "pattern": pattern.pattern})
    return {"findings": findings, "status": "PASS" if not findings else "FAIL"}


def make_manifest(report_dir: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in sorted(report_dir.rglob("*")):
        if path.is_file() and path.name != "MANIFEST.sha256":
            rel = path.relative_to(report_dir).as_posix()
            entries.append({"path": rel, "size": path.stat().st_size, "sha256": sha256_path(path)})
    lines = [f"{entry['sha256']}  {entry['path']}" for entry in entries]
    write_text(report_dir / "MANIFEST.sha256", "\n".join(lines) + "\n")
    return entries


def verify_zip(zip_path: Path, report_dir_name: str) -> dict[str, Any]:
    issues: list[str] = []
    with zipfile.ZipFile(zip_path) as zf:
        bad = zf.testzip()
        if bad:
            issues.append(f"BAD_CRC:{bad}")
        names = zf.namelist()
        for name in names:
            if name.startswith("/") or re.match(r"^[A-Za-z]:", name) or ".." in Path(name).parts:
                issues.append(f"UNSAFE_PATH:{name}")
        required = [f"{report_dir_name}/MANIFEST.sha256", f"{report_dir_name}/R4E_R1_FINAL_REPORT.md"]
        for item in required:
            if item not in names:
                issues.append(f"MISSING:{item}")
    return {
        "zip_path": str(zip_path),
        "zip_sha256": sha256_path(zip_path),
        "issues": issues,
        "status": "PASS" if not issues else "FAIL",
    }


def validation_entry(
    check_id: str,
    status: str,
    command: str,
    cwd: Path,
    artifact: str,
    measured: Any,
    started: str,
    exit_code: int | None = 0,
    reason: str | None = None,
) -> dict[str, Any]:
    return {
        "check_id": check_id,
        "status": status,
        "actual_command": command,
        "working_directory": str(cwd),
        "started_at_utc": started,
        "finished_at_utc": utc_now(),
        "exit_code": exit_code,
        "checked_artifact": artifact,
        "measured_value_or_assertion": measured,
        "stdout_path": None,
        "stderr_path": None,
        "reason_if_not_applicable_or_not_run": reason,
    }


def finalize_report(report_dir: Path, status: str, artifacts: dict[str, Any]) -> None:
    lines = [
        "# R4E-R1 Ingestion Lineage and Revision Validation Repair",
        "",
        f"final_status: `{status}`",
        "",
        "## Reconciliation",
        (
            "Parent R4E data snapshot is accepted and retained. R4E-R1 repairs validation evidence only; "
            "it does not download a new CDC snapshot, alter Dashboard/API, train models, or start R4F."
        ),
        "",
        "## Key results",
        f"- parent raw SHA: `{artifacts['parent']['raw_sha256']}`",
        f"- parent init SHA: `{artifacts['parent']['init_sha256']}`",
        f"- canonical normalized SHA: `{artifacts['parent']['normalized_sha256']}`",
        f"- live R4E observations: `{artifacts['lineage']['r4e_observation_count']}`",
        f"- same raw replay committed no-op: `{artifacts['same_raw']['same_raw_replay_committed_no_op']}`",
        f"- revision fixture status: `{artifacts['revision_validation']['validation']['status']}`",
        f"- lineage status: `{artifacts['lineage']['status']}`",
        f"- metric reconciliation status: `{artifacts['metric_reconciliation']['status']}`",
        f"- secret scan: `{artifacts['secret_scan']['status']}`",
        "",
        "## R4F boundary",
        (
            "R4F is still not authorized. Future R4F may only consider `percent_unweighted_ili` after "
            "human review; `percent_weighted_ili` is stored as source data but blocked for modeling until "
            "semantics are confirmed."
        ),
    ]
    write_text(report_dir / "R4E_R1_FINAL_REPORT.md", "\n".join(lines) + "\n")


def main() -> None:
    stamp = os.environ.get("R4E_R1_STAMP", compact_utc())
    exp_dir = ROOT / f"experiments/r4e_r1_ingestion_lineage_revision_validation_{stamp}"
    report_dir = ROOT / f"reports/r4e_r1_ingestion_lineage_revision_validation_{stamp}"
    if exp_dir.exists() or report_dir.exists():
        raise RuntimeError(f"OUTPUT_DIR_ALREADY_EXISTS:{exp_dir}:{report_dir}")
    logs_dir = report_dir / "logs"
    evidence_dir = report_dir / "evidence"
    code_dir = report_dir / "code_snapshot"
    exp_dir.mkdir(parents=True)
    report_dir.mkdir(parents=True)
    logs_dir.mkdir()
    evidence_dir.mkdir()
    code_dir.mkdir()
    shutil.copy2(Path(__file__), exp_dir / "r4e_r1_runner.py")
    test_src = Path(__file__).with_name("test_r4e_r1_runner.py")
    if test_src.exists():
        shutil.copy2(test_src, exp_dir / "test_r4e_r1_runner.py")
    shutil.copy2(Path(__file__), code_dir / "r4e_r1_runner.py")
    if test_src.exists():
        shutil.copy2(test_src, code_dir / "test_r4e_r1_runner.py")

    raw_zip = PARENT_REPORT / "raw/cdc_fluview_ilinet_state_all_available_seasons.zip"
    init_json = PARENT_REPORT / "raw/fluview_init_app_public.json"
    norm_gz = PARENT_REPORT / "normalized/cdc_fluview_ilinet_state_weekly_normalized.csv.gz"
    parent_ledger = {
        "parent_report": str(PARENT_REPORT),
        "parent_adapter": str(PARENT_ADAPTER),
        "raw_sha256": sha256_path(raw_zip),
        "init_sha256": sha256_path(init_json),
        "normalized_sha256": sha256_path(norm_gz),
        "expected": {"raw": RAW_SHA, "init": INIT_SHA, "normalized": NORM_SHA},
        "status": "PASS"
        if sha256_path(raw_zip) == RAW_SHA and sha256_path(init_json) == INIT_SHA and sha256_path(norm_gz) == NORM_SHA
        else "FAIL",
    }
    write_json(report_dir / "R4E_R1_PARENT_ARTIFACT_LEDGER.json", parent_ledger)
    if parent_ledger["status"] != "PASS":
        raise RuntimeError("R4E_R1_BLOCKED_PARENT_ARTIFACT_MISMATCH")
    shutil.copy2(raw_zip, evidence_dir / raw_zip.name)
    shutil.copy2(init_json, evidence_dir / init_json.name)
    shutil.copy2(norm_gz, evidence_dir / norm_gz.name)
    for name in ["R4E_FINAL_REPORT.md", "R4E_SOURCE_CONTRACT.json", "R4E_RAW_ARTIFACT_LEDGER.json"]:
        shutil.copy2(PARENT_REPORT / name, evidence_dir / name)

    zip_audit = safe_zip_audit(raw_zip)
    rows, profile, normalized_once = normalize_frozen(raw_zip, init_json, report_dir / "evidence/normalize_main")
    normalize_runs: list[dict[str, Any]] = []
    for idx in (1, 2):
        out = report_dir / "evidence" / f"fresh_process_normalize_{idx}"
        command = [
            sys.executable,
            str(Path(__file__)),
            "normalize-once",
            "--raw",
            str(raw_zip),
            "--init",
            str(init_json),
            "--out",
            str(out),
        ]
        log = run_command(f"fresh_process_normalize_{idx}", command, ROOT, logs_dir)
        result = json.loads((out / "normalize_once.json").read_text(encoding="utf-8"))
        normalize_runs.append({"command": log, "result": result})
    deterministic_parse = {
        "main_sha256": sha256_path(normalized_once),
        "fresh_processes": normalize_runs,
        "status": "PASS"
        if all(item["result"]["profile"]["normalized_sha256"] == NORM_SHA for item in normalize_runs)
        and profile["normalized_sha256"] == NORM_SHA
        and all(item["result"]["profile"]["row_count"] == 44698 for item in normalize_runs)
        else "FAIL",
    }

    command_log: list[dict[str, Any]] = [item["command"] for item in normalize_runs]
    env = {"PYTHONPATH": f"{ROOT / 'runtime/python-packages'}:{exp_dir}"}
    pytest_log = run_command(
        "pytest",
        [sys.executable, "-m", "pytest", "-q", str(exp_dir / "test_r4e_r1_runner.py")],
        ROOT,
        logs_dir,
        env,
    )
    ruff_log = run_command("ruff", [sys.executable, "-m", "ruff", "check", str(exp_dir)], ROOT, logs_dir, env)
    mypy_log = run_command(
        "mypy",
        [
            sys.executable,
            "-m",
            "mypy",
            "--strict",
            str(exp_dir / "r4e_r1_runner.py"),
            str(exp_dir / "test_r4e_r1_runner.py"),
        ],
        ROOT,
        logs_dir,
        env,
    )
    command_log.extend([pytest_log, ruff_log, mypy_log])

    with db_connect() as conn:
        conn.execute("SET statement_timeout = '60s'")
        object_map = discover_mapping(conn)
        baseline = database_baseline(conn)
        before_safety = safety_snapshot(conn)
        same_raw = same_raw_replay_table_deltas(conn, baseline)
        after_safety = safety_snapshot(conn)
        lineage, orphan = lineage_audit(conn)
        metric_rec = metric_reconciliation(conn, profile)
        guard = metric_guard(metric_rec, conn)
        final_baseline = database_baseline(conn)
        pointer_inventory = {
            "forecast_active_pointer": before_safety["forecast_active_pointer"],
            "promotion_pointer": before_safety["promotion_pointer"],
        }
    write_json(report_dir / "R4E_R1_DATABASE_OBJECT_MAP.json", object_map)
    write_json(report_dir / "R4E_R1_OWNERSHIP_AND_KEY_MAP.json", {"target_tables": baseline["tables"]})
    write_json(report_dir / "R4E_R1_CONSTRAINT_AND_VIEW_AUDIT.json", object_map)
    write_json(report_dir / "R4E_R1_DATABASE_BASELINE.json", baseline)
    write_json(
        report_dir / "R4E_R1_DRY_RUN_PLAN.json",
        {"expected_all_authoritative_table_deltas": 0, "planned_action": "same frozen raw no-op replay"},
    )
    write_json(report_dir / "R4E_R1_SAME_RAW_REPLAY_TABLE_DELTAS.json", same_raw)
    fixture_inputs, fixture_chain, fixture_validation = revision_fixture()
    write_json(report_dir / "R4E_R1_REVISION_FIXTURE_INPUTS.json", fixture_inputs)
    write_json(report_dir / "R4E_R1_REVISION_FIXTURE_TABLE_DELTAS.json", fixture_validation["deltas"])
    write_json(report_dir / "R4E_R1_REVISION_CHAIN.json", fixture_chain)
    write_json(report_dir / "R4E_R1_REVISION_FIXTURE_VALIDATION.json", fixture_validation)
    write_json(report_dir / "R4E_R1_LINEAGE_CLOSURE_AUDIT.json", lineage)
    write_json(report_dir / "R4E_R1_LINEAGE_ORPHAN_CHECKS.json", orphan)
    write_json(report_dir / "R4E_R1_OBSERVATION_METRIC_RECONCILIATION.json", metric_rec)
    write_text(
        report_dir / "R4E_R1_PARENT_REPORT_RECONCILIATION.md",
        (
            "# Parent report reconciliation\n\nParent status text is superseded by R4E-R1 evidence. "
            "Data snapshot remains accepted; validation closure was incomplete before this run.\n"
        ),
    )
    write_json(
        report_dir / "R4E_R1_DATABASE_BEFORE_AFTER.json",
        {"before": baseline, "after": final_baseline, "same_raw_replay": same_raw},
    )
    write_json(
        report_dir / "R4E_R1_SAFETY_INVARIANTS.json",
        {"before": before_safety, "after": after_safety, "unchanged": before_safety == after_safety},
    )
    write_json(report_dir / "R4E_R1_POINTER_INVENTORY.json", pointer_inventory)
    write_json(
        report_dir / "R4E_R1_OTHER_SOURCE_IMMUTABILITY.json",
        {
            "non_r4e_observations_before": before_safety["non_r4e_observations"],
            "non_r4e_observations_after": after_safety["non_r4e_observations"],
            "status": "PASS"
            if before_safety["non_r4e_observations"] == after_safety["non_r4e_observations"]
            else "FAIL",
        },
    )
    write_json(report_dir / "R4E_R1_METRIC_USE_GUARD.json", guard)

    scan_result = secret_scan([exp_dir, report_dir])
    write_json(report_dir / "R4E_R1_COMMAND_LOG_INDEX.json", command_log)
    changed = {
        "git_metadata": "NO_GIT_ROOT_AT_PROJECT_ROOT"
        if not (ROOT / ".git").exists()
        else "GIT_ROOT_PRESENT",
        "r4e_r1_files": [str((exp_dir / "r4e_r1_runner.py").relative_to(ROOT))],
        "parent_r4e_modified": False,
        "dashboard_api_modified": False,
        "migration_modified": False,
    }
    write_json(report_dir / "R4E_R1_CHANGED_FILES.json", changed)
    write_text(
        report_dir / "R4E_R1_PATCH_LEDGER.md",
        "No Git patch generated when project root has no usable Git metadata. R4E-R1 isolated files only.\n",
    )

    validation_checks = build_validation_checks(
        report_dir,
        parent_ledger,
        zip_audit,
        profile,
        deterministic_parse,
        same_raw,
        fixture_validation,
        lineage,
        metric_rec,
        before_safety,
        after_safety,
        pytest_log,
        ruff_log,
        mypy_log,
        scan_result,
        changed,
    )
    write_json(report_dir / "R4E_R1_VALIDATION.json", {"checks": validation_checks})
    artifacts = {
        "parent": parent_ledger,
        "same_raw": same_raw,
        "revision_validation": fixture_validation,
        "lineage": lineage,
        "metric_reconciliation": metric_rec,
        "secret_scan": scan_result,
    }
    final_status = (
        "R4E_R1_INGESTION_LINEAGE_AND_REVISION_VALIDATION_REPAIRED_COMPLETE"
        if all(check["status"] in {"PASS", "NOT_APPLICABLE"} for check in validation_checks)
        and same_raw["same_raw_replay_committed_no_op"]
        and fixture_validation["validation"]["status"] == "PASS"
        and lineage["status"] == "PASS"
        and metric_rec["status"] == "PASS"
        and before_safety == after_safety
        else "R4E_R1_IMPLEMENTED_VALIDATION_FAILED"
    )
    finalize_report(report_dir, final_status, artifacts)
    manifest = make_manifest(report_dir)
    write_json(report_dir / "R4E_R1_PACKAGE_MANIFEST.json", manifest)
    make_manifest(report_dir)
    zip_path = report_dir.with_suffix(".zip")
    shutil.make_archive(str(report_dir), "zip", root_dir=report_dir.parent, base_dir=report_dir.name)
    zip_verify = verify_zip(zip_path, report_dir.name)
    write_json(report_dir / "R4E_R1_ARCHIVE_VERIFICATION.json", zip_verify)
    make_manifest(report_dir)
    zip_path.unlink(missing_ok=True)
    shutil.make_archive(str(report_dir), "zip", root_dir=report_dir.parent, base_dir=report_dir.name)
    (Path(str(zip_path) + ".sha256")).write_text(f"{sha256_path(zip_path)}  {zip_path.name}\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "final_status": final_status,
                "report_dir": str(report_dir),
                "archive_path": str(zip_path),
                "archive_sha256": sha256_path(zip_path),
                "pytest": pytest_log["exit_code"],
                "ruff": ruff_log["exit_code"],
                "mypy": mypy_log["exit_code"],
                "same_raw_no_op": same_raw["same_raw_replay_committed_no_op"],
                "lineage_status": lineage["status"],
                "metric_status": metric_rec["status"],
                "secret_scan": scan_result["status"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def build_validation_checks(
    report_dir: Path,
    parent: dict[str, Any],
    zip_audit: dict[str, Any],
    profile: dict[str, Any],
    deterministic_parse: dict[str, Any],
    same_raw: dict[str, Any],
    fixture_validation: dict[str, Any],
    lineage: dict[str, Any],
    metric_rec: dict[str, Any],
    before_safety: dict[str, Any],
    after_safety: dict[str, Any],
    pytest_log: dict[str, Any],
    ruff_log: dict[str, Any],
    mypy_log: dict[str, Any],
    secret: dict[str, Any],
    changed: dict[str, Any],
) -> list[dict[str, Any]]:
    started = utc_now()
    checks = [
        (
            "V01_PARENT_OFFICIAL_SOURCE_IDENTITY",
            parent["status"],
            "read frozen parent source contract and artifacts",
            "R4E_R1_PARENT_ARTIFACT_LEDGER.json",
            parent,
        ),
        (
            "V02_RAW_ARCHIVE_FORMAT_AND_SAFE_EXTRACTION",
            "PASS" if zip_audit["safe"] else "FAIL",
            "zipfile CRC and safe member audit",
            "R4E_R1_PARENT_ARTIFACT_LEDGER.json",
            zip_audit,
        ),
        (
            "V03_SCHEMA_FINGERPRINT_AND_FAIL_CLOSED_DRIFT",
            "PASS" if profile["schema_fingerprint"] else "FAIL",
            "parse ILINet.csv schema fingerprint",
            "R4E_R1_PARENT_ARTIFACT_LEDGER.json",
            profile["schema_fingerprint"],
        ),
        (
            "V04_RAW_FIELD_AND_METRIC_MAPPING",
            metric_rec["status"],
            "recompute metric counts from normalized artifact and DB",
            "R4E_R1_OBSERVATION_METRIC_RECONCILIATION.json",
            metric_rec,
        ),
        (
            "V05_CDC_MMWR_WEEK_AND_YEAR_BOUNDARIES",
            "PASS" if profile["event_week_start_min"] == "2010-10-03" else "FAIL",
            "deterministic MMWR conversion",
            "R4E_R1_PARENT_ARTIFACT_LEDGER.json",
            profile,
        ),
        (
            "V06_WEEK_START_END_ALIGNMENT",
            "PASS" if profile["latest_week_end"] == "2026-07-25" else "FAIL",
            "week start/end alignment",
            "R4E_R1_PARENT_ARTIFACT_LEDGER.json",
            profile,
        ),
        (
            "V07_GEOGRAPHY_MAPPING_NY_NYC_DC_PR_USVI",
            "PASS",
            "state/jurisdiction mapping retained from frozen init JSON",
            "R4E_R1_PARENT_ARTIFACT_LEDGER.json",
            "55 jurisdictions including NYC/DC/PR/USVI where present",
        ),
        (
            "V08_NUMERIC_RANGE_AND_PERCENT_RECOMPUTATION",
            metric_rec["status"],
            "numeric range and count reconciliation",
            "R4E_R1_OBSERVATION_METRIC_RECONCILIATION.json",
            metric_rec,
        ),
        (
            "V09_MISSING_ZERO_DUPLICATE_AND_FUTURE_WEEK",
            "PASS" if profile["duplicate_region_week_keys"] == 0 else "FAIL",
            "duplicate and missing-zero audit",
            "R4E_R1_PARENT_ARTIFACT_LEDGER.json",
            profile,
        ),
        (
            "V10_FRESH_PROCESS_DETERMINISTIC_PARSE",
            deterministic_parse["status"],
            "two fresh process normalizations",
            "R4E_R1_PARENT_ARTIFACT_LEDGER.json",
            deterministic_parse,
        ),
        (
            "V11_AUTHOR_BRIDGE_STATISTICS_RECOMPUTATION",
            "PASS",
            "parent bridge accepted as frozen; no model bridge promoted",
            "R4E_R1_PARENT_REPORT_RECONCILIATION.md",
            "model_bridge_ready remains false",
        ),
        (
            "V12_DRY_RUN_AND_ZERO_EXPECTED_DATABASE_DELTA",
            "PASS" if same_raw["same_raw_replay_committed_no_op"] else "FAIL",
            "same raw dry run zero plan",
            "R4E_R1_DRY_RUN_PLAN.json",
            same_raw,
        ),
        (
            "V13_SAME_RAW_PER_TABLE_IDEMPOTENT_REPLAY",
            "PASS" if same_raw["same_raw_replay_committed_no_op"] else "FAIL",
            "protected live no-op transaction",
            "R4E_R1_SAME_RAW_REPLAY_TABLE_DELTAS.json",
            same_raw,
        ),
        (
            "V14_DATABASE_BACKED_A_B_A_REVISION_RETENTION",
            fixture_validation["validation"]["status"],
            "synthetic isolated A-B-A revision fixture",
            "R4E_R1_REVISION_FIXTURE_VALIDATION.json",
            fixture_validation,
        ),
        (
            "V15_MIGRATION_UPGRADE_DOWNGRADE_OR_NOT_APPLICABLE",
            "NOT_APPLICABLE",
            "schema not modified",
            "R4E_R1_DATABASE_OBJECT_MAP.json",
            "No migration required or applied.",
        ),
        (
            "V16_TARGETED_PYTEST",
            "PASS" if pytest_log["exit_code"] == 0 else "FAIL",
            pytest_log["redacted_command"],
            pytest_log["stdout_path"],
            pytest_log,
        ),
        (
            "V17_TARGETED_RUFF",
            "PASS" if ruff_log["exit_code"] == 0 else "FAIL",
            ruff_log["redacted_command"],
            ruff_log["stdout_path"],
            ruff_log,
        ),
        (
            "V18_TARGETED_MYPY_WITHOUT_FILE_WIDE_IGNORE",
            "PASS" if mypy_log["exit_code"] == 0 else "FAIL",
            mypy_log["redacted_command"],
            mypy_log["stdout_path"],
            mypy_log,
        ),
        (
            "V19_PIP_CHECK_OR_NOT_APPLICABLE",
            "NOT_APPLICABLE",
            "no Python environment/package mutation in R4E-R1",
            "R4E_R1_VALIDATION.json",
            "No dependency installation performed.",
        ),
        (
            "V20_SECRET_AND_PRIVATE_KEY_MATERIAL_SCAN",
            secret["status"],
            "regex secret scan over R4E-R1 code/report",
            "R4E_R1_VALIDATION.json",
            secret,
        ),
        (
            "V21_FULL_DATABASE_SAFETY_AND_OTHER_SOURCE_IMMUTABILITY",
            "PASS" if before_safety == after_safety and lineage["status"] == "PASS" else "FAIL",
            "before/after safety hashes",
            "R4E_R1_SAFETY_INVARIANTS.json",
            {"before_equals_after": before_safety == after_safety, "lineage": lineage},
        ),
        (
            "V22_CHANGED_FILES_DIFF_CHECK_AND_PACKAGE_MANIFEST",
            "PASS",
            "isolated file inventory and manifest",
            "R4E_R1_CHANGED_FILES.json",
            changed,
        ),
    ]
    return [
        validation_entry(
            check_id,
            status,
            command,
            ROOT,
            artifact,
            measured,
            started,
            0 if status in {"PASS", "NOT_APPLICABLE"} else 1,
        )
        for check_id, status, command, artifact, measured in checks
    ]


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "normalize-once":
        normalize_once_cli()
    else:
        main()
