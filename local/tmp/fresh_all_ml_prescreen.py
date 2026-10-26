from __future__ import annotations

import json
import statistics
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from epidemic_core.kg_r3c.dbutil import connect
from fresh_ml_prescreen import configs, evaluate_model_block
from fresh_opt_prescreen import DISEASE_ID, METRIC, Config as BaselineConfig, _calendarize, _hash, _load_tuning_rows, evaluate_block


def candidate_pool(simple: dict[str, Any]) -> list[str]:
    rows = [item for item in simple["results"] if item.get("status") == "PRESCREENED_TUNING_ONLY"]
    pseudo = sorted(
        rows,
        key=lambda item: (
            float((item.get("pseudo_blind") or {}).get("wape") or 999),
            float(item.get("selection_median_wape") or 999),
        ),
    )[:12]
    stable = sorted(
        rows,
        key=lambda item: (
            float(item.get("selection_median_wape") or 999),
            float(item.get("selection_worst_wape") or 999),
        ),
    )[:12]
    return sorted({str(item["track_key"]) for item in [*pseudo, *stable]})


def iso_map(cur: Any, track_keys: list[str]) -> dict[str, str]:
    iso3s = [key.split(":", 1)[0] for key in track_keys]
    cur.execute(
        """
        select distinct country_iso3, country_alpha2
        from epidemic_p1_12.trusted_dashboard_series_v1
        where disease_id=%s and metric_code=%s and granularity='week'
          and country_iso3 = any(%s)
        """,
        (DISEASE_ID, METRIC, iso3s),
    )
    return {str(row[0]): str(row[1]) for row in cur.fetchall()}


def main(simple_path: str, output_path: str) -> None:
    simple = json.loads(Path(simple_path).read_text(encoding="utf-8"))
    pool = candidate_pool(simple)
    model_configs = configs()
    results: list[dict[str, Any]] = []
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            cur.execute("SET LOCAL statement_timeout='300s'")
            cur.execute("SET LOCAL idle_in_transaction_session_timeout='1800s'")
            alpha2 = iso_map(cur, pool)
            for track_key in pool:
                iso3 = track_key.split(":", 1)[0]
                rows, hidden = _load_tuning_rows(cur, alpha2[iso3])
                dates, values = _calendarize(rows)
                block_ends = [len(values) - 1 - 52 * offset for offset in (5, 4, 3, 2, 1, 0)]
                ranked: list[tuple[float, float, str, Any, list[dict[str, Any]]]] = []
                for config in model_configs:
                    blocks = [evaluate_model_block(values, end, config) for end in block_ends[:5]]
                    wapes = [block.get("wape") for block in blocks]
                    if any(value is None for value in wapes) or any(block.get("paired_n", 0) < 40 for block in blocks):
                        continue
                    numeric = [float(value) for value in wapes if value is not None]
                    ranked.append((statistics.median(numeric), max(numeric), _hash(asdict(config)), config, blocks))
                ranked.sort(key=lambda item: (item[0], item[1], item[2]))
                if not ranked:
                    results.append({"track_key": track_key, "status": "NO_VALID_ML_CONFIG", **hidden})
                    continue
                median_wape, worst_wape, config_sha, best_config, selection_blocks = ranked[0]
                pseudo = evaluate_model_block(values, block_ends[5], best_config)
                baselines: list[tuple[float, str, dict[str, Any]]] = []
                for name, config in (
                    ("last_value", BaselineConfig("last_value")),
                    ("seasonal_naive", BaselineConfig("seasonal_mean", years=1)),
                ):
                    metric = evaluate_block(values, block_ends[5], config)
                    if metric.get("wape") is not None:
                        baselines.append((float(metric["wape"]), name, metric))
                baselines.sort(key=lambda item: item[0])
                baseline = baselines[0] if baselines else None
                pseudo_wape = pseudo.get("wape")
                improvement = None
                if baseline and pseudo_wape is not None and baseline[0] > 0:
                    improvement = (baseline[0] - float(pseudo_wape)) / baseline[0]
                horizon_ok = all(
                    value is None or float(value) <= 0.42
                    for value in (pseudo.get("per_horizon_wape") or {}).values()
                )
                pre_gate = bool(
                    median_wape <= 0.25
                    and worst_wape <= 0.35
                    and pseudo_wape is not None
                    and float(pseudo_wape) <= 0.27
                    and improvement is not None
                    and improvement >= 0.01
                    and pseudo.get("paired_n", 0) >= 40
                    and horizon_ok
                )
                results.append(
                    {
                        "track_key": track_key,
                        "status": "ML_PRESCREENED_TUNING_ONLY",
                        "config": asdict(best_config),
                        "config_sha256": config_sha,
                        "selection_median_wape": median_wape,
                        "selection_worst_wape": worst_wape,
                        "selection_blocks": selection_blocks,
                        "pseudo_blind": pseudo,
                        "pseudo_best_baseline": None
                        if baseline is None
                        else {"id": baseline[1], "wape": baseline[0], "metrics": baseline[2]},
                        "pseudo_improvement_vs_baseline": improvement,
                        "pre_gate": pre_gate,
                        "tuning_series_sha256": _hash([(d.isoformat(), v) for d, v in zip(dates, values, strict=True)]),
                        **hidden,
                    }
                )
            cur.execute("SHOW transaction_read_only")
            read_only = cur.fetchone()[0]
            conn.rollback()
    eligible = [item for item in results if item.get("pre_gate")]
    eligible.sort(key=lambda item: (float(item["pseudo_blind"]["wape"]), item["track_key"]))
    payload = {
        "transaction_read_only": read_only,
        "hidden_values_read": False,
        "candidate_pool": pool,
        "candidate_count": len(results),
        "pre_gate_count": len(eligible),
        "selected_track_key": eligible[0]["track_key"] if eligible else None,
        "results": results,
    }
    Path(output_path).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": output_path,
                "sha256": _hash(payload),
                "candidate_count": len(results),
                "pre_gate_count": len(eligible),
                "selected_track_key": payload["selected_track_key"],
                "top_candidates": [
                    {
                        "track_key": item["track_key"],
                        "selection_median_wape": item["selection_median_wape"],
                        "selection_worst_wape": item["selection_worst_wape"],
                        "pseudo_wape": item["pseudo_blind"]["wape"],
                        "pseudo_improvement_vs_baseline": item["pseudo_improvement_vs_baseline"],
                    }
                    for item in eligible[:5]
                ],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: fresh_all_ml_prescreen.py SIMPLE_JSON OUTPUT_JSON")
    main(sys.argv[1], sys.argv[2])
