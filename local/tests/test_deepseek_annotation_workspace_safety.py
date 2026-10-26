from __future__ import annotations

import re

from deepseek_web_search_agent.annotation_workspace import export_annotation_workspace_bundle
from scripts.run_deepseek_synthetic_promotion_smoke import run_smoke
import argparse
from tests.deepseek_human_review_fixture import create_human_review_fixture


SECRET_LIKE = re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9_-]{18,}\b")


def test_annotation_workspace_and_synthetic_reports_are_safe(tmp_path) -> None:
    report_dir, _database_url, output_dir = create_human_review_fixture(tmp_path)
    workspace_dir = output_dir / "workspace"
    export_annotation_workspace_bundle(report_dir, workspace_dir)
    smoke_dir = tmp_path / "synthetic"
    run_smoke(
        argparse.Namespace(
            report_dir=str(report_dir),
            annotation_file="tests/fixtures/deepseek_human_review/synthetic_reviewed_annotations.json",
            output_dir=str(smoke_dir),
            database_url=f"sqlite:///{(smoke_dir / 'synthetic.sqlite').as_posix()}",
            allow_persistence=True,
        )
    )

    rendered = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for root in (workspace_dir, smoke_dir)
        for path in root.glob("*")
        if path.suffix.lower() in {".json", ".md"}
    )
    lowered = rendered.lower()
    assert not SECRET_LIKE.search(rendered)
    assert "synthetic_review_fixture" in lowered
    for forbidden in [
        "api_key",
        "token",
        "password",
        "secret",
        "prompt",
        "completion",
        "response body",
        "raw_text",
        "<html",
        "metadata_json",
        "feature_json",
        "provenance_json",
        "lesson_json",
    ]:
        assert forbidden not in lowered
