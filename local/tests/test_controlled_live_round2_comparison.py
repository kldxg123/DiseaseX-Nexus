from __future__ import annotations

import json
from pathlib import Path

import httpx

from scripts.run_controlled_live_pilot import run_controlled_live_pilot_live
from tests.test_controlled_live_round2_mock import LIVE_URL, _six_week_html


def _write_inputs(tmp_path: Path) -> tuple[Path, Path]:
    round1 = tmp_path / "round1"
    reprocess = tmp_path / "reprocess"
    round1.mkdir()
    reprocess.mkdir()
    (round1 / "live_fetch_summary.json").write_text(
        json.dumps(
            {
                "raw_documents_saved": 6,
                "cleaner": {"events_created": 6},
                "features": {"feature_rows": 0},
                "content_hash": "round1-hash",
            }
        ),
        encoding="utf-8",
    )
    (round1 / "postmortem_report.json").write_text(json.dumps({}), encoding="utf-8")
    (round1 / "crawler_optimizer_report.json").write_text(
        json.dumps({"failure_case_count": 12}),
        encoding="utf-8",
    )
    (reprocess / "comparison_with_original.json").write_text(
        json.dumps(
            {
                "reprocessed": {
                    "feature_rows_total": 7,
                    "feature_rows_h7": 5,
                    "feature_rows_h28": 2,
                    "crawler_optimizer_failure_case_count": 0,
                }
            }
        ),
        encoding="utf-8",
    )
    (reprocess / "reprocess_summary.json").write_text(json.dumps({}), encoding="utf-8")
    return round1, reprocess


def test_round2_comparison_file_compares_round1_reprocess_and_round2(tmp_path) -> None:
    round1, reprocess = _write_inputs(tmp_path)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, text=_six_week_html())

    summary = run_controlled_live_pilot_live(
        source_key="cdc_norostat",
        live_url=LIVE_URL,
        allow_network=True,
        terms_reviewed=True,
        manual_reviewed=True,
        robots_allowed=True,
        max_requests=1,
        output_dir=str(tmp_path / "outputs"),
        round_label="round2",
        compare_with_original=str(round1),
        compare_with_reprocess=str(reprocess),
        transport=httpx.MockTransport(handler),
    )

    comparison_path = Path(summary["comparison_report"])
    comparison = json.loads(comparison_path.read_text(encoding="utf-8"))

    assert comparison_path.name == "comparison_round1_reprocess_round2.json"
    assert comparison["round1"]["feature_rows_total"] == 0
    assert comparison["round1"]["optimizer_failure_case_count"] == 12
    assert comparison["reprocess"]["feature_rows_total"] == 7
    assert comparison["round2"]["feature_rows_total"] == 7
    assert comparison["round2"]["feature_rows_h7"] == 5
    assert comparison["round2"]["feature_rows_h28"] == 2
    assert comparison["round2"]["optimizer_failure_case_count"] == 0
    assert comparison["assessment"]["round2_matches_reprocess_expectation"] is True
    assert comparison["assessment"]["feature_generation_fixed"] is True
    assert comparison["assessment"]["optimizer_false_positives_fixed"] is True
