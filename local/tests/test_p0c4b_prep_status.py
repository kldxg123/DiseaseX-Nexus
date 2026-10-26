import json
from pathlib import Path

from src.reports.p0c4b_prep_status import build_status


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_p0c4b_status_ok_when_all_preparation_artifacts_exist(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    reports = tmp_path / "data/outputs/reports"
    _write(reports / "p0c4a_final_review.json", {"status": "ok", "blocked_reason": "none"})
    _write(
        reports / "qwen_candidate_v2_dataset_report.json",
        {
            "status": "ok",
            "blocked_reason": "none",
            "no_test_leakage": True,
            "v2_train_distribution": {"上升": 2, "下降": 1, "稳定": 1},
            "v2_balanced_train_distribution": {"上升": 2, "下降": 2, "稳定": 2},
        },
    )
    _write(reports / "qwen_candidate_v2_prompt_plan.json", {"status": "ok", "blocked_reason": "none"})
    _write(reports / "qwen_candidate_v2_training_plan.json", {"status": "ok", "blocked_reason": "none"})
    _write(reports / "qwen_candidate_v2_eval_plan.json", {"status": "ok", "blocked_reason": "none"})
    _write(
        tmp_path / "data/processed/model_registry.json",
        {
            "allow_auto_promote": False,
            "adapters": [
                {"adapter_id": "qwen25_lora_v2_planned", "status": "planned", "promote_allowed": False, "allow_auto_promote": False}
            ],
        },
    )

    status = build_status()

    assert status["status"] == "ok"
    assert status["p0c4b_train_allowed"] is True
    assert status["requires_user_confirmation_for_training"] is True
    assert status["promote_allowed"] is False


def test_p0c4b_status_blocks_when_dataset_missing(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    reports = tmp_path / "data/outputs/reports"
    _write(reports / "p0c4a_final_review.json", {"status": "ok", "blocked_reason": "none"})
    _write(reports / "qwen_candidate_v2_prompt_plan.json", {"status": "ok", "blocked_reason": "none"})
    _write(reports / "qwen_candidate_v2_training_plan.json", {"status": "ok", "blocked_reason": "none"})
    _write(reports / "qwen_candidate_v2_eval_plan.json", {"status": "ok", "blocked_reason": "none"})
    _write(tmp_path / "data/processed/model_registry.json", {"allow_auto_promote": False, "adapters": []})

    status = build_status()

    assert status["status"] == "blocked"
    assert "qwen_candidate_v2_dataset_report" in status["blocked_reason"]
