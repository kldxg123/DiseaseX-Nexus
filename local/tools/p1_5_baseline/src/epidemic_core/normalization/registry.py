from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session

from epidemic_core.db.models.tables import ReferenceDataset
from epidemic_core.normalization.contract import ContractError, file_sha256


def import_normalization_references(session: Session, reference_root: Path) -> dict[str, object]:
    specs = (
        (
            "who_covid19_country_map_v1",
            "who_covid19_country_map.v1.json",
            "who-covid19-country-map-v1",
            "WHO COVID-19 country code mapping",
            "https://data.who.int/dashboards/covid19/data",
        ),
        (
            "who_covid19_normalization_contract_v1",
            "who_covid19_normalization_contract.v1.json",
            "who-covid19-normalization-v1",
            "WHO COVID-19 normalization and quality contract",
            "https://data.who.int/dashboards/covid19/data",
        ),
        (
            "who_covid19_normalization_contract_v2",
            "who_covid19_normalization_contract.v2.json",
            "who-covid19-normalization-v2",
            "WHO COVID-19 normalization and quality contract",
            "https://data.who.int/dashboards/covid19/data",
        ),
    )
    results: list[dict[str, object]] = []
    for key, name, version, source_name, source_url in specs:
        path = reference_root / name
        value = json.loads(path.read_text(encoding="utf-8"))
        digest = file_sha256(path)
        metadata: dict[str, object] = {"schema_version": 1, "file_name": name}
        if key == "who_covid19_country_map_v1":
            mappings = value.get("mappings", [])
            metadata.update(
                {
                    "mapping_count": len(mappings),
                    "mapped_count": sum(x.get("mapping_status") == "mapped" for x in mappings),
                    "unmapped_codes": sorted(
                        x.get("who_country_code") for x in mappings if x.get("mapping_status") != "mapped"
                    ),
                    "evidence_artifact_sha256": value.get("source_artifact_sha256"),
                }
            )
        row = session.get(ReferenceDataset, key)
        if row is None:
            session.add(
                ReferenceDataset(
                    dataset_key=key,
                    version=version,
                    source_name=source_name,
                    source_url=source_url,
                    license_expression="CC-BY-4.0",
                    content_sha256=digest,
                    metadata_json=metadata,
                )
            )
        elif row.content_sha256 != digest or row.version != version:
            raise ContractError("REFERENCE_DATASET_INTEGRITY_ERROR")
        results.append({"dataset_key": key, "version": version, "sha256": digest, **metadata})
    return {"datasets": results}


def import_dengue_references(session: Session, reference_root: Path) -> dict[str, object]:
    specs = (
        ("who_dengue_xmart_contract_v1", "who_dengue_xmart_contract.v1.json", "who-dengue-xmart-contract-v1", "WHO dengue xMart source contract"),
        ("who_dengue_iso3_country_map_v1", "who_dengue_iso3_country_map.v1.json", "who-dengue-iso3-country-map-v1", "WHO dengue ISO3 mapping"),
        ("who_dengue_quality_rules_v1", "who_dengue_quality_rules.v1.json", "who-dengue-reported-period-quality-v1", "WHO dengue quality rules"),
    )
    results=[]
    for key,name,version,source_name in specs:
        path=reference_root/name;digest=file_sha256(path);value=json.loads(path.read_text(encoding="utf-8"));row=session.get(ReferenceDataset,key)
        if row is None:session.add(ReferenceDataset(dataset_key=key,version=version,source_name=source_name,source_url="https://worldhealthorg.shinyapps.io/dengue_global/",license_expression="LicenseRef-WHO-Data-Terms",content_sha256=digest,metadata_json={"schema_version":1,"file_name":name,"record_count":len(value.get("mappings",[])) if isinstance(value,dict) else None}))
        elif row.content_sha256!=digest or row.version!=version:raise ContractError("REFERENCE_DATASET_INTEGRITY_ERROR")
        results.append({"dataset_key":key,"version":version,"sha256":digest})
    return {"datasets":results}
