"""Read-only validation scan for a third influenza autotuning track.

This helper never evaluates the final 52 rows.  It loads each candidate's
trailing 445 weekly observations, restricts all fitting/scoring to rows
0..392 (321 train + 72 validation), and reports validation metrics only.
"""

from __future__ import annotations

import json
from typing import Any

import psycopg
from psycopg.rows import dict_row

from epidemic_core.kg_pt.split import make_split
from epidemic_core.kg_pt.tuner import run_tuning, select_best_trial
from epidemic_core.kg_qwen import adapters_ext, contracts_ext
from epidemic_core.kg_qwen.pipeline import _load_normalized_rows
from epidemic_core.kg_r3c.execution import sha256_json


SOCKET = "USER_HOME/projects/openclaw/runtime/postgres-run"
DB = "epidemic_openclaw"
USER = "epidemic_app"
DISEASE_ID = "6c9e2d3e-3a33-525a-8fbf-2b2eb1db4982"
METRIC = "influenza_positive_detections"
TOTAL_N = 445
TUNING_N = 393
TRAIN_N = 321
VAL_N = 72
SEED = 20260811

ADAPTERS = (
    "last_value",
    "rolling_mean",
    "drift",
    "seasonal_naive",
    "lagged_count_boosting.v1",
    "fourier_negbin_glm.v1",
)


def main() -> None:
    adapters_ext.register_extended_models()
    with psycopg.connect(host=SOCKET, port=55432, dbname=DB, user=USER, row_factory=dict_row) as conn:
        conn.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        conn.execute("SET LOCAL statement_timeout = '60s'")
        countries = [
            row["country_alpha2"]
            for row in conn.execute(
                """
                select country_alpha2
                from epidemic.normalized_observations
                where disease_id=%s and metric_code=%s and granularity='week'
                group by country_alpha2
                having count(distinct period_start) >= %s
                order by country_alpha2
                """,
                (DISEASE_ID, METRIC, TOTAL_N),
            ).fetchall()
            if row["country_alpha2"] != "CL"
        ]
        results: list[dict[str, Any]] = []
        for country in countries:
            source = {
                "country_alpha2": country,
                "disease_id": DISEASE_ID,
                "metric_code": METRIC,
                "granularity": "week",
            }
            rows = _load_normalized_rows(conn, source)[-TOTAL_N:]
            if len(rows) != TOTAL_N:
                continue
            tuning_rows = rows[:TUNING_N]
            split = make_split([r.period_start for r in tuning_rows], TRAIN_N, VAL_N, 0)
            adapter_metrics: dict[str, Any] = {}
            for adapter_id in ADAPTERS:
                contracts = contracts_ext.all_contracts_for(adapter_id, "week")
                tuning = run_tuning(
                    tuning_rows,
                    split,
                    adapter_id,
                    contracts,
                    seed=SEED,
                    max_trials=1,
                    max_seconds=180,
                    frequency="week",
                )
                idx = select_best_trial(tuning.trials)
                trial = tuning.trials[0] if idx is None else next(t for t in tuning.trials if t.trial_index == idx)
                adapter_metrics[adapter_id] = {
                    "wape": trial.common_wape,
                    "agreement_pct": None if trial.common_wape is None else max(0.0, 100.0 * (1.0 - trial.common_wape)),
                    "coverage": trial.coverage,
                    "gate_pass": trial.gate_pass,
                    "labeled_cells": sum(o.n_labeled for o in trial.origins),
                }
            baseline_ids = ("last_value", "rolling_mean", "drift", "seasonal_naive")
            baseline_valid = [
                (aid, adapter_metrics[aid])
                for aid in baseline_ids
                if adapter_metrics[aid]["gate_pass"] and adapter_metrics[aid]["wape"] is not None
            ]
            best_baseline = min(baseline_valid, key=lambda x: x[1]["wape"]) if baseline_valid else (None, None)
            candidates = [
                (aid, m)
                for aid, m in adapter_metrics.items()
                if aid not in baseline_ids and m["gate_pass"] and m["wape"] is not None
            ]
            best_candidate = min(candidates, key=lambda x: x[1]["wape"]) if candidates else (None, None)
            results.append(
                {
                    "country_alpha2": country,
                    "window": {
                        "start": rows[0].period_start.isoformat(),
                        "tuning_end": tuning_rows[-1].period_start.isoformat(),
                        "hidden_start": rows[TUNING_N].period_start.isoformat(),
                        "hidden_end": rows[-1].period_start.isoformat(),
                        "hidden_opened": False,
                    },
                    "nonzero_tuning": sum(1 for r in tuning_rows if r.value is not None and r.value > 0),
                    "missing_tuning": sum(1 for r in tuning_rows if r.value is None),
                    "snapshot_candidate_sha256": sha256_json(
                        [[r.period_start.isoformat(), r.value, r.revision] for r in rows]
                    ),
                    "best_baseline": {"adapter_id": best_baseline[0], **(best_baseline[1] or {})},
                    "best_candidate": {"adapter_id": best_candidate[0], **(best_candidate[1] or {})},
                    "adapters": adapter_metrics,
                }
            )
        conn.rollback()
    results.sort(
        key=lambda r: (
            -(r["best_candidate"].get("agreement_pct") or -1),
            -(r["best_baseline"].get("agreement_pct") or -1),
            r["country_alpha2"],
        )
    )
    print(json.dumps({"hidden_rows_evaluated": 0, "candidate_count": len(results), "results": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
