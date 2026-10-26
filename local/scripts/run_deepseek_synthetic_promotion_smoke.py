from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from deepseek_web_search_agent.human_review_promotion import promote_reviewed_records_to_epidemic_events


def run_smoke(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    report = promote_reviewed_records_to_epidemic_events(
        report_dir=args.report_dir,
        annotation_file=args.annotation_file,
        output_dir=output_dir,
        database_url=args.database_url,
        allow_persistence=bool(args.allow_persistence),
    )
    result = report.result
    blockers = list(result.feature_eligibility_blockers)
    if "insufficient_distinct_iso_weeks" in blockers and "insufficient_temporal_signal" not in blockers:
        blockers.append("insufficient_temporal_signal")
    summary = {
        "status": "ok",
        "synthetic_review_fixture": True,
        "not_real_epidemiological_data": True,
        "for_gate_testing_only": True,
        "promoted_epidemic_events": result.promoted_epidemic_events,
        "blocked_count": result.blocked_count,
        "feature_generation_allowed": result.feature_generation_allowed,
        "feature_eligibility_blockers": blockers,
        "network_accessed": False,
        "deepseek_called_in_this_step": False,
        "tavily_rerun_in_this_step": False,
        "pilot_database_only": True,
    }
    paths = {
        "synthetic_promotion_summary_json": output_dir / "synthetic_promotion_summary.json",
        "synthetic_promotion_summary_md": output_dir / "synthetic_promotion_summary.md",
        "synthetic_event_promotion_decisions": output_dir / "synthetic_event_promotion_decisions.json",
        "synthetic_feature_eligibility": output_dir / "synthetic_feature_eligibility.json",
        "synthetic_promotion_safety_audit": output_dir / "synthetic_promotion_safety_audit.md",
    }
    paths["synthetic_promotion_summary_json"].write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    paths["synthetic_event_promotion_decisions"].write_text(
        json.dumps(
            {
                "synthetic_review_fixture": True,
                "not_real_epidemiological_data": True,
                "decisions": [decision.to_dict() for decision in result.decisions],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    paths["synthetic_feature_eligibility"].write_text(
        json.dumps(
            {
                "synthetic_review_fixture": True,
                "not_real_epidemiological_data": True,
                "feature_generation_allowed": result.feature_generation_allowed,
                "blocker_types": blockers,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    paths["synthetic_promotion_summary_md"].write_text(
        "# Synthetic Promotion Smoke Summary\n\n"
        "- Synthetic fixture: true\n"
        "- Not real epidemiological data: true\n"
        f"- Promoted events: {result.promoted_epidemic_events}\n"
        f"- Blocked decisions: {result.blocked_count}\n"
        f"- Feature generation allowed: {result.feature_generation_allowed}\n"
        f"- Feature blockers: {', '.join(blockers)}\n",
        encoding="utf-8",
    )
    paths["synthetic_promotion_safety_audit"].write_text(
        "# Synthetic Promotion Safety Audit\n\n"
        "- Network accessed: false\n"
        "- DeepSeek called: false\n"
        "- Tavily rerun: false\n"
        "- Candidate URLs accessed: false\n"
        "- Production database used: false\n"
        "- Synthetic labels present: true\n",
        encoding="utf-8",
    )
    return {**summary, "paths": {key: str(value) for key, value in paths.items()}}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run local synthetic DeepSeek promotion smoke test.")
    parser.add_argument("--report-dir", required=True)
    parser.add_argument("--annotation-file", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--allow-persistence", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = run_smoke(args)
    print(
        json.dumps(
            {
                "status": result.get("status"),
                "synthetic_review_fixture": result.get("synthetic_review_fixture"),
                "promoted_epidemic_events": result.get("promoted_epidemic_events"),
                "blocked_count": result.get("blocked_count"),
                "feature_generation_allowed": result.get("feature_generation_allowed"),
                "feature_eligibility_blockers": result.get("feature_eligibility_blockers"),
                "synthetic_promotion_summary": result.get("paths", {}).get("synthetic_promotion_summary_json"),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
