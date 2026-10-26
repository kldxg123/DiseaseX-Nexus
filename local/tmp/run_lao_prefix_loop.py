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

SOURCE = Path("USER_HOME/projects/openclaw-fresh-ukhsa-20260814/data/kg_qwen/who_fluid_lao_ili_rate_frozen.csv")
TRACK_KEY = "LAO:influenza:who_fluid_ili_rate_per_1000_outpatients:week"
SNAPSHOT_SHA = "prefix-only-lao-" + "b" * 48


def main() -> None:
    prefix: list[TrainingRow] = []
    with SOURCE.open("r", encoding="utf-8", newline="") as handle:
        for index, row in enumerate(csv.DictReader(handle)):
            if index >= 552 - 52:
                break
            value = row["value"]
            prefix.append(
                TrainingRow(
                    period_start=datetime.strptime(row["period_start"], "%Y-%m-%d").date(),
                    period_end=None,
                    value=None if not value else float(value),
                )
            )
    rows = prefix[-393:]
    assert len(rows) == 393
    split = make_split([row.period_start for row in rows], 321, 72, 0)
    plan = deterministic_fallback_plan(SNAPSHOT_SHA, "week", "LAO prefix-only adapter replay")
    plan["candidate_models"] = ["seasonal_delta_blend.v1"]
    plan["parameter_spaces"] = [
        {
            "adapter_id": "seasonal_delta_blend.v1",
            "parameter_name": name,
            "values": values,
            "transform": "none",
            "parameter_source": "DATA_PROFILE_RULE",
            "evidence_ref": f"who_fluid_lao_prefix:{name}",
            "rationale": "仅依据隐藏窗之前的滚动回测。",
        }
        for name, values in (
            ("seasonal_years", [1, 2, 3]),
            ("recent_window", [4, 8, 13, 26]),
            ("blend_weight", [0.4, 0.6, 0.8]),
        )
    ]
    plan["max_trials_per_round"] = 50
    errors = validate_plan(plan, expected_snapshot_sha256=SNAPSHOT_SHA, frequency="week")
    assert not errors, errors

    def planner(context: dict, round_index: int) -> QwenProposal:
        del context, round_index
        return QwenProposal(
            plan=plan,
            plan_source="QWEN_PREFIX_REPLAY",
            prompt_sha256="p" * 64,
            response_sha256="r" * 64,
            latency_ms=0,
        )

    context = ToolContext(TRACK_KEY, "week", rows, SNAPSHOT_SHA, split, [])
    result = run_autotuning_loop(
        track_key=TRACK_KEY,
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
