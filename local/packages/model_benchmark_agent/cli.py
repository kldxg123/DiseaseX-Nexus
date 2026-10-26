from __future__ import annotations

import argparse
import json
from pathlib import Path

from model_benchmark_agent.backtest_runner import load_fixture_records, run_benchmark_matrix
from model_benchmark_agent.benchmark_matrix import BenchmarkRunConfig, build_benchmark_matrix
from model_benchmark_agent.feature_ablation import run_feature_ablation
from model_benchmark_agent.feature_selection import rank_feature_groups
from model_benchmark_agent.m2_m2_report import (
    load_json,
    write_m2_m2_ablation_reports,
    write_m2_m2_minimum_reports,
    write_m2_m2_selection_reports,
)
from model_benchmark_agent.minimum_feature_recommender import recommend_minimum_features
from model_benchmark_agent.ranking import load_results, rank_benchmark_results
from model_benchmark_agent.report import (
    write_backtest_reports,
    write_benchmark_matrix_reports,
    write_feature_availability_report,
    write_gap_and_next_action_reports,
    write_ranking_reports,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Milestone 2 M1 model benchmark harness.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build-matrix")
    build.add_argument("--m2-readiness-dir", default="reports/milestone2_readiness")
    build.add_argument("--m1-freeze-dir", default="reports/milestone1_freeze")
    build.add_argument("--output-dir", default="reports/milestone2_m1")

    backtest = subparsers.add_parser("run-backtest")
    backtest.add_argument("--matrix", required=True)
    backtest.add_argument("--output-dir", default="reports/milestone2_m1")
    backtest.add_argument("--synthetic-smoke-if-needed", action="store_true")
    backtest.add_argument("--max-tasks", type=int)

    rank = subparsers.add_parser("rank-results")
    rank.add_argument("--results", required=True)
    rank.add_argument("--output-dir", default="reports/milestone2_m1")

    ablation = subparsers.add_parser("run-feature-ablation")
    ablation.add_argument("--m2-m1-dir", default="reports/milestone2_m1")
    ablation.add_argument("--output-dir", default="reports/milestone2_m2")
    ablation.add_argument("--synthetic-smoke-if-needed", action="store_true")

    selection = subparsers.add_parser("rank-feature-selection")
    selection.add_argument("--ablation-results", required=True)
    selection.add_argument("--output-dir", default="reports/milestone2_m2")

    minimum = subparsers.add_parser("recommend-minimum-features")
    minimum.add_argument("--selection-ranking", required=True)
    minimum.add_argument("--output-dir", default="reports/milestone2_m2")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "build-matrix":
        return _build_matrix(args)
    if args.command == "run-backtest":
        return _run_backtest(args)
    if args.command == "rank-results":
        return _rank_results(args)
    if args.command == "run-feature-ablation":
        return _run_feature_ablation(args)
    if args.command == "rank-feature-selection":
        return _rank_feature_selection(args)
    if args.command == "recommend-minimum-features":
        return _recommend_minimum_features(args)
    parser.error(f"Unknown command: {args.command}")
    return 2


def _build_matrix(args: argparse.Namespace) -> int:
    matrix = build_benchmark_matrix(
        m2_readiness_dir=args.m2_readiness_dir,
        m1_freeze_dir=args.m1_freeze_dir,
        config=BenchmarkRunConfig(),
    )
    paths = write_benchmark_matrix_reports(matrix, args.output_dir)
    feature_paths = write_feature_availability_report(load_fixture_records(), args.output_dir)
    print(
        json.dumps(
            {
                "status": "ok",
                "benchmark_matrix": paths["json"],
                "benchmark_matrix_markdown": paths["markdown"],
                "feature_availability_report": feature_paths["markdown"],
                "task_count": len(matrix.get("tasks", [])),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def _run_backtest(args: argparse.Namespace) -> int:
    results = run_benchmark_matrix(
        args.matrix,
        synthetic_smoke_if_needed=args.synthetic_smoke_if_needed,
        max_tasks=args.max_tasks,
    )
    paths = write_backtest_reports(results, args.output_dir)
    statuses: dict[str, int] = {}
    for row in results.get("results", []):
        statuses[row["status"]] = statuses.get(row["status"], 0) + 1
    print(
        json.dumps(
            {
                "status": "ok",
                "backtest_results": paths["json"],
                "backtest_results_markdown": paths["markdown"],
                "result_count": len(results.get("results", [])),
                "statuses": statuses,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def _rank_results(args: argparse.Namespace) -> int:
    results = load_results(args.results)
    ranking = rank_benchmark_results(results)
    paths = write_ranking_reports(ranking, args.output_dir)
    extra = write_gap_and_next_action_reports(ranking=ranking, output_dir=args.output_dir)
    print(
        json.dumps(
            {
                "status": "ok",
                "ranking_report": paths["json"],
                "ranking_markdown": paths["markdown"],
                "gap_analysis_update": extra["gap"],
                "next_action_plan": extra["next"],
                "top_model": (ranking.get("top_5") or [{}])[0].get("model_name"),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def _run_feature_ablation(args: argparse.Namespace) -> int:
    payload = run_feature_ablation(
        m2_m1_dir=args.m2_m1_dir,
        output_dir=None,
        synthetic_smoke_if_needed=args.synthetic_smoke_if_needed,
    )
    paths = write_m2_m2_ablation_reports(payload, args.output_dir)
    statuses: dict[str, int] = {}
    for row in payload.get("ablation_results", []):
        status = str(row.get("status", "unknown"))
        statuses[status] = statuses.get(status, 0) + 1
    print(
        json.dumps(
            {
                "status": "ok",
                "feature_ablation_results": paths["ablation_json"],
                "interpretable_baseline_results": paths["baseline_json"],
                "stability_analysis": paths["stability_json"],
                "result_count": len(payload.get("ablation_results", [])),
                "statuses": statuses,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def _rank_feature_selection(args: argparse.Namespace) -> int:
    payload = rank_feature_groups(load_json(args.ablation_results))
    paths = write_m2_m2_selection_reports(payload, args.output_dir)
    print(
        json.dumps(
            {
                "status": "ok",
                "feature_selection_ranking": paths["json"],
                "feature_selection_markdown": paths["markdown"],
                "top_recommended_feature_groups": payload.get("top_recommended_feature_groups", [])[:5],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def _recommend_minimum_features(args: argparse.Namespace) -> int:
    payload = recommend_minimum_features(load_json(args.selection_ranking))
    paths = write_m2_m2_minimum_reports(payload, args.output_dir)
    print(
        json.dumps(
            {
                "status": "ok",
                "minimum_feature_recommendation": paths["json"],
                "minimum_feature_markdown": paths["markdown"],
                "gap_analysis_update": paths["gap"],
                "next_action_plan": paths["next"],
                "recommendation_count": len(payload.get("recommendations", [])),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
