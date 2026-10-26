from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from epidemic_core.db.models.tables import ReferenceDataset


class ContractError(RuntimeError):
    pass


@dataclass(frozen=True)
class Versions:
    transform: str
    country_mapping: str
    quality_ruleset: str
    snapshot_schema: int


@dataclass(frozen=True)
class CountryMapping:
    version: str
    artifact_sha256: str
    by_country: dict[str, str]
    unmapped_codes: frozenset[str]


@dataclass(frozen=True)
class Contract:
    versions: Versions
    expected_header: tuple[str, ...]
    metric_mapping: dict[str, str]
    source_key: str
    source_url: str
    source_page_url: str
    terms_url: str
    attribution: str
    disclaimer: str


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verified_json(session: Session, path: Path, dataset_key: str) -> dict[str, object]:
    row = session.get(ReferenceDataset, dataset_key)
    if row is None or file_sha256(path) != row.content_sha256:
        raise ContractError("REFERENCE_DATASET_INTEGRITY_ERROR")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError("REFERENCE_DATASET_INTEGRITY_ERROR") from exc
    if not isinstance(value, dict):
        raise ContractError("REFERENCE_DATASET_INTEGRITY_ERROR")
    return value


def load_contract(session: Session, reference_root: Path) -> tuple[Contract, CountryMapping]:
    raw_contract = _verified_json(
        session, reference_root / "who_covid19_normalization_contract.v1.json", "who_covid19_normalization_contract_v1"
    )
    raw_mapping = _verified_json(
        session, reference_root / "who_covid19_country_map.v1.json", "who_covid19_country_map_v1"
    )
    try:
        raw_snapshot_schema = raw_contract["snapshot_schema_version"]
        if not isinstance(raw_snapshot_schema, (int, str)):
            raise TypeError
        versions = Versions(
            transform=str(raw_contract["transform_version"]),
            country_mapping=str(raw_contract["country_mapping_version"]),
            quality_ruleset=str(raw_contract["quality_ruleset_version"]),
            snapshot_schema=int(raw_snapshot_schema),
        )
        raw_header = raw_contract["expected_header"]
        raw_metrics = raw_contract["metric_mapping"]
        raw_rows = raw_mapping["mappings"]
        if not isinstance(raw_header, list) or not isinstance(raw_metrics, dict) or not isinstance(raw_rows, list):
            raise TypeError
        expected_header = tuple(str(x) for x in raw_header)
        metric_mapping = {str(k): str(v) for k, v in raw_metrics.items()}
        rows = raw_rows
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractError("REFERENCE_DATASET_INTEGRITY_ERROR") from exc
    by_country: dict[str, str] = {}
    unmapped: set[str] = set()
    seen_who: set[str] = set()
    for item in rows:
        if not isinstance(item, dict):
            raise ContractError("REFERENCE_DATASET_INTEGRITY_ERROR")
        who = str(item.get("who_country_code", ""))
        target = item.get("country_alpha2")
        if not who or who in seen_who:
            raise ContractError("REFERENCE_DATASET_INTEGRITY_ERROR")
        seen_who.add(who)
        if target is None:
            unmapped.add(who)
            continue
        alpha2 = str(target)
        if alpha2 in by_country:
            raise ContractError("AMBIGUOUS_TARGET_COUNTRY")
        by_country[alpha2] = who
    if str(raw_mapping.get("mapping_version")) != versions.country_mapping:
        raise ContractError("REFERENCE_DATASET_INTEGRITY_ERROR")
    contract = Contract(
        versions=versions,
        expected_header=expected_header,
        metric_mapping=metric_mapping,
        source_key=str(raw_contract["source_key"]),
        source_url=str(raw_contract["source_url"]),
        source_page_url=str(raw_contract["source_page_url"]),
        terms_url=str(raw_contract["terms_url"]),
        attribution=str(raw_contract["attribution"]),
        disclaimer=str(raw_contract["endorsement_disclaimer"]),
    )
    mapping = CountryMapping(
        version=versions.country_mapping,
        artifact_sha256=str(raw_mapping["source_artifact_sha256"]),
        by_country=by_country,
        unmapped_codes=frozenset(unmapped),
    )
    return contract, mapping
