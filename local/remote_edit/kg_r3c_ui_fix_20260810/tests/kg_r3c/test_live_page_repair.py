from __future__ import annotations

import uuid
from pathlib import Path

from epidemic_core.forecast_model_kg.dashboard_app import KG_HTML, _jsonable

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "src" / "epidemic_core" / "dashboard" / "static" / "forecast-model-kg.js"
STYLE = ROOT / "src" / "epidemic_core" / "dashboard" / "static" / "forecast-model-kg.css"


def test_legacy_jsonable_serializes_nested_uuid_values() -> None:
    value = uuid.uuid4()
    assert _jsonable({"candidate": {"candidate_id": value}, "ids": [value]}) == {
        "candidate": {"candidate_id": str(value)},
        "ids": [str(value)],
    }


def test_primary_kg_surface_uses_r3c_v2_only() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    assert "/api/v1/kg/r3c/catalog" in script
    assert "/api/v1/kg/r3c/recommendations?" in script
    assert "/api/v1/kg/data-profiles/" in script
    assert "/api/v1/forecast-model-kg/r3a/preexecution/candidates?" not in script
    assert "detailError" in script
    assert "Dynamic hurdle count state-space" in script


def test_kg_page_and_script_are_utf8_without_placeholder_mojibake() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    for content in (KG_HTML, script):
        assert "\ufffd" not in content
        assert "???" not in content
        content.encode("utf-8")


def test_all_detail_panels_have_explicit_render_paths() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    for panel_id in (
        "kgDirectAnswer",
        "kgWaterfall",
        "kgMatrix",
        "kgEvidence",
        "kgParams",
        "kgTableFallback",
        "kgExcluded",
        "kgDeferred",
    ):
        assert panel_id in script


def test_chinese_first_readable_renderers_and_compact_layout() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    style = STYLE.read_text(encoding="utf-8")
    for label in (
        "A 数据结构适配",
        "B 疾病与指标机制",
        "C 国家与监测场景",
        "D 科学文献证据",
        "E 参数可信度",
        "F 执行与验证准备度",
        "为什么推荐这个模型",
        "展开英文原文和精确审计定位",
    ):
        assert label in script or label in KG_HTML
    assert "kg-evidence-grid" in style
    assert "kg-score-grid" in style
    assert "kg-graph-title" in style
    assert "table-layout:auto" in style
