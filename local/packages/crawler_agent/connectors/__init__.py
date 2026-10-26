from __future__ import annotations

from crawler_agent.connectors.base import ConnectorDocument, ConnectorSpec, OfficialConnector
from crawler_agent.connectors.cdc import (
    CdcFluViewConnector,
    CdcFluViewCsvConnector,
    CdcFluViewHtmlTableConnector,
    CdcFluViewReportConnector,
    CdcNoroStatConnector,
    CdcNorsConnector,
)
from crawler_agent.connectors.ecdc import EcdcErvissConnector, EcdcSurveillanceAtlasConnector
from crawler_agent.connectors.registry import (
    CONNECTORS,
    CONNECTOR_SPECS,
    get_connector_spec,
    get_official_connector,
    list_official_connectors,
)
from crawler_agent.connectors.who import WhoDiseaseOutbreakNewsConnector

__all__ = [
    "CONNECTORS",
    "CONNECTOR_SPECS",
    "CdcNoroStatConnector",
    "CdcNorsConnector",
    "CdcFluViewConnector",
    "CdcFluViewCsvConnector",
    "CdcFluViewHtmlTableConnector",
    "CdcFluViewReportConnector",
    "ConnectorDocument",
    "ConnectorSpec",
    "EcdcErvissConnector",
    "EcdcSurveillanceAtlasConnector",
    "OfficialConnector",
    "WhoDiseaseOutbreakNewsConnector",
    "get_connector_spec",
    "get_official_connector",
    "list_official_connectors",
]
