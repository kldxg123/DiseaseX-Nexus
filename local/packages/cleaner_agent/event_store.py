from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from cleaner_agent.event_extraction import ExtractedEvent
from common.models import EpidemicEvent, RawDocument


class EpidemicEventStore:
    def __init__(self, session: Session):
        self.session = session

    def exists_for_raw_document(self, raw_document_id: str | int) -> bool:
        return self._get_event(raw_document_id) is not None

    def save_event(self, raw_document_id: str | int, event: ExtractedEvent) -> EpidemicEvent:
        existing = self._get_event(raw_document_id)
        raw_document = self._get_raw_document(raw_document_id)
        if existing is not None:
            if raw_document is not None:
                raw_document.extraction_status = "cleaned"
            return existing

        epidemic_event = EpidemicEvent(
            raw_document_id=int(raw_document_id),
            country=event.country,
            admin1=event.admin1,
            admin2=event.admin2,
            disease=event.disease,
            event_date=event.event_date,
            iso_week=event.iso_week,
            cases=event.cases,
            suspected_cases=event.suspected_cases,
            confirmed_cases=event.confirmed_cases,
            deaths=event.deaths,
            hospitalizations=event.hospitalizations,
            tests=event.tests,
            positivity_rate=event.positivity_rate,
            outbreaks_count=event.outbreaks_count,
            setting=event.setting,
            pathogen_genotype=event.pathogen_genotype,
            confidence_score=event.confidence_score,
            extraction_method=event.extraction_method,
            provenance_json=event.provenance_json,
        )
        self.session.add(epidemic_event)
        if raw_document is not None:
            raw_document.extraction_status = "cleaned"
            raw_document.country_detected = event.country
            raw_document.disease_detected = event.disease
        self.session.flush()
        return epidemic_event

    def mark_no_event(self, raw_document_id: str | int) -> None:
        raw_document = self._get_raw_document(raw_document_id)
        if raw_document is not None:
            raw_document.extraction_status = "no_event"
            self.session.flush()

    def _get_event(self, raw_document_id: str | int) -> EpidemicEvent | None:
        return self.session.scalar(
            select(EpidemicEvent).where(EpidemicEvent.raw_document_id == int(raw_document_id))
        )

    def _get_raw_document(self, raw_document_id: str | int) -> RawDocument | None:
        return self.session.get(RawDocument, int(raw_document_id))
