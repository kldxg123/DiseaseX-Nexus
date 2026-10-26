from __future__ import annotations

import argparse
import json
from typing import Any

from src.eval.backtest import run_backtests
from src.eval.metrics import LABEL_UP
from src.utils.config import load_config, resolve_path
from src.utils.io import ensure_dir, write_json


def _best_7d_up_recall(result: dict[str, Any]) -> dict[str, Any]:
    best = {"feature_set": None, "up_recall_mean": None, "macro_f1_mean": None, "balanced_accuracy_mean": None}
    for feature_set, item in result.get("horizons", {}).get("7d", {}).items():
        if item.get("status") != "ok":
            continue
        recall = item.get("up_recall_mean")
        if recall is None:
            continue
        if best["up_recall_mean"] is None or recall > best["up_recall_mean"]:
            best = {
                "feature_set": feature_set,
                "up_recall_mean": recall,
                "macro_f1_mean": item.get("macro_f1_mean"),
                "balanced_accuracy_mean": item.get("balanced_accuracy_mean"),
            }
    return best


def run_after_data_expansion_backtest(config_path: str = "configs/config.yaml", horizons: list[int] | None = None) -> dict[str, Any]:
    cfg = load_config(config_path)
    reports = ensure_dir(resolve_path(cfg["paths"]["outputs"]) / "reports")
    result = run_backtests(config_path, horizons or [7, 14, 21, 30])
    payload = {
        "status": result["status"],
        "blocked_reason": result["blocked_reason"],
        "metric_source": "5_fold_chronological_expanding_walk_forward_after_data_expansion",
        "best_7d_up_recall_backtest": _best_7d_up_recall(result),
        "horizons": result.get("horizons", {}),
    }
    write_json(reports / "backtest_after_data_expansion.json", payload)
    lines = [
        "# Backtest After Data Expansion",
        "",
        f"status: {payload['status']}",
        f"blocked_reason: {payload['blocked_reason']}",
        "metric_source: 5_fold_chronological_expanding_walk_forward_after_data_expansion",
        "",
        "## Best 7d Up Recall",
        "```json",
        json.dumps(payload["best_7d_up_recall_backtest"], ensure_ascii=False, indent=2),
        "```",
    ]
    (reports / "backtest_after_data_expansion.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Run P0d walk-forward backtest after data expansion.")
    parser.add_argument("--config", default="configs/config.yaml")
    parser.add_argument("--horizons", nargs="*", type=int, default=[7, 14, 21, 30])
    args = parser.parse_args()
    result = run_after_data_expansion_backtest(args.config, args.horizons)
    print(json.dumps({"status": result["status"], "blocked_reason": result["blocked_reason"], "best_7d": result["best_7d_up_recall_backtest"]}, ensure_ascii=False))
    return 0 if result["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
