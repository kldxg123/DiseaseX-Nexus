from __future__ import annotations

import datetime as dt
import hashlib
import html
import ipaddress
import json
import os
import re
import socket
import unicodedata
import urllib.parse
import urllib.request
import urllib.robotparser
import uuid
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url


PROJECT = Path(os.environ["PROJECT"])
REPORT = PROJECT / "reports/ebola_data_visualization_prediction_engineering_loop"
ARTIFACT_DIR = REPORT / "landing_artifacts"
SUMMARY_VERSION = "raw_source_summary_v1_landing_doc"
ALLOW_DOMAINS = {
    "www.gov.uk",
    "stacks.cdc.gov",
    "evd-daily.health.go.ug",
}


def jdefault(value: Any) -> Any:
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    return str(value)


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha_text(value: str) -> str:
    return sha_bytes(value.encode("utf-8"))


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=jdefault)


def sha_obj(value: Any) -> str:
    return sha_text(canonical(value))


def write_json(name: str, value: Any) -> None:
    REPORT.mkdir(parents=True, exist_ok=True)
    (REPORT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=jdefault) + "\n", encoding="utf-8")


def db_url() -> Any:
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
    raw = re.sub(r"(?is)<(script|style|noscript|nav|footer|form|svg|header).*?</\1>", " ", raw)
    raw = re.sub(r"(?is)<br\s*/?>", "\n", raw)
    raw = re.sub(r"(?is)</(h1|h2|h3|h4|p|li|div|section|article|tr)>", "\n", raw)
    raw = re.sub(r"(?is)<[^>]+>", " ", raw)
    return normalize_text(raw)


def title_from_html(raw: str) -> str | None:
    match = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw)
    return normalize_text(re.sub(r"(?is)<[^>]+>", " ", match.group(1))) if match else None


URL_RE = re.compile(r"https?://[^\s\"'<>)}\]]+", re.I)


def collect_urls(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return URL_RE.findall(value)
    if isinstance(value, dict):
        out: list[str] = []
        for item in value.values():
            out.extend(collect_urls(item))
        return out
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(collect_urls(item))
        return out
    return []


def url_domain(url: str) -> str:
    return urllib.parse.urlparse(url).netloc.lower()


def is_private_host(host: str) -> bool:
    infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
            return True
    return False


def robots_allows(url: str) -> dict[str, Any]:
    parsed = urllib.parse.urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    rp = urllib.robotparser.RobotFileParser()
    rp.set_url(robots_url)
    try:
        rp.read()
        return {"robots_url": robots_url, "allowed": bool(rp.can_fetch("epidemic-openclaw-review-summary/1.0", url)), "error": None}
    except Exception as exc:
        return {"robots_url": robots_url, "allowed": False, "error": type(exc).__name__}


def derive_landing_urls(meta: dict[str, Any]) -> list[dict[str, str]]:
    urls = sorted(set(collect_urls(meta)))
    candidates: list[dict[str, str]] = []
    for url in urls:
        parsed = urllib.parse.urlparse(url)
        host = parsed.netloc.lower()
        path = parsed.path
        if host == "www.gov.uk" and "/government/statistics/" in path:
            candidates.append({"url": url, "reason": "metadata_official_landing_page"})
        if host == "evd-daily.health.go.ug" and path in {"", "/"}:
            candidates.append({"url": url, "reason": "metadata_official_landing_page"})
        if host == "stacks.cdc.gov":
            match = re.search(r"/view/cdc/(\d+)", path)
            if match:
                candidates.append({"url": f"https://stacks.cdc.gov/view/cdc/{match.group(1)}", "reason": "derived_cdc_stacks_landing_from_artifact_url"})
    dedup: dict[str, str] = {}
    for item in candidates:
        dedup[item["url"]] = item["reason"]
    return [{"url": url, "reason": reason} for url, reason in sorted(dedup.items())]


def fetch_landing(url: str) -> dict[str, Any]:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()
    if parsed.scheme != "https" or host not in ALLOW_DOMAINS:
        return {"ok": False, "url": url, "blocker": "LANDING_URL_NOT_IN_ALLOWLIST"}
    if is_private_host(host):
        return {"ok": False, "url": url, "blocker": "LANDING_URL_RESOLVES_PRIVATE_OR_RESERVED"}
    robots = robots_allows(url)
    if not robots.get("allowed"):
        return {"ok": False, "url": url, "blocker": "ROBOTS_DISALLOW_OR_UNAVAILABLE", "robots": robots}
    req = urllib.request.Request(url, headers={"User-Agent": "epidemic-openclaw-review-summary/1.0"})
    with urllib.request.urlopen(req, timeout=12) as resp:
        final_url = resp.geturl()
        final_host = urllib.parse.urlparse(final_url).netloc.lower()
        if final_host not in ALLOW_DOMAINS:
            return {"ok": False, "url": url, "final_url": final_url, "blocker": "REDIRECT_OUTSIDE_ALLOWLIST", "robots": robots}
        ctype = resp.headers.get("content-type", "")
        data = resp.read(2_000_001)
    if len(data) > 2_000_000:
        return {"ok": False, "url": url, "final_url": final_url, "blocker": "LANDING_RESPONSE_TOO_LARGE", "robots": robots}
    if "html" not in ctype.lower() and "text" not in ctype.lower():
        return {"ok": False, "url": url, "final_url": final_url, "content_type": ctype, "blocker": "LANDING_NOT_TEXT_OR_HTML", "robots": robots}
    raw = data.decode("utf-8", errors="replace")
    text_body = html_to_text(raw)
    artifact_sha = sha_bytes(data)
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    suffix = ".html" if "html" in ctype.lower() else ".txt"
    artifact_path = ARTIFACT_DIR / f"{artifact_sha}{suffix}"
    if not artifact_path.exists():
        artifact_path.write_bytes(data)
    return {
        "ok": True,
        "url": url,
        "final_url": final_url,
        "content_type": ctype,
        "bytes": len(data),
        "landing_artifact_sha256": artifact_sha,
        "landing_artifact_path": str(artifact_path.relative_to(PROJECT)),
        "title": title_from_html(raw),
        "text_chars": len(text_body),
        "text_sha256": sha_text(text_body),
        "text_body": text_body,
        "robots": robots,
    }


def split_sentences(text_body: str) -> list[str]:
    parts = re.split(r"(?<=[.!?。！？])\s+|\n+", text_body)
    clean = []
    for part in parts:
        value = normalize_text(part)
        if len(value) >= 40 and not re.search(r"cookie|subscribe|share this|related content|sign up|newsletter", value, re.I):
            clean.append(value)
    return clean


def build_summary(case: dict[str, Any], landing: dict[str, Any], derivation_reason: str) -> dict[str, Any] | None:
    text_body = landing.get("text_body") or ""
    if len(re.sub(r"\s+", "", text_body)) < 300:
        return None
    sentences = split_sentences(text_body)
    if not sentences:
        return None
    keywords = [
        "Ebola",
        "influenza",
        "COVID",
        "surveillance",
        "report",
        "data",
        "case",
        "death",
        "weekly",
        "Guinea",
        "Liberia",
        "Sierra Leone",
        "Uganda",
    ]
    scored = []
    for idx, sent in enumerate(sentences):
        score = sum(1 for word in keywords if word.lower() in sent.lower())
        if re.search(r"\b20\d{2}\b|\b\d+\b", sent):
            score += 1
        scored.append((score, idx, sent))
    chosen = [sent for _, _, sent in sorted(scored, key=lambda x: (-x[0], x[1]))[:6]]
    selected = [sent for sent in sentences if sent in set(chosen)][:6]
    facts = []
    for idx, sent in enumerate(selected, start=1):
        facts.append(
            {
                "fact_id": f"landing-fact-{idx}",
                "source_sentence": sent,
                "locator": {
                    "artifact_sha256": landing["landing_artifact_sha256"],
                    "locator_kind": "landing_page_normalized_sentence_ordinal",
                    "sentence_ordinal": sentences.index(sent) + 1,
                    "normalized_text_sha256": sha_text(sent),
                },
            }
        )
    zh_lines = [
        f"该候选的结构化 artifact 是 {case.get('format')}，因此本摘要改用官方 landing/documentation page 的冻结正文作为输入。",
        f"官方页面：{landing.get('title') or case.get('dataset_name')}",
        "摘要输入来自 landing page artifact，而不是 candidate rows。",
    ]
    for fact in facts[:4]:
        zh_lines.append("原文要点：" + fact["source_sentence"][:260])
    return {
        "summary_version": SUMMARY_VERSION,
        "case_id": str(case["review_case_id"]),
        "candidate_version_id": str(case["candidate_version_id"]),
        "source_artifact_identity": {
            "candidate_artifact_sha256": case.get("artifact_sha256"),
            "landing_artifact_sha256": landing["landing_artifact_sha256"],
            "landing_artifact_path": landing["landing_artifact_path"],
            "derivation_reason": derivation_reason,
        },
        "original_url": landing["url"],
        "final_url": landing["final_url"],
        "page_title": landing.get("title") or case.get("dataset_name"),
        "publisher": case.get("publisher"),
        "published_at": None,
        "retrieved_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "document_type": "landing_html",
        "summary_input_kind": "raw_source_document",
        "candidate_payload_used": False,
        "summary_method": "extractive_fallback",
        "summary_model_version": "deterministic_extractive_v1_landing_doc_no_llm",
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "summary_input_sha256": landing["text_sha256"],
        "summary_status": "SUMMARY_READY",
        "failure_reason": None,
        "zh_summary": "\n".join(zh_lines),
        "events_places_periods_status": {
            "source_dataset_name": case.get("dataset_name"),
            "source_format": case.get("format"),
            "source_tier": case.get("source_tier"),
        },
        "main_developments_from_page": [fact["source_sentence"] for fact in facts[:3]],
        "public_health_response_from_page": [fact["source_sentence"] for fact in facts if re.search(r"surveillance|public health|WHO|CDC|Ministry|response|data", fact["source_sentence"], re.I)][:3],
        "risks_limitations_uncertainties_from_page": [fact["source_sentence"] for fact in facts if re.search(r"provisional|revision|limited|risk|uncertain|probable|suspected|experimental", fact["source_sentence"], re.I)][:3],
        "fact_locator_map": facts,
    }


def summary_identity(summary: dict[str, Any]) -> tuple[str, str]:
    payload = json.loads(canonical(summary))
    payload.pop("generated_at", None)
    digest = sha_obj(payload)
    ns = uuid.UUID("b77579c6-d1e2-5e2f-9008-ee17f1693ff0")
    identity = str(
        uuid.uuid5(
            ns,
            "|".join(
                [
                    str(summary["case_id"]),
                    str(summary["candidate_version_id"]),
                    str(summary["summary_version"]),
                    str(summary["source_artifact_identity"].get("candidate_artifact_sha256")),
                    str(summary["source_artifact_identity"].get("landing_artifact_sha256")),
                    str(summary["summary_input_sha256"]),
                    digest,
                ]
            ),
        )
    )
    return identity, digest


def load_blocked_cases(conn: sa.Connection) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in conn.execute(
            text(
                """
                SELECT
                  rc.id AS review_case_id,
                  rc.state,
                  v.id AS candidate_version_id,
                  v.content_sha256 AS artifact_sha256,
                  v.format,
                  v.metadata,
                  c.publisher,
                  c.dataset_name,
                  c.source_tier,
                  s.summary_status,
                  s.failure_reason
                FROM epidemic_discovery.source_review_cases rc
                JOIN epidemic_discovery.source_candidate_versions v ON v.id = rc.candidate_version_id
                JOIN epidemic_discovery.source_candidates c ON c.id = v.candidate_id
                LEFT JOIN epidemic_discovery.review_case_raw_source_summaries_v1 s ON s.review_case_id=rc.id
                WHERE COALESCE(s.summary_status,'') <> 'SUMMARY_READY'
                ORDER BY rc.id::text
                """
            )
        )
        .mappings()
        .all()
    ]


def main() -> None:
    REPORT.mkdir(parents=True, exist_ok=True)
    engine = sa.create_engine(db_url(), hide_parameters=True, future=True)
    attempts: list[dict[str, Any]] = []
    inserts = 0
    skips = 0
    try:
        with engine.begin() as conn:
            before = int(conn.execute(text("SELECT count(*)::bigint FROM epidemic_discovery.source_review_case_raw_source_summaries")).scalar_one())
            cases = load_blocked_cases(conn)
            for case in cases:
                meta = case.get("metadata") or {}
                if isinstance(meta, str):
                    try:
                        meta = json.loads(meta)
                    except Exception:
                        meta = {}
                candidates = derive_landing_urls(meta if isinstance(meta, dict) else {})
                case_attempt = {
                    "review_case_id": str(case["review_case_id"]),
                    "candidate_version_id": str(case["candidate_version_id"]),
                    "publisher": case.get("publisher"),
                    "dataset_name": case.get("dataset_name"),
                    "format": case.get("format"),
                    "candidate_landing_urls": candidates,
                    "fetches": [],
                    "inserted_summary": False,
                }
                for candidate in candidates[:2]:
                    try:
                        fetched = fetch_landing(candidate["url"])
                    except Exception as exc:
                        fetched = {"ok": False, "url": candidate["url"], "blocker": type(exc).__name__}
                    safe_fetched = {k: v for k, v in fetched.items() if k != "text_body"}
                    case_attempt["fetches"].append(safe_fetched)
                    if not fetched.get("ok"):
                        continue
                    summary = build_summary(case, fetched, candidate["reason"])
                    if not summary:
                        case_attempt["fetches"][-1]["summary_blocker"] = "LANDING_TEXT_NOT_SUMMARIZABLE"
                        continue
                    summary_id, summary_sha = summary_identity(summary)
                    summary["summary_sha256"] = summary_sha
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
                            "artifact_sha256": summary["source_artifact_identity"]["candidate_artifact_sha256"],
                            "summary_input_kind": summary["summary_input_kind"],
                            "candidate_payload_used": bool(summary["candidate_payload_used"]),
                            "original_url": summary.get("original_url"),
                            "final_url": summary.get("final_url"),
                            "page_title": summary.get("page_title"),
                            "publisher": summary.get("publisher"),
                            "published_at": summary.get("published_at"),
                            "retrieved_at": summary.get("retrieved_at"),
                            "document_type": summary.get("document_type"),
                            "summary_status": summary["summary_status"],
                            "failure_reason": summary.get("failure_reason"),
                            "summary_input_sha256": summary.get("summary_input_sha256"),
                            "summary_json": json.dumps(summary, ensure_ascii=False, sort_keys=True),
                            "summary_sha256": summary_sha,
                            "method": summary.get("summary_method"),
                            "model_version": summary.get("summary_model_version"),
                        },
                    )
                    if int(res.rowcount or 0) > 0:
                        inserts += 1
                        case_attempt["inserted_summary"] = True
                    else:
                        skips += 1
                    case_attempt["summary_sha256"] = summary_sha
                    break
                attempts.append(case_attempt)
            after = int(conn.execute(text("SELECT count(*)::bigint FROM epidemic_discovery.source_review_case_raw_source_summaries")).scalar_one())
    finally:
        engine.dispose()
    report = {
        "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "LANDING_SUMMARY_BACKFILL_COMPLETE",
        "attempted_cases": len(attempts),
        "summary_inserts": inserts,
        "idempotent_skips": skips,
        "attempts": attempts,
        "database_writes_scope": "append-only summary metadata only",
        "trusted_snapshot_promotion_human_decision_writes": 0,
        "delete": 0,
        "summary_count_before_after": {"before": before, "after": after},
    }
    write_json("landing_summary_backfill_report.json", report)
    print(json.dumps({"status": report["status"], "attempted_cases": len(attempts), "summary_inserts": inserts, "summary_count_after": after}, sort_keys=True))


if __name__ == "__main__":
    main()
