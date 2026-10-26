from __future__ import annotations

import csv
from pathlib import Path

from src.eval.outbreak_dataset_audit import build_outbreak_dataset_audit
from src.eval.outbreak_external_error_audit import build_outbreak_external_error_audit
from src.remote.pull_reports import P2B_REPORTS
from src.reports.outbreak_dataset_expansion_plan import build_outbreak_dataset_expansion_plan
from src.reports.outbreak_feature_gap_analysis import build_outbreak_feature_gap_analysis
from src.reports.outbreak_literature_link_triage import build_outbreak_literature_link_triage
from src.reports.p2a_prototype_final_review import build_p2a_prototype_final_review
from src.reports.p2b_status import build_p2b_status
from src.utils.io import write_json


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _seed_p2a_fixture(root: Path) -> None:
    reports = root / "data/outputs/reports"
    processed = root / "data/processed/outbreak_risk"
    write_json(
        reports / "p2a_status.json",
        {
            "outbreak_event_count": 9,
            "region_week_panel_row_count": 272,
            "positive_sample_count": 36,
            "negative_sample_count": 96,
            "leakage_check_status": "passed",
            "prototype_training_allowed": True,
            "formal_training_allowed": False,
            "model_trained": "LogisticRegression",
            "prototype_only": True,
        },
    )
    write_json(
        reports / "outbreak_dataset_quality_report.json",
        {
            "outbreak_event_count": 9,
            "positive_region_week_count": 32,
            "negative_region_week_count": 96,
            "prototype_training_allowed": True,
            "formal_training_allowed": False,
            "prototype_only": True,
            "formal_checks": {"outbreak_event_count_min": False, "positive_region_week_count_min": False},
        },
    )
    write_json(reports / "outbreak_data_acquisition_report.json", {"records": [{"source": "WHO", "status": "downloaded"}]})
    write_json(
        reports / "outbreak_literature_survey_report.json",
        {
            "dataset_link_count": 3,
            "dataset_links": [
                {"source": "Zenodo", "title": "Ebola dataset", "url": "https://zenodo.org/records/1", "downloadable": True, "doi": "10.1/demo"},
                {"source": "PubMed", "title": "Ebola outbreak paper", "url": "https://pubmed.ncbi.nlm.nih.gov/1/", "pmid": "1"},
                {"source": "Crossref", "title": "Uganda Ebola vaccine report", "url": "https://doi.org/10.2/demo", "doi": "10.2/demo"},
            ],
        },
    )
    write_json(
        reports / "outbreak_risk_baseline_eval_report.json",
        {
            "results": {
                "external_test": {
                    "pr_auc": 0.238320707,
                    "recall_at_k": 0.0,
                    "false_alarms_per_100_region_weeks": 43.75,
                    "lead_time_weeks": 1.0,
                    "low_n_warning": True,
                    "confusion_matrix": {"tp": 1, "fp": 7, "tn": 5, "fn": 3},
                }
            }
        },
    )
    (root / "configs").mkdir(exist_ok=True)
    (root / "configs/outbreak_risk_training.yaml").write_text(
        "formal_thresholds:\n  outbreak_event_count_min: 15\n  positive_region_week_count_min: 100\n  negative_to_positive_min: 3.0\n",
        encoding="utf-8",
    )
    rows = [
        {"sample_id": "a", "split": "train", "outbreak_id": "o1", "label": 1, "week_start": "2020-01-01", "week_end": "2020-01-07", "region_id": "r1", "population": 1, "gdelt_signal_count_7d": 0, "mobility_proxy": 0.2},
        {"sample_id": "b", "split": "train", "outbreak_id": "o1", "label": 0, "week_start": "2020-01-08", "week_end": "2020-01-14", "region_id": "r1", "population": 1, "gdelt_signal_count_7d": 0, "mobility_proxy": 0.2},
        {"sample_id": "c", "split": "test", "outbreak_id": "o2", "label": 1, "week_start": "2021-01-01", "week_end": "2021-01-07", "region_id": "r2", "population": 1, "gdelt_signal_count_7d": 0, "mobility_proxy": 0.2},
    ]
    _write_csv(processed / "outbreak_risk_dataset.csv", rows)
    _write_csv(processed / "region_week_panel.csv", rows)
    _write_csv(processed / "outbreak_labels.csv", [{"sample_id": row["sample_id"], "outbreak_within_4w": row["label"]} for row in rows])
    _write_csv(processed / "negative_samples.csv", [{"sample_id": "b", "negative_type": "season_matched_control"}])


def test_p2b_reports_preserve_fixed_metrics_and_no_training_flags(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _seed_p2a_fixture(tmp_path)
    review = build_p2a_prototype_final_review()
    assert review["fixed_p2a_metrics"]["outbreak_event_count"] == 9
    assert review["fixed_p2a_metrics"]["formal_training_allowed"] is False
    assert review["not_for_model_claim"] is True
    triage = build_outbreak_literature_link_triage()
    assert triage["input_link_count"] == 3
    assert "directly_downloadable_structured_dataset" in triage["triage_counts"]
    expansion = build_outbreak_dataset_expansion_plan()
    assert expansion["continue_training_recommended"] is False
    assert expansion["qwen_lora_training_recommended"] is False


def test_dataset_and_external_error_audits_are_read_only(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _seed_p2a_fixture(tmp_path)
    audit = build_outbreak_dataset_audit()
    assert audit["single_class_issue"] is True
    assert audit["split_revision_should_train_now"] is False
    external = build_outbreak_external_error_audit()
    assert external["per_sample_artifact_available"] is False
    assert external["summary_level_audit"] is True
    assert external["threshold_tuned"] is False
    assert external["model_retrained"] is False


def test_feature_gap_and_p2b_status_require_data_expansion(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _seed_p2a_fixture(tmp_path)
    build_p2a_prototype_final_review()
    build_outbreak_dataset_audit()
    build_outbreak_external_error_audit()
    feature_gap = build_outbreak_feature_gap_analysis()
    assert feature_gap["gap_flags"]["pre_event_osint_signals"] is True
    assert feature_gap["gap_flags"]["enough_outbreak_events"] is False
    build_outbreak_literature_link_triage()
    build_outbreak_dataset_expansion_plan()
    status = build_p2b_status()
    assert status["Qwen_allowed"] is False
    assert status["LoRA_allowed"] is False
    assert status["service_allowed"] is False
    assert status["promote_allowed"] is False
    assert status["continue_training_recommended"] is False


def test_p2b_pull_whitelist_excludes_forbidden_paths() -> None:
    assert "p2b_status.json" in P2B_REPORTS
    assert "outbreak_risk_supervisor_report.md" in P2B_REPORTS
    assert "models/" not in P2B_REPORTS
    assert "lora_weights/" not in P2B_REPORTS
