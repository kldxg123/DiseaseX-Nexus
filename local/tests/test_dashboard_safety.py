from __future__ import annotations

from apps.dashboard.safety import safe_json_preview, sanitize_dict, truncate_text


def test_sanitize_dict_masks_sensitive_nested_values() -> None:
    payload = {
        "token": "do-not-show",
        "nested": {"password": 'REDACTED', "normal": "ok"},
        "items": [{"api_key": 'REDACTED'}],
        "feature_keys": ["current_week_cases"],
        "metadata_keys": ["source_score"],
        "feature_json": {"current_week_cases": 1},
        "lesson_json": {"notes": "do-not-show"},
        "metadata_json": {"token": "do-not-show"},
        "provenance_json": {"secret": 'REDACTED'},
        "raw_text": "do-not-show",
        "html": "<html>do-not-show</html>",
    }

    safe = sanitize_dict(payload)
    rendered = str(safe)

    assert safe["token"] == "***"
    assert safe["nested"]["password"] == "***"
    assert safe["nested"]["normal"] == "ok"
    assert safe["feature_keys"] == ["current_week_cases"]
    assert safe["metadata_keys"] == ["source_score"]
    assert "feature_json" not in safe
    assert "lesson_json" not in safe
    assert "metadata_json" not in safe
    assert "provenance_json" not in safe
    assert "raw_text" not in safe
    assert "html" not in safe
    assert "do-not-show" not in rendered


def test_truncate_text_and_safe_json_preview_limit_long_content() -> None:
    long_text = "x" * 200

    assert truncate_text(long_text, max_chars=20) == "x" * 20 + "...[truncated]"
    preview = safe_json_preview({"body": long_text}, max_chars=60)

    assert len(preview) <= 74
    assert "truncated" in preview
