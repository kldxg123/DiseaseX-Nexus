from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from src.eval.metrics import LABEL_ORDER, LABEL_STABLE, LABEL_UP, classification_metrics
from src.utils.config import resolve_path
from src.utils.io import ensure_dir, read_jsonl, write_json


HORIZONS = [7, 14, 21, 30]
REPORTS_DIR = Path("data/outputs/reports")
FIGURES_DIR = Path("data/outputs/figures")
EXTERNAL_VALIDATION_DIR = Path("data/processed/external_validation")


def load_json(path: str | Path) -> dict[str, Any]:
    p = resolve_path(path)
    if not p.exists():
        return {}
    try:
        loaded = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def write_report_pair(name: str, payload: dict[str, Any], lines: list[str]) -> None:
    reports = ensure_dir(resolve_path(REPORTS_DIR))
    write_json(reports / f"{name}.json", payload)
    (reports / f"{name}.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def label_distribution(samples: list[dict[str, Any]], label_key: str = "true_label") -> dict[str, int]:
    counts = Counter(str(row.get(label_key) or row.get("label") or "") for row in samples)
    return {label: int(counts.get(label, 0)) for label in LABEL_ORDER}


def external_dataset_path(horizon: int) -> Path:
    return EXTERNAL_VALIDATION_DIR / f"external_test_{horizon}d.jsonl"


def external_qwen_dataset_path(horizon: int) -> Path:
    return EXTERNAL_VALIDATION_DIR / f"external_qwen_sft_test_{horizon}d.jsonl"


def read_external_samples(horizon: int) -> list[dict[str, Any]]:
    return read_jsonl(resolve_path(external_dataset_path(horizon)))


def false_alarm_rate(y_true: list[str], y_pred: list[str]) -> float:
    non_up = sum(1 for label in y_true if label != LABEL_UP)
    false_up = sum(1 for truth, pred in zip(y_true, y_pred) if truth != LABEL_UP and pred == LABEL_UP)
    return false_up / non_up if non_up else 0.0


def stable_to_up_count(y_true: list[str], y_pred: list[str]) -> int:
    return sum(1 for truth, pred in zip(y_true, y_pred) if truth == LABEL_STABLE and pred == LABEL_UP)


def metric_bundle(y_true: list[str], y_pred: list[str]) -> dict[str, Any]:
    metrics = classification_metrics(y_true, y_pred, LABEL_ORDER)
    metrics["up_recall"] = metrics["per_class"][LABEL_UP]["recall"]
    metrics["up_precision"] = metrics["per_class"][LABEL_UP]["precision"]
    metrics["false_alarm_rate"] = false_alarm_rate(y_true, y_pred)
    metrics["stable_to_up_count"] = stable_to_up_count(y_true, y_pred)
    return metrics


def not_available(reason: str, **extra: Any) -> dict[str, Any]:
    payload = {"status": "not_available", "blocked_reason": "none", "reason": reason}
    payload.update(extra)
    return payload


def low_n_warning(sample_count: int, horizon: int) -> str | None:
    if sample_count == 0:
        return "not_available:no_external_test_samples"
    if horizon == 21 or sample_count < 50:
        return "low_n_warning"
    return None


def source_samples_by_horizon() -> dict[str, list[dict[str, Any]]]:
    return {f"{horizon}d": read_external_samples(horizon) for horizon in HORIZONS}


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def sample_true_label(sample: dict[str, Any]) -> str:
    return str(sample.get("true_label") or sample.get("label") or LABEL_STABLE)


def qwen_prediction_rows(version: str) -> list[dict[str, Any]]:
    return read_jsonl(resolve_path(REPORTS_DIR / f"external_qwen_{version}_predictions.jsonl"))


def write_confusion_matrix_figure(matrix: dict[str, dict[str, int]], path: str | Path, title: str) -> None:
    out = resolve_path(path)
    ensure_dir(out.parent)
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np

        values = np.array([[matrix[t][p] for p in LABEL_ORDER] for t in LABEL_ORDER])
        fig, ax = plt.subplots(figsize=(5, 4))
        ax.imshow(values, cmap="Blues")
        ax.set_xticks(range(len(LABEL_ORDER)), LABEL_ORDER)
        ax.set_yticks(range(len(LABEL_ORDER)), LABEL_ORDER)
        ax.set_xlabel("predicted")
        ax.set_ylabel("true")
        ax.set_title(title)
        for i in range(values.shape[0]):
            for j in range(values.shape[1]):
                ax.text(j, i, str(values[i, j]), ha="center", va="center")
        fig.tight_layout()
        fig.savefig(out, dpi=150)
        plt.close(fig)
    except Exception:
        out.with_suffix(".txt").write_text(json.dumps(matrix, ensure_ascii=False, indent=2), encoding="utf-8")


def horizon_metric_table(horizon_metrics: dict[str, Any]) -> list[str]:
    lines = [
        "| horizon | samples | macro-F1 | balanced accuracy | up recall | up precision | false alarm rate | warning |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for horizon in [f"{h}d" for h in HORIZONS]:
        item = horizon_metrics.get(horizon, {})
        if item.get("status") == "not_available":
            lines.append(f"| {horizon} | 0 |  |  |  |  |  | {item.get('reason', 'not_available')} |")
            continue
        metrics = item.get("metrics", item)
        samples = item.get("sample_count") or item.get("samples") or sum((metrics.get("support") or {}).values())
        lines.append(
            f"| {horizon} | {samples} | {metrics.get('macro_f1', 0):.4f} | {metrics.get('balanced_accuracy', 0):.4f} | "
            f"{metrics.get('up_recall', 0):.4f} | {metrics.get('up_precision', 0):.4f} | {metrics.get('false_alarm_rate', 0):.4f} | "
            f"{item.get('low_n_warning') or ''} |"
        )
    return lines


def extract_metric_summary(metrics: dict[str, Any]) -> dict[str, Any]:
    per_class = metrics.get("per_class") if isinstance(metrics.get("per_class"), dict) else {}
    up = per_class.get(LABEL_UP, {}) if isinstance(per_class.get(LABEL_UP), dict) else {}
    return {
        "macro_f1": metrics.get("macro_f1"),
        "balanced_accuracy": metrics.get("balanced_accuracy"),
        "up_recall": metrics.get("up_recall", up.get("recall")),
        "up_precision": metrics.get("up_precision", up.get("precision")),
        "false_alarm_rate": metrics.get("false_alarm_rate"),
        "stable_to_up_count": metrics.get("stable_to_up_count"),
        "confusion_matrix": metrics.get("confusion_matrix"),
        "per_class": metrics.get("per_class"),
    }
