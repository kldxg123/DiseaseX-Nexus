from __future__ import annotations

from sqlalchemy.orm import Session

from epidemic_core.db.models.tables import QueryRequest
from epidemic_core.db.repositories import CountryRepository, DiseaseRepository, QueryRepository
from epidemic_core.domain.query import EpidemicQueryInput, NormalizedEpidemicQuery
from epidemic_core.normalization import CountryResolver, DiseaseResolver, parse_date_range


class QueryService:
    def __init__(self) -> None:
        self.countries = CountryRepository()
        self.diseases = DiseaseRepository()
        self.queries = QueryRepository()

    def normalize(self, session: Session, value: EpidemicQueryInput) -> NormalizedEpidemicQuery:
        country = CountryResolver(self.countries.aliases(session)).resolve(value.country)
        disease = DiseaseResolver(self.diseases.aliases(session)).resolve(value.disease)
        start, end = parse_date_range(value.start_date, value.end_date)
        return NormalizedEpidemicQuery(
            country_alpha2=country.alpha2, disease_slug=disease.slug, start_date=start, end_date=end
        )

    def register(self, session: Session, value: EpidemicQueryInput) -> QueryRequest:
        normalized = self.normalize(session, value)
        disease_id = self.diseases.id_for_slug(session, normalized.disease_slug)
        return self.queries.create_or_get(session, value.country, value.disease, disease_id, normalized)
