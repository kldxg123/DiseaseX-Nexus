from __future__ import annotations

import os

from apps.dashboard.api_client import DEFAULT_API_BASE_URL, DashboardApiClient, DashboardApiError
from apps.dashboard.pages import (
    render_access_guardrails_page,
    render_crawler_optimizer_page,
    render_data_quality_page,
    render_deepseek_web_search_page,
    render_events_page,
    render_features_page,
    render_feedback_page,
    render_ingestion_page,
    render_minimum_data_page,
    render_models_page,
    render_overview_page,
    render_source_discovery_page,
    render_sources_page,
    render_who_data_acquisition_page,
)

SYSTEM_STARTED_TEXT = "系统已启动"
DASHBOARD_TITLE = "传染病情报平台可视化仪表盘"


def render_deepseek_demo_pipeline_page(client):
    """Render the demo page without requiring pages.py to export this name."""
    from apps.dashboard import pages as dashboard_pages
    import streamlit as st

    renderer = getattr(dashboard_pages, "render_deepseek_demo_pipeline_page", None)
    try:
        if callable(renderer):
            return renderer(client)
        return render_deepseek_web_search_page(client)
    except DashboardApiError as exc:
        _render_dashboard_api_error(st, exc, title="演示流程调用失败")
        return None


def _render_dashboard_api_error(st, exc: DashboardApiError, *, title: str = "接口调用失败") -> None:
    message = str(exc)
    st.error(f"{title}。页面已拦截错误，没有自动重试，也没有访问额外网页。")
    if "ReadTimeout" in message:
        st.warning("本地 API 响应超时。可以稍后重试，或先把最多读取页面数调低。")
    elif "status=500" in message:
        st.warning("后端处理失败。常见原因是本地密钥配置、外部服务返回异常、或本次结果不足以结构化抽取。")
    elif "status=401" in message or "status=403" in message:
        st.warning("外部服务授权失败或访问被拒绝。请检查本地密钥配置和授权勾选。")
    elif "status=429" in message:
        st.warning("外部服务限流。本系统不会自动重试，请稍后再试。")
    else:
        st.warning("本地 API 调用没有成功。请确认 FastAPI 正在运行，并检查页面参数。")
    st.caption("为了演示安全，页面不展示完整响应体、网页正文、prompt、completion、密钥或服务器信息。")


PAGE_RENDERERS = {
    "WHO first data acquisition": render_who_data_acquisition_page,
    "【演示】DeepSeek 自动检索→抽取→可视化→审核入库": render_deepseek_demo_pipeline_page,
    "总览 Overview": render_overview_page,
    "数据源 Sources": render_sources_page,
    "数据源发现 Source Discovery": render_source_discovery_page,
    "访问安全闸门 Access Guardrails": render_access_guardrails_page,
    "数据质量 Data Quality": render_data_quality_page,
    "采集入库 Ingestion": render_ingestion_page,
    "爬虫优化 Crawler Optimizer": render_crawler_optimizer_page,
    "疫情事件 Events": render_events_page,
    "模型特征 Features": render_features_page,
    "预测模型 Models": render_models_page,
    "人工反馈 Feedback": render_feedback_page,
    "最小数据集 Minimum Data": render_minimum_data_page,
}


def render() -> str:
    import streamlit as st

    st.set_page_config(page_title=DASHBOARD_TITLE, layout="wide")
    st.title(DASHBOARD_TITLE)
    st.caption("本地演示界面：展示数据源发现、受控接入、清洗、特征、预测、反馈与质量诊断流程。")
    api_base_url = st.sidebar.text_input(
        "API 地址（本地 FastAPI）",
        value=os.getenv("API_BASE_URL", DEFAULT_API_BASE_URL),
        key="api_base_url_8001",
    )
    page = st.sidebar.radio("页面导航", list(PAGE_RENDERERS), index=0)
    st.sidebar.caption("本地 MVP 仪表盘。默认连接 127.0.0.1:8001，不会自动远程部署。")
    client = DashboardApiClient(api_base_url)
    try:
        PAGE_RENDERERS[page](client)
    except DashboardApiError as exc:
        _render_dashboard_api_error(st, exc)
    return SYSTEM_STARTED_TEXT


if __name__ == "__main__":
    render()
