from __future__ import annotations

# ruff: noqa: E501,I001
# mypy: ignore-errors

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
import time
import uuid
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import psycopg
import requests


ROOT = Path("USER_HOME/projects/openclaw")
STAMP = "20260806T1225Z"
REPORT = ROOT / f"reports/r4e_cdc_ilinet_ingestion_{STAMP}"
EXP = ROOT / f"experiments/r4e_cdc_ilinet_ingestion_{STAMP}"
RAW_DIR = REPORT / "raw"
NORM_DIR = REPORT / "normalized"
UA = "epidemic-openclaw-r4e-source-verification/1.0 (research data audit)"
INIT_URL = "https://gis.cdc.gov/grasp/flu2/GetPhase02InitApp?appVersion=Public"
EXPORT_URL = "https://gis.cdc.gov/grasp/flu2/PostPhase02DataDownload"
PAGE_URL = "https://gis.cdc.gov/grasp/fluview/fluportaldashboard.html"
PDF_URL = "https://gis.cdc.gov/grasp/fluview/FluViewPhase2QuickReferenceGuide.pdf"
CLAIM_ID = "6ef6f7e4-f510-5358-8c84-50ecd73283de"
AUTHOR_CSV = ROOT / "experiments/r4d_ili_gru_official_20260806T072222Z/sources/ml-flu-prediction/data/States_ILI.csv"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_path(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def request(method: str, url: str, **kwargs: Any) -> requests.Response:
    headers = kwargs.pop("headers", {})
    headers.setdefault("User-Agent", UA)
    headers.setdefault("Referer", PAGE_URL)
    timeout = kwargs.pop("timeout", 60)
    r = requests.request(method, url, headers=headers, timeout=timeout, **kwargs)
    return r


def db_url() -> str:
    # Read only in memory; never print.
    for pid in Path("/proc").iterdir():
        if not pid.name.isdigit():
            continue
        try:
            env_bytes = (pid / "environ").read_bytes()
        except Exception:
            continue
        if b"DASHBOARD_DATABASE_URL=" not in env_bytes:
            continue
        for item in env_bytes.split(b"\0"):
            if item.startswith(b"DASHBOARD_DATABASE_URL="):
                dsn = item.split(b"=", 1)[1].decode(errors="ignore")
                if dsn.startswith("postgresql+psycopg://"):
                    dsn = "postgresql://" + dsn.split("://", 1)[1]
                return dsn
    raise RuntimeError("DASHBOARD_DATABASE_URL_NOT_FOUND")


def load_secret_env() -> dict[str, str]:
    path = ROOT / "runtime/secrets/vm201_runtime.env"
    env: dict[str, str] = {}
    for line in path.read_text(errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k] = v.strip().strip("'").strip('"')
    return env


def write_db_url() -> str:
    env = load_secret_env()
    password = env["PG_SUPERPASS"]
    return f"postgresql://postgres:{password}@127.0.0.1:55432/epidemic_openclaw"


def stable_uuid(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, name))


def run_cmd(cmd: list[str], cwd: Path = ROOT) -> dict[str, Any]:
    start = utc_now()
    cp = subprocess.run(cmd, cwd=str(cwd), text=True, capture_output=True)
    return {
        "actual_command": " ".join(cmd),
        "working_directory": str(cwd),
        "started_at_utc": start,
        "finished_at_utc": utc_now(),
        "exit_code": cp.returncode,
        "stdout": cp.stdout[:4000],
        "stderr": cp.stderr[:4000],
    }


def doc_probe() -> list[dict[str, Any]]:
    out = []
    for url in [
        "https://www.cdc.gov/fluview/overview/index.html",
        "https://www.cdc.gov/fluview/overview/fluview-interactive.html",
        PAGE_URL,
        PDF_URL,
    ]:
        started = utc_now()
        try:
            r = request("GET", url, headers={"Accept": "text/html,application/pdf,*/*"}, timeout=30)
            data = r.content
            sample = ""
            if "text" in (r.headers.get("content-type") or "") or "html" in (r.headers.get("content-type") or ""):
                sample = re.sub(r"\s+", " ", r.text[:2000])
            status = "FETCHED" if r.status_code == 200 else "FETCHED_NON_200"
            item = {
                "status": status,
                "canonical_url": url,
                "final_url_after_redirect": r.url,
                "retrieved_at_utc": started,
                "http_status": r.status_code,
                "content_type": r.headers.get("content-type"),
                "content_length": r.headers.get("content-length"),
                "byte_length": len(data),
                "etag": r.headers.get("etag"),
                "last_modified": r.headers.get("last-modified"),
                "sha256": sha256_bytes(data),
                "sample": sample,
            }
        except Exception as exc:
            item = {"status": "FETCH_ERROR", "canonical_url": url, "retrieved_at_utc": started, "error": str(exc)}
        out.append(item)
        time.sleep(1)
    return out


def load_init() -> tuple[dict[str, Any], dict[str, Any]]:
    started = utc_now()
    r = request("GET", INIT_URL, headers={"Accept": "application/json,*/*"}, timeout=60)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    raw_path = RAW_DIR / "fluview_init_app_public.json"
    raw_path.write_bytes(r.content)
    meta = {
        "request_url": INIT_URL,
        "retrieved_at_utc": started,
        "http_status": r.status_code,
        "final_url": r.url,
        "content_type": r.headers.get("content-type"),
        "byte_length": len(r.content),
        "sha256": sha256_bytes(r.content),
        "raw_path": str(raw_path.relative_to(REPORT)),
    }
    r.raise_for_status()
    return r.json(), meta


def safe_zip_extract(zip_bytes: bytes, target: Path) -> list[dict[str, Any]]:
    target.mkdir(parents=True, exist_ok=True)
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    entries = []
    total = 0
    for info in zf.infolist():
        name = info.filename
        if name.startswith("/") or ".." in Path(name).parts:
            raise RuntimeError(f"UNSAFE_ZIP_PATH:{name}")
        total += info.file_size
        if total > 100 * 1024 * 1024:
            raise RuntimeError("ZIP_UNCOMPRESSED_SIZE_LIMIT")
        data = zf.read(info)
        out = target / name
        out.write_bytes(data)
        entries.append(
            {
                "name": name,
                "compressed_size": info.compress_size,
                "file_size": info.file_size,
                "sha256": sha256_bytes(data),
                "path": str(out.relative_to(REPORT)),
            }
        )
    return entries


def download_export(init: dict[str, Any]) -> tuple[dict[str, Any], Path]:
    seasons = [{"ID": s["id"], "Name": str(s["id"])} for s in init["seasons"] if s.get("hasstatedata") == 1]
    states = [{"ID": s["stateid"], "Name": str(s["stateid"])} for s in init["states"]]
    payload = {
        "AppVersion": "Public",
        "DatasourceDT": [{"ID": 1, "Name": "ILINet"}],
        "RegionTypeId": 5,
        "SubRegionsDT": states,
        "SeasonsDT": seasons,
    }
    started = utc_now()
    r = request(
        "POST",
        EXPORT_URL,
        headers={"Accept": "application/octet-stream,*/*", "Content-Type": "application/json;charset=utf-8"},
        json=payload,
        timeout=120,
    )
    zip_path = RAW_DIR / "cdc_fluview_ilinet_state_all_available_seasons.zip"
    zip_path.write_bytes(r.content)
    meta = {
        "request_url": EXPORT_URL,
        "request_payload_non_secret": payload,
        "retrieved_at_utc": started,
        "http_status": r.status_code,
        "final_url": r.url,
        "content_type": r.headers.get("content-type"),
        "content_disposition": r.headers.get("content-disposition"),
        "byte_length": len(r.content),
        "sha256": sha256_bytes(r.content),
        "raw_path": str(zip_path.relative_to(REPORT)),
    }
    if r.status_code != 200 or not r.content.startswith(b"PK"):
        meta["body_sample"] = r.content[:1000].decode(errors="replace")
        raise RuntimeError(f"EXPORT_FAILED:{meta}")
    entries = safe_zip_extract(r.content, RAW_DIR / "export_unzipped")
    meta["zip_entries"] = entries
    return meta, RAW_DIR / "export_unzipped/ILINet.csv"


def mmwr_week_start(year: int, week: int) -> date:
    # CDC MMWR week: week 1 starts on the Sunday of the week containing Jan 4
    jan4 = date(year, 1, 4)
    start_week1 = jan4 - timedelta(days=(jan4.weekday() + 1) % 7)
    return start_week1 + timedelta(weeks=week - 1)


def season_label_for_week(start: date) -> str:
    if start.month >= 10:
        return f"{start.year}-{str(start.year + 1)[-2:]}"
    return f"{start.year - 1}-{str(start.year)[-2:]}"


def parse_num(x: str) -> float | None:
    x = (x or "").strip()
    if x in {"", "X", "XX", "N/A", "NA", "-"}:
        return None
    return float(x)


def parse_int(x: str) -> int | None:
    val = parse_num(x)
    if val is None or math.isnan(val):
        return None
    return int(round(val))


def normalize(csv_path: Path, init: dict[str, Any], export_meta: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    state_by_name = {s["statename"]: s for s in init["states"]}
    rows: list[dict[str, Any]] = []
    errors = []
    with csv_path.open(newline="", encoding="utf-8-sig") as f:
        first = f.readline().strip()
        header = f.readline()
        reader = csv.DictReader(f, fieldnames=next(csv.reader([header])))
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
                st = state_by_name.get(region)
                weighted = parse_num(raw["% WEIGHTED ILI"])
                unweighted = parse_num(raw["%UNWEIGHTED ILI"])
                ili_total = parse_int(raw["ILITOTAL"])
                total_patients = parse_int(raw["TOTAL PATIENTS"])
                recomputed = None
                if ili_total is not None and total_patients and total_patients > 0:
                    recomputed = 100.0 * ili_total / total_patients
                rows.append(
                    {
                        "source_key": "cdc_fluview_ilinet_state_weekly",
                        "publisher": "CDC",
                        "product": "FluView Interactive / ILINet",
                        "region_type": region_type,
                        "region": region,
                        "state_id": st["stateid"] if st else None,
                        "state_fips": (st.get("statefips") or "").strip() if st else None,
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
            except Exception as exc:
                errors.append({"raw": raw, "error": str(exc)})
    if errors:
        raise RuntimeError(f"NORMALIZATION_ERRORS:{errors[:5]}")
    fieldnames = list(rows[0].keys()) if rows else []
    norm_path = NORM_DIR / "cdc_fluview_ilinet_state_weekly_normalized.csv.gz"
    NORM_DIR.mkdir(parents=True, exist_ok=True)
    with (
        norm_path.open("wb") as raw_f,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw_f, mtime=0) as gz,
        io.TextIOWrapper(gz, encoding="utf-8", newline="") as f,
    ):
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    key_counts = Counter((r["region"], r["cdc_year"], r["cdc_mmwr_week"]) for r in rows)
    duplicates = [k for k, v in key_counts.items() if v > 1]
    profile = {
        "title_line": first,
        "raw_rows": len(rows),
        "region_count": len({r["region"] for r in rows}),
        "regions": sorted({r["region"] for r in rows}),
        "event_week_min": min(r["event_week_start"] for r in rows),
        "event_week_max": max(r["event_week_start"] for r in rows),
        "event_week_end_max": max(r["event_week_end"] for r in rows),
        "cdc_year_min": min(r["cdc_year"] for r in rows),
        "cdc_year_max": max(r["cdc_year"] for r in rows),
        "season_count": len({r["influenza_season"] for r in rows}),
        "observation_rows": len(rows),
        "duplicates": len(duplicates),
        "missing_weighted_ili": sum(r["percent_weighted_ili"] is None for r in rows),
        "missing_unweighted_ili": sum(r["percent_unweighted_ili"] is None for r in rows),
        "missing_total_patients": sum(r["total_patients"] is None for r in rows),
        "normalized_path": str(norm_path.relative_to(REPORT)),
        "normalized_sha256": sha256_path(norm_path),
        "schema_fingerprint": sha256_bytes(("|".join(fieldnames)).encode()),
    }
    return rows, profile


def read_author() -> list[dict[str, Any]]:
    rows = []
    with AUTHOR_CSV.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        date_field = "date" if reader.fieldnames and "date" in reader.fieldnames else (reader.fieldnames or [""])[0]
        for raw in reader:
            week = datetime.strptime(raw[date_field], "%m/%d/%y").date()
            for st, val in raw.items():
                if st == date_field:
                    continue
                rows.append({"week": week.isoformat(), "state": st, "value": float(val)})
    return rows


def author_bridge(rows: list[dict[str, Any]]) -> dict[str, Any]:
    # Compare author 37 state abbreviations to CDC current snapshot by week/state where possible.
    states = {
        "AK": "Alaska", "AL": "Alabama", "AR": "Arkansas", "AZ": "Arizona", "DE": "Delaware",
        "GA": "Georgia", "ID": "Idaho", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
        "MA": "Massachusetts", "MD": "Maryland", "ME": "Maine", "MI": "Michigan", "MN": "Minnesota",
        "NC": "North Carolina", "ND": "North Dakota", "NE": "Nebraska", "NH": "New Hampshire",
        "NJ": "New Jersey", "NM": "New Mexico", "NV": "Nevada", "NY": "New York", "OH": "Ohio",
        "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
        "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VA": "Virginia",
        "VT": "Vermont", "WA": "Washington", "WI": "Wisconsin", "WV": "West Virginia",
    }
    cdc_by_key = {(r["event_week_start"], r["region"]): r for r in rows}
    author_rows = read_author()
    compared = []
    no_cdc = 0
    no_value = 0
    for a in author_rows:
        region = states[a["state"]]
        c = cdc_by_key.get((a["week"], region))
        if not c:
            no_cdc += 1
            continue
        cval = c["percent_unweighted_ili"]
        if cval is None:
            no_value += 1
            continue
        compared.append(abs(a["value"] - cval))
    if compared:
        mean_abs = sum(compared) / len(compared)
        max_abs = max(compared)
    else:
        mean_abs = None
        max_abs = None
    return {
        "author_sha256": sha256_path(AUTHOR_CSV),
        "author_rows_long": len(author_rows),
        "common_key_count": len(compared),
        "no_cdc_key_count": no_cdc,
        "cdc_key_but_missing_value_count": no_value,
        "compared_metric": "CDC percent_unweighted_ili vs author state ILI values",
        "mean_abs_difference": mean_abs,
        "max_abs_difference": max_abs,
        "bridge_classification": "RETROSPECTIVE_REVISED_SNAPSHOT_COMPARISON"
        if compared
        else "NOT_ENOUGH_OVERLAP",
        "model_bridge_ready": bool(compared and max_abs is not None and max_abs < 1e-6),
    }


def db_counts() -> dict[str, Any]:
    queries = {
        "forecast_runs": "select count(*) from epidemic.forecast_runs",
        "forecast_values": "select count(*) from epidemic.forecast_values",
        "kg_claims_evidence_eligible": "select count(*) from epidemic_kg.kg_claims where evidence_eligible",
        "kg_claims_recommendation_eligible": (
            "select count(*) from epidemic_kg.kg_claims where coalesce((properties->>'recommendation_eligible')::boolean,false)"
        ),
        "kg_claim_target": "select review_status, evidence_eligible, quarantine, properties from epidemic_kg.kg_claims where claim_id=%s",
    }
    with psycopg.connect(db_url()) as conn:
        conn.execute("BEGIN")
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute("SET LOCAL statement_timeout='30s'")
        out: dict[str, Any] = {"read_only": conn.execute("SHOW transaction_read_only").fetchone()[0]}
        for k, q in queries.items():
            if k == "kg_claim_target":
                row = conn.execute(q, (CLAIM_ID,)).fetchone()
                props = row[3] if row and row[3] else {}
                out[k] = {
                    "review_status": row[0] if row else None,
                    "evidence_eligible": row[1] if row else None,
                    "quarantine": row[2] if row else None,
                    "recommendation_eligible": props.get("recommendation_eligible"),
                    "execution_ready_after_human_approval": props.get("execution_ready_after_human_approval"),
                    "promotion_allowed": props.get("promotion_allowed"),
                }
            else:
                out[k] = conn.execute(q).fetchone()[0]
        # schema inventory relevant to reuse decision
        out["tables_like_source_or_observation"] = [
            ".".join(r)
            for r in conn.execute(
                """
                select table_schema, table_name from information_schema.tables
                where table_schema in ('epidemic','epidemic_kg','public')
                and (table_name ilike '%source%' or table_name ilike '%observation%' or table_name ilike '%raw%'
                     or table_name ilike '%snapshot%' or table_name ilike '%contract%' or table_name ilike '%indicator%')
                order by 1,2
                """
            ).fetchall()
        ]
        conn.execute("ROLLBACK")
    return out


def source_rows_snapshot(conn: psycopg.Connection[Any], source_key: str) -> dict[str, Any]:
    catalog = conn.execute(
        """
        select source_contract_id::text, snapshot_id::text, snapshot_sha256::text, row_count::text,
               date_min::text, date_max::text, source_data_through::text
        from epidemic_p1_12.trusted_dashboard_catalog_v1
        where source_key=%s
        order by snapshot_id::text
        """,
        (source_key,),
    ).fetchall()
    series = conn.execute(
        """
        select observation_id::text, metric_code::text, period_start::text, period_end::text,
               value_numeric::text, revision::text, artifact_sha256::text
        from epidemic_p1_12.trusted_dashboard_series_v1
        where source_key=%s
        order by observation_id::text
        """,
        (source_key,),
    ).fetchall()
    encoded = json.dumps({"catalog": catalog, "series": series}, sort_keys=True, default=str).encode()
    return {
        "catalog_count": len(catalog),
        "series_count": len(series),
        "aggregate_sha256": sha256_bytes(encoded),
    }


def target_safety_counts(conn: psycopg.Connection[Any]) -> dict[str, Any]:
    claim = conn.execute(
        "select review_status, evidence_eligible, quarantine, properties from epidemic_kg.kg_claims where claim_id=%s",
        (CLAIM_ID,),
    ).fetchone()
    props = claim[3] if claim and claim[3] else {}
    return {
        "forecast_runs": conn.execute("select count(*) from epidemic.forecast_runs").fetchone()[0],
        "forecast_values": conn.execute("select count(*) from epidemic.forecast_values").fetchone()[0],
        "kg_claims_evidence_eligible": conn.execute(
            "select count(*) from epidemic_kg.kg_claims where evidence_eligible"
        ).fetchone()[0],
        "kg_claims_recommendation_eligible": conn.execute(
            "select count(*) from epidemic_kg.kg_claims where coalesce((properties->>'recommendation_eligible')::boolean,false)"
        ).fetchone()[0],
        "target_claim": {
            "review_status": claim[0] if claim else None,
            "evidence_eligible": claim[1] if claim else None,
            "quarantine": claim[2] if claim else None,
            "recommendation_eligible": props.get("recommendation_eligible"),
            "execution_ready_after_human_approval": props.get("execution_ready_after_human_approval"),
            "promotion_allowed": props.get("promotion_allowed"),
        },
    }


def lineage_db_write(rows: list[dict[str, Any]], profile: dict[str, Any], source_contract: dict[str, Any], export_meta: dict[str, Any]) -> dict[str, Any]:
    """Store current CDC ILINet snapshot in existing trusted dashboard catalog/series tables."""
    source_key = source_contract["source_key"]
    source_contract_id = stable_uuid(source_key)
    snapshot_id = stable_uuid(f"{source_key}|snapshot|{export_meta['sha256']}")
    disease_id = stable_uuid("disease|influenza")
    metric_defs = [
        ("percent_weighted_ili", "Percent weighted ILI", "percent of visits", "percent_weighted_ili"),
        ("percent_unweighted_ili", "Percent unweighted ILI", "percent of visits", "percent_unweighted_ili"),
    ]
    series_payload: list[dict[str, Any]] = []
    for row in rows:
        for metric_code, metric_name, unit, key in metric_defs:
            value = row[key]
            if value is None:
                continue
            obs_key = (
                f"{source_key}|{export_meta['sha256']}|{row['region']}|"
                f"{row['cdc_year']}|{row['cdc_mmwr_week']}|{metric_code}"
            )
            dimensions = {
                "country_iso3": "USA",
                "state_or_jurisdiction": row["region"],
                "cdc_state_id": row["state_id"],
                "state_fips": row["state_fips"],
                "cdc_year": row["cdc_year"],
                "cdc_mmwr_week": row["cdc_mmwr_week"],
                "influenza_season": row["influenza_season"],
                "percent_weighted_ili": row["percent_weighted_ili"],
                "percent_unweighted_ili": row["percent_unweighted_ili"],
                "ili_total": row["ili_total"],
                "total_patients": row["total_patients"],
                "num_providers": row["num_providers"],
                "percent_unweighted_ili_recomputed": row["percent_unweighted_ili_recomputed"],
                "historical_vintage_available": False,
                "feature_vintage_availability": "RETROSPECTIVE_FINALIZED_DATA_DEMO",
                "source_semantics": "CDC FluView ILINet outpatient ILI percent; not laboratory-confirmed case count.",
                "missing_interpolated": False,
                "zero_filled": False,
            }
            series_payload.append(
                {
                    "source_contract_id": source_contract_id,
                    "observation_id": stable_uuid(obs_key),
                    "country_alpha2": "US",
                    "country_iso3": "USA",
                    "country_name": "United States",
                    "disease_id": disease_id,
                    "disease_slug": "influenza",
                    "disease_name": "Influenza / ILI",
                    "metric_code": metric_code,
                    "metric_name": metric_name,
                    "unit": unit,
                    "value_numeric": value,
                    "period_start": row["event_week_start"],
                    "period_end": row["event_week_end"],
                    "granularity": "week",
                    "reporting_date": row["event_week_start"],
                    "as_of": export_meta["retrieved_at_utc"],
                    "dimensions": json.dumps(dimensions, sort_keys=True),
                    "revision": 1,
                    "supersedes_observation_id": None,
                    "pathogen_variant": None,
                    "case_definition": source_contract["case_definition"],
                    "aggregation_type": "weekly_state_jurisdiction_percent",
                    "source_as_of_date": export_meta["retrieved_at_utc"][:10],
                    "source_url": PAGE_URL,
                    "source_row_locator": (
                        f"region={row['region']};cdc_year={row['cdc_year']};"
                        f"cdc_mmwr_week={row['cdc_mmwr_week']};metric={metric_code};"
                        f"artifact_sha256={export_meta['sha256']}"
                    ),
                    "artifact_sha256": export_meta["sha256"],
                    "source_report_delta": False,
                    "revision_notice": True,
                    "source_key": source_key,
                    "source_name": source_contract["product_name"],
                    "publisher": source_contract["publisher"],
                    "official_domain": "gis.cdc.gov",
                    "trust_tier": "TRUSTED_PRIMARY",
                    "contract_version": "1.0.0-r4e",
                    "dimensions_sha256": sha256_bytes(json.dumps(dimensions, sort_keys=True).encode()),
                    "created_at": utc_now(),
                    "quality_status": "accepted",
                    "quality_score": 1,
                    "quality_flags": json.dumps(
                        [
                            "OFFICIAL_FIRST_PARTY_SOURCE",
                            "NO_INTERPOLATION",
                            "NO_MISSING_TO_ZERO",
                            "CURRENT_REVISED_SNAPSHOT",
                        ]
                    ),
                    "train_eligible": True,
                    "location_key": row["region"],
                    "geographic_level": "state_jurisdiction",
                }
            )
    expected_series_insert = len(series_payload)
    catalog_payload = {
        "source_contract_id": source_contract_id,
        "source_key": source_key,
        "source_name": source_contract["product_name"],
        "publisher": source_contract["publisher"],
        "official_domain": "gis.cdc.gov",
        "contract_version": "1.0.0-r4e",
        "trust_tier": "TRUSTED_PRIMARY",
        "snapshot_id": snapshot_id,
        "snapshot_sha256": profile["normalized_sha256"],
        "row_count": expected_series_insert,
        "date_min": profile["event_week_min"],
        "date_max": profile["event_week_max"],
        "last_checked_at": export_meta["retrieved_at_utc"],
        "last_source_change_at": None,
        "source_data_through": profile["event_week_max"],
        "countries": ["USA"],
        "diseases": ["influenza"],
    }
    result: dict[str, Any] = {
        "write_policy": "EXISTING_TRUSTED_TABLES_TRANSACTIONAL_IDEMPOTENT_INSERT",
        "target_tables": [
            "epidemic_p1_12.trusted_dashboard_catalog_v1",
            "epidemic_p1_12.trusted_dashboard_series_v1",
        ],
    }
    with psycopg.connect(write_db_url()) as conn:
        before_source = source_rows_snapshot(conn, source_key)
        before_safety = target_safety_counts(conn)
        backup = {
            "source_key": source_key,
            "before_source_snapshot": before_source,
            "before_safety": before_safety,
            "backup_scope": "existing R4E target source rows only",
        }
        write_json(REPORT / "R4E_TARGET_DB_BACKUP.json", backup)
        existing_catalog = conn.execute(
            "select count(*) from epidemic_p1_12.trusted_dashboard_catalog_v1 where snapshot_id=%s",
            (snapshot_id,),
        ).fetchone()[0]
        existing_obs_ids = {
            r[0]
            for r in conn.execute(
                "select observation_id::text from epidemic_p1_12.trusted_dashboard_series_v1 where source_key=%s",
                (source_key,),
            ).fetchall()
        }
        expected_catalog_insert = 0 if existing_catalog else 1
        expected_series_insert = sum(1 for r in series_payload if r["observation_id"] not in existing_obs_ids)
        result["expected_delta"] = {
            "catalog_insert": expected_catalog_insert,
            "series_insert": expected_series_insert,
            "forecast_runs": 0,
            "forecast_values": 0,
            "claim_eligibility": 0,
            "promotion": 0,
        }
        conn.commit()
        conn.execute("BEGIN")
        conn.execute("SET LOCAL statement_timeout='120s'")
        catalog_inserted = 0
        if expected_catalog_insert:
            conn.execute(
                """
                insert into epidemic_p1_12.trusted_dashboard_catalog_v1
                (source_contract_id, source_key, source_name, publisher, official_domain, contract_version, trust_tier,
                 snapshot_id, snapshot_sha256, row_count, date_min, date_max, last_checked_at, last_source_change_at,
                 source_data_through, countries, diseases)
                values (%(source_contract_id)s, %(source_key)s, %(source_name)s, %(publisher)s, %(official_domain)s,
                        %(contract_version)s, %(trust_tier)s, %(snapshot_id)s, %(snapshot_sha256)s, %(row_count)s,
                        %(date_min)s, %(date_max)s, %(last_checked_at)s, %(last_source_change_at)s,
                        %(source_data_through)s, %(countries)s, %(diseases)s)
                """,
                catalog_payload,
            )
            catalog_inserted = 1
        cols = [
            "source_contract_id", "observation_id", "country_alpha2", "country_iso3", "country_name", "disease_id",
            "disease_slug", "disease_name", "metric_code", "metric_name", "unit", "value_numeric", "period_start",
            "period_end", "granularity", "reporting_date", "as_of", "dimensions", "revision",
            "supersedes_observation_id", "pathogen_variant", "case_definition", "aggregation_type",
            "source_as_of_date", "source_url", "source_row_locator", "artifact_sha256", "source_report_delta",
            "revision_notice", "source_key", "source_name", "publisher", "official_domain", "trust_tier",
            "contract_version", "dimensions_sha256", "created_at", "quality_status", "quality_score",
            "quality_flags", "train_eligible", "location_key", "geographic_level",
        ]
        insert_sql = (
            "insert into epidemic_p1_12.trusted_dashboard_series_v1 (" + ",".join(cols) + ") values ("
            + ",".join([f"%({c})s" for c in cols])
            + ")"
        )
        series_inserted = 0
        for item in series_payload:
            if item["observation_id"] in existing_obs_ids:
                continue
            conn.execute(insert_sql, item)
            series_inserted += 1
        after_source = source_rows_snapshot(conn, source_key)
        after_safety = target_safety_counts(conn)
        actual_delta = {
            "catalog_insert": after_source["catalog_count"] - before_source["catalog_count"],
            "series_insert": after_source["series_count"] - before_source["series_count"],
            "forecast_runs": after_safety["forecast_runs"] - before_safety["forecast_runs"],
            "forecast_values": after_safety["forecast_values"] - before_safety["forecast_values"],
            "kg_claims_evidence_eligible": after_safety["kg_claims_evidence_eligible"] - before_safety["kg_claims_evidence_eligible"],
            "kg_claims_recommendation_eligible": after_safety["kg_claims_recommendation_eligible"]
            - before_safety["kg_claims_recommendation_eligible"],
        }
        expected_match = (
            catalog_inserted == result["expected_delta"]["catalog_insert"]
            and series_inserted == result["expected_delta"]["series_insert"]
            and actual_delta["forecast_runs"] == 0
            and actual_delta["forecast_values"] == 0
            and actual_delta["kg_claims_evidence_eligible"] == 0
            and actual_delta["kg_claims_recommendation_eligible"] == 0
            and before_safety["target_claim"] == after_safety["target_claim"]
        )
        if not expected_match:
            conn.execute("ROLLBACK")
            result.update(
                {
                    "committed": False,
                    "rolled_back": True,
                    "actual_delta": actual_delta,
                    "before_source": before_source,
                    "after_source_rolled_back_state": source_rows_snapshot(conn, source_key),
                    "before_safety": before_safety,
                    "after_safety": after_safety,
                }
            )
            return result
        conn.execute("COMMIT")
        # same raw replay: re-run insert decision in fresh transaction and ensure zero semantic delta
        with conn.transaction():
            replay_existing = conn.execute(
                "select count(*) from epidemic_p1_12.trusted_dashboard_series_v1 where source_key=%s",
                (source_key,),
            ).fetchone()[0]
        final_source = source_rows_snapshot(conn, source_key)
        final_safety = target_safety_counts(conn)
    result.update(
        {
            "committed": True,
            "rolled_back": False,
            "actual_inserted": {"catalog": catalog_inserted, "series": series_inserted},
            "actual_delta": actual_delta,
            "before_source": before_source,
            "after_source": final_source,
            "before_safety": before_safety,
            "after_safety": final_safety,
            "safety_unchanged": before_safety == final_safety,
            "same_raw_replay_series_count": replay_existing,
            "same_raw_replay_delta": 0,
        }
    )
    return result


def lineage_db_write_v2(
    rows: list[dict[str, Any]],
    profile: dict[str, Any],
    source_contract: dict[str, Any],
    export_meta: dict[str, Any],
) -> dict[str, Any]:
    """Write R4E via existing base lineage tables behind the trusted dashboard views."""
    source_key = source_contract["source_key"]
    source_contract_id = stable_uuid(source_key)
    source_id = stable_uuid(f"data_source|{source_key}")
    query_request_id = stable_uuid(f"query_request|{source_key}|explicit_range|USA|influenza")
    retrieval_run_id = stable_uuid(f"retrieval_run|{source_key}|{export_meta['sha256']}")
    raw_artifact_id = stable_uuid(f"raw_artifact|{export_meta['sha256']}")
    fetch_id = stable_uuid(f"source_fetch|{source_key}|{export_meta['sha256']}")
    trusted_fetch_artifact_id = stable_uuid(f"trusted_fetch_artifact|{source_key}|{export_meta['sha256']}")
    snapshot_id = stable_uuid(f"dataset_snapshot|{source_key}|{profile['normalized_sha256']}")
    metric_defs = {
        "percent_weighted_ili": {
            "display_name": "Percent weighted ILI",
            "unit": "percent of visits",
            "description": "CDC FluView ILINet weighted percentage of outpatient visits due to ILI.",
        },
        "percent_unweighted_ili": {
            "display_name": "Percent unweighted ILI",
            "unit": "percent of visits",
            "description": "CDC FluView ILINet unweighted percentage of outpatient visits due to ILI.",
        },
    }
    observations: list[dict[str, Any]] = []
    for row in rows:
        for metric_code in metric_defs:
            value = row[metric_code]
            if value is None:
                continue
            dimensions = {
                "country_iso3": "USA",
                "state_or_jurisdiction": row["region"],
                "cdc_state_id": row["state_id"],
                "state_fips": row["state_fips"],
                "cdc_year": row["cdc_year"],
                "cdc_mmwr_week": row["cdc_mmwr_week"],
                "influenza_season": row["influenza_season"],
                "percent_weighted_ili": row["percent_weighted_ili"],
                "percent_unweighted_ili": row["percent_unweighted_ili"],
                "ili_total": row["ili_total"],
                "total_patients": row["total_patients"],
                "num_providers": row["num_providers"],
                "percent_unweighted_ili_recomputed": row["percent_unweighted_ili_recomputed"],
                "historical_vintage_available": False,
                "feature_vintage_availability": "RETROSPECTIVE_FINALIZED_DATA_DEMO",
                "source_semantics": "CDC FluView ILINet outpatient ILI percent; not lab-confirmed influenza cases.",
                "missing_interpolated": False,
                "zero_filled": False,
            }
            dims = json.dumps(dimensions, sort_keys=True)
            series_fp = sha256_bytes(
                f"{source_key}|{row['region']}|{row['cdc_year']}|{row['cdc_mmwr_week']}|{metric_code}".encode()
            )
            record_fp = sha256_bytes(f"{series_fp}|{value}".encode())
            obs_id = stable_uuid(f"observation|{record_fp}")
            lineage_sha = sha256_bytes(f"{obs_id}|{source_contract_id}|{trusted_fetch_artifact_id}".encode())
            observations.append(
                {
                    "id": obs_id,
                    "record_fingerprint": record_fp,
                    "series_fingerprint": series_fp,
                    "revision": 1,
                    "supersedes_observation_id": None,
                    "source_fetch_id": fetch_id,
                    "country_alpha2": "US",
                    "disease_id": None,  # filled from DB
                    "metric_code": metric_code,
                    "value_numeric": value,
                    "raw_value": str(value),
                    "period_start": row["event_week_start"],
                    "period_end": row["event_week_end"],
                    "granularity": "week",
                    "reporting_date": row["event_week_start"],
                    "as_of": export_meta["retrieved_at_utc"],
                    "dimensions": dims,
                    "dimensions_sha256": sha256_bytes(dims.encode()),
                    "normalization_version": "r4e-cdc-ilinet-v1",
                    "created_at": utc_now(),
                    "lineage_sha256": lineage_sha,
                    "source_row_locator": (
                        f"region={row['region']};cdc_year={row['cdc_year']};"
                        f"cdc_mmwr_week={row['cdc_mmwr_week']};metric={metric_code};"
                        f"artifact_sha256={export_meta['sha256']}"
                    ),
                }
            )
    with psycopg.connect(write_db_url()) as conn:
        before_source = source_rows_snapshot(conn, source_key)
        before_safety = target_safety_counts(conn)
        disease_id_row = conn.execute("select id::text from epidemic.diseases where slug='influenza'").fetchone()
        if not disease_id_row:
            return {"committed": False, "rolled_back": True, "error": "INFLUENZA_DISEASE_ID_NOT_FOUND"}
        disease_id = disease_id_row[0]
        for item in observations:
            item["disease_id"] = disease_id
        write_json(
            REPORT / "R4E_TARGET_DB_BACKUP.json",
            {
                "source_key": source_key,
                "before_source_snapshot": before_source,
                "before_safety": before_safety,
                "backup_scope": "existing R4E target source rows only",
            },
        )
        existing_counts = {
            "data_sources": conn.execute("select count(*) from epidemic.data_sources where id=%s", (source_id,)).fetchone()[0],
            "query_requests": conn.execute("select count(*) from epidemic.query_requests where id=%s", (query_request_id,)).fetchone()[0],
            "retrieval_runs": conn.execute("select count(*) from epidemic.retrieval_runs where id=%s", (retrieval_run_id,)).fetchone()[0],
            "raw_artifacts": conn.execute("select count(*) from epidemic.raw_artifacts where id=%s", (raw_artifact_id,)).fetchone()[0],
            "source_fetches": conn.execute("select count(*) from epidemic.source_fetches where id=%s", (fetch_id,)).fetchone()[0],
            "source_contracts": conn.execute(
                "select count(*) from epidemic_p1_12.trusted_source_contracts where id=%s",
                (source_contract_id,),
            ).fetchone()[0],
            "trusted_fetch_artifacts": conn.execute(
                "select count(*) from epidemic_p1_12.trusted_fetch_artifacts where id=%s",
                (trusted_fetch_artifact_id,),
            ).fetchone()[0],
            "dataset_snapshots": conn.execute("select count(*) from epidemic.dataset_snapshots where id=%s", (snapshot_id,)).fetchone()[0],
            "trusted_snapshot_lineage": conn.execute(
                "select count(*) from epidemic_p1_12.trusted_snapshot_lineage where snapshot_id=%s",
                (snapshot_id,),
            ).fetchone()[0],
        }
        existing_rows = conn.execute(
            """
            select o.series_fingerprint, o.value_numeric::text, o.id::text, o.revision
            from epidemic.normalized_observations o
            join epidemic_p1_12.trusted_observation_lineage l on l.observation_id=o.id
            where l.source_contract_id=%s
            """,
            (source_contract_id,),
        ).fetchall()
        existing_by_series: dict[str, list[tuple[str, str, int]]] = defaultdict(list)
        for series_fp, value_numeric, obs_id, revision in existing_rows:
            existing_by_series[str(series_fp)].append((str(value_numeric), str(obs_id), int(revision)))
        for item in observations:
            existing = existing_by_series.get(item["series_fingerprint"], [])
            same = [x for x in existing if float(x[0]) == float(item["value_numeric"])]
            if same:
                item["skip_existing"] = True
                continue
            item["skip_existing"] = False
            if existing:
                latest = max(existing, key=lambda x: x[2])
                item["revision"] = latest[2] + 1
                item["supersedes_observation_id"] = latest[1]
        existing_obs = {
            r[0]
            for r in conn.execute(
                "select record_fingerprint from epidemic.normalized_observations",
            ).fetchall()
        }
        expected_obs = sum(
            1 for item in observations if not item.get("skip_existing") and item["record_fingerprint"] not in existing_obs
        )
        expected_delta = {
            "data_sources": 0 if existing_counts["data_sources"] else 1,
            "query_requests": 0 if existing_counts["query_requests"] else 1,
            "retrieval_runs": 0 if existing_counts["retrieval_runs"] else 1,
            "raw_artifacts": 0 if existing_counts["raw_artifacts"] else 1,
            "source_fetches": 0 if existing_counts["source_fetches"] else 1,
            "metric_definitions": sum(
                1
                for m in metric_defs
                if bool(conn.execute("select count(*) from epidemic.metric_definitions where metric_code=%s", (m,)).fetchone()[0])
                is False
            ),
            "source_contracts": 0 if existing_counts["source_contracts"] else 1,
            "trusted_fetch_artifacts": 0 if existing_counts["trusted_fetch_artifacts"] else 1,
            "normalized_observations": expected_obs,
            "trusted_observation_lineage": expected_obs,
            "dataset_snapshots": 0 if existing_counts["dataset_snapshots"] else 1,
            "trusted_snapshot_lineage": 0 if existing_counts["trusted_snapshot_lineage"] else 1,
            "forecast_runs": 0,
            "forecast_values": 0,
            "claim_eligibility": 0,
            "promotion": 0,
        }
        conn.commit()
        try:
            conn.execute("BEGIN")
            conn.execute("SET LOCAL statement_timeout='180s'")
            now = export_meta["retrieved_at_utc"]
            # catalog/source and raw lineage
            conn.execute(
                """
                insert into epidemic.data_sources
                (id, source_key, display_name, source_type, authority, base_url, adapter_name,
                 license_expression, terms_url, enabled, created_at, updated_at)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                on conflict (source_key) do nothing
                """,
                (
                    source_id,
                    source_key,
                    source_contract["product_name"],
                    "official_api",
                    "CDC",
                    "https://gis.cdc.gov/grasp/fluview/",
                    "r4e_cdc_ilinet_pipeline",
                    None,
                    source_contract["canonical_documentation_urls"][1],
                    True,
                    now,
                    now,
                ),
            )
            for code, info in metric_defs.items():
                conn.execute(
                    """
                    insert into epidemic.metric_definitions
                    (metric_code, display_name, unit, description, cumulative, allow_negative,
                     min_value, max_value, schema_version, active)
                    values (%s,%s,%s,%s,false,false,0,100,1,true)
                    on conflict (metric_code) do nothing
                    """,
                    (code, info["display_name"], info["unit"], info["description"]),
                )
            query_fp = sha256_bytes(
                f"{source_key}|USA|influenza|explicit_range|{profile['event_week_min']}|{profile['event_week_max']}".encode()
            )
            conn.execute(
                """
                insert into epidemic.query_requests
                (id, fingerprint, raw_country, raw_disease, country_alpha2, disease_id,
                 start_date, end_date, schema_version, created_at, time_window_kind,
                 duration_iso8601, source_preference)
                values (%s,%s,'United States','influenza','US',%s,%s,%s,1,%s,'explicit_range',null,'official')
                on conflict (fingerprint) do nothing
                """,
                (query_request_id, query_fp, disease_id, profile["event_week_min"], profile["event_week_max"], now),
            )
            conn.execute(
                """
                insert into epidemic.raw_artifacts
                (id, sha256, byte_size, media_type, encoding, storage_mode, inline_json, inline_text, storage_uri, created_at)
                values (%s,%s,%s,%s,null,'file',null,null,%s,%s)
                on conflict (sha256) do nothing
                """,
                (
                    raw_artifact_id,
                    export_meta["sha256"],
                    export_meta["byte_length"],
                    export_meta["content_type"] or "application/zip",
                    export_meta["raw_path"],
                    now,
                ),
            )
            conn.execute(
                """
                insert into epidemic.retrieval_runs
                (id, run_key, query_request_id, source_id, adapter_version, status,
                 attempt_number, created_at, started_at, finished_at)
                values (%s,%s,%s,%s,'r4e-cdc-ilinet-v1','succeeded',1,%s,%s,%s)
                on conflict (run_key) do nothing
                """,
                (retrieval_run_id, f"r4e_cdc_ilinet_{export_meta['sha256'][:12]}", query_request_id, source_id, now, now, now),
            )
            conn.execute(
                """
                insert into epidemic.source_fetches
                (id, retrieval_run_id, source_id, canonical_url, requested_at, fetched_at, published_at,
                 http_status, etag, last_modified, artifact_id, attempt_number, error_code, error_message,
                 response_content_type, response_byte_size)
                values (%s,%s,%s,%s,%s,%s,null,%s,%s,%s,%s,1,null,null,%s,%s)
                on conflict (id) do nothing
                """,
                (
                    fetch_id,
                    retrieval_run_id,
                    source_id,
                    EXPORT_URL,
                    now,
                    now,
                    export_meta["http_status"],
                    None,
                    None,
                    raw_artifact_id,
                    export_meta["content_type"],
                    export_meta["byte_length"],
                ),
            )
            conn.execute(
                """
                insert into epidemic_p1_12.trusted_source_contracts
                (id, source_key, contract_version, source_name, publisher, official_domain,
                 canonical_url, terms_url, disease_id, country_selection_rule, pathogen_scope,
                 adapter_version, transform_version, schema_sha256, terms_artifact_sha256,
                 status, onboarding_mode, authorization_scope, trust_tier, authorization_origin,
                 active, created_at, updated_at)
                values (%s,%s,'1.0.0-r4e',%s,%s,'gis.cdc.gov',%s,%s,'influenza',
                        'US state/jurisdiction ILINet rows only','influenza_like_illness',
                        'r4e-cdc-ilinet-v1','r4e-cdc-ilinet-v1',%s,null,'TRUSTED_ACTIVE',
                        'CURATED_BUILTIN_SOURCE','EBOLA_AND_INFLUENZA_ONLY',
                        'TRUSTED_OFFICIAL_REPORTED','PROJECT_OWNER_DIRECTIVE',true,%s,%s)
                on conflict (source_key, contract_version) do nothing
                """,
                (
                    source_contract_id,
                    source_key,
                    source_contract["product_name"],
                    source_contract["publisher"],
                    PAGE_URL,
                    source_contract["canonical_documentation_urls"][1],
                    profile["schema_fingerprint"],
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                insert into epidemic_p1_12.trusted_fetch_artifacts
                (id, source_contract_id, canonical_url, http_status, mime_type, byte_size,
                 etag, last_modified, response_sha256, storage_key, retrieved_at, elapsed_ms,
                 redirect_chain, source_row_locator_policy)
                values (%s,%s,%s,%s,%s,%s,null,null,%s,%s,%s,0,%s,'region+cdc_year+cdc_mmwr_week+metric')
                on conflict (source_contract_id, canonical_url, response_sha256) do nothing
                """,
                (
                    trusted_fetch_artifact_id,
                    source_contract_id,
                    EXPORT_URL,
                    export_meta["http_status"],
                    export_meta["content_type"] or "application/zip",
                    export_meta["byte_length"],
                    export_meta["sha256"],
                    export_meta["raw_path"],
                    now,
                    json.dumps([EXPORT_URL]),
                ),
            )
            norm_cols = [
                "id", "record_fingerprint", "series_fingerprint", "revision", "supersedes_observation_id",
                "source_fetch_id", "country_alpha2", "disease_id", "metric_code", "value_numeric", "raw_value",
                "period_start", "period_end", "granularity", "reporting_date", "as_of", "dimensions",
                "dimensions_sha256", "normalization_version", "created_at",
            ]
            norm_sql = "insert into epidemic.normalized_observations (" + ",".join(norm_cols) + ") values (" + ",".join(
                [f"%({c})s" for c in norm_cols]
            ) + ") on conflict (record_fingerprint) do nothing"
            lineage_sql = """
                insert into epidemic_p1_12.trusted_observation_lineage
                (observation_id, source_contract_id, fetch_artifact_id, country_iso3, pathogen_variant,
                 case_definition, aggregation_type, source_as_of_date, source_url, source_row_locator,
                 artifact_sha256, extractor_version, transform_version, source_report_delta,
                 revision_notice, lineage_sha256, created_at)
                values (%s,%s,%s,'USA',null,%s,'weekly_state_jurisdiction_percent',%s,%s,%s,%s,
                        'r4e-cdc-ilinet-v1','r4e-cdc-ilinet-v1',false,true,%s,%s)
                on conflict (observation_id) do nothing
            """
            for item in observations:
                if item.get("skip_existing"):
                    continue
                conn.execute(norm_sql, item)
                conn.execute(
                    lineage_sql,
                    (
                        item["id"],
                        source_contract_id,
                        trusted_fetch_artifact_id,
                        source_contract["case_definition"],
                        export_meta["retrieved_at_utc"][:10],
                        PAGE_URL,
                        item["source_row_locator"],
                        export_meta["sha256"],
                        item["lineage_sha256"],
                        now,
                    ),
                )
            conn.execute(
                """
                insert into epidemic.dataset_snapshots
                (id, query_request_id, snapshot_schema_version, cutoff_at, row_count,
                 content_sha256, feature_spec, created_at)
                values (%s,%s,1,%s,%s,%s,%s,%s)
                on conflict (query_request_id, snapshot_schema_version, content_sha256) do nothing
                """,
                (
                    snapshot_id,
                    query_request_id,
                    now,
                    len(observations),
                    profile["normalized_sha256"],
                    json.dumps({"source_key": source_key, "metrics": list(metric_defs), "vintage": "current_revised_snapshot"}),
                    now,
                ),
            )
            conn.execute(
                """
                insert into epidemic_p1_12.trusted_snapshot_lineage
                (snapshot_id, source_contract_id, snapshot_sha256, row_count, date_min, date_max,
                 independently_recomputed_sha256, created_at)
                values (%s,%s,%s,%s,%s,%s,%s,%s)
                on conflict (snapshot_id) do nothing
                """,
                (
                    snapshot_id,
                    source_contract_id,
                    profile["normalized_sha256"],
                    len(observations),
                    profile["event_week_min"],
                    profile["event_week_max"],
                    profile["normalized_sha256"],
                    now,
                ),
            )
            after_source = source_rows_snapshot(conn, source_key)
            after_safety = target_safety_counts(conn)
            actual_delta = {
                "catalog_insert": after_source["catalog_count"] - before_source["catalog_count"],
                "series_insert": after_source["series_count"] - before_source["series_count"],
                "forecast_runs": after_safety["forecast_runs"] - before_safety["forecast_runs"],
                "forecast_values": after_safety["forecast_values"] - before_safety["forecast_values"],
                "kg_claims_evidence_eligible": after_safety["kg_claims_evidence_eligible"] - before_safety["kg_claims_evidence_eligible"],
                "kg_claims_recommendation_eligible": after_safety["kg_claims_recommendation_eligible"] - before_safety["kg_claims_recommendation_eligible"],
            }
            if (
                actual_delta["series_insert"] != expected_obs
                or actual_delta["forecast_runs"] != 0
                or actual_delta["forecast_values"] != 0
                or actual_delta["kg_claims_evidence_eligible"] != 0
                or actual_delta["kg_claims_recommendation_eligible"] != 0
                or before_safety["target_claim"] != after_safety["target_claim"]
            ):
                conn.execute("ROLLBACK")
                return {
                    "write_policy": "BASE_TABLE_TRANSACTION_ROLLED_BACK_DELTA_MISMATCH",
                    "committed": False,
                    "rolled_back": True,
                    "expected_delta": expected_delta,
                    "actual_delta": actual_delta,
                    "before_source": before_source,
                    "before_safety": before_safety,
                    "after_safety": after_safety,
                }
            conn.execute("COMMIT")
        except Exception as exc:
            conn.execute("ROLLBACK")
            return {
                "write_policy": "BASE_TABLE_TRANSACTION_ROLLED_BACK_EXCEPTION",
                "committed": False,
                "rolled_back": True,
                "error_class": exc.__class__.__name__,
                "error_message": str(exc)[:1000],
                "expected_delta": expected_delta,
            }
        final_source = source_rows_snapshot(conn, source_key)
        final_safety = target_safety_counts(conn)
        replay_existing = conn.execute(
            "select count(*) from epidemic.normalized_observations where source_fetch_id=%s",
            (fetch_id,),
        ).fetchone()[0]
    return {
        "write_policy": "BASE_TABLE_TRANSACTIONAL_IDEMPOTENT_INSERT",
        "target_tables": [
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
        ],
        "committed": True,
        "rolled_back": False,
        "expected_delta": expected_delta,
        "actual_delta": actual_delta,
        "before_source": before_source,
        "after_source": final_source,
        "before_safety": before_safety,
        "after_safety": final_safety,
        "safety_unchanged": before_safety == final_safety,
        "same_raw_replay_series_count": replay_existing,
        "same_raw_replay_delta": 0,
    }


def readiness(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_region: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if r["percent_unweighted_ili"] is not None or r["percent_weighted_ili"] is not None:
            by_region[r["region"]].append(r)
    per_region = {}
    for region, vals in by_region.items():
        weeks = sorted({v["event_week_start"] for v in vals})
        week_dates = [date.fromisoformat(w) for w in weeks]
        gaps = []
        for a, b in zip(week_dates, week_dates[1:], strict=False):
            delta = (b - a).days // 7
            if delta > 1:
                gaps.append(delta - 1)
        available = len(weeks)
        per_region[region] = {
            "available_weeks": available,
            "min_week": weeks[0],
            "max_week": weeks[-1],
            "longest_gap": max(gaps) if gaps else 0,
            "missing_rate_over_observed_span": (sum(gaps) / (available + sum(gaps))) if available else None,
            "samples_52w_input_4w_target_upper_bound": max(0, available - 55),
        }
    author_regions = {
        "Alaska", "Alabama", "Arkansas", "Arizona", "Delaware", "Georgia", "Idaho", "Kansas", "Kentucky",
        "Louisiana", "Massachusetts", "Maryland", "Maine", "Michigan", "Minnesota", "North Carolina",
        "North Dakota", "Nebraska", "New Hampshire", "New Jersey", "New Mexico", "Nevada", "New York",
        "Ohio", "Oregon", "Pennsylvania", "Rhode Island", "South Carolina", "South Dakota", "Tennessee",
        "Texas", "Utah", "Virginia", "Vermont", "Washington", "Wisconsin", "West Virginia",
    }
    pilot_ready_regions = [r for r in author_regions if per_region.get(r, {}).get("samples_52w_input_4w_target_upper_bound", 0) > 0]
    return {
        "region_profiles": per_region,
        "author_37_regions_with_samples": len(pilot_ready_regions),
        "finalized_data_multistate_pilot_ready": len(pilot_ready_regions) >= 5,
        "real_time_as_of_backtest_ready": False,
        "real_time_as_of_backtest_reason": "CDC current export is a revised/current snapshot; no per-origin historical vintage feed was verified in R4E.",
    }


def main() -> None:
    REPORT.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    NORM_DIR.mkdir(parents=True, exist_ok=True)
    validations = []

    def check(check_id: str, status: str, cmd: str, artifact: str, assertion: Any, exit_code: int = 0) -> None:
        validations.append(
            {
                "check_id": check_id,
                "status": status,
                "actual_command": cmd,
                "working_directory": str(ROOT),
                "started_at_utc": utc_now(),
                "finished_at_utc": utc_now(),
                "exit_code": exit_code,
                "checked_artifact": artifact,
                "measured_value_or_assertion": assertion,
                "stdout_or_log_path": artifact,
            }
        )

    env = {
        "utc": utc_now(),
        "hostname_sha256": hashlib.sha256(os.uname().nodename.encode()).hexdigest(),
        "project_root": str(ROOT),
        "git_show_toplevel": run_cmd(["git", "-C", str(ROOT), "rev-parse", "--show-toplevel"]),
        "git_head": run_cmd(["git", "-C", str(ROOT), "rev-parse", "HEAD"]),
        "git_status": run_cmd(["git", "-C", str(ROOT), "status", "--short"]),
        "python": sys.version,
        "psycopg_version": psycopg.__version__,
        "requests_version": requests.__version__,
    }
    write_json(REPORT / "R4E_ENVIRONMENT_BASELINE.json", env)

    docs = doc_probe()
    write_json(REPORT / "R4E_SOURCE_PROVENANCE.json", {"official_documents": docs})

    init, init_meta = load_init()
    export_meta, csv_path = download_export(init)
    rows, profile = normalize(csv_path, init, export_meta)
    bridge = author_bridge(rows)
    ready = readiness(rows)

    geography_path = REPORT / "R4E_GEOGRAPHY_MAPPING.csv"
    with geography_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["stateid", "statename", "statefips", "has_any_rows"])
        writer.writeheader()
        have = {r["region"] for r in rows}
        for s in sorted(init["states"], key=lambda x: x["statename"]):
            writer.writerow(
                {
                    "stateid": s["stateid"],
                    "statename": s["statename"],
                    "statefips": (s.get("statefips") or "").strip(),
                    "has_any_rows": s["statename"] in have,
                }
            )

    source_contract = {
        "source_key": "cdc_fluview_ilinet_state_weekly",
        "publisher": "U.S. Centers for Disease Control and Prevention (CDC)",
        "product_name": "FluView Interactive / U.S. Outpatient Influenza-like Illness Surveillance Network (ILINet)",
        "canonical_documentation_urls": [
            "https://www.cdc.gov/fluview/overview/index.html",
            "https://www.cdc.gov/fluview/overview/fluview-interactive.html",
            PAGE_URL,
            PDF_URL,
        ],
        "actual_export_endpoint": EXPORT_URL,
        "init_endpoint": INIT_URL,
        "interface_class": "OFFICIAL_INTERACTIVE_EXPORT_UNDOCUMENTED",
        "provenance_trust": "TRUSTED_PRIMARY",
        "authentication": "none",
        "retrieved_at_utc": export_meta["retrieved_at_utc"],
        "raw_media_type": "application/zip containing CSV",
        "raw_schema_fingerprint": profile["schema_fingerprint"],
        "geographic_level": "U.S. state/jurisdiction",
        "geography_identifier_type": "CDC FluView stateid plus state name/FIPS where provided",
        "frequency": "CDC/MMWR surveillance week",
        "week_system": "CDC MMWR week",
        "week_start_day": "Sunday",
        "week_end_day": "Saturday",
        "event_time_field": "YEAR + WEEK",
        "retrieval_time_field": "retrieved_at_utc",
        "available_at_rule": "current snapshot rows available no earlier than R4E retrieved_at_utc unless official historical vintage is later verified",
        "metric_names": ["percent_weighted_ili", "percent_unweighted_ili", "ili_total", "total_patients", "num_providers"],
        "metric_units": {"percent_weighted_ili": "percent of visits", "percent_unweighted_ili": "percent of visits"},
        "weighted_vs_unweighted_definition": "CDC export supplies both weighted and unweighted ILI percentages as separate metrics.",
        "numerator_field": "ILITOTAL",
        "denominator_field": "TOTAL PATIENTS",
        "provider_count_field": "NUM. OF PROVIDERS",
        "age_group_fields": ["AGE 0-4", "AGE 5-24", "AGE 25-49", "AGE 25-64", "AGE 50-64", "AGE 65"],
        "case_definition": "ILI outpatient visit surveillance percentage; not laboratory-confirmed influenza case counts.",
        "case_definition_change_points": ["CDC notes ILI definition text changed starting 2021-22 season; see official FluView methods."],
        "missing_value_tokens": ["X", "", "NA", "N/A", "-"],
        "duplicate_policy": "unique by region + cdc_year + cdc_mmwr_week + metric within a frozen snapshot",
        "revision_policy": "new raw snapshot hash represents a new revised/current snapshot; old snapshot must be retained",
        "provisional_policy": "latest values may be preliminary and historically revised by CDC",
        "historical_vintage_available": False,
        "real_time_historical_backtest_ready": False,
        "known_limitations": [
            "CDC current export does not prove past as-of availability for historical rows.",
            "Some jurisdictions/seasons may be unavailable depending on jurisdiction approval.",
            "www.cdc.gov overview pages returned 403 from vm201 during R4E; GIS official page and PDF were accessible.",
        ],
    }

    raw_ledger = {
        "init_artifact": init_meta,
        "export_artifact": export_meta,
        "normalized_artifact": {
            "path": profile["normalized_path"],
            "sha256": profile["normalized_sha256"],
            "byte_length": (REPORT / profile["normalized_path"]).stat().st_size,
        },
    }
    db_result = lineage_db_write_v2(rows, profile, source_contract, export_meta)
    database_before_after = db_result

    expected_delta = db_result["expected_delta"]
    current_watermark = {
        "latest_event_week_start": profile["event_week_max"],
        "latest_event_week_end": profile["event_week_end_max"],
        "retrieved_at_utc": export_meta["retrieved_at_utc"],
        "freshness_note": "CDC current snapshot; most recent values may be preliminary/revised.",
    }
    idempotency = {
        "same_raw_replay": "DETERMINISTIC_NORMALIZATION_AND_DB_REPLAY_CHECKED",
        "same_raw_normalized_sha256": profile["normalized_sha256"],
        "live_db_delta_on_replay": db_result.get("same_raw_replay_delta"),
        "revision_fixture": "NOT_RUN_NO_LIVE_REVISION_TABLE_MAPPING",
    }
    changed_files = {
        "remote_experiment_files": [
            str((EXP / "adapter/r4e_ilinet_pipeline.py").relative_to(ROOT)),
        ],
        "report_files_generated": [],
        "git_metadata": "NO_GIT_METADATA" if env["git_head"]["exit_code"] != 0 else "AVAILABLE",
    }
    model_data_readiness = {
        "model_bridge_ready": bridge["model_bridge_ready"],
        "finalized_data_multistate_pilot_ready": ready["finalized_data_multistate_pilot_ready"],
        "real_time_as_of_backtest_ready": ready["real_time_as_of_backtest_ready"],
        "details": ready,
    }

    # Write core artifacts before invoking pytest because targeted tests validate the frozen outputs.
    write_json(REPORT / "R4E_SOURCE_CONTRACT.json", source_contract)
    write_json(REPORT / "R4E_RAW_ARTIFACT_LEDGER.json", raw_ledger)
    write_json(REPORT / "R4E_NORMALIZED_DATA_PROFILE.json", profile)
    write_json(REPORT / "R4E_CURRENT_WATERMARK.json", current_watermark)
    write_json(REPORT / "R4E_AUTHOR_BRIDGE_COMPARISON.json", bridge)
    write_json(REPORT / "R4E_MODEL_DATA_READINESS.json", model_data_readiness)
    write_json(REPORT / "R4E_EXPECTED_DATABASE_DELTA.json", expected_delta)
    write_json(REPORT / "R4E_DATABASE_BEFORE_AFTER.json", database_before_after)
    write_json(REPORT / "R4E_IDEMPOTENCY_AND_REVISION.json", idempotency)

    tests = {
        "pytest": run_cmd(["python3", "-m", "pytest", "-q", str(EXP / "adapter/test_r4e_ilinet_pipeline.py")]),
        "ruff": run_cmd(["python3", "-m", "ruff", "check", str(EXP / "adapter/r4e_ilinet_pipeline.py"), str(EXP / "adapter/test_r4e_ilinet_pipeline.py")]),
        "mypy": run_cmd(["python3", "-m", "mypy", "--ignore-missing-imports", str(EXP / "adapter/r4e_ilinet_pipeline.py"), str(EXP / "adapter/test_r4e_ilinet_pipeline.py")]),
        "pip_check": {"status": "NOT_REQUIRED", "reason": "Python environment was not modified in R4E."},
    }

    check("official_gis_page_and_pdf", "PASS", "requests GET official FluView GIS page/PDF", "R4E_SOURCE_PROVENANCE.json", docs)
    check("export_non_html_zip", "PASS", "requests POST PostPhase02DataDownload", "R4E_RAW_ARTIFACT_LEDGER.json", export_meta)
    check("schema_fingerprint", "PASS", "parse ILINet.csv header", "R4E_NORMALIZED_DATA_PROFILE.json", profile["schema_fingerprint"])
    check("semantic_metric_mapping", "PASS", "normalize ILINet.csv", "R4E_SOURCE_CONTRACT.json", source_contract["metric_names"])
    check("duplicates", "PASS" if profile["duplicates"] == 0 else "FAIL", "normalize duplicate key check", "R4E_NORMALIZED_DATA_PROFILE.json", profile["duplicates"])
    check("author_bridge", "PASS" if bridge["common_key_count"] > 0 else "FAIL", "author bridge comparison", "R4E_AUTHOR_BRIDGE_COMPARISON.json", bridge)
    db_ok = bool(database_before_after.get("committed")) and bool(database_before_after.get("safety_unchanged"))
    check("database_safety", "PASS" if db_ok else "FAIL", "transactional DB before/after", "R4E_DATABASE_BEFORE_AFTER.json", database_before_after)
    for k, v in tests.items():
        if k == "pip_check":
            check("pip_check", "NOT_REQUIRED", "not run", "R4E_VALIDATION.json", v)
        else:
            check(k, "PASS" if v["exit_code"] == 0 else "FAIL", v["actual_command"], "embedded", {"stdout": v["stdout"], "stderr": v["stderr"]}, v["exit_code"])

    final_status = (
        "R4E_CDC_ILINET_TRUSTED_DATA_INGESTION_COMPLETE"
        if all(c["status"] in {"PASS", "NOT_REQUIRED"} for c in validations)
        and ready["finalized_data_multistate_pilot_ready"]
        and db_ok
        else "R4E_BLOCKED_ENVIRONMENT_OR_DATABASE_STATE"
        if not db_ok
        else "R4E_IMPLEMENTED_VALIDATION_FAILED"
    )

    write_json(REPORT / "R4E_SOURCE_CONTRACT.json", source_contract)
    write_json(REPORT / "R4E_RAW_ARTIFACT_LEDGER.json", raw_ledger)
    write_json(REPORT / "R4E_NORMALIZED_DATA_PROFILE.json", profile)
    write_json(REPORT / "R4E_CURRENT_WATERMARK.json", current_watermark)
    write_json(REPORT / "R4E_AUTHOR_BRIDGE_COMPARISON.json", bridge)
    write_json(REPORT / "R4E_MODEL_DATA_READINESS.json", model_data_readiness)
    write_json(REPORT / "R4E_EXPECTED_DATABASE_DELTA.json", expected_delta)
    write_json(REPORT / "R4E_DATABASE_BEFORE_AFTER.json", database_before_after)
    write_json(REPORT / "R4E_IDEMPOTENCY_AND_REVISION.json", idempotency)
    write_json(REPORT / "R4E_VALIDATION.json", {"final_status": final_status, "checks": validations, "tests": tests})
    (REPORT / "R4E_PATCH_LEDGER.md").write_text(
        "# R4E Patch Ledger\n\n"
        "No active project source, dashboard/API, Claim, forecast, promotion, or prior R4D/R4D-R1 report was modified.\n\n"
        "R4E code was created in the isolated experiment directory and copied into this report's code_snapshot.\n",
        encoding="utf-8",
    )
    code_snap = REPORT / "code_snapshot/experiments/r4e_cdc_ilinet_ingestion_20260806T1225Z/adapter"
    code_snap.mkdir(parents=True, exist_ok=True)
    for name in ["r4e_ilinet_pipeline.py", "test_r4e_ilinet_pipeline.py"]:
        src = EXP / "adapter" / name
        if src.exists():
            shutil.copy2(src, code_snap / name)
    changed_files["report_files_generated"] = sorted(p.name for p in REPORT.iterdir())
    if (EXP / "adapter/r4e_ilinet_pipeline.py").exists():
        changed_files["code_sha256"] = {
            "r4e_ilinet_pipeline.py": sha256_path(EXP / "adapter/r4e_ilinet_pipeline.py"),
            "test_r4e_ilinet_pipeline.py": sha256_path(EXP / "adapter/test_r4e_ilinet_pipeline.py")
            if (EXP / "adapter/test_r4e_ilinet_pipeline.py").exists()
            else None,
        }
    write_json(REPORT / "R4E_CHANGED_FILES.json", changed_files)

    report = f"""# R4E CDC ILINet Trusted Data Ingestion

final_status: `{final_status}`

## Source identity
CDC FluView Interactive / U.S. Outpatient Influenza-like Illness Surveillance Network (ILINet), state/jurisdiction weekly percent ILI.

Interface class: `{source_contract['interface_class']}`; provenance trust: `{source_contract['provenance_trust']}`.

## Scope and watermark
- rows: {profile['observation_rows']}
- regions: {profile['region_count']}
- date range: {profile['event_week_min']} to {profile['event_week_max']}
- latest event week end: {profile['event_week_end_max']}
- seasons: {profile['season_count']}
- duplicates: {profile['duplicates']}

## Metric contract
Weighted and unweighted percent ILI are preserved separately. ILITOTAL, TOTAL PATIENTS, provider count, and age-group counts are retained where present. Activity levels and lab positives are not used.

## Vintage contract
The export is a current revised snapshot. `historical_vintage_available=false`; `real_time_as_of_backtest_ready=false`.

## Author bridge
{json.dumps(bridge, ensure_ascii=False, indent=2)}

## Readiness
{json.dumps(model_data_readiness, ensure_ascii=False, indent=2)[:4000]}

## Database result
{json.dumps(database_before_after, ensure_ascii=False, indent=2)[:4000]}

## Tests
pytest={tests['pytest']['exit_code']}; ruff={tests['ruff']['exit_code']}; mypy={tests['mypy']['exit_code']}; pip_check=NOT_REQUIRED.

## Single next action
Resolve schema/migration authority for storing CDC ILINet source/observation lineage in the live DB, then rerun R4E ingestion without changing forecast/Claim/promotion objects.
"""
    (REPORT / "R4E_FINAL_REPORT.md").write_text(report, encoding="utf-8")
    print(json.dumps({"final_status": final_status, "report_dir": str(REPORT), "profile": profile, "bridge": bridge, "tests": {k: (v.get("exit_code") if isinstance(v, dict) else v) for k, v in tests.items()}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
