from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from epidemic_core.db.models.tables import MetricDefinition, ReferenceDataset


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
    contract_version: str = "who-covid19-normalization-v1"
    metrics_version: str = "metrics-v1"
    contract_sha256: str = ""
    metrics_sha256: str = ""


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verified_json_value(session: Session, path: Path, dataset_key: str) -> object:
    row = session.get(ReferenceDataset, dataset_key)
    if row is None or file_sha256(path) != row.content_sha256:
        raise ContractError("REFERENCE_DATASET_INTEGRITY_ERROR")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError("REFERENCE_DATASET_INTEGRITY_ERROR") from exc
    return value


def _verified_json(session: Session, path: Path, dataset_key: str) -> dict[str, object]:
    value = _verified_json_value(session, path, dataset_key)
    if not isinstance(value, dict):
        raise ContractError("REFERENCE_DATASET_INTEGRITY_ERROR")
    return value


def load_contract(session: Session, reference_root: Path) -> tuple[Contract, CountryMapping]:
    p1_4_schema = session.execute(text("SELECT to_regclass('epidemic.normalization_runs')")).scalar_one()
    if p1_4_schema is None:
        raise ContractError("ACTIVE_CONTRACT_CATALOG_MISMATCH")
    manifest = _verified_json(session, reference_root / "active_catalogs.v2.json", "active-catalogs-v2")
    try:
        active_contract = manifest["active_covid_contract"]
        active_metrics = manifest["active_metrics"]
        if not isinstance(active_contract, dict) or not isinstance(active_metrics, dict):
            raise TypeError
        if (
            manifest["minimum_alembic"] != "0003_p1_4_normalization_pipeline"
            or active_contract["dataset_key"] != "who_covid19_normalization_contract_v2"
            or active_contract["version"] != "who-covid19-normalization-v2"
            or active_metrics["dataset_key"] != "metrics-v2"
            or active_metrics["version"] != "2"
        ):
            raise ValueError
        contract_name = str(active_contract["file_name"])
        metrics_name = str(active_metrics["file_name"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractError("ACTIVE_CONTRACT_CATALOG_MISMATCH") from exc
    raw_contract = _verified_json(session, reference_root / contract_name, "who_covid19_normalization_contract_v2")
    raw_metric_rows = _verified_json_value(session, reference_root / metrics_name, "metrics-v2")
    raw_mapping = _verified_json(
        session, reference_root / "who_covid19_country_map.v1.json", "who_covid19_country_map_v1"
    )
    if active_contract.get("sha256") != file_sha256(reference_root / contract_name) or active_metrics.get(
        "sha256"
    ) != file_sha256(reference_root / metrics_name):
        raise ContractError("ACTIVE_CONTRACT_CATALOG_MISMATCH")
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
        raw_metric_catalog = raw_contract["metric_catalog"]
        if not isinstance(raw_metric_catalog, dict) or not isinstance(raw_metric_rows, list):
            raise TypeError
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractError("REFERENCE_DATASET_INTEGRITY_ERROR") from exc
    metrics_by_code = {str(item.get("metric_code")): item for item in raw_metric_rows if isinstance(item, dict)}
    database_metrics = {
        item.metric_code: item
        for item in session.scalars(
            select(MetricDefinition).where(MetricDefinition.metric_code.in_(tuple(metric_mapping.values())))
        )
    }
    try:
        for source_field, metric_code in metric_mapping.items():
            contract_metric = raw_metric_catalog[source_field]
            seed_metric = metrics_by_code[metric_code]
            database_metric = database_metrics[metric_code]
            if contract_metric["metric_code"] != metric_code:
                raise ValueError
            expected = {
                "unit": str(contract_metric["unit"]),
                "cumulative": bool(contract_metric["cumulative"]),
                "allow_negative": bool(contract_metric["allow_negative"]),
                "min_value": None if contract_metric["min_value"] is None else str(contract_metric["min_value"]),
                "schema_version": int(contract_metric["schema_version"]),
            }
            seed = {
                "unit": str(seed_metric["unit"]),
                "cumulative": bool(seed_metric["cumulative"]),
                "allow_negative": bool(seed_metric["allow_negative"]),
                "min_value": None if seed_metric["min_value"] is None else str(seed_metric["min_value"]),
                "schema_version": int(seed_metric["schema_version"]),
            }
            database = {
                "unit": database_metric.unit,
                "cumulative": database_metric.cumulative,
                "allow_negative": database_metric.allow_negative,
                "min_value": None if database_metric.min_value is None else str(database_metric.min_value),
                "schema_version": database_metric.schema_version,
            }
            if expected != seed or expected != database:
                raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise ContractError("ACTIVE_CONTRACT_CATALOG_MISMATCH") from exc
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
        contract_version=str(raw_contract["contract_version"]),
        metrics_version=str(active_metrics["dataset_key"]),
        contract_sha256=file_sha256(reference_root / contract_name),
        metrics_sha256=file_sha256(reference_root / metrics_name),
    )
    mapping = CountryMapping(
        version=versions.country_mapping,
        artifact_sha256=str(raw_mapping["source_artifact_sha256"]),
        by_country=by_country,
        unmapped_codes=frozenset(unmapped),
    )
    return contract, mapping
