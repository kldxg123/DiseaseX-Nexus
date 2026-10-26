import json
from pathlib import Path

from src.eval.qwen_lora_v2_compare import build_comparison
from src.models.qwen25_lora_v2_dataset_validate import validate_dataset
from src.reports.p0c4b_train_status import build_status
from src.step.adapter_registry import update_p0c4b_train_candidate


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows) + "\n", encoding="utf-8")


def _record(label: str, country: str = "A", date: str = "2020-01-01") -> dict:
    return {
        "messages": [
            {"role": "system", "content": "system"},
            {"role": "user", "content": json.dumps({"country": country, "date": date, "horizon_days": 7, "features": {}}, ensure_ascii=False)},
            {"role": "assistant", "content": json.dumps({"trend": label, "confidence": 0.8, "risk_score": 0.5, "evidence": []}, ensure_ascii=False)},
        ]
    }


def test_v2_dataset_validate_blocks_validation_leakage(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _write_json(
        tmp_path / "configs/train_qwen25_lora_v2.yaml",
        {
            "base_model_dir": "models/Qwen2.5-7B-Instruct",
            "local_model_dir": "models/Qwen2.5-7B-Instruct",
            "original_train_file": "data/processed/qwen25_sft_train.jsonl",
            "candidate_v2_train_file": "data/processed/qwen25_sft_train_v2.jsonl",
            "train_file": "data/processed/qwen25_sft_train_v2_balanced.jsonl",
            "validation_file": "data/processed/qwen25_sft_val.jsonl",
            "test_file": "data/processed/qwen25_sft_test.jsonl",
        },
    )
    leaked = _record("上升", "V")
    _write_jsonl(tmp_path / "data/processed/qwen25_sft_train.jsonl", [_record("稳定", "T")])
    _write_jsonl(tmp_path / "data/processed/qwen25_sft_train_v2.jsonl", [leaked])
    _write_jsonl(tmp_path / "data/processed/qwen25_sft_train_v2_balanced.jsonl", [leaked])
    _write_jsonl(tmp_path / "data/processed/qwen25_sft_val.jsonl", [leaked])
    _write_jsonl(tmp_path / "data/processed/qwen25_sft_test.jsonl", [_record("下降", "X")])
    _write_json(
        tmp_path / "data/outputs/reports/qwen_candidate_v2_dataset_report.json",
        {"status": "ok", "no_test_leakage": True, "validation_unchanged": True, "test_unchanged": True},
    )

    result = validate_dataset("configs/train_qwen25_lora_v2.yaml")

    assert result["status"] == "blocked"
    assert "validation_or_test_leakage_detected" in result["blocked_reason"]


def test_p0c4b_train_status_running_keeps_no_promote(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    reports = tmp_path / "data/outputs/reports"
    _write_json(reports / "qwen_lora_v2_dataset_validation_report.json", {"status": "ok"})
    _write_json(reports / "qwen_lora_v2_gpu_preflight.json", {"status": "ok", "selected_gpu": 2})
    _write_json(reports / "qwen_lora_v2_dryrun_report.json", {"status": "ok", "training_status": "dry_run_ok"})
    _write_json(
        reports / "qwen_lora_v2_train_report.json",
        {"status": "running", "training_status": "running", "tmux_session": "s", "log_path": "~/aibola/logs/x.log"},
    )
    _write_json(tmp_path / "data/processed/model_registry.json", {"adapters": [{"adapter_id": "qwen25_lora_v2_planned", "status": "planned"}]})

    status = build_status()

    assert status["status"] == "running"
    assert status["registry_status"] == "planned"
    assert status["promote_allowed"] is False


def test_v2_registry_candidate_requires_eval_and_compare(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    _write_json(tmp_path / "configs/config.yaml", {"step": {"model_registry_path": "data/processed/model_registry.json"}})
    _write_json(
        tmp_path / "data/processed/model_registry.json",
        {"allow_auto_promote": False, "adapters": [{"adapter_id": "qwen25_lora_20260623_011633", "status": "candidate", "promote_allowed": False}]},
    )
    adapter = tmp_path / "lora_weights/ebola_qwen25_step/candidates/qwen25_lora_v2_20200101_000000"
    adapter.mkdir(parents=True)
    for name in ["adapter_config.json", "adapter_model.safetensors", "run_manifest.json", "train_metrics.json"]:
        (adapter / name).write_text("{}", encoding="utf-8")
    _write_json(
        tmp_path / "data/outputs/reports/qwen_lora_v2_train_report.json",
        {"training_status": "completed", "candidate_adapter_path": adapter.relative_to(tmp_path).as_posix()},
    )

    result = update_p0c4b_train_candidate("configs/config.yaml")

    assert result["status"] == "blocked"
    assert result["blocked_reason"] == "v2_eval_not_ok"


def test_v2_compare_never_allows_promote(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    metric = {
        "json_validity_rate": 1.0,
        "evidence_list_validity_rate": 1.0,
        "metrics": {
            "macro_f1": 0.9,
            "balanced_accuracy": 0.9,
            "false_alarm_rate": 0.01,
            "stable_to_up_count": 1,
            "per_class": {"上升": {"recall": 0.8, "precision": 0.8}},
        },
        "metric_source": "sft_test_original_prompt",
    }
    _write_json(tmp_path / "data/outputs/reports/qwen_lora_v2_eval_metrics.json", {"status": "ok", "test_original": metric})

    result = build_comparison()

    assert result["status"] == "ok"
    assert result["promote_gate_passed"] is True
    assert result["promote_allowed"] is False
    assert result["allow_auto_promote"] is False
