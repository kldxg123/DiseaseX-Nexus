from __future__ import annotations

from crawler_agent.connectors.base import ConnectorSpec, OfficialConnector
from crawler_agent.connectors.cdc import CdcFluViewConnector, CdcNoroStatConnector, CdcNorsConnector
from crawler_agent.connectors.ecdc import EcdcErvissConnector, EcdcSurveillanceAtlasConnector
from crawler_agent.connectors.who import WhoDiseaseOutbreakNewsConnector


CONNECTOR_SPECS: dict[str, ConnectorSpec] = {
    "who_disease_outbreak_news": ConnectorSpec(
        name="who_disease_outbreak_news",
        display_name="WHO Disease Outbreak News",
        base_url="https://www.who.int/emergencies/disease-outbreak-news",
        source_type="official_page",
        connector=WhoDiseaseOutbreakNewsConnector(),
    ),
    "ecdc_surveillance_atlas": ConnectorSpec(
        name="ecdc_surveillance_atlas",
        display_name="ECDC Surveillance Atlas",
        base_url="https://atlas.ecdc.europa.eu/public/index.aspx",
        source_type="official_dataset",
        connector=EcdcSurveillanceAtlasConnector(),
    ),
    "ecdc_erviss": ConnectorSpec(
        name="ecdc_erviss",
        display_name="ECDC ERVISS",
        base_url="https://erviss.org",
        source_type="official_dataset",
        connector=EcdcErvissConnector(),
    ),
    "cdc_norostat": ConnectorSpec(
        name="cdc_norostat",
        display_name="CDC NoroSTAT",
        base_url="https://www.cdc.gov/norovirus/php/reporting/norostat.html",
        source_type="official_page",
        connector=CdcNoroStatConnector(),
    ),
    "cdc_nors": ConnectorSpec(
        name="cdc_nors",
        display_name="CDC NORS",
        base_url="https://www.cdc.gov/nors/index.html",
        source_type="official_dataset",
        connector=CdcNorsConnector(),
    ),
    "cdc_fluview": ConnectorSpec(
        name="cdc_fluview",
        display_name="CDC FluView",
        base_url="https://www.cdc.gov/flu/weekly/fluviewinteractive.htm",
        source_type="official_dataset",
        connector=CdcFluViewConnector(),
    ),
}

CONNECTORS: dict[str, OfficialConnector] = {
    name: spec.connector for name, spec in CONNECTOR_SPECS.items()
}


def get_connector_spec(name: str) -> ConnectorSpec:
    try:
        return CONNECTOR_SPECS[name]
    except KeyError as exc:
        raise ValueError(f"Unknown official connector: {name}.") from exc


def get_official_connector(name: str) -> OfficialConnector:
    return get_connector_spec(name).connector


def list_official_connectors() -> list[ConnectorSpec]:
    return [CONNECTOR_SPECS[name] for name in sorted(CONNECTOR_SPECS)]
