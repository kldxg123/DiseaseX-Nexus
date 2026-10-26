from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
from pathlib import Path

from sqlalchemy import Engine, text

from epidemic_core.config import Settings
from epidemic_core.db.engine import create_app_engine
from epidemic_core.p1_12.discovery import SPECS, CandidateDiscoveryService
from epidemic_core.p1_12.trusted import (
    TrustedIngestionService,
    TrustedUpdateService,
    create_trusted_ingest_engine,
)

PROJECT = Path(__file__).resolve().parents[3]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="epidemic-p1-12")
    parser.add_argument(
        "command",
        choices=(
            "ingest-trusted",
            "ingest-ebola",
            "ingest-influenza",
            "discover-candidates",
            "discover-uganda",
            "discover-who-afro",
            "discover-ukhsa",
            "schedule-manual",
            "schedule-run",
            "status",
        ),
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    engine = create_app_engine(Settings())
    trusted_engine: Engine | None = None
    payload: object
    try:
        if args.command == "ingest-trusted":
            trusted_engine = create_trusted_ingest_engine()
            payload = {
                key: dataclasses.asdict(value)
                for key, value in TrustedIngestionService(trusted_engine, PROJECT).ingest_all().items()
            }
        elif args.command == "ingest-ebola":
            trusted_engine = create_trusted_ingest_engine()
            payload = dataclasses.asdict(TrustedIngestionService(trusted_engine, PROJECT).ingest_ebola())
        elif args.command == "ingest-influenza":
            trusted_engine = create_trusted_ingest_engine()
            payload = dataclasses.asdict(TrustedIngestionService(trusted_engine, PROJECT).ingest_influenza())
        elif args.command == "discover-candidates":
            payload = [dataclasses.asdict(value) for value in CandidateDiscoveryService(engine, PROJECT).run_all()]
        elif args.command.startswith("discover-"):
            keys = {
                "discover-uganda": "uganda-moh-evd-daily-v2",
                "discover-who-afro": "who-afro-bvd-weekly-v2",
                "discover-ukhsa": "ukhsa-flu-ecmo-admissions-v2",
            }
            discovery_service = CandidateDiscoveryService(engine, PROJECT)
            spec = next(item for item in SPECS if item.key == keys[args.command])
            payload = dataclasses.asdict(discovery_service.run_one(spec))
        elif args.command == "schedule-manual":
            trusted_engine = create_trusted_ingest_engine()
            ids = TrustedUpdateService(engine, PROJECT, trusted_engine).seed_due(
                "manual", dt.datetime.now(dt.timezone.utc)
            )
            payload = {"run_ids": [str(value) for value in ids], "count": len(ids)}
        elif args.command == "schedule-run":
            trusted_engine = create_trusted_ingest_engine()
            update_service = TrustedUpdateService(engine, PROJECT, trusted_engine)
            count = 0
            while update_service.run_due_once():
                count += 1
                if count > 20:
                    raise RuntimeError("TRUSTED_UPDATE_RUN_LIMIT")
            payload = {"processed": count}
        else:
            with engine.connect() as connection:
                payload = {
                    "review_ready": [
                        dict(row)
                        for row in connection.execute(
                            text("SELECT * FROM epidemic_p1_12.review_ready_candidates_v2 ORDER BY publisher")
                        ).mappings()
                    ],
                    "trusted_catalog": [
                        dict(row)
                        for row in connection.execute(
                            text("SELECT * FROM epidemic_p1_12.trusted_dashboard_catalog_v1 ORDER BY source_key")
                        ).mappings()
                    ],
                    "schedules": [
                        dict(row)
                        for row in connection.execute(
                            text("SELECT * FROM epidemic_p1_12.trusted_update_status_v1 ORDER BY schedule_id")
                        ).mappings()
                    ],
                }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))
    finally:
        if trusted_engine is not None:
            trusted_engine.dispose()
        engine.dispose()


if __name__ == "__main__":
    main()
