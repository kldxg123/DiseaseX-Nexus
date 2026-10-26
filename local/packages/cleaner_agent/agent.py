from __future__ import annotations

import argparse
import json

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from cleaner_agent.event_extraction import extract_event_from_raw_document
from cleaner_agent.event_store import EpidemicEventStore
from common.config import get_settings
from common.db import get_session, init_db
from common.models import RawDocument


class CleanerAgent:
    def __init__(self, session: Session):
        self.session = session

    def process_pending(
        self,
        *,
        limit: int = 100,
        country: str | None = None,
        disease: str | None = None,
    ) -> dict:
        summary = {
            "processed": 0,
            "events_created": 0,
            "no_event": 0,
            "duplicates": 0,
            "errors": 0,
        }
        store = EpidemicEventStore(self.session)
        documents = self.session.scalars(
            select(RawDocument)
            .where(or_(RawDocument.extraction_status.is_(None), RawDocument.extraction_status != "cleaned"))
            .order_by(RawDocument.id)
            .limit(max(0, int(limit)))
        ).all()

        for raw_document in documents:
            summary["processed"] += 1
            try:
                if store.exists_for_raw_document(raw_document.id):
                    summary["duplicates"] += 1
                    raw_document.extraction_status = "cleaned"
                    continue
                event = extract_event_from_raw_document(
                    raw_document,
                    target_country=country,
                    target_disease=disease,
                )
                if event is None:
                    store.mark_no_event(raw_document.id)
                    summary["no_event"] += 1
                    continue
                store.save_event(raw_document.id, event)
                summary["events_created"] += 1
            except Exception:
                summary["errors"] += 1
        self.session.flush()
        return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run local rule-based cleaner_agent MVP.")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--country", default=None)
    parser.add_argument("--disease", default=None)
    args = parser.parse_args(argv)

    database_url = args.database_url or get_settings().database_url
    init_db(database_url).dispose()
    with get_session(database_url) as session:
        summary = CleanerAgent(session).process_pending(
            limit=args.limit,
            country=args.country,
            disease=args.disease,
        )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
