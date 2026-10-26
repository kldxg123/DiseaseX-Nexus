from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from epidemic_core.db.models.tables import DataSource, ReferenceDataset

WHO_DOWNLOAD_URL = "https://srhdpeuwpubsa.blob.core.windows.net/whdh/COVID/WHO-COVID-19-global-data.csv"
REGISTRY_KEY = "data_sources_v1"


class RegistryError(RuntimeError):
    pass


class NoCompatibleSource(RegistryError):
    pass


@dataclass(frozen=True)
class SourceSpec:
    source_key: str
    disease_slug: str
    adapter_name: str
    adapter_version: str
    source_page_url: str
    download_url: str
    terms_url: str
    license_expression: str
    attribution: str
    artifact_scope: str
    expected_header: str
    allowed_media_types: tuple[str, ...]
    maximum_bytes: int
    enabled: bool


def canonical_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def load_registry(path: Path) -> tuple[dict[str, Any], str, SourceSpec]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema_version") != 1 or len(value.get("sources", [])) != 1:
        raise RegistryError("REGISTRY_SCHEMA_INVALID")
    item = value["sources"][0]
    if item["download_url"] != WHO_DOWNLOAD_URL:
        raise RegistryError("REGISTRY_ADAPTER_URL_MISMATCH")
    endpoint = item["endpoint"]
    if endpoint != {"scheme": "https", "host": "srhdpeuwpubsa.blob.core.windows.net", "port": 443, "path": "/whdh/COVID/WHO-COVID-19-global-data.csv", "query": ""}:
        raise RegistryError("REGISTRY_ENDPOINT_INVALID")
    spec = SourceSpec(
        source_key=item["source_key"], disease_slug=item["disease_slug"], adapter_name=item["adapter_name"],
        adapter_version=item["adapter_version"], source_page_url=item["source_page_url"], download_url=item["download_url"],
        terms_url=item["terms_url"], license_expression=item["license_expression"], attribution=item["attribution"],
        artifact_scope=item["artifact_scope"], expected_header=item["expected_header"], allowed_media_types=tuple(item["allowed_media_types"]),
        maximum_bytes=item["maximum_bytes"], enabled=item["enabled"],
    )
    return value, hashlib.sha256(canonical_bytes(value)).hexdigest(), spec


def select_source(session: Session, spec: SourceSpec, disease_slug: str) -> DataSource:
    if disease_slug != spec.disease_slug:
        raise NoCompatibleSource("NO_COMPATIBLE_SOURCE")
    row = session.execute(select(DataSource).where(DataSource.source_key == spec.source_key)).scalar_one_or_none()
    if row is None or not row.enabled or row.adapter_name != spec.adapter_name or row.base_url != spec.download_url:
        raise RegistryError("SOURCE_REGISTRY_MISMATCH")
    return row


def import_registry(session: Session, path: Path) -> dict[str, str]:
    value, digest, spec = load_registry(path)
    dataset = session.get(ReferenceDataset, REGISTRY_KEY)
    metadata = {"schema_version": 1, "source_count": 1, "attribution": spec.attribution, "artifact_scope": spec.artifact_scope}
    if dataset is None:
        session.add(ReferenceDataset(dataset_key=REGISTRY_KEY, version="1", source_name="Project controlled source registry", source_url=spec.source_page_url, license_expression=spec.license_expression, content_sha256=digest, metadata_json=metadata))
    elif dataset.content_sha256 != digest:
        raise RegistryError("SOURCE_REGISTRY_HASH_MISMATCH")
    row = session.execute(select(DataSource).where(DataSource.source_key == spec.source_key)).scalar_one_or_none()
    if row is None:
        session.add(DataSource(source_key=spec.source_key, display_name=value["sources"][0]["display_name"], source_type="official_csv", authority="World Health Organization", base_url=spec.download_url, adapter_name=spec.adapter_name, license_expression=spec.license_expression, terms_url=spec.terms_url, enabled=True))
    elif row.base_url != spec.download_url or row.adapter_name != spec.adapter_name:
        raise RegistryError("SOURCE_REGISTRY_DATABASE_MISMATCH")
    return {"dataset_key": REGISTRY_KEY, "sha256": digest, "source_key": spec.source_key}
