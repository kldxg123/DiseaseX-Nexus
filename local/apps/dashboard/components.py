from __future__ import annotations

from typing import Any

from apps.dashboard.api_client import DashboardApiError
from apps.dashboard.formatters import render_safe_summary_panel, render_sanitized_debug_json
from apps.dashboard.safety import sanitize_dict

UI_LABELS = {
    "api": "API 状态",
    "sources": "数据源数量",
    "predictions": "预测记录数",
    "feedback": "反馈记录数",
    "status": "状态",
    "count": "数量",
    "verdict": "人工判断",
    "country": "国家/地区",
    "disease": "病种",
    "iso_week": "ISO 周",
    "cases": "病例数",
    "outbreaks_count": "暴发数",
    "confidence_score": "置信度",
    "target_date": "预测目标日期",
    "predicted_cases": "预测病例数",
    "predicted_value": "预测值",
    "probability": "暴发概率",
    "risk_level": "风险等级",
    "model_version": "模型版本",
    "name": "名称",
    "base_url": "基础 URL",
    "domain": "域名",
    "source_type": "来源类型",
    "source_score": "数据源评分",
    "requires_human_review": "需要人工复核",
    "connector_name": "连接器名称",
    "source_name": "数据源名称",
    "parser_strategy": "解析策略",
    "sample_url_count": "样例 URL 数量",
    "test_fixture_required": "需要测试 fixture",
    "run_id": "运行编号",
    "providers_planned": "计划访问 provider",
    "providers_used": "实际使用 provider",
    "request_count_total": "总请求次数",
    "candidates_seen": "看到的候选数",
    "candidates_after_dedupe": "去重后候选数",
    "pending_review_inserted": "写入待审核数",
    "connector_specs_generated": "连接器建议数",
    "candidate_urls_visited": "是否访问候选 URL",
    "no_follow_links": "是否禁止跟随链接",
    "warnings": "警告",
    "limitations": "限制说明",
    "failure_type": "失败类型",
    "http_status_code": "HTTP 状态码",
    "network_accessed": "是否联网",
    "safe_failure": "是否安全失败",
    "quality_gate_status": "质量闸门状态",
    "metadata_check_status": "元数据检查状态",
    "data_source_likelihood": "像数据源的概率",
    "recommended_action": "建议动作",
    "registry_status": "注册表状态",
    "auto_enable": "是否自动启用",
    "raw_documents_count": "原始文档数",
    "epidemic_events_count": "疫情事件数",
    "model_features_count": "模型特征数",
    "feature_eligible": "是否可生成特征",
    "blocker_types": "阻断原因类型",
    "recommended_next_action": "建议下一步",
}

VALUE_LABELS = {
    "active": "已启用 active",
    "pending_review": "待人工审核 pending_review",
    "degraded": "降级 degraded",
    "disabled": "已禁用 disabled",
    "success": "成功 success",
    "partial_success": "部分成功 partial_success",
    "failure": "失败 failure",
    "unknown": "未知 unknown",
    "ok": "正常 ok",
    "failed": "失败 failed",
    "safe_failed": "安全失败 safe_failed",
    "planned": "仅计划 planned",
    "high": "高 high",
    "medium": "中 medium",
    "low": "低 low",
    "critical": "严重 critical",
}


def render_disclaimer():
    import streamlit as st

    st.info(
        "仅用于研究和决策辅助：预测不保证未来准确率，公共卫生相关判断必须经过人工复核。"
    )


def render_api_status(client):
    import streamlit as st

    try:
        payload = client.health()
    except DashboardApiError as exc:
        st.error(f"API 不可用：{exc}")
        return {"status": "error"}
    st.success(f"API 状态：{payload.get('status', 'unknown')}")
    return payload


def render_summary_cards(summary: dict):
    import streamlit as st

    safe_summary = sanitize_dict(summary or {})
    if not safe_summary:
        st.caption("暂无概要数据。")
        return
    columns = st.columns(min(4, max(1, len(safe_summary))))
    for index, (label, value) in enumerate(safe_summary.items()):
        with columns[index % len(columns)]:
            st.metric(_display_label(str(label)), _display_value(value) if value is not None else "暂无")


def render_table(title: str, rows: list[dict], empty_message: str = "No data"):
    import streamlit as st

    st.subheader(title)
    if not rows:
        st.caption(_display_empty_message(empty_message))
        return
    st.dataframe([_localize_mapping(sanitize_dict(row)) for row in rows], use_container_width=True)


def render_json_block(title: str, data: dict):
    render_safe_summary_panel(title, data or {})
    render_sanitized_debug_json(data or {})


def _display_label(key: str) -> str:
    normalized = str(key or "").strip()
    return UI_LABELS.get(normalized, normalized.replace("_", " "))


def _display_value(value: Any) -> Any:
    if isinstance(value, str):
        return VALUE_LABELS.get(value.strip().lower(), value)
    return value


def _localize_mapping(value: Any) -> Any:
    if isinstance(value, dict):
        localized = {}
        for key, item in value.items():
            label = _display_label(str(key))
            display_key = f"{label} ({key})" if label != key else str(key)
            localized[display_key] = _localize_mapping(_display_value(item))
        return localized
    if isinstance(value, list):
        return [_localize_mapping(item) for item in value]
    return _display_value(value)


def _display_empty_message(message: str) -> str:
    if message == "No data":
        return "暂无数据"
    if message.startswith("No "):
        return f"暂无数据：{message}"
    return message
