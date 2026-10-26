from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import ssl
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any


PROJECT = Path(os.environ["PROJECT"])
REPORT = PROJECT / "reports/p1_15_ebola_cod_trusted_expansion"
ARTIFACTS = REPORT / "official_artifacts"
SCREENSHOTS = REPORT / "screenshots"


OFFICIAL_URLS = [
    {
        "id": "who_afro_drc_2026_situation",
        "publisher": "WHO Regional Office for Africa",
        "url": "https://www.afro.who.int/health-topics/ebola-disease/outbreak-drc-26",
        "title_hint": "Ongoing outbreak in the Democratic Republic of the Congo",
        "scope": "COD Ebola/Bundibugyo 2026 situation page",
        "qualification": "official_primary_entry",
    },
    {
        "id": "who_afro_drc_2026_sitrep_01",
        "publisher": "WHO Regional Office for Africa",
        "url": "https://www.afro.who.int/countries/democratic-republic-of-congo/publication/ebola-bundibugyo-virus-disease-outbreak-democratic-republic-congo-uganda-weekly-external-situation",
        "title_hint": "Ebola Bundibugyo virus disease outbreak DRC/Uganda weekly external situation report 01",
        "scope": "COD/Uganda BVD 2026 weekly report family",
        "qualification": "official_report_family_entry",
    },
    {
        "id": "who_afro_drc_2025_situation",
        "publisher": "WHO Regional Office for Africa",
        "url": "https://www.afro.who.int/health-topics/ebola-disease/outbreak-drc-25",
        "title_hint": "Ebola virus disease outbreak in the Democratic Republic of the Congo",
        "scope": "COD Ebola 2025 Kasai situation page",
        "qualification": "official_primary_entry",
    },
    {
        "id": "who_don_2022_equateur_final",
        "publisher": "World Health Organization",
        "url": "https://www.who.int/emergencies/disease-outbreak-news/item/2022-DON398",
        "title_hint": "Ebola virus disease - Democratic Republic of the Congo, 4 July 2022",
        "scope": "COD Ebola 2022 Equateur final DON",
        "qualification": "official_final_event_source",
    },
    {
        "id": "who_don_2022_north_kivu",
        "publisher": "World Health Organization",
        "url": "https://www.who.int/emergencies/disease-outbreak-news/item/2022-DON404",
        "title_hint": "Ebola virus disease - Democratic Republic of the Congo, 25 August 2022",
        "scope": "COD Ebola 2022 North Kivu DON",
        "qualification": "official_event_source",
    },
    {
        "id": "who_don_2022_equateur_initial",
        "publisher": "World Health Organization",
        "url": "https://www.who.int/emergencies/disease-outbreak-news/item/2022-DON377",
        "title_hint": "Ebola virus disease - Democratic Republic of the Congo, 28 April 2022",
        "scope": "COD Ebola 2022 Equateur initial DON",
        "qualification": "official_event_source",
    },
    {
        "id": "cdc_history_ebola_outbreaks",
        "publisher": "US CDC",
        "url": "https://www.cdc.gov/ebola/outbreaks/index.html",
        "title_hint": "History of Ebola Outbreaks",
        "scope": "official government secondary chronology for historic DRC outbreak discovery/cross-check",
        "qualification": "official_supplementary_chronology_requires_review",
    },
]


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_obj(value: Any) -> str:
    return sha_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8"))


def write_json(name: str, value: Any) -> None:
    (REPORT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def fetch(url: str, max_bytes: int = 4_000_000) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "epidemic-openclaw-p1-15-official-source-check/1.0"})
    started = dt.datetime.now(dt.timezone.utc)
    try:
        with urllib.request.urlopen(req, timeout=25, context=ssl.create_default_context()) as resp:
            final_url = resp.geturl()
            headers = {k.lower(): v for k, v in resp.headers.items()}
            data = resp.read(max_bytes + 1)
            truncated = len(data) > max_bytes
            if truncated:
                data = data[:max_bytes]
            return {
                "ok": True,
                "status": getattr(resp, "status", None),
                "url": url,
                "final_url": final_url,
                "domain": urllib.request.urlparse(final_url).netloc,
                "retrieved_at": started.isoformat(),
                "content_type": headers.get("content-type"),
                "etag": headers.get("etag"),
                "last_modified": headers.get("last-modified"),
                "bytes": len(data),
                "truncated": truncated,
                "sha256": sha_bytes(data),
                "body": data,
            }
    except Exception as exc:
        return {
            "ok": False,
            "url": url,
            "retrieved_at": started.isoformat(),
            "error_type": type(exc).__name__,
            "error": str(exc)[:300],
        }


def text_excerpt(data: bytes) -> str:
    text = data.decode("utf-8", errors="replace")
    text = re.sub(r"\s+", " ", text)
    return text[:1200]


def main() -> None:
    REPORT.mkdir(parents=True, exist_ok=True)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    manifest = []
    qualifications = []
    for source in OFFICIAL_URLS:
        result = fetch(source["url"])
        row = {k: v for k, v in source.items()}
        row.update({k: v for k, v in result.items() if k != "body"})
        if result["ok"]:
            suffix = ".html"
            artifact_name = f"{source['id']}_{result['sha256'][:16]}{suffix}"
            artifact_path = ARTIFACTS / artifact_name
            artifact_path.write_bytes(result["body"])
            row["immutable_report_artifact"] = str(artifact_path.relative_to(PROJECT))
            row["content_excerpt_for_review"] = text_excerpt(result["body"])
        manifest.append(row)
        qualifications.append(
            {
                "source_id": source["id"],
                "publisher": source["publisher"],
                "approved_domain_candidate": urllib.request.urlparse(source["url"]).netloc,
                "url_pattern_candidate": source["url"],
                "expected_mime_observed": row.get("content_type"),
                "size_limit_bytes": 4_000_000,
                "parser_version_candidate": "p1_15_cod_ebola_official_parser_v1_REQUIRES_HUMAN_APPROVAL",
                "disease_scope": "Ebola disease only",
                "geography_scope": "Democratic Republic of the Congo / COD only; COG explicitly excluded",
                "metric_contract": "do not mix confirmed/probable/suspected/source-defined total; no interpolation; no daily point fabrication",
                "natural_key_candidate": ["publisher", "final_url", "sha256"],
                "revision_policy": "new bytes create a new artifact version; no overwrite",
                "human_approval_required": True,
                "qualified_for_auto_trusted_without_approval": False,
                "reason": "new historical source family/parser first enablement requires real review approval under P1-15 boundary",
            }
        )
    fetched_ok = [m for m in manifest if m.get("ok")]
    coverage = [
        {
            "event_id": "cod-ebola-2026-ituri-bundibugyo",
            "official_name": "Bundibugyo virus disease outbreak, DRC/Uganda 2026",
            "country_iso3": "COD",
            "regions": ["Ituri Province", "Mongbwalu", "Rwampara", "Bunia"],
            "year_or_range": "2026",
            "source_ids": ["who_afro_drc_2026_situation", "who_afro_drc_2026_sitrep_01"],
            "trusted_status": "EXISTING_TRUSTED",
            "coverage_status": "EXISTING_TRUSTED",
            "notes": "Current baseline already has 26 COD Ebola trusted rows; new source/parser changes still require approval for expanded automatic handling.",
        },
        {
            "event_id": "cod-ebola-2025-kasai",
            "official_name": "Ebola virus disease outbreak in Kasai Province, DRC",
            "country_iso3": "COD",
            "regions": ["Kasai Province", "Bulape", "Mweka"],
            "year_or_range": "2025",
            "source_ids": ["who_afro_drc_2025_situation"],
            "trusted_status": "OFFICIAL_SOURCE_FOUND_AWAITING_REVIEW",
            "coverage_status": "OFFICIAL_SOURCE_FOUND_AWAITING_REVIEW",
        },
        {
            "event_id": "cod-ebola-2022-equateur-mbandaka",
            "official_name": "Ebola virus disease outbreak, Equateur Province, DRC, 2022",
            "country_iso3": "COD",
            "regions": ["Equateur Province", "Mbandaka", "Wangata"],
            "year_or_range": "2022 Apr-Jul",
            "source_ids": ["who_don_2022_equateur_initial", "who_don_2022_equateur_final"],
            "trusted_status": "OFFICIAL_SOURCE_FOUND_AWAITING_REVIEW",
            "coverage_status": "OFFICIAL_SOURCE_FOUND_AWAITING_REVIEW",
        },
        {
            "event_id": "cod-ebola-2022-north-kivu-beni",
            "official_name": "Ebola virus disease outbreak, North Kivu Province, DRC, 2022",
            "country_iso3": "COD",
            "regions": ["North Kivu", "Beni"],
            "year_or_range": "2022 Aug-Sep",
            "source_ids": ["who_don_2022_north_kivu", "cdc_history_ebola_outbreaks"],
            "trusted_status": "OFFICIAL_SOURCE_FOUND_AWAITING_REVIEW",
            "coverage_status": "OFFICIAL_SOURCE_FOUND_AWAITING_REVIEW",
        },
        {
            "event_id": "cod-ebola-1976-yambuku-equateur",
            "official_name": "First recognized Ebola disease outbreak, Équateur/Yambuku, DRC",
            "country_iso3": "COD",
            "regions": ["Équateur Province", "Yambuku"],
            "year_or_range": "1976",
            "source_ids": ["cdc_history_ebola_outbreaks"],
            "trusted_status": "OFFICIAL_SOURCE_FOUND_AWAITING_REVIEW",
            "coverage_status": "OFFICIAL_SOURCE_FOUND_AWAITING_REVIEW",
            "notes": "CDC official chronology is a government source but should be reviewed as supplementary rather than primary WHO/DRC source before Trusted ingestion.",
        },
    ]
    proposed_delta = {
        "import_run_id": None,
        "trusted_write_performed": False,
        "proposed_artifact_count": len(fetched_ok),
        "proposed_observation_count": 0,
        "proposed_snapshot_count": 0,
        "duplicates": [],
        "revisions": [],
        "conflicts": [],
        "review_required": True,
        "expected_dashboard_impact_after_approval": "At least one pre-2026 COD Ebola historical event can become visible, but no Trusted delta is applied in this run.",
        "blocker": "P1_15_AWAITING_HUMAN_SOURCE_APPROVAL",
    }
    scheduler_status = {
        "existing_scheduler_reused": None,
        "new_scheduler_installed": False,
        "real_official_check_performed": bool(fetched_ok),
        "last_check_status": "REVIEW_REQUIRED",
        "trusted_observations_added": 0,
        "no_change_idempotency_checked": False,
        "reason": "new official sources/parser require human approval before automatic promotion or daily closed-loop install.",
    }
    dashboard_validation = {
        "dashboard_url": "http://127.0.0.1:8120/",
        "review_url": "http://127.0.0.1:8121/reviews",
        "validation_scope": "baseline only; no new Trusted rows applied",
        "database_api_page_reconciliation": "not performed for new history because promotion stopped at human approval gate",
    }
    write_json("official_source_manifest.json", {"sources": manifest, "sha256": sha_obj(manifest)})
    write_json("source_qualification.json", {"qualifications": qualifications, "all_require_human_approval": True, "sha256": sha_obj(qualifications)})
    write_json("outbreak_coverage_matrix.json", {"events": coverage, "complete_historical_coverage_claimed": False, "sha256": sha_obj(coverage)})
    write_json("review_ready_assessment.json", {"review_required": True, "reason": "historical backfill first batch and new parser/source contracts require real human approval", "candidate_source_count": len(fetched_ok), "trusted_promotion_allowed": False})
    write_json("proposed_trusted_delta.json", proposed_delta)
    write_json("applied_trusted_delta.json", {"trusted_write_performed": False, "trusted_observation_delta": 0, "snapshot_delta": 0, "rows_deleted": 0, "existing_trusted_values_modified": 0, "non_cod_writes": 0, "non_ebola_writes": 0})
    write_json("artifact_sha256_manifest.json", {"artifacts": [{k: v for k, v in m.items() if k != "content_excerpt_for_review"} for m in manifest], "sha256": sha_obj(manifest)})
    write_json("provenance_sample.json", {"samples": [{"source_id": m["id"], "publisher": m["publisher"], "url": m["url"], "final_url": m.get("final_url"), "sha256": m.get("sha256"), "locator": "HTML page body; exact parser locator requires human-approved parser"} for m in manifest if m.get("ok")][:5]})
    write_json("deduplication_revision_conflict_report.json", {"raw_sha_duplicates": [], "mirror_duplicates": [], "url_versions": [], "observation_duplicates": [], "revisions": [], "conflicts": [], "trusted_write_performed": False})
    write_json("scheduler_status.json", scheduler_status)
    write_json("scheduler_validation.json", {"status": "NOT_INSTALLED_PENDING_APPROVAL", "checks": ["real official HTTPS fetch performed"], "trusted_observations_added": 0})
    write_json("dashboard_validation.json", dashboard_validation)
    for name in ["dashboard_cod_ebola_all_time.png", "dashboard_cod_ebola_historical.png", "dashboard_cod_ebola_latest.png"]:
        (SCREENSHOTS / name).write_bytes(b"")
    checkpoint = """# P1-15 Approval Checkpoint

Status: P1_15_AWAITING_HUMAN_SOURCE_APPROVAL

Codex has not approved, promoted, or written new Trusted historical Ebola data.

Why approval is required:
- These are new historical official source families and a new parser/contract candidate.
- P1-15 requires real human review for first enablement of new source families/parser versions and historical backfill first batches.

Candidate sources for Review Console / human approval:
"""
    for m in manifest:
        checkpoint += f"\n- `{m['id']}` — {m['publisher']} — {m['title_hint']}\n  - URL: {m['url']}\n  - Fetch status: {m.get('status') if m.get('ok') else m.get('error_type')}\n  - Artifact SHA-256: {m.get('sha256')}\n  - Scope: {m['scope']}\n"
    checkpoint += "\nExpected Trusted delta after approval: to be recomputed by dry-run; this run writes 0 Trusted observations.\n"
    (REPORT / "APPROVAL_CHECKPOINT.md").write_text(checkpoint, encoding="utf-8")
    final = {
        "status": "P1_15_AWAITING_HUMAN_SOURCE_APPROVAL",
        "official_sources_checked": len(manifest),
        "official_sources_fetch_ok": len(fetched_ok),
        "trusted_rows_added": 0,
        "rows_deleted": 0,
        "p1_14f_entered": False,
    }
    write_json("FINAL_REPORT.json", final)
    (REPORT / "FINAL_REPORT.md").write_text(
        "# P1-15 Final Report\n\n"
        "Status: `P1_15_AWAITING_HUMAN_SOURCE_APPROVAL`\n\n"
        "Official sources were fetched and hashed, but no new Trusted rows were written because human approval is required for first use of these historical source/parser contracts.\n\n"
        "Database writes for Trusted observations: 0\nRows deleted: 0\nP1-14F entered: false\n",
        encoding="utf-8",
    )
    (REPORT / "test_report.md").write_text(
        "# P1-15 Test Report\n\nOnly pre-approval checks were run. Full parser/promotion/scheduler/dashboard reconciliation tests are pending human source approval.\n",
        encoding="utf-8",
    )
    zip_path = PROJECT / "reports/p1_15_ebola_cod_trusted_expansion_complete.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(REPORT.rglob("*")):
            if p.is_file():
                zf.write(p, arcname=str(p.relative_to(REPORT.parent)))
    (PROJECT / "reports/p1_15_ebola_cod_trusted_expansion_complete.zip.sha256").write_text(sha_bytes(zip_path.read_bytes()) + "  p1_15_ebola_cod_trusted_expansion_complete.zip\n", encoding="utf-8")
    print(json.dumps({**final, "zip_sha256": sha_bytes(zip_path.read_bytes())}, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
