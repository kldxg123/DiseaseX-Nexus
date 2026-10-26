from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlalchemy import create_engine, text

from epidemic_core.config import Settings
from epidemic_core.db.engine import create_app_engine
from epidemic_core.db.session import session_factory, transaction
from epidemic_core.domain.query import EpidemicQueryInput
from epidemic_core.query_service import QueryService
from epidemic_core.reference import seed


def main() -> int:
    parser=argparse.ArgumentParser(prog="epidemic-core")
    sub=parser.add_subparsers(dest="command",required=True)
    sub.add_parser("health");sub.add_parser("schema-version");sub.add_parser("seed-reference")
    for name in ("normalize-query","register-query"):
        p=sub.add_parser(name);p.add_argument("country");p.add_argument("disease");p.add_argument("start_date");p.add_argument("end_date")
    args=parser.parse_args();settings=Settings()
    if args.command=="seed-reference":
        if settings.MIGRATION_DATABASE_URL is None:raise SystemExit("migration connection is required")
        engine=create_engine(settings.MIGRATION_DATABASE_URL.get_secret_value(),hide_parameters=True)
        try:
            factory=session_factory(engine)
            with transaction(factory) as session:result=seed(session,Path.cwd())
            print(json.dumps(result,sort_keys=True))
        finally:engine.dispose()
        return 0
    engine=create_app_engine(settings)
    try:
        if args.command=="health":
            with engine.connect() as conn:conn.execute(text("SELECT 1"))
            print(json.dumps({"ok":True}));return 0
        if args.command=="schema-version":
            with engine.connect() as conn:v=conn.execute(text("SELECT version_num FROM epidemic.alembic_version")).scalar_one()
            print(json.dumps({"schema_version":v}));return 0
        value=EpidemicQueryInput(country=args.country,disease=args.disease,start_date=args.start_date,end_date=args.end_date);factory=session_factory(engine);service=QueryService()
        with transaction(factory) as session:
            if args.command=="normalize-query":result=service.normalize(session,value).model_dump(mode="json")
            else:
                row=service.register(session,value);result={"query_id":str(row.id),"fingerprint":row.fingerprint}
        print(json.dumps(result,sort_keys=True));return 0
    finally:engine.dispose()


if __name__=="__main__":raise SystemExit(main())
