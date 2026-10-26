from __future__ import annotations

import argparse
import json

from src.step.state import StepState


def run_once() -> dict:
    state = StepState()
    state.tasks_run.extend(["T_BUILD_FEATURES", "T_BUILD_LABELS", "T_EVALUATE_BACKTEST"])
    return {"status": "ok", "mode": "run_once", "tasks_run": state.tasks_run}


def main() -> int:
    parser = argparse.ArgumentParser(description="STEP orchestrator.")
    parser.add_argument("--config", default="configs/config.yaml")
    parser.add_argument("--run-once", action="store_true")
    parser.add_argument("--mode", choices=["continuous"], default=None)
    args = parser.parse_args()
    if args.mode == "continuous":
        print(json.dumps({"status": "p2_blocked", "reason": "continuous_mode_after_p0"}, ensure_ascii=False))
        return 2
    print(json.dumps(run_once(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
