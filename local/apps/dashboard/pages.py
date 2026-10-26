from __future__ import annotations

import json
from typing import Any

from apps.dashboard.api_client import DashboardApiError
from apps.dashboard.components import (
    render_api_status,
    render_disclaimer,
    render_json_block,
    render_summary_cards,
    render_table,
)
from apps.dashboard.formatters import (
    build_time_series_rows,
    compact_access_decision,
    compact_access_policy_summary,
    compact_current_risk_summary,
    compact_acquisition_quality_summary,
    compact_data_quality_summary,
    compact_feedback_summary,
    compact_label_audit,
    compact_live_source_search_summary,
    compact_candidate_acquisition_summary,
    compact_optimizer_summary,
    compact_source_health_summary,
    compact_source_discovery_summary,
    feedback_counts,
    minimum_data_summary,
    rows_from_access_policies,
    rows_from_data_quality_tables,
    rows_from_live_source_search_connector_specs,
    rows_from_live_source_search_metadata_results,
    rows_from_live_source_search_provider_diagnostics,
    rows_from_live_source_search_review_queue,
    rows_from_connector_specs,
    rows_from_optimizer_actions,
    rows_from_optimizer_failure_cases,
    rows_from_recent_events_and_predictions,
    rows_from_source_discovery_candidates,
    rows_from_source_discovery_connector_specs,
    summarize_risk,
    summarize_source_health,
)
from apps.dashboard.safety import sanitize_dict

SOURCE_DISCOVERY_SAMPLES = {
    "norovirus": {
        "query": "United States norovirus outbreak data",
        "results": [
            {
                "title": "CDC NoroSTAT Data Table",
                "url": "https://www.cdc.gov/norovirus/php/reporting/norostat-data-table.html",
                "snippet": "Data table for suspected and confirmed norovirus outbreaks reported to NoroSTAT.",
                "source_name": "CDC NoroSTAT",
                "rank": 1,
            },
            {
                "title": "State health norovirus outbreak dashboard",
                "url": "https://health.example.gov/norovirus/outbreak-dashboard",
                "snippet": "A state health dashboard style page for norovirus outbreak indicators.",
                "source_name": "State Health Example",
                "rank": 2,
            },
            {
                "title": "Health news report on norovirus outbreaks",
                "url": "https://www.healthnews.example/norovirus-school-outbreaks",
                "snippet": "Short news summary about recent norovirus school outbreaks.",
                "source_name": "Health News Example",
                "rank": 3,
            },
        ],
    },
    "influenza": {
        "query": "United States influenza surveillance data",
        "results": [
            {
                "title": "CDC FluView Weekly U.S. Influenza Surveillance Report",
                "url": "https://www.cdc.gov/fluview/index.html",
                "snippet": "CDC FluView provides weekly influenza surveillance indicators and official reports.",
                "source_name": "CDC FluView",
                "rank": 1,
            },
            {
                "title": "CDC FluView Interactive",
                "url": "https://www.cdc.gov/fluview/surveillance/index.html",
                "snippet": "Interactive influenza surveillance charts and dashboard-like source from CDC.",
                "source_name": "CDC FluView Interactive",
                "rank": 2,
            },
            {
                "title": "ECDC ERVISS Respiratory Virus Surveillance",
                "url": "https://www.ecdc.europa.eu/en/seasonal-influenza/surveillance-and-disease-data/erviss",
                "snippet": "European respiratory virus surveillance metadata source for influenza-like illness.",
                "source_name": "ECDC ERVISS",
                "rank": 3,
            },
        ],
    },
}


def render_overview_page(client):
    import streamlit as st

    st.header("系统总览 Overview")
    render_disclaimer()
    st.info("这里展示 API、数据源、近期事件、预测和人工反馈的整体状态。预测只作辅助分析，必须人工复核。")
    health = render_api_status(client)
    source_health = _safe_call(client.get_source_health, {})
    events = _safe_call(client.get_events, [], limit=20)
    predictions = _safe_call(client.get_predictions, [], limit=20)
    feedback_summary = _safe_call(client.get_feedback_summary, {})
    risk = compact_current_risk_summary(summarize_risk(predictions))
    source_summary = summarize_source_health(source_health)
    feedback_summary_compact = feedback_counts(feedback_summary)
    recent = rows_from_recent_events_and_predictions(events, predictions)

    render_summary_cards(
        {
            "api": health.get("status", "unknown") if isinstance(health, dict) else "unknown",
            "sources": source_summary.get("total", 0),
            "predictions": len(predictions),
            "feedback": feedback_summary_compact.get("feedback_count", 0),
        }
    )

    st.subheader("当前风险摘要")
    _render_risk_status(st, risk)
    risk_columns = st.columns(4)
    risk_metrics = [
        ("风险等级", risk["current_risk_level"]),
        ("预测目标日期", risk["latest_target_date"]),
        ("预测生成日期", risk["latest_prediction_date"]),
        ("模型版本", risk["model_version"]),
    ]
    for index, (label, value) in enumerate(risk_metrics):
        with risk_columns[index]:
            st.metric(label, value)
    st.caption(risk["status_note"])

    st.subheader("数据源健康状态")
    source_rows = compact_source_health_summary(source_health)
    source_columns = st.columns(4)
    for index, row in enumerate(source_rows):
        with source_columns[index]:
            st.metric(row["status"], row["count"])
    render_table("数据源状态统计", source_rows)

    st.subheader("人工反馈摘要")
    feedback_rows = compact_feedback_summary(feedback_summary)
    feedback_columns = st.columns(5)
    for index, row in enumerate(feedback_rows):
        with feedback_columns[index]:
            st.metric(row["verdict"], row["count"])
    render_table("反馈数量统计", feedback_rows)

    st.subheader("近期事件与预测")
    recent_columns = st.columns(2)
    with recent_columns[0]:
        render_table("近期疫情事件", recent["events_rows"], empty_message="暂无近期事件")
    with recent_columns[1]:
        render_table("近期预测记录", recent["prediction_rows"], empty_message="暂无近期预测")


def render_overview(client):
    render_overview_page(client)


def render_sources_page(client):
    import streamlit as st

    st.header("数据源管理 Sources")
    st.caption("查看和管理 source_registry。新发现的数据源默认是 pending_review，必须人工审核后才能启用。")
    country = st.text_input("国家/地区筛选", value="")
    disease = st.text_input("病种筛选", value="")
    status = st.selectbox("数据源状态", ["", "active", "pending_review", "degraded", "disabled"])
    limit = st.number_input("最多显示条数", min_value=1, max_value=1000, value=100)
    dry_run = st.checkbox("仅预演种子源写入 dry-run", value=True)
    if st.button("写入/预演默认种子源"):
        _show_result(client.seed_sources(dry_run=dry_run))
    health = _safe_call(client.get_source_health, {})
    sources = _safe_call(
        client.get_sources,
        [],
        status=_none(status),
        country=_none(country),
        disease=_none(disease),
        limit=int(limit),
    )
    render_json_block("数据源健康摘要", summarize_source_health(health))
    render_table("数据源注册表 source_registry", sources)
    with st.expander("人工审核 / 启用选中的数据源", expanded=True):
        st.caption(
            "从上表复制 source id。这里的启用只改变注册表状态，不会抓取网页、不会跟随链接、不会部署 connector。"
        )
        selected_source_id = st.number_input("数据源 ID", min_value=1, value=1)
        decision = st.selectbox("审核决定", ["activate", "keep_pending", "disable"])
        terms_reviewed = st.checkbox("已审查站点条款/robots", value=False)
        manual_reviewed = st.checkbox("已完成人工复核", value=False)
        reviewer = st.text_input("审核人/记录编号", value="")
        notes = st.text_area("审核备注", value="", height=80)
        if st.button("应用审核决定"):
            _show_result(
                client.review_source(
                    int(selected_source_id),
                    {
                        "decision": decision,
                        "terms_reviewed": bool(terms_reviewed),
                        "manual_reviewed": bool(manual_reviewed),
                        "reviewer": _none(reviewer),
                        "notes": _none(notes),
                    },
                )
            )


def render_source_discovery_page(client):
    import streamlit as st

    st.header("Source Discovery")
    st.info(
        "当前只支持 fixture/mock search results；不进行真实网络搜索。新数据源只写入 pending_review，"
        "必须人工审核后才能启用。"
    )
    st.warning(
        "ConnectorSpec 只是建议，不会自动上线 connector；Dashboard 不显示完整 snippet、metadata 或原始搜索响应。"
    )

    country = st.text_input("Discovery country", value="United States")
    disease = st.text_input("Discovery disease", value="norovirus")
    state = _get_streamlit_state(st)
    sample_key = _source_discovery_sample_key(disease)
    if state.get("_source_discovery_sample_key") != sample_key:
        state["_source_discovery_sample_key"] = sample_key
        state["source_discovery_search_results_json"] = json.dumps(
            SOURCE_DISCOVERY_SAMPLES[sample_key],
            ensure_ascii=False,
            indent=2,
        )
    sample_label = "Influenza fixture sample" if sample_key == "influenza" else "Norovirus fixture sample"
    st.caption(f"Loaded local fixture sample: {sample_label}. This is not automatic live web exploration.")
    if st.button("Reload fixture sample for disease"):
        state["source_discovery_search_results_json"] = json.dumps(
            SOURCE_DISCOVERY_SAMPLES[sample_key],
            ensure_ascii=False,
            indent=2,
        )
    min_source_score = st.number_input("Minimum source score", min_value=0.0, max_value=1.0, value=0.35)
    generate_connector_specs = st.checkbox("Generate ConnectorSpec suggestions", value=True)

    with st.expander("Controlled Live Search Demo", expanded=True):
        st.warning(
            "This performs controlled live source discovery. Auto mode tries GDELT first and falls back to "
            "the public Data.gov catalog and then an official disease directory page if earlier providers return no "
            "results. It does not open candidate URLs, does not follow links, and keeps all sources pending_review."
        )
        provider = st.selectbox("Live provider", ["auto", "official_directory", "data_gov", "gdelt"])
        allow_network = st.checkbox("Allow controlled live network requests", value=False)
        terms_reviewed = st.checkbox("Terms reviewed", value=False)
        manual_reviewed = st.checkbox("Manual review approved", value=False)
        if st.button("Run controlled live source discovery"):
            try:
                result = client.source_discovery_live_controlled(
                    {
                        "provider": provider,
                        "country": country,
                        "disease": disease,
                        "allow_network": bool(allow_network),
                        "terms_reviewed": bool(terms_reviewed),
                        "manual_reviewed": bool(manual_reviewed),
                        "max_requests": 3 if provider == "auto" else 1,
                        "min_source_score": float(min_source_score),
                        "generate_connector_specs": bool(generate_connector_specs),
                    }
                )
            except DashboardApiError as exc:
                result = {
                    "status": "failed",
                    "provider": provider,
                    "country": country,
                    "disease": disease,
                    "network_accessed": "unknown",
                    "request_count": "unknown",
                    "candidate_urls_accessed": False,
                    "follow_links": False,
                    "error": str(exc),
                    "warnings": [
                        "Controlled live discovery did not return before the dashboard timeout.",
                        "The system does not retry automatically from the dashboard.",
                    ],
                }
            _render_live_discovery_status(st, result)
            render_json_block("Controlled Live Search Summary", compact_source_discovery_summary(result))
            render_table(
                "Live Top Candidates",
                rows_from_source_discovery_candidates(result),
                empty_message=_live_discovery_empty_candidates_message(result),
            )
            render_table(
                "Live ConnectorSpec Suggestions",
                rows_from_source_discovery_connector_specs(result),
                empty_message=_live_discovery_empty_specs_message(result),
            )

    with st.expander("Live Source Search", expanded=True):
        st.warning(
            "Provider-only orchestration: Data.gov, official directory, and GDELT can be queried only "
            "with explicit review flags. Candidate URLs are not opened and links are not followed."
        )
        search_provider = st.selectbox("Search providers", ["auto", "data_gov", "official_directory", "gdelt"])
        max_total_requests = st.number_input("Max total provider requests", min_value=1, max_value=3, value=3)
        search_allow_network = st.checkbox("Allow live source search network requests", value=False)
        search_terms_reviewed = st.checkbox("Live source search terms reviewed", value=False)
        search_manual_reviewed = st.checkbox("Live source search manual review approved", value=False)
        include_rate_limited_provider = st.checkbox("Include rate-limited providers", value=False)
        curated_directory_url = st.text_input("Curated official directory URL", value="")
        metadata_check = st.checkbox("Run controlled metadata check on refined top candidates", value=False)
        metadata_check_top_n = st.number_input("Metadata check top N", min_value=0, max_value=3, value=3)
        max_metadata_check_requests = st.number_input("Max metadata check requests", min_value=0, max_value=3, value=3)
        search_payload = {
            "provider": search_provider,
            "providers": search_provider,
            "country": country,
            "disease": disease,
            "max_total_requests": int(max_total_requests),
            "allow_network": bool(search_allow_network),
            "terms_reviewed": bool(search_terms_reviewed),
            "manual_reviewed": bool(search_manual_reviewed),
            "include_rate_limited_provider": bool(include_rate_limited_provider),
            "curated_official_directory_url": _none(curated_directory_url),
            "metadata_check": bool(metadata_check),
            "metadata_check_top_n": int(metadata_check_top_n),
            "max_metadata_check_requests": int(max_metadata_check_requests),
            "min_source_score": float(min_source_score),
        }
        search_result = None
        if st.button("Run live source search dry-run"):
            if hasattr(client, "live_source_search_dry_run"):
                try:
                    search_result = client.live_source_search_dry_run(search_payload)
                except DashboardApiError as exc:
                    search_result = {
                        "status": "failed",
                        "country": country,
                        "disease": disease,
                        "request_count_total": 0,
                        "candidate_urls_visited": False,
                        "no_follow_links": True,
                        "warnings": [
                            str(exc),
                            "Dry-run did not complete in the dashboard request. No live retry was performed.",
                        ],
                    }
                    st.warning("Live source search dry-run failed safely. No network retry was performed.")
            else:
                st.info("Dashboard client does not expose live source search dry-run yet.")
        if st.button("Run controlled live source search"):
            if hasattr(client, "live_source_search_run"):
                try:
                    search_result = client.live_source_search_run(search_payload)
                except DashboardApiError as exc:
                    search_result = {
                        "status": "failed",
                        "country": country,
                        "disease": disease,
                        "request_count_total": "unknown",
                        "candidate_urls_visited": False,
                        "no_follow_links": True,
                        "warnings": [str(exc), "No automatic retry was performed."],
                    }
            else:
                st.info("Dashboard client does not expose controlled live source search yet.")
        if search_result:
            render_json_block("Live Source Search Summary", compact_live_source_search_summary(search_result))
            render_table(
                "Provider Diagnostics",
                rows_from_live_source_search_provider_diagnostics(search_result),
                empty_message="No provider diagnostics were returned.",
            )
            render_table(
                "Live Source Search Review Queue",
                rows_from_live_source_search_review_queue(search_result),
                empty_message="No review queue items were generated.",
            )
            render_table(
                "Metadata Check Results",
                rows_from_live_source_search_metadata_results(search_result),
                empty_message="No metadata check results were generated.",
            )
            render_table(
                "Live Source Search ConnectorSpec Recommendations",
                rows_from_live_source_search_connector_specs(search_result),
                empty_message="No ConnectorSpec recommendations were generated.",
            )

    with st.expander("Candidate Acquisition Review", expanded=False):
        st.warning(
            "Controlled acquisition visits at most one metadata-confirmed candidate URL. It does not follow links, "
            "download files, activate sources, or show raw response bodies."
        )
        acquisition_run_dir = st.text_input(
            "Reviewed source-search run directory",
            value="reports/live_source_search/search_measles_united_states_20260706T150829Z",
        )
        acquisition_output_dir = st.text_input(
            "Acquisition output directory",
            value="reports/live_source_search/acquisition_runs",
        )
        acquisition_allow_network = st.checkbox("Allow acquisition network request", value=False)
        acquisition_terms = st.checkbox("Acquisition terms reviewed", value=False)
        acquisition_manual = st.checkbox("Acquisition manual review approved", value=False)
        acquisition_payload = {
            "run_dir": acquisition_run_dir,
            "strategy": "top_confirmed",
            "max_requests": 1,
            "output_dir": acquisition_output_dir,
            "allow_network": bool(acquisition_allow_network),
            "terms_reviewed": bool(acquisition_terms),
            "manual_reviewed": bool(acquisition_manual),
        }
        if st.button("Plan controlled acquisition"):
            result = client.live_source_acquisition_plan(acquisition_payload)
            render_json_block("Acquisition Plan", compact_candidate_acquisition_summary(result))
        if st.button("Run controlled acquisition"):
            result = client.live_source_acquisition_run(acquisition_payload)
            render_json_block("Acquisition Summary", compact_candidate_acquisition_summary(result))
        st.divider()
        st.caption("Local quality diagnostics do not make network requests and do not generate features unless the gate passes.")
        quality_run_dir = st.text_input(
            "Acquisition run directory for quality diagnostics",
            value="reports/live_source_search/acquisition_runs/acquisition_measles_united_states_20260706T154307Z",
        )
        quality_database_url = st.text_input(
            "Local acquisition SQLite URL",
            value="sqlite:///reports/live_source_search/acquisition_runs/measles_candidate_acquisition.sqlite",
        )
        if st.button("Diagnose acquisition quality"):
            result = client.live_source_acquisition_quality_diagnose(
                {"run_dir": quality_run_dir, "database_url": quality_database_url}
            )
            render_json_block("Acquisition Quality Summary", compact_acquisition_quality_summary(result))

    search_results_text = st.text_area(
        "Search results JSON",
        key="source_discovery_search_results_json",
        height=220,
    )

    if st.button("Run fixture discovery"):
        try:
            search_results = json.loads(search_results_text or "{}")
        except json.JSONDecodeError:
            st.error("Search results JSON could not be parsed.")
            return
        if not isinstance(search_results, dict):
            st.error("Search results JSON must be an object.")
            return
        result = client.source_discovery_discover_fixture(
            country=country,
            disease=disease,
            search_results=search_results,
            min_source_score=float(min_source_score),
            generate_connector_specs=bool(generate_connector_specs),
        )
        render_json_block("Source Discovery Summary", compact_source_discovery_summary(result))
        render_table("Top Candidates", rows_from_source_discovery_candidates(result))
        render_table("ConnectorSpec Suggestions", rows_from_source_discovery_connector_specs(result))


def _source_discovery_sample_key(disease: str) -> str:
    normalized = str(disease or "").strip().lower()
    if "flu" in normalized or "influenza" in normalized or "流感" in normalized:
        return "influenza"
    return "norovirus"


def _get_streamlit_state(st) -> dict:
    return getattr(st, "session_state", {})


def _render_live_discovery_status(st, result: dict) -> None:
    status = str((result or {}).get("status") or "unknown")
    request_count = (result or {}).get("request_count", 0)
    network_accessed = (result or {}).get("network_accessed", False)
    candidate_visits = (result or {}).get("candidate_urls_accessed", False)
    follow_links = (result or {}).get("follow_links", False)
    st.caption(
        "Live request status: "
        f"status={status}; network_accessed={network_accessed}; request_count={request_count}; "
        f"candidate_url_visits={candidate_visits}; follow_links={follow_links}."
    )
    warnings_text = " ".join(str(item) for item in ((result or {}).get("warnings") or []))
    failure_type = str((result or {}).get("failure_type") or "").strip().lower()
    if failure_type == "rate_limited" or "HTTP 429" in warnings_text:
        st.warning(
            "GDELT returned HTTP 429 rate limiting. The system did perform the controlled live request, "
            "but GDELT did not return article results, so there are no candidates to parse or store. "
            "No retry, candidate URL visit, or link following was performed."
        )
    elif status == "failed":
        st.warning(
            "Controlled live discovery returned a safe failure. No retry, candidate URL visit, "
            "or link following was performed."
        )
    elif int((result or {}).get("candidates_found") or 0) > 0:
        st.success("Controlled live discovery found candidate sources. All remain pending_review.")
    else:
        st.info("Controlled live discovery completed without candidate sources.")


def _live_discovery_empty_candidates_message(result: dict) -> str:
    warnings_text = " ".join(str(item) for item in ((result or {}).get("warnings") or []))
    if str((result or {}).get("failure_type") or "").lower() == "rate_limited" or "HTTP 429" in warnings_text:
        return "No live candidates: GDELT returned HTTP 429 rate limiting for this single controlled request."
    return "No live candidates were returned by this controlled request."


def _live_discovery_empty_specs_message(result: dict) -> str:
    if int((result or {}).get("candidates_found") or 0) <= 0:
        return "No ConnectorSpec suggestions because no live candidates were available."
    return "No ConnectorSpec suggestions were generated."


def render_ingestion_page(client):
    import streamlit as st

    st.header("采集入库 Ingestion")
    render_disclaimer()
    st.caption("这里用于本地 mock/fixture 采集测试，不会访问真实外网。")
    with st.expander("本地 HTML 模拟抓取 Mock Crawl", expanded=True):
        source_id = st.text_input("数据源 ID", value="")
        url = st.text_input("模拟 URL", value="https://fixture.invalid/local")
        html = st.text_area("HTML fixture 内容", value="", height=140)
        country = st.text_input("识别国家/地区", value="")
        disease = st.text_input("识别病种", value="")
        if st.button("运行本地模拟抓取"):
            _show_result(
                client.crawl_mock_url(
                    {
                        "source_id": source_id,
                        "url": url,
                        "html": html,
                        "country_detected": _none(country),
                        "disease_detected": _none(disease),
                    }
                )
            )
    with st.expander("官方连接器 fixture 解析"):
        connector = st.selectbox(
            "连接器类型",
            ["who_disease_outbreak_news", "ecdc_surveillance_atlas", "ecdc_erviss", "cdc_norostat", "cdc_nors"],
        )
        fixture_content = st.text_area("fixture 文件内容", value="", height=140)
        country = st.text_input("连接器国家/地区", value="")
        disease = st.text_input("连接器病种", value="")
        if st.button("解析官方连接器 fixture"):
            _show_result(
                client.parse_official_fixture(
                    {
                        "connector": connector,
                        "fixture_content": fixture_content,
                        "country": _none(country),
                        "disease": _none(disease),
                    }
                )
            )
    with st.expander("GDELT mock 候选源发现"):
        country = st.text_input("GDELT 国家/地区", value="United States")
        disease = st.text_input("GDELT 病种", value="norovirus")
        lookback_days = st.number_input("回看天数", min_value=1, max_value=365, value=30)
        mock_json = st.text_area("Mock 响应 JSON", value='{"articles": []}', height=120)
        if st.button("从 mock GDELT 结果发现候选源"):
            _show_result(
                client.discover_gdelt_mock(
                    {
                        "country": country,
                        "disease": disease,
                        "lookback_days": int(lookback_days),
                        "mock_response_json": _parse_json(mock_json),
                    }
                )
            )


def render_events_page(client):
    import streamlit as st

    st.header("疫情事件 Events")
    country, disease, limit = _common_filters(st)
    if st.button("运行清洗器，抽取疫情事件"):
        _show_result(client.run_cleaner({"limit": int(limit), "country": _none(country), "disease": _none(disease)}))
    events = _safe_call(client.get_events, [], country=_none(country), disease=_none(disease), limit=int(limit))
    render_table("疫情事件表 epidemic_events", events)


def render_features_page(client):
    import streamlit as st

    st.header("模型特征 Features")
    country, disease, limit = _common_filters(st)
    horizon_days = st.number_input("预测窗口天数 horizon", min_value=1, max_value=365, value=28)
    if st.button("从事件生成模型特征"):
        _show_result(
            client.build_features(
                {
                    "country": _none(country),
                    "disease": _none(disease),
                    "horizons_days": [int(horizon_days)],
                    "outbreak_threshold": 3,
                    "split_type": "train",
                    "replace_existing": True,
                }
            )
        )
    rows = _safe_call(
        client.get_features,
        [],
        country=_none(country),
        disease=_none(disease),
        horizon_days=int(horizon_days),
        limit=int(limit),
    )
    render_table("模型特征表 model_features", rows)


def render_models_page(client):
    import streamlit as st

    st.header("预测模型 Models")
    render_disclaimer()
    country, disease, limit = _common_filters(st)
    admin1 = st.text_input("一级行政区 admin1（可选）", value="")
    horizon_days = st.number_input("预测窗口天数 horizon", min_value=1, max_value=365, value=28)
    min_train_size = st.number_input("最小训练样本数", min_value=1, max_value=10000, value=8)
    test_window = st.number_input("回测窗口周数", min_value=1, max_value=52, value=1)
    model_name = st.selectbox("模型类型", ["moving_average", "seasonal_naive", "feature_rule"])
    if st.button("运行滚动回测"):
        _show_result(
            client.backtest_model(
                {
                    "country": _none(country),
                    "disease": _none(disease),
                    "admin1": _none(admin1),
                    "horizon_days": int(horizon_days),
                    "model_name": model_name,
                    "min_train_size": int(min_train_size),
                    "test_window": int(test_window),
                }
            )
        )
    if st.button("加载回测指标"):
        _show_result(
            client.get_backtest_metrics(
                country=_none(country),
                disease=_none(disease),
                admin1=_none(admin1),
                horizon_days=int(horizon_days),
                model_name=model_name,
                min_train_size=int(min_train_size),
                test_window=int(test_window),
            )
        )
    if st.button("生成最新预测"):
        _show_result(
            client.predict_model(
                {
                    "country": _none(country),
                    "disease": _none(disease),
                    "admin1": _none(admin1),
                    "horizon_days": int(horizon_days),
                    "model_name": model_name,
                    "limit": int(limit),
                    "min_train_size": int(min_train_size),
                    "write_predictions": True,
                }
            )
        )
    with st.expander("离线模型晋级检查（只给建议，不自动替换模型）"):
        champion_mae = st.number_input("当前 champion MAE", min_value=0.0, value=10.0)
        challenger_mae = st.number_input("候选 challenger MAE", min_value=0.0, value=9.0)
        min_improvement = st.number_input("最小相对改进比例", min_value=0.0, max_value=1.0, value=0.05)
        if st.button("检查是否建议晋级"):
            _show_result(
                client.model_promotion_check(
                    {
                        "champion": {
                            "model_version": "dashboard-champion",
                            "model_name": "current",
                            "metrics": {"regression": {"mae": float(champion_mae)}},
                            "warnings": [],
                        },
                        "challenger": {
                            "model_version": "dashboard-challenger",
                            "model_name": model_name,
                            "metrics": {"regression": {"mae": float(challenger_mae)}},
                            "warnings": [],
                        },
                        "primary_metric": "regression.mae",
                        "lower_is_better": True,
                        "min_relative_improvement": float(min_improvement),
                    }
                )
            )
    predictions = _safe_call(
        client.get_predictions,
        [],
        country=_none(country),
        disease=_none(disease),
        horizon_days=int(horizon_days),
        limit=int(limit),
    )
    render_json_block("风险摘要", summarize_risk(predictions))
    render_table("预测结果 predictions", predictions)


def render_feedback_page(client):
    import streamlit as st

    st.header("人工反馈 Feedback")
    render_disclaimer()
    prediction_id = st.text_input("预测记录 ID", value="")
    verdict = st.selectbox("人工判断结果", ["success", "partial_success", "failure", "unknown"])
    true_cases = st.number_input("真实病例数", min_value=0.0, value=0.0)
    true_outbreak_label = st.checkbox("真实是否暴发", value=False)
    reason = st.selectbox(
        "失败/偏差原因",
        ["", "under_prediction", "over_prediction", "missed_outbreak", "false_alarm", "data_gap", "source_quality"],
    )
    reviewer = st.text_input("复核人/记录编号", value="")
    notes = st.text_area("备注", value="", height=80)
    if st.button("提交人工反馈"):
        _show_result(
            client.add_feedback(
                {
                    "prediction_id": prediction_id,
                    "verdict": verdict,
                    "true_cases": true_cases,
                    "true_outbreak_label": true_outbreak_label,
                    "reason": _none(reason),
                    "reviewer": _none(reviewer),
                    "notes": _none(notes),
                }
            )
        )
    feedback_id = st.text_input("用于生成经验记录的反馈 ID", value="")
    if st.button("生成经验库记录"):
        _show_result(client.build_experience({"feedback_id": feedback_id}))
    render_json_block("反馈摘要", feedback_counts(_safe_call(client.get_feedback_summary, {})))
    render_json_block("经验库摘要", _safe_call(client.get_experience_summary, {}))
    feedback_rows = _safe_call(client.get_feedback, [], limit=100)
    render_table("人工反馈 human_feedback", feedback_rows)
    experiences = _safe_call(client.get_experiences, [], limit=100)
    render_table("经验库 model_experience_bank", experiences)


def render_minimum_data_page(client):
    import streamlit as st

    st.header("最小数据集分析 Minimum Data")
    render_disclaimer()
    country = st.text_input("分析国家/地区", value="United States")
    disease = st.text_input("分析病种", value="norovirus")
    horizon_days = st.number_input("分析预测窗口天数", min_value=1, max_value=365, value=28)
    target_metric = st.selectbox("目标评估指标", ["pr_auc", "auroc", "f1_score", "brier_score", "mae", "rmse", "mape", "smape"])
    min_score = st.number_input("最低可接受分数/阈值", value=0.75)
    feature_groups = st.text_input("候选特征组", value="case_counts,data_quality,seasonality")
    if st.button("分析最小数据集合"):
        result = client.minimum_data_analysis(
            {
                "country": _none(country),
                "disease": _none(disease),
                "horizon_days": int(horizon_days),
                "target_metric": target_metric,
                "min_acceptable_score": float(min_score),
                "candidate_feature_groups": _csv(feature_groups),
                "model_name": "feature_rule",
                "min_train_size": 8,
            }
        )
        render_json_block("最小数据集合摘要", minimum_data_summary(result))
        render_table("评估预览", result.get("evaluation_preview", []))
        render_table("学习曲线", result.get("learning_curve", []))
        render_table("消融结果", result.get("ablation_results", []))


def render_crawler_optimizer_page(client):
    import streamlit as st

    st.header("Crawler Optimizer")
    st.info(
        "人工审核：本页只做本地爬虫失败诊断和优化建议，不自动上线新爬虫，不自动启用新数据源。"
    )
    st.warning(
        "高风险动作继续标记为 requires_human_review=True；真实网络抓取仍由 Live Data Access Guardrails 控制。"
    )
    country = st.text_input("Optimizer country", value="United States")
    disease = st.text_input("Optimizer disease", value="norovirus")
    limit_sources = st.number_input("Source limit", min_value=1, max_value=1000, value=100)
    limit_documents = st.number_input("Document limit", min_value=1, max_value=2000, value=200)
    include_connector_specs = st.checkbox("Include connector specs", value=True)
    apply_safe_actions = st.checkbox("Apply safe low/medium-risk actions", value=False)
    if apply_safe_actions:
        st.warning("即使启用安全动作，高风险动作也不会自动执行，仍需人工审核。")

    if st.button("Run optimizer analysis"):
        result = client.crawler_optimizer_analyze(
            country=_none(country),
            disease=_none(disease),
            limit_sources=int(limit_sources),
            limit_documents=int(limit_documents),
            include_connector_specs=bool(include_connector_specs),
            apply_safe_actions=bool(apply_safe_actions),
        )
        render_json_block("Optimizer Summary", compact_optimizer_summary(result))
        render_table("Failure Cases", rows_from_optimizer_failure_cases(result))
        render_table("Recommended Actions", rows_from_optimizer_actions(result))
        render_table("Connector Specs", rows_from_connector_specs(result))

    with st.expander("Source Connector Spec"):
        source_id = st.text_input("Optimizer source ID", value="")
        markdown = st.checkbox("Return markdown draft", value=False)
        if st.button("Generate connector spec"):
            result = client.crawler_optimizer_connector_spec(source_id=source_id, markdown=bool(markdown))
            render_json_block("Connector Spec Summary", compact_optimizer_summary(result))
            render_table("Connector Spec", rows_from_connector_specs(result))
            if markdown and result.get("markdown"):
                st.text_area("Connector 规格草案", value=str(result["markdown"]), height=240)

    with st.expander("Analyze One Source"):
        source_id = st.text_input("Source ID for analysis", value="")
        if st.button("Analyze source"):
            result = client.crawler_optimizer_analyze_source(
                source_id=source_id,
                country=_none(country),
                disease=_none(disease),
            )
            render_json_block("Source Optimizer Summary", compact_optimizer_summary(result))
            render_table("Source Failure Cases", rows_from_optimizer_failure_cases(result))
            render_table("Source Recommended Actions", rows_from_optimizer_actions(result))


def render_access_guardrails_page(client):
    import streamlit as st

    st.header("Access Guardrails")
    st.info("这是安全闸门检查页面；它不会真实访问网络，live fetch 默认禁止。")
    st.warning("真实接入前必须人工审查 robots.txt、站点条款、访问频率和引用要求。")

    policies = _safe_call(client.get_access_policies, {})
    render_json_block("Policy Summary", compact_access_policy_summary(policies))
    render_table("Source Access Policies", rows_from_access_policies(policies))

    with st.expander("Guardrail Decision", expanded=True):
        source_key = st.text_input("Source key", value="cdc_norostat")
        url = st.text_input("Requested URL", value="https://example.invalid/test")
        allow_network = st.checkbox("Allow network flag", value=False)
        terms_reviewed = st.checkbox("Terms reviewed", value=False)
        manual_reviewed = st.checkbox("Manual reviewed", value=False)
        robots_allowed = st.checkbox("Robots allowed", value=False)
        request_count = st.number_input("Request count", min_value=1, max_value=10000, value=1)
        if st.button("Check access policy"):
            result = client.check_access_policy(
                source_key=source_key,
                url=_none(url),
                allow_network=bool(allow_network),
                terms_reviewed=bool(terms_reviewed),
                manual_reviewed=bool(manual_reviewed),
                robots_allowed=bool(robots_allowed),
                request_count=int(request_count),
            )
            render_json_block("Access Decision", compact_access_decision(result))


def render_data_quality_page(client):
    import streamlit as st

    st.header("数据质量 Data Quality")
    render_disclaimer()
    st.info(
        "这里只展示本地数据质量摘要。数据质量和标签审计结果需要人工复核，不保证预测准确率。"
    )
    country = st.text_input("数据质量国家/地区", value="")
    disease = st.text_input("数据质量病种", value="")
    write_files = st.checkbox("写出报告文件", value=False)
    output_dir = st.text_input("报告输出目录", value="reports")

    profile = _safe_call(client.get_data_quality_profile, {})
    render_json_block("数据质量摘要", compact_data_quality_summary(profile))
    render_table("各表质量概览", rows_from_data_quality_tables(profile))

    audit = _safe_call(
        client.get_data_quality_label_audit,
        {},
        country=_none(country),
        disease=_none(disease),
    )
    render_json_block("标签审计", compact_label_audit(audit))

    if st.button("生成数据质量报告"):
        result = client.build_data_quality_report(
            {
                "country": _none(country),
                "disease": _none(disease),
                "write_files": bool(write_files),
                "output_dir": output_dir or "reports",
            }
        )
        render_json_block("报告生成结果", result)


def _show_result(payload: dict):
    import streamlit as st

    st.success("请求已完成")
    render_json_block("执行结果", sanitize_dict(payload))


def _render_risk_status(st_module, risk: dict) -> None:
    level = risk.get("current_risk_level", "Unknown")
    message = f"风险等级：{level}。仅作辅助分析，必须人工复核。"
    if level == "Critical":
        st_module.error(message)
    elif level == "High":
        st_module.warning(message)
    elif level == "Medium":
        st_module.info(message)
    elif level == "Low":
        st_module.success(message)
    else:
        st_module.info("风险等级：未知。当前还没有近期预测信号。")


def _safe_call(func, fallback, **kwargs):
    try:
        return func(**kwargs)
    except DashboardApiError as exc:
        import streamlit as st

        st.warning(str(exc))
        return fallback


def _common_filters(st_module) -> tuple[str, str, int]:
    country = st_module.text_input("国家/地区", value="")
    disease = st_module.text_input("病种", value="")
    limit = st_module.number_input("最多显示条数", min_value=1, max_value=1000, value=100)
    return country, disease, int(limit)


def _none(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return value


def _csv(value: str) -> list[str] | None:
    if not value.strip():
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_json(value: str) -> dict:
    try:
        payload = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {"articles": []}
    return payload if isinstance(payload, dict) else {"articles": []}


def render_source_discovery_page(client):
    import streamlit as st

    st.header("数据源发现 Source Discovery")
    st.info(
        "用途：输入国家/地区和病种后，系统会发现可能包含疫情记录的数据源，并生成待人工审核的接入建议。"
        "新数据源默认写入 pending_review，不会自动启用。"
    )
    st.warning(
        "安全边界：本页不展示完整 snippet、metadata 或原始响应正文；ConnectorSpec 只是建议，不会自动上线 connector。"
    )

    country = st.text_input("检索国家/地区", value="United States")
    disease = st.text_input("检索病种", value="norovirus")
    state = _get_streamlit_state(st)
    sample_key = _source_discovery_sample_key_cn(disease)
    if state.get("_source_discovery_sample_key") != sample_key:
        state["_source_discovery_sample_key"] = sample_key
        state["source_discovery_search_results_json"] = json.dumps(
            SOURCE_DISCOVERY_SAMPLES[sample_key],
            ensure_ascii=False,
            indent=2,
        )
    sample_label = "流感 influenza 本地样例" if sample_key == "influenza" else "诺如病毒 norovirus 本地样例"
    st.caption(f"当前载入：{sample_label}。这是本地 fixture/mock 样例，不代表真实联网自动搜索结果。")
    if st.button("按当前病种重新载入本地样例"):
        state["source_discovery_search_results_json"] = json.dumps(
            SOURCE_DISCOVERY_SAMPLES[sample_key],
            ensure_ascii=False,
            indent=2,
        )
    min_source_score = st.number_input("最低数据源评分阈值", min_value=0.0, max_value=1.0, value=0.35)
    generate_connector_specs = st.checkbox("生成 ConnectorSpec 接入建议", value=True)

    with st.expander("受控联网搜索演示 Controlled Live Search", expanded=True):
        st.warning(
            "只有勾选授权项后才会执行受控 provider 检索。auto 模式会按策略尝试 GDELT、Data.gov 和官方目录。"
            "不会打开候选 URL，不会跟随链接，所有新源都保持 pending_review。"
        )
        provider = st.selectbox("联网 provider", ["auto", "official_directory", "data_gov", "gdelt"])
        allow_network = st.checkbox("允许本次受控联网请求", value=False)
        terms_reviewed = st.checkbox("已审查 provider/站点条款", value=False)
        manual_reviewed = st.checkbox("已人工批准本次联网", value=False)
        if st.button("执行受控联网数据源发现"):
            try:
                result = client.source_discovery_live_controlled(
                    {
                        "provider": provider,
                        "country": country,
                        "disease": disease,
                        "allow_network": bool(allow_network),
                        "terms_reviewed": bool(terms_reviewed),
                        "manual_reviewed": bool(manual_reviewed),
                        "max_requests": 3 if provider == "auto" else 1,
                        "min_source_score": float(min_source_score),
                        "generate_connector_specs": bool(generate_connector_specs),
                    }
                )
            except DashboardApiError as exc:
                result = {
                    "status": "failed",
                    "provider": provider,
                    "country": country,
                    "disease": disease,
                    "network_accessed": "unknown",
                    "request_count": "unknown",
                    "candidate_urls_accessed": False,
                    "follow_links": False,
                    "error": str(exc),
                    "warnings": [
                        "受控联网请求未在 Dashboard 超时时间内返回。",
                        "Dashboard 不会自动重试。",
                    ],
                }
            _render_live_discovery_status(st, result)
            render_json_block("受控联网搜索摘要", compact_source_discovery_summary(result))
            render_table(
                "联网候选数据源 Top Candidates",
                rows_from_source_discovery_candidates(result),
                empty_message=_live_discovery_empty_candidates_message(result),
            )
            render_table(
                "联网 ConnectorSpec 建议",
                rows_from_source_discovery_connector_specs(result),
                empty_message=_live_discovery_empty_specs_message(result),
            )

    with st.expander("多 Provider 数据源搜索 Live Source Search", expanded=True):
        st.warning(
            "dry-run 只做本地计划和报告，不联网；受控 live search 必须勾选授权项。"
            "候选 URL 不会被打开，链接不会被跟随。"
        )
        search_provider = st.selectbox("搜索 provider 组合", ["auto", "data_gov", "official_directory", "gdelt"])
        max_total_requests = st.number_input("最多 provider 请求数", min_value=1, max_value=3, value=3)
        search_allow_network = st.checkbox("允许 Live Source Search 联网", value=False)
        search_terms_reviewed = st.checkbox("已审查 Live Search 条款", value=False)
        search_manual_reviewed = st.checkbox("已人工批准 Live Search", value=False)
        include_rate_limited_provider = st.checkbox("包含处于限流冷却的 provider", value=False)
        curated_directory_url = st.text_input("手工指定官方目录 URL（可选）", value="")
        metadata_check = st.checkbox("对排名靠前候选源做受控 metadata 检查", value=False)
        metadata_check_top_n = st.number_input("metadata 检查候选数量", min_value=0, max_value=3, value=3)
        max_metadata_check_requests = st.number_input("metadata 检查最大请求数", min_value=0, max_value=3, value=3)
        search_payload = {
            "provider": search_provider,
            "providers": search_provider,
            "country": country,
            "disease": disease,
            "max_total_requests": int(max_total_requests),
            "allow_network": bool(search_allow_network),
            "terms_reviewed": bool(search_terms_reviewed),
            "manual_reviewed": bool(search_manual_reviewed),
            "include_rate_limited_provider": bool(include_rate_limited_provider),
            "curated_official_directory_url": _none(curated_directory_url),
            "metadata_check": bool(metadata_check),
            "metadata_check_top_n": int(metadata_check_top_n),
            "max_metadata_check_requests": int(max_metadata_check_requests),
            "min_source_score": float(min_source_score),
        }
        search_result = None
        if st.button("运行数据源搜索 dry-run（不联网）"):
            if hasattr(client, "live_source_search_dry_run"):
                try:
                    search_result = client.live_source_search_dry_run(search_payload)
                except DashboardApiError as exc:
                    search_result = {
                        "status": "failed",
                        "country": country,
                        "disease": disease,
                        "request_count_total": 0,
                        "candidate_urls_visited": False,
                        "no_follow_links": True,
                        "warnings": [str(exc), "dry-run 安全失败：没有自动重试，也没有真实联网。"],
                    }
                    st.warning("数据源搜索 dry-run 安全失败：页面没有自动重试，也没有发起真实联网。")
            else:
                st.info("Dashboard client 尚未暴露 live source search dry-run 接口。")
        if st.button("运行受控联网数据源搜索"):
            if hasattr(client, "live_source_search_run"):
                try:
                    search_result = client.live_source_search_run(search_payload)
                except DashboardApiError as exc:
                    search_result = {
                        "status": "failed",
                        "country": country,
                        "disease": disease,
                        "request_count_total": "unknown",
                        "candidate_urls_visited": False,
                        "no_follow_links": True,
                        "warnings": [str(exc), "没有执行自动重试。"],
                    }
            else:
                st.info("Dashboard client 尚未暴露受控 live source search 接口。")
        if search_result:
            render_json_block("数据源搜索摘要", compact_live_source_search_summary(search_result))
            render_table(
                "Provider 诊断结果",
                rows_from_live_source_search_provider_diagnostics(search_result),
                empty_message="暂无 provider 诊断结果。",
            )
            render_table(
                "待人工审核候选源队列",
                rows_from_live_source_search_review_queue(search_result),
                empty_message="暂无待审核候选源。",
            )
            render_table(
                "Metadata 检查结果",
                rows_from_live_source_search_metadata_results(search_result),
                empty_message="暂无 metadata 检查结果。",
            )
            render_table(
                "ConnectorSpec 接入建议",
                rows_from_live_source_search_connector_specs(search_result),
                empty_message="暂无 ConnectorSpec 建议。",
            )

    with st.expander("候选源接入评审 Candidate Acquisition", expanded=False):
        st.warning(
            "候选源接入最多访问一个已确认的候选 URL；不会跟随链接、下载文件、自动启用数据源或展示原始响应正文。"
        )
        acquisition_run_dir = st.text_input(
            "已评审的数据源搜索运行目录",
            value="reports/live_source_search/search_measles_united_states_20260706T150829Z",
        )
        acquisition_output_dir = st.text_input(
            "接入结果输出目录",
            value="reports/live_source_search/acquisition_runs",
        )
        acquisition_allow_network = st.checkbox("允许本次接入联网请求", value=False)
        acquisition_terms = st.checkbox("已审查接入目标站点条款", value=False)
        acquisition_manual = st.checkbox("已人工批准接入", value=False)
        acquisition_payload = {
            "run_dir": acquisition_run_dir,
            "strategy": "top_confirmed",
            "max_requests": 1,
            "output_dir": acquisition_output_dir,
            "allow_network": bool(acquisition_allow_network),
            "terms_reviewed": bool(acquisition_terms),
            "manual_reviewed": bool(acquisition_manual),
        }
        if st.button("生成受控接入计划"):
            result = client.live_source_acquisition_plan(acquisition_payload)
            render_json_block("接入计划", compact_candidate_acquisition_summary(result))
        if st.button("执行受控接入"):
            result = client.live_source_acquisition_run(acquisition_payload)
            render_json_block("接入摘要", compact_candidate_acquisition_summary(result))
        st.divider()
        st.caption("本地质量诊断不会发起网络请求；只有质量闸门通过时才会生成模型特征。")
        quality_run_dir = st.text_input(
            "用于质量诊断的接入运行目录",
            value="reports/live_source_search/acquisition_runs/acquisition_measles_united_states_20260706T154307Z",
        )
        quality_database_url = st.text_input(
            "本地接入 SQLite 数据库 URL",
            value="sqlite:///reports/live_source_search/acquisition_runs/measles_candidate_acquisition.sqlite",
        )
        if st.button("诊断接入质量"):
            result = client.live_source_acquisition_quality_diagnose(
                {"run_dir": quality_run_dir, "database_url": quality_database_url}
            )
            render_json_block("接入质量摘要", compact_acquisition_quality_summary(result))

    search_results_text = st.text_area(
        "本地搜索结果 JSON（fixture/mock）",
        key="source_discovery_search_results_json",
        height=220,
    )

    if st.button("运行本地 fixture 数据源发现"):
        try:
            search_results = json.loads(search_results_text or "{}")
        except json.JSONDecodeError:
            st.error("搜索结果 JSON 无法解析。")
            return
        if not isinstance(search_results, dict):
            st.error("搜索结果 JSON 必须是对象。")
            return
        result = client.source_discovery_discover_fixture(
            country=country,
            disease=disease,
            search_results=search_results,
            min_source_score=float(min_source_score),
            generate_connector_specs=bool(generate_connector_specs),
        )
        render_json_block("数据源发现摘要", compact_source_discovery_summary(result))
        render_table("候选数据源排序", rows_from_source_discovery_candidates(result))
        render_table("ConnectorSpec 建议", rows_from_source_discovery_connector_specs(result))


def _source_discovery_sample_key_cn(disease: str) -> str:
    normalized = str(disease or "").strip().lower()
    if "flu" in normalized or "influenza" in normalized or "流感" in normalized:
        return "influenza"
    return "norovirus"


def render_crawler_optimizer_page(client):
    import streamlit as st

    st.header("爬虫优化诊断 Crawler Optimizer")
    st.info(
        "用途：分析数据源和原始文档的采集/解析失败原因，给出优化建议。"
        "本页只做诊断和建议，不会自动修改代码、不会自动上线新爬虫。"
    )
    st.warning(
        "高风险动作仍会标记为 requires_human_review=True；真实网络抓取必须经过访问安全闸门控制。"
    )
    country = st.text_input("诊断国家/地区", value="United States")
    disease = st.text_input("诊断病种", value="norovirus")
    limit_sources = st.number_input("最多分析数据源数", min_value=1, max_value=1000, value=100)
    limit_documents = st.number_input("最多分析原始文档数", min_value=1, max_value=2000, value=200)
    include_connector_specs = st.checkbox("生成 connector 建议", value=True)
    apply_safe_actions = st.checkbox("应用低/中风险安全动作", value=False)
    if apply_safe_actions:
        st.warning("即使启用安全动作，高风险动作也不会自动执行，仍需人工审核。")

    if st.button("运行爬虫优化诊断"):
        result = client.crawler_optimizer_analyze(
            country=_none(country),
            disease=_none(disease),
            limit_sources=int(limit_sources),
            limit_documents=int(limit_documents),
            include_connector_specs=bool(include_connector_specs),
            apply_safe_actions=bool(apply_safe_actions),
        )
        render_json_block("优化诊断摘要", compact_optimizer_summary(result))
        render_table("失败案例", rows_from_optimizer_failure_cases(result))
        render_table("建议动作", rows_from_optimizer_actions(result))
        render_table("Connector 建议", rows_from_connector_specs(result))

    with st.expander("为单个数据源生成 ConnectorSpec"):
        source_id = st.text_input("数据源 ID", value="")
        markdown = st.checkbox("返回 Markdown 草案", value=False)
        if st.button("生成 connector 规格建议"):
            result = client.crawler_optimizer_connector_spec(source_id=source_id, markdown=bool(markdown))
            render_json_block("Connector 规格摘要", compact_optimizer_summary(result))
            render_table("Connector 规格", rows_from_connector_specs(result))
            if markdown and result.get("markdown"):
                st.text_area("Connector 规格草案", value=str(result["markdown"]), height=240)

    with st.expander("分析单个数据源"):
        source_id = st.text_input("待分析数据源 ID", value="")
        if st.button("分析该数据源"):
            result = client.crawler_optimizer_analyze_source(
                source_id=source_id,
                country=_none(country),
                disease=_none(disease),
            )
            render_json_block("单源优化摘要", compact_optimizer_summary(result))
            render_table("单源失败案例", rows_from_optimizer_failure_cases(result))
            render_table("单源建议动作", rows_from_optimizer_actions(result))


def render_access_guardrails_page(client):
    import streamlit as st

    st.header("访问安全闸门 Access Guardrails")
    st.info("用途：在真实接入前检查访问策略。本页本身不抓取网页，live fetch 默认禁止。")
    st.warning("真实接入前必须人工审查 robots.txt、站点条款、访问频率和引用要求。")

    policies = _safe_call(client.get_access_policies, {})
    render_json_block("访问策略摘要", compact_access_policy_summary(policies))
    render_table("数据源访问策略", rows_from_access_policies(policies))

    with st.expander("访问决策检查", expanded=True):
        source_key = st.text_input("数据源 key", value="cdc_norostat")
        url = st.text_input("待访问 URL", value="https://example.invalid/test")
        allow_network = st.checkbox("允许联网标记", value=False)
        terms_reviewed = st.checkbox("已审查站点条款", value=False)
        manual_reviewed = st.checkbox("已人工批准", value=False)
        robots_allowed = st.checkbox("robots.txt 允许", value=False)
        request_count = st.number_input("本次请求次数", min_value=1, max_value=10000, value=1)
        if st.button("检查访问策略"):
            result = client.check_access_policy(
                source_key=source_key,
                url=_none(url),
                allow_network=bool(allow_network),
                terms_reviewed=bool(terms_reviewed),
                manual_reviewed=bool(manual_reviewed),
                robots_allowed=bool(robots_allowed),
                request_count=int(request_count),
            )
            render_json_block("访问决策结果", compact_access_decision(result))

def render_deepseek_web_search_page(client):
    import streamlit as st

    st.header("DeepSeek 全网病种数据源检索")
    st.info(
        "用途：输入国家和病种后，先生成检索计划，再通过 mock 或受控 live 模式汇总候选数据源与证据记录。"
        "默认不联网；只有勾选 DeepSeek/API、通用搜索、条款确认和人工确认后才允许真实调用。"
    )
    st.warning(
        "安全边界：不展示完整 HTML、raw text、prompt/completion、metadata/provenance/feature/lesson JSON；"
        "不自动启用数据源，不自动上线 connector，所有结论都需要人工复核。"
    )

    country = st.text_input("国家/地区", value="United States", key="deepseek_search_country")
    disease = st.text_input("病种", value="influenza", key="deepseek_search_disease")
    query_intent = st.selectbox(
        "检索意图",
        [
            "structured_time_series",
            "outbreak_information",
            "surveillance_summary",
            "data_source_discovery",
            "model_feature_evidence",
        ],
        index=0,
    )
    search_backend = st.selectbox(
        "检索后端",
        ["auto", "mock", "tavily", "openai_web_search", "perplexity", "bing", "brave", "serpapi", "exa"],
        index=2,
    )
    max_queries = st.number_input("最多查询数", min_value=1, max_value=6, value=4)
    max_results = st.number_input("每个查询最多结果", min_value=1, max_value=8, value=8)
    max_pages = st.number_input("最多读取页面数", min_value=0, max_value=8, value=8)
    max_read_success = st.number_input("成功读取页目标", min_value=0, max_value=8, value=8)
    max_http = st.number_input("总 HTTP 请求上限", min_value=0, max_value=16, value=16)
    prefer_official_structured = st.checkbox("优先官方/结构化数据源", value=True)
    stop_when_structured_found = st.checkbox("找到强结构化候选后停止", value=True)
    output_dir = st.text_input("本地报告目录", value="reports/deepseek_web_search")

    payload = {
        "country": country,
        "disease": disease,
        "query_intent": query_intent,
        "search_backend": search_backend,
        "max_search_queries": int(max_queries),
        "max_search_results_per_query": int(max_results),
        "max_pages_to_read": int(max_pages),
        "max_total_http_requests": int(max_http),
        "max_readable_pages_success": int(max_read_success),
        "stop_when_structured_candidates_found": bool(stop_when_structured_found),
        "prefer_official_and_structured_sources": bool(prefer_official_structured),
        "output_dir": output_dir,
        "allow_deepseek_api": False,
        "allow_general_web_search": False,
        "allow_page_read": False,
        "terms_reviewed": False,
        "manual_reviewed": False,
    }

    with st.expander("Structured Event Table Review / 结构化疫情事件表审批", expanded=True):
        st.caption(
            "输入病种、地区和时间范围后，系统生成候选事件表。所有行默认 pending_review，"
            "只有人工把行改为 approved 并显式允许写库后才会进入 epidemic_events。"
        )
        table_country = st.text_input("表格抽取国家/地区", value=country, key="deepseek_table_country")
        table_start = st.text_input("开始日期 YYYY-MM-DD", value="2026-05-01", key="deepseek_table_start")
        table_end = st.text_input("结束日期 YYYY-MM-DD", value="2026-07-01", key="deepseek_table_end")
        table_output_dir = st.text_input(
            "表格审查输出目录",
            value="reports/deepseek_table_review",
            key="deepseek_table_output_dir",
        )
        table_payload = {
            "disease": disease,
            "country_or_region": table_country,
            "date_start": table_start,
            "date_end": table_end,
            "max_sources": 3,
            "output_dir": table_output_dir,
            "allow_deepseek": False,
        }
        table_cols = st.columns(3)
        with table_cols[0]:
            if st.button("Build source pack"):
                result = client.deepseek_table_review_build_source_pack(table_payload)
                render_json_block("Source pack 摘要", result)
                render_table("Source pack", _deepseek_table_source_rows(result.get("sources", [])))
        with table_cols[1]:
            if st.button("Run mock table extraction"):
                result = client.deepseek_table_review_run_mock(table_payload)
                summary = result.get("summary", result)
                render_json_block("表格抽取摘要", summary)
                review_json = summary.get("review_json") or (summary.get("paths") or {}).get("review_json")
                if review_json:
                    try:
                        validation = client.deepseek_table_review_clean_validate({"review_table": review_json})
                        render_table("待审批事件表", _deepseek_table_review_rows(validation.get("rows", [])))
                        render_table("校验问题", _deepseek_table_issue_rows(validation.get("issues", [])))
                        _render_deepseek_table_visualization(st, validation.get("visualization", {}))
                    except DashboardApiError as exc:
                        st.warning(f"表格已生成，但校验预览失败：{exc}")
        with table_cols[2]:
            allow_table_deepseek = st.checkbox("允许本次真实 DeepSeek 表格抽取", value=False)
            if st.button("Run DeepSeek table extraction"):
                live_payload = {**table_payload, "allow_deepseek": bool(allow_table_deepseek)}
                result = client.deepseek_table_review_run_deepseek(live_payload)
                summary = result.get("summary", result)
                render_json_block("DeepSeek 表格抽取摘要", summary)
        st.divider()
        review_table_path = st.text_input(
            "审批表 JSON 路径",
            value="",
            key="deepseek_table_review_path",
        )
        review_cols = st.columns(3)
        with review_cols[0]:
            validate_clicked = st.button("Validate review table")
        with review_cols[1]:
            export_clicked = st.button("Export review table")
        with review_cols[2]:
            preview_clicked = st.button("Preview approval ingestion")
        if validate_clicked:
            if not review_table_path.strip():
                st.error("请先填写审批表 JSON 路径。")
            else:
                result = client.deepseek_table_review_clean_validate({"review_table": review_table_path.strip()})
                render_table("待审批事件表", _deepseek_table_review_rows(result.get("rows", [])))
                render_table("校验问题", _deepseek_table_issue_rows(result.get("issues", [])))
                _render_deepseek_table_visualization(st, result.get("visualization", {}))
        if export_clicked:
            if not review_table_path.strip():
                st.error("请先填写审批表 JSON 路径。")
            else:
                result = client.deepseek_table_review_export_review_table({"review_table": review_table_path.strip()})
                render_json_block("审批表导出路径", result)
        if preview_clicked:
            if not review_table_path.strip():
                st.error("请先填写审批表 JSON 路径。")
            else:
                result = client.deepseek_table_review_preview_ingestion({"review_table": review_table_path.strip()})
                render_json_block("审批入库预览", result)
        allow_table_persistence = st.checkbox("允许写入本地 pilot DB", value=False, key="deepseek_table_allow_persistence")
        table_database_url = st.text_input(
            "表格审批 pilot SQLite URL",
            value="sqlite:///reports/deepseek_table_review/deepseek_table_review.sqlite",
            key="deepseek_table_db_url",
        )
        if st.button("Ingest approved rows"):
            if not review_table_path.strip():
                st.error("请先填写审批表 JSON 路径。")
            else:
                result = client.deepseek_table_review_ingest_approved(
                    {
                        "review_table": review_table_path.strip(),
                        "database_url": table_database_url,
                        "allow_persistence": bool(allow_table_persistence),
                    }
                )
                render_json_block("审批入库摘要", result.get("summary", result))

    columns = st.columns(2)
    with columns[0]:
        if st.button("生成检索计划（不联网）"):
            result = client.deepseek_web_search_plan(payload)
            render_json_block("检索计划摘要", result)
    with columns[1]:
        if st.button("运行 mock 检索（不联网）"):
            result = client.deepseek_web_search_run(payload)
            render_json_block("mock 检索摘要", result.get("summary", result))

    with st.expander("受控 live 检索授权", expanded=False):
        st.caption("只有全部授权项满足时，API 才会按请求进入受控 live 模式；否则会自动降级为 mock。")
        allow_deepseek = st.checkbox("允许本次调用 DeepSeek API", value=False)
        allow_search = st.checkbox("允许本次通用搜索 API 请求", value=False)
        allow_page_read = st.checkbox("允许读取候选页面安全摘要", value=False)
        terms_reviewed = st.checkbox("已人工审查 provider/站点条款", value=False)
        manual_reviewed = st.checkbox("已人工批准本次受控检索", value=False)
        live_payload = {
            **payload,
            "allow_deepseek_api": bool(allow_deepseek),
            "allow_general_web_search": bool(allow_search),
            "allow_page_read": bool(allow_page_read),
            "terms_reviewed": bool(terms_reviewed),
            "manual_reviewed": bool(manual_reviewed),
        }
        if st.button("运行受控 live 检索"):
            try:
                result = client.deepseek_web_search_run(live_payload)
            except DashboardApiError as exc:
                result = {
                    "status": "failed",
                    "warnings": [str(exc), "Dashboard 不自动重试，也不访问候选 URL。"],
                }
            render_json_block("受控 live 检索摘要", result.get("summary", result))

    with st.expander("DeepSeek API 诊断与结构化 replay", expanded=False):
        st.caption("只显示 true/false、状态、failure_type 和建议修复；不显示 key、prompt、completion。")
        diagnose_allow = st.checkbox("允许本次 DeepSeek health check", value=False, key="deepseek_diag_allow")
        if st.button("诊断 DeepSeek API"):
            result = client.deepseek_api_diagnose(
                {"allow_deepseek_api": bool(diagnose_allow), "timeout_seconds": 20.0}
            )
            render_json_block("DeepSeek API 诊断摘要", result)
        replay_run_dir = st.text_input(
            "已有外部搜索验证目录",
            value="reports/deepseek_web_search/external_search_live_validation_20260708T011246Z",
            key="deepseek_replay_run_dir",
        )
        replay_allow = st.checkbox("允许本次 DeepSeek structured replay", value=False, key="deepseek_replay_allow")
        if st.button("执行结构化抽取 replay"):
            result = client.deepseek_replay_extraction(
                {
                    "run_dir": replay_run_dir,
                    "allow_deepseek_api": bool(replay_allow),
                    "output_dir": "reports/deepseek_web_search",
                }
            )
            render_json_block("DeepSeek replay 摘要", result.get("summary", result))

    with st.expander("DeepSeek 抽取质量审计", expanded=False):
        st.caption("只读取本地成功 replay 报告和本地持久化结果；不调用 DeepSeek，不重跑 Tavily，不访问候选 URL。")
        audit_report_dir = st.text_input(
            "DeepSeek 成功 replay 报告目录",
            value="reports/deepseek_web_search/deepseek_api_diagnostics_20260708T022808Z",
            key="deepseek_audit_report_dir",
        )
        if st.button("运行本地质量审计"):
            try:
                result = client.deepseek_extraction_quality_audit({"report_dir": audit_report_dir})
            except DashboardApiError as exc:
                result = {
                    "status": "failed",
                    "failure_type": "dashboard_api_error",
                    "report_dir": audit_report_dir,
                    "warnings": [
                        str(exc),
                        "没有调用 DeepSeek，没有重跑搜索，也没有访问候选 URL。",
                    ],
                    "limitations": "请确认报告目录中已有 replay 输出，或先执行结构化抽取 replay。",
                }
            render_json_block("DeepSeek 抽取质量审计摘要", result.get("summary", result))

    with st.expander("DeepSeek Extraction Readiness Review / 抽取结果就绪度复核", expanded=False):
        st.caption(
            "这些记录当前是 evidence / visualization 候选，不能直接当作 epidemic_events 或 model_features；"
            "需要人工复核或更结构化数据源。"
        )
        review_report_dir = st.text_input(
            "Readiness review 报告目录",
            value="reports/deepseek_web_search/deepseek_api_diagnostics_20260708T022808Z",
            key="deepseek_readiness_report_dir",
        )
        readiness_filter = st.selectbox(
            "Readiness filter",
            ["all", "evidence_only", "event_ready", "feature_ready", "visualization_ready"],
            index=0,
        )
        blocker_filter = st.selectbox(
            "Blocker filter",
            ["all", "insufficient_temporal_signal", "insufficient_numeric_signal", "insufficient_source_grounding", "requires_human_review"],
            index=0,
        )
        if st.button("加载就绪度复核表格"):
            summary = client.deepseek_readiness_review_summary(report_dir=review_report_dir)
            table = client.deepseek_readiness_review_table(
                report_dir=review_report_dir,
                readiness=None if readiness_filter == "all" else readiness_filter,
                blocker=None if blocker_filter == "all" else blocker_filter,
            )
            render_table("Readiness summary cards", _deepseek_readiness_summary_cards(summary))
            render_table("Blocker distribution", _deepseek_blocker_rows(summary))
            rows = _deepseek_readiness_rows(table.get("records", []))
            render_table("逐条复核表格", rows)
            if rows:
                render_json_block("第一条记录缺口解释", _deepseek_readiness_detail(rows[0]))
        if st.button("Export manual review queue"):
            result = client.deepseek_readiness_review_export({"report_dir": review_report_dir})
            render_json_block("Manual review queue export", result.get("summary", result))

    with st.expander("Human Review & Event Promotion / 人工复核与事件晋升门控", expanded=False):
        st.caption(
            "默认只验证标注，不写数据库。只有显式允许 persistence 且提供本地 SQLite URL 时，合格记录才会晋升为 epidemic_events。"
        )
        human_review_report_dir = st.text_input(
            "Human review 输入报告目录",
            value="reports/deepseek_web_search/deepseek_api_diagnostics_20260708T022808Z",
            key="deepseek_human_review_report_dir",
        )
        human_review_output_dir = st.text_input(
            "Human review 输出目录",
            value="reports/deepseek_web_search/deepseek_api_diagnostics_20260708T022808Z/human_review",
            key="deepseek_human_review_output_dir",
        )
        annotation_file = st.text_input(
            "Annotation file path",
            value="reports/deepseek_web_search/deepseek_api_diagnostics_20260708T022808Z/human_review/deepseek_human_review_template.json",
            key="deepseek_human_review_annotation_file",
        )
        allow_persistence = st.checkbox("允许写入本地 pilot DB", value=False, key="deepseek_human_review_allow_persistence")
        promotion_database_url = st.text_input(
            "本地 pilot SQLite URL",
            value="sqlite:///reports/deepseek_web_search/deepseek_human_review_promotion.sqlite",
            key="deepseek_human_review_database_url",
        )
        if st.button("Export annotation template"):
            result = client.deepseek_human_review_export_template(
                {"report_dir": human_review_report_dir, "output_dir": human_review_output_dir}
            )
            render_json_block("Annotation template export", result.get("summary", result))
        if st.button("Import annotations / run promotion gate"):
            payload = {
                "report_dir": human_review_report_dir,
                "annotation_file": annotation_file,
                "output_dir": human_review_output_dir,
                "allow_persistence": bool(allow_persistence),
                "database_url": promotion_database_url if allow_persistence else None,
            }
            result = client.deepseek_human_review_import_annotations(payload)
            summary = result.get("summary", result)
            render_json_block("Annotation validation summary", _deepseek_human_review_summary(summary))
            render_table("Promotion decisions", _deepseek_promotion_decision_rows(summary))
            render_json_block("Feature eligibility after promotion", _deepseek_feature_eligibility_summary(summary))
        if st.button("View latest promotion report"):
            result = client.deepseek_human_review_promotion_report(output_dir=human_review_output_dir)
            render_json_block("Promotion report", result)
        st.caption("Annotation Workspace：用于本地编辑和预览，不写数据库，不调用 DeepSeek。")
        workspace_output_dir = st.text_input(
            "Annotation workspace 输出目录",
            value="reports/deepseek_web_search/deepseek_api_diagnostics_20260708T022808Z/human_review_workspace",
            key="deepseek_annotation_workspace_output_dir",
        )
        draft_file = st.text_input(
            "Annotation draft file path",
            value="reports/deepseek_web_search/deepseek_api_diagnostics_20260708T022808Z/human_review_workspace/annotation_draft_template.json",
            key="deepseek_annotation_workspace_draft_file",
        )
        if st.button("Load readiness records"):
            result = client.deepseek_annotation_workspace_rows(report_dir=human_review_report_dir)
            render_table("Annotation Workspace", _deepseek_annotation_workspace_rows(result.get("rows", [])))
        if st.button("Save annotation draft"):
            result = client.deepseek_annotation_workspace_export_template(
                {"report_dir": human_review_report_dir, "output_dir": workspace_output_dir}
            )
            render_json_block("Annotation draft saved", result.get("summary", result))
        if st.button("Validate annotations"):
            result = client.deepseek_annotation_workspace_validate({"annotation_file": draft_file})
            render_json_block("Annotation validation preview", result)
            render_table("Gate preview", _deepseek_annotation_decision_rows(result.get("decisions", [])))
        if st.button("Preview promotion decisions"):
            result = client.deepseek_annotation_workspace_preview_promotion({"annotation_file": draft_file})
            render_table("Promotion decision preview", _deepseek_annotation_decision_rows(result.get("decisions", [])))
        if st.button("Export manual review bundle"):
            result = client.deepseek_annotation_workspace_export_bundle(
                {"report_dir": human_review_report_dir, "output_dir": workspace_output_dir}
            )
            render_json_block("Manual review bundle", result.get("summary", result))

    st.divider()
    run_id = st.text_input("运行编号（用于查看记录/送清洗）", value="", key="deepseek_search_run_id")
    if st.button("查看最近运行"):
        result = client.deepseek_web_search_runs(limit=20)
        render_table("最近运行", _deepseek_rows(result.get("runs", [])))
    if st.button("查看信息记录"):
        result = client.deepseek_web_search_records(
            disease=_none(disease),
            country=_none(country),
            run_id=_none(run_id),
            limit=100,
        )
        render_table("疾病-国家信息记录", _deepseek_rows(result.get("records", [])))
    if st.button("将运行结果送入清洗桥接"):
        if not run_id.strip():
            st.error("请先填写运行编号。")
        else:
            result = client.deepseek_web_search_send_to_cleaning({"run_id": run_id.strip()})
            render_json_block("清洗桥接摘要", result.get("summary", result))
    if st.button("生成 3D 可视化数据"):
        result = client.deepseek_web_search_3d_data(disease=disease, country=country, mode="auto")
        render_json_block("3D 可视化数据摘要", _deepseek_3d_summary(result))
        render_table("3D 点位预览", _deepseek_rows((result.get("points") or [])[:50]))


def _deepseek_rows(rows):
    cleaned = []
    for row in rows or []:
        if isinstance(row, dict):
            cleaned.append(
                {
                    key: value
                    for key, value in sanitize_dict(row).items()
                    if key
                    not in {
                        "metadata_json",
                        "provenance_json",
                        "feature_json",
                        "lesson_json",
                        "raw_text",
                        "html",
                        "prompt",
                        "completion",
                    }
                }
            )
    return cleaned


def _deepseek_table_source_rows(rows):
    cleaned = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        cleaned.append(
            sanitize_dict(
                {
                    "source_id": row.get("source_id"),
                    "title": row.get("title_summary"),
                    "domain": row.get("domain"),
                    "source_type": row.get("source_type"),
                    "confidence": row.get("confidence"),
                    "full_text_saved": row.get("full_text_saved"),
                }
            )
        )
    return cleaned


def _deepseek_table_review_rows(rows):
    cleaned = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        cleaned.append(
            sanitize_dict(
                {
                    "row_id": row.get("row_id"),
                    "date": row.get("date"),
                    "country": row.get("country"),
                    "region": row.get("region"),
                    "confirmed_cases": row.get("confirmed_cases"),
                    "confirmed_deaths": row.get("confirmed_deaths"),
                    "suspected_cases": row.get("suspected_cases"),
                    "contacts_under_monitoring": row.get("contacts_under_monitoring"),
                    "source_id": row.get("source_id"),
                    "date_source": row.get("date_source"),
                    "date_anchor_id": row.get("date_anchor_id"),
                    "metric_anchor_id": row.get("metric_anchor_id"),
                    "date_confidence": row.get("date_confidence"),
                    "confidence": row.get("extraction_confidence"),
                    "validation_status": row.get("validation_status"),
                    "approval_status": row.get("approval_status"),
                    "reviewer_note": row.get("reviewer_note"),
                }
            )
        )
    return cleaned


def _deepseek_table_issue_rows(rows):
    cleaned = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        cleaned.append(
            sanitize_dict(
                {
                    "row_id": row.get("row_id"),
                    "field": row.get("field"),
                    "issue_type": row.get("issue_type"),
                    "severity": row.get("severity"),
                    "message": row.get("message"),
                    "suggested_fix": row.get("suggested_fix"),
                }
            )
        )
    return cleaned


def _render_deepseek_table_visualization(st, visualization):
    if not isinstance(visualization, dict) or not visualization:
        return
    summary = visualization.get("visualization_summary") or {}
    time_series = visualization.get("time_series_data") or []
    region_summary = visualization.get("country_region_summary") or []
    source_summary = visualization.get("source_contribution_summary") or []
    metric_totals = visualization.get("metric_totals_summary") or {}
    render_json_block("表格可视化摘要", summary)
    if time_series:
        render_table("时间序列汇总", _deepseek_rows(time_series))
        if hasattr(st, "line_chart"):
            chart_rows = [
                {
                    "date": row.get("date"),
                    "confirmed_cases": row.get("confirmed_cases"),
                    "suspected_cases": row.get("suspected_cases"),
                    "confirmed_deaths": row.get("confirmed_deaths"),
                    "contacts_under_monitoring": row.get("contacts_under_monitoring"),
                }
                for row in time_series
                if isinstance(row, dict)
            ]
            try:
                st.line_chart(chart_rows, x="date")
            except Exception:
                st.caption("时间序列图暂不可渲染，已保留表格预览。")
    render_table("国家 / 地区聚合", _deepseek_rows(region_summary))
    render_table("来源贡献汇总", _deepseek_rows(source_summary))
    render_table("指标总计", [{"metric": key, "value": value} for key, value in (metric_totals or {}).items()])
    optional_3d = visualization.get("optional_3d_visualization_data") or {}
    if isinstance(optional_3d, dict) and optional_3d.get("status") == "not_available":
        st.caption("没有坐标字段，因此不生成地图或 3D 点位；系统不会伪造地理坐标。")


def _deepseek_readiness_summary_cards(summary):
    summary = sanitize_dict(summary or {})
    return [
        {"metric": "total records", "value": summary.get("total_records", 0)},
        {"metric": "evidence only", "value": summary.get("evidence_only_count", 0)},
        {"metric": "event ready", "value": summary.get("event_ready_count", 0)},
        {"metric": "feature ready", "value": summary.get("feature_ready_count", 0)},
        {"metric": "visualization ready", "value": summary.get("visualization_ready_count", 0)},
        {"metric": "requires human review", "value": summary.get("requires_human_review_count", 0)},
    ]


def _deepseek_blocker_rows(summary):
    distribution = (summary or {}).get("blocker_distribution") or {}
    rows = []
    if isinstance(distribution, dict):
        for blocker, count in distribution.items():
            rows.append({"blocker": blocker, "count": count})
    return [sanitize_dict(row) for row in rows]


def _deepseek_readiness_rows(rows):
    cleaned = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        cleaned.append(
            sanitize_dict(
                {
                    "record_id": row.get("record_id"),
                    "disease": row.get("disease"),
                    "country": row.get("country"),
                    "source_domain": row.get("source_domain"),
                    "readiness_class": row.get("readiness_class"),
                    "event_ready": row.get("event_ready"),
                    "feature_ready": row.get("feature_ready"),
                    "visualization_ready": row.get("visualization_ready"),
                    "missing_fields": ", ".join(row.get("missing_fields") or []),
                    "blocker_types": ", ".join(row.get("blocker_types") or []),
                    "recommended_action": row.get("recommended_action"),
                    "evidence_summary": row.get("evidence_summary"),
                    "why_not_event_ready": row.get("why_not_event_ready"),
                    "why_not_feature_ready": row.get("why_not_feature_ready"),
                    "source_reference_summary": row.get("source_reference_summary"),
                    "safety_flags": ", ".join(row.get("safety_flags") or []),
                }
            )
        )
    return cleaned


def _deepseek_readiness_detail(row):
    return sanitize_dict(
        {
            "evidence_summary": row.get("evidence_summary"),
            "why_not_event_ready": row.get("why_not_event_ready"),
            "why_not_feature_ready": row.get("why_not_feature_ready"),
            "source_reference_summary": row.get("source_reference_summary"),
            "safety_flags": row.get("safety_flags"),
        }
    )


def _deepseek_human_review_summary(payload):
    result = (payload or {}).get("result") if isinstance((payload or {}).get("result"), dict) else payload or {}
    return sanitize_dict(
        {
            "status": payload.get("status") if isinstance(payload, dict) else None,
            "imported_annotations": result.get("imported_annotations"),
            "approved_event_count": result.get("approved_event_count"),
            "blocked_count": result.get("blocked_count"),
            "promoted_epidemic_events": result.get("promoted_epidemic_events"),
            "feature_generation_allowed": result.get("feature_generation_allowed"),
            "feature_eligibility_blockers": result.get("feature_eligibility_blockers"),
            "paths": (payload or {}).get("paths"),
        }
    )


def _deepseek_promotion_decision_rows(payload):
    result = (payload or {}).get("result") if isinstance((payload or {}).get("result"), dict) else payload or {}
    rows = []
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
                    "recommended_action": decision.get("recommended_action"),
                }
            )
        )
    return rows


def _deepseek_feature_eligibility_summary(payload):
    result = (payload or {}).get("result") if isinstance((payload or {}).get("result"), dict) else payload or {}
    return sanitize_dict(
        {
            "feature_generation_allowed": result.get("feature_generation_allowed"),
            "feature_eligibility_blockers": result.get("feature_eligibility_blockers") or [],
            "promoted_epidemic_events": result.get("promoted_epidemic_events"),
            "note": "Model features are not generated unless eligibility passes; missing weeks are not fabricated.",
        }
    )


def _deepseek_annotation_workspace_rows(rows):
    cleaned = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        cleaned.append(
            sanitize_dict(
                {
                    "record_id": row.get("record_id"),
                    "source_domain": row.get("source_domain"),
                    "readiness_class": row.get("readiness_class"),
                    "missing_fields": ", ".join(row.get("missing_fields") or []),
                    "suggested_review_action": row.get("suggested_review_action"),
                    "reviewer_decision": row.get("reviewer_decision"),
                    "confirmed_event_date": row.get("confirmed_event_date"),
                    "metric_type": row.get("metric_type"),
                    "metric_value": row.get("metric_value"),
                    "source_grounding_confirmed": row.get("source_grounding_confirmed"),
                    "gate_preview": row.get("gate_preview"),
                    "evidence_summary": row.get("evidence_summary"),
                }
            )
        )
    return cleaned


def _deepseek_annotation_decision_rows(rows):
    cleaned = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        cleaned.append(
            sanitize_dict(
                {
                    "record_id": row.get("record_id"),
                    "reviewer_decision": row.get("reviewer_decision"),
                    "gate_status": row.get("gate_status"),
                    "blocker_reason": ", ".join(row.get("blocker_reason") or []),
                    "promoted_event_id": row.get("promoted_event_id"),
                    "recommended_action": row.get("recommended_action"),
                }
            )
        )
    return cleaned


def _deepseek_3d_summary(payload):
    safe = sanitize_dict(payload or {})
    return {
        "disease": safe.get("disease"),
        "country": safe.get("country"),
        "mode": safe.get("mode"),
        "point_count": len(safe.get("points") or []),
        "axes": safe.get("axes"),
        "warnings": safe.get("warnings", []),
    }


def render_deepseek_web_search_page(client):
    import streamlit as st

    state = _get_streamlit_state(st)

    st.header("DeepSeek 全网检索与结构化表格审批")
    st.info(
        "本页分成两条清晰流程：1）Tavily 真实联网检索并读取候选页面，再交给 DeepSeek 做结构化分析；"
        "2）DeepSeek 表格抽取是基于已准备的来源摘要生成待审批事件表，不等同于自动全网搜索。"
    )
    st.warning(
        "安全边界：不显示完整网页正文、原始 HTML、prompt、completion、metadata_json、provenance_json 或 feature_json；"
        "所有抽取结果都需要人工复核后才能写入本地数据库。"
    )

    country = st.text_input("国家 / 地区", value="United States", key="deepseek_cn_country")
    disease = st.text_input("病种", value="influenza", key="deepseek_cn_disease")
    output_dir = st.text_input("检索报告保存目录", value="reports/deepseek_web_search", key="deepseek_cn_output_dir")

    st.subheader("完整演示流程")
    st.caption(
        "按下面 4 个按钮从左到右操作：第 1 步是真实 Tavily 全网检索；"
        "第 2 步调用 DeepSeek 生成审批表；第 3 步展示表格和图表；第 4 步人工审核后入库。"
    )
    flow_country = country
    flow_disease = disease
    flow_table_output_dir = st.text_input(
        "审批表与可视化输出目录",
        value="reports/deepseek_table_review",
        key="deepseek_flow_table_output_dir",
    )
    flow_dates = st.columns(2)
    with flow_dates[0]:
        flow_start = st.text_input("分析开始日期", value="2026-05-01", key="deepseek_flow_start")
    with flow_dates[1]:
        flow_end = st.text_input("分析结束日期", value="2026-07-01", key="deepseek_flow_end")
    flow_auth = st.checkbox(
        "我已确认：允许本次受控调用 Tavily、读取最多 8 个页面摘要、调用 DeepSeek，并已人工确认站点/服务条款",
        value=False,
        key="deepseek_flow_auth",
    )
    flow_cols = st.columns(4)
    with flow_cols[0]:
        st.markdown("**第一步：自动联网检索**")
        st.caption("Tavily 搜索病种相关数据源，最多读取 8 个页面摘要；不跟随链接、不下载文件。")
        if st.button("第一步：Tavily 检索网页"):
            if not flow_auth:
                st.error("请先勾选流程授权确认。")
            else:
                result = client.deepseek_web_search_run(
                    {
                        "country": flow_country,
                        "disease": flow_disease,
                        "query_intent": "structured_time_series",
                        "search_backend": "tavily",
                        "max_search_queries": 4,
                        "max_search_results_per_query": 8,
                        "max_pages_to_read": 8,
                        "max_total_http_requests": 16,
                        "max_readable_pages_success": 8,
                        "stop_when_structured_candidates_found": True,
                        "prefer_official_and_structured_sources": True,
                        "output_dir": output_dir,
                        "allow_deepseek_api": True,
                        "allow_general_web_search": True,
                        "allow_page_read": True,
                        "terms_reviewed": True,
                        "manual_reviewed": True,
                    }
                )
                summary = result.get("summary", result)
                state["last_deepseek_web_search_summary"] = summary
                state["last_tavily_source_pack"] = _deepseek_search_source_pack_path(summary)
                st.success("检索完成，已保存来源摘要。")
                _render_deepseek_run_result(st, summary)
    with flow_cols[1]:
        st.markdown("**第二步：DeepSeek 分析抽取**")
        st.caption("基于上一步 Tavily 结果生成结构化事件审批表。")
        if st.button("第二步：生成结构化审批表"):
            if not flow_auth:
                st.error("请先勾选流程授权确认。")
            else:
                source_pack = state.get("last_tavily_source_pack")
                if not source_pack:
                    st.warning("还没有可用的 Tavily 来源摘要，请先点击第 1 步。")
                else:
                    result = client.deepseek_table_review_run_deepseek(
                        {
                            "disease": flow_disease,
                            "country_or_region": flow_country,
                            "date_start": flow_start,
                            "date_end": flow_end,
                            "max_sources": 3,
                            "source_pack": source_pack,
                            "output_dir": flow_table_output_dir,
                            "allow_deepseek": True,
                            "allow_live_source_search": False,
                            "allow_network": False,
                            "terms_reviewed": True,
                            "manual_reviewed": True,
                        }
                    )
                    summary = result.get("summary", result)
                    _remember_deepseek_review_table(state, summary)
                    st.success("审批表已生成。")
                    _render_table_review_result(st, summary)
    with flow_cols[2]:
        st.markdown("**第三步：可视化预览**")
        st.caption("选择最新审批表，展示事件表、校验问题和趋势图。")
        if st.button("第三步：显示表格和图表"):
            selected_path = _latest_or_remembered_review_table(state, flow_table_output_dir)
            if not selected_path:
                st.warning("还没有可视化数据，请先生成审批表。")
            else:
                validation = client.deepseek_table_review_clean_validate({"review_table": selected_path})
                state["last_deepseek_review_table"] = selected_path
                render_table("待审批事件表", _deepseek_table_review_rows(validation.get("rows", [])))
                render_table("校验问题", _deepseek_table_issue_rows(validation.get("issues", [])), empty_message="未发现校验问题")
                _render_deepseek_table_visualization(st, validation.get("visualization", {}))
    with flow_cols[3]:
        st.markdown("**第四步：人工审核入库**")
        st.caption("人工批准行后，才允许写入本地数据库。")
        if st.button("第四步：打开审核表"):
            selected_path = _latest_or_remembered_review_table(state, flow_table_output_dir)
            if not selected_path:
                st.warning("还没有可入库的审批表。")
            else:
                state["last_deepseek_review_table"] = selected_path
                state["show_flow_review_editor"] = True

    if state.get("show_flow_review_editor"):
        selected_path = _latest_or_remembered_review_table(state, flow_table_output_dir)
        if selected_path:
            st.subheader("第 4 步：人工审核与入库")
            validation = client.deepseek_table_review_clean_validate({"review_table": selected_path})
            rows = _deepseek_table_review_rows(validation.get("rows", []))
            edited_rows = _render_editable_review_rows(st, rows, key="deepseek_flow_review_editor")
            state["_deepseek_flow_edited_rows"] = edited_rows
            flow_action_cols = st.columns(3)
            with flow_action_cols[0]:
                if st.button("保存审批修改"):
                    save_result = _save_deepseek_review_table_edits(selected_path, state.get("_deepseek_flow_edited_rows") or [])
                    st.success(f"审批修改已保存：更新 {save_result['updated_rows']} 行。")
                    render_summary_cards(
                        {
                            "已批准": save_result["approved"],
                            "待复核": save_result["pending_review"],
                            "需修改": save_result["edit_required"],
                            "已拒绝": save_result["rejected"],
                        }
                    )
            with flow_action_cols[1]:
                if st.button("预览入库影响"):
                    preview = client.deepseek_table_review_preview_ingestion({"review_table": selected_path})
                    _render_ingestion_preview(st, preview)
            with flow_action_cols[2]:
                allow_flow_persistence = st.checkbox(
                    "允许写入本地 pilot DB",
                    value=False,
                    key="deepseek_flow_allow_persistence",
                )
                if st.button("写入已批准行"):
                    ingest_result = client.deepseek_table_review_ingest_approved(
                        {
                            "review_table": selected_path,
                            "database_url": "sqlite:///reports/deepseek_table_review/deepseek_table_review.sqlite",
                            "allow_persistence": bool(allow_flow_persistence),
                        }
                    )
                    _render_ingestion_preview(st, ingest_result.get("summary", ingest_result))

    with st.expander("高级设置：真实联网检索与 DeepSeek 分析", expanded=False):
        st.caption(
            "这里才是自动联网检索：Tavily 搜索候选网页，可按上限读取页面安全摘要，然后调用 DeepSeek 做结构化理解。"
        )
        search_cols = st.columns(4)
        with search_cols[0]:
            search_backend = st.selectbox(
                "检索后端",
                ["tavily", "mock", "auto", "openai_web_search", "perplexity", "bing", "brave", "exa", "serpapi"],
                index=0,
            )
        with search_cols[1]:
            query_intent = st.selectbox(
                "检索目标",
                ["structured_time_series", "outbreak_information", "surveillance_summary", "data_source_discovery"],
                index=0,
            )
        with search_cols[2]:
            max_pages = st.number_input("读取页面数上限", min_value=0, max_value=8, value=8)
        with search_cols[3]:
            max_results = st.number_input("每个查询结果数", min_value=1, max_value=8, value=8)
        max_read_success = st.number_input("成功读取页目标", min_value=0, max_value=8, value=8)
        max_total_http = st.number_input("总 HTTP 请求上限", min_value=0, max_value=16, value=16)
        prefer_official_structured = st.checkbox("优先官方/结构化数据源", value=True, key="deepseek_cn_prefer_official")
        stop_when_structured_found = st.checkbox("找到强结构化候选后停止", value=True, key="deepseek_cn_stop_structured")

        allow_search = st.checkbox("允许本次调用 Tavily / 通用搜索 API", value=False, key="deepseek_cn_allow_search")
        allow_page_read = st.checkbox("允许读取候选页面安全摘要", value=False, key="deepseek_cn_allow_page_read")
        allow_deepseek = st.checkbox("允许本次调用 DeepSeek API", value=False, key="deepseek_cn_allow_deepseek")
        terms_reviewed = st.checkbox("已确认本次检索符合 provider 与站点条款", value=False, key="deepseek_cn_terms")
        manual_reviewed = st.checkbox("人工批准本次受控真实检索", value=False, key="deepseek_cn_manual")

        if st.button("执行真实检索与分析"):
            live_payload = {
                "country": country,
                "disease": disease,
                "query_intent": query_intent,
                "search_backend": search_backend,
                "max_search_queries": 4,
                "max_search_results_per_query": int(max_results),
                "max_pages_to_read": int(max_pages),
                "max_total_http_requests": int(max_total_http),
                "max_readable_pages_success": int(max_read_success),
                "stop_when_structured_candidates_found": bool(stop_when_structured_found),
                "prefer_official_and_structured_sources": bool(prefer_official_structured),
                "output_dir": output_dir,
                "allow_deepseek_api": bool(allow_deepseek),
                "allow_general_web_search": bool(allow_search),
                "allow_page_read": bool(allow_page_read),
                "terms_reviewed": bool(terms_reviewed),
                "manual_reviewed": bool(manual_reviewed),
            }
            try:
                result = client.deepseek_web_search_run(live_payload)
                summary = result.get("summary", result)
                state["last_deepseek_web_search_summary"] = summary
                _render_deepseek_run_result(st, summary)
            except DashboardApiError as exc:
                st.error("真实检索没有完成。Dashboard 不会自动重试，也不会访问候选 URL。")
                st.caption(str(exc))

        if state.get("last_deepseek_web_search_summary"):
            st.subheader("最近一次检索结果")
            _render_deepseek_run_result(st, state["last_deepseek_web_search_summary"])

    with st.expander("高级设置：生成结构化事件审批表", expanded=False):
        st.caption(
            "这里不是自动全网搜索。它会基于来源包或受控来源发现结果，让 DeepSeek 抽取日期、地区、病例数等字段，生成待人工审批的表格。"
        )
        table_cols = st.columns(4)
        with table_cols[0]:
            table_country = st.text_input("表格抽取地区", value=country, key="deepseek_cn_table_country")
        with table_cols[1]:
            table_start = st.text_input("开始日期", value="2026-05-01", key="deepseek_cn_table_start")
        with table_cols[2]:
            table_end = st.text_input("结束日期", value="2026-07-01", key="deepseek_cn_table_end")
        with table_cols[3]:
            max_sources = st.number_input("最多来源数", min_value=1, max_value=10, value=3, key="deepseek_cn_max_sources")

        table_output_dir = st.text_input("表格输出目录", value="reports/deepseek_table_review", key="deepseek_cn_table_output_dir")
        allow_table_deepseek = st.checkbox("允许本次调用 DeepSeek 生成审批表", value=False, key="deepseek_cn_allow_table_deepseek")
        allow_source_search = st.checkbox(
            "表格抽取前允许受控发现来源",
            value=False,
            key="deepseek_cn_allow_source_search",
            help="打开后才会在表格抽取流程里尝试受控来源发现；否则只使用现有/fixture 来源包。",
        )
        allow_table_network = st.checkbox("允许表格流程使用受控网络请求", value=False, key="deepseek_cn_table_network")
        table_terms = st.checkbox("已确认表格流程来源访问条款", value=False, key="deepseek_cn_table_terms")
        table_manual = st.checkbox("人工批准本次表格抽取", value=False, key="deepseek_cn_table_manual")

        table_payload = {
            "disease": disease,
            "country_or_region": table_country,
            "date_start": table_start,
            "date_end": table_end,
            "max_sources": int(max_sources),
            "output_dir": table_output_dir,
            "allow_deepseek": False,
            "allow_live_source_search": bool(allow_source_search),
            "allow_network": bool(allow_table_network),
            "terms_reviewed": bool(table_terms),
            "manual_reviewed": bool(table_manual),
            "providers": "auto",
            "max_total_provider_requests": 3,
        }

        action_cols = st.columns(3)
        with action_cols[0]:
            if st.button("构建来源包"):
                result = client.deepseek_table_review_build_source_pack(table_payload)
                st.success("来源包已生成。")
                _render_source_pack_result(st, result)
        with action_cols[1]:
            if st.button("生成模拟审批表"):
                result = client.deepseek_table_review_run_mock(table_payload)
                summary = result.get("summary", result)
                _remember_deepseek_review_table(state, summary)
                _render_table_review_result(st, summary)
        with action_cols[2]:
            if st.button("调用 DeepSeek 生成审批表"):
                live_table_payload = {**table_payload, "allow_deepseek": bool(allow_table_deepseek)}
                result = client.deepseek_table_review_run_deepseek(live_table_payload)
                summary = result.get("summary", result)
                _remember_deepseek_review_table(state, summary)
                _render_table_review_result(st, summary)

    with st.expander("高级设置：选择上一步生成的表格并人工审批", expanded=False):
        st.caption("不需要手填路径。下拉框会自动列出最近生成的审批表，并优先选择上一步刚生成的表格。")
        if st.button("刷新可审批表格列表"):
            state.pop("_deepseek_review_table_options", None)

        options = _discover_deepseek_review_tables(table_output_dir)
        remembered = state.get("last_deepseek_review_table")
        if remembered and all(item["path"] != remembered for item in options):
            options.insert(0, _review_table_option(remembered))
        state["_deepseek_review_table_options"] = options

        if not options:
            st.warning("还没有发现可审批表格。请先执行“生成模拟审批表”或“调用 DeepSeek 生成审批表”。")
            return

        labels = [item["label"] for item in options]
        default_index = 0
        if remembered:
            for index, item in enumerate(options):
                if item["path"] == remembered:
                    default_index = index
                    break
        selected_label = st.selectbox("选择审批表", labels, index=default_index)
        selected_path = options[labels.index(selected_label)]["path"]
        state["last_deepseek_review_table"] = selected_path

        review_cols = st.columns(4)
        with review_cols[0]:
            validate_clicked = st.button("预览并校验")
        with review_cols[1]:
            save_clicked = st.button("保存审批修改")
        with review_cols[2]:
            preview_clicked = st.button("预览入库影响")
        with review_cols[3]:
            export_clicked = st.button("导出审批文件")

        validation = None
        if validate_clicked or save_clicked or preview_clicked:
            validation = client.deepseek_table_review_clean_validate({"review_table": selected_path})
            rows = _deepseek_table_review_rows(validation.get("rows", []))
            issues = _deepseek_table_issue_rows(validation.get("issues", []))
            st.subheader("待审批事件表")
            edited_rows = _render_editable_review_rows(st, rows)
            state["_deepseek_last_edited_review_rows"] = edited_rows
            render_table("校验问题", issues, empty_message="未发现校验问题")
            _render_deepseek_table_visualization(st, validation.get("visualization", {}))

        if save_clicked:
            edited_rows = state.get("_deepseek_last_edited_review_rows") or []
            result = _save_deepseek_review_table_edits(selected_path, edited_rows)
            st.success(f"审批修改已保存：更新 {result['updated_rows']} 行。")
            render_summary_cards({"已批准": result["approved"], "待复核": result["pending_review"], "需修改": result["edit_required"], "已拒绝": result["rejected"]})

        if preview_clicked:
            result = client.deepseek_table_review_preview_ingestion({"review_table": selected_path})
            _render_ingestion_preview(st, result)

        if export_clicked:
            result = client.deepseek_table_review_export_review_table({"review_table": selected_path})
            st.success("审批文件已导出。")
            render_table("导出文件", _path_summary_rows(result))

        st.divider()
        allow_table_persistence = st.checkbox("允许把已批准行写入本地 pilot DB", value=False, key="deepseek_cn_allow_persistence")
        table_database_url = st.text_input(
            "本地 pilot SQLite URL",
            value="sqlite:///reports/deepseek_table_review/deepseek_table_review.sqlite",
            key="deepseek_cn_table_database_url",
        )
        if st.button("写入已批准行"):
            result = client.deepseek_table_review_ingest_approved(
                {
                    "review_table": selected_path,
                    "database_url": table_database_url,
                    "allow_persistence": bool(allow_table_persistence),
                }
            )
            _render_ingestion_preview(st, result.get("summary", result))


def _render_deepseek_run_result(st, summary):
    safe = sanitize_dict(summary or {})
    render_summary_cards(
        {
            "状态": safe.get("status", "unknown"),
            "DeepSeek 调用": safe.get("deepseek_call_count", 0),
            "读取页面": safe.get("page_read_request_count", 0),
            "持久化证据": safe.get("records_persisted", 0),
        }
    )
    render_summary_cards(
        {
            "搜索候选": safe.get("search_results_seen", 0),
            "成功读页": safe.get("readable_pages_success", 0),
            "阻断页面": safe.get("blocked_pages_count", 0),
            "结构化候选": safe.get("structured_candidate_count", 0),
        }
    )
    cleaning = safe.get("cleaning") if isinstance(safe.get("cleaning"), dict) else {}
    render_summary_cards(
        {
            "原始文档": safe.get("raw_documents_created", cleaning.get("raw_documents_created", 0)),
            "疫情事件": safe.get("epidemic_events_created", cleaning.get("epidemic_events_created", 0)),
            "可生成特征": safe.get("feature_ready_records", cleaning.get("feature_ready_records", 0)),
        }
    )
    warnings = safe.get("warnings") or []
    if warnings:
        st.warning("；".join(str(item) for item in warnings[:3]))


def _render_source_pack_result(st, result):
    safe = sanitize_dict(result or {})
    render_summary_cards(
        {
            "来源数量": safe.get("source_count", 0),
            "是否联网": safe.get("network_accessed", False),
            "provider 请求": safe.get("provider_request_count_total", 0),
            "DeepSeek 调用": safe.get("deepseek_called", False),
        }
    )
    render_table("候选来源摘要", _deepseek_table_source_rows(safe.get("sources", [])), empty_message="暂无来源")


def _render_table_review_result(st, summary):
    safe = sanitize_dict(summary or {})
    _remember_deepseek_review_table(_get_streamlit_state(st), safe)
    render_summary_cards(
        {
            "状态": safe.get("status", "unknown"),
            "DeepSeek 调用": safe.get("deepseek_call_count", 1 if safe.get("deepseek_called") else 0),
            "抽取行数": safe.get("cleaned_rows", safe.get("mock_rows_generated", 0)),
            "校验问题": safe.get("validation_issues_count", 0),
        }
    )
    if safe.get("review_json"):
        st.success("已生成审批表，下一步可直接在下方下拉框中选择。")
    warnings = safe.get("warnings") or []
    if warnings:
        st.warning("；".join(str(item) for item in warnings[:3]))


def _render_table_review_workspace(st, client, review_path):
    if not review_path:
        st.warning("还没有结构化审批表。请先生成审批表。")
        return {}
    validation = client.deepseek_table_review_clean_validate({"review_table": review_path})
    rows = _deepseek_table_review_rows(validation.get("rows", []))
    issues = _deepseek_table_issue_rows(validation.get("issues", []))
    render_summary_cards(
        {
            "审批表行数": len(rows),
            "可人工审核": sum(1 for row in rows if row.get("approval_status") in {"pending_review", "approved"}),
            "校验问题": len(issues),
            "本地表路径": _short_path(review_path),
        }
    )
    render_table("结构化审批表（人工审核后才可入库）", rows, empty_message="暂无可审批行")
    render_table("校验问题", issues, empty_message="未发现校验问题")
    _render_zero_row_diagnostics(st, validation)
    _render_review_source_evidence(st, review_path)
    _render_deepseek_table_visualization(st, validation.get("visualization", {}))
    return validation


def _render_review_source_evidence(st, review_path):
    from pathlib import Path

    source_path = Path(str(review_path)).parent / "source_map.json"
    rows = _source_evidence_rows(source_path)
    st.subheader("网页证据摘要")
    st.caption("这里按网页展示系统看到的安全摘要、日期信号、数值信号和阻塞原因；不会展示完整网页正文或 HTML。")
    render_table("每个网页总结出的内容", rows, empty_message="没有找到网页证据摘要")


def _render_source_pack_evidence(st, source_pack_path):
    rows = _source_evidence_rows(source_pack_path)
    st.subheader("已读取网页摘要")
    st.caption("这里展示 Tavily 检索后实际进入抽取流程的网页摘要和信号统计。")
    render_table("每个网页总结出的内容", rows, empty_message="没有找到网页摘要")


def _source_evidence_rows(path, *, limit=12):
    import json
    from pathlib import Path

    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return []
    sources = []
    if isinstance(payload, dict):
        for key in ("sources", "source_pack", "page_reads", "results"):
            if isinstance(payload.get(key), list):
                sources = payload[key]
                break
    elif isinstance(payload, list):
        sources = payload
    rows = []
    for item in sources[:limit]:
        if not isinstance(item, dict):
            continue
        hints = item.get("hints") if isinstance(item.get("hints"), dict) else {}
        blockers = item.get("extraction_blockers") or hints.get("extraction_blockers") or []
        summary = (
            item.get("evidence_summary")
            or item.get("text_summary")
            or item.get("snippet")
            or item.get("title_summary")
            or item.get("title")
            or ""
        )
        rows.append(
            sanitize_dict(
                {
                    "来源ID": item.get("source_id"),
                    "网站": item.get("domain") or item.get("source_domain"),
                    "页面标题": _clip_text(item.get("title_summary") or item.get("title") or item.get("source_title"), 90),
                    "网页摘要": _clip_text(summary, 260),
                    "材料类型": item.get("material_strength") or _infer_material_strength_for_dashboard(item, hints, path),
                    "抽取状态": item.get("extraction_readiness") or "unknown",
                    "日期信号": item.get("dated_signal_count") or hints.get("dated_signal_count") or 0,
                    "数值信号": item.get("numeric_signal_count") or hints.get("numeric_signal_count") or 0,
                    "段落预览": item.get("paragraph_signal_preview_count") or hints.get("paragraph_signal_preview_count") or 0,
                    "表格预览行": item.get("table_preview_rows_count") or hints.get("table_preview_rows_count") or 0,
                    "阻塞原因": "；".join(str(value) for value in blockers[:3]),
                    "建议动作": _clip_text(item.get("recommended_next_action"), 120),
                    "链接摘要": _clip_text(item.get("url_safe_summary") or item.get("source_url") or item.get("url"), 120),
                }
            )
        )
    return rows


def _clip_text(value, max_chars=160):
    text = " ".join(str(value or "").split())
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 1)] + "…"


def _short_path(value):
    text = str(value or "")
    marker = "reports"
    idx = text.find(marker)
    return text[idx:] if idx >= 0 else _clip_text(text, 120)


def _render_editable_review_rows(st, rows, *, key="deepseek_cn_review_editor"):
    if not rows:
        st.caption("暂无可审批行。")
        return []
    column_config = {
        "approval_status": st.column_config.SelectboxColumn(
            "审批状态",
            options=["pending_review", "approved", "edit_required", "rejected"],
            required=True,
        ),
        "reviewer_note": st.column_config.TextColumn("复核备注"),
    }
    disabled = [
        "row_id",
        "date",
        "country",
        "region",
        "confirmed_cases",
        "confirmed_deaths",
        "suspected_cases",
        "contacts_under_monitoring",
        "source_id",
        "confidence",
        "validation_status",
    ]
    return st.data_editor(
        rows,
        column_config=column_config,
        disabled=[key for key in disabled if key in rows[0]],
        use_container_width=True,
        num_rows="fixed",
        key=key,
    )


def _deepseek_search_source_pack_path(summary):
    if not isinstance(summary, dict):
        return None
    paths = summary.get("paths") if isinstance(summary.get("paths"), dict) else {}
    for key in (
        "structured_source_pack",
        "structured_source_pack_json",
        "source_pack",
        "page_read_source_pack",
        "page_read_source_pack_json",
        "page_read_summary",
        "page_read_summary_json",
        "search_summary_json",
        "search_results_summary_json",
        "search_results_summary",
    ):
        if paths.get(key):
            return str(paths[key])
    run_id = summary.get("run_id")
    output_root = "reports/deepseek_web_search"
    if run_id:
        return select_best_source_pack_path(__import__("pathlib").Path(output_root) / str(run_id))
    return None


def select_best_source_pack_path(search_dir, review_dir=None):
    from pathlib import Path

    roots = [Path(str(search_dir or ""))]
    if review_dir:
        roots.append(Path(str(review_dir)))
    names = (
        "structured_source_pack.json",
        "source_pack.json",
        "page_read_source_pack.json",
        "page_read_summary.json",
        "metadata_check_results.json",
        "candidate_acquisition_summary.json",
        "raw_document_ingest_summary.json",
        "search_results_summary.json",
    )
    for root in roots:
        if not root.exists():
            continue
        for name in names:
            path = root / name
            if path.exists():
                return str(path)
    return None


def _latest_or_remembered_review_table(state, output_dir):
    remembered = state.get("last_deepseek_review_table")
    if remembered and _review_table_row_count(remembered) > 0:
        return remembered
    options = _discover_deepseek_review_tables(output_dir)
    if options:
        for option in options:
            if int(option.get("row_count") or 0) > 0:
                return option["path"]
        return options[0]["path"]
    return None


def _source_pack_material_summary(path):
    import json
    from pathlib import Path

    if not path:
        return {"path": None, "source_count": 0, "material_strength_counts": {}, "blocker_counts": {"missing_source_pack": 1}}
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {"path": str(path), "source_count": 0, "material_strength_counts": {}, "blocker_counts": {"unreadable_source_pack": 1}}
    if isinstance(payload, dict) and "material_strength_counts" in payload:
        return {
            "path": str(path),
            "source_count": payload.get("source_count", 0),
            "material_strength_counts": payload.get("material_strength_counts") or {},
            "extraction_readiness_counts": payload.get("extraction_readiness_counts") or {},
            "blocker_counts": payload.get("blocker_counts") or {},
            "dated_numeric_signal_count": payload.get("dated_numeric_signal_count", 0),
            "paragraph_signal_preview_count": payload.get("paragraph_signal_preview_count", 0),
            "table_preview_count": payload.get("table_preview_count", 0),
            "document_candidate_count": payload.get("document_candidate_count", 0),
            "all_search_result_only": False,
        }
    sources = []
    if isinstance(payload, dict):
        for key in ("sources", "page_reads", "results", "candidates", "review_queue"):
            if isinstance(payload.get(key), list):
                sources = payload[key]
                break
    elif isinstance(payload, list):
        sources = payload
    strengths = {}
    readiness = {}
    blockers = {}
    dated_numeric_signal_count = 0
    paragraph_signal_preview_count = 0
    table_preview_count = 0
    document_candidate_count = 0
    for item in sources:
        if not isinstance(item, dict):
            continue
        hints = item.get("hints") if isinstance(item.get("hints"), dict) else {}
        strength = item.get("material_strength") or _infer_material_strength_for_dashboard(item, hints, path)
        ready = item.get("extraction_readiness") or "unknown"
        dated_numeric_signal_count += int(item.get("dated_numeric_signal_count") or hints.get("dated_numeric_signal_count") or 0)
        paragraph_signal_preview_count += int(item.get("paragraph_signal_preview_count") or hints.get("paragraph_signal_preview_count") or 0)
        if item.get("has_table_preview") or hints.get("table_preview_rows_count"):
            table_preview_count += 1
        if item.get("document_candidate") or hints.get("document_download_hint"):
            document_candidate_count += 1
        strengths[str(strength)] = strengths.get(str(strength), 0) + 1
        readiness[str(ready)] = readiness.get(str(ready), 0) + 1
        for blocker in item.get("extraction_blockers") or []:
            blockers[str(blocker)] = blockers.get(str(blocker), 0) + 1
    if str(path).endswith("search_results_summary.json"):
        blockers["search_result_only_material"] = blockers.get("search_result_only_material", 0) + 1
    return {
        "path": str(path),
        "source_count": len(sources),
        "material_strength_counts": strengths,
        "extraction_readiness_counts": readiness,
        "blocker_counts": blockers,
        "dated_numeric_signal_count": dated_numeric_signal_count,
        "paragraph_signal_preview_count": paragraph_signal_preview_count,
        "table_preview_count": table_preview_count,
        "document_candidate_count": document_candidate_count,
        "is_search_result_fallback": str(path).endswith("search_results_summary.json"),
        "all_search_result_only": bool(sources) and set(strengths) <= {"search_result_only"},
    }


def _infer_material_strength_for_dashboard(item, hints, path):
    if hints.get("table_preview_rows") or hints.get("table_headers"):
        return "table_preview"
    if hints.get("has_table_hint") or hints.get("has_csv_hint") or hints.get("has_json_api_hint"):
        return "page_with_structured_hints"
    if str(path).endswith("page_read_summary.json") or item.get("text_summary"):
        return "page_summary"
    return "search_result_only"


def _render_source_pack_material_status(st, path):
    summary = _source_pack_material_summary(path)
    st.caption(f"当前材料包：{summary.get('path') or '未选择'}")
    render_summary_cards(
        {
            "材料来源数": summary.get("source_count", 0),
            "材料强度类型数": len(summary.get("material_strength_counts") or {}),
            "阻断类型数": len(summary.get("blocker_counts") or {}),
            "日期+数值信号": summary.get("dated_numeric_signal_count", 0),
        }
    )
    render_summary_cards(
        {
            "段落信号预览": summary.get("paragraph_signal_preview_count", 0),
            "表格预览源": summary.get("table_preview_count", 0),
            "文档候选": summary.get("document_candidate_count", 0),
        }
    )
    render_table(
        "Source pack material strength",
        [{"type": key, "count": value} for key, value in (summary.get("material_strength_counts") or {}).items()],
        empty_message="暂无材料强度信息",
    )
    render_table(
        "Table extraction blockers",
        [
            {"blocker": key, "count": value, "meaning": _source_pack_blocker_label(key)}
            for key, value in (summary.get("blocker_counts") or {}).items()
        ],
        empty_message="未发现明显材料阻断",
    )
    if summary.get("is_search_result_fallback"):
        st.warning("当前只使用搜索结果摘要，缺少页面读取或结构化表格材料，可能无法抽取出日期和数值。")
    if summary.get("all_search_result_only"):
        st.warning("当前 source pack 全部是 search_result_only，不建议直接调用 DeepSeek 表格抽取。")
    return summary


def _source_pack_blocker_label(blocker):
    labels = {
        "missing_source_pack": "没有找到 source pack",
        "unreadable_source_pack": "source pack 无法读取",
        "search_result_only_material": "只有搜索摘要，没有页面或结构化预览",
        "no_dated_signal": "缺少明确日期或周次",
        "no_numeric_signal": "缺少明确病例/死亡/检测等数值",
        "no_table_csv_json_preview": "没有表格、CSV 或 JSON 预览",
        "insufficient_source_material": "材料不足，不能安全抽取结构化行",
        "no_dated_numeric_signal": "没有同时出现日期/周次和数值指标",
        "numeric_signal_without_date": "有数值但缺少日期或周次",
        "date_signal_without_numeric": "有日期或周次但缺少病例/死亡等数值",
        "api_hint_without_data": "只有 API/JSON 提示，没有可抽取数据行",
        "document_candidate_not_downloaded": "是文档候选，但当前受控流程不下载文档",
        "needs_structured_document_or_dataset": "需要结构化表格、CSV、JSON 行预览或日期+数值段落",
    }
    return labels.get(str(blocker), str(blocker))


def _render_zero_row_diagnostics(st, validation):
    rows = validation.get("rows") if isinstance(validation, dict) else []
    issues = validation.get("issues") if isinstance(validation, dict) else []
    if rows:
        return
    st.warning("审批表行数：0。没有可校验的结构化行。")
    bundle_issues = [issue for issue in (issues or []) if issue.get("row_id") == "__bundle__"]
    if bundle_issues:
        render_table("0 行阻断原因", _deepseek_table_issue_rows(bundle_issues), empty_message="暂无阻断原因")
    st.info(
        "建议下一步：优先使用 page_read_summary 或 structured_source_pack；运行 metadata check；"
        "选择 CSV / HTML table / JSON API 候选源；如果页面只是官方说明页，则保留为 evidence source，不入库。"
    )


def _goal_mode_attempt_rows(attempts):
    rows = []
    for item in attempts or []:
        if not isinstance(item, dict):
            continue
        rows.append(
            sanitize_dict(
                {
                    "目标": item.get("target"),
                    "状态": item.get("status"),
                    "审批候选行": item.get("review_rows_from_live_material", 0),
                    "日期锚点": item.get("date_anchor_count", 0),
                    "数值锚点": item.get("metric_anchor_count", 0),
                    "上下文关联候选": item.get("context_linked_candidate_count", 0),
                    "确定性行": item.get("deterministic_rows_generated", 0),
                    "DeepSeek 行": item.get("deepseek_rows_generated", 0),
                    "阻断原因": "；".join(str(value) for value in (item.get("blockers") or [])[:4]),
                    "审批表": item.get("review_table"),
                }
            )
        )
    return rows


def _remember_deepseek_review_table(state, summary):
    if not isinstance(summary, dict):
        return
    paths = summary.get("paths") if isinstance(summary.get("paths"), dict) else {}
    review_json = summary.get("review_json") or paths.get("review_json")
    if review_json:
        review_path = str(review_json)
        if _review_table_row_count(review_path) > 0:
            state["last_deepseek_review_table"] = review_path
        else:
            state["last_zero_row_deepseek_review_table"] = review_path


def _discover_deepseek_review_tables(output_dir):
    from pathlib import Path

    root = Path(str(output_dir or "reports/deepseek_table_review"))
    if not root.exists():
        return []
    tables = sorted(
        root.glob("*/structured_event_table_review.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    options = [_review_table_option(str(path)) for path in tables[:30]]
    return sorted(options, key=lambda item: (int(item.get("row_count") or 0) <= 0, -float(item.get("modified") or 0)))


def _review_table_option(path):
    from pathlib import Path

    table_path = Path(str(path))
    run_dir = table_path.parent
    label = run_dir.name
    try:
        modified = table_path.stat().st_mtime
    except OSError:
        modified = 0
    row_count = _review_table_row_count(table_path)
    return {"label": f"{label}（{row_count} 行）", "path": str(table_path), "modified": modified, "row_count": row_count}


def _review_table_row_count(path):
    import json
    from pathlib import Path

    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return 0
    rows = payload.get("rows") if isinstance(payload, dict) else payload
    return len(rows or []) if isinstance(rows, list) else 0


def _save_deepseek_review_table_edits(review_table_path, edited_rows):
    import json
    from pathlib import Path

    path = Path(str(review_table_path))
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("rows") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        rows = []
    by_id = {str(row.get("row_id")): row for row in edited_rows or [] if isinstance(row, dict)}
    allowed_statuses = {"pending_review", "approved", "edit_required", "rejected"}
    counts = {"updated_rows": 0, "approved": 0, "pending_review": 0, "edit_required": 0, "rejected": 0}
    for row in rows:
        if not isinstance(row, dict):
            continue
        edited = by_id.get(str(row.get("row_id")))
        if edited:
            status = str(edited.get("approval_status") or row.get("approval_status") or "pending_review")
            if status not in allowed_statuses:
                status = "pending_review"
            note = str(edited.get("reviewer_note") or "")[:500]
            if row.get("approval_status") != status or row.get("reviewer_note") != note:
                counts["updated_rows"] += 1
            row["approval_status"] = status
            row["reviewer_note"] = note
        status = str(row.get("approval_status") or "pending_review")
        if status in counts:
            counts[status] += 1
    if isinstance(payload, dict):
        payload["rows"] = rows
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return counts


def _render_ingestion_preview(st, result):
    safe = sanitize_dict(result or {})
    render_summary_cards(
        {
            "状态": safe.get("status", "unknown"),
            "已批准行": safe.get("approved_rows", safe.get("approved", 0)),
            "写入事件": safe.get("epidemic_events_created", safe.get("events_created", 0)),
            "是否写库": safe.get("persistence_enabled", safe.get("allow_persistence", False)),
        }
    )
    warnings = safe.get("warnings") or []
    if warnings:
        st.warning("；".join(str(item) for item in warnings[:3]))


def _path_summary_rows(result):
    rows = []
    for key, value in sanitize_dict(result or {}).items():
        if key in {"review_json", "review_csv", "review_markdown", "source_map", "validation_issues", "duplicate_report", "visualization_summary", "safety_audit"}:
            rows.append({"文件类型": key, "本地路径": value})
    return rows


def render_deepseek_demo_pipeline_page(client):
    import streamlit as st

    state = _get_streamlit_state(st)
    st.header("DeepSeek 自动检索、抽取、可视化、审核入库演示")
    st.info(
        "这是一条完整流程：先用 Tavily 真实联网检索病种相关网页，再让 DeepSeek 从检索结果中抽取结构化事件表，"
        "随后展示表格和趋势图，最后由人工审核后写入本地数据库。"
    )
    st.warning(
        "所有真实联网和 DeepSeek 调用都需要你勾选授权确认。系统不会展示完整网页正文、HTML、prompt、completion 或密钥。"
    )

    top_cols = st.columns(4)
    with top_cols[0]:
        country = st.text_input("国家/地区", value="United States", key="demo_country")
    with top_cols[1]:
        disease = st.text_input("病种", value="influenza", key="demo_disease")
    with top_cols[2]:
        date_start = st.text_input("开始日期", value="2026-05-01", key="demo_date_start")
    with top_cols[3]:
        date_end = st.text_input("结束日期", value="2026-07-01", key="demo_date_end")

    search_output_dir = st.text_input("检索结果目录", value="reports/deepseek_web_search", key="demo_search_output_dir")
    table_output_dir = st.text_input("审批表与可视化目录", value="reports/deepseek_table_review", key="demo_table_output_dir")
    auth = st.checkbox(
        "我确认允许本次演示调用搜索后端、读取受控页面摘要、调用 DeepSeek，并已人工确认服务/站点条款",
        value=False,
        key="demo_pipeline_auth",
    )
    with st.expander("Goal Mode：自动切换病种直到生成可审批行", expanded=False):
        st.caption("按目标列表逐个尝试。找到真实材料产生的审批候选行后停止；不会自动入库。")
        goal_targets = st.text_input(
            "目标列表",
            value="dengue:Global,measles:United States,cholera:Global,mpox:Global,Ebola:African Region",
            key="demo_goal_targets",
        )
        goal_cols = st.columns(4)
        with goal_cols[0]:
            goal_mock_search = st.checkbox("mock 检索", value=True, key="demo_goal_mock_search")
        with goal_cols[1]:
            goal_mock_deepseek = st.checkbox("mock DeepSeek", value=True, key="demo_goal_mock_deepseek")
        with goal_cols[2]:
            goal_allow_document_preview = st.checkbox("允许受控文档预览", value=True, key="demo_goal_allow_doc_preview")
        with goal_cols[3]:
            goal_stop_when_rows = st.checkbox("找到行后停止", value=True, key="demo_goal_stop_rows")
        if st.button("运行 Goal Mode 自动尝试", key="demo_goal_mode_run"):
            if not auth:
                st.error("请先勾选授权确认。")
            else:
                result = client.deepseek_table_review_goal_mode(
                    {
                        "targets": goal_targets,
                        "date_start": date_start,
                        "date_end": date_end,
                        "output_dir": "reports/deepseek_table_review_goal",
                        "database_url": "sqlite:///reports/deepseek_table_review_goal/deepseek_table_review_goal.sqlite",
                        "mock_search": bool(goal_mock_search),
                        "mock_deepseek": bool(goal_mock_deepseek),
                        "allow_live_source_search": not bool(goal_mock_search),
                        "allow_deepseek": not bool(goal_mock_deepseek),
                        "terms_reviewed": True,
                        "manual_reviewed": True,
                        "allow_document_preview": bool(goal_allow_document_preview),
                        "stop_when_rows_found": bool(goal_stop_when_rows),
                    }
                )
                summary = result.get("summary", result)
                render_summary_cards(
                    {
                        "状态": summary.get("status"),
                        "成功目标": summary.get("successful_target") or "暂无",
                        "真实材料审批行": summary.get("review_rows_from_live_material", 0),
                        "DeepSeek 调用": summary.get("deepseek_call_count_total", 0),
                    }
                )
                render_table(
                    "Goal Mode 目标尝试结果",
                    _goal_mode_attempt_rows(summary.get("attempts", [])),
                    empty_message="暂无尝试结果",
                )
    with st.expander("检索与读取预算设置", expanded=True):
        budget_cols = st.columns(4)
        with budget_cols[0]:
            search_provider = st.selectbox(
                "搜索后端",
                ["tavily", "mock", "openai_web_search", "perplexity", "bing", "brave", "exa", "serpapi"],
                index=0,
                key="demo_search_provider",
            )
            max_search_queries = st.number_input(
                "最多检索查询数",
                min_value=1,
                max_value=6,
                value=4,
                key="demo_max_search_queries",
            )
        with budget_cols[1]:
            max_search_results = st.number_input(
                "每个查询最多结果",
                min_value=1,
                max_value=8,
                value=8,
                key="demo_max_search_results",
            )
            max_pages_to_read = st.number_input(
                "最多读取网页数",
                min_value=0,
                max_value=8,
                value=8,
                key="demo_max_pages_to_read",
            )
        with budget_cols[2]:
            max_readable_pages_success = st.number_input(
                "成功读取页目标",
                min_value=0,
                max_value=8,
                value=8,
                key="demo_max_readable_pages_success",
            )
            max_total_http_requests = st.number_input(
                "总 HTTP 请求上限",
                min_value=0,
                max_value=16,
                value=16,
                key="demo_max_total_http_requests",
            )
        with budget_cols[3]:
            structured_numeric_mode = st.checkbox(
                "结构化数值证据模式",
                value=True,
                key="demo_structured_numeric_mode",
            )
            prefer_official_and_structured = st.checkbox(
                "优先官方/结构化数据源",
                value=True,
                key="demo_prefer_official_structured",
            )
            stop_when_structured = st.checkbox(
                "找到强结构化候选后停止",
                value=True,
                key="demo_stop_when_structured",
            )
            prefer_situation_reports = st.checkbox(
                "优先 situation reports",
                value=True,
                key="demo_prefer_situation_reports",
            )
            prefer_dated_numeric_sources = st.checkbox(
                "优先日期+数值来源",
                value=True,
                key="demo_prefer_dated_numeric_sources",
            )
            include_document_candidates = st.checkbox(
                "保留报告/PDF 候选但不下载",
                value=True,
                key="demo_include_document_candidates",
            )
        st.caption("默认扩大到 4 个查询、每查 8 条、最多读 8 页、总 HTTP 16；不跟随链接，不下载文件。")

    st.subheader("四步演示按钮")
    step_cols = st.columns(4)
    with step_cols[0]:
        st.markdown("**第一步：检索**")
        st.caption("联网搜索候选网页，并读取安全页面摘要；不会跟随候选页内链接。")
        if st.button("第一步：Tavily 检索网页", key="demo_step_1_search"):
            if not auth:
                st.error("请先勾选授权确认。")
            else:
                result = client.deepseek_web_search_run(
                    {
                        "country": country,
                        "disease": disease,
                        "query_intent": "structured_numeric_evidence" if structured_numeric_mode else "structured_time_series",
                        "search_backend": search_provider,
                        "max_search_queries": int(max_search_queries),
                        "max_search_results_per_query": int(max_search_results),
                        "max_pages_to_read": int(max_pages_to_read),
                        "max_total_http_requests": int(max_total_http_requests),
                        "max_readable_pages_success": int(max_readable_pages_success),
                        "stop_when_structured_candidates_found": bool(stop_when_structured),
                        "prefer_official_and_structured_sources": bool(prefer_official_and_structured),
                        "prefer_situation_reports": bool(prefer_situation_reports),
                        "prefer_dated_numeric_sources": bool(prefer_dated_numeric_sources),
                        "include_document_candidates": bool(include_document_candidates),
                        "do_not_download_documents": True,
                        "max_paragraph_signal_preview": 10,
                        "output_dir": search_output_dir,
                        "allow_deepseek_api": True,
                        "allow_general_web_search": True,
                        "allow_page_read": True,
                        "terms_reviewed": True,
                        "manual_reviewed": True,
                    }
                )
                summary = result.get("summary", result)
                state["demo_last_search_summary"] = summary
                state["demo_last_source_pack"] = _deepseek_search_source_pack_path(summary)
                st.success("Tavily 检索和 DeepSeek 分析已完成。")
                _render_deepseek_run_result(st, summary)
                if state.get("demo_last_source_pack"):
                    _render_source_pack_evidence(st, state.get("demo_last_source_pack"))

    with step_cols[1]:
        st.markdown("**第二步：抽取**")
        st.caption("DeepSeek 基于 Tavily 结果生成待审批事件表。")
        if st.button("第二步：生成结构化审批表", key="demo_step_2_extract"):
            if not auth:
                st.error("请先勾选授权确认。")
            else:
                source_pack = state.get("demo_last_source_pack") or _latest_deepseek_search_results_path(
                    search_output_dir,
                    disease=disease,
                    country=country,
                )
                if not source_pack:
                    st.warning("没有找到可用的 Tavily 检索结果。请先点击第一步。")
                else:
                    material_summary = _render_source_pack_material_status(st, source_pack)
                    if material_summary.get("all_search_result_only"):
                        st.warning("材料不足时继续抽取可能得到 0 行；建议先重新生成 page_read_summary 或选择结构化候选源。")
                    result = client.deepseek_table_review_run_deepseek(
                        {
                            "disease": disease,
                            "country_or_region": country,
                            "date_start": date_start,
                            "date_end": date_end,
                            "max_sources": 8,
                            "source_pack": source_pack,
                            "output_dir": table_output_dir,
                            "allow_deepseek": True,
                            "allow_live_source_search": False,
                            "allow_network": False,
                            "terms_reviewed": True,
                            "manual_reviewed": True,
                        }
                    )
                    summary = result.get("summary", result)
                    _remember_deepseek_review_table(state, summary)
                    st.success("结构化审批表已生成。")
                    _render_table_review_result(st, summary)
                    review_path = _latest_or_remembered_review_table(state, table_output_dir)
                    if review_path:
                        _render_table_review_workspace(st, client, review_path)

    with step_cols[2]:
        st.markdown("**第三步：展示**")
        st.caption("显示审批表、校验问题和趋势图。")
        if st.button("第三步：显示表格和图表", key="demo_step_3_visualize"):
            review_path = _latest_or_remembered_review_table(state, table_output_dir)
            if not review_path:
                st.warning("还没有审批表。请先点击第二步。")
            else:
                state["last_deepseek_review_table"] = review_path
                _render_table_review_workspace(st, client, review_path)

    with step_cols[3]:
        st.markdown("**第四步：入库**")
        st.caption("人工审核 approved 后写入本地 pilot 数据库。")
        if st.button("第四步：打开审核入库区", key="demo_step_4_review"):
            review_path = _latest_or_remembered_review_table(state, table_output_dir)
            if not review_path:
                st.warning("还没有可审核表格。请先点击第二步。")
            else:
                state["last_deepseek_review_table"] = review_path
                state["demo_show_review_editor"] = True

    latest_review_path = _latest_or_remembered_review_table(state, table_output_dir)
    if latest_review_path:
        st.subheader("最新结构化审批表（自动展示）")
        st.caption("这里直接展示最近生成的审批表和每个网页的证据摘要；人工审核通过后才允许入库。")
        state["last_deepseek_review_table"] = latest_review_path
        _render_table_review_workspace(st, client, latest_review_path)
    else:
        st.info("当前还没有结构化审批表。请先执行第一步检索和第二步抽取。")

    if state.get("demo_show_review_editor"):
        review_path = _latest_or_remembered_review_table(state, table_output_dir)
        if review_path:
            st.subheader("人工审核与入库")
            validation = client.deepseek_table_review_clean_validate({"review_table": review_path})
            _render_zero_row_diagnostics(st, validation)
            edited_rows = _render_editable_review_rows(
                st,
                _deepseek_table_review_rows(validation.get("rows", [])),
                key="demo_review_editor",
            )
            action_cols = st.columns(3)
            with action_cols[0]:
                if st.button("保存审批修改", key="demo_save_review_edits"):
                    saved = _save_deepseek_review_table_edits(review_path, edited_rows)
                    st.success(f"已保存审批修改：更新 {saved['updated_rows']} 行。")
                    render_summary_cards(
                        {
                            "已批准": saved["approved"],
                            "待复核": saved["pending_review"],
                            "需修改": saved["edit_required"],
                            "已拒绝": saved["rejected"],
                        }
                    )
            with action_cols[1]:
                if st.button("预览入库影响", key="demo_preview_ingestion"):
                    result = client.deepseek_table_review_preview_ingestion({"review_table": review_path})
                    _render_ingestion_preview(st, result)
            with action_cols[2]:
                allow_persistence = st.checkbox("允许写入本地 pilot DB", value=False, key="demo_allow_persistence")
                if st.button("写入已批准行", key="demo_ingest_approved"):
                    result = client.deepseek_table_review_ingest_approved(
                        {
                            "review_table": review_path,
                            "database_url": "sqlite:///reports/deepseek_table_review/deepseek_table_review.sqlite",
                            "allow_persistence": bool(allow_persistence),
                        }
                    )
                    _render_ingestion_preview(st, result.get("summary", result))


def _latest_deepseek_search_results_path(output_dir, *, disease, country):
    from pathlib import Path

    root = Path(str(output_dir or "reports/deepseek_web_search"))
    if not root.exists():
        return None
    prefix = "run_deepseek_"
    disease_token = "_".join(str(disease or "").lower().split())
    country_token = "_".join(str(country or "").lower().split())
    candidates = []
    for path in root.glob("*/search_results_summary.json"):
        name = path.parent.name.lower()
        if name.startswith(prefix) and disease_token in name and country_token in name:
            candidates.append(path)
    if not candidates:
        candidates = list(root.glob("*/search_results_summary.json"))
    if not candidates:
        return None
    latest = max(candidates, key=lambda item: item.stat().st_mtime)
    return select_best_source_pack_path(latest.parent) or str(latest)


def render_who_data_acquisition_page(client):
    import streamlit as st

    st.header("WHO 优先可下载/表格数据接入测试")
    st.info(
        "本页用于演示 WHO / WHO AFRO 优先的数据源发现、下载元数据检查、结构化审批表生成和 approved 行入库。"
        "默认 dry-run 不联网；受控 live 需要显式授权。"
    )
    st.warning("所有候选源和抽取行默认 pending_review；不会自动启用数据源、不会生产预测、不会展示完整网页正文。")

    country_or_region = st.text_input("国家/地区", value="Democratic Republic of the Congo", key="who_country")
    disease = st.text_input("病种", value="Ebola", key="who_disease")
    date_start = st.text_input("开始日期", value="2025-01-01", key="who_date_start")
    date_end = st.text_input("结束日期", value="2025-12-31", key="who_date_end")
    output_dir = st.text_input("本地报告目录", value="reports/who_data_acquisition", key="who_output_dir")

    payload = {
        "country_or_region": country_or_region,
        "disease": disease,
        "date_start": date_start,
        "date_end": date_end,
        "provider": "tavily",
        "output_dir": output_dir,
        "max_provider_requests": 3,
        "max_metadata_checks": 3,
    }

    cols = st.columns(3)
    with cols[0]:
        if st.button("1. 生成 WHO dry-run 审批表", key="who_dry_run"):
            result = client.who_data_acquisition_dry_run(payload)
            summary = result.get("summary", result)
            _render_who_acquisition_summary(st, summary)
            review_path = summary.get("review_json")
            if review_path:
                _render_table_review_workspace(st, client, review_path)

    with cols[1]:
        with st.expander("2. 受控 live WHO 检索", expanded=False):
            st.caption("只允许 WHO/WHO AFRO 优先候选；不访问候选外链，不保存全文。")
            allow_live = st.checkbox("允许本次 WHO live", value=False, key="who_live_allow")
            allow_network = st.checkbox("允许联网", value=False, key="who_live_network")
            terms_reviewed = st.checkbox("已审查条款", value=False, key="who_live_terms")
            manual_reviewed = st.checkbox("人工批准", value=False, key="who_live_manual")
            if st.button("运行受控 live", key="who_live_run"):
                result = client.who_data_acquisition_run(
                    {
                        **payload,
                        "allow_live_who": bool(allow_live),
                        "allow_network": bool(allow_network),
                        "terms_reviewed": bool(terms_reviewed),
                        "manual_reviewed": bool(manual_reviewed),
                    }
                )
                summary = result.get("summary", result)
                _render_who_acquisition_summary(st, summary)
                review_path = summary.get("review_json")
                if review_path:
                    _render_table_review_workspace(st, client, review_path)

    with cols[2]:
        with st.expander("3. approved 行入库", expanded=False):
            review_table = st.text_input("审批表路径", value="", key="who_review_table")
            database_url = st.text_input(
                "pilot SQLite URL",
                value="sqlite:///reports/who_data_acquisition/who_ebola_pilot.sqlite",
                key="who_database_url",
            )
            allow_persistence = st.checkbox("允许写入本地 pilot DB", value=False, key="who_allow_persistence")
            if st.button("预览/写入 approved 行", key="who_ingest"):
                result = client.who_data_acquisition_ingest_approved(
                    {
                        "review_table": review_table,
                        "database_url": database_url,
                        "allow_persistence": bool(allow_persistence),
                    }
                )
                _render_ingestion_preview(st, result.get("summary", result))

    st.subheader("最近一次结果说明")
    st.caption("dry-run 会生成可入库前审批表；只有人工把行改为 approved 且显式允许写入时，才会进入本地 pilot DB。")


def _render_who_acquisition_summary(st, summary):
    safe = sanitize_dict(summary or {})
    render_summary_cards(
        {
            "状态": safe.get("status", "unknown"),
            "候选源": safe.get("candidates_found", 0),
            "审批行": safe.get("review_rows_generated", 0),
            "pending_review": safe.get("pending_review_rows", 0),
        }
    )
    render_summary_cards(
        {
            "联网": safe.get("network_accessed", False),
            "provider 请求": safe.get("provider_request_count", 0),
            "HEAD 元数据检查": safe.get("metadata_check_request_count", 0),
            "已入库 approved 行": safe.get("approved_rows_ingested", 0),
        }
    )
    rows = [
        {"字段": key, "值": value}
        for key, value in {
            "run_id": safe.get("run_id"),
            "报告目录": safe.get("run_dir"),
            "审批 JSON": safe.get("review_json"),
            "审批 CSV": safe.get("review_csv"),
            "字段映射报告": safe.get("field_mapping_report"),
            "元数据检查报告": safe.get("metadata_checks"),
        }.items()
        if value
    ]
    render_table("WHO 接入测试输出", rows, empty_message="暂无结果")
    warnings = safe.get("warnings") or []
    if warnings:
        st.warning("；".join(str(item) for item in warnings[:4]))
