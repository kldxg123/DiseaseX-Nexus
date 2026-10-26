from __future__ import annotations

from pathlib import Path


def test_dashboard_pages_do_not_use_default_json_or_code_blocks() -> None:
    pages_source = Path("apps/dashboard/pages.py").read_text(encoding="utf-8")
    components_source = Path("apps/dashboard/components.py").read_text(encoding="utf-8")

    assert "st.json(" not in pages_source
    assert "st.code(" not in pages_source
    assert "st.json(" not in components_source
    assert "st.code(" not in components_source


def test_advanced_debug_details_is_the_only_sanitized_json_code_path() -> None:
    formatter_source = Path("apps/dashboard/formatters.py").read_text(encoding="utf-8")

    assert "Advanced debug details (sanitized)" in formatter_source
    assert formatter_source.count("st.code(") == 1
    assert "render_sanitized_debug_json" in formatter_source
