from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from src.eval.metrics import LABEL_STABLE, LABEL_UP
from src.eval.p0c4a_common import qwen_samples, sft_records
from src.eval.up_recall_slice_analysis import evidence_has_case_growth
from src.utils.config import resolve_path
from src.utils.io import ensure_dir, write_json


def diagnose() -> dict[str, Any]:
    try:
        samples = qwen_samples(required=True)
    except FileNotFoundError as exc:
        return {"status": "blocked", "blocked_reason": str(exc), "missing_artifacts": ["qwen_lora_eval_samples.jsonl"]}
    sft_test = sft_records("test")
    up_misses = [row for row in samples if row.get("true_label") == LABEL_UP and row.get("predicted_label") == LABEL_STABLE]
    up_all = [row for row in samples if row.get("true_label") == LABEL_UP]
    prompt_has_case_features = 0
    prompt_has_threshold = 0
    prompt_has_future_rule = 0
    for row in up_misses:
        try:
            idx = int(str(row.get("sample_id", "0")).split("_")[-1])
            record = sft_test[idx]["record"]
        except Exception:
            record = {}
        text = " ".join(str(item.get("content", "")) for item in record.get("messages", []) if isinstance(item, dict) and item.get("role") in {"system", "user"})
        prompt_has_case_features += int("case_features" in text or "growth_" in text or "new_cases" in text)
        prompt_has_threshold += int("0.2" in text or "threshold" in text.lower() or "阈值" in text)
        prompt_has_future_rule += int("future" in text.lower() or "未来" in text)
    evidence_counts = Counter("case_growth_evidence_present" if evidence_has_case_growth(row) else "case_growth_evidence_missing" for row in up_misses)
    result = {
        "status": "ok",
        "blocked_reason": "none",
        "up_support": len(up_all),
        "up_as_stable_count": len(up_misses),
        "prompt_case_features_present_count": prompt_has_case_features,
        "prompt_explicit_threshold_present_count": prompt_has_threshold,
        "prompt_future_rule_present_count": prompt_has_future_rule,
        "evidence_case_growth_counts": dict(evidence_counts),
        "diagnosis": {
            "prompt_contains_enough_case_trend_fields": prompt_has_case_features == len(up_misses) if up_misses else True,
            "explicit_trend_threshold_missing": prompt_has_threshold < len(up_misses),
            "evidence_insufficient_for_up_misses": evidence_counts.get("case_growth_evidence_missing", 0) > evidence_counts.get("case_growth_evidence_present", 0),
            "qwen_overemphasizes_stable": len(up_misses) > 0,
        },
        "p0c4b_recommendations": [
            "add explicit up/down/stable threshold explanation to SFT prompt",
            "add hard positive up-class samples where Qwen predicted stable",
            "require evidence to cite case growth, acceleration, and recent-case features when predicting stable despite positive signals",
            "keep weather/ecology evidence secondary to recent case trend for up-class decisions",
        ],
        "prompt_modified": False,
    }
    return result


def write_report(result: dict[str, Any]) -> Path:
    reports = ensure_dir(resolve_path("data/outputs/reports"))
    write_json(reports / "qwen_prompt_evidence_diagnosis.json", result)
    lines = [
        "# Qwen Prompt Evidence Diagnosis",
        "",
        f"status: {result['status']}",
        f"blocked_reason: {result['blocked_reason']}",
        "prompt_modified: false",
        f"up_as_stable_count: {result.get('up_as_stable_count')}",
        "",
        "## Diagnosis",
        "```json",
        json.dumps(result.get("diagnosis", {}), ensure_ascii=False, indent=2, sort_keys=True),
        "```",
        "",
        "## Recommendations",
        "```json",
        json.dumps(result.get("p0c4b_recommendations", []), ensure_ascii=False, indent=2),
        "```",
    ]
    path = reports / "qwen_prompt_evidence_diagnosis.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose Qwen prompt/evidence issues without modifying prompts.")
    parser.parse_args()
    result = diagnose()
    report = write_report(result)
    print(json.dumps({"status": result["status"], "blocked_reason": result["blocked_reason"], "report": report.as_posix()}, ensure_ascii=False))
    return 0 if result["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
