from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

from epidemic_core.kg_pt.split import make_split
from epidemic_core.kg_qwen.agent_tools import ToolContext
from epidemic_core.kg_qwen.loop import QwenProposal, run_autotuning_loop
from epidemic_core.kg_qwen.plan_contract import deterministic_fallback_plan, validate_plan
from epidemic_core.kg_r3c.execution import TrainingRow

ROOT = Path("USER_HOME/projects/openclaw-fresh-ukhsa-20260814")
SOURCE = ROOT / "data/kg_qwen/who_fluid_zmb_sari_rate_frozen.csv"
TRAINVAL_N = 393
HIDDEN_N = 52
SNAPSHOT_SHA = "prefix-only-shadow-" + "a" * 45


def main() -> None:
    prefix: list[TrainingRow] = []
    prefix_end = 541 - HIDDEN_N
    with SOURCE.open("r", encoding="utf-8", newline="") as handle:
        for index, row in enumerate(csv.DictReader(handle)):
            if index >= prefix_end:
                break
            value = row["value"]
            prefix.append(
                TrainingRow(
                    period_start=datetime.strptime(row["period_start"], "%Y-%m-%d").date(),
                    period_end=None,
                    value=None if not value else float(value),
                )
            )
    rows = prefix[-TRAINVAL_N:]
    assert len(rows) == TRAINVAL_N
    split = make_split([row.period_start for row in rows], 321, 72, 0)
    plan = deterministic_fallback_plan(SNAPSHOT_SHA, "week", "prefix-only adapter replay")
    plan["candidate_models"] = ["seasonal_delta_blend.v1"]
    plan["parameter_spaces"] = [
        {
            "adapter_id": "seasonal_delta_blend.v1",
            "parameter_name": name,
            "values": values,
            "transform": "none",
            "parameter_source": "DATA_PROFILE_RULE",
            "evidence_ref": f"who_fluid_prefix_prescreen:{name}",
            "rationale": "仅依据隐藏窗之前的严格滚动回测预筛选。",
        }
        for name, values in (
            ("seasonal_years", [1, 2, 3]),
            ("recent_window", [4, 8, 13, 26]),
            ("blend_weight", [0.4, 0.6, 0.8]),
        )
    ]
    plan["max_trials_per_round"] = 50
    errors = validate_plan(plan, expected_snapshot_sha256=SNAPSHOT_SHA, frequency="week")
    if errors:
        raise SystemExit(json.dumps([error.__dict__ for error in errors], sort_keys=True))

    def planner(context: dict, round_index: int) -> QwenProposal:
        del context, round_index
        return QwenProposal(
            plan=plan,
            plan_source="QWEN_PREFIX_REPLAY",
            prompt_sha256="p" * 64,
            response_sha256="r" * 64,
            latency_ms=0,
        )

    context = ToolContext(
        track_key="ZMB:influenza:who_fluid_sari_rate_per_1000_inpatients:week",
        frequency="week",
        trainval_rows=rows,
        snapshot_sha256=SNAPSHOT_SHA,
        split=split,
        validation_summaries=[],
    )
    result = run_autotuning_loop(
        track_key=context.track_key,
        frequency="week",
        trainval_rows=rows,
        split=split,
        snapshot_sha256=SNAPSHOT_SHA,
        planner=planner,
        tool_context=context,
        max_rounds=1,
        max_trials_per_round=50,
    )
    print(
        json.dumps(
            {
                "hidden_values_read": False,
                "champion_adapter_id": result.champion_adapter_id,
                "champion_params": result.champion_params,
                "best_val_wape": result.best_val_wape,
                "best_val_agreement_pct": result.best_val_agreement_pct,
                "best_val_coverage": result.best_val_coverage,
                "target_met": result.target_met,
                "baseline_metrics": result.baseline_metrics,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
