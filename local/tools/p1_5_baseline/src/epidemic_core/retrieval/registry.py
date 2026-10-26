from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from epidemic_core.db.models.tables import DataSource, ReferenceDataset

COVID_URL = "https://srhdpeuwpubsa.blob.core.windows.net/whdh/COVID/WHO-COVID-19-global-data.csv"
DENGUE_URL = "https://xmart-api-public.who.int/ARBOV/V_DENGUE_GLOBAL_VALIDATED_PUBLIC"
REGISTRY_KEY = "data_sources_v2"


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
    expected_header: str | None
    allowed_media_types: tuple[str, ...]
    maximum_bytes: int
    enabled: bool
    source_type: str


def canonical_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def load_registry(path: Path) -> tuple[dict[str, Any], str, tuple[SourceSpec, ...]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    items = value.get("sources", [])
    if value.get("schema_version") != 2 or not isinstance(items, list) or len(items) != 2:
        raise RegistryError("REGISTRY_SCHEMA_INVALID")
    specs: list[SourceSpec] = []
    expected_endpoints = {
        "who_covid19_global_weekly": (COVID_URL, "srhdpeuwpubsa.blob.core.windows.net", "/whdh/COVID/WHO-COVID-19-global-data.csv"),
        "who_dengue_global_validated": (DENGUE_URL, "xmart-api-public.who.int", "/ARBOV/V_DENGUE_GLOBAL_VALIDATED_PUBLIC"),
    }
    for item in items:
        key = item.get("source_key")
        if key not in expected_endpoints:
            raise RegistryError("REGISTRY_ADAPTER_URL_MISMATCH")
        url, host, endpoint_path = expected_endpoints[key]
        if item.get("download_url") != url or item.get("endpoint") != {
            "scheme": "https", "host": host, "port": 443, "path": endpoint_path, "query": ""
        }:
            raise RegistryError("REGISTRY_ENDPOINT_INVALID")
        specs.append(SourceSpec(
            source_key=key, disease_slug=item["disease_slug"], adapter_name=item["adapter_name"],
            adapter_version=str(item["adapter_version"]), source_page_url=item["source_page_url"],
            download_url=url, terms_url=item["terms_url"], license_expression=item["license_expression"],
            attribution=item["attribution"], artifact_scope=item["artifact_scope"],
            expected_header=item.get("expected_header"), allowed_media_types=tuple(item["allowed_media_types"]),
            maximum_bytes=int(item["maximum_bytes"]), enabled=bool(item["enabled"]), source_type=item["source_type"],
        ))
    return value, hashlib.sha256(canonical_bytes(value)).hexdigest(), tuple(specs)


def spec_for_disease(specs: tuple[SourceSpec, ...], disease_slug: str) -> SourceSpec:
    matches = [x for x in specs if x.enabled and x.disease_slug == disease_slug]
    if len(matches) != 1:
        raise NoCompatibleSource("NO_COMPATIBLE_SOURCE")
    return matches[0]


def spec_for_source(specs: tuple[SourceSpec, ...], source_key: str) -> SourceSpec:
    matches = [x for x in specs if x.source_key == source_key]
    if len(matches) != 1:
        raise RegistryError("SOURCE_REGISTRY_MISMATCH")
    return matches[0]


def select_source(session: Session, specs: tuple[SourceSpec, ...], disease_slug: str) -> tuple[DataSource, SourceSpec]:
    spec = spec_for_disease(specs, disease_slug)
    row = session.scalar(select(DataSource).where(DataSource.source_key == spec.source_key))
    if row is None or not row.enabled or row.adapter_name != spec.adapter_name or row.base_url != spec.download_url:
        raise RegistryError("SOURCE_REGISTRY_MISMATCH")
    return row, spec


def import_registry(session: Session, path: Path) -> dict[str, object]:
    value, digest, specs = load_registry(path)
    dataset = session.get(ReferenceDataset, REGISTRY_KEY)
    if dataset is None:
        session.add(ReferenceDataset(dataset_key=REGISTRY_KEY, version="2", source_name="Project controlled source registry",
            source_url="project://data_sources.v2.json", license_expression="LicenseRef-Mixed-Source-Terms",
            content_sha256=digest, metadata_json={"schema_version": 2, "source_count": 2}))
    elif dataset.content_sha256 != digest or dataset.version != "2":
        raise RegistryError("SOURCE_REGISTRY_HASH_MISMATCH")
    for item, spec in zip(value["sources"], specs, strict=True):
        row = session.scalar(select(DataSource).where(DataSource.source_key == spec.source_key))
        if row is None:
            session.add(DataSource(source_key=spec.source_key, display_name=item["display_name"], source_type=spec.source_type,
                authority="World Health Organization", base_url=spec.download_url, adapter_name=spec.adapter_name,
                license_expression=spec.license_expression, terms_url=spec.terms_url, enabled=True))
        elif row.base_url != spec.download_url or row.adapter_name != spec.adapter_name or row.license_expression != spec.license_expression:
            raise RegistryError("SOURCE_REGISTRY_DATABASE_MISMATCH")
    return {"dataset_key": REGISTRY_KEY, "sha256": digest, "source_keys": [x.source_key for x in specs]}
