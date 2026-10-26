from __future__ import annotations

from apps.dashboard import streamlit_app


def test_dashboard_streamlit_app_importable() -> None:
    assert callable(streamlit_app.render)
    assert streamlit_app.SYSTEM_STARTED_TEXT == "系统已启动"
    assert streamlit_app.DASHBOARD_TITLE == "传染病情报平台可视化仪表盘"
    first_page, renderer = next(iter(streamlit_app.PAGE_RENDERERS.items()))
    assert first_page == "【演示】DeepSeek 自动检索→抽取→可视化→审核入库"
    assert renderer.__name__ == "render_deepseek_demo_pipeline_page"
