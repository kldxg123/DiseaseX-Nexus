import json
from pathlib import Path

from src.reports.qwen_candidate_v2_training_plan import build_training_plan


def test_training_plan_is_draft_and_does_not_start_training(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "configs/train_qwen25_lora_v2.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "base_model": "Qwen/Qwen2.5-7B-Instruct",
                "local_model_dir": "models/Qwen2.5-7B-Instruct",
                "train_from": "base_model",
                "continue_from_adapter": None,
                "use_4bit": True,
                "bnb_4bit_quant_type": "nf4",
                "bnb_4bit_compute_dtype": "bfloat16",
                "num_train_epochs": 3,
                "max_seq_length": 4096,
                "per_device_train_batch_size": 1,
                "gradient_accumulation_steps": 8,
                "seed": 43,
                "candidate_configs": {
                    "v2a_rebalance_only": {"lora_r": 16, "lora_alpha": 32, "learning_rate": 0.0001},
                    "v2b_rebalance_plus_prompt_instruction": {"lora_r": 32, "lora_alpha": 64, "learning_rate": 0.0002},
                },
            }
        ),
        encoding="utf-8",
    )

    plan = build_training_plan("configs/train_qwen25_lora_v2.yaml")

    assert plan["status"] == "ok"
    assert plan["training_status"] == "not_started"
    assert plan["tmux_started"] is False
    assert plan["qwen_download"] is False
    assert plan["promote_allowed"] is False
