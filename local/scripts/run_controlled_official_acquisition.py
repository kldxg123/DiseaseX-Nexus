from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGES_DIR = PROJECT_ROOT / "packages"
for candidate in (PROJECT_ROOT, PACKAGES_DIR):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from cleaner_agent.agent import CleanerAgent
from cleaner_agent.features import FeatureBuilder
from common.db import get_session, init_db
from common.models import EpidemicEvent, ModelFeature, RawDocument, SourceRegistry
from crawler_agent.access_policy import load_access_policies
from crawler_agent.connector_ingest import store_connector_documents, upsert_connector_source
from crawler_agent.connectors import CdcFluViewConnector, ConnectorDocument
from crawler_agent.connectors.cdc import CDC_FLUVIEW_SOURCE_KEY
from crawler_agent.live_fetch_guard import LiveFetchGuard
from crawler_agent.raw_document_store import RawDocumentStore
from crawler_optimizer_agent import CrawlerOptimizerAgent, summarize_optimizer_report
from data_quality_agent.report import build_data_quality_report, render_markdown_report

DEFAULT_FLUVIEW_URL = "https://www.cdc.gov/fluview/index.html"
DEFAULT_POLICY = "configs/source_access_policy.example.json"
DEFAULT_OUTPUT_DIR = "reports/live_acquisition"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a guarded single-URL official source acquisition pilot.",
    )
    parser.add_argument("--source-key", default=CDC_FLUVIEW_SOURCE_KEY)
    parser.add_argument("--country", default="United States")
    parser.add_argument("--disease", default="influenza")
    parser.add_argument("--execute-live", action="store_true")
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--terms-reviewed", action="store_true")
    parser.add_argument("--manual-reviewed", action="store_true")
    parser.add_argument("--robots-allowed", action="store_true")
    parser.add_argument("--max-requests", type=int, default=1)
    parser.add_argument("--live-url", default=DEFAULT_FLUVIEW_URL)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--policy", default=DEFAULT_POLICY)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)

    if not args.execute_live:
        summary = {
            "status": "failed",
            "live_execution_enabled": False,
            "network_accessed": False,
            "warnings": ["--execute-live is required for controlled acquisition."],
        }
        print(json.dumps(_safe_json(summary), ensure_ascii=False, sort_keys=True))
        return 2

    result = run_controlled_official_acquisition(
        source_key=args.source_key,
        country=args.country,
        disease=args.disease,
        live_url=args.live_url,
        allow_network=args.allow_network,
        terms_reviewed=args.terms_reviewed,
        manual_reviewed=args.manual_reviewed,
        robots_allowed=args.robots_allowed,
        max_requests=args.max_requests,
        database_url=args.database_url,
        policy_path=args.policy,
        output_dir=args.output_dir,
    )
    print(json.dumps(_public_summary(result), ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") == "ok" else 1


def run_controlled_official_acquisition(
    *,
    source_key: str = CDC_FLUVIEW_SOURCE_KEY,
    country: str = "United States",
    disease: str = "influenza",
    live_url: str = DEFAULT_FLUVIEW_URL,
    allow_network: bool = False,
    terms_reviewed: bool = False,
    manual_reviewed: bool = False,
    robots_allowed: bool = False,
    max_requests: int = 1,
    database_url: str | None = None,
    policy_path: str = DEFAULT_POLICY,
    output_dir: str = DEFAULT_OUTPUT_DIR,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = _allocate_run_dir(Path(output_dir), source_key, timestamp)
    run_dir.mkdir(parents=True, exist_ok=True)
    db_path = run_dir / "controlled_official_acquisition.sqlite"
    resolved_database_url = database_url or f"sqlite:///{db_path.as_posix()}"
    summary = _base_summary(
        source_key=source_key,
        country=country,
        disease=disease,
        live_url=live_url,
        run_dir=run_dir,
        database_url="sqlite",
    )

    validation_error = _validate_args(
        source_key=source_key,
        disease=disease,
        max_requests=max_requests,
        live_url=live_url,
    )
    if validation_error:
        summary["warnings"].append(validation_error)
        return _finalize_outputs(summary, run_dir, resolved_database_url)

    try:
        policies = load_access_policies(policy_path)
        guard = LiveFetchGuard(policies)
        decision = guard.check(
            source_key,
            allow_network=allow_network,
            requested_url=live_url,
            robots_allowed=robots_allowed,
            terms_reviewed=terms_reviewed,
            manual_reviewed=manual_reviewed,
            request_count=max_requests,
        )
        summary["guardrail_allowed"] = bool(decision.allowed)
        if not decision.allowed:
            summary["warnings"].append(f"guardrail denied acquisition: {decision.reason}")
            return _finalize_outputs(summary, run_dir, resolved_database_url)

        init_db(resolved_database_url).dispose()
        response = _fetch_once(live_url, transport=transport)
        summary["network_accessed"] = True
        summary["request_count"] = 1
        summary["http_status_code"] = int(response.status_code)
        summary["content_type"] = response.headers.get("content-type")
        summary["content_length"] = len(response.content or b"")
        summary["content_hash_prefix"] = hashlib.sha256(response.content or b"").hexdigest()[:16]
        response.raise_for_status()

        connector = CdcFluViewConnector()
        parsed_documents = connector.parse(response.text, base_url=live_url)
        safe_documents = [_truncate_document(document) for document in parsed_documents]
        summary["connector_documents_parsed"] = len(safe_documents)
        if not safe_documents:
            summary["warnings"].append("connector parsed zero documents")

        with get_session(resolved_database_url) as session:
            before = _counts(session)
            source = upsert_connector_source(
                session,
                "cdc_fluview",
                base_url=live_url,
                status="pending_review",
            )
            stored = store_connector_documents(
                source=source,
                documents=safe_documents,
                store=RawDocumentStore(session),
            )
            for result in stored:
                result.document.metadata_json = {
                    **(result.document.metadata_json or {}),
                    "controlled_acquisition": True,
                    "network_executed": True,
                    "source_key": source_key,
                    "human_review_required": True,
                    "full_response_body_saved": False,
                }
            session.flush()
            clean_summary = CleanerAgent(session).process_pending(
                limit=max(100, len(safe_documents) + 10),
                country=country,
                disease=disease,
            )
            feature_summary = FeatureBuilder(session).build(
                country=country,
                disease=disease,
                horizons_days=(7, 28),
                outbreak_threshold=1,
                split_type="controlled_acquisition",
                replace_existing=True,
            )
            after = _counts(session)
            summary["source_status"] = source.status
            summary["raw_documents_saved"] = after["raw_documents"] - before["raw_documents"]
            summary["epidemic_events_created"] = after["epidemic_events"] - before["epidemic_events"]
            summary["model_features_created"] = after["model_features"] - before["model_features"]
            summary["duplicates"] = max(0, len(stored) - summary["raw_documents_saved"])
            summary["cleaner"] = {
                "processed": int(clean_summary.get("processed", 0)),
                "events_created": int(clean_summary.get("events_created", 0)),
                "no_event": int(clean_summary.get("no_event", 0)),
                "errors": int(clean_summary.get("errors", 0)),
            }
            summary["features"] = {
                "feature_rows": int(feature_summary.get("feature_rows", 0)),
                "saved": int(feature_summary.get("saved", 0)),
                "updated": int(feature_summary.get("updated", 0)),
            }
            if summary["features"]["feature_rows"] == 0:
                summary["warnings"].append("not enough live acquisition history to build model features")
            summary["data_quality_report"] = _write_data_quality_report(
                session,
                run_dir,
                country=country,
                disease=disease,
            )
            optimizer_report = CrawlerOptimizerAgent(session).analyze(
                country=country,
                disease=disease,
                include_connector_specs=True,
                apply_safe_actions=False,
            )
            optimizer_summary = summarize_optimizer_report(
                optimizer_report,
                max_failure_cases=20,
                max_actions=20,
                max_connector_specs=10,
            )
            optimizer_path = run_dir / "crawler_optimizer_report.json"
            _write_json(optimizer_path, optimizer_summary)
            summary["crawler_optimizer_report"] = str(optimizer_path)
            summary["crawler_optimizer"] = {
                "failure_case_count": int(optimizer_summary.get("failure_case_count", 0)),
                "recommended_action_count": int(optimizer_summary.get("recommended_action_count", 0)),
                "connector_spec_count": int(optimizer_summary.get("connector_spec_count", 0)),
            }
        summary["status"] = "ok"
    except httpx.HTTPError as exc:
        summary["status"] = "failed"
        summary["warnings"].append(f"controlled acquisition failed: {type(exc).__name__}")
        if hasattr(exc, "response") and exc.response is not None:
            response = exc.response
            summary["http_status_code"] = int(response.status_code)
            summary["content_type"] = response.headers.get("content-type")
            summary["content_length"] = len(response.content or b"")
            summary["content_hash_prefix"] = hashlib.sha256(response.content or b"").hexdigest()[:16]
    except Exception as exc:
        summary["status"] = "failed"
        summary["warnings"].append(f"controlled acquisition failed: {type(exc).__name__}")
    return _finalize_outputs(summary, run_dir, resolved_database_url)


def _fetch_once(live_url: str, *, transport: httpx.BaseTransport | None = None) -> httpx.Response:
    with httpx.Client(
        follow_redirects=False,
        timeout=20.0,
        transport=transport,
        headers={"User-Agent": "infectious-intel-local/0.1 controlled-official-acquisition"},
    ) as client:
        return client.get(live_url)


def _truncate_document(document: ConnectorDocument, *, max_chars: int = 2400) -> ConnectorDocument:
    body = document.body_text or ""
    if len(body) <= max_chars:
        return document
    return replace(
        document,
        body_text=body[:max_chars] + " ...[truncated for controlled acquisition]",
        metadata={
            **document.metadata,
            "body_text_truncated": True,
            "body_text_char_limit": max_chars,
            "original_body_text_length": len(body),
        },
    )


def _validate_args(*, source_key: str, disease: str, max_requests: int, live_url: str) -> str | None:
    if source_key != CDC_FLUVIEW_SOURCE_KEY:
        return "only cdc_fluview is allowed for this controlled official acquisition"
    if str(disease).lower() != "influenza":
        return "this controlled official acquisition is limited to influenza"
    if max_requests != 1:
        return "max_requests must be exactly 1"
    if not live_url.startswith("https://"):
        return "live_url must be an HTTPS URL"
    return None


def _allocate_run_dir(output_dir: Path, source_key: str, timestamp: str) -> Path:
    candidate = output_dir / f"{source_key}_controlled_acquisition_{timestamp}"
    if not candidate.exists():
        return candidate
    for index in range(1, 100):
        numbered = output_dir / f"{source_key}_controlled_acquisition_{timestamp}_{index:02d}"
        if not numbered.exists():
            return numbered
    raise FileExistsError("could not allocate controlled acquisition output directory")


def _base_summary(
    *,
    source_key: str,
    country: str,
    disease: str,
    live_url: str,
    run_dir: Path,
    database_url: str,
) -> dict[str, Any]:
    return {
        "status": "failed",
        "source_key": source_key,
        "country": country,
        "disease": disease,
        "url": live_url,
        "output_dir": str(run_dir),
        "database": database_url,
        "live_execution_enabled": True,
        "network_accessed": False,
        "request_count": 0,
        "max_requests": 1,
        "follow_links": False,
        "candidate_urls_accessed": False,
        "guardrail_allowed": False,
        "http_status_code": None,
        "content_type": None,
        "content_length": 0,
        "content_hash_prefix": None,
        "connector_documents_parsed": 0,
        "source_status": None,
        "raw_documents_saved": 0,
        "duplicates": 0,
        "epidemic_events_created": 0,
        "model_features_created": 0,
        "cleaner": {"processed": 0, "events_created": 0, "no_event": 0, "errors": 0},
        "features": {"feature_rows": 0, "saved": 0, "updated": 0},
        "data_quality_report": {"json": None, "markdown": None},
        "crawler_optimizer_report": None,
        "crawler_optimizer": {
            "failure_case_count": 0,
            "recommended_action_count": 0,
            "connector_spec_count": 0,
        },
        "production_prediction_started": False,
        "warnings": [],
    }


def _counts(session) -> dict[str, int]:
    return {
        "source_registry": session.query(SourceRegistry).count(),
        "raw_documents": session.query(RawDocument).count(),
        "epidemic_events": session.query(EpidemicEvent).count(),
        "model_features": session.query(ModelFeature).count(),
    }


def _write_data_quality_report(session, run_dir: Path, *, country: str, disease: str) -> dict[str, str]:
    report = build_data_quality_report(session, country=country, disease=disease)
    report["limitations"] = list(report.get("limitations", [])) + [
        "Single-request controlled official acquisition only.",
        "This does not validate forecast accuracy and requires human review.",
    ]
    json_path = run_dir / "data_quality_report.json"
    md_path = run_dir / "data_quality_report.md"
    _write_json(json_path, report)
    md_path.write_text(render_markdown_report(report), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


def _finalize_outputs(summary: dict[str, Any], run_dir: Path, database_url: str) -> dict[str, Any]:
    _ = database_url
    rollback_path = run_dir / "rollback_commands.md"
    rollback_path.write_text(_rollback_markdown(run_dir), encoding="utf-8")
    summary["rollback_commands"] = str(rollback_path)
    summary_path = run_dir / "controlled_acquisition_summary.json"
    _write_json(summary_path, summary)
    markdown_path = run_dir / "controlled_acquisition_summary.md"
    markdown_path.write_text(_summary_markdown(summary), encoding="utf-8")
    summary["summary_json"] = str(summary_path)
    summary["summary_markdown"] = str(markdown_path)
    return _safe_json(summary)


def _rollback_markdown(run_dir: Path) -> str:
    return "\n".join(
        [
            "# Controlled Official Acquisition Rollback",
            "",
            "This cleanup is local-only and does not touch production systems.",
            "",
            "## Cleanup",
            "",
            f"- Delete this output directory after review: `{run_dir}`",
            f"- Delete this pilot SQLite file if present: `{run_dir / 'controlled_official_acquisition.sqlite'}`",
            "- Confirm no production database URL was used.",
            "- Confirm no complete HTML body or response body was saved.",
            "- Confirm no sensitive configuration values appear in reports.",
            "",
            "This acquisition output requires human review before any downstream use.",
            "",
        ]
    )


def _summary_markdown(summary: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Controlled Official Acquisition Summary",
            "",
            "Single-request controlled acquisition. Complete HTML, response bodies, raw text, and sensitive configuration values are not included.",
            "",
            "```json",
            json.dumps(_public_summary(summary), ensure_ascii=False, indent=2, sort_keys=True),
            "```",
            "",
            "Results require human review and do not guarantee forecast accuracy.",
            "",
        ]
    )


def _public_summary(summary: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "status",
        "source_key",
        "country",
        "disease",
        "url",
        "output_dir",
        "live_execution_enabled",
        "network_accessed",
        "request_count",
        "max_requests",
        "follow_links",
        "candidate_urls_accessed",
        "guardrail_allowed",
        "http_status_code",
        "content_type",
        "content_length",
        "content_hash_prefix",
        "connector_documents_parsed",
        "source_status",
        "raw_documents_saved",
        "duplicates",
        "epidemic_events_created",
        "model_features_created",
        "cleaner",
        "features",
        "data_quality_report",
        "crawler_optimizer_report",
        "crawler_optimizer",
        "rollback_commands",
        "summary_json",
        "summary_markdown",
        "production_prediction_started",
        "warnings",
    ]
    return _safe_json({key: summary.get(key) for key in keys})


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(_safe_json(payload), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _safe_json(value: Any) -> Any:
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            lowered_key = key_text.lower()
            if any(
                marker in lowered_key
                for marker in (
                    "password",
                    "token",
                    "secret",
                    "private",
                    "ssh",
                    "raw_text",
                    "body_text",
                    "metadata_json",
                    "provenance_json",
                    "feature_json",
                    "lesson_json",
                )
            ):
                continue
            safe[key_text] = _safe_json(item)
        return safe
    if isinstance(value, list):
        return [_safe_json(item) for item in value[:100]]
    if isinstance(value, tuple):
        return [_safe_json(item) for item in value[:100]]
    if isinstance(value, str):
        lowered = value.lower()
        if any(marker in lowered for marker in ("password", "token", "secret", "private key", "ssh_key_path")):
            return "[redacted]"
        if "<html" in lowered or "</html" in lowered:
            return "[redacted-html]"
    return value


if __name__ == "__main__":
    raise SystemExit(main())
