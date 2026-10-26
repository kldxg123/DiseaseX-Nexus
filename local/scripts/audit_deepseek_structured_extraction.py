from __future__ import annotations

import argparse
import json
from typing import Any

from deepseek_web_search_agent.extraction_quality_audit import write_deepseek_quality_audit_report


def run_audit(args: argparse.Namespace) -> dict[str, Any]:
    return write_deepseek_quality_audit_report(args.report_dir, database_url=args.database_url)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit existing DeepSeek structured extraction replay outputs.")
    parser.add_argument("--report-dir", required=True)
    parser.add_argument("--database-url", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = run_audit(args)
    paths = result.get("paths") or {}
    print(
        json.dumps(
            {
                "status": result.get("status"),
                "records_extracted": result.get("records_extracted"),
                "records_persisted": result.get("records_persisted"),
                "persistence_match": result.get("persistence_match"),
                "evidence_only_count": result.get("evidence_only_count"),
                "event_ready_count": result.get("event_ready_count"),
                "feature_ready_count": result.get("feature_ready_count"),
                "visualization_ready_count": result.get("visualization_ready_count"),
                "requires_human_review_count": result.get("requires_human_review_count"),
                "audit_report": paths.get("audit_json"),
                "readiness_table": paths.get("readiness_json"),
                "visualization_audit": paths.get("visualization_json"),
                "next_replay_plan": paths.get("next_replay_plan"),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
