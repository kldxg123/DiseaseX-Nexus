from __future__ import annotations

from pathlib import Path

from src.data.outbreak_risk_dataset import check_leakage
from src.utils.io import write_csv_records


def test_leakage_checker_blocks_publish_time_after_week(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "configs").mkdir()
    write_csv_records(
        tmp_path / "data/processed/outbreak_risk/outbreak_risk_dataset.csv",
        [
            {
                "sample_id": "s1",
                "week_end": "2022-01-07",
                "publish_time": "2022-01-08",
                "feature_cutoff_time": "2022-01-07",
                "split": "train",
                "source_event_usage": "train_candidate",
            }
        ],
    )
    result = check_leakage()
    assert result["status"] == "blocked"
    assert result["leakage_detected"] is True
