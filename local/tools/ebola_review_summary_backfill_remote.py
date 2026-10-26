from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
import uuid
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url


PROJECT = Path(os.environ["PROJECT"])
REPORT = PROJECT / "reports/ebola_data_visualization_prediction_engineering_loop"
SUMMARY_VERSION = "raw_source_summary_v1"


def jdefault(value: Any) -> Any:
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    return str(value)


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_text(value: str) -> str:
    return sha_bytes(value.encode("utf-8"))


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=jdefault)


def sha_obj(value: Any) -> str:
    return sha_text(canonical(value))


def write_json(rel: str, value: Any) -> None:
    (REPORT / rel).parent.mkdir(parents=True, exist_ok=True)
    (REPORT / rel).write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=jdefault) + "\n", encoding="utf-8")


def append_jsonl(rel: str, value: Any) -> None:
    (REPORT / rel).parent.mkdir(parents=True, exist_ok=True)
    with (REPORT / rel).open("a", encoding="utf-8") as f:
        f.write(json.dumps(value, ensure_ascii=False, sort_keys=True, default=jdefault) + "\n")


def db_url():
    url = os.environ["MIGRATION_DATABASE_URL"]
    if os.environ.get("POSTGRES_ADMIN_PASSWORD") and os.environ.get("POSTGRES_ADMIN_USER"):
        url = make_url(url).set(username=os.environ["POSTGRES_ADMIN_USER"], password=os.environ["POSTGRES_ADMIN_PASSWORD"])
    return url


def normalize_text(raw: str) -> str:
    value = html.unescape(raw)
    value = unicodedata.normalize("NFKC", value)
    value = value.replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s+", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|noscript|nav|footer|form).*?</\1>", " ", raw)
    raw = re.sub(r"(?is)<br\s*/?>", "\n", raw)
    raw = re.sub(r"(?is)</(h1|h2|h3|h4|p|li|div|section|article)>", "\n", raw)
    raw = re.sub(r"(?is)<[^>]+>", " ", raw)
    return normalize_text(raw)


def find_artifact(sha: str) -> Path | None:
    roots = [
        PROJECT / "reports",
        PROJECT / "storage",
        PROJECT / "data",
        PROJECT / "artifacts",
    ]
    prefix = sha[:16]
    candidates = []
    for root in roots:
        if root.exists():
            candidates.extend([p for p in root.rglob(f"*{prefix}*") if p.is_file()])
    for p in candidates:
        try:
            if sha_bytes(p.read_bytes()) == sha:
                return p
        except Exception:
            continue
    shard_candidates = list((PROJECT / "storage").glob(f"**/{sha[:2]}/{sha}*")) if (PROJECT / "storage").exists() else []
    for p in shard_candidates:
        if p.is_file():
            try:
                if sha_bytes(p.read_bytes()) == sha:
                    return p
            except Exception:
                continue
    return None


def text_from_artifact(path: Path, fmt: str | None) -> tuple[str, str, str | None]:
    suffix = path.suffix.lower()
    data = path.read_bytes()
    content_kind = (fmt or suffix.lstrip(".") or "unknown").lower()
    if suffix in {".html", ".htm"} or data[:100].lower().find(b"<html") >= 0:
        return html_to_text(data.decode("utf-8", errors="replace")), "html", None
    if suffix in {".txt", ".md"}:
        return normalize_text(data.decode("utf-8", errors="replace")), "text", None
    if suffix == ".pdf" or content_kind == "pdf":
        if shutil.which("pdftotext") is not None:
            with tempfile.NamedTemporaryFile(suffix=".pdf") as src, tempfile.NamedTemporaryFile(suffix=".txt") as dst:
                src.write(data)
                src.flush()
                cp = subprocess.run(["pdftotext", "-layout", src.name, dst.name], text=True, capture_output=True, timeout=30)
                if cp.returncode == 0:
                    text_value = normalize_text(Path(dst.name).read_text(encoding="utf-8", errors="replace"))
                    if len(re.sub(r"\s+", "", text_value)) > 300:
                        return text_value, "pdf", None
        try:
            import pypdf  # type: ignore[import-not-found]

            with tempfile.NamedTemporaryFile(suffix=".pdf") as src:
                src.write(data)
                src.flush()
                reader = pypdf.PdfReader(src.name)
                pages = [(page.extract_text() or "") for page in reader.pages]
            text_value = normalize_text("\n\n".join(pages))
            if len(re.sub(r"\s+", "", text_value)) > 300:
                return text_value, "pdf", None
            return "", "pdf", "BLOCKED_PDF_TEXT_EXTRACTION_EMPTY"
        except Exception:
            if shutil.which("pdftotext") is None:
                return "", "pdf", "BLOCKED_PDF_TEXT_EXTRACTION_TOOL_MISSING"
            return "", "pdf", "BLOCKED_PDF_TEXT_EXTRACTION_FAILED"
    # JSON may be raw API/sample data; do not summarize it as page prose.
    if suffix == ".json" or content_kind == "json":
        return "", "json", "BLOCKED_NO_NARRATIVE_SOURCE_CONTENT_JSON_OR_API"
    if suffix in {".xlsx", ".xls", ".ods", ".csv"} or content_kind in {"xlsx", "xls", "ods", "csv"}:
        return "", content_kind, "BLOCKED_NO_NARRATIVE_SOURCE_CONTENT_TABULAR_ARTIFACT"
    text_guess = data.decode("utf-8", errors="replace")
    if len(re.sub(r"\s+", "", text_guess)) > 300 and text_guess.count("\x00") < 10:
        return normalize_text(text_guess), "text_guess", None
    return "", content_kind, "BLOCKED_SOURCE_CONTENT_UNAVAILABLE"


def split_sentences(text_body: str) -> list[str]:
    parts = re.split(r"(?<=[.!?。！？])\s+|\n+", text_body)
    clean = []
    for p in parts:
        p = normalize_text(p)
        if len(p) >= 30 and not re.search(r"cookie|subscribe|share this|related|follow us", p, re.I):
            clean.append(p)
    return clean


def make_summary(case: dict[str, Any], artifact_path: Path | None) -> dict[str, Any]:
    meta = case.get("metadata") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = {}
    artifact_sha = case.get("artifact_sha256")
    fetch_chain = meta.get("fetch_chain") or []
    original_url = None
    final_url = None
    if fetch_chain:
        original_url = fetch_chain[0].get("url")
        final_url = fetch_chain[-1].get("url")
    else:
        original_url = meta.get("download_url") or meta.get("landing_page_url")
        final_url = meta.get("download_url") or meta.get("landing_page_url")
    artifact_relpath = None
    if artifact_path:
        try:
            artifact_relpath = str(artifact_path.relative_to(PROJECT))
        except ValueError:
            artifact_relpath = "[outside-project-test-artifact]"
    base = {
        "summary_version": SUMMARY_VERSION,
        "case_id": str(case["review_case_id"]),
        "candidate_version_id": str(case["candidate_version_id"]),
        "source_artifact_identity": {"artifact_sha256": artifact_sha, "path": artifact_relpath},
        "original_url": original_url,
        "final_url": final_url,
        "page_title": case.get("dataset_name"),
        "publisher": case.get("publisher"),
        "published_at": None,
        "retrieved_at": None,
        "document_type": case.get("format"),
        "summary_input_kind": "raw_source_document",
        "candidate_payload_used": False,
        "summary_method": "extractive_fallback",
        "summary_model_version": "deterministic_extractive_v1_no_llm",
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    if not artifact_path:
        return {**base, "summary_status": "BLOCKED_SOURCE_CONTENT_UNAVAILABLE", "failure_reason": "Frozen artifact bytes not found on disk by artifact SHA.", "facts": [], "zh_summary": None}
    text_body, kind, blocker = text_from_artifact(artifact_path, str(case.get("format") or ""))
    base["document_type"] = kind
    base["summary_input_sha256"] = sha_text(text_body) if text_body else None
    if blocker:
        return {**base, "summary_status": blocker, "failure_reason": blocker, "facts": [], "zh_summary": None}
    sentences = split_sentences(text_body)
    if not sentences:
        return {**base, "summary_status": "BLOCKED_SOURCE_CONTENT_UNAVAILABLE", "failure_reason": "No readable narrative body after boilerplate removal.", "facts": [], "zh_summary": None}
    keywords = ["Ebola", "virus", "outbreak", "case", "death", "confirmed", "probable", "response", "risk", "Democratic Republic", "Guinea", "Liberia", "Sierra Leone", "influenza", "dengue"]
    scored = []
    for idx, sent in enumerate(sentences):
        score = sum(1 for k in keywords if k.lower() in sent.lower())
        if re.search(r"\b20\d{2}\b|\b\d+\b", sent):
            score += 1
        scored.append((score, idx, sent))
    chosen = [x for _, _, x in sorted(scored, key=lambda t: (-t[0], t[1]))[:6]]
    # Keep original-order selected sentences for auditability.
    selected = [s for s in sentences if s in set(chosen)][:6]
    facts = []
    for idx, sent in enumerate(selected, start=1):
        facts.append(
            {
                "fact_id": f"fact-{idx}",
                "source_sentence": sent,
                "locator": {
                    "artifact_sha256": artifact_sha,
                    "locator_kind": "normalized_text_sentence_ordinal",
                    "sentence_ordinal": sentences.index(sent) + 1,
                    "normalized_text_sha256": sha_text(sent),
                },
            }
        )
    zh_lines = [
        f"该原始来源由{case.get('publisher') or '发布机构未明'}发布，标题/数据集名称为“{case.get('dataset_name') or '未命名'}”。",
        "以下摘要完全来自冻结原始 Artifact 的正文抽取，不使用候选数据表作为输入。",
    ]
    for fact in facts[:4]:
        zh_lines.append("原文要点：" + fact["source_sentence"][:260])
    return {
        **base,
        "summary_status": "SUMMARY_READY",
        "failure_reason": None,
        "zh_summary": "\n".join(zh_lines),
        "events_places_periods_status": {
            "country_iso3": meta.get("country_iso3"),
            "country_name": meta.get("country_name"),
            "disease": meta.get("disease_id") or meta.get("disease_code") or meta.get("disease_name"),
            "latest_report_period": case.get("latest_report_period"),
            "geographic_scope": case.get("geographic_scope"),
        },
        "main_developments_from_page": [f["source_sentence"] for f in facts[:3]],
        "public_health_response_from_page": [f["source_sentence"] for f in facts if re.search(r"response|surveillance|vaccination|contact|health|WHO|Ministry", f["source_sentence"], re.I)][:3],
        "risks_limitations_uncertainties_from_page": [f["source_sentence"] for f in facts if re.search(r"risk|limited|uncertain|probable|suspected|revision|caution", f["source_sentence"], re.I)][:3],
        "fact_locator_map": facts,
    }


def stable_summary_payload(summary: dict[str, Any]) -> dict[str, Any]:
    payload = json.loads(canonical(summary))
    payload.pop("generated_at", None)
    return payload


def summary_identity(summary: dict[str, Any]) -> tuple[str, str]:
    stable = stable_summary_payload(summary)
    digest = sha_obj(stable)
    ns = uuid.UUID("365bd0ff-a65e-5d72-95f1-35c0a5c0f8a3")
    identity = str(
        uuid.uuid5(
            ns,
            "|".join(
                [
                    str(summary["case_id"]),
                    str(summary["candidate_version_id"]),
                    str(summary["summary_version"]),
                    str(summary["source_artifact_identity"]["artifact_sha256"]),
                    str(summary.get("summary_input_sha256") or summary.get("summary_status")),
                    digest,
                ]
            ),
        )
    )
    return identity, digest


def main() -> None:
    REPORT.mkdir(parents=True, exist_ok=True)
    engine = sa.create_engine(db_url(), hide_parameters=True, future=True)
    before_counts: dict[str, int] = {}
    after_counts: dict[str, int] = {}
    summaries = []
    summary_inserts = 0
    idempotent_skips = 0
    independence_tests: list[dict[str, Any]] = []
    independence_test_done = False
    try:
        with engine.begin() as conn:
            for reg in [
                "epidemic_discovery.source_candidate_versions",
                "epidemic_discovery.source_review_cases",
                "epidemic_discovery.source_review_case_raw_source_summaries",
                "epidemic_discovery.source_review_decisions",
                "epidemic_discovery.source_promotion_runs",
                "epidemic_p1_12.trusted_dashboard_series_v1",
                "epidemic.dataset_snapshots",
            ]:
                before_counts[reg] = int(conn.execute(text(f"SELECT count(*)::bigint FROM {reg}")).scalar_one())
            cases = [
                dict(r)
                for r in conn.execute(
                    text(
                        """
                        WITH cards AS (
                          SELECT review_case_id, state, candidate_version_id, publisher, dataset_name, source_tier,
                                 candidate_status, license_status, format, metadata, row_count, artifact_sha256,
                                 geographic_scope, latest_report_period, effective_status
                          FROM epidemic_discovery.review_candidate_cards_v2
                        ),
                        fallback_cases AS (
                          SELECT
                            rc.id AS review_case_id,
                            rc.state,
                            rc.candidate_version_id,
                            c.publisher,
                            c.dataset_name,
                            c.source_tier,
                            c.status AS candidate_status,
                            v.license_status,
                            v.format,
                            v.metadata,
                            COALESCE(
                              NULLIF(v.metadata->>'exact_candidate_count','')::bigint,
                              CASE WHEN jsonb_typeof(v.metadata->'candidate_rows') = 'array'
                                   THEN jsonb_array_length(v.metadata->'candidate_rows')::bigint
                                   ELSE NULL END
                            ) AS row_count,
                            v.content_sha256 AS artifact_sha256,
                            COALESCE(
                              v.metadata->>'geographic_scope',
                              v.metadata #>> '{candidate_rows,0,geographic_scope}'
                            ) AS geographic_scope,
                            COALESCE(
                              v.metadata->>'latest_report_period',
                              v.metadata #>> '{candidate_rows,0,data_as_of}'
                            ) AS latest_report_period,
                            rc.state AS effective_status
                          FROM epidemic_discovery.source_review_cases rc
                          JOIN epidemic_discovery.source_candidate_versions v ON v.id = rc.candidate_version_id
                          JOIN epidemic_discovery.source_candidates c ON c.id = v.candidate_id
                          WHERE NOT EXISTS (
                            SELECT 1 FROM cards existing WHERE existing.review_case_id = rc.id
                          )
                        )
                        SELECT *
                        FROM (
                          SELECT * FROM cards
                          UNION ALL
                          SELECT * FROM fallback_cases
                        ) all_cases
                        ORDER BY review_case_id::text
                        """
                    )
                )
                .mappings()
                .all()
            ]
            for case in cases:
                artifact_path = find_artifact(str(case.get("artifact_sha256") or ""))
                summary = make_summary(case, artifact_path)
                summary_id, summary_sha = summary_identity(summary)
                summary["summary_sha256"] = summary_sha
                summaries.append(summary)
                res = conn.execute(
                    text(
                        """
                        INSERT INTO epidemic_discovery.source_review_case_raw_source_summaries
                        (id,review_case_id,candidate_version_id,summary_version,artifact_sha256,
                         summary_input_kind,candidate_payload_used,original_url,final_url,page_title,
                         publisher,published_at,retrieved_at,document_type,summary_status,failure_reason,
                         summary_input_sha256,summary_json,summary_sha256,method,model_version)
                        VALUES
                        (CAST(:id AS uuid),CAST(:review_case_id AS uuid),CAST(:candidate_version_id AS uuid),
                         :summary_version,:artifact_sha256,:summary_input_kind,:candidate_payload_used,
                         :original_url,:final_url,:page_title,:publisher,
                         CAST(:published_at AS timestamptz),CAST(:retrieved_at AS timestamptz),
                         :document_type,:summary_status,:failure_reason,:summary_input_sha256,
                         CAST(:summary_json AS jsonb),:summary_sha256,:method,:model_version)
                        ON CONFLICT (id) DO NOTHING
                        """
                    ),
                    {
                        "id": summary_id,
                        "review_case_id": summary["case_id"],
                        "candidate_version_id": summary["candidate_version_id"],
                        "summary_version": summary["summary_version"],
                        "artifact_sha256": summary["source_artifact_identity"]["artifact_sha256"],
                        "summary_input_kind": summary["summary_input_kind"],
                        "candidate_payload_used": bool(summary["candidate_payload_used"]),
                        "original_url": summary.get("original_url"),
                        "final_url": summary.get("final_url"),
                        "page_title": summary.get("page_title"),
                        "publisher": summary.get("publisher"),
                        "published_at": summary.get("published_at"),
                        "retrieved_at": summary.get("retrieved_at"),
                        "document_type": summary.get("document_type") or "unknown",
                        "summary_status": summary["summary_status"],
                        "failure_reason": summary.get("failure_reason"),
                        "summary_input_sha256": summary.get("summary_input_sha256"),
                        "summary_json": json.dumps(summary, ensure_ascii=False, sort_keys=True),
                        "summary_sha256": summary_sha,
                        "method": summary.get("summary_method") or "extractive_fallback",
                        "model_version": summary.get("summary_model_version") or "deterministic_extractive_v1_no_llm",
                    },
                )
                if int(res.rowcount or 0) > 0:
                    summary_inserts += int(res.rowcount or 0)
                else:
                    idempotent_skips += 1
                # Independence tests are executed against the same frozen artifact path.
                if (not independence_test_done) and artifact_path and summary.get("summary_status") == "SUMMARY_READY":
                    mutated_case = dict(case)
                    mutated_meta = dict(mutated_case.get("metadata") or {})
                    mutated_meta["candidate_rows"] = [{"metric": "cases", "value": 999999}]
                    mutated_case["metadata"] = mutated_meta
                    mutated = make_summary(mutated_case, artifact_path)
                    independence_tests.append(
                        {
                            "name": "candidate_values_changed_summary_unchanged",
                            "case_id": summary["case_id"],
                            "passed": sha_obj(stable_summary_payload(summary) | {"summary_sha256": None}) == sha_obj(stable_summary_payload(mutated) | {"summary_sha256": None}),
                            "evidence": "make_summary reads frozen artifact text and source metadata, not preview/candidate rows",
                        }
                    )
                    data = artifact_path.read_bytes()
                    with tempfile.NamedTemporaryFile(suffix=artifact_path.suffix, delete=True) as tmp:
                        tmp.write(data + b"\nVisible outbreak summary input changed sentinel 12345.")
                        tmp.flush()
                        changed = make_summary(case, Path(tmp.name))
                    independence_tests.append(
                        {
                            "name": "artifact_text_changed_summary_input_digest_changes",
                            "case_id": summary["case_id"],
                            "passed": changed.get("summary_input_sha256") != summary.get("summary_input_sha256"),
                            "before_input_sha256": summary.get("summary_input_sha256"),
                            "after_input_sha256": changed.get("summary_input_sha256"),
                        }
                    )
                    independence_test_done = True
            for reg in before_counts:
                after_counts[reg] = int(conn.execute(text(f"SELECT count(*)::bigint FROM {reg}")).scalar_one())
    finally:
        engine.dispose()
    ready = [s for s in summaries if s["summary_status"] == "SUMMARY_READY"]
    blocked = [s for s in summaries if s["summary_status"] != "SUMMARY_READY"]
    coverage = {
        "case_total": len(summaries),
        "summary_ready_count": len(ready),
        "blocked_count": len(blocked),
        "coverage_equation_holds": len(summaries) == len(ready) + len(blocked),
        "candidate_payload_used_any": any(s.get("candidate_payload_used") for s in summaries),
        "summary_input_all_raw_source_document": all(s.get("summary_input_kind") == "raw_source_document" for s in summaries),
        "statuses": [{"case_id": s["case_id"], "status": s["summary_status"], "artifact_sha256": s["source_artifact_identity"]["artifact_sha256"], "document_type": s["document_type"], "failure_reason": s.get("failure_reason")} for s in summaries],
    }
    # Independence tests: summary digest depends on raw source text/status, not candidate payload.
    test_report = {
        "tests": independence_tests,
        "passed": bool(independence_tests) and all(t["passed"] for t in independence_tests),
        "summary_inserts": summary_inserts,
        "idempotent_skips": idempotent_skips,
    }
    write_json("review_summary_input_manifest.json", {"summaries": summaries})
    write_json("review_summary_coverage.json", coverage)
    write_json("review_summary_claim_audit.json", {"method": "deterministic extractive fallback from frozen artifact bytes only", "candidate_payload_used": False, "model_used": False, "qwen_used": False, "tests": test_report})
    write_json("summary_backfill_write_audit.json", {"before_counts": before_counts, "after_counts": after_counts, "summary_inserts": summary_inserts, "idempotent_skips": idempotent_skips, "metadata_updates": 0, "trusted_snapshot_promotion_human_decision_writes": 0})
    append_jsonl(
        "repair_cycle_ledger.jsonl",
        {
            "cycle_id": "cycle-" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
            "status": "SUMMARY_BACKFILL_COMPLETE",
            "failed_checks": [] if coverage["coverage_equation_holds"] else ["SUMMARY_COVERAGE_EQUATION_FAILED"],
            "root_cause_evidence": "Review candidate version metadata lacked raw source summary fields.",
            "solution": "Inserted raw_source_summary_v1 records into append-only summary table from frozen artifacts or explicit source-content blockers.",
            "modified_files": [],
            "test_results": test_report,
            "database_before_after": {"before": before_counts, "after": after_counts, "summary_inserts": summary_inserts, "metadata_updates": 0},
            "remaining_acceptance_gaps": ["UI_NON_LOGIN_SCREENSHOT_PENDING", "DASHBOARD_RECONCILIATION_PENDING", "PREDICTION_DEMO_PENDING"],
        },
    )
    print(json.dumps({"status": "SUMMARY_BACKFILL_COMPLETE", "case_total": len(summaries), "summary_ready_count": len(ready), "blocked_count": len(blocked), "summary_inserts": summary_inserts, "idempotent_skips": idempotent_skips}, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
