from __future__ import annotations

import argparse
from pathlib import Path

from epidemic_core.config import Settings
from epidemic_core.db.engine import create_app_engine
from epidemic_core.ebola.service import EbolaMonitoringService, canonical_json


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("seed", "initial-backfill", "due-once"))
    args = parser.parse_args()
    project_root = Path(__file__).resolve().parents[3]
    engine = create_app_engine(Settings())
    try:
        service = EbolaMonitoringService(engine, project_root)
        if args.action == "seed":
            service.seed()
            result: object = {"seeded": True}
        elif args.action == "initial-backfill":
            result = service.initial_backfill()
        else:
            result = {"worked": service.run_due_once()}
        print(canonical_json(result))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
