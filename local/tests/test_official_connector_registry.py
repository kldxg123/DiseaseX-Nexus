from __future__ import annotations

import pytest

from crawler_agent import get_official_connector, list_official_connectors
from crawler_agent.connectors import CONNECTOR_SPECS, CONNECTORS, ConnectorDocument
from crawler_agent.official_connectors import (
    ConnectorDocument as LegacyConnectorDocument,
)
from crawler_agent.official_connectors import (
    get_official_connector as legacy_get_official_connector,
)


def test_connector_package_lists_stable_official_interfaces() -> None:
    names = [spec.name for spec in list_official_connectors()]

    assert names == [
        "cdc_norostat",
        "cdc_nors",
        "ecdc_erviss",
        "ecdc_surveillance_atlas",
        "who_disease_outbreak_news",
    ]
    assert set(names) == set(CONNECTORS)
    assert set(names) == set(CONNECTOR_SPECS)


def test_legacy_facade_uses_new_connector_document_type() -> None:
    assert LegacyConnectorDocument is ConnectorDocument
    assert legacy_get_official_connector("cdc_nors") is get_official_connector("cdc_nors")


def test_unknown_connector_raises_stable_value_error() -> None:
    with pytest.raises(ValueError, match="Unknown official connector"):
        get_official_connector("missing_connector")
