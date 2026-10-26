from __future__ import annotations

from source_discovery_agent.live_providers.data_gov_provider import DataGovLiveDiscoveryProvider
from source_discovery_agent.live_providers.gdelt_provider import GdeltLiveDiscoveryProvider
from source_discovery_agent.live_providers.mock_provider import MockLiveDiscoveryProvider
from source_discovery_agent.live_providers.official_directory_provider import OfficialDirectoryLiveDiscoveryProvider
from source_discovery_agent.live_providers.types import LiveDiscoveryProvider


_PROVIDERS: dict[str, LiveDiscoveryProvider] = {
    "mock": MockLiveDiscoveryProvider(),
    "gdelt": GdeltLiveDiscoveryProvider(),
    "data_gov": DataGovLiveDiscoveryProvider(),
    "official_directory": OfficialDirectoryLiveDiscoveryProvider(),
}


def get_live_discovery_provider(name: str) -> LiveDiscoveryProvider:
    normalized = str(name or "").strip().lower()
    try:
        return _PROVIDERS[normalized]
    except KeyError as exc:
        raise ValueError(f"Unknown live discovery provider: {name}.") from exc


def list_live_discovery_providers() -> list[str]:
    return sorted(_PROVIDERS)
