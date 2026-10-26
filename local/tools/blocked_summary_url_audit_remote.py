from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url


PROJECT = Path(os.environ["PROJECT"])
REPORT = PROJECT / "reports/ebola_data_visualization_prediction_engineering_loop"


def jdefault(value: Any) -> Any:
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    return str(value)


def sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def write_json(name: str, value: Any) -> None:
    REPORT.mkdir(parents=True, exist_ok=True)
    (REPORT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=jdefault) + "\n", encoding="utf-8")


def db_url() -> Any:
    url = os.environ["MIGRATION_DATABASE_URL"]
    if os.environ.get("POSTGRES_ADMIN_PASSWORD") and os.environ.get("POSTGRES_ADMIN_USER"):
        url = make_url(url).set(username=os.environ["POSTGRES_ADMIN_USER"], password=os.environ["POSTGRES_ADMIN_PASSWORD"])
    return url


URL_RE = re.compile(r"https?://[^\s\"'<>)}\]]+", re.I)


def collect_urls(value: Any) -> list[str]:
    found: list[str] = []
    if value is None:
        return found
    if isinstance(value, str):
        found.extend(URL_RE.findall(value))
        return found
    if isinstance(value, dict):
        for item in value.values():
            found.extend(collect_urls(item))
        return found
    if isinstance(value, list):
        for item in value:
            found.extend(collect_urls(item))
        return found
    return found


def domain(url: str) -> str:
    from urllib.parse import urlparse

    return urlparse(url).netloc.lower()


def classify_url(url: str) -> str:
    path = url.lower().split("?", 1)[0]
    if path.endswith((".xlsx", ".xls", ".ods", ".csv", ".json", ".pdf")):
        return "download_or_api_artifact"
    if any(token in path for token in ["about", "metadata", "dataset", "landing", "documentation", "docs", "report", "situation"]):
        return "possible_landing_or_documentation"
    return "unknown_page"


def main() -> None:
    engine = sa.create_engine(db_url(), hide_parameters=True, future=True)
    rows: list[dict[str, Any]] = []
    try:
        with engine.connect() as conn:
            conn.execute(text("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            conn.execute(text("SET LOCAL statement_timeout='30s'"))
            result = conn.execute(
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
            ).mappings().all()
            conn.execute(text("ROLLBACK"))
    finally:
        engine.dispose()

    for r in result:
        meta = r["metadata"] or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {"raw": meta}
        urls = sorted(set(collect_urls(meta)))
        rows.append(
            {
                "review_case_id": str(r["review_case_id"]),
                "state": r["state"],
                "candidate_version_id": str(r["candidate_version_id"]),
                "artifact_sha256": r["artifact_sha256"],
                "publisher": r["publisher"],
                "dataset_name": r["dataset_name"],
                "source_tier": r["source_tier"],
                "format": r["format"],
                "summary_status": r["summary_status"],
                "failure_reason": r["failure_reason"],
                "metadata_top_level_keys": sorted(str(k) for k in meta.keys()) if isinstance(meta, dict) else [],
                "url_count": len(urls),
                "urls": [
                    {
                        "url_sha256": sha_text(url),
                        "domain": domain(url),
                        "classification": classify_url(url),
                        "url": url,
                    }
                    for url in urls
                ],
            }
        )
    write_json(
        "blocked_summary_url_audit.json",
        {
            "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "case_count": len(rows),
            "cases_with_urls": sum(1 for row in rows if row["url_count"]),
            "domains": sorted({u["domain"] for row in rows for u in row["urls"]}),
            "rows": rows,
            "database_writes": 0,
            "trusted_snapshot_promotion_human_decision_writes": 0,
        },
    )
    print(json.dumps({"status": "BLOCKED_SUMMARY_URL_AUDIT_COMPLETE", "case_count": len(rows), "cases_with_urls": sum(1 for row in rows if row["url_count"])}, sort_keys=True))


if __name__ == "__main__":
    main()
