from __future__ import annotations

import json

from tests.deepseek_human_review_fixture import create_human_review_fixture, make_annotation_file


def test_human_review_api_template_import_and_report_are_safe(api_client, tmp_path) -> None:
    report_dir, database_url, output_dir = create_human_review_fixture(tmp_path)
    annotation_file = make_annotation_file(
        output_dir / "deepseek_human_review_template.json",
        output_dir / "approved.json",
    )
    template = api_client.get("/deepseek-web-search/human-review/template", params={"report_dir": str(report_dir)})
    export = api_client.post(
        "/deepseek-web-search/human-review/export-template",
        json={"report_dir": str(report_dir), "output_dir": str(output_dir)},
    )
    imported = api_client.post(
        "/deepseek-web-search/human-review/import-annotations",
        json={
            "report_dir": str(report_dir),
            "annotation_file": str(annotation_file),
            "output_dir": str(output_dir),
            "database_url": database_url,
            "allow_persistence": False,
        },
    )
    report = api_client.get("/deepseek-web-search/human-review/promotion-report", params={"output_dir": str(output_dir)})

    assert template.status_code == 200
    assert export.status_code == 200
    assert imported.status_code == 200
    assert report.status_code == 200
    assert imported.json()["summary"]["result"]["promoted_epidemic_events"] == 0
    rendered = json.dumps(
        {"template": template.json(), "export": export.json(), "imported": imported.json(), "report": report.json()},
        ensure_ascii=False,
    ).lower()
    assert "api_key" not in rendered
    assert "prompt" not in rendered
    assert "completion" not in rendered
    assert "raw_text" not in rendered

