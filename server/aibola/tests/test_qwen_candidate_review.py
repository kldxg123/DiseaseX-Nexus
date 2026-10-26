import json
from pathlib import Path

from src.eval.metrics import LABEL_DOWN, LABEL_STABLE, LABEL_UP
from src.eval.qwen_candidate_review import review_candidate


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_candidate_review_holds_low_up_recall(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _write(tmp_path / "configs/config.yaml", {"step": {"model_registry_path": "data/processed/model_registry.json"}})
    adapter = tmp_path / "lora_weights/ebola_qwen25_step/candidates/qwen25_lora_20260623_011633"
    adapter.mkdir(parents=True)
    for name in ["adapter_config.json", "adapter_model.safetensors", "run_manifest.json", "train_metrics.json"]:
        (adapter / name).write_text("{}", encoding="utf-8")
    _write(
        tmp_path / "data/processed/model_registry.json",
        {
            "allow_auto_promote": False,
            "adapters": [
                {
                    "adapter_id": "qwen25_lora_20260623_011633",
                    "status": "candidate",
                    "promote_allowed": False,
                    "allow_auto_promote": False,
                }
            ],
        },
    )
    _write(
        tmp_path / "data/outputs/reports/qwen_lora_train_report.json",
        {"training_status": "completed", "candidate_adapter_path": "lora_weights/ebola_qwen25_step/candidates/qwen25_lora_20260623_011633"},
    )
    _write(tmp_path / "data/outputs/reports/p0c2_status.json", {"eval_status": "completed"})
    _write(
        tmp_path / "data/outputs/reports/qwen_lora_eval_metrics.json",
        {
            "status": "ok",
            "test": {
                "json_validity_rate": 1.0,
                "evidence_list_validity_rate": 1.0,
                "metrics": {
                    "macro_f1": 0.8,
                    "per_class": {
                        LABEL_UP: {"recall": 0.29},
                        LABEL_DOWN: {"recall": 0.8},
                        LABEL_STABLE: {"recall": 0.9},
                    },
                },
            },
        },
    )
    result = review_candidate("configs/config.yaml")
    assert result["status"] == "ok"
    assert result["registry_status"] == "candidate"
    assert result["promote_allowed"] is False
    assert result["promote_gate_result"]["promote_gate_passed"] is False
    assert result["promote_gate_result"]["primary_reason"] == "low_up_recall"
