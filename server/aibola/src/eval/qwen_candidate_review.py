from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from src.eval.metrics import LABEL_UP
from src.step.adapter_registry import P0C2_REQUIRED_FILES
from src.utils.config import load_config, resolve_path
from src.utils.io import ensure_dir, write_json


DEFAULT_ADAPTER_ID = "qwen25_lora_20260623_011633"
DEFAULT_ADAPTER_PATH = "lora_weights/ebola_qwen25_step/candidates/qwen25_lora_20260623_011633"
UP_RECALL_THRESHOLD = 0.50
MACRO_F1_THRESHOLD = 0.70
JSON_VALIDITY_THRESHOLD = 0.98


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def adapter_record(registry: dict[str, Any], adapter_id: str) -> dict[str, Any]:
    for item in registry.get("adapters", []):
        if isinstance(item, dict) and item.get("adapter_id") == adapter_id:
            return item
    return {}


def promote_gate_from_metrics(test_eval: dict[str, Any]) -> dict[str, Any]:
    metrics = test_eval.get("metrics", {}) if isinstance(test_eval, dict) else {}
    per_class = metrics.get("per_class", {}) if isinstance(metrics, dict) else {}
    up_recall = (per_class.get(LABEL_UP) or {}).get("recall")
    macro_f1 = metrics.get("macro_f1")
    json_validity_rate = test_eval.get("json_validity_rate")
    evidence_validity_rate = test_eval.get("evidence_list_validity_rate")

    checks = {
        "json_validity_ok": isinstance(json_validity_rate, (int, float)) and json_validity_rate >= JSON_VALIDITY_THRESHOLD,
        "macro_f1_ok": isinstance(macro_f1, (int, float)) and macro_f1 >= MACRO_F1_THRESHOLD,
        "up_recall_ok": isinstance(up_recall, (int, float)) and up_recall >= UP_RECALL_THRESHOLD,
        "evidence_validity_ok": isinstance(evidence_validity_rate, (int, float)) and evidence_validity_rate >= JSON_VALIDITY_THRESHOLD,
    }
    failed = [name for name, ok in checks.items() if not ok]
    primary_reason = "none"
    if "up_recall_ok" in failed:
        primary_reason = "low_up_recall"
    elif failed:
        primary_reason = failed[0].replace("_ok", "")
    return {
        "promote_gate_passed": False if failed else True,
        "primary_reason": primary_reason,
        "thresholds": {
            "json_validity_rate": JSON_VALIDITY_THRESHOLD,
            "macro_f1": MACRO_F1_THRESHOLD,
            "up_recall": UP_RECALL_THRESHOLD,
            "evidence_validity_rate": JSON_VALIDITY_THRESHOLD,
        },
        "observed": {
            "json_validity_rate": json_validity_rate,
            "macro_f1": macro_f1,
            "up_recall": up_recall,
            "evidence_validity_rate": evidence_validity_rate,
        },
        "checks": checks,
        "manual_review_required": True,
        "promote_recommendation": "hold",
    }


def review_candidate(config_path: str, adapter_id: str = DEFAULT_ADAPTER_ID) -> dict[str, Any]:
    cfg = load_config(config_path)
    reports = resolve_path("data/outputs/reports")
    registry_path = resolve_path(cfg["step"]["model_registry_path"])
    registry = load_json(registry_path)
    train_report = load_json(reports / "qwen_lora_train_report.json")
    eval_metrics = load_json(reports / "qwen_lora_eval_metrics.json")
    p0c2_status = load_json(reports / "p0c2_status.json")

    adapter_path = train_report.get("candidate_adapter_path") or DEFAULT_ADAPTER_PATH
    adapter_dir = resolve_path(adapter_path)
    required_file_status = {name: (adapter_dir / name).exists() for name in P0C2_REQUIRED_FILES}
    missing = [name for name, exists in required_file_status.items() if not exists]
    record = adapter_record(registry, adapter_id)
    test_eval = eval_metrics.get("test", {}) if isinstance(eval_metrics.get("test"), dict) else {}
    promote_gate = promote_gate_from_metrics(test_eval)
    registry_candidate_ok = record.get("status") == "candidate"
    auto_promote_risk = bool(record.get("promote_allowed")) or bool(record.get("allow_auto_promote")) or bool(registry.get("allow_auto_promote"))

    blocked_reasons: list[str] = []
    if missing:
        blocked_reasons.append("missing_candidate_artifacts:" + ",".join(missing))
    if eval_metrics.get("status") != "ok":
        blocked_reasons.append("p0c2_eval_metrics_not_ok")
    if not registry_candidate_ok:
        blocked_reasons.append("registry_not_candidate")
    if auto_promote_risk:
        blocked_reasons.append("auto_promote_risk_detected")
    if p0c2_status and p0c2_status.get("eval_status") != "completed":
        blocked_reasons.append("p0c2_eval_not_completed")

    return {
        "status": "ok" if not blocked_reasons else "blocked",
        "blocked_reason": "none" if not blocked_reasons else ",".join(blocked_reasons),
        "adapter_id": adapter_id,
        "adapter_path": adapter_path,
        "required_artifacts": required_file_status,
        "registry_status": record.get("status", "missing"),
        "promote_allowed": False,
        "allow_auto_promote": False,
        "auto_promote_risk": auto_promote_risk,
        "eval_metrics_status": eval_metrics.get("status", "missing"),
        "p0c2_eval_status": p0c2_status.get("eval_status", "unknown"),
        "metrics_source": "P0c-2 accepted SFT test eval",
        "promote_gate_result": promote_gate,
        "candidate_review_passed": not blocked_reasons,
    }


def write_review_report(result: dict[str, Any]) -> Path:
    reports = ensure_dir("data/outputs/reports")
    write_json(reports / "qwen_candidate_review.json", result)
    gate = result["promote_gate_result"]
    lines = [
        "# Qwen Candidate Review",
        "",
        f"status: {result['status']}",
        f"blocked_reason: {result['blocked_reason']}",
        f"adapter_id: {result['adapter_id']}",
        f"adapter_path: {result['adapter_path']}",
        f"registry_status: {result['registry_status']}",
        f"candidate_review_passed: {str(result['candidate_review_passed']).lower()}",
        f"promote_gate_passed: {str(gate['promote_gate_passed']).lower()}",
        f"primary_reason: {gate['primary_reason']}",
        "promote_allowed: false",
        "allow_auto_promote: false",
        "promote_recommendation: hold",
        "manual_review_required: true",
        "",
        "## Required Artifacts",
        "```json",
        json.dumps(result["required_artifacts"], ensure_ascii=False, indent=2, sort_keys=True),
        "```",
        "",
        "## Promote Gate",
        "```json",
        json.dumps(gate, ensure_ascii=False, indent=2, sort_keys=True),
        "```",
    ]
    path = reports / "qwen_candidate_review.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Review fixed Qwen LoRA candidate without promotion.")
    parser.add_argument("--config", default="configs/config.yaml")
    parser.add_argument("--adapter-id", default=DEFAULT_ADAPTER_ID)
    args = parser.parse_args()
    result = review_candidate(args.config, args.adapter_id)
    report = write_review_report(result)
    print(json.dumps({"status": result["status"], "blocked_reason": result["blocked_reason"], "report": report.as_posix()}, ensure_ascii=False))
    return 0 if result["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
