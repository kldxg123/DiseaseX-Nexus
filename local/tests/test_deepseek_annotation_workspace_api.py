from __future__ import annotations

import json

from tests.deepseek_human_review_fixture import create_human_review_fixture, make_annotation_file


def test_annotation_workspace_api_rows_validate_preview_and_bundle_are_safe(api_client, tmp_path) -> None:
    report_dir, _database_url, output_dir = create_human_review_fixture(tmp_path)
    annotation_file = make_annotation_file(
        output_dir / "deepseek_human_review_template.json",
        output_dir / "approved.json",
    )

    rows = api_client.get("/deepseek-web-search/annotation-workspace/rows", params={"report_dir": str(report_dir)})
    export = api_client.post(
        "/deepseek-web-search/annotation-workspace/export-template",
        json={"report_dir": str(report_dir), "output_dir": str(output_dir / "workspace")},
    )
    validate = api_client.post(
        "/deepseek-web-search/annotation-workspace/validate",
        json={"annotation_file": str(annotation_file)},
    )
    preview = api_client.post(
        "/deepseek-web-search/annotation-workspace/preview-promotion",
        json={"annotation_file": str(annotation_file)},
    )
    bundle = api_client.post(
        "/deepseek-web-search/annotation-workspace/export-bundle",
        json={"report_dir": str(report_dir), "output_dir": str(output_dir / "bundle")},
    )

    assert rows.status_code == 200
    assert len(rows.json()["rows"]) == 3
    assert export.status_code == 200
    assert validate.json()["approved_event_count"] == 1
    assert preview.json()["approved_event_count"] == 1
    assert bundle.status_code == 200
    rendered = json.dumps(
        {"rows": rows.json(), "export": export.json(), "validate": validate.json(), "preview": preview.json(), "bundle": bundle.json()},
        ensure_ascii=False,
    ).lower()
    assert "api_key" not in rendered
    assert "prompt" not in rendered
    assert "completion" not in rendered
    assert "raw_text" not in rendered

