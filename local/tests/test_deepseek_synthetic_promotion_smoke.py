from __future__ import annotations

from common.db import get_session
from common.models import EpidemicEvent
from scripts.run_deepseek_synthetic_promotion_smoke import main, run_smoke
import argparse


def test_synthetic_promotion_smoke_promotes_one_event_and_blocks_features(tmp_path) -> None:
    output_dir = tmp_path / "synthetic_promotion_smoke"
    database_url = f"sqlite:///{(output_dir / 'synthetic_promotion.sqlite').as_posix()}"
    args = argparse.Namespace(
        report_dir="reports/deepseek_web_search/deepseek_api_diagnostics_20260708T022808Z",
        annotation_file="tests/fixtures/deepseek_human_review/synthetic_reviewed_annotations.json",
        output_dir=str(output_dir),
        database_url=database_url,
        allow_persistence=True,
    )

    result = run_smoke(args)

    assert result["synthetic_review_fixture"] is True
    assert result["not_real_epidemiological_data"] is True
    assert result["promoted_epidemic_events"] >= 1
    assert result["blocked_count"] >= 1
    assert result["feature_generation_allowed"] is False
    assert "insufficient_temporal_signal" in result["feature_eligibility_blockers"]
    with get_session(database_url) as session:
        event = session.query(EpidemicEvent).one()
        assert event.disease == "synthetic_influenza_gate_test"
        assert event.cases == 1


def test_synthetic_promotion_smoke_cli_outputs_safe_summary(tmp_path, capsys) -> None:
    output_dir = tmp_path / "synthetic_cli"
    code = main(
        [
            "--report-dir",
            "reports/deepseek_web_search/deepseek_api_diagnostics_20260708T022808Z",
            "--annotation-file",
            "tests/fixtures/deepseek_human_review/synthetic_reviewed_annotations.json",
            "--output-dir",
            str(output_dir),
            "--database-url",
            f"sqlite:///{(output_dir / 'synthetic.sqlite').as_posix()}",
            "--allow-persistence",
        ]
    )
    output = capsys.readouterr().out.lower()
    assert code == 0
    assert "synthetic_review_fixture" in output
    assert "api_key" not in output
    assert "prompt" not in output
    assert "completion" not in output

