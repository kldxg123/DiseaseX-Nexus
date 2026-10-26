import json

from src.step.forgetting_guard import build_forgetting_guard_report, can_promote, compute_cfi


def test_cfi_calculation():
    cfi = compute_cfi({"task1": {"best_score": 0.9, "current_score": 0.8}, "task2": {"best_score": 0.7, "current_score": 0.75}})
    assert round(cfi, 3) == 0.05


def test_cfi_blocks_promote():
    decision = can_promote({"task1": {"best_score": 0.9, "current_score": 0.7}}, threshold=0.05)
    assert decision["can_promote"] is False


def test_first_candidate_cfi_not_fully_available(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs/config.yaml").write_text(
        json.dumps(
            {
                "step": {
                    "model_registry_path": "data/processed/model_registry.json",
                    "experience_buffer_path": "data/processed/step_experience_buffer.jsonl",
                    "cfi_threshold": 0.05,
                }
            }
        ),
        encoding="utf-8",
    )
    registry_path = tmp_path / "data/processed/model_registry.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        json.dumps(
            {
                "adapters": [
                    {
                        "adapter_id": "qwen25_lora_20260623_011633",
                        "status": "candidate",
                        "promote_allowed": False,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    result = build_forgetting_guard_report("configs/config.yaml")
    assert result["cfi_status"] == "not_fully_available"
    assert result["reason"] == "first_candidate_adapter_no_prior_adapter_history"
    assert result["promote_allowed"] is False
