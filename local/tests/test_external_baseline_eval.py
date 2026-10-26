from __future__ import annotations

from pathlib import Path

from src.eval.external_baseline_eval import evaluate_external_baseline


def test_external_baseline_eval_does_not_train_when_artifacts_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = evaluate_external_baseline()
    assert result["baseline_external_eval_status"] == "not_available"
    assert result["reason"] == "missing_baseline_artifacts"
    assert result["retrained_baseline"] is False
