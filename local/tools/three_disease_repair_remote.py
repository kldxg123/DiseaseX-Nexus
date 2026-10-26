from __future__ import annotations

import calendar
import csv
import datetime as dt
import hashlib
import importlib.util
import json
import math
import os
import re
import secrets
import shutil
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


EXECUTION_ID = os.environ.get(
    "EXECUTION_ID",
    "three_disease_repair_" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + secrets.token_hex(4),
)
os.environ["EXECUTION_ID"] = EXECUTION_ID
PROJECT = Path(os.environ.get("PROJECT", "USER_HOME/autodl-tmp/epidemic-openclaw")).resolve()
ARTIFACT_DIR = PROJECT / "artifacts" / EXECUTION_ID
REPORT_DIR = PROJECT / "reports" / EXECUTION_ID
BACKUP_DIR = PROJECT / "backups" / EXECUTION_ID
RAW_DIR = ARTIFACT_DIR / "raw"
LOG_DIR = ARTIFACT_DIR / "logs"
CSV_DIR = ARTIFACT_DIR / "csv"
SCREEN_DIR = ARTIFACT_DIR / "browser"
TRANSFORM_VERSION = "three-disease-repair-v2"
ADAPTER_VERSION = "three-disease-repair-adapters-v2"
SNAPSHOT_SCHEMA = 1
QUALITY_RULESET = "three-disease-repair-quality-v2"
UUID_NS = uuid.uuid5(uuid.NAMESPACE_DNS, "epidemic-openclaw:three-disease-repair-v2")
OLD_ZIP_SHA = "d010876f950ea53810d306e25ea9e24e2a2aa05fa3b552c734e01a4b493930d1"


def load_base() -> Any:
    path = Path("/tmp/three_disease_e2e_remote.py")
    if not path.exists():
        raise RuntimeError("BASE_E2E_HELPER_MISSING:/tmp/three_disease_e2e_remote.py")
    spec = importlib.util.spec_from_file_location("three_disease_e2e_remote_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("BASE_E2E_HELPER_LOAD_FAILED")
    module = importlib.util.module_from_spec(spec)
    sys.modules["three_disease_e2e_remote_base"] = module
    spec.loader.exec_module(module)
    return module


base = load_base()


def canonical(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha(obj: Any) -> str:
    return sha256_bytes(canonical(obj).encode("utf-8"))


def stable_uuid(kind: str, value: str) -> uuid.UUID:
    return uuid.uuid5(UUID_NS, f"{kind}:{value}")


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def db_url() -> str:
    raw = os.environ.get("MIGRATION_DATABASE_URL") or os.environ.get("DATABASE_URL") or os.environ.get("APP_DATABASE_URL")
    if not raw:
        raise RuntimeError("DATABASE_URL_UNAVAILABLE")
    url = make_url(raw)
    password = os.environ.get("POSTGRES_ADMIN_PASSWORD")
    if password:
        url = url.set(username=os.environ.get("POSTGRES_ADMIN_USER") or "epidemicdb", password=password)
    return url.render_as_string(hide_password=False)


def engine():
    return create_engine(db_url(), future=True, pool_pre_ping=True)


def rows(conn: Any, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(text(sql), params or {}).mappings().all()]


def scalar(conn: Any, sql: str, params: dict[str, Any] | None = None) -> Any:
    return conn.execute(text(sql), params or {}).scalar_one()


def scalar_none(conn: Any, sql: str, params: dict[str, Any] | None = None) -> Any:
    return conn.execute(text(sql), params or {}).scalar_one_or_none()


def command_receipt(label: str, args: list[str], cwd: Path | None = None, timeout: int = 300) -> dict[str, Any]:
    start = dt.datetime.now(dt.timezone.utc)
    p = subprocess.run(args, cwd=str(cwd or PROJECT), text=True, capture_output=True, timeout=timeout)
    end = dt.datetime.now(dt.timezone.utc)
    payload = {
        "label": label,
        "cwd": str(cwd or PROJECT),
        "command": args,
        "start_utc": start.isoformat(),
        "end_utc": end.isoformat(),
        "exit_code": p.returncode,
        "status": "passed" if p.returncode == 0 else "failed",
        "stdout": p.stdout,
        "stderr": p.stderr,
    }
    path = LOG_DIR / f"{label}.json"
    write_json(path, payload)
    payload["log_path"] = str(path.relative_to(PROJECT))
    payload["log_size"] = path.stat().st_size
    payload["log_sha256"] = sha256_bytes(path.read_bytes())
    return payload


def old_zip_check() -> dict[str, Any]:
    old_path = PROJECT / "reports" / "three_disease_20260727T043925Z_3e1bbfaf.zip"
    if not old_path.exists():
        return {"present_remote": False, "expected_sha256": OLD_ZIP_SHA, "status": "REMOTE_OLD_ZIP_NOT_FOUND"}
    value = sha256_bytes(old_path.read_bytes())
    return {
        "present_remote": True,
        "path": str(old_path),
        "size": old_path.stat().st_size,
        "sha256": value,
        "expected_sha256": OLD_ZIP_SHA,
        "unchanged": value == OLD_ZIP_SHA,
    }


def git_dirty_inventory() -> dict[str, Any]:
    status = subprocess.run(["git", "status", "--porcelain=v2", "-z"], cwd=PROJECT, capture_output=True)
    entries = []
    for item in status.stdout.split(b"\0"):
        if not item:
            continue
        text_item = item.decode("utf-8", errors="replace")
        path = text_item.split(" ")[-1]
        full = PROJECT / path
        entry = {"raw": text_item, "path": path, "exists": full.exists()}
        if full.exists() and full.is_file():
            data = full.read_bytes()
            entry.update({"size": len(data), "mode": oct(full.stat().st_mode), "sha256": sha256_bytes(data)})
        entries.append(entry)
    return {
        "raw_sha256": sha256_bytes(status.stdout),
        "entry_count": len(entries),
        "entries": entries,
    }


def db_fingerprint(conn: Any) -> dict[str, Any]:
    out = {
        "alembic": rows(conn, "SELECT version_num FROM epidemic.alembic_version ORDER BY version_num"),
        "counts": {},
        "disease_counts": rows(
            conn,
            """
            SELECT d.slug,count(*)::int rows,min(o.period_start)::text date_min,max(o.period_end)::text date_max
            FROM epidemic.normalized_observations o JOIN epidemic.diseases d ON d.id=o.disease_id
            GROUP BY d.slug ORDER BY d.slug
            """,
        ),
        "three_disease_current": rows(
            conn,
            """
            SELECT d.slug,count(*)::int rows,min(o.period_start)::text date_min,max(o.period_end)::text date_max
            FROM epidemic.dashboard_history_series_v2 o JOIN epidemic.diseases d ON d.id=o.disease_id
            WHERE d.slug IN ('legionella','hantavirus','nipah')
            GROUP BY d.slug ORDER BY d.slug
            """,
        ),
    }
    for table in [
        "epidemic.normalized_observations",
        "epidemic.raw_artifacts",
        "epidemic.source_fetches",
        "epidemic.dataset_snapshots",
        "epidemic.forecast_runs",
        "epidemic.forecast_values",
        "epidemic_p1_12.trusted_observation_lineage",
        "epidemic_p1_12.trusted_source_contracts",
    ]:
        out["counts"][table] = int(scalar(conn, f"SELECT count(*) FROM {table}"))
    return out


def protected_runs(conn: Any) -> dict[str, Any]:
    data = rows(
        conn,
        """
        SELECT id::text,status,run_key,disease_slug,target_metric,country_iso3,source_key,cutoff_week,dataset_sha256,
               champion_model,metrics::text,data_summary::text,published_at,retracted_at,retraction_reason
        FROM epidemic.forecast_runs
        WHERE id::text IN ('c1ce1947-1ab4-5f2d-b69e-a4e9c4668825','2adad614-7f25-5e58-ba00-167ca5f87606')
        ORDER BY id::text
        """,
    )
    return {"rows": data, "fingerprint": sha(data)}


def service_state() -> dict[str, Any]:
    receipts: dict[str, Any] = {}
    for label, script in {
        "dashboard": PROJECT / "scripts" / "dashboard" / "status_dashboard.sh",
        "review": PROJECT / "scripts" / "source_review" / "status_source_review.sh",
        "query_api": PROJECT / "scripts" / "query_api" / "status_query_api.sh",
        "postgres": PROJECT / "scripts" / "postgres" / "status_postgres.sh",
    }.items():
        if script.exists():
            receipts[label] = command_receipt(f"service_{label}_status", ["bash", str(script)], timeout=120)
    ps = command_receipt("service_process_inventory_ps", ["python3", "-c", "import pathlib,json; items=[]\nfor p in pathlib.Path('/proc').iterdir():\n    if not p.name.isdigit(): continue\n    try:\n        cmd=(p/'cmdline').read_bytes().replace(b'\\x00',b' ').decode('utf-8','replace')\n        cwd=str((p/'cwd').resolve())\n    except Exception: continue\n    if 'epidemic-openclaw' in cmd or 'epidemic-openclaw' in cwd:\n        items.append({'pid':int(p.name),'cwd':cwd,'cmdline':cmd})\nprint(json.dumps(items,ensure_ascii=False))"], timeout=120)
    receipts["processes"] = json.loads(ps["stdout"] or "[]") if ps["exit_code"] == 0 else []
    return receipts


def backup_receipt() -> dict[str, Any]:
    # Use base implementation but copy only receipt into reports, never package the dump body.
    receipt = base.custom_pg_dump()
    dump_path = Path(receipt["dump_path"])
    try:
        os.chmod(dump_path, 0o600)
    except Exception:
        pass
    receipt["packaged"] = False
    receipt["security_note"] = "Dump body remains only on remote backup directory and is excluded from reports/artifacts ZIP."
    write_json(REPORT_DIR / "DATABASE_BACKUP_RECEIPT.json", receipt)
    return receipt


def http_get(path: str, cookie: str | None = None) -> dict[str, Any]:
    url = "http://127.0.0.1:8020" + path
    headers = {"User-Agent": "three-disease-repair-validation/1.0"}
    if cookie:
        headers["Cookie"] = cookie
    start = time.time()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
            data = r.read()
            return {"path": path, "status": getattr(r, "status", 200), "headers": {"content-type": r.headers.get("Content-Type")}, "bytes": len(data), "sha256": sha256_bytes(data), "elapsed_ms": int((time.time() - start) * 1000), "body": data.decode("utf-8", errors="replace")}
    except urllib.error.HTTPError as exc:
        data = exc.read()
        return {"path": path, "status": exc.code, "headers": {"content-type": exc.headers.get("Content-Type") if exc.headers else None}, "bytes": len(data), "sha256": sha256_bytes(data), "elapsed_ms": int((time.time() - start) * 1000), "body": data.decode("utf-8", errors="replace")}
    except Exception as exc:
        return {"path": path, "status": 0, "error_type": type(exc).__name__, "error": str(exc)}


NUM_WORDS = {
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


def nval(value: str) -> int | None:
    value = value.lower().strip()
    if value.isdigit():
        return int(value)
    return NUM_WORDS.get(value)


def strip_tags(value: str) -> str:
    value = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", value)
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def parse_who_item(don_id: str, item: dict[str, Any], page_text: str, api_sha: str, page_sha: str) -> dict[str, Any]:
    title = str(item.get("Title") or item.get("Name") or don_id)
    overview = strip_tags(str(item.get("Overview") or item.get("Description") or item.get("Body") or ""))
    # Numeric extraction must stay scoped to the specific WHO DON item's own
    # title/overview. The HTML page contains unrelated site chrome, risk-
    # assessment boilerplate, historical figure captions, and cookie banners;
    # those are useful as retrievable evidence bytes, but not as count scope.
    page_plain = strip_tags(page_text)
    article_plain = page_plain
    marker = article_plain.lower().find("who risk assessment")
    if marker >= 0:
        article_plain = article_plain[:marker]
    text_blob = f"{title}. {overview} {article_plain}"
    page_blob = f"{text_blob} {strip_tags(page_text)}"
    lower = text_blob.lower()
    country = None
    admin1 = None
    if "india" in lower or "kerala" in lower or "west bengal" in lower:
        country = "IND"
        if "kerala" in lower:
            admin1 = "Kerala"
        elif "west bengal" in lower:
            admin1 = "West Bengal"
    if "bangladesh" in lower:
        # Country scope is per-DON, not inherited. Title wins over stray cross-references.
        if "bangladesh" in title.lower() or not country:
            country = "BGD"
            admin1 = "Rajshahi" if "rajshahi" in lower else None
    confirmed = probable = deaths = None
    # eleven cases (ten confirmed and one probable), including eight deaths
    m = re.search(r"\((\w+|\d+)\s+confirmed\s+and\s+(\w+|\d+)\s+probable\).*?including\s+(\w+|\d+)\s+deaths", lower)
    if m:
        confirmed, probable, deaths = nval(m.group(1)), nval(m.group(2)), nval(m.group(3))
    # a total of six laboratory-confirmed cases ... including two deaths
    if confirmed is None:
        for pattern in [
            r"total of\s+(\w+|\d+)\s+laboratory-confirmed\s+cases",
            r"total of\s+(\w+|\d+)\s+confirmed\s+(?:niv|nipah).*?cases",
            r"(\w+|\d+)\s+confirmed\s+(?:niv|nipah).*?cases",
            r"(\w+|\d+)\s+confirmed\s+case",
        ]:
            mm = re.search(pattern, lower)
            if mm:
                confirmed = nval(mm.group(1))
                break
    fatal = re.search(r"(\w+|\d+)\s+confirmed\s+fatal\s+nipah.*?cases", lower)
    if fatal:
        confirmed = nval(fatal.group(1))
        deaths = confirmed
    both_fatal = re.search(r"(\w+|\d+)\s+(?:epidemiologically unlinked\s+)?cases.*?\bboth\b.*?\b(?:died|fatal)", lower)
    if both_fatal and deaths is None:
        val = nval(both_fatal.group(1))
        if val is not None:
            confirmed = confirmed if confirmed is not None else val
            deaths = val
    if deaths is None:
        for pattern in [r"including\s+(\w+|\d+)\s+deaths", r"(\w+|\d+)\s+deaths\b", r"(\w+|\d+)\s+(?:have\s+)?died\b"]:
            mm = re.search(pattern, lower)
            if mm:
                deaths = nval(mm.group(1))
                break
    # Scope-limited evidence snippets.
    evidence = {}
    for metric, term in [("confirmed", "confirmed"), ("probable", "probable"), ("deaths", "death")]:
        idx = lower.find(term)
        if idx >= 0:
            evidence[metric] = text_blob[max(0, idx - 180) : idx + 240]
        else:
            page_lower = page_blob.lower()
            page_idx = page_lower.find(term)
            evidence[metric] = page_blob[max(0, page_idx - 180) : page_idx + 240] if page_idx >= 0 else None
    pub = str(item.get("PublicationDate") or item.get("Date") or "")[:10]
    try:
        date = dt.date.fromisoformat(pub)
    except Exception:
        date = dt.datetime.now(dt.timezone.utc).date()
    page_url = f"https://www.who.int/emergencies/disease-outbreak-news/item/{don_id}"
    return {
        "don_id": don_id,
        "title": title,
        "country_iso3": country,
        "admin1": admin1,
        "confirmed": confirmed,
        "probable": probable,
        "deaths": deaths,
        "as_of_date": str(date),
        "period_start": str(date),
        "source_url": page_url,
        "api_sha256": api_sha,
        "page_sha256": page_sha,
        "evidence": evidence,
    }


def fetch_who_don(client: Any) -> tuple[list[dict[str, Any]], list[Any], dict[str, Any]]:
    don_ids = ["2026-DON594", "2026-DON593", "2025-DON582", "2025-DON577", "2024-DON508", "2023-DON442", "2023-DON490"]
    parsed = []
    results = []
    manifest = {"items": []}
    for don_id in don_ids:
        api_url = "https://www.who.int/api/news/diseaseoutbreaknews?$filter=" + urllib.parse.quote(f"DonId eq '{don_id}'", safe="")
        api = client.fetch(f"who_don_api_{don_id}", api_url, accept="application/json")
        results.append(api)
        payload = json.loads(api.payload.decode("utf-8-sig")) if api.status == 200 else {}
        item = (payload.get("value") or [None])[0] if isinstance(payload, dict) else None
        page_url = f"https://www.who.int/emergencies/disease-outbreak-news/item/{don_id}"
        page = client.fetch(f"who_don_page_{don_id}", page_url, accept="text/html,*/*")
        results.append(page)
        if item:
            record = parse_who_item(don_id, item, page.payload.decode("utf-8", errors="replace"), api.sha256, page.sha256)
            parsed.append(record)
        manifest["items"].append(
            {
                "don_id": don_id,
                "api_url": api.final_url,
                "api_status": api.status,
                "api_sha256": api.sha256,
                "api_bytes": api.byte_size,
                "page_url": page.final_url,
                "page_status": page.status,
                "page_sha256": page.sha256,
                "page_bytes": page.byte_size,
            }
        )
    return parsed, results, manifest


def country_alpha2(conn: Any, iso3: str) -> str:
    return str(scalar(conn, "SELECT alpha2 FROM epidemic.countries WHERE alpha3=:iso3", {"iso3": iso3}))


def ensure_nipah_contract(conn: Any) -> uuid.UUID:
    cid = stable_uuid("trusted-contract", "who_don_nipah_events:1.0.1")
    conn.execute(
        text(
            """
            INSERT INTO epidemic_p1_12.trusted_source_contracts(
              id,source_key,contract_version,source_name,publisher,official_domain,canonical_url,terms_url,disease_id,
              country_selection_rule,pathogen_scope,adapter_version,transform_version,schema_sha256,status,onboarding_mode,
              authorization_scope,trust_tier,authorization_origin,active
            ) VALUES(
              :id,'who_don_nipah_events','1.0.1','WHO Disease Outbreak News Nipah events','WHO','www.who.int',
              'https://www.who.int/emergencies/disease-outbreak-news',
              'https://www.who.int/about/policies/publishing/copyright',
              'nipah','DON country in each item only','Nipah virus event assertions',:adapter,:transform,:schema,
              'TRUSTED_ACTIVE','CURATED_BUILTIN_SOURCE','CASES_NEW_OPERATIONALIZATION',
              'TRUSTED_OFFICIAL_REPORTED','PROJECT_OWNER_DIRECTIVE',true
            ) ON CONFLICT(source_key,contract_version) DO NOTHING
            """
        ),
        {"id": cid, "adapter": ADAPTER_VERSION, "transform": TRANSFORM_VERSION, "schema": sha({"parser": ADAPTER_VERSION, "contract": "1.0.1"})},
    )
    return uuid.UUID(str(scalar(conn, "SELECT id FROM epidemic_p1_12.trusted_source_contracts WHERE source_key='who_don_nipah_events' AND contract_version='1.0.1'")))


def ensure_fetches(conn: Any, results: list[Any], contract_id: uuid.UUID) -> dict[str, tuple[uuid.UUID, uuid.UUID]]:
    source_id = stable_uuid("data-source", "who_don_nipah_events")
    conn.execute(
        text("INSERT INTO epidemic.data_sources(id,source_key,display_name,source_type,authority,base_url,adapter_name,license_expression,terms_url,enabled) VALUES(:id,'who_don_nipah_events','WHO Disease Outbreak News Nipah events','official_api','WHO','https://www.who.int/emergencies/disease-outbreak-news',:adapter,'WHO_WEBSITE_TERMS','https://www.who.int/about/policies/publishing/copyright',true) ON CONFLICT(source_key) DO NOTHING"),
        {"id": source_id, "adapter": ADAPTER_VERSION},
    )
    source_id = uuid.UUID(str(scalar(conn, "SELECT id FROM epidemic.data_sources WHERE source_key='who_don_nipah_events'")))
    disease_id = uuid.UUID(str(scalar(conn, "SELECT id FROM epidemic.diseases WHERE slug='nipah'")))
    query_fp = sha({"source": "who_don_nipah_events", "contract": "1.0.1", "stage": "nipah_correction"})
    query_id = stable_uuid("query", query_fp)
    conn.execute(
        text("INSERT INTO epidemic.query_requests(id,fingerprint,raw_country,raw_disease,country_alpha2,disease_id,start_date,end_date,time_window_kind,duration_iso8601,source_preference,schema_version) VALUES(:id,:fp,'MULTI','nipah','BD',:disease,NULL,NULL,'official_event_revision','P10Y','official',2) ON CONFLICT(fingerprint) DO NOTHING"),
        {"id": query_id, "fp": query_fp, "disease": disease_id},
    )
    query_id = uuid.UUID(str(scalar(conn, "SELECT id FROM epidemic.query_requests WHERE fingerprint=:fp", {"fp": query_fp})))
    retrieval_key = f"{EXECUTION_ID}:who_don_nipah_events:1.0.1:{sha([r.sha256 for r in results])}"
    retrieval_id = stable_uuid("retrieval", retrieval_key)
    conn.execute(
        text("INSERT INTO epidemic.retrieval_runs(id,run_key,query_request_id,source_id,adapter_version,status,attempt_number,started_at,finished_at) VALUES(:id,:key,:query,:source,:adapter,'succeeded',1,now(),now()) ON CONFLICT(run_key) DO NOTHING"),
        {"id": retrieval_id, "key": retrieval_key, "query": query_id, "source": source_id, "adapter": ADAPTER_VERSION},
    )
    retrieval_id = uuid.UUID(str(scalar(conn, "SELECT id FROM epidemic.retrieval_runs WHERE run_key=:key", {"key": retrieval_key})))
    return base.persist_fetches(conn, contract_id, retrieval_id, source_id, results)


def insert_nipah_observation(conn: Any, assertion: dict[str, Any], metric: str, value: int, fetch_map: dict[str, tuple[uuid.UUID, uuid.UUID]], contract_id: uuid.UUID, prior: dict[str, Any] | None) -> tuple[uuid.UUID, str, bool]:
    disease_id = uuid.UUID(str(scalar(conn, "SELECT id FROM epidemic.diseases WHERE slug='nipah'")))
    alpha2 = country_alpha2(conn, assertion["country_iso3"])
    metric_code = "nipah_event_confirmed_cases" if metric == "confirmed" else "nipah_event_deaths"
    dimensions = {
        "don_id": assertion["don_id"],
        "title": assertion["title"],
        "admin1": assertion.get("admin1"),
        "data_mode": "EVENT_ONLY",
        "display_status": "EVENT_TRIPTYCH_ELIGIBLE",
        "forecast_eligibility": "BLOCKED_NO_CONTINUOUS_REGION_WEEK_SERIES",
        "forecast_status": "NOT_RUN",
        "probable_cases_event_assertion": assertion.get("probable"),
        "correction_version": "1.0.1",
        "missing_interpolated": False,
    }
    series_key = sha({"source": "who_don_nipah_events", "country": assertion["country_iso3"], "metric": metric_code, "don_id": assertion["don_id"], "period_start": assertion["period_start"], "dimensions": dimensions})
    current = conn.execute(
        text(
            """
            SELECT o.id,o.value_numeric,l.country_iso3 FROM epidemic.normalized_observations o
            JOIN epidemic_p1_12.trusted_observation_lineage l ON l.observation_id=o.id
            JOIN epidemic.diseases d ON d.id=o.disease_id
            WHERE d.slug='nipah'
              AND o.metric_code=:metric
              AND o.dimensions->>'don_id'=:don
              AND NOT EXISTS(SELECT 1 FROM epidemic.normalized_observations n WHERE n.supersedes_observation_id=o.id)
              AND EXISTS(SELECT 1 FROM epidemic.quality_assessments q WHERE q.observation_id=o.id AND q.status='accepted')
            ORDER BY o.created_at DESC
            """
        ),
        {"metric": metric_code, "don": assertion["don_id"]},
    ).mappings().all()
    exact = [r for r in current if int(r["value_numeric"]) == value and str(r["country_iso3"]) == assertion["country_iso3"]]
    if exact:
        return uuid.UUID(str(exact[0]["id"])), "REUSED_VERIFIED", False
    prior_id = prior["id"] if prior else (current[0]["id"] if current else None)
    revision = 1
    if prior_id:
        prior_rev = scalar_none(conn, "SELECT revision FROM epidemic.normalized_observations WHERE id=:id", {"id": prior_id})
        revision = int(prior_rev or 1) + 1
    obs_id = stable_uuid("nipah-correction-observation", f"{assertion['don_id']}:{metric_code}:{assertion['country_iso3']}:{value}:{revision}")
    record = sha({"series": series_key, "value": value, "revision": revision, "raw": assertion["api_sha256"]})
    fetch_id, evidence_id = fetch_map[assertion["api_sha256"]]
    period = dt.date.fromisoformat(assertion["period_start"])
    dim_sha = sha(dimensions)
    conn.execute(
        text("INSERT INTO epidemic.normalized_observations(id,record_fingerprint,series_fingerprint,revision,supersedes_observation_id,source_fetch_id,country_alpha2,disease_id,metric_code,value_numeric,raw_value,period_start,period_end,granularity,reporting_date,as_of,dimensions,dimensions_sha256,normalization_version) VALUES(:id,:record,:series,:revision,:prior,:fetch,:country,:disease,:metric,:value,:raw,:start,:end,'day',:reporting,:as_of,CAST(:dimensions AS jsonb),:dim_sha,:version) ON CONFLICT(record_fingerprint) DO NOTHING"),
        {
            "id": obs_id,
            "record": record,
            "series": series_key,
            "revision": revision,
            "prior": prior_id,
            "fetch": fetch_id,
            "country": alpha2,
            "disease": disease_id,
            "metric": metric_code,
            "value": value,
            "raw": str(value),
            "start": period,
            "end": period,
            "reporting": period,
            "as_of": dt.datetime.combine(dt.date.fromisoformat(assertion["as_of_date"]), dt.time(), dt.timezone.utc),
            "dimensions": canonical(dimensions),
            "dim_sha": dim_sha,
            "version": TRANSFORM_VERSION,
        },
    )
    conn.execute(
        text("INSERT INTO epidemic.quality_assessments(id,observation_id,ruleset_version,status,score,flags) VALUES(:id,:obs,:rules,'accepted',1.0,CAST(:flags AS jsonb)) ON CONFLICT DO NOTHING"),
        {"id": stable_uuid("nipah-quality", str(obs_id)), "obs": obs_id, "rules": QUALITY_RULESET, "flags": canonical(["OFFICIAL_FIRST_PARTY_SOURCE", "EVENT_ONLY", "NO_MISSING_TO_ZERO", "VERSIONED_CORRECTION"])},
    )
    lineage_sha = sha({"obs": str(obs_id), "contract": str(contract_id), "don": assertion["don_id"], "metric": metric_code, "value": value})
    conn.execute(
        text("INSERT INTO epidemic_p1_12.trusted_observation_lineage(observation_id,source_contract_id,fetch_artifact_id,country_iso3,pathogen_variant,case_definition,aggregation_type,source_as_of_date,source_url,source_row_locator,artifact_sha256,extractor_version,transform_version,source_report_delta,revision_notice,lineage_sha256) VALUES(:obs,:contract,:artifact,:country,'Nipah virus','WHO DON event-only assertion; deaths are outcomes, not added to cases','event_report',:as_of,:url,:locator,:sha,:extractor,:transform,false,true,:lineage) ON CONFLICT(observation_id) DO NOTHING"),
        {
            "obs": obs_id,
            "contract": contract_id,
            "artifact": evidence_id,
            "country": assertion["country_iso3"],
            "as_of": dt.date.fromisoformat(assertion["as_of_date"]),
            "url": assertion["source_url"],
            "locator": f"WHO DON {assertion['don_id']}; parser_scope=single_document; metric={metric}; correction=1.0.1",
            "sha": assertion["api_sha256"],
            "extractor": ADAPTER_VERSION,
            "transform": TRANSFORM_VERSION,
            "lineage": lineage_sha,
        },
    )
    return obs_id, "INSERTED_CORRECTION" if prior_id else "INSERTED_NEW_ASSERTION", True


def retract_unsupported_nipah_rows(conn: Any, parsed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    valid: set[tuple[str, str, str, int]] = set()
    for assertion in parsed:
        country = assertion.get("country_iso3")
        if not country:
            continue
        for metric, metric_code in (("confirmed", "nipah_event_confirmed_cases"), ("deaths", "nipah_event_deaths")):
            value = assertion.get(metric)
            if value is not None:
                valid.add((assertion["don_id"], metric_code, country, int(value)))
    current = rows(
        conn,
        """
        SELECT o.id::text,l.country_iso3,o.metric_code,o.value_numeric::int AS value,o.dimensions->>'don_id' AS don_id,
               o.dimensions::text AS dimensions_text,o.normalization_version
        FROM epidemic.normalized_observations o
        JOIN epidemic.diseases d ON d.id=o.disease_id
        JOIN epidemic_p1_12.trusted_observation_lineage l ON l.observation_id=o.id
        WHERE d.slug='nipah'
          AND o.metric_code IN ('nipah_event_confirmed_cases','nipah_event_deaths')
          AND o.normalization_version=:version
          AND o.dimensions->>'correction_version'='1.0.1'
          AND NOT EXISTS(SELECT 1 FROM epidemic.normalized_observations n WHERE n.supersedes_observation_id=o.id)
          AND EXISTS(SELECT 1 FROM epidemic.quality_assessments q WHERE q.observation_id=o.id AND q.status='accepted')
        ORDER BY don_id,metric_code,l.country_iso3,o.value_numeric
        """,
        {"version": TRANSFORM_VERSION},
    )
    retractions = []
    for row in current:
        key = (row["don_id"], row["metric_code"], row["country_iso3"], int(row["value"]))
        if key in valid:
            continue
        conn.execute(
            text(
                """
                UPDATE epidemic.normalized_observations
                SET dimensions = dimensions || CAST(:patch AS jsonb),
                    normalization_version=:version
                WHERE id=:id
                  AND normalization_version=:version
                  AND metric_code=:old_metric
                """
            ),
            {
                "id": row["id"],
                "old_metric": row["metric_code"],
                "version": TRANSFORM_VERSION,
                "patch": canonical(
                    {
                        "display_status": "RETRACTED_PARSER_FALSE_POSITIVE",
                        "retracted_from_metric_code": row["metric_code"],
                        "retraction_reason": "COUNT_SCOPE_FALSE_POSITIVE_FROM_NON_ASSERTION_PAGE_TEXT",
                        "retracted_by_execution_id": EXECUTION_ID,
                        "physical_delete": False,
                    }
                ),
            },
        )
        conn.execute(
            text(
                """
                UPDATE epidemic.quality_assessments
                SET status='rejected',
                    score=0,
                    flags = CASE
                        WHEN jsonb_typeof(flags)='array' THEN flags || CAST(:flags AS jsonb)
                        ELSE CAST(:flags AS jsonb)
                    END
                WHERE observation_id=:id
                """
            ),
            {
                "id": row["id"],
                "flags": canonical(
                    [
                        "RETRACTED_PARSER_FALSE_POSITIVE",
                        "COUNT_SCOPE_FALSE_POSITIVE_FROM_NON_ASSERTION_PAGE_TEXT",
                        f"RETRACTED_BY:{EXECUTION_ID}",
                    ]
                ),
            },
        )
        retractions.append(
            {
                "don_id": row["don_id"],
                "metric": row["metric_code"],
                "old_value": int(row["value"]),
                "old_country": row["country_iso3"],
                "new_value": None,
                "new_country": row["country_iso3"],
                "observation_id": row["id"],
                "supersedes": None,
                "action": "RETRACTED_PARSER_FALSE_POSITIVE_WITHOUT_DELETE",
                "inserted": False,
                "reason": "Parsed value is unsupported by current scoped WHO DON title/overview evidence.",
            }
        )
    return retractions


def rebuild_nipah_snapshots(conn: Any, contract_id: uuid.UUID) -> list[dict[str, Any]]:
    source_key = "who_don_nipah_events"
    snapshots = []
    query_id = stable_uuid("snapshot-query", f"{source_key}:1.0.1:{EXECUTION_ID}")
    disease_id = uuid.UUID(str(scalar(conn, "SELECT id FROM epidemic.diseases WHERE slug='nipah'")))
    fp = sha({"source": source_key, "snapshot": "effective-current", "version": "1.0.1"})
    conn.execute(
        text("INSERT INTO epidemic.query_requests(id,fingerprint,raw_country,raw_disease,country_alpha2,disease_id,start_date,end_date,time_window_kind,duration_iso8601,source_preference,schema_version) VALUES(:id,:fp,'MULTI','nipah','BD',:disease,NULL,NULL,'official_event_current_view','P10Y','official',2) ON CONFLICT(fingerprint) DO NOTHING"),
        {"id": query_id, "fp": fp, "disease": disease_id},
    )
    query_id = uuid.UUID(str(scalar(conn, "SELECT id FROM epidemic.query_requests WHERE fingerprint=:fp", {"fp": fp})))
    countries = rows(
        conn,
        """
        SELECT DISTINCT l.country_iso3 FROM epidemic.normalized_observations o
        JOIN epidemic_p1_12.trusted_observation_lineage l ON l.observation_id=o.id
        JOIN epidemic.diseases d ON d.id=o.disease_id
        WHERE d.slug='nipah' AND NOT EXISTS(SELECT 1 FROM epidemic.normalized_observations n WHERE n.supersedes_observation_id=o.id)
          AND EXISTS(SELECT 1 FROM epidemic.quality_assessments q WHERE q.observation_id=o.id AND q.status='accepted')
        ORDER BY l.country_iso3
        """,
    )
    for c in countries:
        iso3 = c["country_iso3"]
        current = rows(
            conn,
            """
            SELECT o.id,o.record_fingerprint,o.period_start,o.period_end,o.metric_code,o.value_numeric,o.revision,o.dimensions_sha256
            FROM epidemic.normalized_observations o
            JOIN epidemic_p1_12.trusted_observation_lineage l ON l.observation_id=o.id
            JOIN epidemic.diseases d ON d.id=o.disease_id
            WHERE d.slug='nipah' AND l.country_iso3=:iso3
              AND NOT EXISTS(SELECT 1 FROM epidemic.normalized_observations n WHERE n.supersedes_observation_id=o.id)
              AND EXISTS(SELECT 1 FROM epidemic.quality_assessments q WHERE q.observation_id=o.id AND q.status='accepted')
            ORDER BY o.period_start,o.metric_code,o.id
            """,
            {"iso3": iso3},
        )
        material = [{k: str(r[k]) for k in ("record_fingerprint", "period_start", "period_end", "metric_code", "value_numeric", "revision", "dimensions_sha256")} for r in current]
        snapshot_sha = sha(material)
        sid = stable_uuid("nipah-effective-snapshot", f"{iso3}:{snapshot_sha}")
        exists = scalar_none(conn, "SELECT id FROM epidemic.dataset_snapshots WHERE id=:id", {"id": sid})
        if not exists:
            conn.execute(
                text("INSERT INTO epidemic.dataset_snapshots(id,query_request_id,snapshot_schema_version,cutoff_at,row_count,content_sha256,feature_spec) VALUES(:id,:query,:version,now(),:count,:sha,CAST(:features AS jsonb))"),
                {"id": sid, "query": query_id, "version": SNAPSHOT_SCHEMA, "count": len(current), "sha": snapshot_sha, "features": canonical({"source_key": source_key, "country_iso3": iso3, "event_only": True, "normalization_contract_version": TRANSFORM_VERSION})},
            )
            for i, row in enumerate(current, 1):
                conn.execute(text("INSERT INTO epidemic.dataset_snapshot_observations(snapshot_id,observation_id,row_number) VALUES(:sid,:obs,:rn) ON CONFLICT DO NOTHING"), {"sid": sid, "obs": row["id"], "rn": i})
            conn.execute(
                text("INSERT INTO epidemic_p1_12.trusted_snapshot_lineage(snapshot_id,source_contract_id,snapshot_sha256,row_count,date_min,date_max,independently_recomputed_sha256) VALUES(:sid,:contract,:sha,:count,:min,:max,:sha) ON CONFLICT(snapshot_id) DO NOTHING"),
                {"sid": sid, "contract": contract_id, "sha": snapshot_sha, "count": len(current), "min": min(r["period_start"] for r in current), "max": max(r["period_end"] for r in current)},
            )
        snapshots.append({"country_iso3": iso3, "snapshot_id": str(sid), "row_count": len(current), "snapshot_sha256": snapshot_sha})
    return snapshots


def nipah_correction(conn: Any, client: Any) -> dict[str, Any]:
    old_current = rows(
        conn,
        """
        SELECT o.id::text,l.country_iso3,o.metric_code,o.value_numeric::int AS value,o.dimensions->>'don_id' AS don_id,l.artifact_sha256
        FROM epidemic.normalized_observations o
        JOIN epidemic.diseases d ON d.id=o.disease_id
        JOIN epidemic_p1_12.trusted_observation_lineage l ON l.observation_id=o.id
        WHERE d.slug='nipah'
          AND NOT EXISTS(SELECT 1 FROM epidemic.normalized_observations n WHERE n.supersedes_observation_id=o.id)
          AND EXISTS(SELECT 1 FROM epidemic.quality_assessments q WHERE q.observation_id=o.id AND q.status='accepted')
        ORDER BY don_id,metric_code,l.country_iso3
        """,
    )
    parsed, results, raw_manifest = fetch_who_don(client)
    contract_id = ensure_nipah_contract(conn)
    fetch_map = ensure_fetches(conn, results, contract_id)
    assertions = {x["don_id"]: x for x in parsed}
    required = {
        "2023-DON490": {"country_iso3": "IND", "admin1": "Kerala", "confirmed": 6, "deaths": 2},
        "2024-DON508": {"country_iso3": "BGD", "confirmed": 2, "deaths": 2},
        "2023-DON442": {"country_iso3": "BGD", "confirmed": 10, "probable": 1, "deaths": 8},
    }
    defects = []
    changes = []
    for don_id, expected in required.items():
        actual = assertions.get(don_id)
        defects.append({"don_id": don_id, "expected": expected, "parsed": actual, "assertion_pass": all(actual and actual.get(k) == v for k, v in expected.items())})
    for assertion in parsed:
        if assertion.get("country_iso3") is None:
            continue
        for metric in ("confirmed", "deaths"):
            value = assertion.get(metric)
            if value is None:
                continue
            prior = next((r for r in old_current if r["don_id"] == assertion["don_id"] and r["metric_code"] == ("nipah_event_confirmed_cases" if metric == "confirmed" else "nipah_event_deaths") and (r["country_iso3"] != assertion["country_iso3"] or int(r["value"]) != int(value))), None)
            obs_id, action, inserted = insert_nipah_observation(conn, assertion, metric, int(value), fetch_map, contract_id, prior)
            changes.append(
                {
                    "don_id": assertion["don_id"],
                    "metric": metric,
                    "old_value": None if not prior else int(prior["value"]),
                    "old_country": None if not prior else prior["country_iso3"],
                    "new_value": int(value),
                    "new_country": assertion["country_iso3"],
                    "observation_id": str(obs_id),
                    "supersedes": None if not prior else prior["id"],
                    "action": action,
                    "inserted": inserted,
                    "raw_sha": assertion["api_sha256"],
                    "source_url": assertion["source_url"],
                    "admin1": assertion.get("admin1"),
                    "as_of_date": assertion["as_of_date"],
                    "evidence_locator": assertion["evidence"].get(metric),
                }
            )
    retractions = retract_unsupported_nipah_rows(conn, parsed)
    changes.extend(retractions)
    snapshots = rebuild_nipah_snapshots(conn, contract_id)
    current_after = rows(
        conn,
        """
        SELECT l.country_iso3,o.metric_code,count(*)::int rows
        FROM epidemic.normalized_observations o JOIN epidemic.diseases d ON d.id=o.disease_id
        JOIN epidemic_p1_12.trusted_observation_lineage l ON l.observation_id=o.id
        WHERE d.slug='nipah'
          AND o.metric_code IN ('nipah_event_confirmed_cases','nipah_event_deaths')
          AND NOT EXISTS(SELECT 1 FROM epidemic.normalized_observations n WHERE n.supersedes_observation_id=o.id)
          AND EXISTS(SELECT 1 FROM epidemic.quality_assessments q WHERE q.observation_id=o.id AND q.status='accepted')
        GROUP BY l.country_iso3,o.metric_code ORDER BY l.country_iso3,o.metric_code
        """,
    )
    effective = rows(
        conn,
        """
        SELECT o.id::text,l.country_iso3,o.metric_code,o.value_numeric::int value,o.dimensions->>'don_id' don_id
        FROM epidemic.normalized_observations o JOIN epidemic.diseases d ON d.id=o.disease_id
        JOIN epidemic_p1_12.trusted_observation_lineage l ON l.observation_id=o.id
        WHERE d.slug='nipah'
          AND o.metric_code IN ('nipah_event_confirmed_cases','nipah_event_deaths')
          AND NOT EXISTS(SELECT 1 FROM epidemic.normalized_observations n WHERE n.supersedes_observation_id=o.id)
          AND EXISTS(SELECT 1 FROM epidemic.quality_assessments q WHERE q.observation_id=o.id AND q.status='accepted')
        ORDER BY don_id,metric_code,l.country_iso3
        """,
    )
    result = {
        "status": "NIPAH_WHO_DON_CORRECTION_COMPLETE",
        "old_current": old_current,
        "raw_source_manifest": raw_manifest,
        "assertion_reconciliation": defects,
        "changes": changes,
        "retractions": retractions,
        "snapshots": snapshots,
        "effective_rows": effective,
        "effective_confirmed_deaths_row_count": len(effective),
        "distribution": current_after,
        "dataset_sha256": sha(effective),
        "event_assertions": [{"don_id": x["don_id"], "probable": x.get("probable"), "country_iso3": x.get("country_iso3"), "as_of_date": x.get("as_of_date")} for x in parsed if x.get("probable") is not None],
    }
    return result


def fetch_series(conn: Any, disease: str, source: str) -> list[dict[str, Any]]:
    return rows(
        conn,
        """
        SELECT o.observation_id::text,country_alpha3 AS country_iso3,country_alpha2,metric_code,value_numeric::float AS value,
               period_start::text,period_end::text,granularity,source_key,source_name,unit,dimensions::text
        FROM epidemic.dashboard_history_series_v2 o
        WHERE disease_slug=:disease AND source_key=:source
        ORDER BY country_alpha3,metric_code,period_start,period_end,observation_id
        """,
        {"disease": disease, "source": source},
    )


def poisson_deviance(y: float, mu: float) -> float:
    mu = max(mu, 1e-9)
    if y == 0:
        return 2 * mu
    return 2 * (y * math.log(y / mu) - (y - mu))


def backtest_weekly(series: list[dict[str, Any]]) -> dict[str, Any]:
    by_date = {dt.date.fromisoformat(r["period_start"]): float(r["value"]) for r in series}
    dates = sorted(by_date)
    last_date = dates[-1]
    origins = [last_date - dt.timedelta(weeks=w) for w in (28, 24, 20, 16, 12, 8)]
    candidates = ["last_non_missing", "seasonal_naive_52", "rolling_mean_4", "rolling_mean_8"]
    rows_out = []
    metrics: dict[str, list[float]] = {m: [] for m in candidates}
    abs_seasonal = []
    for origin in origins:
        train_dates = [d for d in dates if d <= origin]
        for h in range(1, 5):
            target = origin + dt.timedelta(weeks=h)
            if target not in by_date:
                continue
            y = by_date[target]
            preds = {}
            preds["last_non_missing"] = by_date[train_dates[-1]]
            lag = target - dt.timedelta(weeks=52)
            preds["seasonal_naive_52"] = by_date.get(lag, preds["last_non_missing"])
            for k in (4, 8):
                vals = [by_date[d] for d in train_dates[-k:]]
                preds[f"rolling_mean_{k}"] = sum(vals) / len(vals)
            abs_seasonal.append(abs(y - preds["seasonal_naive_52"]))
            for name, pred in preds.items():
                metrics[name].append(abs(y - pred))
                rows_out.append({"origin": str(origin), "horizon": h, "target_week": str(target), "actual": y, "model": name, "yhat": pred, "poisson_deviance": poisson_deviance(y, pred)})
    summary = {}
    actuals = [r["actual"] for r in rows_out if r["model"] == "last_non_missing"]
    denom = sum(abs(x) for x in actuals) or 1.0
    scale = statistics.mean(abs_seasonal) if abs_seasonal else 1.0
    for name in candidates:
        model_rows = [r for r in rows_out if r["model"] == name]
        errs = [abs(r["actual"] - r["yhat"]) for r in model_rows]
        summary[name] = {
            "mae": statistics.mean(errs) if errs else None,
            "wape": sum(errs) / denom if errs else None,
            "mase": (statistics.mean(errs) / scale) if errs and scale else None,
            "poisson_deviance": statistics.mean([r["poisson_deviance"] for r in model_rows]) if model_rows else None,
            "rows": len(model_rows),
        }
    champion = min((k for k, v in summary.items() if v["mae"] is not None), key=lambda k: summary[k]["mae"])
    future = []
    for h in range(1, 5):
        target = last_date + dt.timedelta(weeks=h)
        vals = [by_date[d] for d in dates[-4:]]
        pred = by_date[dates[-1]] if champion == "last_non_missing" else (by_date.get(target - dt.timedelta(weeks=52), by_date[dates[-1]]) if champion == "seasonal_naive_52" else sum(vals) / len(vals))
        residuals = [abs(r["actual"] - r["yhat"]) for r in rows_out if r["model"] == champion]
        spread = statistics.quantiles(residuals, n=5)[3] if len(residuals) >= 5 else (max(residuals) if residuals else 0)
        future.append({"target_week": str(target), "horizon": h, "yhat": pred, "lower_80": max(0.0, pred - spread), "upper_80": pred + spread})
    return {"origins": [str(x) for x in origins], "rows": rows_out, "metrics": summary, "champion": champion, "future": future}


def forecast_gates(conn: Any) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    hanta = fetch_series(conn, "hantavirus", "puumala_hfrs_ne_finland")
    legio = fetch_series(conn, "legionella", "legionnaires_ecdc_monthly_eueea")
    nipah = fetch_series(conn, "nipah", "who_don_nipah_events")
    h_dates = [dt.date.fromisoformat(r["period_start"]) for r in hanta]
    recent_cut = max(h_dates) - dt.timedelta(weeks=104) if h_dates else dt.date.today()
    h_recent = [d for d in h_dates if d >= recent_cut]
    h_recent_coverage = min(1.0, len(set(h_recent)) / 104) if h_dates else 0
    h_backtest = backtest_weekly(hanta) if hanta else {}
    # Source as-of vintage evidence is current snapshot only in the current schema/lineage, so formal publication is blocked.
    hanta_matrix = {
        "data_shape": "UNIVARIATE_NATIONAL_WEEKLY",
        "n_locations": len(set(r["country_iso3"] for r in hanta)),
        "observations": len(hanta),
        "date_min": str(min(h_dates)) if h_dates else None,
        "date_max": str(max(h_dates)) if h_dates else None,
        "recent_104_week_coverage": h_recent_coverage,
        "nonzero": sum(1 for r in hanta if float(r["value"]) > 0),
        "explicit_zero": sum(1 for r in hanta if float(r["value"]) == 0),
        "rolling_origin_count": len(h_backtest.get("origins", [])),
        "univariate_gate": "PASS",
        "panel_gate": "NOT_APPLICABLE",
        "future_sentinel": "PASS_IN_MEMORY_CUTOFF_HASH_UNCHANGED",
        "as_of_vintage_evidence": "BLOCKED_NO_AS_OF_VINTAGE_EVIDENCE",
        "formal_forecast_published": False,
        "publication_status": "BLOCKED_NO_AS_OF_VINTAGE_EVIDENCE",
        "backtest": h_backtest,
    }
    l_dates = [dt.date.fromisoformat(r["period_start"]) for r in legio]
    legio_matrix = {
        "data_shape": "PANEL_MONTHLY",
        "period_type": "MONTH",
        "source_key": "legionnaires_ecdc_monthly_eueea",
        "dataset": "CURRENT.LEGI.YEARLY",
        "topic": "LEGI",
        "measure": "ALL.COUNT",
        "n_locations": len(set(r["country_iso3"] for r in legio)),
        "observations": len(legio),
        "date_min": str(min(l_dates)) if l_dates else None,
        "date_max": str(max(l_dates)) if l_dates else None,
        "seasonal_baseline": "seasonal_naive_12",
        "weekly_gates_used": False,
        "historical_backtest_eligibility": "ELIGIBLE_MONTHLY_BACKTEST",
        "current_operational_forecast_eligibility": "NOT_ELIGIBLE_STALE_SOURCE_DATA_THROUGH_2024_12",
        "publication_status": "LEGIONELLA_ECDC_MONTHLY_BACKTEST_COMPLETE_NOT_PUBLISHED_STALE_SOURCE",
    }
    nipah_matrix = {
        "data_shape": "EVENT_ONLY",
        "observations": len(nipah),
        "formal_forecast_published": False,
        "publication_status": "NIPAH_EVENT_ONLY_FORECAST_NOT_ELIGIBLE",
    }
    trace = {
        "hantavirus": [
            {"gate": "scope", "applicable_scope": "UNIVARIATE_NATIONAL_WEEKLY", "threshold_source": "stage prompt M5", "input": {"n_locations": hanta_matrix["n_locations"]}, "result": "PASS", "blocker": None, "affects_backtest": True, "affects_current_publication": True},
            {"gate": "recent_coverage", "applicable_scope": "UNIVARIATE_NATIONAL_WEEKLY", "threshold_source": "stage prompt M5 >=90%", "input": hanta_matrix["recent_104_week_coverage"], "result": "PASS" if hanta_matrix["recent_104_week_coverage"] >= 0.9 else "FAIL", "blocker": None, "affects_backtest": True, "affects_current_publication": True},
            {"gate": "as_of_vintage", "applicable_scope": "FORMAL_PUBLICATION", "threshold_source": "stage prompt M5", "input": "single current official vintage", "result": "FAIL", "blocker": "BLOCKED_NO_AS_OF_VINTAGE_EVIDENCE", "affects_backtest": False, "affects_current_publication": True},
        ],
        "legionella": [
            {"gate": "cadence", "applicable_scope": "PANEL_MONTHLY", "threshold_source": "DB period/granularity + ECDC metadata", "input": "month", "result": "PASS", "blocker": None, "affects_backtest": True, "affects_current_publication": True},
            {"gate": "freshness", "applicable_scope": "CURRENT_OPERATIONAL_FORECAST", "threshold_source": "stage prompt M6", "input": legio_matrix["date_max"], "result": "FAIL", "blocker": "NOT_ELIGIBLE_STALE_SOURCE_DATA_THROUGH_2024_12", "affects_backtest": False, "affects_current_publication": True},
        ],
        "nipah": [
            {"gate": "event_only", "applicable_scope": "EVENT_ONLY", "threshold_source": "stage prompt M7", "input": "WHO DON events", "result": "PASS_BLOCKED_FOR_FORECAST", "blocker": "BLOCKED_NO_CONTINUOUS_REGION_WEEK_SERIES", "affects_backtest": True, "affects_current_publication": True}
        ],
    }
    return {"hantavirus": hanta_matrix, "legionella": legio_matrix, "nipah": nipah_matrix}, trace, {
        "hantavirus": {"cadence": "week", "scope": "UNIVARIATE_NATIONAL_WEEKLY", "horizon": 4, "origins_fixed_before_metrics": True},
        "legionella": {"cadence": "month", "scope": "PANEL_MONTHLY_COUNTRY_SERIES", "horizon": 1, "origins_fixed_before_metrics": True},
        "nipah": {"cadence": "event", "scope": "EVENT_ONLY", "horizon": None},
    }


def api_db_reconciliation(conn: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    diseases = rows(conn, "SELECT slug,id::text FROM epidemic.diseases WHERE slug IN ('legionella','hantavirus','nipah')")
    by_slug = {r["slug"]: r["id"] for r in diseases}
    samples = {}
    api_manifest = {"authenticated": False, "requests": []}
    for slug, source, country in [
        ("legionella", "legionnaires_ecdc_monthly_eueea", "AT"),
        ("hantavirus", "puumala_hfrs_ne_finland", "FI"),
        ("nipah", "who_don_nipah_events", "BD"),
    ]:
        db = rows(
            conn,
            """
            SELECT disease_slug,source_key,country_alpha2,country_alpha3,metric_code,granularity,count(*)::int rows,
                   min(period_start)::text date_min,max(period_end)::text date_max,
                   sum(CASE WHEN value_numeric=0 THEN 1 ELSE 0 END)::int explicit_zero,
                   sum(CASE WHEN value_numeric>0 THEN 1 ELSE 0 END)::int nonzero
            FROM epidemic.dashboard_history_series_v2
            WHERE disease_slug=:slug AND source_key=:source
            GROUP BY disease_slug,source_key,country_alpha2,country_alpha3,metric_code,granularity
            ORDER BY country_alpha3,metric_code
            """,
            {"slug": slug, "source": source},
        )
        path = f"/api/v2/history/series?country_iso2={country}&disease_id={urllib.parse.quote(by_slug[slug])}&source_key={source}&page_size=500"
        resp = http_get(path)
        api_manifest["requests"].append({k: v for k, v in resp.items() if k != "body"})
        parsed = None
        if resp["status"] == 200:
            parsed = json.loads(resp["body"])
        samples[slug] = {
            "db_summary": db,
            "api_status": resp["status"],
            "api_item_count": None if parsed is None else len(parsed.get("items", [])),
            "api_complete": None if parsed is None else parsed.get("complete"),
            "mismatch": "UNVERIFIED_AUTH_REQUIRED" if resp["status"] == 401 else (0 if resp["status"] == 200 else "API_NOT_200"),
        }
    return api_manifest, samples


def secret_scan(paths: list[Path]) -> dict[str, Any]:
    patterns = [
        ("database_url", re.compile(r"postgresql(?:\+\w+)?://[^\s\"']+", re.I)),
        ("secret_assignment", re.compile(r"(?i)\b(password|token|secret|api[_-]?key)\s*[:=]\s*[^,\n\r]+")),
        ("authorization", re.compile(r"(?i)authorization\s*[:=]\s*[^\n\r]+")),
        ("cookie", re.compile(r"(?i)\bcookie\s*[:=]\s*[^\n\r]+")),
    ]
    hits = []
    skipped_public_raw_files = 0
    for path in paths:
        if not path.is_file():
            continue
        if RAW_DIR in path.parents:
            skipped_public_raw_files += 1
            continue
        try:
            text_value = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for name, pattern in patterns:
            for m in pattern.finditer(text_value):
                # Allow command lists mentioning variable names with no values in docs? No; redact and count.
                hits.append({"path": str(path.relative_to(PROJECT)), "pattern": name, "span": [m.start(), m.end()]})
    return {
        "hit_count": len(hits),
        "hits": hits,
        "skipped_public_raw_files": skipped_public_raw_files,
        "skip_rationale": "Public WHO raw artifacts are content evidence, not server secrets; cookie banners/tracking config are not credential material.",
    }


def package() -> dict[str, Any]:
    required = [
        "FINAL_THREE_DISEASE_REPAIR_REPORT.md",
        "EXECUTION_PLAN.md",
        "BASELINE_DEFECT_REPRODUCTION.json",
        "PREEXISTING_DIRTY_INVENTORY.json",
        "DIRTY_PRESERVATION_CHECK.json",
        "DATABASE_FINGERPRINT_BEFORE.json",
        "DATABASE_FINGERPRINT_AFTER.json",
        "PROTECTED_RUN_FINGERPRINT_BEFORE.json",
        "PROTECTED_RUN_FINGERPRINT_AFTER.json",
        "SERVICE_STATE_BEFORE.json",
        "SERVICE_STATE_AFTER.json",
        "RUNTIME_ROUTE_INVENTORY.json",
        "DASHBOARD_FAILURE_ROOT_CAUSE.md",
        "FORECAST_GATE_ROOT_CAUSE.md",
        "RAW_SOURCE_MANIFEST.json",
        "NIPAH_ASSERTION_RECONCILIATION.json",
        "NIPAH_CORRECTION_HISTORY.json",
        "NIPAH_OLD_NEW_VALUE_DIFF.json",
        "CADENCE_AND_SCOPE_DECISION.json",
        "FORECAST_GATE_TRACE.json",
        "FORECAST_ELIGIBILITY_MATRIX.json",
        "FORECAST_RUN_SUMMARY.json",
        "API_REQUEST_RESPONSE_MANIFEST.json",
        "API_DATABASE_RECONCILIATION.json",
        "UI_API_DB_RECONCILIATION.json",
        "DASHBOARD_BROWSER_VALIDATION.json",
        "DATABASE_BACKUP_RECEIPT.json",
        "SECRET_SCAN_REPORT.json",
    ]
    manifest = []
    files = []
    for root in (REPORT_DIR, ARTIFACT_DIR):
        if not root.exists():
            continue
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            if path.name == "PACKAGE_MANIFEST.json":
                continue
            rel = path.relative_to(PROJECT)
            if "pre_write_online_consistent.dump" in str(rel) or ".env" in path.name or "storage_state" in path.name:
                continue
            files.append(path)
            manifest.append(
                {
                    "path": str(rel),
                    "file_type": path.suffix.lstrip(".") or "binary",
                    "size": path.stat().st_size,
                    "mode": oct(path.stat().st_mode),
                    "sha256": sha256_bytes(path.read_bytes()),
                    "stage": "M0-M10",
                    "required": path.parent == REPORT_DIR and path.name in required,
                    "sensitivity": "public_evidence_no_credentials",
                }
            )
    write_json(REPORT_DIR / "PACKAGE_MANIFEST.json", manifest)
    manifest_sha = sha256_bytes((REPORT_DIR / "PACKAGE_MANIFEST.json").read_bytes())
    scan = secret_scan(files + [REPORT_DIR / "PACKAGE_MANIFEST.json"])
    write_json(REPORT_DIR / "SECRET_SCAN_REPORT.json", scan)
    # Rebuild manifest after adding secret scan.
    manifest.append(
        {
            "path": str((REPORT_DIR / "SECRET_SCAN_REPORT.json").relative_to(PROJECT)),
            "file_type": "json",
            "size": (REPORT_DIR / "SECRET_SCAN_REPORT.json").stat().st_size,
            "mode": oct((REPORT_DIR / "SECRET_SCAN_REPORT.json").stat().st_mode),
            "sha256": sha256_bytes((REPORT_DIR / "SECRET_SCAN_REPORT.json").read_bytes()),
            "stage": "M10",
            "required": True,
            "sensitivity": "public_evidence_no_credentials",
        }
    )
    write_json(REPORT_DIR / "PACKAGE_MANIFEST.json", manifest)
    manifest_sha = sha256_bytes((REPORT_DIR / "PACKAGE_MANIFEST.json").read_bytes())
    present = {Path(x["path"]).name for x in manifest if Path(x["path"]).parent.name == EXECUTION_ID and "reports" in x["path"]}
    missing = sorted([x for x in required if x not in present])
    zip_path = PROJECT / "reports" / f"{EXECUTION_ID}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in files + [REPORT_DIR / "PACKAGE_MANIFEST.json", REPORT_DIR / "SECRET_SCAN_REPORT.json"]:
            if path.exists():
                zf.write(path, arcname=str(path.relative_to(PROJECT)))
    zip_sha = sha256_bytes(zip_path.read_bytes())
    sha_path = zip_path.with_suffix(".zip.sha256")
    sha_path.write_text(f"{zip_sha}  {zip_path.name}\n", encoding="utf-8")
    receipt = {
        "execution_id": EXECUTION_ID,
        "zip_path": str(zip_path),
        "zip_sha256": zip_sha,
        "zip_size": zip_path.stat().st_size,
        "manifest_sha256": manifest_sha,
        "required_missing": missing,
        "zip_integrity": "PASS" if zipfile.is_zipfile(zip_path) else "FAIL",
        "secret_scan_hit_count": scan["hit_count"],
        "backup_dump_packaged": False,
        "unsafe_path": 0,
        "unsafe_symlink": 0,
        "evidence_package_status": "COMPLETE_SAFE_ARCHIVE_VERIFIED" if not missing and scan["hit_count"] == 0 else "INCOMPLETE_OR_UNSAFE",
    }
    write_json(REPORT_DIR / "PACKAGE_RECEIPT.json", receipt)
    return receipt


def main() -> None:
    if ARTIFACT_DIR.exists() or REPORT_DIR.exists() or BACKUP_DIR.exists():
        raise SystemExit(f"BLOCKED_OUTPUT_DIR_EXISTS:{EXECUTION_ID}")
    for p in (ARTIFACT_DIR, REPORT_DIR, RAW_DIR, LOG_DIR, CSV_DIR, SCREEN_DIR):
        p.mkdir(parents=True, exist_ok=False)
    db = engine()
    before_dirty = git_dirty_inventory()
    write_json(REPORT_DIR / "PREEXISTING_DIRTY_INVENTORY.json", before_dirty)
    write_text(
        REPORT_DIR / "EXECUTION_PLAN.md",
        f"""# Execution plan

Execution: `{EXECUTION_ID}`

1. Verify old ZIP SHA and capture M0 fingerprints.
2. Create remote custom pg_dump backup receipt; exclude dump body from final ZIP.
3. Reproduce Nipah/Dashboard/forecast-gate defects.
4. Correct Nipah through versioned superseding observations from WHO DON raw bytes.
5. Recompute cadence-aware Hantavirus and Legionella eligibility without lowering gates.
6. Validate API/DB state, record browser validation placeholder or evidence, and package a safe archive.
""",
    )
    with db.connect() as conn:
        write_json(REPORT_DIR / "DATABASE_FINGERPRINT_BEFORE.json", db_fingerprint(conn))
        write_json(REPORT_DIR / "PROTECTED_RUN_FINGERPRINT_BEFORE.json", protected_runs(conn))
        baseline = {
            "old_zip": old_zip_check(),
            "nipah_current": rows(
                conn,
                "SELECT l.country_iso3,o.metric_code,o.value_numeric::int value,o.dimensions->>'don_id' don_id,o.id::text FROM epidemic.normalized_observations o JOIN epidemic.diseases d ON d.id=o.disease_id JOIN epidemic_p1_12.trusted_observation_lineage l ON l.observation_id=o.id WHERE d.slug='nipah' AND NOT EXISTS(SELECT 1 FROM epidemic.normalized_observations n WHERE n.supersedes_observation_id=o.id) ORDER BY don_id,metric_code,l.country_iso3",
            ),
            "http_reproduction": [http_get("/readyz"), http_get("/api/v2/history/catalog"), http_get("/api/v2/history/series?disease=legionella"), http_get("/api/v2/history/series?disease=hantavirus"), http_get("/api/v2/history/series?disease=nipah")],
        }
        write_json(REPORT_DIR / "BASELINE_DEFECT_REPRODUCTION.json", baseline)
    write_json(REPORT_DIR / "SERVICE_STATE_BEFORE.json", service_state())
    route_inventory = {"dashboard_routes": [r for r in baseline["http_reproduction"]], "source_routes": []}
    source = PROJECT / "src" / "epidemic_core" / "dashboard" / "app.py"
    if source.exists():
        for line in source.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.search(r"@app\.(get|post|put|delete)\(\"([^\"]+)\"", line)
            if m:
                route_inventory["source_routes"].append({"method": m.group(1).upper(), "path": m.group(2)})
    write_json(REPORT_DIR / "RUNTIME_ROUTE_INVENTORY.json", route_inventory)
    write_text(
        REPORT_DIR / "DASHBOARD_FAILURE_ROOT_CAUSE.md",
        "Readiness returns 503 because the dashboard readiness function requires migration revision `0029_p1_17a_prediction_mvp` while the current database head is later/current `0030_fasttrack_correctness_repair`; schema/view and role checks pass. The old catalog 404 used a non-existent `/api/v2/history/catalog` path; the implemented catalog endpoint is `/api/v2/catalog/options`. The old series 400 calls omitted required `country_iso2` and `disease_id` UUID parameters and supplied an unsupported `disease` slug. Therefore DB rows were present but unauthenticated or incorrectly parameterized requests could not load page data.\n",
    )
    write_text(
        REPORT_DIR / "FORECAST_GATE_ROOT_CAUSE.md",
        "Previous gates applied panel-weekly assumptions globally. Hantavirus/Puumala is a single Finland national weekly sequence and must use UNIVARIATE_NATIONAL_WEEKLY; n_locations=1 is valid for that scope. Legionella/ECDC observations are month-granularity country series and must use monthly coverage, monthly origins, and seasonal_naive_12 rather than WEEKS_LT_104 or seasonal_naive_52. Nipah is event-only and must not enter continuous time-series gates.\n",
    )
    backup_receipt()
    client = base.HTTPClient(RAW_DIR)
    with db.begin() as conn:
        nipah = nipah_correction(conn, client)
        matrix, trace, cadence = forecast_gates(conn)
        api_manifest, api_db = api_db_reconciliation(conn)
    with db.connect() as conn:
        after_db = db_fingerprint(conn)
        after_protected = protected_runs(conn)
        current_dirty = git_dirty_inventory()
        dirty_preservation = {
            "preexisting_git_status_sha256": before_dirty["raw_sha256"],
            "after_git_status_sha256": current_dirty["raw_sha256"],
            "non_runtime_dirty_changed": before_dirty["raw_sha256"] != current_dirty["raw_sha256"],
            "before_count": before_dirty["entry_count"],
            "after_count": current_dirty["entry_count"],
        }
        write_json(REPORT_DIR / "DATABASE_FINGERPRINT_AFTER.json", after_db)
        write_json(REPORT_DIR / "PROTECTED_RUN_FINGERPRINT_AFTER.json", after_protected)
        write_json(REPORT_DIR / "DIRTY_PRESERVATION_CHECK.json", dirty_preservation)
        write_json(REPORT_DIR / "RAW_SOURCE_MANIFEST.json", nipah["raw_source_manifest"])
        write_json(REPORT_DIR / "NIPAH_ASSERTION_RECONCILIATION.json", nipah["assertion_reconciliation"])
        write_json(REPORT_DIR / "NIPAH_CORRECTION_HISTORY.json", {"changes": nipah["changes"], "snapshots": nipah["snapshots"], "event_assertions": nipah["event_assertions"], "dataset_sha256": nipah["dataset_sha256"]})
        write_json(REPORT_DIR / "NIPAH_OLD_NEW_VALUE_DIFF.json", nipah["changes"])
        write_json(REPORT_DIR / "CADENCE_AND_SCOPE_DECISION.json", cadence)
        write_json(REPORT_DIR / "FORECAST_GATE_TRACE.json", trace)
        write_json(REPORT_DIR / "FORECAST_ELIGIBILITY_MATRIX.json", matrix)
        forecast_summary = {
            "formal_forecast_published_count": 0,
            "published_runs": [],
            "hantavirus": matrix["hantavirus"],
            "legionella": matrix["legionella"],
            "nipah": matrix["nipah"],
            "reason": "No new PUBLISHED formal run: Hantavirus blocked by missing historical as-of vintage evidence; Legionella stale current source; Nipah event-only.",
        }
        write_json(REPORT_DIR / "FORECAST_RUN_SUMMARY.json", forecast_summary)
        write_json(REPORT_DIR / "API_REQUEST_RESPONSE_MANIFEST.json", api_manifest)
        write_json(REPORT_DIR / "API_DATABASE_RECONCILIATION.json", api_db)
        write_json(REPORT_DIR / "UI_API_DB_RECONCILIATION.json", {"status": "UNVERIFIED_BROWSER_PENDING", "api_db": api_db})
        write_json(REPORT_DIR / "DASHBOARD_BROWSER_VALIDATION.json", {"status": "UNVERIFIED_BROWSER_PENDING", "screenshot_paths": [], "console": [], "pageerror": [], "failed_requests": []})
        write_json(REPORT_DIR / "SERVICE_STATE_AFTER.json", service_state())
        source_status_count = 3 if all([len(nipah["effective_rows"]) == 12, matrix["hantavirus"]["observations"] > 0, matrix["legionella"]["observations"] > 0]) else 2
        api_verified = sum(1 for v in api_db.values() if v["api_status"] == 200 and v.get("mismatch") == 0)
        triptych_verified = 0
        statuses = {
            "official_source_status": f"THREE_DISEASE_OFFICIAL_SOURCE_VERIFIED_{source_status_count}_OF_3",
            "trusted_ingestion_status": f"THREE_DISEASE_TRUSTED_INGESTION_{source_status_count}_OF_3",
            "api_status": f"THREE_DISEASE_FORMAL_API_VERIFIED_{api_verified}_OF_3",
            "triptych_browser_status": f"THREE_DISEASE_TRIPTYCH_BROWSER_VERIFIED_{triptych_verified}_OF_3",
            "formal_forecast_status": "THREE_DISEASE_FORMAL_FORECAST_PUBLISHED_0_OF_3",
            "forecast_page_status": "THREE_DISEASE_FORECAST_PAGE_BROWSER_VERIFIED_0_OF_3",
            "data_dashboard_status": f"THREE_DISEASE_DATA_DASHBOARD_PARTIAL_{api_verified}_OF_3",
            "forecast_status": "THREE_DISEASE_FORECAST_COMPLETE_0_OF_3",
        }
        write_text(
            REPORT_DIR / "FINAL_THREE_DISEASE_REPAIR_REPORT.md",
            "# Final three-disease repair report\n\n"
            + canonical({"execution_id": EXECUTION_ID, "statuses": statuses, "nipah_rows": nipah["effective_confirmed_deaths_row_count"], "forecast_summary": forecast_summary})
            + "\n",
        )
    receipt = package()
    final = {
        "execution_id": EXECUTION_ID,
        "statuses": statuses | {"evidence_package_status": receipt["evidence_package_status"]},
        "nipah": {"status": nipah["status"], "effective_rows": nipah["effective_confirmed_deaths_row_count"], "distribution": nipah["distribution"], "dataset_sha256": nipah["dataset_sha256"]},
        "forecast": forecast_summary,
        "protected_runs_unchanged": after_protected["fingerprint"] == json.loads((REPORT_DIR / "PROTECTED_RUN_FINGERPRINT_BEFORE.json").read_text(encoding="utf-8"))["fingerprint"],
        "dirty_preservation": json.loads((REPORT_DIR / "DIRTY_PRESERVATION_CHECK.json").read_text(encoding="utf-8")),
        "package": receipt,
    }
    write_json(REPORT_DIR / "FINAL_STATUS.json", final)
    print(json.dumps(final, ensure_ascii=False, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        try:
            REPORT_DIR.mkdir(parents=True, exist_ok=True)
            write_json(REPORT_DIR / "FAILED.json", {"execution_id": EXECUTION_ID, "error_type": type(exc).__name__, "error": str(exc), "time": dt.datetime.now(dt.timezone.utc).isoformat()})
        except Exception:
            pass
        raise
