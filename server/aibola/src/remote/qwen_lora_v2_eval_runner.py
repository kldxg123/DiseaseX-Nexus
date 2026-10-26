from __future__ import annotations

import json
import os
from pathlib import Path

from src.models.qwen25_lora_v2_eval import run_eval, write_eval_report


def _load(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def main() -> int:
    train = _load("data/outputs/reports/qwen_lora_v2_train_report.json")
    gpu = _load("data/outputs/reports/qwen_lora_v2_gpu_preflight.json")
    selected = train.get("gpu_used") or gpu.get("selected_gpu")
    if selected is not None:
        os.environ["CUDA_VISIBLE_DEVICES"] = str(selected)
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_DATASETS_OFFLINE"] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    adapter = train.get("candidate_adapter_path")
    result = run_eval("configs/train_qwen25_lora_v2.yaml", adapter_dir=adapter, prompt_mode="both")
    report = write_eval_report(result)
    print(json.dumps({"status": result["status"], "blocked_reason": result.get("blocked_reason", "none"), "report": report.as_posix(), "gpu": selected}, ensure_ascii=False))
    return 0 if result["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
