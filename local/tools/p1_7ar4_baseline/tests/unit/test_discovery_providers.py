from __future__ import annotations

import json
import uuid
from pathlib import Path

from epidemic_core.discovery.providers import (
    BraveSearchProvider,
    DataCiteProvider,
    DirectoryRegistry,
    SearchRequest,
)
from epidemic_core.discovery.safe_fetcher import FetchBlocked, FetchResult
from epidemic_core.discovery.schemas import DiscoveryInput

ROOT = Path(__file__).parents[2]


class NeverFetcher:
    calls = 0

    def fetch(self, *_args, **_kwargs):
        self.calls += 1
        raise AssertionError("network must not be called")


class JsonFetcher:
    def __init__(self, value: dict):
        self.value = value
        self.calls = 0

    def fetch(self, _url: str, _headers=None) -> FetchResult:
        self.calls += 1
        payload = json.dumps(self.value).encode()
        return FetchResult(
            "https://api.datacite.org/dois?[controlled-query]",
            "0" * 64,
            payload,
            "application/vnd.api+json",
            (),
            "api.datacite.org",
            200,
            15,
            "1" * 64,
        )


class BlockedFetcher:
    def fetch(self, *_args, **_kwargs):
        raise FetchBlocked("RATE_LIMITED", http_status=429, network_attempted=True)


def request(tiers: tuple[str, ...] = ("NATIONAL_HEALTH",)) -> SearchRequest:
    return SearchRequest(
        "CHN",
        "CN",
        "China",
        "中国",
        "COVID-19",
        "新型冠状病毒感染",
        ("cases_new", "deaths_new", "incidence_rate"),
        "latest_rolling_year",
        tiers,
        ("zh", "en"),
        5,
    )


def registry() -> DirectoryRegistry:
    return DirectoryRegistry.load(ROOT / "config/source_discovery/directories.v1.json")


def test_missing_brave_configuration_has_no_fake_request() -> None:
    fetcher = NeverFetcher()
    provider = BraveSearchProvider(key="", fetcher=fetcher)  # type: ignore[arg-type]
    outcome = provider.search(request(), True)
    assert outcome.capability.health_status == "MISSING_CREDENTIAL"
    assert outcome.evidence.reason_code == "BRAVE_SEARCH_API_KEY_NOT_CONFIGURED"
    assert not outcome.evidence.network_request
    assert outcome.evidence.http_status is None and outcome.evidence.result_count == 0
    assert fetcher.calls == 0


def test_datacite_fixture_is_real_request_evidence_and_deduplicable() -> None:
    directory = next(item for item in registry().directories if item.directory_id == "datacite-public-api-v2")
    fetcher = JsonFetcher(
        {
            "data": [
                {
                    "id": "10.1234/example",
                    "attributes": {
                        "doi": "10.1234/example",
                        "publisher": "Example public-health repository",
                        "titles": [{"title": "China COVID-19 weekly dataset"}],
                        "descriptions": [{"description": "Weekly cases"}],
                    },
                },
                {
                    "id": "10.1234/example",
                    "attributes": {
                        "doi": "10.1234/example",
                        "publisher": "Example public-health repository",
                        "titles": [{"title": "Duplicate"}],
                    },
                },
            ]
        }
    )
    outcome = DataCiteProvider(directory, fetcher=fetcher).search(request(("PUBLIC_HEALTH_RESEARCH",)), True)  # type: ignore[arg-type]
    assert outcome.evidence.network_request and outcome.evidence.http_status == 200
    assert outcome.evidence.domain == "api.datacite.org"
    assert outcome.evidence.response_sha256 == "1" * 64
    assert len(outcome.results) == 2 and fetcher.calls == 1


def test_rate_limit_is_retryable_evidence_not_success() -> None:
    directory = next(item for item in registry().directories if item.directory_id == "datacite-public-api-v2")
    outcome = DataCiteProvider(directory, fetcher=BlockedFetcher()).search(  # type: ignore[arg-type]
        request(("PUBLIC_HEALTH_RESEARCH",)), True
    )
    assert outcome.evidence.network_request
    assert outcome.evidence.http_status == 429
    assert outcome.evidence.reason_code == "RATE_LIMITED"
    assert outcome.evidence.result == "FAILED"


def test_registry_does_not_count_protocol_as_directory() -> None:
    selected = registry()
    chn = selected.matching("CHN", ("NATIONAL_HEALTH", "GOVERNMENT_OPEN_DATA"))
    assert {item.directory_id for item in chn} == {
        "chn-national-health-commission-web-v1",
        "chn-government-open-data-web-v1",
    }
    assert all(item.protocol == "WEB_DIRECTORY" for item in chn)


def test_linked_retry_uuid_strings_validate_under_strict_model() -> None:
    source, nonce, disease = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    value = DiscoveryInput.model_validate(
        {
            "country_iso3": "CHN",
            "disease_id": str(disease),
            "desired_metrics": ["cases_new"],
            "time_window_kind": "latest_rolling_year",
            "preferred_source_tiers": ["NATIONAL_HEALTH"],
            "language_preferences": ["zh", "en"],
            "max_candidates": 5,
            "retry_of_run_id": str(source),
            "retry_nonce": str(nonce),
        }
    )
    assert value.retry_of_run_id == source and value.retry_nonce == nonce
