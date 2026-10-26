from __future__ import annotations

import argparse
import json
import sys

from common.config import get_settings
from common.db import get_session, init_db
from predictor_agent.minimum_data_analyzer import (
    MinimumDataAnalysisRequest,
    MinimumDataAnalyzer,
    result_to_dict,
)


def _parse_groups(value: str | None) -> list[str] | None:
    if value is None or not value.strip():
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_windows(value: str | None) -> list[int] | None:
    if value is None or not value.strip():
        return None
    return [int(item.strip()) for item in value.split(",") if item.strip()]


def main(argv: list[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    if args_list and args_list[0] != "analyze":
        args_list = ["analyze", *args_list]
    parser = argparse.ArgumentParser(description="Analyze minimum local feature groups and history windows.")
    subparsers = parser.add_subparsers(dest="command")
    analyze_parser = subparsers.add_parser("analyze")
    analyze_parser.add_argument("--database-url", default=None)
    analyze_parser.add_argument("--country", default=None)
    analyze_parser.add_argument("--disease", default=None)
    analyze_parser.add_argument("--horizon-days", type=int, required=True)
    analyze_parser.add_argument("--target-metric", required=True)
    analyze_parser.add_argument("--min-score", type=float, required=True)
    analyze_parser.add_argument("--feature-groups", "--candidate-feature-groups", dest="feature_groups", default=None)
    analyze_parser.add_argument("--history-windows-weeks", default=None)
    analyze_parser.add_argument("--model", default="feature_rule")
    analyze_parser.add_argument("--min-train-size", type=int, default=8)
    args = parser.parse_args(args_list)

    if args.command != "analyze":
        parser.error("Use the analyze command.")

    database_url = args.database_url or get_settings().database_url
    init_db(database_url).dispose()
    request_kwargs = {
        "country": args.country,
        "disease": args.disease,
        "horizon_days": args.horizon_days,
        "target_metric": args.target_metric,
        "min_acceptable_score": args.min_score,
        "candidate_feature_groups": _parse_groups(args.feature_groups),
        "model_name": args.model,
        "min_train_size": args.min_train_size,
    }
    windows = _parse_windows(args.history_windows_weeks)
    if windows is not None:
        request_kwargs["history_windows_weeks"] = windows
    try:
        request = MinimumDataAnalysisRequest(**request_kwargs)
        with get_session(database_url) as session:
            result = MinimumDataAnalyzer(session).analyze(request)
    except ValueError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2

    print(json.dumps(result_to_dict(result), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
