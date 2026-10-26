# ruff: noqa: I001
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_readiness_uses_dynamic_migration_head() -> None:
    text = (ROOT / "src/epidemic_core/p1_10/diagnostics.py").read_text(encoding="utf-8")
    assert "def _migration_script_heads" in text
    assert "marker.get(\"alembic_revision\") in COMPATIBLE_REVISIONS" not in text


def test_three_disease_dashboard_has_no_prediction_demo_panel() -> None:
    text = (ROOT / "src/epidemic_core/dashboard/app.py").read_text(encoding="utf-8")
    assert "TRIPTYCH_NO_PREDICTION_SCOPE_LOCK = True" in text
    assert "prediction-demo-panel" in text
    assert "re.sub" in text


def test_three_disease_main_js_avoids_forecast_endpoints() -> None:
    js = (ROOT / "src/epidemic_core/dashboard/static/dashboard.p1-7ar-v1.js").read_text(encoding="utf-8")
    assert "/api/v2/catalog/options" in js
    assert "/api/v2/history/metadata" in js
    assert "/api/v2/history/series" in js
    assert "forecast" not in js.lower()
    assert "prediction" not in js.lower()


def test_p1_12_three_disease_renderer_contracts() -> None:
    text = (ROOT / "src/epidemic_core/p1_12/dashboard_app.py").read_text(encoding="utf-8")
    for needle in [
        "THREE_DISEASE_TRIPTYCH_RENDERER_CONTRACT",
        "NATIONAL_WEEKLY",
        "COUNTRY_MONTHLY",
        "EVENT_ONLY",
        "THL_CC_BY_4_0_SCOPE_VERIFIED",
        "ECDC_DATABASE_REUSE_NOTICE_VERIFIED",
        "WHO_BOUNDED_NONCOMMERCIAL_WEB_EXTRACT_CONTRACT_VERIFIED",
        "prediction_scope=DEFERRED_BY_USER_OUT_OF_SCOPE",
    ]:
        assert needle in text
