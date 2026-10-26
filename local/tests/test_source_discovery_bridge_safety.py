from __future__ import annotations

import json

from scripts.source_discovery_bridge_smoke import run_source_discovery_bridge_smoke


def test_bridge_outputs_do_not_expose_sensitive_or_verbose_fields(tmp_path) -> None:
    summary = run_source_discovery_bridge_smoke(temp_db=True, output_dir=str(tmp_path / "reports"))
    rendered = json.dumps(summary, ensure_ascii=False).lower()

    assert "服务器信息.txt" not in rendered
    for forbidden in (
        "password",
        "token",
        "secret",
        "ssh_key_path",
        "raw_text",
        "metadata_json",
        "provenance_json",
        "feature_json",
        "lesson_json",
        "<html",
    ):
        assert forbidden not in rendered
