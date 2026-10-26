from __future__ import annotations

from crawler_agent.connector_ingest import store_connector_documents
from crawler_agent.connectors import (
    CONNECTORS,
    CONNECTOR_SPECS,
    CdcNoroStatConnector,
    CdcNorsConnector,
    CdcFluViewConnector,
    CdcFluViewCsvConnector,
    CdcFluViewHtmlTableConnector,
    CdcFluViewReportConnector,
    ConnectorDocument,
    ConnectorSpec,
    EcdcErvissConnector,
    EcdcSurveillanceAtlasConnector,
    OfficialConnector,
    WhoDiseaseOutbreakNewsConnector,
    get_connector_spec,
    get_official_connector,
    list_official_connectors,
)

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
    "store_connector_documents",
]
