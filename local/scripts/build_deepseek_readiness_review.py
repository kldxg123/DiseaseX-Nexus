from __future__ import annotations

import argparse
import json
from typing import Any

from deepseek_web_search_agent.readiness_review import (
    DEFAULT_DEEPSEEK_REPORT_DIR,
    write_readiness_review_reports,
)


def run_review(args: argparse.Namespace) -> dict[str, Any]:
    return write_readiness_review_reports(args.report_dir)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a local DeepSeek readiness review table and manual review queue.")
    parser.add_argument("--report-dir", default=DEFAULT_DEEPSEEK_REPORT_DIR)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = run_review(args)
    paths = result.get("paths") or {}
    print(
        json.dumps(
            {
                "status": result.get("status"),
                "total_records": result.get("total_records"),
                "evidence_only_count": result.get("evidence_only_count"),
                "event_ready_count": result.get("event_ready_count"),
                "feature_ready_count": result.get("feature_ready_count"),
                "visualization_ready_count": result.get("visualization_ready_count"),
                "requires_human_review_count": result.get("requires_human_review_count"),
                "top_blockers": result.get("top_blockers") or [],
                "readiness_review_table": paths.get("readiness_review_table_json"),
                "manual_review_queue": paths.get("manual_review_queue_json"),
                "dashboard_summary": paths.get("dashboard_summary_json"),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
