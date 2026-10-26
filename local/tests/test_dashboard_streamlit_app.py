from __future__ import annotations

import importlib

import httpx


def test_streamlit_app_import_does_not_start_http_client(monkeypatch) -> None:
    def fail_client(*args, **kwargs):
        raise AssertionError("HTTP client should not be created on import")

    monkeypatch.setattr(httpx, "Client", fail_client)

    module = importlib.reload(importlib.import_module("apps.dashboard.streamlit_app"))

    assert callable(module.render)
    assert "访问安全闸门 Access Guardrails" in module.PAGE_RENDERERS
    assert "总览 Overview" in module.PAGE_RENDERERS
    assert "爬虫优化 Crawler Optimizer" in module.PAGE_RENDERERS
    assert "数据源发现 Source Discovery" in module.PAGE_RENDERERS
    assert "最小数据集 Minimum Data" in module.PAGE_RENDERERS
    assert "数据质量 Data Quality" in module.PAGE_RENDERERS
