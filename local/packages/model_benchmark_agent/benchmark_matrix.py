from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from model_benchmark_agent.feature_groups import get_feature_group_sets
from model_benchmark_agent.model_registry import get_benchmark_model_specs
from model_benchmark_agent.types import BenchmarkRunConfig, BenchmarkTask, task_from_dict

DEFAULT_METRICS = ["mae", "rmse", "smape", "f1", "auroc", "pr_auc"]


def build_benchmark_matrix(
    *,
    m2_readiness_dir: str | Path = "reports/milestone2_readiness",
    m1_freeze_dir: str | Path = "reports/milestone1_freeze",
    config: BenchmarkRunConfig | None = None,
) -> dict[str, Any]:
    run_config = config or BenchmarkRunConfig()
    evidence_summary = load_evidence_summary(m2_readiness_dir)
    m1_summary = load_m1_summary(m1_freeze_dir)
    tasks: list[BenchmarkTask] = []
    feature_sets = get_feature_group_sets()
    model_specs = get_benchmark_model_specs()
    for disease in run_config.diseases:
        for horizon in run_config.horizons_days:
            for model_spec in model_specs:
                for feature_set_name, groups in feature_sets.items():
                    task_id = (
                        f"{disease}_{horizon}d_{model_spec.model_name}_{feature_set_name}"
                        .replace("/", "_")
                        .replace(" ", "_")
                    )
                    warnings = list(model_spec.warnings)
                    if horizon not in {7, 14, 28}:
                        warnings.append("Non-standard horizon was requested.")
                    if model_spec.execution_status.startswith("skipped"):
                        warnings.append(model_spec.skip_reason or "Model is skipped for this benchmark.")
                    if "weather" in groups or "mobility" in groups:
                        warnings.append("Weather/mobility are planned but not available in current M1 data.")
                    tasks.append(
                        BenchmarkTask(
                            task_id=task_id,
                            disease=disease,
                            country=run_config.country,
                            prediction_target=run_config.prediction_target,
                            horizon_days=int(horizon),
                            model_family=model_spec.model_family,
                            model_name=model_spec.model_name,
                            feature_groups=list(groups),
                            train_window_weeks=run_config.train_window_weeks,
                            test_window_weeks=run_config.test_window_weeks,
                            rolling_folds=run_config.rolling_folds,
                            metrics=list(DEFAULT_METRICS),
                            evidence_source=run_config.evidence_source,
                            requires_human_review=run_config.requires_human_review,
                            warnings=warnings,
                        )
                    )
    return {
        "schema_version": "m2_m1_benchmark_matrix_v1",
        "config": run_config.to_dict(),
        "evidence_summary": evidence_summary,
        "m1_summary": m1_summary,
        "model_candidates": [spec.to_dict() for spec in model_specs],
        "feature_group_sets": feature_sets,
        "tasks": [task.to_dict() for task in tasks],
        "limitations": [
            "This matrix is generated from local reports and fixtures only.",
            "It does not claim final model superiority.",
            "All recommendations require human review before any production use.",
        ],
    }


def load_matrix(path: str | Path) -> list[BenchmarkTask]:
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return [task_from_dict(item) for item in payload.get("tasks", [])]


def load_evidence_summary(m2_readiness_dir: str | Path) -> dict[str, Any]:
    base = Path(m2_readiness_dir)
    json_path = base / "M2_M0_EVIDENCE_DISCOVERY_REPORT.json"
    if not json_path.exists():
        return {"status": "missing", "path": str(json_path), "warnings": ["M2 M0 evidence report not found."]}
    try:
        with json_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (json.JSONDecodeError, OSError) as exc:
        return {"status": "unreadable", "path": str(json_path), "warnings": [str(exc)]}
    return {
        "status": "loaded",
        "path": str(json_path),
        "candidate_count": _safe_len(payload.get("candidates") or payload.get("evidence_items")),
        "diseases": sorted(set(_extract_terms(payload, ["influenza", "norovirus"]))),
        "note": "Summary only; no raw evidence text is copied into benchmark outputs.",
    }


def load_m1_summary(m1_freeze_dir: str | Path) -> dict[str, Any]:
    base = Path(m1_freeze_dir)
    if not base.exists():
        return {"status": "missing", "path": str(base), "warnings": ["M1 freeze directory not found."]}
    report_files = sorted(path.name for path in base.glob("*") if path.is_file())
    return {
        "status": "loaded",
        "path": str(base),
        "file_count": len(report_files),
        "known_reports": report_files[:20],
    }


def matrix_to_markdown(matrix: dict[str, Any]) -> str:
    tasks = matrix.get("tasks", [])
    lines = [
        "# M2 M1 Benchmark Matrix",
        "",
        "This matrix converts local M2 M0 evidence/model/feature candidates into executable benchmark tasks.",
        "No live network, external APIs, deep model training, or production predictor changes are performed.",
        "",
        f"- Task count: {len(tasks)}",
        f"- Models: {', '.join(sorted({task['model_name'] for task in tasks}))}",
        f"- Diseases: {', '.join(sorted({task['disease'] for task in tasks}))}",
        f"- Horizons: {', '.join(str(value) for value in sorted({task['horizon_days'] for task in tasks}))} days",
        "",
        "| task_id | disease | horizon | model | feature_groups |",
        "|---|---:|---:|---|---|",
    ]
    for task in tasks[:60]:
        lines.append(
            f"| `{task['task_id']}` | {task['disease']} | {task['horizon_days']} | "
            f"{task['model_name']} | {', '.join(task['feature_groups'])} |"
        )
    if len(tasks) > 60:
        lines.append(f"| ... | ... | ... | ... | {len(tasks) - 60} additional tasks omitted from markdown preview |")
    lines.extend(
        [
            "",
            "## Limitations",
            "",
            "- Matrix generation is evidence-to-benchmark planning, not proof of final model performance.",
            "- Skipped optional models are retained in the matrix with warnings and cannot rank first.",
            "- All outputs require analyst review.",
        ]
    )
    return "\n".join(lines) + "\n"


def _safe_len(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def _extract_terms(payload: Any, defaults: list[str]) -> list[str]:
    text = json.dumps(payload, ensure_ascii=False).lower()[:5000]
    found = [term for term in defaults if term in text]
    return found or defaults


__all__ = [
    "DEFAULT_METRICS",
    "build_benchmark_matrix",
    "load_evidence_summary",
    "load_m1_summary",
    "load_matrix",
    "matrix_to_markdown",
]
