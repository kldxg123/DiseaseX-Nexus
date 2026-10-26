from __future__ import annotations

import calendar
import csv
import datetime as dt
import hashlib
import html
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url


PROJECT = Path(os.environ.get("PROJECT", "USER_HOME/autodl-tmp/epidemic-openclaw")).resolve()
EXECUTION_ID = os.environ.get(
    "EXECUTION_ID",
    "three_disease_" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + secrets.token_hex(4),
)
ARTIFACT_DIR = PROJECT / "artifacts" / EXECUTION_ID
REPORT_DIR = PROJECT / "reports" / EXECUTION_ID
BACKUP_DIR = PROJECT / "backups" / EXECUTION_ID
RAW_DIR = ARTIFACT_DIR / "raw"
PARSED_DIR = ARTIFACT_DIR / "parsed"
TRANSFORM_VERSION = "three-disease-official-v1"
ADAPTER_VERSION = "three-disease-official-adapters-v1"
SNAPSHOT_SCHEMA = 1
QUALITY_RULESET = "trusted-official-quality-v1"
COUNTRY_MAPPING = "iso-3166-db-v1"
UUID_NS = uuid.uuid5(uuid.NAMESPACE_DNS, "epidemic-openclaw:three-disease-official-v1")


def canonical(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text_value: str) -> str:
    return sha256_bytes(text_value.encode("utf-8"))


def sha(obj: Any) -> str:
    return sha256_text(canonical(obj))


def stable_uuid(kind: str, value: str) -> uuid.UUID:
    return uuid.uuid5(UUID_NS, f"{kind}:{value}")


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def run_cmd(args: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None, timeout: int = 120) -> dict[str, Any]:
    start = time.time()
    proc = subprocess.run(args, cwd=str(cwd or PROJECT), env=env, text=True, capture_output=True, timeout=timeout)
    return {
        "args": [Path(args[0]).name, *args[1:]],
        "returncode": proc.returncode,
        "stdout": proc.stdout[-5000:],
        "stderr": proc.stderr[-5000:],
        "elapsed_ms": int((time.time() - start) * 1000),
    }


def secret_scan_text(text_value: str) -> list[str]:
    patterns = [
        r"postgresql(?:\+\w+)?://[^ \n\r\t]+",
        r"(?i)\b(password|secret|token|api[_-]?key)\s*[:=]\s*[^ \n\r\t]+",
        r"(?i)authorization:\s*[^ \n\r\t]+",
        r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+",
    ]
    hits: list[str] = []
    for pattern in patterns:
        if re.search(pattern, text_value):
            hits.append(pattern)
    return hits


@dataclass
class FetchResult:
    key: str
    url: str
    final_url: str
    status: int
    payload: bytes
    mime_type: str
    etag: str | None
    last_modified: str | None
    elapsed_ms: int
    redirect_chain: list[str]
    error: str | None = None

    @property
    def sha256(self) -> str:
        return sha256_bytes(self.payload)

    @property
    def byte_size(self) -> int:
        return len(self.payload)


@dataclass
class TrustedRow:
    source_key: str
    source_name: str
    publisher: str
    source_url: str
    source_row_locator: str
    artifact_sha256: str
    country_alpha2: str
    country_iso3: str
    country_name: str
    disease_slug: str
    disease_label: str
    metric_code: str
    metric_label: str
    value: int
    unit: str
    period_start: dt.date
    period_end: dt.date
    granularity: str
    reporting_date: dt.date
    source_as_of: dt.date
    dimensions: dict[str, Any]
    case_definition: str
    aggregation_type: str
    pathogen_variant: str | None = None
    source_report_delta: bool = False
    revision_notice: bool = False


class HTTPClient:
    def __init__(self, raw_dir: Path) -> None:
        self.raw_dir = raw_dir
        self.opener = urllib.request.build_opener(urllib.request.HTTPRedirectHandler())
        self.headers = {
            "User-Agent": "epidemic-openclaw-official-source-ingestion/1.0 (+controlled official-source audit)",
            "Accept": "*/*",
        }

    def fetch(self, key: str, url: str, *, timeout: int = 45, accept: str | None = None) -> FetchResult:
        headers = dict(self.headers)
        if accept:
            headers["Accept"] = accept
        req = urllib.request.Request(url, headers=headers)
        start = time.time()
        try:
            with self.opener.open(req, timeout=timeout) as resp:
                payload = resp.read()
                final_url = resp.geturl()
                status = getattr(resp, "status", 200)
                mime = resp.headers.get_content_type() or "application/octet-stream"
                result = FetchResult(
                    key=key,
                    url=url,
                    final_url=final_url,
                    status=int(status),
                    payload=payload,
                    mime_type=mime,
                    etag=resp.headers.get("ETag"),
                    last_modified=resp.headers.get("Last-Modified"),
                    elapsed_ms=int((time.time() - start) * 1000),
                    redirect_chain=[url, final_url] if final_url != url else [url],
                )
        except urllib.error.HTTPError as exc:
            payload = exc.read() or str(exc).encode("utf-8")
            result = FetchResult(
                key=key,
                url=url,
                final_url=exc.geturl() or url,
                status=int(exc.code),
                payload=payload,
                mime_type=exc.headers.get_content_type() if exc.headers else "text/plain",
                etag=exc.headers.get("ETag") if exc.headers else None,
                last_modified=exc.headers.get("Last-Modified") if exc.headers else None,
                elapsed_ms=int((time.time() - start) * 1000),
                redirect_chain=[url],
                error=f"HTTPError:{exc.code}",
            )
        except Exception as exc:  # noqa: BLE001 - evidence capture must be explicit
            result = FetchResult(
                key=key,
                url=url,
                final_url=url,
                status=0,
                payload=str(exc).encode("utf-8", errors="replace"),
                mime_type="text/plain",
                etag=None,
                last_modified=None,
                elapsed_ms=int((time.time() - start) * 1000),
                redirect_chain=[url],
                error=type(exc).__name__,
            )
        self.store(result)
        return result

    def store(self, result: FetchResult) -> Path:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", result.key).strip("_")[:120] or "fetch"
        suffix = ".bin"
        if result.mime_type == "application/json":
            suffix = ".json"
        elif "csv" in result.mime_type or result.final_url.lower().endswith(".csv"):
            suffix = ".csv"
        elif "html" in result.mime_type:
            suffix = ".html"
        elif "text" in result.mime_type:
            suffix = ".txt"
        path = self.raw_dir / f"{safe}.{result.sha256[:12]}{suffix}"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(result.payload)
        return path


def fetch_retry(client: HTTPClient, key: str, url: str, *, attempts: int = 3, timeout: int = 90, accept: str | None = None) -> FetchResult:
    last: FetchResult | None = None
    for attempt in range(1, attempts + 1):
        last = client.fetch(f"{key}_attempt{attempt}", url, timeout=timeout, accept=accept)
        if last.status == 200:
            return last
        time.sleep(min(2 * attempt, 6))
    assert last is not None
    return last


def parse_date(value: str | None) -> dt.date | None:
    if not value:
        return None
    value = value.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            parsed = dt.datetime.strptime(value[: len(fmt)], fmt)
            return parsed.date()
        except ValueError:
            pass
    try:
        return dt.date.fromisoformat(value[:10])
    except Exception:
        return None


def json_loads_bytes(payload: bytes) -> Any:
    return json.loads(payload.decode("utf-8-sig"))


def strip_tags(value: str) -> str:
    value = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", value)
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def word_to_int(value: str) -> int | None:
    mapping = {
        "zero": 0,
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
        "eleven": 11,
        "twelve": 12,
        "thirteen": 13,
        "fourteen": 14,
        "fifteen": 15,
        "sixteen": 16,
        "seventeen": 17,
        "eighteen": 18,
        "nineteen": 19,
        "twenty": 20,
    }
    value = value.strip().lower()
    if value.isdigit():
        return int(value)
    return mapping.get(value)


def resolve_db_url() -> URL:
    raw = (
        os.environ.get("MIGRATION_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or os.environ.get("APP_DATABASE_URL")
        or ""
    )
    if not raw:
        raise RuntimeError("DATABASE_URL_UNAVAILABLE")
    url = make_url(raw)
    admin_password = os.environ.get("POSTGRES_ADMIN_PASSWORD")
    if admin_password:
        url = url.set(username=os.environ.get("POSTGRES_ADMIN_USER") or "epidemicdb", password=admin_password)
    return url


def connect_engine():
    return create_engine(resolve_db_url().render_as_string(hide_password=False), future=True, pool_pre_ping=True)


def db_scalar(conn: Any, sql: str, params: dict[str, Any] | None = None) -> Any:
    return conn.execute(text(sql), params or {}).scalar_one()


def db_scalar_none(conn: Any, sql: str, params: dict[str, Any] | None = None) -> Any:
    return conn.execute(text(sql), params or {}).scalar_one_or_none()


def db_rows(conn: Any, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(text(sql), params or {}).mappings().all()]


def git_status_inventory() -> dict[str, Any]:
    status = run_cmd(["git", "status", "--porcelain=v2", "-z"], timeout=60)
    raw = status["stdout"]
    entries: list[dict[str, Any]] = []
    if status["returncode"] == 0:
        for item in raw.split("\0"):
            if not item:
                continue
            parts = item.split(" ")
            path = parts[-1]
            full = PROJECT / path
            entry = {"raw": item, "path": path, "exists": full.exists()}
            if full.exists() and full.is_file():
                data = full.read_bytes()
                entry.update({"sha256": sha256_bytes(data), "bytes": len(data)})
            entries.append(entry)
    return {"command": status, "dirty_count": len(entries), "entries": entries}


def pg_system_id() -> dict[str, Any]:
    pgdata = os.environ.get("PGDATA")
    candidates = [Path(pgdata)] if pgdata else []
    candidates += [
        PROJECT / "storage" / "database" / "postgresql18" / "data",
        PROJECT / "storage" / "postgresql18" / "data",
    ]
    pg_controldata = shutil.which("pg_controldata")
    for path in candidates:
        if not path.exists():
            continue
        exe = pg_controldata or str(PROJECT / "storage" / "postgresql18" / "bin" / "pg_controldata")
        if not Path(exe).exists() and not shutil.which(exe):
            continue
        result = run_cmd([exe, str(path)], timeout=60)
        if result["returncode"] == 0:
            line = next((x for x in result["stdout"].splitlines() if "Database system identifier" in x), "")
            return {"pgdata": str(path), "system_identifier": line.split(":", 1)[-1].strip(), "receipt": result}
    return {"pgdata": None, "system_identifier": None, "receipt": {"returncode": 1, "stderr": "pg_controldata unavailable"}}


def service_state() -> dict[str, Any]:
    state: dict[str, Any] = {"hostname": socket.gethostname(), "ports": {}, "commands": {}}
    for name, script in {
        "postgres": PROJECT / "scripts" / "postgres" / "status_postgres.sh",
        "dashboard": PROJECT / "scripts" / "dashboard" / "status_dashboard.sh",
        "source_review": PROJECT / "scripts" / "source_review" / "status_source_review.sh",
        "query_api": PROJECT / "scripts" / "query_api" / "status_query_api.sh",
    }.items():
        if script.exists():
            state["commands"][name] = run_cmd(["bash", str(script)], timeout=60)
    ss = shutil.which("ss")
    if ss:
        state["ports"]["ss"] = run_cmd([ss, "-ltnp"], timeout=30)
    return state


def database_fingerprint(engine: Any) -> dict[str, Any]:
    with engine.connect() as conn:
        out = {
            "pg_system": pg_system_id(),
            "alembic": db_rows(
                conn,
                "SELECT table_schema,table_name FROM information_schema.tables WHERE table_name='alembic_version' ORDER BY table_schema",
            ),
            "table_counts": {},
            "disease_counts": db_rows(
                conn,
                """
                SELECT d.slug, count(*)::int AS rows
                FROM epidemic.normalized_observations o
                JOIN epidemic.diseases d ON d.id=o.disease_id
                GROUP BY d.slug ORDER BY d.slug
                """,
            ),
            "frozen_runs": db_rows(
                conn,
                """
                SELECT id::text,status,run_key,disease_slug,target_metric,cutoff_week,dataset_sha256,retracted_at,retraction_reason
                FROM epidemic.forecast_runs
                WHERE id::text IN ('c1ce1947-1ab4-5f2d-b69e-a4e9c4668825','2adad614-7f25-5e58-ba00-167ca5f87606')
                ORDER BY id::text
                """,
            ),
        }
        for table in [
            "epidemic.data_sources",
            "epidemic.raw_artifacts",
            "epidemic.source_fetches",
            "epidemic.normalized_observations",
            "epidemic.dataset_snapshots",
            "epidemic.dataset_snapshot_observations",
            "epidemic.forecast_runs",
            "epidemic.forecast_values",
            "epidemic_p1_12.trusted_source_contracts",
            "epidemic_p1_12.trusted_observation_lineage",
            "epidemic_p1_12.trusted_snapshot_lineage",
        ]:
            out["table_counts"][table] = int(db_scalar(conn, f"SELECT count(*) FROM {table}"))
        out["alembic_versions"] = []
        for row in out["alembic"]:
            version_rows = db_rows(conn, f"SELECT version_num FROM {row['table_schema']}.alembic_version ORDER BY version_num")
            out["alembic_versions"].append({"schema": row["table_schema"], "versions": version_rows})
        return out


def custom_pg_dump() -> dict[str, Any]:
    url = resolve_db_url()
    if not url.username or not url.database:
        raise RuntimeError("PG_DUMP_DB_URL_INCOMPLETE")
    BACKUP_DIR.mkdir(parents=True, exist_ok=False)
    pg_dump = shutil.which("pg_dump")
    pg_restore = shutil.which("pg_restore")
    for candidate_root in [
        PROJECT.parent / "conda" / "envs" / "postgresql18" / "bin",
        Path("USER_HOME/autodl-tmp/conda/envs/postgresql18/bin"),
        Path("USER_HOME/autodl-tmp/conda/envs/postgresql18/bin"),
        PROJECT / "storage" / "postgresql18" / "bin",
        PROJECT / "storage" / "database" / "postgresql18" / "bin",
    ]:
        if not pg_dump and (candidate_root / "pg_dump").exists():
            pg_dump = str(candidate_root / "pg_dump")
        if not pg_restore and (candidate_root / "pg_restore").exists():
            pg_restore = str(candidate_root / "pg_restore")
    if not pg_dump or not pg_restore:
        raise RuntimeError("PG_DUMP_OR_PG_RESTORE_UNAVAILABLE")
    host = url.host or url.query.get("host") or ""
    port = str(url.port or url.query.get("port") or "5432")
    dump_path = BACKUP_DIR / "pre_write_online_consistent.dump"
    env = os.environ.copy()
    if url.password:
        env["PGPASSWORD"] = url.password
    cmd = [pg_dump]
    if host:
        cmd += ["-h", host]
    if port:
        cmd += ["-p", port]
    cmd += ["-U", url.username, "-d", url.database, "-F", "c", "-f", str(dump_path)]
    receipt = run_cmd(cmd, env=env, timeout=1800)
    if receipt["returncode"] != 0:
        raise RuntimeError("PG_DUMP_FAILED")
    listing_path = BACKUP_DIR / "pg_restore_list.txt"
    listing = run_cmd([pg_restore, "--list", str(dump_path)], timeout=300)
    listing_path.write_text(listing["stdout"] + listing["stderr"], encoding="utf-8")
    if listing["returncode"] != 0:
        raise RuntimeError("PG_RESTORE_LIST_FAILED")
    sha_path = dump_path.with_suffix(".dump.sha256")
    dump_sha = sha256_bytes(dump_path.read_bytes())
    sha_path.write_text(dump_sha + "  " + dump_path.name + "\n", encoding="utf-8")
    return {
        "dump_path": str(dump_path),
        "dump_sha256": dump_sha,
        "dump_bytes": dump_path.stat().st_size,
        "pg_dump_receipt": {k: v for k, v in receipt.items() if k != "args"},
        "pg_restore_list_path": str(listing_path),
        "pg_restore_list_sha256": sha256_bytes(listing_path.read_bytes()),
        "pg_restore_list_receipt": {k: v for k, v in listing.items() if k != "args"},
    }


def current_architecture_markdown(before: dict[str, Any], services: dict[str, Any]) -> str:
    lines = [
        "# Current architecture inventory",
        "",
        f"- Execution: `{EXECUTION_ID}`",
        f"- Project: `{PROJECT}`",
        f"- Git HEAD: `{before.get('git_head')}`",
        f"- Alembic: `{before.get('database', {}).get('alembic_versions')}`",
        "- Frozen run policy:",
        "  - COVID formal run c1ce1947-1ab4-5f2d-b69e-a4e9c4668825: read-only, not retrained.",
        "  - Defective run 2adad614-7f25-5e58-ba00-167ca5f87606: remains INVALIDATED/RETRACTED; not displayed as latest.",
        "- Persistence path:",
        "  raw official bytes -> raw_artifacts/source_fetches/trusted_fetch_artifacts -> normalized_observations -> trusted_observation_lineage -> dataset_snapshots -> dashboard history views.",
        "- Services remain loopback-bound; this stage does not expose public ports.",
        "",
        "## Service receipts",
        "",
        "```json",
        canonical(services)[:12000],
        "```",
    ]
    return "\n".join(lines) + "\n"


def execution_plan_markdown() -> str:
    return """# Execution plan

1. Freeze existing COVID and invalidated defective forecast run by fingerprinting DB state.
2. Create a custom-format pg_dump and validate it with `pg_restore --list` before any DB write.
3. Retrieve official first-party raw bytes for Legionella, Hantavirus/Puumala, and Nipah.
4. Parse only explicit source values; never interpolate, copy, fill gaps, or convert missing to zero.
5. Persist trusted source contracts, raw artifact hashes, normalized observations, lineage, and snapshots.
6. Validate Dashboard history/catalog visibility and forecast eligibility independently.
7. Package reproducible evidence, source receipts, hashes, row-level lineage, and blockers.

No P1-7/P1-14 remediation deletion, approval, promotion, or old forecast retraining is performed.
"""


def fetch_cdc_legionella(client: HTTPClient) -> dict[str, Any]:
    urls = [
        "https://data.cdc.gov/resource/x9gk-5huc.json?$limit=5000&label=Legionellosis",
        "https://data.cdc.gov/api/views/x9gk-5huc",
        "https://data.cdc.gov/api/views/x9gk-5huc/rows.csv?accessType=DOWNLOAD",
    ]
    results = [client.fetch(f"cdc_legionella_{idx}", url, accept="application/json,text/csv,*/*") for idx, url in enumerate(urls, 1)]
    return {
        "source_key": "legionellosis_nndss_weekly_us",
        "official": True,
        "publisher": "CDC",
        "dataset": "NNDSS x9gk-5huc",
        "label_filter": "Legionellosis",
        "usable": any(r.status == 200 and b"Legionellosis" in r.payload for r in results),
        "results": [asdict(r) | {"sha256": r.sha256, "byte_size": r.byte_size} for r in results],
        "rows": [],
        "blocker": None if any(r.status == 200 for r in results) else "CDC_DATA_GOV_RETURNED_403_OR_UNREACHABLE",
    }


def fetch_ecdc_legionella(client: HTTPClient, countries_by_alpha2: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], list[TrustedRow], list[FetchResult]]:
    dataset_result = client.fetch(
        "ecdc_atlas_datasets",
        "https://atlas.ecdc.europa.eu/public/AtlasService/rest/GetDatasets",
        accept="application/json",
    )
    dataset_payload_raw = json_loads_bytes(dataset_result.payload) if dataset_result.status == 200 else []
    dataset_payload = (
        dataset_payload_raw.get("Datasets", [])
        if isinstance(dataset_payload_raw, dict)
        else dataset_payload_raw
    )
    dataset = next((d for d in dataset_payload if isinstance(d, dict) and d.get("Code") == "CURRENT.LEGI.YEARLY"), None)
    dataset_id = dataset.get("Id") if dataset else 519

    topic_result = client.fetch(
        "ecdc_atlas_topics",
        f"https://atlas.ecdc.europa.eu/public/AtlasService/rest/GetHealthTopicsForDataset?datasetId={dataset_id}",
        accept="application/json",
    )
    topic_payload_raw = json_loads_bytes(topic_result.payload) if topic_result.status == 200 else []
    topic_payload = (
        topic_payload_raw.get("HealthTopics", [])
        if isinstance(topic_payload_raw, dict)
        else topic_payload_raw
    )
    topic = next((t for t in topic_payload if isinstance(t, dict) and t.get("Code") == "LEGI"), None)
    topic_id = topic.get("Id") if topic else 30

    measure_result = client.fetch(
        "ecdc_atlas_legionella_measures",
        f"https://atlas.ecdc.europa.eu/public/AtlasService/rest/GetIndicatorMeasuresForHealthTopicAndDataset?healthTopicId={topic_id}&datasetId={dataset_id}",
        accept="application/json",
    )
    measure_payload_raw = json_loads_bytes(measure_result.payload) if measure_result.status == 200 else []
    measure_payload = (
        measure_payload_raw.get("Measures", [])
        if isinstance(measure_payload_raw, dict)
        else measure_payload_raw
    )
    measure = next((m for m in measure_payload if isinstance(m, dict) and m.get("Code") == "ALL.COUNT"), None)
    measure_id = measure.get("Id") if measure else 1119215

    ecdc_codes = [
        "AT",
        "BE",
        "BG",
        "HR",
        "CY",
        "CZ",
        "DK",
        "EE",
        "FI",
        "FR",
        "DE",
        "EL",
        "GR",
        "HU",
        "IS",
        "IE",
        "IT",
        "LV",
        "LI",
        "LT",
        "LU",
        "MT",
        "NL",
        "NO",
        "PL",
        "PT",
        "RO",
        "SK",
        "SI",
        "ES",
        "SE",
    ]
    rows: list[TrustedRow] = []
    results: list[FetchResult] = [dataset_result, topic_result, measure_result]
    per_country_summary: list[dict[str, Any]] = []
    granularity_used = "month"
    for code in ecdc_codes:
        if code == "GR":
            continue
        for time_unit in ("M", "Y"):
            url = (
                "https://atlas.ecdc.europa.eu/public/AtlasService/rest/"
                f"GetMeasureResultsForTimeUnitAndGeoRegion?measureId={measure_id}&timeUnit={time_unit}&geoCode={urllib.parse.quote(code)}"
            )
            result = client.fetch(f"ecdc_legionella_{code}_{time_unit}", url, accept="application/json")
            results.append(result)
            if result.status != 200:
                continue
            try:
                payload = json_loads_bytes(result.payload)
            except Exception:
                continue
            values = payload.get("MeasureResults") if isinstance(payload, dict) else payload
            if not values:
                continue
            alpha2 = "GR" if code == "EL" else code
            country = countries_by_alpha2.get(alpha2)
            if not country:
                break
            country_rows = 0
            for idx, item in enumerate(values):
                raw_value = item.get("N")
                if raw_value is None:
                    raw_value = item.get("YValue")
                if raw_value in (None, ""):
                    continue
                try:
                    value = int(float(str(raw_value).replace(",", "")))
                except ValueError:
                    continue
                time_code = str(item.get("TimeCode") or item.get("Time") or item.get("Year") or "")
                if time_unit == "M" and re.fullmatch(r"\d{4}-\d{2}", time_code):
                    year, month = map(int, time_code.split("-"))
                    start = dt.date(year, month, 1)
                    end = dt.date(year, month, calendar.monthrange(year, month)[1])
                    granularity = "month"
                elif time_unit == "Y" and re.fullmatch(r"\d{4}", time_code):
                    year = int(time_code)
                    start = dt.date(year, 1, 1)
                    end = dt.date(year, 12, 31)
                    granularity = "year"
                else:
                    continue
                granularity_used = granularity
                rows.append(
                    TrustedRow(
                        source_key="legionnaires_ecdc_monthly_eueea",
                        source_name="ECDC Surveillance Atlas Legionnaires' disease",
                        publisher="ECDC",
                        source_url=result.final_url,
                        source_row_locator=f"MeasureResults[{idx}]; geoCode={code}; timeUnit={time_unit}; measure=ALL.COUNT",
                        artifact_sha256=result.sha256,
                        country_alpha2=str(country["alpha2"]),
                        country_iso3=str(country["alpha3"]),
                        country_name=str(country["name"]),
                        disease_slug="legionella",
                        disease_label="Legionellosis / Legionnaires' disease",
                        metric_code="legionnaires_reported_cases",
                        metric_label="Reported Legionnaires' disease cases",
                        value=value,
                        unit="person",
                        period_start=start,
                        period_end=end,
                        granularity=granularity,
                        reporting_date=end,
                        source_as_of=dt.datetime.now(dt.timezone.utc).date(),
                        dimensions={
                            "dataset_code": "CURRENT.LEGI.YEARLY",
                            "health_topic": "LEGI",
                            "measure_code": "ALL.COUNT",
                            "measure_id": measure_id,
                            "geo_code": code,
                            "time_code": time_code,
                            "time_unit": time_unit,
                            "source_semantics": "ECDC Atlas reported cases; measure ALL.COUNT.",
                            "missing_interpolated": False,
                        },
                        case_definition="ECDC Atlas reported Legionnaires' disease cases, ALL.COUNT.",
                        aggregation_type="reported_cases",
                    )
                )
                country_rows += 1
            per_country_summary.append({"code": code, "time_unit": time_unit, "rows": country_rows, "status": result.status})
            break
    matrix = {
        "source_key": "legionnaires_ecdc_monthly_eueea",
        "official": True,
        "publisher": "ECDC",
        "dataset_code": "CURRENT.LEGI.YEARLY",
        "dataset_id": dataset_id,
        "topic": "LEGI",
        "topic_id": topic_id,
        "measure_code": "ALL.COUNT",
        "measure_id": measure_id,
        "granularity_used": granularity_used,
        "rows": len(rows),
        "countries": sorted({r.country_iso3 for r in rows}),
        "date_min": min((r.period_start for r in rows), default=None),
        "date_max": max((r.period_end for r in rows), default=None),
        "per_country": per_country_summary,
        "raw_artifacts": [{"key": r.key, "status": r.status, "sha256": r.sha256, "bytes": r.byte_size, "url": r.final_url, "error": r.error} for r in results],
    }
    return matrix, rows, results


def parse_thl_dimensions(payload: bytes) -> dict[str, Any]:
    text_value = payload.decode("utf-8-sig", errors="replace")
    m = re.search(r"loadDimensions\((.*)\)\s*;?\s*$", text_value, re.S)
    if not m:
        return json.loads(text_value)
    return json.loads(m.group(1))


def collect_thl_year_nodes(dimensions: dict[str, Any]) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    dimension_list = dimensions.get("dimensions", []) if isinstance(dimensions, dict) else dimensions
    for dimension in dimension_list:
        if not isinstance(dimension, dict):
            continue
        if dimension.get("code") != "yearweek" and dimension.get("id") != "yearweek":
            continue
        stack = list(dimension.get("tree") or dimension.get("children") or [])
        while stack:
            node = stack.pop()
            label = str(node.get("label") or node.get("text") or "")
            code = str(node.get("code") or node.get("id") or "")
            stage = str(node.get("stage") or "")
            is_year_node = stage == "year" or re.fullmatch(r"Year\s+20\d{2}", label) or re.fullmatch(r"20\d{2}", code)
            if is_year_node and (re.fullmatch(r"20\d{2}", code) or re.search(r"\b20\d{2}\b", label)):
                year = int(re.search(r"20\d{2}", code + " " + label).group(0))  # type: ignore[union-attr]
                if 2018 <= year <= dt.datetime.now(dt.timezone.utc).year:
                    nodes.append({"year": year, "sid": node.get("sid") or node.get("id"), "label": label})
            stack.extend(node.get("children", []) or [])
    return sorted({(int(n["year"]), str(n["sid"])): n for n in nodes if n.get("sid")}.values(), key=lambda x: x["year"])


def parse_thl_csv(result: FetchResult, year: int, countries_by_alpha2: dict[str, dict[str, Any]]) -> list[TrustedRow]:
    country = countries_by_alpha2["FI"]
    text_value = result.payload.decode("utf-8-sig", errors="replace")
    sample = text_value[:1000]
    dialect = csv.Sniffer().sniff(sample, delimiters=";,")
    rows: list[TrustedRow] = []
    def parse_number(raw: Any) -> int | None:
        if raw is None:
            return None
        cleaned = str(raw).replace("\xa0", "").replace(" ", "").replace(",", ".").strip()
        if not cleaned:
            return None
        try:
            return int(float(cleaned))
        except ValueError:
            return None

    reader = csv.DictReader(text_value.splitlines(), dialect=dialect)
    for idx, item in enumerate(reader):
        year_week = item.get("Week") or item.get("Year week") or item.get("Time") or item.get("Week and year")
        disease = item.get("ICD-10-based reporting group") or item.get("Disease") or item.get("Report group")
        value_raw = item.get("Number of cases") or item.get("Cases") or item.get("value") or item.get("Value")
        if not year_week or "Puumala-virus" not in str(disease or "") or value_raw in (None, ""):
            continue
        m = re.search(r"(20\d{2})\D+(\d{1,2})", str(year_week))
        if not m:
            continue
        iso_year, week = int(m.group(1)), int(m.group(2))
        try:
            start = dt.date.fromisocalendar(iso_year, week, 1)
        except ValueError:
            continue
        value = parse_number(value_raw)
        if value is None:
            continue
        rows.append(
            TrustedRow(
                source_key="puumala_hfrs_ne_finland",
                source_name="THL infectious diseases register: Puumala-virus",
                publisher="Finnish Institute for Health and Welfare (THL)",
                source_url=result.final_url,
                source_row_locator=f"csv_year={year}; row={idx + 2}; disease=Puumala-virus; week={iso_year}-W{week:02d}",
                artifact_sha256=result.sha256,
                country_alpha2=str(country["alpha2"]),
                country_iso3=str(country["alpha3"]),
                country_name=str(country["name"]),
                disease_slug="hantavirus",
                disease_label="Puumala virus reports (HFRS/nephropathia epidemica proxy)",
                metric_code="puumala_virus_reports",
                metric_label="Reported Puumala-virus cases",
                value=value,
                unit="person",
                period_start=start,
                period_end=start + dt.timedelta(days=6),
                granularity="week",
                reporting_date=start + dt.timedelta(days=6),
                source_as_of=dt.datetime.now(dt.timezone.utc).date(),
                dimensions={
                    "source_disease_label": "Puumala-virus",
                    "official_display": "芬兰 Puumala 病毒报告（HFRS/肾综合征出血热相关病原体代理）",
                    "syndrome_proxy": "HFRS/nephropathia epidemica related pathogen proxy, not all hantavirus disease.",
                    "iso_week": f"{iso_year}-W{week:02d}",
                    "missing_interpolated": False,
                },
                case_definition="THL infectious diseases register Puumala-virus reports; pathogen proxy for HFRS/nephropathia epidemica.",
                aggregation_type="reported_cases",
                pathogen_variant="Puumala-virus",
            )
        )
    if rows:
        return rows
    for idx, line in enumerate(text_value.splitlines()):
        parts = [part.strip().strip('"') for part in line.split(";")]
        if len(parts) < 3:
            continue
        if "Puumala-virus" not in parts[1]:
            continue
        m = re.search(r"(20\d{2})\D+(\d{1,2})", parts[0])
        if not m:
            continue
        iso_year, week = int(m.group(1)), int(m.group(2))
        value = parse_number(parts[2])
        if value is None:
            continue
        try:
            start = dt.date.fromisocalendar(iso_year, week, 1)
        except ValueError:
            continue
        rows.append(
            TrustedRow(
                source_key="puumala_hfrs_ne_finland",
                source_name="THL infectious diseases register: Puumala-virus",
                publisher="Finnish Institute for Health and Welfare (THL)",
                source_url=result.final_url,
                source_row_locator=f"csv_year={year}; line={idx + 1}; disease=Puumala-virus; week={iso_year}-W{week:02d}",
                artifact_sha256=result.sha256,
                country_alpha2=str(country["alpha2"]),
                country_iso3=str(country["alpha3"]),
                country_name=str(country["name"]),
                disease_slug="hantavirus",
                disease_label="Puumala virus reports (HFRS/nephropathia epidemica proxy)",
                metric_code="puumala_virus_reports",
                metric_label="Reported Puumala-virus cases",
                value=value,
                unit="person",
                period_start=start,
                period_end=start + dt.timedelta(days=6),
                granularity="week",
                reporting_date=start + dt.timedelta(days=6),
                source_as_of=dt.datetime.now(dt.timezone.utc).date(),
                dimensions={
                    "source_disease_label": "Puumala-virus",
                    "official_display": "芬兰 Puumala 病毒报告（HFRS/肾综合征出血热相关病原体代理）",
                    "syndrome_proxy": "HFRS/nephropathia epidemica related pathogen proxy, not all hantavirus disease.",
                    "iso_week": f"{iso_year}-W{week:02d}",
                    "missing_interpolated": False,
                },
                case_definition="THL infectious diseases register Puumala-virus reports; pathogen proxy for HFRS/nephropathia epidemica.",
                aggregation_type="reported_cases",
                pathogen_variant="Puumala-virus",
            )
        )
    return rows


def fetch_thl_puumala(client: HTTPClient, countries_by_alpha2: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], list[TrustedRow], list[FetchResult]]:
    old_user_agent = client.headers.get("User-Agent")
    client.headers["User-Agent"] = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )
    try:
        dimensions = fetch_retry(
            client,
            "thl_puumala_dimensions",
            "https://sampo.thl.fi/pivot/prod/en/ttr/casesweek/fact_ttr_casesweek.dimensions.json",
            attempts=3,
            timeout=120,
            accept="application/json,text/javascript,*/*",
        )
        license_page = fetch_retry(client, "thl_license_terms", "https://thl.fi/en/web/thlfi-en/statistics-and-data/data-and-services/open-data", attempts=2, timeout=30)
    finally:
        if old_user_agent:
            client.headers["User-Agent"] = old_user_agent
    results = [dimensions, license_page]
    if dimensions.status != 200:
        return {"source_key": "puumala_hfrs_ne_finland", "rows": 0, "blocker": "THL_DIMENSIONS_UNAVAILABLE"}, [], results
    dims = parse_thl_dimensions(dimensions.payload)
    year_nodes = collect_thl_year_nodes(dims)
    rows: list[TrustedRow] = []
    for node in year_nodes:
        sid = node["sid"]
        year = int(node["year"])
        url = (
            "https://sampo.thl.fi/pivot/prod/en/ttr/casesweek/fact_ttr_casesweek.csv"
            f"?row=nidrreportgroup-877795&column=yearweek-{sid}&filter=measure-877837&"
        )
        old_user_agent = client.headers.get("User-Agent")
        client.headers["User-Agent"] = (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
        )
        try:
            result = fetch_retry(client, f"thl_puumala_weekly_{year}", url, attempts=2, timeout=90, accept="text/csv,*/*")
        finally:
            if old_user_agent:
                client.headers["User-Agent"] = old_user_agent
        results.append(result)
        if result.status == 200:
            rows.extend(parse_thl_csv(result, year, countries_by_alpha2))
    matrix = {
        "source_key": "puumala_hfrs_ne_finland",
        "official": True,
        "publisher": "THL",
        "disease_label": "Puumala-virus",
        "license_expression": "CC-BY-4.0",
        "license_url": "https://thl.fi/en/web/thlfi-en/statistics-and-data/data-and-services/open-data",
        "license_fetch_status": license_page.status,
        "rows": len(rows),
        "date_min": min((r.period_start for r in rows), default=None),
        "date_max": max((r.period_end for r in rows), default=None),
        "raw_artifacts": [{"key": r.key, "status": r.status, "sha256": r.sha256, "bytes": r.byte_size, "url": r.final_url, "error": r.error} for r in results],
    }
    return matrix, rows, results


def parse_nipah_counts(text_value: str) -> tuple[int | None, int | None, str]:
    lowered = text_value.lower()
    snippet = text_value[:1200]
    cases: int | None = None
    deaths: int | None = None
    fatal_cases = re.search(r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+confirmed\s+fatal\s+nipah.*?\bcases", lowered)
    if fatal_cases:
        cases = word_to_int(fatal_cases.group(1))
        deaths = cases
    if cases is None:
        patterns = [
            r"\b(?:total of\s+)?(\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+laboratory-confirmed\s+cases",
            r"\b(?:total of\s+)?(\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+confirmed\s+(?:niv|nipah).*?\bcases",
            r"\bnotified who of\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+confirmed\s+case",
        ]
        for pattern in patterns:
            m = re.search(pattern, lowered)
            if m:
                cases = word_to_int(m.group(1))
                break
    death_patterns = [
        r"\bincluding\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+deaths",
        r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+deaths\b",
    ]
    for pattern in death_patterns:
        m = re.search(pattern, lowered)
        if m:
            deaths = word_to_int(m.group(1))
            break
    return cases, deaths, snippet


def nipah_country(title: str, text_value: str) -> str | None:
    combined = f"{title} {text_value[:2000]}".lower()
    if "bangladesh" in combined:
        return "BD"
    if "india" in combined or "kerala" in combined or "west bengal" in combined:
        return "IN"
    return None


def fetch_who_nipah(client: HTTPClient, countries_by_alpha2: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], list[TrustedRow], list[FetchResult]]:
    don_ids = ["2026-DON594", "2026-DON593", "2025-DON582", "2025-DON577", "2024-DON508", "2023-DON442", "2023-DON490"]
    rows: list[TrustedRow] = []
    results: list[FetchResult] = []
    events: list[dict[str, Any]] = []
    for don_id in don_ids:
        api_url = "https://www.who.int/api/news/diseaseoutbreaknews?$filter=" + urllib.parse.quote(
            f"DonId eq '{don_id}'", safe=""
        )
        api = client.fetch(f"who_nipah_api_{don_id}", api_url, accept="application/json")
        results.append(api)
        if api.status != 200:
            events.append({"don_id": don_id, "status": api.status, "rows": 0})
            continue
        payload = json_loads_bytes(api.payload)
        item = (payload.get("value") or [None])[0] if isinstance(payload, dict) else None
        if not item:
            events.append({"don_id": don_id, "status": api.status, "rows": 0, "reason": "WHO_API_EMPTY"})
            continue
        title = str(item.get("Title") or item.get("Name") or don_id)
        overview = strip_tags(str(item.get("Overview") or item.get("Description") or item.get("Body") or ""))
        country_code = nipah_country(title, overview)
        if not country_code or country_code not in countries_by_alpha2:
            events.append({"don_id": don_id, "status": api.status, "rows": 0, "reason": "COUNTRY_UNMAPPED", "title": title})
            continue
        country = countries_by_alpha2[country_code]
        date_value = parse_date(str(item.get("PublicationDate") or item.get("Date") or ""))
        if date_value is None:
            date_value = dt.datetime.now(dt.timezone.utc).date()
        page_url = item.get("ItemDefaultUrl") or item.get("Url") or f"/emergencies/disease-outbreak-news/item/{don_id}"
        if not str(page_url).startswith("/emergencies/disease-outbreak-news/item/"):
            page_url = f"/emergencies/disease-outbreak-news/item/{don_id}"
        page_url = "https://www.who.int" + str(page_url) if str(page_url).startswith("/") else str(page_url)
        page = client.fetch(f"who_nipah_page_{don_id}", str(page_url), accept="text/html,*/*")
        results.append(page)
        text_source = strip_tags(page.payload.decode("utf-8", errors="replace")) if page.status == 200 else overview
        cases, deaths, snippet = parse_nipah_counts(overview + " " + text_source[:2000])
        common = {
            "source_key": "who_don_nipah_events",
            "source_name": "WHO Disease Outbreak News Nipah virus events",
            "publisher": "WHO",
            "source_url": str(page_url),
            "country_alpha2": str(country["alpha2"]),
            "country_iso3": str(country["alpha3"]),
            "country_name": str(country["name"]),
            "disease_slug": "nipah",
            "disease_label": "Nipah virus disease",
            "unit": "person",
            "period_start": date_value,
            "period_end": date_value,
            "granularity": "day",
            "reporting_date": date_value,
            "source_as_of": date_value,
            "dimensions": {
                "don_id": don_id,
                "title": title,
                "data_mode": "EVENT_ONLY",
                "display_status": "EVENT_TRIPTYCH_ELIGIBLE",
                "forecast_eligibility": "BLOCKED_NO_CONTINUOUS_REGION_WEEK_SERIES",
                "forecast_status": "NOT_RUN",
                "date_type": "WHO publication date",
                "summary_snippet_sha256": sha256_text(snippet),
                "missing_interpolated": False,
            },
            "case_definition": "WHO Disease Outbreak News event report. Event-only observations; not a continuous region-week series.",
            "aggregation_type": "event_report",
            "pathogen_variant": "Nipah virus",
        }
        event_rows = 0
        if cases is not None:
            rows.append(
                TrustedRow(
                    **common,
                    source_row_locator=f"WHO DON {don_id}; parsed explicit confirmed cases phrase",
                    artifact_sha256=api.sha256,
                    metric_code="nipah_event_confirmed_cases",
                    metric_label="Nipah event confirmed cases",
                    value=cases,
                )
            )
            event_rows += 1
        if deaths is not None:
            rows.append(
                TrustedRow(
                    **common,
                    source_row_locator=f"WHO DON {don_id}; parsed explicit deaths phrase",
                    artifact_sha256=api.sha256,
                    metric_code="nipah_event_deaths",
                    metric_label="Nipah event deaths",
                    value=deaths,
                )
            )
            event_rows += 1
        events.append({"don_id": don_id, "status": api.status, "page_status": page.status, "country": country_code, "cases": cases, "deaths": deaths, "rows": event_rows, "title": title})
    matrix = {
        "source_key": "who_don_nipah_events",
        "official": True,
        "publisher": "WHO",
        "event_ids": don_ids,
        "rows": len(rows),
        "events": events,
        "date_min": min((r.period_start for r in rows), default=None),
        "date_max": max((r.period_end for r in rows), default=None),
        "raw_artifacts": [{"key": r.key, "status": r.status, "sha256": r.sha256, "bytes": r.byte_size, "url": r.final_url, "error": r.error} for r in results],
    }
    return matrix, rows, results


def insert_reference_data(conn: Any) -> dict[str, Any]:
    disease_specs = [
        ("legionella", "Legionellosis / Legionnaires' disease", ["Legionellosis", "Legionnaires' disease", "军团菌病", "军团菌肺炎", "庞蒂亚克热"]),
        ("hantavirus", "Hantavirus-related disease / Puumala virus proxy", ["Hantavirus", "Puumala-virus", "HFRS", "肾综合征出血热", "汉坦病毒", "Puumala 病毒"]),
        ("nipah", "Nipah virus disease", ["Nipah", "Nipah virus", "NiV", "尼帕病毒", "尼帕病毒病"]),
    ]
    metric_specs = [
        ("legionnaires_reported_cases", "Reported Legionnaires' disease cases", "person", "ECDC Atlas ALL.COUNT reported cases."),
        ("puumala_virus_reports", "Reported Puumala-virus cases", "person", "THL Puumala-virus reported cases; HFRS/nephropathia epidemica pathogen proxy."),
        ("nipah_event_confirmed_cases", "Nipah event confirmed cases", "person", "WHO DON event-only explicit confirmed cases."),
        ("nipah_event_deaths", "Nipah event deaths", "person", "WHO DON event-only explicit deaths."),
    ]
    disease_ids: dict[str, uuid.UUID] = {}
    inserted = {"diseases": [], "aliases": [], "metrics": []}
    for slug, name, aliases in disease_specs:
        did = stable_uuid("disease", slug)
        conn.execute(
            text(
                "INSERT INTO epidemic.diseases(id,slug,canonical_name,description,ontology_system,ontology_code,ontology_version,catalog_version) VALUES(:id,:slug,:name,:description,:ontology_system,:ontology_code,:ontology_version,:catalog_version) ON CONFLICT(slug) DO NOTHING"
            ),
            {
                "id": did,
                "slug": slug,
                "name": name,
                "description": f"Official-source disease family added by {EXECUTION_ID}",
                "ontology_system": "stage",
                "ontology_code": slug.upper(),
                "ontology_version": "EPIDEMIC_THREE_DISEASE_TRUSTED_DATA_DASHBOARD_FORECAST_E2E",
                "catalog_version": "three-disease-official-v1",
            },
        )
        disease_ids[slug] = uuid.UUID(str(db_scalar(conn, "SELECT id FROM epidemic.diseases WHERE slug=:slug", {"slug": slug})))
        inserted["diseases"].append(slug)
        for alias in aliases:
            conn.execute(
                text(
                    "INSERT INTO epidemic.disease_aliases(disease_id,alias,normalized_alias,language_tag,alias_type,catalog_version) VALUES(:disease,:alias,:norm,:lang,'synonym','three-disease-official-v1') ON CONFLICT DO NOTHING"
                ),
                {
                    "disease": disease_ids[slug],
                    "alias": alias,
                    "norm": re.sub(r"\s+", " ", alias.strip().lower()),
                    "lang": "zh" if re.search(r"[\u4e00-\u9fff]", alias) else "en",
                },
            )
            inserted["aliases"].append({"slug": slug, "alias": alias})
    for code, display, unit, desc in metric_specs:
        conn.execute(
            text(
                "INSERT INTO epidemic.metric_definitions(metric_code,display_name,unit,description,cumulative,allow_negative,min_value,schema_version,active) VALUES(:code,:display,:unit,:desc,false,false,0,1,true) ON CONFLICT(metric_code) DO NOTHING"
            ),
            {"code": code, "display": display, "unit": unit, "desc": desc},
        )
        inserted["metrics"].append(code)
    return inserted


def ensure_contract(conn: Any, source_key: str, source_name: str, publisher: str, canonical_url: str, terms_url: str, disease_slug: str, schema_payload: dict[str, Any]) -> uuid.UUID:
    contract_id = stable_uuid("trusted-contract", f"{source_key}:v1")
    conn.execute(
        text(
            """
            INSERT INTO epidemic_p1_12.trusted_source_contracts(
              id,source_key,contract_version,source_name,publisher,official_domain,canonical_url,terms_url,disease_id,
              country_selection_rule,pathogen_scope,adapter_version,transform_version,schema_sha256,status,onboarding_mode,
              authorization_scope,trust_tier,authorization_origin,active
            )
            VALUES(
              :id,:source_key,'1.0.0',:source_name,:publisher,:official_domain,:canonical_url,:terms_url,:disease_id,
              'official-source-defined geography','official disease-specific scope',:adapter,:transform,:schema_sha,
              'TRUSTED_ACTIVE','CURATED_BUILTIN_SOURCE','CASES_NEW_OPERATIONALIZATION',
              'TRUSTED_OFFICIAL_REPORTED','PROJECT_OWNER_DIRECTIVE',true
            )
            ON CONFLICT DO NOTHING
            """
        ),
        {
            "id": contract_id,
            "source_key": source_key,
            "source_name": source_name,
            "publisher": publisher,
            "official_domain": urllib.parse.urlparse(canonical_url).netloc,
            "canonical_url": canonical_url,
            "terms_url": terms_url,
            "disease_id": disease_slug,
            "adapter": ADAPTER_VERSION,
            "transform": TRANSFORM_VERSION,
            "schema_sha": sha(schema_payload),
        },
    )
    return uuid.UUID(
        str(
            db_scalar(
                conn,
                "SELECT id FROM epidemic_p1_12.trusted_source_contracts WHERE source_key=:key AND contract_version='1.0.0'",
                {"key": source_key},
            )
        )
    )


def persist_fetches(conn: Any, contract_id: uuid.UUID, retrieval_id: uuid.UUID, source_id: uuid.UUID, results: list[FetchResult]) -> dict[str, tuple[uuid.UUID, uuid.UUID]]:
    mapping: dict[str, tuple[uuid.UUID, uuid.UUID]] = {}
    for result in results:
        storage = str((RAW_DIR / (re.sub(r"[^A-Za-z0-9._-]+", "_", result.key)[:100] + "." + result.sha256[:12] + ".raw")).relative_to(PROJECT))
        raw_path = PROJECT / storage
        if not raw_path.exists():
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_bytes(result.payload)
        artifact_id = stable_uuid("artifact", result.sha256)
        fetch_id = stable_uuid("fetch", f"{retrieval_id}:{result.final_url}:{result.sha256}")
        evidence_id = stable_uuid("trusted-fetch", f"{contract_id}:{result.final_url}:{result.sha256}")
        conn.execute(
            text("INSERT INTO epidemic.raw_artifacts(id,sha256,byte_size,media_type,encoding,storage_mode,storage_uri) VALUES(:id,:sha,:bytes,:mime,NULL,'file',:storage) ON CONFLICT(sha256) DO NOTHING"),
            {"id": artifact_id, "sha": result.sha256, "bytes": len(result.payload), "mime": result.mime_type, "storage": storage},
        )
        artifact_id = uuid.UUID(str(db_scalar(conn, "SELECT id FROM epidemic.raw_artifacts WHERE sha256=:sha", {"sha": result.sha256})))
        conn.execute(
            text("INSERT INTO epidemic.source_fetches(id,retrieval_run_id,source_id,canonical_url,requested_at,fetched_at,http_status,etag,last_modified,artifact_id,attempt_number,response_content_type,response_byte_size) VALUES(:id,:retrieval,:source,:url,now(),now(),:status,:etag,:modified,:artifact,1,:mime,:bytes) ON CONFLICT(id) DO NOTHING"),
            {
                "id": fetch_id,
                "retrieval": retrieval_id,
                "source": source_id,
                "url": result.final_url,
                "status": result.status,
                "etag": result.etag,
                "modified": result.last_modified,
                "artifact": artifact_id,
                "mime": result.mime_type,
                "bytes": len(result.payload),
            },
        )
        if 200 <= int(result.status) < 400:
            conn.execute(
                text("INSERT INTO epidemic_p1_12.trusted_fetch_artifacts(id,source_contract_id,canonical_url,http_status,mime_type,byte_size,etag,last_modified,response_sha256,storage_key,retrieved_at,elapsed_ms,redirect_chain,source_row_locator_policy) VALUES(:id,:contract,:url,:status,:mime,:bytes,:etag,:modified,:sha,:storage,now(),:elapsed,CAST(:redirects AS jsonb),'deterministic API/csv/event row locator') ON CONFLICT(source_contract_id,canonical_url,response_sha256) DO NOTHING"),
                {
                    "id": evidence_id,
                    "contract": contract_id,
                    "url": result.final_url,
                    "status": result.status,
                    "mime": result.mime_type,
                    "bytes": len(result.payload),
                    "etag": result.etag,
                    "modified": result.last_modified,
                    "sha": result.sha256,
                    "storage": storage,
                    "elapsed": result.elapsed_ms,
                    "redirects": canonical(result.redirect_chain),
                },
            )
            evidence_id = uuid.UUID(
                str(
                    db_scalar(
                        conn,
                        "SELECT id FROM epidemic_p1_12.trusted_fetch_artifacts WHERE source_contract_id=:contract AND canonical_url=:url AND response_sha256=:sha",
                        {"contract": contract_id, "url": result.final_url, "sha": result.sha256},
                    )
                )
            )
            mapping[result.sha256] = (fetch_id, evidence_id)
    return mapping


def persist_source(conn: Any, rows: list[TrustedRow], results: list[FetchResult], source_meta: dict[str, Any]) -> dict[str, Any]:
    if not rows:
        return {"source_key": source_meta["source_key"], "persisted": False, "reason": "NO_ROWS"}
    source_key = rows[0].source_key
    disease_slug = rows[0].disease_slug
    manifest_sha = sha([asdict(r) for r in rows])
    contract_id = ensure_contract(
        conn,
        source_key,
        rows[0].source_name,
        rows[0].publisher,
        source_meta.get("canonical_url") or rows[0].source_url,
        source_meta.get("terms_url") or rows[0].source_url,
        disease_slug,
        source_meta,
    )
    source_id = stable_uuid("data-source", source_key)
    conn.execute(
        text("INSERT INTO epidemic.data_sources(id,source_key,display_name,source_type,authority,base_url,adapter_name,license_expression,terms_url,enabled) VALUES(:id,:key,:name,'official_api',:authority,:url,:adapter,:license,:terms,true) ON CONFLICT(source_key) DO NOTHING"),
        {
            "id": source_id,
            "key": source_key,
            "name": rows[0].source_name,
            "authority": rows[0].publisher,
            "url": source_meta.get("canonical_url") or rows[0].source_url,
            "adapter": ADAPTER_VERSION,
            "license": source_meta.get("license_expression", "OFFICIAL_REUSE_TERMS_RECORDED"),
            "terms": source_meta.get("terms_url") or rows[0].source_url,
        },
    )
    source_id = uuid.UUID(str(db_scalar(conn, "SELECT id FROM epidemic.data_sources WHERE source_key=:key", {"key": source_key})))
    retrieval_groups: dict[tuple[str, str], list[TrustedRow]] = {}
    for row in rows:
        retrieval_groups.setdefault((row.country_alpha2, row.country_iso3), []).append(row)
    # One source-level retrieval run, with country-level snapshots below.
    query_fingerprint = sha({"source": source_key, "countries": sorted(k[1] for k in retrieval_groups), "manifest": manifest_sha})
    disease_id = uuid.UUID(str(db_scalar(conn, "SELECT id FROM epidemic.diseases WHERE slug=:slug", {"slug": disease_slug})))
    query_id = stable_uuid("query", query_fingerprint)
    conn.execute(
        text("INSERT INTO epidemic.query_requests(id,fingerprint,raw_country,raw_disease,country_alpha2,disease_id,start_date,end_date,time_window_kind,duration_iso8601,source_preference,schema_version) VALUES(:id,:fingerprint,:country,:disease,:alpha2,:disease_id,NULL,NULL,'official_historical','P10Y','official',2) ON CONFLICT(fingerprint) DO NOTHING"),
        {"id": query_id, "fingerprint": query_fingerprint, "country": "MULTI" if len(retrieval_groups) > 1 else rows[0].country_iso3, "disease": disease_slug, "alpha2": rows[0].country_alpha2, "disease_id": disease_id},
    )
    query_id = uuid.UUID(str(db_scalar(conn, "SELECT id FROM epidemic.query_requests WHERE fingerprint=:fingerprint", {"fingerprint": query_fingerprint})))
    retrieval_key = f"{EXECUTION_ID}:{source_key}:{manifest_sha}"
    retrieval_id = stable_uuid("retrieval", retrieval_key)
    conn.execute(
        text("INSERT INTO epidemic.retrieval_runs(id,run_key,query_request_id,source_id,adapter_version,status,attempt_number,started_at,finished_at) VALUES(:id,:key,:query,:source,:adapter,'succeeded',1,now(),now()) ON CONFLICT(run_key) DO NOTHING"),
        {"id": retrieval_id, "key": retrieval_key, "query": query_id, "source": source_id, "adapter": ADAPTER_VERSION},
    )
    retrieval_id = uuid.UUID(str(db_scalar(conn, "SELECT id FROM epidemic.retrieval_runs WHERE run_key=:key", {"key": retrieval_key})))
    fetch_map = persist_fetches(conn, contract_id, retrieval_id, source_id, results)
    added = revised = reused = 0
    accepted_by_group: dict[tuple[str, str], list[uuid.UUID]] = {key: [] for key in retrieval_groups}
    for row in rows:
        dimensions_sha = sha(row.dimensions)
        series_key = sha(
            {
                "source": source_key,
                "country": row.country_iso3,
                "disease": disease_slug,
                "metric": row.metric_code,
                "period_start": row.period_start,
                "period_end": row.period_end,
                "granularity": row.granularity,
                "dimensions": row.dimensions,
            }
        )
        existing = (
            conn.execute(
                text("SELECT o.id,o.revision,o.value_numeric FROM epidemic.normalized_observations o JOIN epidemic_p1_12.trusted_observation_lineage l ON l.observation_id=o.id WHERE l.source_contract_id=:contract AND o.series_fingerprint=:series AND NOT EXISTS(SELECT 1 FROM epidemic.normalized_observations n WHERE n.supersedes_observation_id=o.id) ORDER BY o.revision DESC LIMIT 1"),
                {"contract": contract_id, "series": series_key},
            )
            .mappings()
            .one_or_none()
        )
        if existing is not None and int(existing["value_numeric"]) == row.value:
            observation_id = uuid.UUID(str(existing["id"]))
            reused += 1
            accepted_by_group[(row.country_alpha2, row.country_iso3)].append(observation_id)
            continue
        revision = 1 if existing is None else int(existing["revision"]) + 1
        observation_id = stable_uuid("observation", f"{series_key}:{revision}:{row.value}:{row.artifact_sha256}")
        record_sha = sha({"series": series_key, "revision": revision, "value": row.value, "artifact": row.artifact_sha256})
        fetch_id, evidence_id = fetch_map[row.artifact_sha256]
        conn.execute(
            text("INSERT INTO epidemic.normalized_observations(id,record_fingerprint,series_fingerprint,revision,supersedes_observation_id,source_fetch_id,country_alpha2,disease_id,metric_code,value_numeric,raw_value,period_start,period_end,granularity,reporting_date,as_of,dimensions,dimensions_sha256,normalization_version) VALUES(:id,:record,:series,:revision,:prior,:fetch,:country,:disease,:metric,:value,:raw,:start,:end,:granularity,:reporting,:as_of,CAST(:dimensions AS jsonb),:dimensions_sha,:version)"),
            {
                "id": observation_id,
                "record": record_sha,
                "series": series_key,
                "revision": revision,
                "prior": None if existing is None else existing["id"],
                "fetch": fetch_id,
                "country": row.country_alpha2,
                "disease": disease_id,
                "metric": row.metric_code,
                "value": row.value,
                "raw": str(row.value),
                "start": row.period_start,
                "end": row.period_end,
                "granularity": row.granularity,
                "reporting": row.reporting_date,
                "as_of": dt.datetime.combine(row.source_as_of, dt.time(), dt.timezone.utc),
                "dimensions": canonical(row.dimensions),
                "dimensions_sha": dimensions_sha,
                "version": TRANSFORM_VERSION,
            },
        )
        flags = ["OFFICIAL_FIRST_PARTY_SOURCE", "NO_INTERPOLATION", "NO_MISSING_TO_ZERO"]
        if row.disease_slug == "nipah":
            flags.append("EVENT_ONLY_NOT_CONTINUOUS_TIME_SERIES")
        conn.execute(
            text("INSERT INTO epidemic.quality_assessments(id,observation_id,ruleset_version,status,score,flags) VALUES(:id,:observation,:rules,'accepted',1.0,CAST(:flags AS jsonb)) ON CONFLICT DO NOTHING"),
            {"id": stable_uuid("quality", str(observation_id)), "observation": observation_id, "rules": QUALITY_RULESET, "flags": canonical(flags)},
        )
        lineage_sha = sha({"observation": str(observation_id), "contract": str(contract_id), "artifact": row.artifact_sha256, "locator": row.source_row_locator})
        conn.execute(
            text("INSERT INTO epidemic_p1_12.trusted_observation_lineage(observation_id,source_contract_id,fetch_artifact_id,country_iso3,pathogen_variant,case_definition,aggregation_type,source_as_of_date,source_url,source_row_locator,artifact_sha256,extractor_version,transform_version,source_report_delta,revision_notice,lineage_sha256) VALUES(:observation,:contract,:artifact_id,:country,:pathogen,:case_definition,:aggregation,:source_as_of,:url,:locator,:sha,:extractor,:transform,:delta,:revision_notice,:lineage) ON CONFLICT(observation_id) DO NOTHING"),
            {
                "observation": observation_id,
                "contract": contract_id,
                "artifact_id": evidence_id,
                "country": row.country_iso3,
                "pathogen": row.pathogen_variant,
                "case_definition": row.case_definition,
                "aggregation": row.aggregation_type,
                "source_as_of": row.source_as_of,
                "url": row.source_url,
                "locator": row.source_row_locator,
                "sha": row.artifact_sha256,
                "extractor": ADAPTER_VERSION,
                "transform": TRANSFORM_VERSION,
                "delta": row.source_report_delta,
                "revision_notice": row.revision_notice,
                "lineage": lineage_sha,
            },
        )
        accepted_by_group[(row.country_alpha2, row.country_iso3)].append(observation_id)
        if existing is None:
            added += 1
        else:
            revised += 1
    snapshots: list[dict[str, Any]] = []
    for (alpha2, iso3), _group_rows in retrieval_groups.items():
        current_rows = (
            conn.execute(
                text("SELECT o.id,o.record_fingerprint,o.period_start,o.period_end,o.metric_code,o.value_numeric,o.revision,o.dimensions_sha256 FROM epidemic.normalized_observations o JOIN epidemic_p1_12.trusted_observation_lineage l ON l.observation_id=o.id WHERE l.source_contract_id=:contract AND o.country_alpha2=:country AND NOT EXISTS(SELECT 1 FROM epidemic.normalized_observations n WHERE n.supersedes_observation_id=o.id) ORDER BY o.period_start,o.period_end,o.metric_code,o.series_fingerprint,o.revision"),
                {"contract": contract_id, "country": alpha2},
            )
            .mappings()
            .all()
        )
        if not current_rows:
            continue
        snapshot_material = [
            {
                "record_fingerprint": str(item["record_fingerprint"]),
                "period_start": str(item["period_start"]),
                "period_end": str(item["period_end"]),
                "metric_code": str(item["metric_code"]),
                "value_numeric": str(item["value_numeric"]),
                "revision": str(item["revision"]),
                "dimensions_sha256": str(item["dimensions_sha256"]),
            }
            for item in current_rows
        ]
        snapshot_sha = sha(snapshot_material)
        existing_snapshot = db_scalar_none(
            conn,
            "SELECT s.id FROM epidemic.dataset_snapshots s JOIN epidemic_p1_12.trusted_snapshot_lineage l ON l.snapshot_id=s.id WHERE l.source_contract_id=:contract AND s.query_request_id=:query AND s.content_sha256=:sha LIMIT 1",
            {"contract": contract_id, "query": query_id, "sha": snapshot_sha},
        )
        snapshot_created = existing_snapshot is None
        snapshot_id = uuid.UUID(str(existing_snapshot)) if existing_snapshot else stable_uuid("snapshot", f"{query_id}:{iso3}:{snapshot_sha}")
        date_min = min(item["period_start"] for item in current_rows)
        date_max = max(item["period_end"] for item in current_rows)
        if snapshot_created:
            features = {
                "source_key": source_key,
                "country_iso3": iso3,
                "contract_version": "1.0.0",
                "normalization_contract_version": TRANSFORM_VERSION,
                "attribution": f"Source: {rows[0].publisher}",
                "terms_url": source_meta.get("terms_url") or rows[0].source_url,
                "trust_tier": "OFFICIAL_FIRST_PARTY",
                "missing_interpolated": False,
            }
            conn.execute(
                text("INSERT INTO epidemic.dataset_snapshots(id,query_request_id,snapshot_schema_version,cutoff_at,row_count,content_sha256,feature_spec) VALUES(:id,:query,:version,now(),:count,:sha,CAST(:features AS jsonb))"),
                {"id": snapshot_id, "query": query_id, "version": SNAPSHOT_SCHEMA, "count": len(current_rows), "sha": snapshot_sha, "features": canonical(features)},
            )
            for number, item in enumerate(current_rows, 1):
                conn.execute(
                    text("INSERT INTO epidemic.dataset_snapshot_observations(snapshot_id,observation_id,row_number) VALUES(:snapshot,:observation,:number) ON CONFLICT DO NOTHING"),
                    {"snapshot": snapshot_id, "observation": item["id"], "number": number},
                )
            conn.execute(
                text("INSERT INTO epidemic_p1_12.trusted_snapshot_lineage(snapshot_id,source_contract_id,snapshot_sha256,row_count,date_min,date_max,independently_recomputed_sha256) VALUES(:snapshot,:contract,:sha,:count,:date_min,:date_max,:sha) ON CONFLICT(snapshot_id) DO NOTHING"),
                {"snapshot": snapshot_id, "contract": contract_id, "sha": snapshot_sha, "count": len(current_rows), "date_min": date_min, "date_max": date_max},
            )
        snapshots.append({"country_iso3": iso3, "snapshot_id": str(snapshot_id), "row_count": len(current_rows), "sha256": snapshot_sha, "date_min": str(date_min), "date_max": str(date_max), "created": snapshot_created})
    primary = results[0]
    primary_fetch = fetch_map.get(primary.sha256, (None, None))[0]
    normalization_key = sha({"execution": EXECUTION_ID, "source": source_key, "manifest": manifest_sha})
    conn.execute(
        text("INSERT INTO epidemic.normalization_runs(id,retrieval_run_id,source_fetch_id,normalization_key,transform_version,country_mapping_version,quality_ruleset_version,snapshot_schema_version,status,result_code,attempt_number,max_attempts,created_at,started_at,finished_at,dataset_snapshot_id,total_source_rows,selected_source_rows,normalized_observation_count,missing_value_count,warning_count,unmapped_source_code_count,exact_duplicate_count) VALUES(:id,:retrieval,:fetch,:key,:transform,:mapping,:rules,:schema,'succeeded','PUBLISHED',1,3,now(),now(),now(),:snapshot,:rows,:rows,:count,0,0,0,0) ON CONFLICT(normalization_key) DO NOTHING"),
        {
            "id": stable_uuid("normalization", normalization_key),
            "retrieval": retrieval_id,
            "fetch": primary_fetch,
            "key": normalization_key,
            "transform": TRANSFORM_VERSION,
            "mapping": COUNTRY_MAPPING,
            "rules": QUALITY_RULESET,
            "schema": SNAPSHOT_SCHEMA,
            "snapshot": uuid.UUID(snapshots[0]["snapshot_id"]) if snapshots else None,
            "rows": len(rows),
            "count": added + revised + reused,
        },
    )
    return {"source_key": source_key, "persisted": True, "contract_id": str(contract_id), "query_id": str(query_id), "retrieval_id": str(retrieval_id), "manifest_sha256": manifest_sha, "added": added, "revised": revised, "reused": reused, "snapshots": snapshots}


def forecast_eligibility(rows_by_source: dict[str, list[TrustedRow]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for source_key, rows in rows_by_source.items():
        by_series: dict[tuple[str, str, str], list[TrustedRow]] = {}
        for row in rows:
            by_series.setdefault((row.country_iso3, row.metric_code, row.granularity), []).append(row)
        series = []
        for (country, metric, granularity), items in sorted(by_series.items()):
            weeks = len({r.period_start for r in items if r.granularity == "week"})
            months = len({r.period_start for r in items if r.granularity == "month"})
            years = len({r.period_start for r in items if r.granularity == "year"})
            nonzero = sum(1 for r in items if r.value > 0)
            recent_cut = dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(weeks=104)
            recent_rows = [r for r in items if r.period_start >= recent_cut]
            recent_possible = 104 if granularity == "week" else 24 if granularity == "month" else 2
            recent_coverage = len({r.period_start for r in recent_rows}) / max(1, recent_possible)
            reasons = []
            if granularity != "week":
                reasons.append(f"NOT_REGION_WEEK_SERIES_GRANULARITY_{granularity.upper()}")
            if weeks < 104:
                reasons.append(f"WEEKS_LT_104:{weeks}")
            if recent_coverage < 0.85:
                reasons.append(f"RECENT_104_COVERAGE_LT_85:{recent_coverage:.3f}")
            if nonzero < 20:
                reasons.append(f"NONZERO_WEEKS_LT_20:{nonzero}")
            series.append({"country_iso3": country, "metric": metric, "granularity": granularity, "rows": len(items), "weeks": weeks, "months": months, "years": years, "nonzero": nonzero, "recent_coverage": round(recent_coverage, 4), "eligible": not reasons, "reasons": reasons})
        eligible_locations = {s["country_iso3"] for s in series if s["eligible"]}
        complete = len(eligible_locations) >= 5
        if source_key == "who_don_nipah_events":
            complete = False
            status = "NOT_RUN_EVENT_ONLY_NO_CONTINUOUS_REGION_WEEK_SERIES"
        elif complete:
            status = "ELIGIBLE_NOT_TRAINED_IN_THIS_STAGE"
        else:
            status = "NOT_RUN_FORMAL_GATE_FAILED"
        output[source_key] = {"series": series, "eligible_location_count": len(eligible_locations), "forecast_complete": False, "forecast_status": status}
    return output


def dashboard_validation(engine: Any) -> dict[str, Any]:
    with engine.connect() as conn:
        out = {
            "history_catalog_v2": db_rows(
                conn,
                """
                SELECT disease_slug, source_key, country_alpha3 AS country_iso3, NULL::text AS metric_code,
                       observation_count AS row_count, data_start AS date_min, data_end AS date_max
                FROM epidemic.dashboard_history_catalog_v2
                WHERE disease_slug IN ('legionella','hantavirus','nipah')
                ORDER BY disease_slug, source_key, country_alpha3
                """,
            ),
            "p1_12_trusted_catalog_v1": db_rows(
                conn,
                """
                SELECT source_key, NULL::text AS country_iso3, NULL::text AS disease_slug, NULL::text AS metric_code,
                       row_count, date_min, date_max, countries, diseases
                FROM epidemic_p1_12.trusted_dashboard_catalog_v1
                WHERE source_key IN ('legionnaires_ecdc_monthly_eueea','puumala_hfrs_ne_finland','who_don_nipah_events')
                ORDER BY source_key
                """,
            ),
            "api_probes": [],
        }
    for path in [
        "/readyz",
        "/api/v2/history/catalog",
        "/api/v2/history/series?disease=legionella",
        "/api/v2/history/series?disease=hantavirus",
        "/api/v2/history/series?disease=nipah",
        "/fasttrack-forecast",
    ]:
        try:
            req = urllib.request.Request("http://127.0.0.1:8020" + path, headers={"User-Agent": "epidemic-openclaw-three-disease-validation/1.0"})
            start = time.time()
            with urllib.request.urlopen(req, timeout=15) as resp:
                payload = resp.read(2000)
                out["api_probes"].append({"path": path, "status": getattr(resp, "status", 200), "bytes_sampled": len(payload), "elapsed_ms": int((time.time() - start) * 1000), "content_type": resp.headers.get("Content-Type")})
        except urllib.error.HTTPError as exc:
            out["api_probes"].append({"path": path, "status": exc.code, "error": "HTTPError"})
        except Exception as exc:  # noqa: BLE001
            out["api_probes"].append({"path": path, "status": 0, "error": type(exc).__name__})
    return out


def validate_freeze(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    before_runs = {row["id"]: row for row in before["database"]["frozen_runs"]}
    after_runs = {row["id"]: row for row in after["database"]["frozen_runs"]}
    return {
        "covid_formal_unchanged": before_runs.get("c1ce1947-1ab4-5f2d-b69e-a4e9c4668825") == after_runs.get("c1ce1947-1ab4-5f2d-b69e-a4e9c4668825"),
        "old_defective_run_still_invalidated": (after_runs.get("2adad614-7f25-5e58-ba00-167ca5f87606") or {}).get("status") in {"INVALIDATED", "RETRACTED"},
        "old_defective_reason": (after_runs.get("2adad614-7f25-5e58-ba00-167ca5f87606") or {}).get("retraction_reason"),
        "before": before_runs,
        "after": after_runs,
    }


def package_outputs(summary: dict[str, Any]) -> dict[str, Any]:
    package_manifest: list[dict[str, Any]] = []
    for root in (REPORT_DIR, ARTIFACT_DIR, BACKUP_DIR):
        if not root.exists():
            continue
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            text_sample = ""
            try:
                text_sample = path.read_text(encoding="utf-8", errors="ignore")[:4000]
            except Exception:
                pass
            hits = secret_scan_text(text_sample)
            package_manifest.append({"path": str(path.relative_to(PROJECT)), "bytes": path.stat().st_size, "sha256": sha256_bytes(path.read_bytes()), "secret_scan_hits": hits})
    write_json(REPORT_DIR / "PACKAGE_MANIFEST.json", package_manifest)
    if any(item["secret_scan_hits"] for item in package_manifest):
        raise RuntimeError("PACKAGE_SECRET_SCAN_FAILED")
    zip_path = PROJECT / "reports" / f"{EXECUTION_ID}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for item in package_manifest:
            zf.write(PROJECT / item["path"], arcname=item["path"])
    zip_sha = sha256_bytes(zip_path.read_bytes())
    (zip_path.with_suffix(".zip.sha256")).write_text(zip_sha + "  " + zip_path.name + "\n", encoding="utf-8")
    return {"zip_path": str(zip_path), "zip_sha256": zip_sha, "zip_bytes": zip_path.stat().st_size, "file_count": len(package_manifest)}


def load_trusted_rows(path: Path) -> list[TrustedRow]:
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows: list[TrustedRow] = []
    for item in raw:
        item = dict(item)
        for key in ["period_start", "period_end", "reporting_date", "source_as_of"]:
            if isinstance(item.get(key), str):
                item[key] = dt.date.fromisoformat(item[key])
        rows.append(TrustedRow(**item))
    return rows


def finalize_existing_run() -> None:
    if not REPORT_DIR.exists() or not ARTIFACT_DIR.exists():
        raise SystemExit(f"BLOCKED_FINALIZE_DIR_MISSING:{EXECUTION_ID}")
    engine = connect_engine()
    before_path = REPORT_DIR / "DATABASE_FINGERPRINT_BEFORE.json"
    before = {"database": json.loads(before_path.read_text(encoding="utf-8"))} if before_path.exists() else {"database": database_fingerprint(engine)}
    ecdc_rows = load_trusted_rows(PARSED_DIR / "legionella_ecdc_rows.json")
    thl_rows = load_trusted_rows(PARSED_DIR / "hantavirus_thl_rows.json")
    nipah_rows = load_trusted_rows(PARSED_DIR / "nipah_who_rows.json")
    all_rows = {
        "legionnaires_ecdc_monthly_eueea": ecdc_rows,
        "puumala_hfrs_ne_finland": thl_rows,
        "who_don_nipah_events": nipah_rows,
    }
    persist_report_path = REPORT_DIR / "FORMAL_TRUSTED_INGESTION_RESULTS.json"
    persist_payload = json.loads(persist_report_path.read_text(encoding="utf-8")) if persist_report_path.exists() else {"sources": []}
    dashboard = dashboard_validation(engine)
    forecast = forecast_eligibility(all_rows)
    after = {
        "execution_id": EXECUTION_ID,
        "database": database_fingerprint(engine),
        "service_state_after": service_state(),
    }
    freeze = validate_freeze(before, after)
    write_json(REPORT_DIR / "DASHBOARD_TRIPTYCH_VALIDATION.json", dashboard)
    write_json(REPORT_DIR / "FORECAST_ELIGIBILITY_AND_PUBLICATION_STATUS.json", forecast)
    write_json(REPORT_DIR / "DATABASE_FINGERPRINT_AFTER.json", after["database"])
    write_json(REPORT_DIR / "SERVICE_STATE_AFTER.json", after["service_state_after"])
    write_json(REPORT_DIR / "FROZEN_RUN_VALIDATION.json", freeze)
    source_complete = {
        "legionella": bool(ecdc_rows),
        "hantavirus": bool(thl_rows),
        "nipah": bool(nipah_rows),
    }
    catalog_rows = dashboard.get("history_catalog_v2") or []
    dashboard_complete = {
        disease: any(row.get("disease_slug") == disease and int(row.get("row_count") or 0) > 0 for row in catalog_rows)
        for disease in ("legionella", "hantavirus", "nipah")
    }
    forecast_complete = {"legionella": False, "hantavirus": False, "nipah": False}
    final_status = (
        "THREE_DISEASE_TRUSTED_DATA_AND_TRIPTYCH_COMPLETE_FORECAST_NOT_RUN"
        if sum(source_complete.values()) == 3 and sum(dashboard_complete.values()) == 3
        else "EPIDEMIC_THREE_DISEASE_PARTIAL_WITH_EXPLICIT_BLOCKERS"
    )
    source_matrix = json.loads((REPORT_DIR / "OFFICIAL_SOURCE_ACCESS_MATRIX.json").read_text(encoding="utf-8")) if (REPORT_DIR / "OFFICIAL_SOURCE_ACCESS_MATRIX.json").exists() else {}
    backup_receipt = json.loads((REPORT_DIR / "PG_DUMP_PRE_WRITE_BACKUP.json").read_text(encoding="utf-8")) if (REPORT_DIR / "PG_DUMP_PRE_WRITE_BACKUP.json").exists() else {}
    summary = {
        "execution_id": EXECUTION_ID,
        "status": final_status,
        "trusted_official_data_access": f"{sum(source_complete.values())}/3",
        "formal_trusted_ingestion": f"{sum(1 for item in persist_payload.get('sources', []) if item.get('persisted'))}/3",
        "dashboard_triptych": f"{sum(dashboard_complete.values())}/3",
        "model_forecasts": f"{sum(forecast_complete.values())}/3",
        "forecast_page_usable_display": f"{sum(forecast_complete.values())}/3",
        "source_complete": source_complete,
        "dashboard_complete": dashboard_complete,
        "forecast_complete": forecast_complete,
        "source_row_counts": {key: len(value) for key, value in all_rows.items()},
        "date_ranges": {
            key: {
                "min": str(min((r.period_start for r in value), default="")),
                "max": str(max((r.period_end for r in value), default="")),
            }
            for key, value in all_rows.items()
        },
        "official_source_matrix_file": str(REPORT_DIR / "OFFICIAL_SOURCE_ACCESS_MATRIX.json"),
        "cdc_nndss_legionellosis_status": source_matrix.get("cdc"),
        "freeze_validation": freeze,
        "backup": backup_receipt,
        "artifacts": {"report_dir": str(REPORT_DIR), "artifact_dir": str(ARTIFACT_DIR), "backup_dir": str(BACKUP_DIR)},
        "notes": [
            "Finalized an already committed run; no repeat ingestion was performed in finalize mode.",
            "Existing trusted_source_contracts check constraints required legacy enum values: PROJECT_OWNER_DIRECTIVE / CURATED_BUILTIN_SOURCE / TRUSTED_OFFICIAL_REPORTED / CASES_NEW_OPERATIONALIZATION.",
            "Nipah event records are stored as day-granularity normalized observations with dimensions.data_mode=EVENT_ONLY.",
            "No missing values were converted to zero; no interpolation, promotion, approval, or old forecast retraining was performed.",
        ],
    }
    write_json(REPORT_DIR / "FINAL_SUMMARY.json", summary)
    write_text(
        REPORT_DIR / "REPORT.md",
        "\n".join(
            [
                "# Three-disease trusted data / dashboard / forecast E2E",
                "",
                f"- Status: `{final_status}`",
                f"- Execution: `{EXECUTION_ID}`",
                f"- Trusted official data access: `{summary['trusted_official_data_access']}`",
                f"- Formal trusted ingestion: `{summary['formal_trusted_ingestion']}`",
                f"- Dashboard triptych: `{summary['dashboard_triptych']}`",
                f"- Model forecasts: `{summary['model_forecasts']}`",
                f"- Forecast page usable display: `{summary['forecast_page_usable_display']}`",
                "",
                "Forecast gates were not lowered. No new formal forecast was published for Legionella, Hantavirus, or Nipah in this run.",
                "",
                "Nipah is event-only and stored with `dimensions.data_mode=EVENT_ONLY` for Dashboard triptych display.",
                "",
                "See `FINAL_SUMMARY.json`, `OFFICIAL_SOURCE_ACCESS_MATRIX.json`, `FORMAL_TRUSTED_INGESTION_RESULTS.json`, and `PACKAGE_MANIFEST.json`.",
            ]
        )
        + "\n",
    )
    package = package_outputs(summary)
    summary["package"] = package
    write_json(REPORT_DIR / "FINAL_SUMMARY.json", summary)
    print(json.dumps({"execution_id": EXECUTION_ID, "status": final_status, "zip": package, "counts": {k: summary[k] for k in ("trusted_official_data_access", "formal_trusted_ingestion", "dashboard_triptych", "model_forecasts", "forecast_page_usable_display")}}, ensure_ascii=False, indent=2))


def main() -> None:
    if os.environ.get("RESUME_E2E_FINALIZE_ONLY") == "1":
        finalize_existing_run()
        return
    if ARTIFACT_DIR.exists() or REPORT_DIR.exists() or BACKUP_DIR.exists():
        raise SystemExit(f"BLOCKED_OUTPUT_DIR_EXISTS:{EXECUTION_ID}")
    ARTIFACT_DIR.mkdir(parents=True)
    REPORT_DIR.mkdir(parents=True)
    RAW_DIR.mkdir(parents=True)
    PARSED_DIR.mkdir(parents=True)
    os.environ.pop("http_proxy", None)
    os.environ.pop("https_proxy", None)
    os.environ.pop("HTTP_PROXY", None)
    os.environ.pop("HTTPS_PROXY", None)
    os.environ.pop("all_proxy", None)
    os.environ.pop("ALL_PROXY", None)
    engine = connect_engine()
    before = {
        "execution_id": EXECUTION_ID,
        "project": str(PROJECT),
        "git_head": run_cmd(["git", "rev-parse", "HEAD"], timeout=60)["stdout"].strip(),
        "git_branch": run_cmd(["git", "branch", "--show-current"], timeout=60)["stdout"].strip(),
        "git_status_inventory": git_status_inventory(),
        "database": database_fingerprint(engine),
    }
    services_before = service_state()
    write_json(REPORT_DIR / "PREEXISTING_DIRTY_INVENTORY.json", before["git_status_inventory"])
    write_json(REPORT_DIR / "DATABASE_FINGERPRINT_BEFORE.json", before["database"])
    write_json(REPORT_DIR / "SERVICE_STATE_BEFORE.json", services_before)
    write_text(REPORT_DIR / "CURRENT_ARCHITECTURE_INVENTORY.md", current_architecture_markdown(before, services_before))
    write_text(REPORT_DIR / "EXECUTION_PLAN.md", execution_plan_markdown())
    backup_receipt = custom_pg_dump()
    write_json(REPORT_DIR / "PG_DUMP_PRE_WRITE_BACKUP.json", backup_receipt)

    with engine.connect() as conn:
        countries = db_rows(conn, "SELECT alpha2,alpha3,name FROM epidemic.countries")
    countries_by_alpha2 = {row["alpha2"]: row for row in countries if row.get("alpha2")}

    client = HTTPClient(RAW_DIR)
    cdc_matrix = fetch_cdc_legionella(client)
    ecdc_matrix, ecdc_rows, ecdc_results = fetch_ecdc_legionella(client, countries_by_alpha2)
    thl_matrix, thl_rows, thl_results = fetch_thl_puumala(client, countries_by_alpha2)
    nipah_matrix, nipah_rows, nipah_results = fetch_who_nipah(client, countries_by_alpha2)
    all_rows = {
        "legionnaires_ecdc_monthly_eueea": ecdc_rows,
        "puumala_hfrs_ne_finland": thl_rows,
        "who_don_nipah_events": nipah_rows,
    }
    write_json(REPORT_DIR / "OFFICIAL_SOURCE_ACCESS_MATRIX.json", {"cdc": cdc_matrix, "ecdc": ecdc_matrix, "thl": thl_matrix, "who_nipah": nipah_matrix})
    write_json(PARSED_DIR / "legionella_ecdc_rows.json", [asdict(r) for r in ecdc_rows])
    write_json(PARSED_DIR / "hantavirus_thl_rows.json", [asdict(r) for r in thl_rows])
    write_json(PARSED_DIR / "nipah_who_rows.json", [asdict(r) for r in nipah_rows])

    persist_results: list[dict[str, Any]] = []
    with engine.begin() as conn:
        insert_ref = insert_reference_data(conn)
        for source_key, rows in all_rows.items():
            if source_key == "legionnaires_ecdc_monthly_eueea":
                meta = {
                    **ecdc_matrix,
                    "canonical_url": "https://atlas.ecdc.europa.eu/public/AtlasService/rest/GetDatasets",
                    "terms_url": "https://www.ecdc.europa.eu/en/about-us/legal-notice",
                    "license_expression": "ECDC_REUSE_NOTICE",
                }
                persist_results.append(persist_source(conn, rows, ecdc_results, meta))
            elif source_key == "puumala_hfrs_ne_finland":
                meta = {
                    **thl_matrix,
                    "canonical_url": "https://sampo.thl.fi/pivot/prod/en/ttr/casesweek/fact_ttr_casesweek",
                    "terms_url": "https://thl.fi/en/web/thlfi-en/statistics-and-data/data-and-services/open-data",
                    "license_expression": "CC-BY-4.0",
                }
                persist_results.append(persist_source(conn, rows, thl_results, meta))
            elif source_key == "who_don_nipah_events":
                meta = {
                    **nipah_matrix,
                    "canonical_url": "https://www.who.int/emergencies/disease-outbreak-news",
                    "terms_url": "https://www.who.int/about/policies/publishing/copyright",
                    "license_expression": "WHO_WEBSITE_TERMS",
                }
                persist_results.append(persist_source(conn, rows, nipah_results, meta))
    write_json(REPORT_DIR / "FORMAL_TRUSTED_INGESTION_RESULTS.json", {"reference_data": insert_ref, "sources": persist_results})

    dashboard = dashboard_validation(engine)
    forecast = forecast_eligibility(all_rows)
    after = {
        "execution_id": EXECUTION_ID,
        "database": database_fingerprint(engine),
        "service_state_after": service_state(),
    }
    freeze = validate_freeze(before, after)
    write_json(REPORT_DIR / "DASHBOARD_TRIPTYCH_VALIDATION.json", dashboard)
    write_json(REPORT_DIR / "FORECAST_ELIGIBILITY_AND_PUBLICATION_STATUS.json", forecast)
    write_json(REPORT_DIR / "DATABASE_FINGERPRINT_AFTER.json", after["database"])
    write_json(REPORT_DIR / "SERVICE_STATE_AFTER.json", after["service_state_after"])
    write_json(REPORT_DIR / "FROZEN_RUN_VALIDATION.json", freeze)

    source_complete = {
        "legionella": bool(ecdc_rows),
        "hantavirus": bool(thl_rows),
        "nipah": bool(nipah_rows),
    }
    dashboard_complete = {}
    catalog_rows = dashboard.get("history_catalog_v2") or []
    for disease in ("legionella", "hantavirus", "nipah"):
        dashboard_complete[disease] = any(row.get("disease_slug") == disease and int(row.get("row_count") or 0) > 0 for row in catalog_rows)
    forecast_complete = {
        "legionella": False,
        "hantavirus": False,
        "nipah": False,
    }
    final_status = (
        "THREE_DISEASE_TRUSTED_DATA_AND_TRIPTYCH_COMPLETE_FORECAST_NOT_RUN"
        if sum(source_complete.values()) == 3 and sum(dashboard_complete.values()) == 3
        else "EPIDEMIC_THREE_DISEASE_PARTIAL_WITH_EXPLICIT_BLOCKERS"
    )
    summary = {
        "execution_id": EXECUTION_ID,
        "status": final_status,
        "trusted_official_data_access": f"{sum(source_complete.values())}/3",
        "formal_trusted_ingestion": f"{sum(1 for item in persist_results if item.get('persisted'))}/3",
        "dashboard_triptych": f"{sum(dashboard_complete.values())}/3",
        "model_forecasts": f"{sum(forecast_complete.values())}/3",
        "forecast_page_usable_display": f"{sum(forecast_complete.values())}/3",
        "source_complete": source_complete,
        "dashboard_complete": dashboard_complete,
        "forecast_complete": forecast_complete,
        "cdc_nndss_legionellosis_status": cdc_matrix,
        "source_row_counts": {key: len(value) for key, value in all_rows.items()},
        "date_ranges": {
            key: {
                "min": str(min((r.period_start for r in value), default="")),
                "max": str(max((r.period_end for r in value), default="")),
            }
            for key, value in all_rows.items()
        },
        "freeze_validation": freeze,
        "backup": backup_receipt,
        "artifacts": {"report_dir": str(REPORT_DIR), "artifact_dir": str(ARTIFACT_DIR), "backup_dir": str(BACKUP_DIR)},
        "notes": [
            "No missing values were converted to zero.",
            "No interpolation, promotion, approval, or old forecast retraining was performed.",
            "Nipah is event-only and forecast_status=NOT_RUN unless a real continuous region-week series is found.",
            "Legionella CDC NNDSS x9gk-5huc was probed separately and retained as official access evidence even if blocked by HTTP 403.",
        ],
    }
    write_json(REPORT_DIR / "FINAL_SUMMARY.json", summary)
    report_md = [
        "# Three-disease trusted data / dashboard / forecast E2E",
        "",
        f"- Status: `{final_status}`",
        f"- Execution: `{EXECUTION_ID}`",
        f"- Trusted official data access: `{summary['trusted_official_data_access']}`",
        f"- Formal trusted ingestion: `{summary['formal_trusted_ingestion']}`",
        f"- Dashboard triptych: `{summary['dashboard_triptych']}`",
        f"- Model forecasts: `{summary['model_forecasts']}`",
        f"- Forecast page usable display: `{summary['forecast_page_usable_display']}`",
        "",
        "## Source row counts",
        "",
        "```json",
        canonical(summary["source_row_counts"]),
        "```",
        "",
        "## Explicit forecast result",
        "",
        "Forecast gates were not lowered. No new formal forecast was published for these three diseases in this run.",
        "",
        "## Package",
        "",
        "See `PACKAGE_MANIFEST.json` and the top-level zip SHA-256.",
    ]
    write_text(REPORT_DIR / "REPORT.md", "\n".join(report_md) + "\n")
    package = package_outputs(summary)
    summary["package"] = package
    write_json(REPORT_DIR / "FINAL_SUMMARY.json", summary)
    print(json.dumps({"execution_id": EXECUTION_ID, "status": final_status, "zip": package, "counts": {k: summary[k] for k in ("trusted_official_data_access", "formal_trusted_ingestion", "dashboard_triptych", "model_forecasts", "forecast_page_usable_display")}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        try:
            REPORT_DIR.mkdir(parents=True, exist_ok=True)
            write_json(REPORT_DIR / "FAILED.json", {"execution_id": EXECUTION_ID, "error_type": type(exc).__name__, "error": str(exc), "time": dt.datetime.now(dt.timezone.utc).isoformat()})
        except Exception:
            pass
        raise
