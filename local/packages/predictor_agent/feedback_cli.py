from __future__ import annotations

import argparse
import json

from common.config import get_settings
from common.db import get_session, init_db
from predictor_agent.feedback import ExperienceBankStore, FeedbackInput, FeedbackStore


def _parse_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes", "y"}:
        return True
    if normalized in {"false", "0", "no", "n"}:
        return False
    raise argparse.ArgumentTypeError("Boolean values must be true or false.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Submit local human feedback for a prediction.")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--summary", action="store_true")
    parser.add_argument("--experiences", action="store_true")
    parser.add_argument("--prediction-id", type=int, default=None)
    parser.add_argument("--status", default=None)
    parser.add_argument("--actual-value", type=float, default=None)
    parser.add_argument("--actual-outbreak-label", type=_parse_bool, default=None)
    parser.add_argument("--failure-reason", default=None)
    parser.add_argument("--notes", default=None)
    parser.add_argument("--reviewer-ref", default=None)
    args = parser.parse_args(argv)

    database_url = args.database_url or get_settings().database_url
    init_db(database_url).dispose()
    with get_session(database_url) as session:
        service = FeedbackStore(session)
        if args.summary:
            print(json.dumps(service.summarize_feedback(), ensure_ascii=False, sort_keys=True))
            return 0
        if args.experiences:
            print(json.dumps(ExperienceBankStore(session).summarize_experiences(), ensure_ascii=False, sort_keys=True))
            return 0
        if args.prediction_id is None or args.status is None:
            raise SystemExit("--prediction-id and --status are required unless --summary or --experiences is used.")
        result = service.add_feedback(
            FeedbackInput(
                prediction_id=args.prediction_id,
                feedback_status=args.status,
                actual_value=args.actual_value,
                actual_outbreak_label=args.actual_outbreak_label,
                failure_reason=args.failure_reason,
                notes=args.notes,
                reviewer_ref=args.reviewer_ref,
            )
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
