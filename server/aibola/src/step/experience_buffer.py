from __future__ import annotations

import argparse
import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from src.eval.metrics import LABEL_ORDER
from src.step.experience import Experience
from src.utils.config import load_config, resolve_path
from src.utils.io import ensure_dir, read_jsonl, write_json, write_jsonl


DEFAULT_ADAPTER_ID = "qwen25_lora_20260623_011633"
SAMPLE_ARTIFACT = "data/outputs/reports/qwen_lora_eval_samples.jsonl"


class ExperienceBuffer:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def append(self, experience: Experience | dict) -> dict:
        record = experience.to_dict() if isinstance(experience, Experience) else dict(experience)
        records = read_jsonl(self.path)
        records.append(record)
        write_jsonl(self.path, records)
        return record

    def load(self) -> list[dict]:
        return read_jsonl(self.path)

    def partition(self, key: str) -> dict[str, list[dict]]:
        groups: dict[str, list[dict]] = defaultdict(list)
        for record in self.load():
            groups[str(record.get(key, ""))].append(record)
        return dict(groups)

    def query(self, **filters: Any) -> list[dict]:
        records = self.load()
        for key, value in filters.items():
            if value is None:
                continue
            records = [record for record in records if str(record.get(key)) == str(value)]
        return records

    def query_by_horizon(self, horizon_days: int) -> list[dict]:
        return self.query(horizon_days=horizon_days)

    def query_by_country(self, country: str) -> list[dict]:
        return self.query(country=country)

    def query_by_label(self, label: str) -> list[dict]:
        return self.query(true_label=label)

    def query_by_error_type(self, error_type: str) -> list[dict]:
        return self.query(error_type=error_type)

    def sample_replay(self, size: int, seed: int = 42) -> list[dict]:
        rng = random.Random(seed)
        records = self.load()
        by_label: dict[str, list[dict]] = defaultdict(list)
        for record in records:
            label = record.get("true_label") or record.get("prediction", {}).get("trend")
            by_label[str(label)].append(record)
        if not by_label:
            return []
        per_label = max(1, size // len(by_label))
        selected: list[dict] = []
        for rows in by_label.values():
            rows = list(rows)
            rng.shuffle(rows)
            selected.extend(rows[:per_label])
        rng.shuffle(selected)
        return selected[:size]

    def balanced_replay_sample(self, size: int, seed: int = 42) -> list[dict]:
        return self.sample_replay(size=size, seed=seed)


def experience_from_eval_sample(sample: dict[str, Any]) -> dict[str, Any]:
    true_label = sample.get("true_label")
    predicted_label = sample.get("predicted_label")
    return {
        "experience_id": sample.get("sample_id"),
        "adapter_id": sample.get("adapter_id", DEFAULT_ADAPTER_ID),
        "source": "p0c3_qwen_eval_replay",
        "source_note": "analysis artifact only; not replacement P0c-2 metrics",
        "date": sample.get("date"),
        "country": sample.get("country"),
        "adm1": sample.get("adm1"),
        "horizon_days": sample.get("horizon_days"),
        "input_features": sample.get("input_features", {}),
        "true_label": true_label,
        "predicted_label": predicted_label,
        "confidence": sample.get("confidence"),
        "risk_score": sample.get("risk_score"),
        "evidence": sample.get("evidence"),
        "data_quality_flags": sample.get("data_quality_flags", []),
        "error_type": "correct" if true_label == predicted_label else f"{true_label}_as_{predicted_label}",
        "json_checks": sample.get("json_checks", {}),
    }


def init_from_qwen_eval(config_path: str, adapter_id: str = DEFAULT_ADAPTER_ID) -> dict[str, Any]:
    cfg = load_config(config_path)
    buffer_path = resolve_path(cfg["step"]["experience_buffer_path"])
    samples = read_jsonl(resolve_path(SAMPLE_ARTIFACT))
    if not samples:
        result = {
            "status": "blocked",
            "blocked_reason": "qwen_eval_samples_missing",
            "adapter_id": adapter_id,
            "experience_buffer_path": cfg["step"]["experience_buffer_path"],
            "experience_count": 0,
        }
        write_experience_report(result)
        return result
    experiences = [experience_from_eval_sample(sample) for sample in samples]
    write_jsonl(buffer_path, experiences)
    label_counts = Counter(str(item.get("true_label")) for item in experiences)
    horizon_counts = Counter(str(item.get("horizon_days")) for item in experiences)
    error_counts = Counter(str(item.get("error_type")) for item in experiences if item.get("error_type") != "correct")
    buffer = ExperienceBuffer(buffer_path)
    replay_limit = min(int(cfg["step"].get("replay_sample_size", 500)), len(experiences))
    replay_sample = buffer.balanced_replay_sample(replay_limit)
    result = {
        "status": "ok",
        "blocked_reason": "none",
        "adapter_id": adapter_id,
        "experience_buffer_path": cfg["step"]["experience_buffer_path"],
        "experience_count": len(experiences),
        "label_distribution": {label: int(label_counts.get(label, 0)) for label in LABEL_ORDER},
        "horizon_distribution": dict(horizon_counts),
        "error_type_distribution": dict(error_counts),
        "balanced_replay_sample_count": len(replay_sample),
        "source_artifact": SAMPLE_ARTIFACT,
    }
    write_experience_report(result)
    return result


def write_experience_report(result: dict[str, Any]) -> Path:
    reports = ensure_dir("data/outputs/reports")
    write_json(reports / "step_experience_report.json", result)
    lines = [
        "# STEP Experience Buffer Report",
        "",
        f"status: {result['status']}",
        f"blocked_reason: {result['blocked_reason']}",
        f"adapter_id: {result.get('adapter_id', DEFAULT_ADAPTER_ID)}",
        f"experience_buffer_path: {result['experience_buffer_path']}",
        f"experience_count: {result.get('experience_count', 0)}",
        "",
        "## Summary",
        "```json",
        json.dumps({k: v for k, v in result.items() if k not in {"status", "blocked_reason"}}, ensure_ascii=False, indent=2, sort_keys=True),
        "```",
    ]
    path = reports / "step_experience_report.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize STEP experience buffer from Qwen eval samples.")
    parser.add_argument("--config", default="configs/config.yaml")
    parser.add_argument("--adapter-id", default=DEFAULT_ADAPTER_ID)
    parser.add_argument("--init-from-qwen-eval", action="store_true")
    args = parser.parse_args()
    if not args.init_from_qwen_eval:
        parser.error("Use --init-from-qwen-eval.")
    result = init_from_qwen_eval(args.config, args.adapter_id)
    print(json.dumps({"status": result["status"], "blocked_reason": result["blocked_reason"], "experience_count": result.get("experience_count", 0)}, ensure_ascii=False))
    return 0 if result["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
