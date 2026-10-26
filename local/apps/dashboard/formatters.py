from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from apps.dashboard.safety import sanitize_dict, truncate_text

RAW_OR_SENSITIVE_KEY_MARKERS = (
    "raw_text",
    "html",
    "full_html",
    "response_body",
    "prompt",
    "completion",
    "api_key",
    "token",
    "password",
    "secret",
    "metadata_json",
    "feature_json",
    "provenance_json",
    "lesson_json",
    "raw_response",
    "full_abstract",
    "raw_xml",
)

REPORT_KEY_MARKERS = (
    "path",
    "paths",
    "report",
    "summary_json",
    "summary_markdown",
    "review_queue",
    "connector_specs",
    "safety_audit",
    "rollback",
)

COUNT_KEY_MARKERS = (
    "count",
    "total",
    "seen",
    "created",
    "generated",
    "inserted",
    "updated",
    "persisted",
    "ready",
    "blocked",
    "failed",
    "passed",
    "rejected",
    "request",
)


def sanitize_dashboard_payload(payload: Any) -> Any:
    """Return a UI-safe payload without raw bodies, prompts, completions, or secrets."""
    if isinstance(payload, Mapping):
        safe: dict[str, Any] = {}
        for key, value in payload.items():
            key_text = str(key)
            if _is_raw_or_sensitive_key(key_text):
                continue
            safe[key_text] = sanitize_dashboard_payload(value)
        return sanitize_dict(safe)
    if isinstance(payload, list):
        return [sanitize_dashboard_payload(item) for item in payload]
    if isinstance(payload, tuple):
        return [sanitize_dashboard_payload(item) for item in payload]
    return sanitize_dict({"value": payload}).get("value")


def render_status_badge(status: Any) -> None:
    import streamlit as st

    text = str(status or "unknown").strip()
    normalized = text.lower()
    label = f"状态：{text}"
    if normalized in {"ok", "success", "completed", "healthy", "passed"}:
        st.success(label)
    elif normalized in {"failed", "error", "blocked"}:
        st.error(label)
    elif normalized in {"limited", "pending", "pending_review", "safe_failed", "warning"}:
        st.warning(label)
    else:
        st.info(label)


def render_metric_cards(metrics: Mapping[str, Any] | None) -> None:
    import streamlit as st

    items = [(str(key), _display_metric_value(value)) for key, value in (metrics or {}).items()]
    if not items:
        st.caption("暂无核心指标。")
        return
    if not hasattr(st, "columns") or not hasattr(st, "metric"):
        for label, value in items:
            st.caption(f"{_humanize_label(label)}：{value}")
        return
    columns = st.columns(min(4, max(1, len(items))))
    for index, (label, value) in enumerate(items):
        with columns[index % len(columns)]:
            st.metric(_humanize_label(label), value)


def render_report_links(paths: Mapping[str, Any] | Sequence[Any] | None) -> None:
    import streamlit as st

    rows = _report_path_rows(paths)
    if not rows:
        return
    st.caption("报告文件")
    st.dataframe(rows, use_container_width=True)


def render_safe_summary_panel(title: str, summary: Mapping[str, Any] | None) -> None:
    import streamlit as st

    safe = sanitize_dashboard_payload(summary or {})
    if title:
        st.subheader(title)
    if not isinstance(safe, dict) or not safe:
        st.caption("暂无结果。")
        return
    if safe.get("status") is not None:
        render_status_badge(safe.get("status"))
    metrics = _summary_metrics(safe)
    if metrics:
        render_metric_cards(metrics)
    messages = _summary_messages(safe)
    for message in messages:
        st.caption(message)
    render_report_links(_summary_paths(safe))


def render_candidate_table(candidates: Sequence[Mapping[str, Any]] | None) -> None:
    _render_rows("候选数据源", candidates, empty_message="暂无候选数据源。")


def render_review_queue_table(rows: Sequence[Mapping[str, Any]] | None) -> None:
    _render_rows("人工复核队列", rows, empty_message="暂无人工复核条目。")


def render_provider_diagnostics_table(rows: Sequence[Mapping[str, Any]] | None) -> None:
    _render_rows("Provider 诊断", rows, empty_message="暂无 provider 诊断。")


def render_quality_gate_summary(summary: Mapping[str, Any] | None) -> None:
    render_safe_summary_panel("质量闸门摘要", summary)


def render_metadata_check_summary(summary: Mapping[str, Any] | None) -> None:
    render_safe_summary_panel("Metadata 检查摘要", summary)


def render_acquisition_summary(summary: Mapping[str, Any] | None) -> None:
    render_safe_summary_panel("候选源接入摘要", summary)


def render_event_quality_summary(summary: Mapping[str, Any] | None) -> None:
    render_safe_summary_panel("事件质量诊断摘要", summary)


def render_deepseek_readiness_summary(summary: Mapping[str, Any] | None) -> None:
    render_safe_summary_panel("DeepSeek 就绪度摘要", summary)


def render_human_review_summary(summary: Mapping[str, Any] | None) -> None:
    render_safe_summary_panel("人工复核摘要", summary)


def render_pubmed_evidence_summary(summary: Mapping[str, Any] | None) -> None:
    render_safe_summary_panel("文献证据摘要", summary)


def render_milestone_summary(summary: Mapping[str, Any] | None) -> None:
    render_safe_summary_panel("里程碑摘要", summary)


def render_next_actions(actions: Sequence[Any] | None) -> None:
    import streamlit as st

    visible = [truncate_text(str(item), 160) for item in list(actions or []) if item]
    if not visible:
        return
    st.caption("建议下一步")
    for action in visible[:6]:
        st.markdown(f"- {action}")


def render_sanitized_debug_json(
    payload: Any,
    title: str = "Advanced debug details (sanitized)",
) -> None:
    import json
    import streamlit as st

    safe = sanitize_dashboard_payload(payload)
    with st.expander(title, expanded=False):
        st.caption("已过滤 raw_text、HTML、prompt、completion、response body、密钥和完整 JSON 字段。")
        st.code(json.dumps(safe, ensure_ascii=False, sort_keys=True, indent=2, default=str), language="json")


def format_risk_level(value: str | None) -> str:
    text = str(value or "").strip().lower()
    mapping = {
        "critical": "严重 Critical",
        "high": "高 High",
        "medium": "中 Medium",
        "moderate": "中 Medium",
        "low": "低 Low",
        "unknown": "未知 Unknown",
    }
    return mapping.get(text, "未知 Unknown")


def format_nullable_date(value: str | None, *, empty_label: str = "暂无") -> str:
    if value is None:
        return empty_label
    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "nat"}:
        return empty_label
    return truncate_text(text, 40) or empty_label


def compact_current_risk_summary(payload: dict) -> dict:
    payload = payload or {}
    prediction_date = payload.get("latest_prediction_date")
    target_date = payload.get("latest_target_date")
    model_version = payload.get("model_version")
    if prediction_date is None and (target_date or model_version):
        status_note = "已有预测记录，但缺少预测日期"
    elif not target_date and not model_version:
        status_note = "暂无近期预测记录"
    else:
        status_note = "仅作辅助分析，需要人工复核"
    return sanitize_dict(
        {
            "current_risk_level": format_risk_level(payload.get("current_risk_level")),
            "latest_prediction_date": format_nullable_date(prediction_date),
            "latest_target_date": format_nullable_date(target_date, empty_label="暂无目标日期"),
            "model_version": truncate_text(model_version, 80) or "暂无",
            "status_note": truncate_text(payload.get("status_note") or payload.get("explanation") or status_note, 120),
        }
    )


def compact_source_health_summary(payload: dict) -> list[dict]:
    summary = summarize_source_health(payload or {})
    rows = [
        {"status": "已启用 active", "count": summary["active"]},
        {"status": "待审核 pending_review", "count": summary["pending_review"]},
        {"status": "降级 degraded", "count": summary["degraded"]},
        {"status": "禁用 disabled", "count": summary["disabled"]},
    ]
    return [sanitize_dict(row) for row in rows]


def compact_feedback_summary(payload: dict) -> list[dict]:
    summary = feedback_counts(payload or {})
    rows = [
        {"verdict": "反馈总数", "count": summary["feedback_count"]},
        {"verdict": "成功 success", "count": summary["success"]},
        {"verdict": "部分成功 partial_success", "count": summary["partial_success"]},
        {"verdict": "失败 failure", "count": summary["failure"]},
        {"verdict": "未知 unknown", "count": summary["unknown"]},
    ]
    return [sanitize_dict(row) for row in rows]


def rows_from_recent_events_and_predictions(events_payload, predictions_payload) -> dict:
    events = _payload_rows(events_payload, "events")
    predictions = _payload_rows(predictions_payload, "predictions")
    events_rows = []
    for event in events[:20]:
        if not isinstance(event, dict):
            continue
        events_rows.append(
            sanitize_dict(
                {
                    "country": truncate_text(event.get("country"), 60) or "暂无",
                    "disease": truncate_text(event.get("disease"), 60) or "暂无",
                    "iso_week": truncate_text(event.get("iso_week"), 24) or format_nullable_date(event.get("event_date")),
                    "cases": event.get("cases", "暂无"),
                    "outbreaks_count": event.get("outbreaks_count", "暂无"),
                    "confidence_score": event.get("confidence_score", "暂无"),
                }
            )
        )
    prediction_rows = []
    for prediction in predictions[:20]:
        if not isinstance(prediction, dict):
            continue
        probability = _nested_value(prediction, "predicted_outbreak_probability")
        prediction_rows.append(
            sanitize_dict(
                {
                    "target_date": format_nullable_date(prediction.get("target_date")),
                    "predicted_cases": prediction.get("predicted_cases", prediction.get("predicted_value", "暂无")),
                    "probability": probability if probability is not None else "暂无",
                    "risk_level": format_risk_level(prediction.get("risk_level") or _risk_from_probability(probability)),
                    "model_version": truncate_text(prediction.get("model_version"), 80) or "暂无",
                }
            )
        )
    return {"events_rows": events_rows, "prediction_rows": prediction_rows}


def summarize_risk(predictions: list[dict]) -> dict:
    if not predictions:
        return {
            "current_risk_level": "unknown",
            "latest_prediction_date": None,
            "latest_target_date": None,
            "model_version": None,
        }
    latest = sorted(predictions, key=lambda item: str(item.get("target_date") or item.get("created_at") or ""))[-1]
    probability = _nested_value(latest, "predicted_outbreak_probability")
    risk_level = latest.get("risk_level") or _risk_from_probability(probability)
    return {
        "current_risk_level": risk_level,
        "latest_prediction_date": latest.get("created_at"),
        "latest_target_date": latest.get("target_date"),
        "model_version": latest.get("model_version"),
    }


def build_time_series_rows(events: list[dict], predictions: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for event in events or []:
        rows.append(
            {
                "kind": "event",
                "date": event.get("event_date") or event.get("target_date"),
                "country": event.get("country"),
                "disease": event.get("disease"),
                "value": event.get("cases"),
            }
        )
    for prediction in predictions or []:
        rows.append(
            {
                "kind": "prediction",
                "date": prediction.get("target_date"),
                "country": prediction.get("country"),
                "disease": prediction.get("disease"),
                "value": prediction.get("predicted_value"),
            }
        )
    return [sanitize_dict(row) for row in rows]


def summarize_source_health(source_health: dict) -> dict:
    source_health = source_health or {}
    by_status = source_health.get("by_status") if isinstance(source_health.get("by_status"), dict) else {}
    return {
        "total": int(source_health.get("total") or 0),
        "active": int(by_status.get("active") or 0),
        "pending_review": int(by_status.get("pending_review") or 0),
        "degraded": int(by_status.get("degraded") or 0),
        "disabled": int(by_status.get("disabled") or 0),
        "recent_failed_sources": int(source_health.get("recent_failed_sources") or 0),
    }


def compact_source_discovery_summary(payload: dict) -> dict:
    payload = payload or {}
    return sanitize_dict(
        {
            "status": payload.get("status"),
            "provider": payload.get("provider"),
            "providers_attempted": payload.get("providers_attempted") or [],
            "true_provider_accessed": payload.get("true_provider_accessed"),
            "country": payload.get("country"),
            "disease": payload.get("disease"),
            "failure_type": payload.get("failure_type"),
            "search_results_seen": int(payload.get("search_results_seen") or 0),
            "candidates_found": int(payload.get("candidates_found") or 0),
            "candidates_inserted_or_updated": int(payload.get("candidates_inserted_or_updated") or 0),
            "skipped": int(payload.get("skipped") or 0),
            "connector_specs_generated": int(payload.get("connector_specs_generated") or 0),
            "network_accessed": bool(payload.get("network_accessed", False)),
            "request_count": payload.get("request_count"),
            "candidate_urls_accessed": payload.get("candidate_urls_accessed"),
            "follow_links": payload.get("follow_links"),
            "retry_allowed": payload.get("retry_allowed"),
            "cooldown_recommended": payload.get("cooldown_recommended"),
            "warnings": payload.get("warnings") or [],
            "limitations": payload.get("limitations"),
        }
    )


def rows_from_source_discovery_candidates(payload: dict) -> list[dict]:
    rows: list[dict] = []
    for candidate in list((payload or {}).get("top_candidates") or []):
        if not isinstance(candidate, dict):
            continue
        rows.append(
            sanitize_dict(
                {
                    "name": truncate_text(candidate.get("name"), 80),
                    "base_url": truncate_text(candidate.get("base_url"), 120),
                    "domain": truncate_text(candidate.get("domain"), 80),
                    "source_type": candidate.get("source_type"),
                    "source_score": candidate.get("source_score"),
                    "requires_human_review": candidate.get("requires_human_review", True),
                }
            )
        )
    return rows


def rows_from_source_discovery_connector_specs(payload: dict) -> list[dict]:
    rows: list[dict] = []
    specs = (payload or {}).get("connector_specs") or []
    for spec in list(specs):
        if not isinstance(spec, dict):
            continue
        rows.append(
            sanitize_dict(
                {
                    "connector_name": truncate_text(spec.get("connector_name"), 80),
                    "source_name": truncate_text(spec.get("source_name"), 80),
                    "source_type": spec.get("source_type"),
                    "parser_strategy": truncate_text(spec.get("parser_strategy"), 80),
                    "sample_url_count": int(spec.get("sample_url_count") or 0),
                    "requires_human_review": spec.get("requires_human_review", True),
                    "test_fixture_required": spec.get("test_fixture_required", True),
                }
            )
        )
    return rows


def compact_deepseek_readiness_summary(payload: dict) -> list[dict]:
    payload = payload or {}
    return [
        {"metric": "total records", "value": int(payload.get("total_records") or 0)},
        {"metric": "evidence only", "value": int(payload.get("evidence_only_count") or 0)},
        {"metric": "event ready", "value": int(payload.get("event_ready_count") or 0)},
        {"metric": "feature ready", "value": int(payload.get("feature_ready_count") or 0)},
        {"metric": "visualization ready", "value": int(payload.get("visualization_ready_count") or 0)},
        {"metric": "requires human review", "value": int(payload.get("requires_human_review_count") or 0)},
    ]


def rows_from_deepseek_readiness_review(payload: dict) -> list[dict]:
    rows: list[dict] = []
    for row in (payload or {}).get("records") or []:
        if not isinstance(row, dict):
            continue
        rows.append(
            sanitize_dict(
                {
                    "record_id": row.get("record_id"),
                    "disease": truncate_text(row.get("disease"), 80),
                    "country": truncate_text(row.get("country"), 80),
                    "source_domain": truncate_text(row.get("source_domain"), 120),
                    "readiness_class": row.get("readiness_class"),
                    "event_ready": row.get("event_ready"),
                    "feature_ready": row.get("feature_ready"),
                    "visualization_ready": row.get("visualization_ready"),
                    "missing_fields": ", ".join(row.get("missing_fields") or []),
                    "blocker_types": ", ".join(row.get("blocker_types") or []),
                    "recommended_action": truncate_text(row.get("recommended_action"), 180),
                }
            )
        )
    return rows


def compact_deepseek_human_review_promotion_summary(payload: dict) -> dict:
    result = (payload or {}).get("result") if isinstance((payload or {}).get("result"), dict) else payload or {}
    return sanitize_dict(
        {
            "imported_annotations": result.get("imported_annotations", 0),
            "approved_event_count": result.get("approved_event_count", 0),
            "blocked_count": result.get("blocked_count", 0),
            "promoted_epidemic_events": result.get("promoted_epidemic_events", 0),
            "feature_generation_allowed": result.get("feature_generation_allowed", False),
            "feature_eligibility_blockers": result.get("feature_eligibility_blockers") or [],
        }
    )


def rows_from_deepseek_promotion_decisions(payload: dict) -> list[dict]:
    result = (payload or {}).get("result") if isinstance((payload or {}).get("result"), dict) else payload or {}
    rows: list[dict] = []
    for decision in result.get("decisions") or []:
        if not isinstance(decision, dict):
            continue
        rows.append(
            sanitize_dict(
                {
                    "record_id": decision.get("record_id"),
                    "reviewer_decision": decision.get("reviewer_decision"),
                    "gate_status": decision.get("gate_status"),
                    "blocker_reason": ", ".join(decision.get("blocker_reason") or []),
                    "promoted_event_id": decision.get("promoted_event_id"),
                }
            )
        )
    return rows


def rows_from_deepseek_annotation_workspace(payload: dict) -> list[dict]:
    rows: list[dict] = []
    for row in (payload or {}).get("rows") or []:
        if not isinstance(row, dict):
            continue
        rows.append(
            sanitize_dict(
                {
                    "record_id": row.get("record_id"),
                    "source_domain": truncate_text(row.get("source_domain"), 120),
                    "readiness_class": row.get("readiness_class"),
                    "missing_fields": ", ".join(row.get("missing_fields") or []),
                    "suggested_review_action": truncate_text(row.get("suggested_review_action"), 180),
                    "reviewer_decision": row.get("reviewer_decision"),
                    "confirmed_event_date": row.get("confirmed_event_date"),
                    "metric_type": row.get("metric_type"),
                    "metric_value": row.get("metric_value"),
                    "source_grounding_confirmed": row.get("source_grounding_confirmed"),
                    "gate_preview": row.get("gate_preview"),
                }
            )
        )
    return rows


def compact_live_source_search_summary(payload: dict) -> dict:
    payload = payload or {}
    positive_yield = payload.get("positive_yield_validation") if isinstance(payload.get("positive_yield_validation"), dict) else {}
    return sanitize_dict(
        {
            "status": payload.get("status"),
            "run_id": payload.get("run_id"),
            "country": payload.get("country"),
            "disease": payload.get("disease"),
            "providers_planned": payload.get("providers_planned") or [],
            "providers_used": payload.get("providers_used") or [],
            "request_count_total": payload.get("request_count_total", 0),
            "candidates_seen": payload.get("candidates_seen", 0),
            "candidates_after_dedupe": payload.get("candidates_after_dedupe", 0),
            "pending_review_inserted": payload.get("pending_review_inserted", 0),
            "connector_specs_generated": payload.get("connector_specs_generated", 0),
            "quality_gate_passed": payload.get("quality_gate_passed", 0),
            "quality_gate_rejected": payload.get("quality_gate_rejected", 0),
            "quality_gate_deprioritized": payload.get("quality_gate_deprioritized", 0),
            "metadata_check_executed": payload.get("metadata_check_executed", False),
            "metadata_check_request_count": payload.get("metadata_check_request_count", 0),
            "metadata_confirmed_data_sources": payload.get("metadata_confirmed_data_sources", 0),
            "metadata_rejected_non_data_sources": payload.get("metadata_rejected_non_data_sources", 0),
            "refined_top_domains": payload.get("refined_top_domains") or [],
            "positive_yield_achieved": positive_yield.get("achieved_positive_yield"),
            "request_budget_respected": positive_yield.get("request_budget_respected"),
            "candidate_urls_visited": payload.get("candidate_urls_visited", False),
            "no_follow_links": payload.get("no_follow_links", True),
            "top_domains": payload.get("top_domains") or [],
            "source_type_distribution": payload.get("source_type_distribution") or {},
            "providers_skipped": payload.get("providers_skipped") or [],
            "failure_types": payload.get("failure_types") or {},
            "warnings": payload.get("warnings") or [],
        }
    )


def rows_from_live_source_search_provider_diagnostics(payload: dict) -> list[dict]:
    rows: list[dict] = []
    for item in list((payload or {}).get("provider_failure_diagnostics") or []):
        if not isinstance(item, dict):
            continue
        rows.append(
            sanitize_dict(
                {
                    "provider": item.get("provider"),
                    "status": item.get("status"),
                    "failure_type": item.get("failure_type"),
                    "http_status_code": item.get("http_status_code"),
                    "request_count": item.get("request_count"),
                    "network_accessed": item.get("network_accessed"),
                    "safe_failure": item.get("safe_failure"),
                    "candidate_urls_visited": item.get("candidate_urls_visited"),
                    "follow_links": item.get("follow_links"),
                }
            )
        )
    return rows


def rows_from_live_source_search_review_queue(payload: dict) -> list[dict]:
    rows: list[dict] = []
    for item in list((payload or {}).get("review_queue") or []):
        if not isinstance(item, dict):
            continue
        rows.append(
            sanitize_dict(
                {
                    "title": truncate_text(item.get("title"), 80),
                    "domain": truncate_text(item.get("domain"), 80),
                    "source_score": item.get("source_score"),
                    "refined_score": item.get("refined_score"),
                    "quality_gate_status": item.get("quality_gate_status"),
                    "metadata_check_status": item.get("metadata_check_status"),
                    "data_source_likelihood": item.get("data_source_likelihood"),
                    "source_type": item.get("source_type"),
                    "parser_strategy": item.get("parser_strategy"),
                    "recommended_action": item.get("recommended_action"),
                    "registry_status": item.get("registry_status"),
                    "requires_human_review": item.get("requires_human_review", True),
                }
            )
        )
    return rows


def rows_from_live_source_search_metadata_results(payload: dict) -> list[dict]:
    rows: list[dict] = []
    for item in list((payload or {}).get("metadata_check_results") or (payload or {}).get("metadata_check_results", []) or (payload or {}).get("metadata_check_results") or []):
        if not isinstance(item, dict):
            continue
        rows.append(
            sanitize_dict(
                {
                    "domain": truncate_text(item.get("domain"), 80),
                    "status": item.get("status"),
                    "http_status_code": item.get("http_status_code"),
                    "content_type": truncate_text(item.get("content_type"), 80),
                    "data_source_likelihood": item.get("data_source_likelihood"),
                    "detected_source_type": item.get("detected_source_type"),
                    "parser_strategy_hint": item.get("parser_strategy_hint"),
                    "recommended_action": truncate_text(item.get("recommended_action"), 100),
                }
            )
        )
    return rows


def compact_candidate_acquisition_summary(payload: dict) -> dict:
    payload = payload or {}
    return sanitize_dict(
        {
            "status": payload.get("status"),
            "candidate_title": truncate_text(payload.get("candidate_title"), 100),
            "candidate_domain": truncate_text(payload.get("candidate_domain"), 80),
            "request_count": payload.get("request_count", 0),
            "network_accessed": payload.get("network_accessed", False),
            "follow_links": payload.get("follow_links", False),
            "files_downloaded": payload.get("files_downloaded", False),
            "full_body_saved": payload.get("full_body_saved", False),
            "content_type": truncate_text(payload.get("content_type"), 80),
            "parser_strategy_used": payload.get("parser_strategy_used"),
            "raw_documents_saved": payload.get("raw_documents_saved", 0),
            "epidemic_events_created": payload.get("epidemic_events_created", 0),
            "event_extraction_status": payload.get("event_extraction_status"),
            "source_registry_status": payload.get("source_registry_status"),
            "recommended_next_step": truncate_text(payload.get("recommended_next_step"), 160),
            "warnings": payload.get("warnings") or [],
        }
    )


def compact_acquisition_quality_summary(payload: dict) -> dict:
    payload = payload or {}
    report = payload.get("quality_report") if isinstance(payload.get("quality_report"), dict) else payload
    return sanitize_dict(
        {
            "status": report.get("status"),
            "raw_documents_count": report.get("raw_documents_count", 0),
            "epidemic_events_count": report.get("epidemic_events_count", 0),
            "model_features_count": report.get("model_features_count", 0),
            "feature_ready_event_count": report.get("feature_ready_event_count", 0),
            "event_quality_status": report.get("event_quality_status"),
            "feature_eligible": report.get("feature_eligible"),
            "blocker_types": report.get("blocker_types") or [],
            "recommended_next_action": truncate_text(report.get("recommended_next_action"), 180),
        }
    )


def rows_from_live_source_search_connector_specs(payload: dict) -> list[dict]:
    rows: list[dict] = []
    for spec in list((payload or {}).get("connector_specs") or []):
        if not isinstance(spec, dict):
            continue
        rows.append(
            sanitize_dict(
                {
                    "connector_name": truncate_text(spec.get("connector_name"), 80),
                    "source_name": truncate_text(spec.get("source_name"), 80),
                    "source_type": spec.get("source_type"),
                    "parser_strategy": spec.get("parser_strategy"),
                    "recommended_action": spec.get("recommended_action"),
                    "auto_enable": spec.get("auto_enable"),
                    "registry_status": spec.get("registry_status"),
                }
            )
        )
    return rows


def feedback_counts(summary: dict) -> dict:
    summary = summary or {}
    by_status = summary.get("by_status") if isinstance(summary.get("by_status"), dict) else {}
    return {
        "feedback_count": int(summary.get("feedback_count") or 0),
        "success": int(by_status.get("success") or 0),
        "partial_success": int(by_status.get("partial_success") or 0),
        "failure": int(by_status.get("failure") or 0),
        "unknown": int(by_status.get("unknown") or 0),
    }


def minimum_data_summary(result: dict) -> dict:
    result = result or {}
    return sanitize_dict(
        {
            "minimum_feature_set": result.get("minimum_feature_set") or [],
            "minimum_history_weeks": result.get("minimum_history_weeks"),
            "expected_score": result.get("expected_score"),
            "confidence_interval": result.get("confidence_interval"),
            "cost_summary": result.get("cost_summary") or {},
            "explanation": result.get("explanation"),
        }
    )


def compact_optimizer_summary(payload: dict) -> dict:
    payload = _optimizer_payload(payload)
    return sanitize_dict(
        {
            "status": payload.get("status"),
            "sources_analyzed": payload.get("sources_analyzed", 0),
            "documents_analyzed": payload.get("documents_analyzed", 0),
            "failure_case_count": payload.get("failure_case_count", 0),
            "recommended_action_count": payload.get("recommended_action_count", 0),
            "connector_spec_count": payload.get("connector_spec_count", 0),
            "top_failure_types": payload.get("top_failure_types") or [],
            "warnings": payload.get("warnings") or [],
            "limitations": payload.get("limitations"),
        }
    )


def rows_from_optimizer_failure_cases(payload: dict) -> list[dict]:
    payload = _optimizer_payload(payload)
    rows: list[dict] = []
    for case in list(payload.get("failure_cases") or []):
        if not isinstance(case, dict):
            continue
        rows.append(
            sanitize_dict(
                {
                    "failure_type": case.get("failure_type"),
                    "severity": case.get("severity"),
                    "confidence": case.get("confidence"),
                    "source_name": truncate_text(case.get("source_name"), 80),
                    "source_id": case.get("source_id"),
                    "raw_document_id": case.get("raw_document_id"),
                    "url": truncate_text(case.get("url"), 120),
                }
            )
        )
    return rows


def rows_from_optimizer_actions(payload: dict) -> list[dict]:
    payload = _optimizer_payload(payload)
    rows: list[dict] = []
    for action in list(payload.get("recommended_actions") or []):
        if not isinstance(action, dict):
            continue
        rows.append(
            sanitize_dict(
                {
                    "action_type": action.get("action_type"),
                    "target_type": action.get("target_type"),
                    "target_id": action.get("target_id"),
                    "reason": truncate_text(action.get("reason"), 140),
                    "confidence": action.get("confidence"),
                    "risk_level": action.get("risk_level"),
                    "requires_human_review": action.get("requires_human_review"),
                }
            )
        )
    return rows


def rows_from_connector_specs(payload: dict) -> list[dict]:
    payload = _optimizer_payload(payload)
    specs = payload.get("connector_specs")
    if specs is None and isinstance(payload.get("connector_spec"), dict):
        specs = [payload.get("connector_spec")]
    rows: list[dict] = []
    for spec in list(specs or []):
        if not isinstance(spec, dict):
            continue
        rows.append(
            sanitize_dict(
                {
                    "connector_name": spec.get("connector_name"),
                    "source_name": truncate_text(spec.get("source_name"), 80),
                    "source_type": spec.get("source_type"),
                    "parser_strategy": spec.get("parser_strategy"),
                    "expected_fields": ", ".join(str(item) for item in list(spec.get("expected_fields") or [])[:12]),
                    "sample_url_count": spec.get("sample_url_count", len(spec.get("sample_urls") or [])),
                    "requires_human_review": spec.get("requires_human_review"),
                    "test_fixture_required": spec.get("test_fixture_required"),
                    "live_fetch_supported": spec.get("live_fetch_supported"),
                }
            )
        )
    return rows


def rows_from_access_policies(payload: dict) -> list[dict]:
    rows: list[dict] = []
    for policy in list((payload or {}).get("policies") or []):
        if not isinstance(policy, dict):
            continue
        rows.append(
            sanitize_dict(
                {
                    "source": policy.get("source"),
                    "name": truncate_text(policy.get("name"), 80),
                    "allowed_access_method": policy.get("allowed_access_method"),
                    "live_fetch_default_enabled": policy.get("live_fetch_default_enabled"),
                    "requires_allow_network_flag": policy.get("requires_allow_network_flag"),
                    "robots_check_required": policy.get("robots_check_required"),
                    "terms_review_required": policy.get("terms_review_required"),
                    "manual_review_required": policy.get("manual_review_required"),
                    "max_requests_per_run": policy.get("max_requests_per_run"),
                    "min_interval_seconds": policy.get("min_interval_seconds"),
                    "allowed_domains": ", ".join(str(item) for item in list(policy.get("allowed_domains") or [])),
                }
            )
        )
    return rows


def compact_access_policy_summary(payload: dict) -> dict:
    payload = payload or {}
    return sanitize_dict(
        {
            "status": payload.get("status"),
            "policies_checked": payload.get("policies_checked"),
            "errors_count": len(payload.get("errors") or []),
            "limitations": payload.get("limitations"),
        }
    )


def compact_access_decision(payload: dict) -> dict:
    payload = payload or {}
    decision = payload.get("decision") if isinstance(payload.get("decision"), dict) else {}
    policy_summary = decision.get("policy_summary") if isinstance(decision.get("policy_summary"), dict) else {}
    return sanitize_dict(
        {
            "allowed": decision.get("allowed"),
            "reason": decision.get("reason"),
            "warnings": decision.get("warnings") or [],
            "source": policy_summary.get("source"),
            "name": policy_summary.get("name"),
            "robots_check_required": policy_summary.get("robots_check_required"),
            "terms_review_required": policy_summary.get("terms_review_required"),
            "manual_review_required": policy_summary.get("manual_review_required"),
            "max_requests_per_run": policy_summary.get("max_requests_per_run"),
            "audit_summary": payload.get("audit_summary") or {},
            "limitations": payload.get("limitations"),
        }
    )


def compact_data_quality_summary(payload: dict) -> dict:
    payload = payload or {}
    profile = payload.get("profile") if isinstance(payload.get("profile"), dict) else {}
    return sanitize_dict(
        {
            "source_count": _row_count(profile.get("source_registry")),
            "raw_document_count": _row_count(profile.get("raw_documents")),
            "event_count": _row_count(profile.get("epidemic_events")),
            "feature_count": _row_count(profile.get("model_features")),
            "prediction_count": _row_count(profile.get("predictions")),
            "feedback_count": _row_count(profile.get("human_feedback")),
            "experience_count": _row_count(profile.get("model_experience_bank")),
            "warnings_count": len(profile.get("overall_warnings") or []),
            "limitations": payload.get("limitations"),
        }
    )


def rows_from_data_quality_tables(payload: dict) -> list[dict]:
    payload = payload or {}
    profile = payload.get("profile") if isinstance(payload.get("profile"), dict) else {}
    rows: list[dict] = []
    for name in (
        "source_registry",
        "raw_documents",
        "epidemic_events",
        "model_features",
        "predictions",
        "human_feedback",
        "model_experience_bank",
    ):
        summary = profile.get(name) if isinstance(profile.get(name), dict) else {}
        metrics = summary.get("key_metrics") if isinstance(summary.get("key_metrics"), dict) else {}
        rows.append(
            sanitize_dict(
                {
                    "table": name,
                    "row_count": summary.get("row_count", 0),
                    "warnings": len(summary.get("warnings") or []),
                    "key_metric_names": ", ".join(list(metrics)[:10]),
                }
            )
        )
    return rows


def compact_label_audit(payload: dict) -> dict:
    audit = payload.get("label_audit") if isinstance((payload or {}).get("label_audit"), dict) else {}
    summary = audit.get("summary") if isinstance(audit.get("summary"), dict) else {}
    return sanitize_dict(
        {
            "checked_features": audit.get("checked_features"),
            "horizon_days_checked": audit.get("horizon_days_checked") or [],
            "leakage_warnings": len(audit.get("leakage_warnings") or []),
            "missing_target_warnings": len(audit.get("missing_target_warnings") or []),
            "inconsistent_rows": len(audit.get("inconsistent_rows") or []),
            "requires_human_review": summary.get("requires_human_review"),
            "limitations": payload.get("limitations") or summary.get("limitations"),
        }
    )


def _nested_value(payload: dict, key: str) -> Any:
    if key in payload:
        return payload[key]
    for nested_name in ("metrics_json", "provenance_json", "prediction_interval_json"):
        nested = payload.get(nested_name)
        if isinstance(nested, dict) and key in nested:
            return nested[key]
    return None


def _optimizer_payload(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return {}
    summary = payload.get("summary")
    if isinstance(summary, dict) and "failure_case_count" in summary:
        return summary
    return payload


def _row_count(summary: Any) -> int:
    if not isinstance(summary, dict):
        return 0
    return int(summary.get("row_count") or 0)


def _payload_rows(payload: Any, key: str) -> list:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get(key), list):
        return payload[key]
    return []


def _risk_from_probability(probability: Any) -> str:
    try:
        value = float(probability)
    except (TypeError, ValueError):
        return "unknown"
    if value >= 0.7:
        return "high"
    if value >= 0.4:
        return "medium"
    return "low"


def _is_raw_or_sensitive_key(key: str) -> bool:
    normalized = key.lower()
    return any(marker in normalized for marker in RAW_OR_SENSITIVE_KEY_MARKERS)


def _summary_metrics(payload: Mapping[str, Any]) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    priority = (
        "country",
        "disease",
        "request_count_total",
        "request_count",
        "deepseek_call_count",
        "page_read_request_count",
        "candidates_seen",
        "candidates_after_dedupe",
        "pending_review_inserted",
        "connector_specs_generated",
        "raw_documents_created",
        "raw_documents_saved",
        "epidemic_events_created",
        "model_features_created",
        "records_extracted",
        "records_persisted",
        "evidence_only_count",
        "event_ready_count",
        "feature_ready_count",
        "visualization_ready_count",
        "requires_human_review_count",
        "blocked_count",
        "approved_event_count",
        "promoted_epidemic_events",
    )
    for key in priority:
        if key in payload:
            metrics[key] = payload.get(key)
    for key, value in payload.items():
        if len(metrics) >= 8:
            break
        if key in metrics or key in {"status", "warnings", "limitations", "paths", "next_actions"}:
            continue
        if _looks_like_metric_key(key, value):
            metrics[key] = value
    return metrics


def _summary_messages(payload: Mapping[str, Any]) -> list[str]:
    messages: list[str] = []
    for key in ("failure_type", "cleaning_status", "event_extraction_status", "recommended_next_step"):
        value = payload.get(key)
        if value not in (None, "", []):
            messages.append(f"{_humanize_label(key)}：{truncate_text(str(value), 180)}")
    warnings = payload.get("warnings")
    if isinstance(warnings, list) and warnings:
        messages.append("警告：" + "；".join(truncate_text(str(item), 120) or "" for item in warnings[:3]))
    limitations = payload.get("limitations")
    if limitations:
        messages.append("限制：" + (truncate_text(str(limitations), 220) or ""))
    return messages


def _summary_paths(payload: Mapping[str, Any]) -> Any:
    if isinstance(payload.get("paths"), (dict, list)):
        return payload.get("paths")
    paths: dict[str, Any] = {}
    for key, value in payload.items():
        if _looks_like_report_key(key) and value:
            paths[key] = value
    return paths


def _report_path_rows(paths: Mapping[str, Any] | Sequence[Any] | None) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if isinstance(paths, Mapping):
        iterator = paths.items()
    elif isinstance(paths, Sequence) and not isinstance(paths, (str, bytes)):
        iterator = [(f"report_{index + 1}", item) for index, item in enumerate(paths)]
    else:
        return rows
    for key, value in iterator:
        if isinstance(value, Mapping):
            rows.extend(_report_path_rows(value))
            continue
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            rows.extend(_report_path_rows(value))
            continue
        if value in (None, ""):
            continue
        text = str(value)
        if _is_raw_or_sensitive_key(str(key)):
            continue
        rows.append({"名称": _humanize_label(str(key)), "路径": truncate_text(text, 180) or ""})
    return rows[:20]


def _render_rows(title: str, rows: Sequence[Mapping[str, Any]] | None, *, empty_message: str) -> None:
    import streamlit as st

    st.subheader(title)
    safe_rows = [sanitize_dashboard_payload(dict(row)) for row in list(rows or []) if isinstance(row, Mapping)]
    if not safe_rows:
        st.caption(empty_message)
        return
    st.dataframe(safe_rows, use_container_width=True)


def _looks_like_metric_key(key: str, value: Any) -> bool:
    normalized = key.lower()
    if isinstance(value, (int, float, bool)):
        return any(marker in normalized for marker in COUNT_KEY_MARKERS)
    if isinstance(value, str) and normalized in {"country", "disease", "provider", "run_id"}:
        return True
    return False


def _looks_like_report_key(key: str) -> bool:
    normalized = key.lower()
    return any(marker in normalized for marker in REPORT_KEY_MARKERS)


def _display_metric_value(value: Any) -> Any:
    if isinstance(value, bool):
        return "是" if value else "否"
    if value is None:
        return "暂无"
    if isinstance(value, (dict, list, tuple)):
        return len(value)
    return truncate_text(str(value), 80) if isinstance(value, str) else value


def _humanize_label(key: str) -> str:
    mapping = {
        "status": "状态",
        "country": "国家/地区",
        "disease": "病种",
        "request_count_total": "总请求数",
        "request_count": "请求数",
        "deepseek_call_count": "DeepSeek 调用",
        "page_read_request_count": "页面读取",
        "candidates_seen": "候选数",
        "candidates_after_dedupe": "去重后候选",
        "pending_review_inserted": "待审核写入",
        "connector_specs_generated": "接入建议",
        "raw_documents_created": "原始文档",
        "raw_documents_saved": "原始文档",
        "epidemic_events_created": "疫情事件",
        "model_features_created": "模型特征",
        "records_extracted": "抽取记录",
        "records_persisted": "持久化记录",
        "evidence_only_count": "证据线索",
        "event_ready_count": "事件就绪",
        "feature_ready_count": "特征就绪",
        "visualization_ready_count": "可视化就绪",
        "requires_human_review_count": "需人工复核",
        "blocked_count": "阻断记录",
        "approved_event_count": "批准事件",
        "promoted_epidemic_events": "晋升事件",
        "failure_type": "失败类型",
        "cleaning_status": "清洗状态",
        "event_extraction_status": "事件抽取状态",
        "recommended_next_step": "建议下一步",
    }
    return mapping.get(key, key.replace("_", " "))
