from __future__ import annotations

import argparse
import json

from deepseek_web_search_agent.human_review_promotion import promote_reviewed_records_to_epidemic_events


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate and optionally promote DeepSeek human review annotations.")
    parser.add_argument("--report-dir", required=True)
    parser.add_argument("--annotation-file", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--allow-persistence", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = promote_reviewed_records_to_epidemic_events(
        report_dir=args.report_dir,
        annotation_file=args.annotation_file,
        output_dir=args.output_dir,
        database_url=args.database_url,
        allow_persistence=bool(args.allow_persistence),
    )
    result = report.result
    print(
        json.dumps(
            {
                "status": report.status,
                "imported_annotations": result.imported_annotations,
                "approved_event_count": result.approved_event_count,
                "blocked_count": result.blocked_count,
                "promoted_epidemic_events": result.promoted_epidemic_events,
                "feature_generation_allowed": result.feature_generation_allowed,
                "feature_eligibility_blockers": result.feature_eligibility_blockers,
                "promotion_report": report.paths.get("event_promotion_summary_json"),
                "feature_eligibility_report": report.paths.get("feature_eligibility_json"),
                "safety_audit": report.paths.get("safety_audit"),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
