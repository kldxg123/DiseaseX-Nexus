from __future__ import annotations

from src.step.forgetting_guard import can_promote


def evaluate_candidate(metrics: dict, history_scores: dict, allow_auto_promote: bool = False, max_cfi: float = 0.05) -> dict:
    guard = can_promote(history_scores, max_cfi)
    return {
        "status": "candidate",
        "promote_allowed": bool(allow_auto_promote and guard["can_promote"]),
        "requires_human_approval": not allow_auto_promote,
        "cfi": guard["cfi"],
        "metrics": metrics,
    }
