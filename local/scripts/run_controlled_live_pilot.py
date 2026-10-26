from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGES_DIR = PROJECT_ROOT / "packages"
for candidate in (PROJECT_ROOT, PACKAGES_DIR):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from scripts.check_source_access_policy import main as check_source_access_policy_main
from scripts.live_data_preflight import DEFAULT_POLICY, run_preflight
from scripts.plan_live_data_pilot import write_live_pilot_plan
from cleaner_agent.agent import CleanerAgent
from cleaner_agent.features import FeatureBuilder
from common.db import get_session, init_db
from common.models import ModelFeature
from crawler_agent.access_policy import load_access_policies
from crawler_agent.connectors import CdcNoroStatConnector
from crawler_agent.connectors.cdc import CDC_NOROSTAT_LIVE_URL, CDC_NOROSTAT_SOURCE_KEY
from crawler_agent.connector_ingest import store_connector_documents, upsert_connector_source
from crawler_agent.live_fetch_guard import LiveFetchGuard
from crawler_agent.raw_document_store import RawDocumentStore
from crawler_optimizer_agent import CrawlerOptimizerAgent, summarize_optimizer_report
from data_quality_agent.report import build_data_quality_report, render_markdown_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare or execute a controlled CDC NoroSTAT live pilot.")
    parser.add_argument("--source-key", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--execute-live", action="store_true")
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--live-url", default=None)
    parser.add_argument("--terms-reviewed", action="store_true")
    parser.add_argument("--manual-reviewed", action="store_true")
    parser.add_argument("--robots-allowed", action="store_true")
    parser.add_argument("--max-requests", type=int, default=1)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--policy", default=DEFAULT_POLICY)
    parser.add_argument("--country", default="United States")
    parser.add_argument("--disease", default="norovirus")
    parser.add_argument("--output-dir", default="reports/live_pilot")
    parser.add_argument("--round-label", default="live")
    parser.add_argument("--compare-with-original", default=None)
    parser.add_argument("--compare-with-reprocess", default=None)
    args = parser.parse_args(argv)

    if args.dry_run and args.execute_live:
        result = _reject(args.source_key, "choose either --dry-run or --execute-live, not both")
        print(json.dumps(_safe_json(result), ensure_ascii=False, sort_keys=True))
        return 2
    if args.execute_live:
        result = run_controlled_live_pilot_live(
            source_key=args.source_key,
            policy_path=args.policy,
            country=args.country,
            disease=args.disease,
            output_dir=args.output_dir,
            database_url=args.database_url,
            live_url=args.live_url,
            allow_network=args.allow_network,
            terms_reviewed=args.terms_reviewed,
            manual_reviewed=args.manual_reviewed,
            robots_allowed=args.robots_allowed,
            max_requests=args.max_requests,
            round_label=args.round_label,
            compare_with_original=args.compare_with_original,
            compare_with_reprocess=args.compare_with_reprocess,
        )
        print(json.dumps(_safe_json(_public_cli_summary(result)), ensure_ascii=False, sort_keys=True))
        return 0 if result["status"] == "ok" else 1

    if not args.dry_run:
        result = {
            "status": "failed",
            "dry_run": False,
            "source_key": args.source_key,
            "live_execution_enabled": False,
            "error": "real live execution is intentionally disabled in this MVP step",
            "network_accessed": False,
            "warnings": [],
        }
        print(json.dumps(_safe_json(result), ensure_ascii=False, sort_keys=True))
        return 2

    result = run_controlled_live_pilot_dry_run(
        source_key=args.source_key,
        policy_path=args.policy,
        country=args.country,
        disease=args.disease,
        output_dir=args.output_dir,
        database_url=args.database_url,
    )
    print(json.dumps(_safe_json(result), ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "ok" else 1


def run_controlled_live_pilot_dry_run(
    *,
    source_key: str = "cdc_norostat",
    policy_path: str = DEFAULT_POLICY,
    country: str = "United States",
    disease: str = "norovirus",
    output_dir: str = "reports/live_pilot",
    database_url: str | None = None,
) -> dict[str, Any]:
    _ = database_url
    preflight = run_preflight(source_key=source_key, policy_path=policy_path)
    plan = write_live_pilot_plan(
        source_key=source_key,
        country=country,
        disease=disease,
        policy_path=policy_path,
        output_dir=output_dir,
        max_requests=1,
    )
    with contextlib.redirect_stdout(io.StringIO()):
        policy_check_exit = check_source_access_policy_main(["--policy", policy_path])
    smoke_import_ok = _smoke_test_import_ok()
    warnings: list[str] = []
    if preflight.get("status") != "ok":
        warnings.append("preflight failed")
    if plan.get("status") != "ok":
        warnings.append("plan failed")
    if policy_check_exit != 0:
        warnings.append("source access policy check failed")
    if not smoke_import_ok:
        warnings.append("smoke_test import failed")
    return {
        "status": "ok" if not warnings else "failed",
        "dry_run": True,
        "source_key": source_key,
        "preflight_ok": preflight.get("status") == "ok",
        "plan_created": plan.get("status") == "ok",
        "plan_json": plan.get("plan_json"),
        "plan_markdown": plan.get("plan_markdown"),
        "policy_check_ok": policy_check_exit == 0,
        "smoke_test_import_ok": smoke_import_ok,
        "live_execution_enabled": False,
        "next_step_required": "human approval before live fetch",
        "network_accessed": False,
        "warnings": warnings,
    }


def run_controlled_live_pilot(**kwargs) -> dict[str, Any]:
    """Compatibility wrapper for controlled live pilot callers.

    The wrapper delegates to the guarded live implementation and does not
    change any safety checks. Callers still need to pass allow_network and the
    explicit review flags for real network access.
    """
    return run_controlled_live_pilot_live(**kwargs)


def run_controlled_live_pilot_live(
    *,
    source_key: str,
    policy_path: str = DEFAULT_POLICY,
    country: str = "United States",
    disease: str = "norovirus",
    output_dir: str = "reports/live_pilot",
    database_url: str | None = None,
    live_url: str | None = None,
    allow_network: bool = False,
    terms_reviewed: bool = False,
    manual_reviewed: bool = False,
    robots_allowed: bool = False,
    max_requests: int = 1,
    transport: httpx.BaseTransport | None = None,
    round_label: str = "live",
    compare_with_original: str | None = None,
    compare_with_reprocess: str | None = None,
) -> dict[str, Any]:
    validation_error = _validate_live_args(
        source_key=source_key,
        live_url=live_url,
        allow_network=allow_network,
        terms_reviewed=terms_reviewed,
        manual_reviewed=manual_reviewed,
        robots_allowed=robots_allowed,
        max_requests=max_requests,
    )
    if validation_error:
        return _reject(source_key, validation_error)

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    normalized_round_label = _normalize_round_label(round_label)
    run_dir = _allocate_run_dir(Path(output_dir), normalized_round_label, timestamp)
    run_dir.mkdir(parents=True, exist_ok=True)
    db_path = run_dir / ("cdc_norostat_round2.sqlite" if normalized_round_label == "round2" else "cdc_norostat_live_pilot.sqlite")
    resolved_database_url = database_url or f"sqlite:///{db_path.as_posix()}"

    summary = _base_live_summary(
        source_key=source_key,
        url=live_url or CDC_NOROSTAT_LIVE_URL,
        run_dir=run_dir,
        database_url="sqlite",
        round_label=normalized_round_label,
    )
    try:
        preflight = run_preflight(source_key=source_key, policy_path=policy_path)
        summary["preflight"] = {
            "status": preflight.get("status"),
            "guardrail_denies_without_allow_network": preflight.get("guardrail_denies_without_allow_network"),
            "guardrail_allows_when_all_conditions_met": preflight.get("guardrail_allows_when_all_conditions_met"),
        }
        if preflight.get("status") != "ok":
            summary["status"] = "failed"
            summary["warnings"].append("preflight failed")
            return _finalize_live_outputs(summary, run_dir, resolved_database_url, country, disease)

        policies = load_access_policies(policy_path)
        guard = LiveFetchGuard(policies)
        guard_decision = guard.check(
            source_key,
            allow_network=allow_network,
            requested_url=live_url,
            robots_allowed=robots_allowed,
            terms_reviewed=terms_reviewed,
            manual_reviewed=manual_reviewed,
            request_count=max_requests,
        )
        summary["guardrail_allowed"] = bool(guard_decision.allowed)
        if not guard_decision.allowed:
            summary["status"] = "failed"
            summary["warnings"].append(f"guardrail denied live fetch: {guard_decision.reason}")
            return _finalize_live_outputs(summary, run_dir, resolved_database_url, country, disease)

        engine = init_db(resolved_database_url)
        engine.dispose()
        connector = CdcNoroStatConnector()
        summary["network_accessed"] = True
        documents = connector.fetch_live(
            url=live_url,
            allow_network=allow_network,
            access_guard=guard,
            robots_allowed=robots_allowed,
            terms_reviewed=terms_reviewed,
            manual_reviewed=manual_reviewed,
            request_count=max_requests,
            transport=transport,
            timeout_seconds=20.0,
            user_agent="infectious-intel-local/0.1 controlled-live-pilot",
        )
        response_summary = connector.last_live_response_summary or {}
        summary["http_status_code"] = response_summary.get("status_code")
        summary["content_length"] = response_summary.get("content_length")
        summary["content_hash"] = response_summary.get("content_hash")
        summary["content_type"] = response_summary.get("content_type")
        summary["connector_documents_parsed"] = len(documents)
        if not documents:
            summary["warnings"].append("connector parsed zero documents")

        with get_session(resolved_database_url) as session:
            source = upsert_connector_source(
                session,
                "cdc_norostat",
                base_url=live_url,
                status="pending_review",
            )
            results = store_connector_documents(
                source=source,
                documents=documents,
                store=RawDocumentStore(session),
            )
            summary["raw_documents_saved"] = sum(1 for result in results if result.created)
            summary["duplicates"] = len(results) - summary["raw_documents_saved"]
            cleaner_summary = CleanerAgent(session).process_pending(
                limit=100,
                country=country,
                disease=disease,
            )
            summary["cleaner"] = {
                "processed": int(cleaner_summary.get("processed", 0)),
                "events_created": int(cleaner_summary.get("events_created", 0)),
                "no_event": int(cleaner_summary.get("no_event", 0)),
                "errors": int(cleaner_summary.get("errors", 0)),
            }
            if summary["cleaner"]["events_created"] == 0:
                summary["warnings"].append("CleanerAgent produced zero events from the live pilot sample")
            try:
                feature_summary = FeatureBuilder(session).build(
                    country=country,
                    disease=disease,
                    horizons_days=(7, 28),
                    split_type="pilot",
                    replace_existing=True,
                )
                horizon_counts = _feature_counts_by_horizon(session)
                feature_rows_total = int(feature_summary.get("feature_rows", 0))
                summary["features"] = {
                    "feature_rows": feature_rows_total,
                    "feature_rows_total": feature_rows_total,
                    "feature_rows_h7": horizon_counts.get("7", 0),
                    "feature_rows_h28": horizon_counts.get("28", 0),
                    "saved": int(feature_summary.get("saved", 0)),
                    "updated": int(feature_summary.get("updated", 0)),
                    "warnings": [],
                }
                if feature_rows_total == 0:
                    summary["features"]["warnings"].append("not enough event history to build features")
                if horizon_counts.get("7", 0) == 0:
                    summary["features"]["warnings"].append("horizon=7 features were not generated")
                if horizon_counts.get("28", 0) == 0:
                    summary["features"]["warnings"].append("horizon=28 features were not generated")
            except Exception as exc:
                summary["features"] = {
                    "feature_rows": 0,
                    "feature_rows_total": 0,
                    "feature_rows_h7": 0,
                    "feature_rows_h28": 0,
                    "saved": 0,
                    "updated": 0,
                    "warnings": [f"FeatureBuilder skipped: {type(exc).__name__}"],
                }
            data_quality_paths = _write_data_quality_report(session, run_dir)
            summary["data_quality_report"] = data_quality_paths
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
            _write_json(run_dir / "crawler_optimizer_report.json", optimizer_summary)
            summary["crawler_optimizer"] = {
                "json": str(run_dir / "crawler_optimizer_report.json"),
                "failure_case_count": int(optimizer_summary.get("failure_case_count", 0)),
                "recommended_action_count": int(optimizer_summary.get("recommended_action_count", 0)),
                "connector_spec_count": int(optimizer_summary.get("connector_spec_count", 0)),
            }
        summary["status"] = "ok"
    except httpx.HTTPError as exc:
        summary["status"] = "failed"
        if hasattr(exc, "response") and exc.response is not None:
            response = exc.response
            summary["http_status_code"] = int(response.status_code)
            summary["content_length"] = len(response.content or b"")
            summary["content_hash"] = hashlib.sha256(response.content or b"").hexdigest()
            summary["content_type"] = response.headers.get("content-type")
        summary["warnings"].append(f"live fetch failed: {type(exc).__name__}")
    except Exception as exc:
        summary["status"] = "failed"
        summary["warnings"].append(f"live pilot failed: {type(exc).__name__}")

    summary["comparison"] = _build_round_comparison(
        summary=summary,
        compare_with_original=compare_with_original,
        compare_with_reprocess=compare_with_reprocess,
    )
    return _finalize_live_outputs(summary, run_dir, resolved_database_url, country, disease)


def _validate_live_args(
    *,
    source_key: str,
    live_url: str | None,
    allow_network: bool,
    terms_reviewed: bool,
    manual_reviewed: bool,
    robots_allowed: bool,
    max_requests: int,
) -> str | None:
    if source_key != CDC_NOROSTAT_SOURCE_KEY:
        return "only cdc_norostat is allowed for this controlled live pilot"
    if live_url != CDC_NOROSTAT_LIVE_URL:
        return "requested URL is not the single allowed CDC NoroSTAT URL"
    if not allow_network:
        return "--allow-network is required for the controlled live pilot"
    if max_requests != 1:
        return "max_requests must be exactly 1 for this controlled live pilot"
    if not terms_reviewed:
        return "terms_reviewed=True is required"
    if not manual_reviewed:
        return "manual_reviewed=True is required"
    if not robots_allowed:
        return "robots_allowed=True is required"
    return None


def _normalize_round_label(round_label: str | None) -> str:
    label = str(round_label or "live").strip().lower()
    return "round2" if label == "round2" else "live"


def _allocate_run_dir(output_dir: Path, round_label: str, timestamp: str) -> Path:
    prefix = "cdc_norostat_round2" if round_label == "round2" else "cdc_norostat_live"
    candidate = output_dir / f"{prefix}_{timestamp}"
    if not candidate.exists():
        return candidate
    for index in range(1, 100):
        numbered = output_dir / f"{prefix}_{timestamp}_{index:02d}"
        if not numbered.exists():
            return numbered
    raise FileExistsError("could not allocate a unique controlled live pilot directory")


def _feature_counts_by_horizon(session) -> dict[str, int]:
    counts: dict[str, int] = {}
    for horizon in session.scalars(select(ModelFeature.horizon_days)).all():
        key = str(int(horizon))
        counts[key] = counts.get(key, 0) + 1
    return counts


def _reject(source_key: str, reason: str) -> dict[str, Any]:
    return {
        "status": "failed",
        "dry_run": False,
        "source_key": source_key,
        "live_execution_enabled": False,
        "network_accessed": False,
        "warnings": [reason],
    }


def _base_live_summary(*, source_key: str, url: str, run_dir: Path, database_url: str, round_label: str = "live") -> dict[str, Any]:
    return {
        "status": "failed",
        "round_label": round_label,
        "source_key": source_key,
        "url": url,
        "request_count": 1,
        "guardrail_allowed": False,
        "http_status_code": None,
        "content_length": 0,
        "content_hash": None,
        "content_type": None,
        "connector_documents_parsed": 0,
        "raw_documents_saved": 0,
        "duplicates": 0,
        "cleaner": {"processed": 0, "events_created": 0, "no_event": 0, "errors": 0},
        "features": {
            "feature_rows": 0,
            "feature_rows_total": 0,
            "feature_rows_h7": 0,
            "feature_rows_h28": 0,
            "saved": 0,
            "updated": 0,
            "warnings": [],
        },
        "data_quality_report": {"json": None, "markdown": None},
        "crawler_optimizer": {
            "json": None,
            "failure_case_count": 0,
            "recommended_action_count": 0,
            "connector_spec_count": 0,
        },
        "output_dir": str(run_dir),
        "database": database_url,
        "live_execution_enabled": True,
        "production_prediction_started": False,
        "network_accessed": False,
        "comparison": {},
        "warnings": [],
    }


def _write_data_quality_report(session, run_dir: Path) -> dict[str, str]:
    report = build_data_quality_report(session, country="United States", disease="norovirus")
    report["limitations"] = list(report.get("limitations", [])) + [
        "Single-request CDC NoroSTAT live pilot only.",
        "This does not represent validated prediction accuracy and requires human review.",
    ]
    json_path = run_dir / "data_quality_report.json"
    md_path = run_dir / "data_quality_report.md"
    _write_json(json_path, report)
    md_path.write_text(render_markdown_report(report), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


def _build_round_comparison(
    *,
    summary: dict[str, Any],
    compare_with_original: str | None,
    compare_with_reprocess: str | None,
) -> dict[str, Any]:
    warnings: list[str] = []
    round1 = _round1_summary(Path(compare_with_original), warnings) if compare_with_original else {}
    reprocess = _reprocess_summary(Path(compare_with_reprocess), warnings) if compare_with_reprocess else {}
    features = summary.get("features") or {}
    optimizer = summary.get("crawler_optimizer") or {}
    round2 = {
        "raw_documents_saved": int(summary.get("raw_documents_saved") or 0),
        "events_created": int((summary.get("cleaner") or {}).get("events_created") or 0),
        "feature_rows_total": int(features.get("feature_rows_total") or features.get("feature_rows") or 0),
        "feature_rows_h7": int(features.get("feature_rows_h7") or 0),
        "feature_rows_h28": int(features.get("feature_rows_h28") or 0),
        "optimizer_failure_case_count": int(optimizer.get("failure_case_count") or 0),
    }
    if round1.get("content_hash") and summary.get("content_hash"):
        round2["content_same_as_round1"] = round1.get("content_hash") == summary.get("content_hash")
        if not round2["content_same_as_round1"]:
            warnings.append("Round 2 content hash differs from Round 1; this can happen when the public data table changes.")
    assessment = {
        "round2_matches_reprocess_expectation": (
            bool(reprocess)
            and round2["feature_rows_total"] == int(reprocess.get("feature_rows_total") or 0)
            and round2["optimizer_failure_case_count"] == int(reprocess.get("optimizer_failure_case_count") or 0)
        ),
        "feature_generation_fixed": round2["feature_rows_total"] > int(round1.get("feature_rows_total") or 0),
        "optimizer_false_positives_fixed": (
            round1.get("optimizer_failure_case_count") is not None
            and round2["optimizer_failure_case_count"] < int(round1.get("optimizer_failure_case_count") or 0)
        ),
    }
    return {
        "round1": round1,
        "reprocess": reprocess,
        "round2": round2,
        "assessment": assessment,
        "warnings": warnings,
    }


def _round1_summary(path: Path | None, warnings: list[str]) -> dict[str, Any]:
    if path is None:
        return {}
    live = _read_json(path / "live_fetch_summary.json", warnings)
    postmortem = _read_json(path / "postmortem_report.json", warnings)
    optimizer = _read_json(path / "crawler_optimizer_report.json", warnings)
    return {
        "raw_documents_saved": _int_or_zero(live.get("raw_documents_saved")),
        "events_created": _int_or_zero(_nested(live, ("cleaner", "events_created")) or _nested(postmortem, ("epidemic_events_summary", "total"))),
        "feature_rows_total": _int_or_zero(_nested(live, ("features", "feature_rows")) or _nested(postmortem, ("model_features_summary", "total"))),
        "optimizer_failure_case_count": _int_or_zero(optimizer.get("failure_case_count")),
        "content_hash": live.get("content_hash"),
    }


def _reprocess_summary(path: Path | None, warnings: list[str]) -> dict[str, Any]:
    if path is None:
        return {}
    comparison = _read_json(path / "comparison_with_original.json", warnings)
    summary = _read_json(path / "reprocess_summary.json", warnings)
    reprocessed = comparison.get("reprocessed") if isinstance(comparison.get("reprocessed"), dict) else {}
    horizon_counts = _nested(summary, ("features", "horizon_counts")) or {}
    optimizer = summary.get("crawler_optimizer") if isinstance(summary.get("crawler_optimizer"), dict) else {}
    return {
        "feature_rows_total": _int_or_zero(reprocessed.get("feature_rows_total") or _nested(summary, ("after_reprocess", "model_features_count"))),
        "feature_rows_h7": _int_or_zero(reprocessed.get("feature_rows_h7") or horizon_counts.get("7")),
        "feature_rows_h28": _int_or_zero(reprocessed.get("feature_rows_h28") or horizon_counts.get("28")),
        "optimizer_failure_case_count": _int_or_zero(reprocessed.get("crawler_optimizer_failure_case_count") or optimizer.get("failure_case_count")),
    }


def _read_json(path: Path, warnings: list[str]) -> dict[str, Any]:
    if not path.exists():
        warnings.append(f"comparison input missing: {path.name}")
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        warnings.append(f"comparison input invalid JSON: {path.name}")
        return {}


def _nested(payload: dict[str, Any], keys: tuple[str, ...]) -> Any:
    value: Any = payload
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _int_or_zero(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _finalize_live_outputs(
    summary: dict[str, Any],
    run_dir: Path,
    database_url: str,
    country: str,
    disease: str,
) -> dict[str, Any]:
    if not summary.get("data_quality_report", {}).get("json"):
        try:
            engine = init_db(database_url)
            engine.dispose()
            with get_session(database_url) as session:
                summary["data_quality_report"] = _write_data_quality_report(session, run_dir)
        except Exception as exc:
            summary["warnings"].append(f"data quality report skipped: {type(exc).__name__}")
    if not summary.get("crawler_optimizer", {}).get("json"):
        try:
            with get_session(database_url) as session:
                optimizer_report = CrawlerOptimizerAgent(session).analyze(
                    country=country,
                    disease=disease,
                    include_connector_specs=True,
                    apply_safe_actions=False,
                )
                optimizer_summary = summarize_optimizer_report(optimizer_report)
                optimizer_path = run_dir / "crawler_optimizer_report.json"
                _write_json(optimizer_path, optimizer_summary)
                summary["crawler_optimizer"] = {
                    "json": str(optimizer_path),
                    "failure_case_count": int(optimizer_summary.get("failure_case_count", 0)),
                    "recommended_action_count": int(optimizer_summary.get("recommended_action_count", 0)),
                    "connector_spec_count": int(optimizer_summary.get("connector_spec_count", 0)),
                }
        except Exception as exc:
            summary["warnings"].append(f"crawler optimizer report skipped: {type(exc).__name__}")
    rollback_path = run_dir / "rollback_commands.md"
    rollback_path.write_text(_rollback_markdown(run_dir), encoding="utf-8")
    summary["rollback_commands"] = str(rollback_path)
    if summary.get("comparison"):
        comparison_path = run_dir / "comparison_round1_reprocess_round2.json"
        _write_json(comparison_path, summary["comparison"])
        summary["comparison_report"] = str(comparison_path)
    _write_json(run_dir / "live_fetch_summary.json", summary)
    (run_dir / "live_fetch_summary.md").write_text(_summary_markdown(summary), encoding="utf-8")
    if summary.get("round_label") == "round2":
        _write_json(run_dir / "round2_live_fetch_summary.json", summary)
        (run_dir / "round2_live_fetch_summary.md").write_text(_summary_markdown(summary), encoding="utf-8")
    return summary


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(_safe_json(payload), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _rollback_markdown(run_dir: Path) -> str:
    db_name = "cdc_norostat_round2.sqlite" if "round2" in run_dir.name else "cdc_norostat_live_pilot.sqlite"
    return "\n".join(
        [
            "# CDC NoroSTAT Live Pilot Rollback",
            "",
            "This rollback is local-only and does not touch production systems.",
            "",
            "## Cleanup",
            "",
            f"- Delete this output directory after review: `{run_dir}`",
            f"- Delete this pilot SQLite file if present: `{run_dir / db_name}`",
            "- Confirm no production database URL was used.",
            "- Confirm no complete HTML body was saved.",
            "- Confirm no sensitive configuration values appear in reports.",
            "",
            "## Boundary",
            "",
            "This pilot does not validate prediction accuracy and requires human review.",
            "",
        ]
    )


def _summary_markdown(summary: dict[str, Any]) -> str:
    public = _public_cli_summary(summary)
    return "\n".join(
        [
            "# CDC NoroSTAT Controlled Live Pilot Summary",
            "",
            "This is a single-request live pilot summary. Complete HTML and raw text are not included.",
            "",
            "```json",
            json.dumps(_safe_json(public), ensure_ascii=False, indent=2, sort_keys=True),
            "```",
            "",
            "Results require human review and do not guarantee prediction accuracy.",
            "",
        ]
    )


def _public_cli_summary(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": summary.get("status"),
        "round_label": summary.get("round_label"),
        "source_key": summary.get("source_key"),
        "url": summary.get("url"),
        "request_count": summary.get("request_count"),
        "guardrail_allowed": summary.get("guardrail_allowed"),
        "http_status_code": summary.get("http_status_code"),
        "content_length": summary.get("content_length"),
        "content_hash": summary.get("content_hash"),
        "connector_documents_parsed": summary.get("connector_documents_parsed"),
        "raw_documents_saved": summary.get("raw_documents_saved"),
        "duplicates": summary.get("duplicates"),
        "cleaner": summary.get("cleaner"),
        "features": summary.get("features"),
        "data_quality_report": summary.get("data_quality_report"),
        "crawler_optimizer": summary.get("crawler_optimizer"),
        "comparison": summary.get("comparison"),
        "comparison_report": summary.get("comparison_report"),
        "rollback_commands": summary.get("rollback_commands"),
        "output_dir": summary.get("output_dir"),
        "production_prediction_started": summary.get("production_prediction_started"),
        "network_accessed": summary.get("network_accessed"),
        "live_execution_enabled": summary.get("live_execution_enabled"),
        "warnings": summary.get("warnings", []),
    }


def _smoke_test_import_ok() -> bool:
    try:
        __import__("scripts.smoke_test")
    except Exception:
        return False
    return True


def _safe_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _safe_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_safe_json(item) for item in value]
    if isinstance(value, tuple):
        return [_safe_json(item) for item in value]
    if isinstance(value, str):
        lowered = value.lower()
        if any(marker in lowered for marker in ("password", "token", "secret", "ssh_key_path", "private key")):
            return "[redacted]"
    return value


if __name__ == "__main__":
    raise SystemExit(main())
