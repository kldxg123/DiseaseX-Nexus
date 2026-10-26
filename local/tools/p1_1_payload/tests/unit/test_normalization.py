from __future__ import annotations

import datetime as dt

import pytest
from pydantic import ValidationError

from epidemic_core.domain.query import EpidemicQueryInput, NormalizedEpidemicQuery
from epidemic_core.errors import EpidemicCoreError, ResolutionError
from epidemic_core.normalization import CountryResolver, DiseaseResolver, normalize_alias, parse_date_range

COUNTRIES=[("CN","CN"),("CHN","CN"),("China","CN"),("中国","CN"),("中华人民共和国","CN"),("US","US"),("USA","US"),("United States","US"),("美国","US"),("BR","BR"),("BRA","BR"),("Brazil","BR"),("巴西","BR")]
DISEASES=[("dengue","dengue"),("Dengue Fever","dengue"),("登革热","dengue"),("influenza","influenza"),("flu","influenza"),("流感","influenza"),("COVID-19","covid-19"),("covid19","covid-19"),("新冠肺炎","covid-19"),("mpox","mpox"),("monkeypox","mpox"),("猴痘","mpox")]


@pytest.mark.parametrize(("raw","expected"),COUNTRIES)
def test_country_aliases(raw:str,expected:str)->None:assert CountryResolver(COUNTRIES).resolve(raw).alpha2==expected


@pytest.mark.parametrize(("raw","expected"),DISEASES)
def test_disease_aliases(raw:str,expected:str)->None:assert DiseaseResolver(DISEASES).resolve(raw).slug==expected


def test_unicode_space_casefold_and_hyphen()->None:
    assert normalize_alias("  COVID\u201119  ")=="covid-19"
    assert CountryResolver(COUNTRIES).resolve("\u3000china\u3000").alpha2=="CN"


def test_unknown_and_ambiguous()->None:
    with pytest.raises(ResolutionError,match="NOT_FOUND"):CountryResolver(COUNTRIES).resolve("Atlantis")
    with pytest.raises(ResolutionError,match="AMBIGUOUS"):CountryResolver(COUNTRIES+[("shared","CN"),("shared","US")]).resolve("shared")
    with pytest.raises(ResolutionError,match="AMBIGUOUS"):DiseaseResolver(DISEASES+[("shared","dengue"),("shared","mpox")]).resolve("shared")


def test_dates()->None:
    assert parse_date_range("2025-01-01","2025-01-01")== (dt.date(2025,1,1),dt.date(2025,1,1))
    assert parse_date_range("2025-01-01","2025-01-02")[1]==dt.date(2025,1,2)
    for a,b in (("2025-01-02","2025-01-01"),("2025/01/01","2025-01-02"),("01/02/2025","2025-02-01"),("2025-02-30","2025-03-01")):
        with pytest.raises(EpidemicCoreError):parse_date_range(a,b)


def test_strict_extra_forbidden()->None:
    with pytest.raises(ValidationError):EpidemicQueryInput(country="CN",disease="dengue",start_date="2025-01-01",end_date="2025-01-01",extra="x")


def test_fingerprint_determinism()->None:
    a=NormalizedEpidemicQuery(country_alpha2="CN",disease_slug="dengue",start_date=dt.date(2025,1,1),end_date=dt.date(2025,1,2))
    assert a.fingerprint()==a.fingerprint()
    assert a.fingerprint()!=a.model_copy(update={"country_alpha2":"US"}).fingerprint()
    assert a.fingerprint()!=a.model_copy(update={"disease_slug":"mpox"}).fingerprint()
    assert a.fingerprint()!=a.model_copy(update={"end_date":dt.date(2025,1,3)}).fingerprint()
