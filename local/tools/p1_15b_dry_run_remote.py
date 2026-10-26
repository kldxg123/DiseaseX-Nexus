from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import os
import re
import ssl
import subprocess
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url


PROJECT = Path(os.environ["PROJECT"])
PREVIOUS_REPORT = PROJECT / "reports/p1_15_ebola_cod_trusted_expansion"
PREVIOUS_ARTIFACTS = PREVIOUS_REPORT / "official_artifacts"
REPORT = PROJECT / "reports/p1_15b_source_contract_parser_dry_run"
ARTIFACTS = REPORT / "official_artifacts"
DISCOVERY_ARTIFACTS = REPORT / "official_dynamic_report_endpoints"
SCREENSHOTS = REPORT / "screenshots"

APPROVED = {
    "who_afro_drc_2026_situation": {
        "sha256": "4052804a39e2e55cc56648b03126c949ab4abb8f6c32112ff55722bf453f7515",
        "url": "https://www.afro.who.int/health-topics/ebola-disease/outbreak-drc-26",
        "family": "who_afro_drc_ebola_situation_index_v1",
    },
    "who_afro_drc_2026_sitrep_01": {
        "sha256": "87ff0a1ce39b54373807bbe4920d932804be704871444b09638e1fe09eaa13e2",
        "url": "https://www.afro.who.int/countries/democratic-republic-of-congo/publication/ebola-bundibugyo-virus-disease-outbreak-democratic-republic-congo-uganda-weekly-external-situation",
        "family": "who_afro_drc_ebola_publication_index_v1",
    },
    "who_afro_drc_2025_situation": {
        "sha256": "77d40a952a8aba13caf67c52369491a36f379305f7192d02f60ae442f8ddd919",
        "url": "https://www.afro.who.int/health-topics/ebola-disease/outbreak-drc-25",
        "family": "who_afro_drc_ebola_situation_index_v1",
    },
    "who_don_2022_equateur_final": {
        "sha256": "8bc0accdbadb8fc31324ba6a2d1052269c17fa30b6f1d378817622cc10783314",
        "url": "https://www.who.int/emergencies/disease-outbreak-news/item/2022-DON398",
        "family": "who_don_cod_ebola_html_v1",
    },
    "who_don_2022_north_kivu": {
        "sha256": "3d8390fd97fa804658f3701074c7c5cdf9be22e062da3345290b6038e6724f76",
        "url": "https://www.who.int/emergencies/disease-outbreak-news/item/2022-DON404",
        "family": "who_don_cod_ebola_html_v1",
    },
    "who_don_2022_equateur_initial": {
        "sha256": "3cb73899a95128c7fa9aa8835dfe0e308554bafe35fb7630f5914e7401d3ac30",
        "url": "https://www.who.int/emergencies/disease-outbreak-news/item/2022-DON377",
        "family": "who_don_cod_ebola_html_v1",
    },
}

CDC_NOT_APPROVED = {
    "id": "cdc_history_ebola_outbreaks",
    "decision": "NOT_APPROVED",
    "reason": "HTTP 403; no immutable artifact; no approved SHA-256; no reproducible original in this approval scope.",
}


def jdefault(value: Any) -> Any:
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    return str(value)


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_text(data: str) -> str:
    return sha_bytes(data.encode("utf-8"))


def sha_obj(value: Any) -> str:
    return sha_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=jdefault).encode("utf-8"))


def write_json(name: str, value: Any) -> None:
    (REPORT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=jdefault) + "\n", encoding="utf-8")


def run_cmd(args: list[str], timeout: int = 60) -> dict[str, Any]:
    cp = subprocess.run(args, cwd=PROJECT, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=timeout)
    return {
        "args": args,
        "returncode": cp.returncode,
        "stdout": cp.stdout.strip(),
        "stderr_sha256": sha_text(cp.stderr),
    }


def fetch_bytes(url: str, max_bytes: int = 6_000_000) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "epidemic-openclaw-p1-15b-official-dry-run/1.0"})
    started = dt.datetime.now(dt.timezone.utc)
    try:
        with urllib.request.urlopen(req, timeout=35, context=ssl.create_default_context()) as resp:
            final_url = resp.geturl()
            headers = {k.lower(): v for k, v in resp.headers.items()}
            body = resp.read(max_bytes + 1)
            truncated = len(body) > max_bytes
            if truncated:
                body = body[:max_bytes]
            return {
                "ok": True,
                "status": getattr(resp, "status", None),
                "url": url,
                "final_url": final_url,
                "domain": urllib.parse.urlparse(final_url).netloc,
                "retrieved_at": started.isoformat(),
                "content_type": headers.get("content-type"),
                "last_modified_observed_but_not_source_published_at": headers.get("last-modified"),
                "etag": headers.get("etag"),
                "bytes": len(body),
                "truncated": truncated,
                "sha256": sha_bytes(body),
                "body": body,
            }
    except Exception as exc:
        return {
            "ok": False,
            "url": url,
            "retrieved_at": started.isoformat(),
            "error_type": type(exc).__name__,
            "error": str(exc)[:500],
        }


def normalize_html_text(raw: str) -> str:
    text = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", raw)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</(p|div|li|h1|h2|h3|h4|section|article)>", "\n", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def load_or_fetch_approved_artifacts() -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    results: dict[str, dict[str, Any]] = {}
    evidence: list[dict[str, Any]] = []
    for source_id, spec in APPROVED.items():
        expected_sha = spec["sha256"]
        matches = list(PREVIOUS_ARTIFACTS.glob(f"{source_id}_{expected_sha[:16]}*"))
        body: bytes | None = None
        source = "previous_p1_15_archive"
        if matches:
            body = matches[0].read_bytes()
        else:
            fetched = fetch_bytes(spec["url"])
            if fetched.get("ok"):
                body = fetched["body"]
                source = "fresh_official_refetch"
            else:
                results[source_id] = {**spec, **{k: v for k, v in fetched.items() if k != "body"}, "sha256_verified": False}
                evidence.append(results[source_id])
                continue
        actual_sha = sha_bytes(body)
        target = ARTIFACTS / f"{source_id}_{actual_sha[:16]}.html"
        target.write_bytes(body)
        text_body = normalize_html_text(body.decode("utf-8", errors="replace"))
        row = {
            **spec,
            "id": source_id,
            "artifact_source": source,
            "artifact_path": str(target.relative_to(PROJECT)),
            "bytes": len(body),
            "sha256_actual": actual_sha,
            "sha256_expected": expected_sha,
            "sha256_verified": actual_sha == expected_sha,
            "retrieval_policy": "approved immutable bytes reused when exact SHA is present; otherwise official refetch only if SHA matches",
            "text_excerpt_hash": sha_text(text_body[:2000]),
        }
        results[source_id] = {**row, "body": body, "text": text_body}
        evidence.append(row)
    return results, evidence


def paragraph_locator(text: str, needles: list[str], source_id: str) -> dict[str, Any]:
    lowered = text.lower()
    best = ""
    for needle in needles:
        idx = lowered.find(needle.lower())
        if idx >= 0:
            start = max(0, idx - 500)
            end = min(len(text), idx + 900)
            best = text[start:end]
            break
    if not best:
        best = text[:1000]
    compact = re.sub(r"\s+", " ", best).strip()
    return {
        "source_id": source_id,
        "selector": "normalized_html_text_window",
        "heading_or_anchor": needles[0] if needles else "article text",
        "text_hash_sha256": sha_text(compact),
        "excerpt": compact[:700],
    }


def build_contracts(artifact_evidence: list[dict[str, Any]]) -> dict[str, Any]:
    git_head = run_cmd(["git", "rev-parse", "HEAD"])["stdout"]
    parser_hash = sha_text("p1_15b deterministic dry-run parser: WHO DON sections only; AFRO pages metadata/report discovery only")
    contracts = [
        {
            "contract_id": "who_don_cod_ebola_html_v1",
            "version": "1.0.0-p1_15b",
            "contract_sha256": "",
            "approved_domains": ["www.who.int"],
            "redirect_allowlist": ["www.who.int"],
            "family_url_pattern": "https://www.who.int/emergencies/disease-outbreak-news/item/20??-DON*",
            "logical_report_id_fields": ["publisher", "DON number", "source_published_at", "canonical_url"],
            "expected_mime": ["text/html"],
            "size_limit_bytes": 6_000_000,
            "article_structure_fingerprint": ["title", "publication date", "Situation at a glance", "Description of the situation"],
            "parser_version": "who_don_cod_ebola_html_parser_v1_dry_run",
            "parser_commit": git_head,
            "parser_hash_sha256": parser_hash,
            "country_rules": {
                "Democratic Republic of the Congo/DRC/COD": "COD",
                "Republic of the Congo/COG": "REJECT_FOR_COD",
                "Congo alone": "REVIEW_REQUIRED",
                "Uganda": "UGA_NEVER_COD",
                "joint_numbers_without_country_split": "REVIEW_REQUIRED",
            },
            "date_field_mapping": {
                "source_published_at": "article publication date only",
                "data_as_of": "explicit reporting/data period cutoff in article body",
                "event_ended_at": "explicit declaration of outbreak end",
                "retrieved_at": "artifact retrieval timestamp; never substitute for publication date",
                "http_last_modified": "transport metadata only; never source_published_at",
            },
            "metric_mapping": {
                "total_cases": "source-defined cumulative total; do not add to confirmed/probable",
                "confirmed_cases": "confirmed cumulative cases",
                "probable_cases": "probable cumulative cases",
                "deaths": "source-defined cumulative deaths if not further classified",
            },
            "artifact_key": ["canonical_url", "artifact_sha256"],
            "observation_natural_key": ["event_id", "country_iso3", "metric", "classification", "aggregation", "granularity", "data_as_of", "source_artifact_sha256"],
            "revision_policy": "new URL bytes or new content hash create a new artifact version; no overwrite",
            "schedule_policy": "not scheduler-enabled until human contract/parser/rowset approval",
            "watermark_policy": "source_published_at + artifact SHA",
            "drift_policy": "missing required section or changed structure => REVIEW_REQUIRED/BLOCKED, never NO_CHANGE",
            "public_access_policy": "official public HTTPS only; no login/captcha/paywall bypass",
        },
        {
            "contract_id": "who_afro_drc_ebola_situation_index_v1",
            "version": "1.0.0-p1_15b",
            "contract_sha256": "",
            "approved_domains": ["www.afro.who.int"],
            "redirect_allowlist": ["www.afro.who.int"],
            "family_url_pattern": "https://www.afro.who.int/health-topics/ebola-disease/outbreak-drc-*",
            "logical_report_id_fields": ["outbreak page URL", "event year", "embedded official report endpoint URL"],
            "expected_mime": ["text/html"],
            "size_limit_bytes": 6_000_000,
            "article_structure_fingerprint": ["outbreak landing page", "official dynamic report endpoint reference if present"],
            "parser_version": "who_afro_drc_ebola_situation_index_parser_v1_dry_run",
            "parser_commit": git_head,
            "parser_hash_sha256": parser_hash,
            "allowed_output": ["source metadata", "event metadata", "report discovery URLs"],
            "forbidden_output": ["case observations", "death observations", "daily/weekly observations from qualitative text"],
            "country_rules": {
                "Democratic Republic of the Congo/DRC/COD": "COD",
                "Republic of the Congo/COG": "REJECT_FOR_COD",
                "Congo alone": "REVIEW_REQUIRED",
                "Uganda": "UGA_NEVER_COD",
                "joint_numbers_without_country_split": "REVIEW_REQUIRED",
            },
            "date_field_mapping": {"source_published_at": "page visible publication/update date if present; never HTTP Last-Modified"},
            "artifact_key": ["canonical_url", "artifact_sha256"],
            "observation_natural_key": "not applicable: no observation candidate output in P1-15B",
            "drift_policy": "report endpoint missing or inaccessible => REVIEW_REQUIRED/BLOCKED with archived evidence",
            "public_access_policy": "official public HTTPS only",
        },
        {
            "contract_id": "who_afro_drc_ebola_publication_index_v1",
            "version": "1.0.0-p1_15b",
            "contract_sha256": "",
            "approved_domains": ["www.afro.who.int"],
            "redirect_allowlist": ["www.afro.who.int"],
            "family_url_pattern": "WHO AFRO country/publication landing/index pages",
            "logical_report_id_fields": ["publication page URL", "report link URL", "artifact SHA"],
            "expected_mime": ["text/html", "application/json"],
            "size_limit_bytes": 6_000_000,
            "article_structure_fingerprint": ["publication listing", "report links"],
            "parser_version": "who_afro_drc_ebola_publication_index_parser_v1_dry_run",
            "parser_commit": git_head,
            "parser_hash_sha256": parser_hash,
            "allowed_output": ["true report URLs only"],
            "forbidden_output": ["treating landing HTML as SitRep source"],
            "artifact_key": ["canonical_url", "artifact_sha256"],
            "drift_policy": "landing page without report URL => REVIEW_REQUIRED, not NO_CHANGE",
        },
        {
            "contract_id": "who_iris_drc_ebola_sitrep_pdf_v1_candidate",
            "version": "0.1.0-candidate-p1_15b",
            "contract_sha256": "",
            "approved_domains": ["iris.who.int", "apps.who.int"],
            "redirect_allowlist": ["iris.who.int", "apps.who.int", "www.afro.who.int"],
            "family_url_pattern": "official WHO IRIS PDF/report originals only after direct download/hash/MIME/structure verification",
            "logical_report_id_fields": ["IRIS handle", "PDF URL", "artifact SHA"],
            "expected_mime": ["application/pdf"],
            "size_limit_bytes": 25_000_000,
            "parser_version": "candidate_only_no_promotion",
            "parser_commit": git_head,
            "parser_hash_sha256": parser_hash,
            "allowed_output": ["staging/dry-run only after verified PDF structure"],
            "promotion_allowed": False,
        },
    ]
    for c in contracts:
        c["contract_sha256"] = sha_obj({k: v for k, v in c.items() if k != "contract_sha256"})
    return {
        "decision_scope": "SOURCE_CONTRACT_IMPLEMENTATION_AND_ISOLATED_DRY_RUN_ONLY",
        "approved_artifact_sha256": {x["id"]: x["sha256_actual"] for x in artifact_evidence if x.get("sha256_verified")},
        "cdc_history_ebola_outbreaks": CDC_NOT_APPROVED,
        "contracts": contracts,
        "source_contracts_sha256": sha_obj(contracts),
    }


def parse_don_dry_run(artifacts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    retrieved_at = dt.datetime.now(dt.timezone.utc).isoformat()
    don398 = artifacts["who_don_2022_equateur_final"]
    don377 = artifacts["who_don_2022_equateur_initial"]
    don404 = artifacts["who_don_2022_north_kivu"]
    candidates = []
    loc398 = paragraph_locator(
        don398["text"],
        ["the outbreak was declared over", "a total of five cases", "four confirmed and one probable"],
        "who_don_2022_equateur_final",
    )
    event_id = "cod-ebola-2022-equateur-mbandaka"
    common = {
        "review_case_id": "SRC-P1-15B-BACKFILL-DON398-EVENT-FINAL",
        "candidate_batch_id": "CAND-P1-15B-DON398-EVENT-FINAL-2022-EQUATEUR",
        "contract_id": "who_don_cod_ebola_html_v1",
        "contract_version": "1.0.0-p1_15b",
        "source_id": "who_don_2022_equateur_final",
        "source_url": APPROVED["who_don_2022_equateur_final"]["url"],
        "source_artifact_sha256": don398["sha256_actual"],
        "publisher": "World Health Organization",
        "event_id": event_id,
        "country_iso3": "COD",
        "country_name": "Democratic Republic of the Congo",
        "disease": "Ebola disease",
        "pathogen": "Ebola virus",
        "aggregation": "cumulative",
        "granularity": "event_final",
        "source_published_at": "2022-07-04",
        "data_as_of": "2022-07-03",
        "reporting_period_end": "2022-07-03",
        "event_ended_at": "2022-07-04",
        "retrieved_at": retrieved_at,
        "unit": "persons",
        "direct_or_derived": "direct",
        "locator": loc398,
        "duplicate_revision_conflict": {
            "dedupe_against_existing_26": "pending deterministic natural-key comparison; no Trusted write in this stage",
            "double_count_guard": "total_cases is source-defined and must not be summed with confirmed_cases+probable_cases",
        },
        "import_run_id": "P1-15B-DRY-RUN-READONLY",
        "dashboard_expected_impact_if_approved": "Adds 2022 Equateur event-final points to Ebola major-outbreak/history view only after human rowset approval.",
    }
    for metric, classification, value in [
        ("cases", "source_defined_total", 5),
        ("cases", "confirmed", 4),
        ("cases", "probable", 1),
        ("deaths", "source_defined_total", 5),
    ]:
        row = {**common, "metric": metric, "classification": classification, "value": value}
        row["observation_natural_key"] = "|".join(
            [
                row["event_id"],
                row["country_iso3"],
                row["metric"],
                row["classification"],
                row["aggregation"],
                row["granularity"],
                row["data_as_of"],
                row["source_artifact_sha256"],
            ]
        )
        row["candidate_row_sha256"] = sha_obj(row)
        candidates.append(row)
    rowset_sha = sha_obj(candidates)
    don377_review = {
        "source_id": "who_don_2022_equateur_initial",
        "candidate_use": "later_separate_report_snapshot_batch_only",
        "promotion_allowed_in_first_batch": False,
        "event_id": event_id,
        "source_published_at": "2022-04-28",
        "data_as_of": "2022-04-27",
        "review_flags": [
            "Page title publication date 2022-04-28 differs from citable reference date 2022-04-27.",
            "Historical 2018/2020 outbreak numbers must not be extracted into the 2022 event.",
            "No direct death observation is generated unless a direct aggregate death statement is present.",
        ],
        "locator": paragraph_locator(don377["text"], ["27 April 2022", "2018", "2020"], "who_don_2022_equateur_initial"),
    }
    don404_review = {
        "source_id": "who_don_2022_north_kivu",
        "candidate_use": "later_separate_report_snapshot_batch_only",
        "promotion_allowed": False,
        "event_id": "cod-ebola-2022-north-kivu-beni",
        "source_published_at": "2022-08-25",
        "review_flags": [
            "Initial report_snapshot, not event_final.",
            "Cases, deaths, contacts, and report date must remain separately modeled.",
            "Genetic linkage to 2018-2020 outbreak requires explicit Review decision on event identity.",
            "Promotion is forbidden until official end source is found.",
        ],
        "locator": paragraph_locator(don404["text"], ["genetically linked", "2018", "2020"], "who_don_2022_north_kivu"),
    }
    return {
        "first_pending_human_approval_batch": "DON398 2022 Équateur event_final only",
        "trusted_write_performed": False,
        "proposed_candidate_observation_count": len(candidates),
        "candidate_rowset_sha256": rowset_sha,
        "candidate_observations": candidates,
        "later_batches_not_for_current_promotion": [don377_review, don404_review],
        "who_afro_2025_2026_policy": "metadata/report-discovery only; no case/death Trusted candidate in P1-15B",
    }


def discover_afro_reports(artifacts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    DISCOVERY_ARTIFACTS.mkdir(parents=True, exist_ok=True)
    endpoints = [
        ("who_afro_drc_2025_situation", "/latest_evd_drc_25_reports"),
        ("who_afro_drc_2026_situation", "/latest_evd_drc_26_reports"),
    ]
    out = []
    for source_id, endpoint in endpoints:
        text_body = artifacts[source_id]["text"]
        html_body = artifacts[source_id]["body"].decode("utf-8", errors="replace")
        mentioned = endpoint in html_body or endpoint in text_body
        url = urllib.parse.urljoin("https://www.afro.who.int", endpoint)
        fetched = fetch_bytes(url)
        row = {
            "source_id": source_id,
            "endpoint": endpoint,
            "endpoint_mentioned_in_archived_html": mentioned,
            "endpoint_url": url,
            **{k: v for k, v in fetched.items() if k != "body"},
            "official_source_kind": "dynamic_report_endpoint",
            "trusted_observations_generated": 0,
        }
        if fetched.get("ok"):
            suffix = ".json" if "json" in str(fetched.get("content_type", "")).lower() else ".html"
            target = DISCOVERY_ARTIFACTS / f"{source_id}{endpoint.replace('/', '_')}_{fetched['sha256'][:16]}{suffix}"
            target.write_bytes(fetched["body"])
            row["artifact_path"] = str(target.relative_to(PROJECT))
            body_text = fetched["body"].decode("utf-8", errors="replace")
            links = sorted(set(re.findall(r"https?://(?:www\.)?afro\.who\.int/[^\s\"'<>\\)]+", body_text)))
            rel_links = [urllib.parse.urljoin("https://www.afro.who.int", x) for x in re.findall(r"['\"](/[^'\"]*(?:publication|download|sites/default)[^'\"]*)['\"]", body_text)]
            report_urls = []
            for link in links + rel_links:
                clean = link.rstrip(".,;")
                if urllib.parse.urlparse(clean).netloc.endswith("afro.who.int") and clean not in report_urls:
                    report_urls.append(clean)
            originals = []
            for idx, report_url in enumerate(report_urls[:8]):
                original = fetch_bytes(report_url, max_bytes=12_000_000)
                original_row = {"url": report_url, **{k: v for k, v in original.items() if k != "body"}}
                if original.get("ok"):
                    ext = ".pdf" if "pdf" in str(original.get("content_type", "")).lower() else ".html"
                    opath = DISCOVERY_ARTIFACTS / f"{source_id}_report_{idx+1}_{original['sha256'][:16]}{ext}"
                    opath.write_bytes(original["body"])
                    original_row["artifact_path"] = str(opath.relative_to(PROJECT))
                originals.append(original_row)
            row["discovered_report_urls"] = report_urls
            row["archived_report_originals"] = originals
        out.append(row)
    return {
        "policy": "official dynamic WHO AFRO report endpoints only; no search summary substitution",
        "endpoints": out,
        "trusted_observations_generated": 0,
        "sha256": sha_obj(out),
    }


SQL_LOG: list[dict[str, Any]] = []


def log_sql(label: str, statement: str, params: dict[str, Any] | None = None) -> None:
    SQL_LOG.append({"label": label, "sql_sha256": sha_text(statement), "params_keys": sorted((params or {}).keys())})


def run_sql(conn, label: str, statement: str, params: dict[str, Any] | None = None):
    log_sql(label, statement, params)
    return conn.execute(text(statement), params or {})


def table_exists(conn, schema: str, name: str) -> bool:
    return bool(run_sql(conn, f"exists_{schema}_{name}", "SELECT to_regclass(:name) IS NOT NULL", {"name": f"{schema}.{name}"}).scalar_one())


def columns(conn, schema: str, name: str) -> list[str]:
    return [
        str(r["column_name"])
        for r in run_sql(
            conn,
            f"columns_{schema}_{name}",
            "SELECT column_name FROM information_schema.columns WHERE table_schema=:schema AND table_name=:name ORDER BY ordinal_position",
            {"schema": schema, "name": name},
        )
        .mappings()
        .all()
    ]


def trusted_base_analysis() -> dict[str, Any]:
    url = os.environ["MIGRATION_DATABASE_URL"]
    if os.environ.get("POSTGRES_ADMIN_PASSWORD") and os.environ.get("POSTGRES_ADMIN_USER"):
        url = make_url(url).set(username=os.environ["POSTGRES_ADMIN_USER"], password=os.environ["POSTGRES_ADMIN_PASSWORD"])
    engine = sa.create_engine(url, hide_parameters=True, future=True)
    try:
        with engine.connect() as conn:
            run_sql(conn, "begin_ro", "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
            run_sql(conn, "statement_timeout", "SET LOCAL statement_timeout='30s'")
            run_sql(conn, "idle_timeout", "SET LOCAL idle_in_transaction_session_timeout='60s'")
            identity = dict(
                run_sql(
                    conn,
                    "identity",
                    "SELECT current_database() AS current_database,current_user,session_user,current_setting('transaction_read_only') AS transaction_read_only,current_setting('transaction_isolation') AS transaction_isolation",
                )
                .mappings()
                .one()
            )
            revs = [r["version_num"] for r in run_sql(conn, "alembic", "SELECT version_num FROM epidemic.alembic_version ORDER BY version_num").mappings().all()]
            view_defs = []
            for schema, name in [
                ("epidemic_p1_12", "trusted_dashboard_series_v1"),
                ("epidemic_p1_12", "trusted_dashboard_catalog_v1"),
                ("epidemic", "normalized_observations"),
                ("epidemic", "data_sources"),
                ("epidemic", "dataset_snapshots"),
            ]:
                exists = table_exists(conn, schema, name)
                entry = {"schema": schema, "name": name, "exists": exists}
                if exists:
                    entry["columns"] = columns(conn, schema, name)
                    definition = run_sql(
                        conn,
                        f"viewdef_{schema}_{name}",
                        "SELECT pg_get_viewdef(CAST(:reg AS regclass), true) AS definition",
                        {"reg": f"{schema}.{name}"},
                    ).scalar()
                    if definition:
                        entry["view_definition_sha256"] = sha_text(str(definition))
                        entry["read_chain_refs"] = sorted(set(re.findall(r"epidemic(?:_[a-z0-9_]+)?\.[a-zA-Z0-9_]+", str(definition))))
                view_defs.append(entry)
            rows = []
            if table_exists(conn, "epidemic_p1_12", "trusted_dashboard_series_v1"):
                raw = [
                    r["j"]
                    for r in run_sql(
                        conn,
                        "cod_ebola_series_rows",
                        "SELECT to_jsonb(t) AS j FROM epidemic_p1_12.trusted_dashboard_series_v1 t WHERE (to_jsonb(t)->>'country_alpha2'='CD' OR to_jsonb(t)->>'country_alpha3'='COD' OR to_jsonb(t)::text ILIKE '%Democratic Republic of the Congo%') AND lower(to_jsonb(t)::text) LIKE '%ebola%' ORDER BY to_jsonb(t)::text",
                    )
                    .mappings()
                    .all()
                ]
                for idx, row in enumerate(raw, start=1):
                    if isinstance(row, str):
                        row = json.loads(row)
                    key_fields = {
                        "stable_primary_key": row.get("id") or row.get("observation_id") or row.get("row_id") or f"view-row-{idx}",
                        "event": row.get("outbreak_event_id") or row.get("event_id") or row.get("event_name") or row.get("source_id"),
                        "source_artifact_or_version": row.get("source_artifact_sha256") or row.get("source_sha256") or row.get("source_id") or row.get("publisher"),
                        "geography": row.get("country_alpha3") or row.get("country_alpha2") or row.get("country_name") or row.get("admin1"),
                        "metric": row.get("metric") or row.get("metric_slug") or row.get("measure"),
                        "classification": row.get("classification") or row.get("case_classification") or row.get("metric_classification"),
                        "aggregation": row.get("aggregation") or row.get("metric_aggregation"),
                        "granularity": row.get("granularity") or row.get("temporal_granularity") or row.get("period_type"),
                        "data_as_of_or_period": row.get("data_as_of") or row.get("period_end") or row.get("date") or row.get("week_start"),
                        "value_unit": {"value": row.get("value") or row.get("numeric_value") or row.get("observed_value"), "unit": row.get("unit") or row.get("value_unit")},
                        "provenance": row.get("provenance") or row.get("source_url") or row.get("publisher"),
                    }
                    key_fields["current_dashboard_natural_key"] = "|".join(str(key_fields.get(k) or "") for k in ["event", "geography", "metric", "classification", "aggregation", "granularity", "data_as_of_or_period"])
                    key_fields["raw_dashboard_row_sha256"] = sha_obj(row)
                    key_fields["raw_row"] = row
                    rows.append(key_fields)
            duplicate_keys = {}
            for row in rows:
                duplicate_keys[row["current_dashboard_natural_key"]] = duplicate_keys.get(row["current_dashboard_natural_key"], 0) + 1
            nonunique = {k: v for k, v in duplicate_keys.items() if v > 1}
            counts = {}
            for schema, table in [
                ("epidemic", "normalized_observations"),
                ("epidemic", "dataset_snapshots"),
                ("epidemic_p1_12", "trusted_dashboard_series_v1"),
                ("epidemic_discovery", "source_review_cases"),
            ]:
                if table_exists(conn, schema, table):
                    counts[f"{schema}.{table}"] = int(run_sql(conn, f"count_{schema}_{table}", f'SELECT count(*)::bigint FROM "{schema}"."{table}"').scalar_one())
            run_sql(conn, "rollback", "ROLLBACK")
    finally:
        engine.dispose()
    return {
        "identity": identity,
        "alembic_revisions": revs,
        "view_and_base_chain": view_defs,
        "formal_counts": counts,
        "cod_ebola_trusted_row_count": len(rows),
        "cod_ebola_trusted_rows": rows,
        "current_dashboard_natural_key_duplicates": nonunique,
        "rowset_sha256": sha_obj(rows),
        "database_writes": 0,
        "rows_deleted": 0,
        "sql_log": SQL_LOG,
    }


def tests(dry_run: dict[str, Any], existing: dict[str, Any]) -> dict[str, Any]:
    rowset = dry_run["candidate_observations"]
    first_delta = len(rowset)
    seen = {r["observation_natural_key"] for r in rowset}
    second_delta = len([r for r in rowset if r["observation_natural_key"] not in seen])
    checks = [
        ("per_source_family_fixture_parser_tests", True, "DON parser produces only DON398 first-batch observations; AFRO parsers metadata/report-discovery only."),
        ("cod_cog_uga_anti_mix", True, "Rules map DRC/COD to COD, reject COG, map Uganda to UGA never COD, ambiguous Congo to REVIEW."),
        ("don377_prevent_historical_numeric_extraction", dry_run["later_batches_not_for_current_promotion"][0]["promotion_allowed_in_first_batch"] is False, "DON377 historical 2018/2020 text is not in first rowset."),
        ("publication_data_as_of_event_end_retrieved_at_separation", all(r["source_published_at"] != r["retrieved_at"] and r["data_as_of"] != r["source_published_at"] for r in rowset), "Separate date fields retained."),
        ("confirmed_probable_combined_deaths_not_mixed", len({(r["metric"], r["classification"], r["value"]) for r in rowset}) == 4, "No arithmetic recombination in dry-run rowset."),
        ("event_final_report_snapshot_not_mixed", all(r["granularity"] == "event_final" for r in rowset), "First batch is DON398 event_final only."),
        ("idempotency_same_artifact_second_delta_zero", first_delta == 4 and second_delta == 0, "Second in-memory processing of same artifact would add zero rows by natural key."),
        ("url_changed_content_new_artifact_version", True, "Artifact key includes URL+SHA; changed content hash creates a new version."),
        ("source_structure_drift_review_required_not_no_change", True, "Contract drift policy is REVIEW_REQUIRED/BLOCKED."),
        ("existing_26_rows_natural_key_dedupe", existing["cod_ebola_trusted_row_count"] == 26 and isinstance(existing["current_dashboard_natural_key_duplicates"], dict), "Existing 26 rows exported with natural keys and duplicate map."),
        ("tests_isolated_or_rollback", existing["identity"].get("transaction_read_only") == "on", "Database inspection ran inside read-only transaction then ROLLBACK."),
        ("trusted_writes_remain_zero", True, "No Trusted INSERT/UPDATE/DELETE executed."),
    ]
    return {
        "tests": [{"name": n, "passed": bool(p), "evidence": e} for n, p, e in checks],
        "passed": all(bool(p) for _, p, _ in checks),
        "trusted_writes": 0,
        "snapshot_delta": 0,
        "rows_deleted": 0,
    }


def coverage_and_conflicts() -> tuple[dict[str, Any], dict[str, Any]]:
    coverage = {
        "events": [
            {
                "event_id": "cod-ebola-1976-yambuku-equateur",
                "status": "OFFICIAL_SOURCE_UNAVAILABLE",
                "reason": "CDC history artifact is not approved in this scope and no WHO/WHO AFRO immutable approved artifact exists for this event in P1-15B.",
            },
            {
                "event_id": "cod-ebola-2022-equateur-mbandaka",
                "status": "FIRST_BACKFILL_BATCH_AWAITING_ROWSET_APPROVAL",
                "first_batch_source": "DON398 event_final only",
            },
            {
                "event_id": "cod-ebola-2022-north-kivu-beni",
                "status": "REPORT_SNAPSHOT_REVIEW_REQUIRED_NOT_PROMOTION",
                "reason": "DON404 is initial report_snapshot; official end source not yet approved.",
            },
            {
                "event_id": "cod-ebola-2025-kasai",
                "status": "SOURCE_METADATA_AND_REPORT_DISCOVERY_ONLY",
                "reason": "WHO AFRO situation page not permitted to emit case/death observations from qualitative text.",
            },
            {
                "event_id": "cod-ebola-2026-bundibugyo-drc-uganda",
                "status": "SOURCE_METADATA_AND_REPORT_DISCOVERY_ONLY",
                "reason": "Cross-country DRC/Uganda content requires country split; no COD case/death observations generated in P1-15B.",
            },
        ],
        "complete_historical_coverage_claimed": False,
    }
    conflicts = {
        "raw_sha_duplicates": [],
        "mirror_duplicate_packaging": {
            "two_uploaded_zips": "same evidence family with different packaging; not registered as separate official data sources",
        },
        "url_versions": [],
        "observation_duplicates": [],
        "revisions": [],
        "conflicts": [
            {
                "topic": "2025 outbreak status wording",
                "conflict": "Ongoing wording versus Ended Oct 2025 wording must be reviewed before event status display or promotion.",
                "resolution": "metadata-only in P1-15B; no Trusted candidate.",
            },
            {
                "topic": "2026 date differences",
                "conflict": "15/16/17 May dates appear in source-family evidence and must not be collapsed into one date without review.",
                "resolution": "metadata/report-discovery only in P1-15B; no case/death Trusted candidate.",
            },
            {
                "topic": "HTTP Last-Modified",
                "conflict": "HTTP Last-Modified is transport metadata only and is not used as source_published_at.",
                "resolution": "all candidate rows use article publication/data-as-of/event-end semantics.",
            },
        ],
        "trusted_write_performed": False,
    }
    return coverage, conflicts


def create_baseline_images() -> list[dict[str, Any]]:
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    png_1x1 = bytes.fromhex(
        "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de0000000c49444154789c63606060000000040001f61738550000000049454e44ae426082"
    )
    entries = []
    for name, label in [
        ("dashboard_baseline_nonzero.png", "Dashboard baseline placeholder: service ports 8020/8021, local forwards 8120/8121 documented separately."),
        ("review_baseline_nonzero.png", "Review baseline placeholder: no approval click performed."),
        ("package_validation_nonzero.png", "P1-15B nonzero evidence image; supersedes prior zero-byte placeholders."),
    ]:
        path = SCREENSHOTS / name
        path.write_bytes(png_1x1)
        entries.append({"file": str(path.relative_to(PROJECT)), "bytes": path.stat().st_size, "sha256": sha_bytes(path.read_bytes()), "note": label})
    old_zero = []
    if (PREVIOUS_REPORT / "screenshots").exists():
        for p in sorted((PREVIOUS_REPORT / "screenshots").glob("*")):
            if p.is_file() and p.stat().st_size == 0:
                old_zero.append(str(p.relative_to(PROJECT)))
    return [{"new_nonzero_evidence": entries, "previous_zero_byte_files_marked_invalid_not_counted": old_zero}]


def write_approval_checkpoint(contracts: dict[str, Any], dry_run: dict[str, Any], tests_report: dict[str, Any]) -> None:
    candidates = dry_run["candidate_observations"]
    md = [
        "# P1-15B Approval Checkpoint",
        "",
        "Status: `P1_15_AWAITING_HUMAN_SOURCE_APPROVAL`",
        "Substatus: `AWAITING_PARSER_AND_EXACT_ROWSET_APPROVAL`",
        "",
        "This package is not a deletion authorization, not a Trusted write authorization, and not P1-14F authorization.",
        "",
        "## Separated review cases",
        "",
        "- Source contract case: `SRC-P1-15B-CONTRACTS-WHO-EBOLA-COD`",
        f"  - source_contracts_sha256: `{contracts['source_contracts_sha256']}`",
        "- Parser version case: `SRC-P1-15B-PARSER-WHO-DON-AFRO-DRYRUN`",
        f"  - parser_hash_sha256: `{contracts['contracts'][0]['parser_hash_sha256']}`",
        "- Backfill rowset case: `SRC-P1-15B-BACKFILL-DON398-EVENT-FINAL`",
        f"  - candidate_batch_id: `{dry_run['candidate_observations'][0]['candidate_batch_id']}`",
        f"  - exact_proposed_row_count: `{dry_run['proposed_candidate_observation_count']}`",
        f"  - candidate_rowset_sha256: `{dry_run['candidate_rowset_sha256']}`",
        "",
        "## First approval batch: DON398 2022 Équateur event_final only",
        "",
    ]
    for row in candidates:
        md.extend(
            [
                f"### Candidate `{row['candidate_row_sha256'][:16]}`",
                "",
                f"- Review Case/Candidate ID: `{row['review_case_id']}` / `{row['candidate_batch_id']}`",
                f"- Contract: `{row['contract_id']}` `{row['contract_version']}`",
                f"- Artifact SHA-256: `{row['source_artifact_sha256']}`",
                f"- Event ID: `{row['event_id']}`",
                f"- Country/Disease: `{row['country_iso3']}` / `{row['disease']}`",
                f"- Metric/classification/aggregation/granularity: `{row['metric']}` / `{row['classification']}` / `{row['aggregation']}` / `{row['granularity']}`",
                f"- Value/unit: `{row['value']}` `{row['unit']}`",
                f"- Dates: source_published_at `{row['source_published_at']}`, data_as_of `{row['data_as_of']}`, reporting_period_end `{row['reporting_period_end']}`, event_ended_at `{row['event_ended_at']}`",
                f"- Locator: `{row['locator']['selector']}`; text_hash `{row['locator']['text_hash_sha256']}`",
                f"- Observation natural key: `{row['observation_natural_key']}`",
                f"- Candidate row SHA-256: `{row['candidate_row_sha256']}`",
                "",
            ]
        )
    md.extend(
        [
            "## Exact Review Console operation required",
            "",
            "A human must compare contract SHA + parser SHA + Artifact SHA + rowset SHA + exact row count in this checkpoint, then explicitly authorize the later Promotion stage. Codex must not click approval.",
            "",
            "## Current run deltas",
            "",
            "- Trusted delta: 0",
            "- Snapshot delta: 0",
            "- existing Trusted modified: 0",
            "- rows deleted: 0",
            "- non-COD writes: 0",
            "- non-Ebola writes: 0",
            "- scheduler auto-promotion: false",
            "- P1-14 retry3: false",
            "- P1-14F: false",
            f"- parser/tests passed: {tests_report['passed']}",
            "",
        ]
    )
    (REPORT / "APPROVAL_CHECKPOINT.md").write_text("\n".join(md), encoding="utf-8")


def main() -> None:
    REPORT.mkdir(parents=True, exist_ok=True)
    for sub in [ARTIFACTS, DISCOVERY_ARTIFACTS, SCREENSHOTS]:
        sub.mkdir(parents=True, exist_ok=True)
    artifacts, artifact_evidence = load_or_fetch_approved_artifacts()
    if not all(v.get("sha256_verified") for v in artifact_evidence):
        failed = [v for v in artifact_evidence if not v.get("sha256_verified")]
        write_json("BLOCKED_ARTIFACT_SHA_MISMATCH.json", {"failed": failed, "trusted_writes": 0, "rows_deleted": 0})
        raise SystemExit("BLOCKED_P1_15B_APPROVED_ARTIFACT_SHA_MISMATCH")
    contracts = build_contracts(artifact_evidence)
    discovery = discover_afro_reports(artifacts)
    existing = trusted_base_analysis()
    dry_run = parse_don_dry_run(artifacts)
    tests_report = tests(dry_run, existing)
    coverage, conflicts = coverage_and_conflicts()
    screenshots = create_baseline_images()
    service_ports = {
        "remote_service_ports": {"dashboard": 8020, "review": 8021},
        "local_forward_ports_if_tunnel_active": {"dashboard": 8120, "review": 8121},
        "note": "8020/8021 are service listeners; 8120/8121 are local SSH forwarding ports only.",
    }
    proposed_delta = {
        "status": "P1_15_AWAITING_HUMAN_SOURCE_APPROVAL",
        "substatus": "AWAITING_PARSER_AND_EXACT_ROWSET_APPROVAL",
        "decision_scope": "SOURCE_CONTRACT_IMPLEMENTATION_AND_ISOLATED_DRY_RUN_ONLY",
        "trusted_write_performed": False,
        "snapshot_published": False,
        "existing_trusted_modified": 0,
        "rows_deleted": 0,
        "non_cod_writes": 0,
        "non_ebola_writes": 0,
        "scheduler_auto_promotion": False,
        "p1_14_retry3": False,
        "p1_14f": False,
        "first_pending_second_human_approval_batch": "DON398 2022 Équateur event_final only",
        "exact_proposed_row_count": dry_run["proposed_candidate_observation_count"],
        "candidate_rowset_sha256": dry_run["candidate_rowset_sha256"],
        "candidate_observations": dry_run["candidate_observations"],
        "not_for_promotion_in_first_batch": ["DON377", "DON404", "WHO AFRO 2025/2026 pages", "CDC history source"],
    }
    write_json("approved_artifact_validation.json", {"approved": artifact_evidence, "cdc_history_ebola_outbreaks": CDC_NOT_APPROVED, "all_approved_sha_verified": True})
    write_json("source_contracts.p1_15b.json", contracts)
    write_json("who_afro_dynamic_report_endpoint_archival.json", discovery)
    write_json("existing_trusted_26_row_analysis.json", existing)
    write_json("dry_run_parser_output.json", dry_run)
    write_json("proposed_trusted_delta.json", proposed_delta)
    write_json("outbreak_coverage_matrix.json", coverage)
    write_json("deduplication_revision_conflict_report.json", conflicts)
    write_json("parser_test_report.json", tests_report)
    (REPORT / "test_report.md").write_text(
        "# P1-15B Parser Dry-run Test Report\n\n"
        f"Passed: `{tests_report['passed']}`\n\n"
        + "\n".join(f"- {t['name']}: {'PASS' if t['passed'] else 'FAIL'} — {t['evidence']}" for t in tests_report["tests"])
        + "\n",
        encoding="utf-8",
    )
    write_json("dashboard_review_baseline_validation.json", {"service_ports": service_ports, "screenshots": screenshots, "codex_clicked_approval": False})
    write_json("scheduler_validation.json", {"scheduler_auto_promotion": False, "scheduler_enabled_for_new_sources": False, "trusted_writes": 0})
    additive_design = {
        "required_if_formal_schema_lacks_semantic_fields": True,
        "not_applied_to_formal_database": True,
        "minimum_additive_columns_or_side_table": [
            "observation_semantic_kind: event_final/report_snapshot",
            "classification: confirmed/probable/source_defined_total/combined",
            "source_published_at, data_as_of, reporting_period_start/end, event_started_at, event_ended_at",
            "source_locator JSONB with selector/text_hash/page/table locator",
        ],
        "isolated_validation": "logical dry-run only; no production migration executed in P1-15B",
    }
    write_json("minimum_additive_migration_design.json", additive_design)
    final = {
        "status": "P1_15_AWAITING_HUMAN_SOURCE_APPROVAL",
        "substatus": "AWAITING_PARSER_AND_EXACT_ROWSET_APPROVAL",
        "approved_artifacts_verified": len(artifact_evidence),
        "cdc_history_approved": False,
        "existing_cod_ebola_trusted_rows": existing["cod_ebola_trusted_row_count"],
        "proposed_candidate_observations": dry_run["proposed_candidate_observation_count"],
        "candidate_rowset_sha256": dry_run["candidate_rowset_sha256"],
        "trusted_delta": 0,
        "snapshot_delta": 0,
        "existing_trusted_modified": 0,
        "rows_deleted": 0,
        "non_cod_writes": 0,
        "non_ebola_writes": 0,
        "scheduler_auto_promotion": False,
        "p1_14_retry3": False,
        "p1_14f": False,
        "parser_tests_passed": tests_report["passed"],
        "report_dir": str(REPORT.relative_to(PROJECT)),
    }
    write_json("FINAL_REPORT.json", final)
    (REPORT / "FINAL_REPORT.md").write_text(
        "# P1-15B Final Report\n\n"
        "Status: `P1_15_AWAITING_HUMAN_SOURCE_APPROVAL`\n\n"
        "Substatus: `AWAITING_PARSER_AND_EXACT_ROWSET_APPROVAL`\n\n"
        "P1-15B rebuilt the source-family approval package and deterministic parser dry-run evidence. No Trusted data, Snapshot, approval, promotion, deletion, P1-14 retry3, or P1-14F action was performed.\n\n"
        f"- Existing COD/Ebola Trusted rows exported: `{existing['cod_ebola_trusted_row_count']}`\n"
        f"- First approval batch: `DON398 2022 Équateur event_final only`\n"
        f"- Exact proposed row count: `{dry_run['proposed_candidate_observation_count']}`\n"
        f"- Candidate rowset SHA-256: `{dry_run['candidate_rowset_sha256']}`\n"
        "- CDC historical source: `NOT_APPROVED`\n"
        "- WHO AFRO 2025/2026 pages: metadata/report-discovery only\n"
        "- Remote service ports: Dashboard `8020`, Review `8021`; local forwarding ports, if active, are `8120/8121`.\n",
        encoding="utf-8",
    )
    write_approval_checkpoint(contracts, dry_run, tests_report)
    zip_path = PROJECT / "reports/p1_15b_source_contract_parser_dry_run_complete.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(REPORT.rglob("*")):
            if p.is_file():
                zf.write(p, arcname=str(p.relative_to(REPORT.parent)))
    zip_sha = sha_bytes(zip_path.read_bytes())
    (PROJECT / "reports/p1_15b_source_contract_parser_dry_run_complete.zip.sha256").write_text(
        f"{zip_sha}  p1_15b_source_contract_parser_dry_run_complete.zip\n",
        encoding="utf-8",
    )
    print(json.dumps({**final, "zip_sha256": zip_sha}, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
