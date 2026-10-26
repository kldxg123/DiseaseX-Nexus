from __future__ import annotations

import json
import math
import statistics
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from epidemic_core.kg_r3c.dbutil import connect
from fresh_opt_prescreen import (
    DISEASE_ID,
    HIDDEN_WEEKS,
    METRIC,
    Config,
    _calendarize,
    _configs,
    _hash,
    _load_tuning_rows,
    evaluate_block,
)


EXCLUDE_TRACKS = {
    "CHL:influenza:influenza_positive_detections:week",
    "MYS:influenza:influenza_positive_detections:week",
}


def candidate_series(cur: Any) -> list[tuple[str, str]]:
    cur.execute(
        """
        select country_iso3, country_alpha2
        from epidemic_p1_12.trusted_dashboard_series_v1
        where disease_id = %s and metric_code = %s and granularity = 'week'
          and country_iso3 is not null and country_alpha2 is not null
        group by country_iso3, country_alpha2
        having count(distinct period_start) >= 52 * 6
        order by country_iso3
        """,
        (DISEASE_ID, METRIC),
    )
    return [(str(row[0]), str(row[1])) for row in cur.fetchall()]


def prior_usage(cur: Any, track_key: str) -> dict[str, int]:
    checks = {
        "registry": "select count(*) from epidemic_kg.kg_qwen_heldout_registry where track_key=%s",
        "agent_runs": "select count(*) from epidemic_kg.kg_qwen_agent_runs where track_key=%s",
        "snapshots": "select count(*) from epidemic_kg.kg_pt_demo_snapshots where demo_key=%s",
    }
    result: dict[str, int] = {}
    for name, query in checks.items():
        cur.execute(query, (track_key,))
        result[name] = int(cur.fetchone()[0])
    return result


def score_candidate(cur: Any, iso3: str, alpha2: str, configs: list[Config]) -> dict[str, Any]:
    track_key = f"{iso3}:influenza:{METRIC}:week"
    usage = prior_usage(cur, track_key)
    if track_key in EXCLUDE_TRACKS or any(usage.values()):
        return {"track_key": track_key, "status": "EXCLUDED_PRIOR_USE", "prior_usage": usage}
    rows, hidden = _load_tuning_rows(cur, alpha2)
    dates, values = _calendarize(rows)
    nonnull = sum(value is not None and math.isfinite(float(value)) for value in values)
    if len(values) < 52 * 6 or nonnull / max(len(values), 1) < 0.80:
        return {
            "track_key": track_key,
            "status": "EXCLUDED_COVERAGE",
            "calendar_weeks_tuning": len(values),
            "nonnull_tuning": nonnull,
            **hidden,
        }

    # Six non-overlapping historical 52-week blocks, all strictly before the
    # reserved hidden tail. The first five select; the sixth is a pseudo-blind
    # prescreen. No hidden value is queried by _load_tuning_rows.
    block_ends = [len(values) - 1 - 52 * offset for offset in (5, 4, 3, 2, 1, 0)]
    ranked: list[tuple[float, float, str, Config, list[dict[str, Any]]]] = []
    for config in configs:
        blocks = [evaluate_block(values, end, config) for end in block_ends[:5]]
        wapes = [block.get("wape") for block in blocks]
        if any(value is None for value in wapes) or any(block["paired_n"] < 40 for block in blocks):
            continue
        numeric = [float(value) for value in wapes if value is not None]
        ranked.append((statistics.median(numeric), max(numeric), _hash(asdict(config)), config, blocks))
    ranked.sort(key=lambda item: (item[0], item[1], item[2]))
    if not ranked:
        return {"track_key": track_key, "status": "NO_VALID_CONFIG", **hidden}

    median_wape, worst_wape, config_sha, best_config, selection_blocks = ranked[0]
    pseudo = evaluate_block(values, block_ends[5], best_config)
    baselines: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
    for baseline_config in (Config("last_value"), Config("seasonal_mean", years=1)):
        metric = evaluate_block(values, block_ends[5], baseline_config)
        if metric["wape"] is not None:
            baselines.append((float(metric["wape"]), asdict(baseline_config), metric))
    baselines.sort(key=lambda item: item[0])
    best_baseline = baselines[0] if baselines else None
    pseudo_wape = pseudo.get("wape")
    improvement = None
    if best_baseline and pseudo_wape is not None and best_baseline[0] > 0:
        improvement = (best_baseline[0] - float(pseudo_wape)) / best_baseline[0]
    horizon_ok = all(
        value is None or float(value) <= 0.42 for value in (pseudo.get("per_horizon_wape") or {}).values()
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
    return {
        "track_key": track_key,
        "status": "PRESCREENED_TUNING_ONLY",
        "config": asdict(best_config),
        "config_sha256": config_sha,
        "selection_median_wape": median_wape,
        "selection_worst_wape": worst_wape,
        "selection_blocks": selection_blocks,
        "pseudo_blind": pseudo,
        "pseudo_best_baseline": None
        if best_baseline is None
        else {"wape": best_baseline[0], "config": best_baseline[1], "metrics": best_baseline[2]},
        "pseudo_improvement_vs_baseline": improvement,
        "pre_gate": pre_gate,
        "tuning_calendar_weeks": len(values),
        "tuning_nonnull": nonnull,
        "tuning_min_period": dates[0].isoformat(),
        "tuning_max_period": dates[-1].isoformat(),
        "tuning_series_sha256": _hash([(d.isoformat(), v) for d, v in zip(dates, values, strict=True)]),
        "prior_usage": usage,
        **hidden,
    }


def main(output_path: str) -> None:
    configs = _configs()
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            cur.execute("SET LOCAL statement_timeout='120s'")
            cur.execute("SET LOCAL idle_in_transaction_session_timeout='900s'")
            candidates = candidate_series(cur)
            results = [score_candidate(cur, iso3, alpha2, configs) for iso3, alpha2 in candidates]
            cur.execute("SHOW transaction_read_only")
            transaction_read_only = cur.fetchone()[0]
            conn.rollback()
    eligible = [result for result in results if result.get("pre_gate")]
    eligible.sort(
        key=lambda result: (
            float((result.get("pseudo_blind") or {}).get("wape") or 999),
            float(result.get("selection_median_wape") or 999),
            result["track_key"],
        )
    )
    payload = {
        "transaction_read_only": transaction_read_only,
        "hidden_values_read": False,
        "hidden_weeks_reserved_per_candidate": HIDDEN_WEEKS,
        "selection_protocol": "five historical 52-week blocks select; sixth disjoint 52-week block prescreens",
        "candidate_count": len(results),
        "pre_gate_count": len(eligible),
        "selected_track_key": eligible[0]["track_key"] if eligible else None,
        "results": results,
    }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    Path(output_path).write_text(rendered + "\n", encoding="utf-8")
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
    if len(sys.argv) != 2:
        raise SystemExit("usage: fresh_all_prescreen.py OUTPUT_JSON")
    main(sys.argv[1])
