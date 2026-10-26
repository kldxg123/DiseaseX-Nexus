from __future__ import annotations

from pathlib import Path

from common.db import get_session
from common.models import DiseaseCountryInformationRecord, RawDocument
from deepseek_web_search_agent.cli import main


def test_deepseek_mock_cli_end_to_end_persists_cleans_and_exports_3d(tmp_path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'deepseek-e2e.sqlite').as_posix()}"
    output_dir = tmp_path / "reports"
    output_json = tmp_path / "measles_us_3d_data.json"

    assert main(["plan", "--disease", "measles", "--country", "United States", "--output-dir", str(output_dir)]) == 0
    assert (
        main(
            [
                "run-mock",
                "--disease",
                "measles",
                "--country",
                "United States",
                "--intent",
                "structured_time_series",
                "--max-search-queries",
                "2",
                "--max-results-per-query",
                "3",
                "--max-pages-to-read",
                "0",
                "--max-total-http-requests",
                "4",
                "--output-dir",
                str(output_dir),
                "--database-url",
                database_url,
            ]
        )
        == 0
    )
    assert main(["send-to-cleaning", "--run-id", "latest", "--database-url", database_url]) == 0
    assert (
        main(
            [
                "export-3d-data",
                "--disease",
                "measles",
                "--country",
                "United States",
                "--database-url",
                database_url,
                "--output",
                str(output_json),
            ]
        )
        == 0
    )

    with get_session(database_url) as session:
        assert session.query(DiseaseCountryInformationRecord).count() > 0
        assert session.query(RawDocument).count() >= 0
    assert (output_dir / "query_plan.json").exists()
    assert Path(output_json).exists()
