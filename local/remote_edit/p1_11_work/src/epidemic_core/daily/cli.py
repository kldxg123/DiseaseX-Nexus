from __future__ import annotations

import argparse
import datetime as dt
import json
import uuid
from pathlib import Path

from sqlalchemy import func, select

from epidemic_core.config import Settings
from epidemic_core.daily.service import DailyUpdateService
from epidemic_core.db.engine import create_app_engine
from epidemic_core.db.models.tables import DailyUpdateRun, DailyUpdateSchedule
from epidemic_core.db.session import session_factory

PROJECT = Path(__file__).resolve().parents[3]
CONFIG = PROJECT / "config/daily_update.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="epidemic-daily-update")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("seed-schedules")
    reconcile = commands.add_parser("reconcile-due")
    reconcile.add_argument("--trigger", choices=("openclaw-cron", "startup"), required=True)
    manual = commands.add_parser("start-manual")
    manual.add_argument("--schedule", type=uuid.UUID, required=True)
    status = commands.add_parser("status")
    status.add_argument("--run-id", type=uuid.UUID)
    return parser


def main() -> None:
    args = _parser().parse_args()
    now = dt.datetime.now(dt.timezone.utc)
    engine = create_app_engine(Settings())
    factory = session_factory(engine)
    try:
        with factory() as session, session.begin():
            if args.command == "seed-schedules":
                result = DailyUpdateService.seed_schedules(session, CONFIG, now)
                payload: dict[str, object] = {
                    "status": "PASS",
                    "created": result.created,
                    "updated": result.updated,
                    "schedule_count": len(result.schedule_ids),
                }
            elif args.command == "reconcile-due":
                reconcile_rows = DailyUpdateService.reconcile_due(session, args.trigger, now)
                payload = {
                    "status": "PASS",
                    "trigger": args.trigger,
                    "created": sum(1 for row in reconcile_rows if row.created),
                    "deduplicated": sum(1 for row in reconcile_rows if not row.created),
                    "run_ids": [str(row.run_id) for row in reconcile_rows],
                }
            elif args.command == "start-manual":
                run_id = DailyUpdateService.start_manual(session, args.schedule, now)
                payload = {"status": "PASS", "run_id": str(run_id)}
            else:
                if args.run_id:
                    run = session.get(DailyUpdateRun, args.run_id)
                    run_rows = [] if run is None else [run]
                else:
                    run_rows = list(
                        session.execute(
                            select(DailyUpdateRun).order_by(DailyUpdateRun.created_at.desc()).limit(20)
                        ).scalars()
                    )
                payload = {
                    "status": "PASS",
                    "schedules": int(session.scalar(select(func.count()).select_from(DailyUpdateSchedule)) or 0),
                    "runs": [
                        {
                            "run_id": str(row.run_id),
                            "schedule_id": str(row.schedule_id),
                            "logical_date": row.logical_date.isoformat(),
                            "trigger_type": row.trigger_type,
                            "state": row.state,
                            "missed_intervals": row.missed_intervals,
                            "retry_count": row.retry_count,
                            "max_retries": row.max_retries,
                            "source_changed": row.source_changed,
                            "observations_added": row.observations_added,
                            "observations_revised": row.observations_revised,
                            "rows_excluded": row.rows_excluded,
                            "reason": row.final_reason_code,
                        }
                        for row in run_rows
                    ],
                }
        print(json.dumps(payload, sort_keys=True))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
