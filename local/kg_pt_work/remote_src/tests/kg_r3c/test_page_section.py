# ruff: noqa: E501
"""Static checks for the unified R3C KG dashboard page."""
from __future__ import annotations

from epidemic_core.forecast_model_kg.dashboard_app import KG_HTML


def test_kg_html_contains_r3c_section() -> None:
    assert '<section id="kg-r3c">' in KG_HTML
    for label in (
        "导入核算",
        "知识图谱版本与完整性",
        "复核队列",
        "知识图谱数据维护状态",
        "当前没有调用模型训练或预测",
    ):
        assert label in KG_HTML, label


def test_kg_r3c_section_is_utf8_without_mojibake() -> None:
    section = KG_HTML.split('<section id="kg-r3c">', 1)[1]
    assert "\ufffd" not in section
    assert "??" not in section
    KG_HTML.encode("utf-8")


def test_kg_r3c_section_below_existing_content_and_ids_preserved() -> None:
    assert KG_HTML.index('<section id="kg-r3c">') > KG_HTML.index('id="kgDeferred"')
    for marker in (
        'id="kgCountry"',
        'id="kgDisease"',
        'id="kgModels"',
        'id="kgGraph"',
        'id="kgStatus"',
        'id="kgDeferred"',
        '<script src="/static/forecast-model-kg.js" defer></script>',
    ):
        assert marker in KG_HTML, marker


def test_kg_page_has_one_r3c_recommendation_surface() -> None:
    assert "模型候选" in KG_HTML
    assert "旧版数据只保留作审计" in KG_HTML
    assert "六个维度的通俗评分说明" in KG_HTML
    assert "论文证据与原文定位" in KG_HTML
    assert 'id="kgR3cRecs"' not in KG_HTML
