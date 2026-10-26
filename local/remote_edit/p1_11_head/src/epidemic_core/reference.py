from __future__ import annotations

import gettext
import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

import pycountry
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from epidemic_core.db.models.tables import (
    Country,
    CountryAlias,
    Disease,
    DiseaseAlias,
    MetricDefinition,
    ReferenceDataset,
)
from epidemic_core.normalization.text import normalize_alias

NAMESPACE = uuid.UUID("98d7ec7d-9e2f-4a91-8a42-b8f78d5cb7f6")
MANUAL = {
    "CN": [("中国", "zh", "manual"), ("中华人民共和国", "zh", "manual")],
    "US": [("美国", "zh", "manual"), ("美國", "zh-Hant", "manual")],
    "BR": [("巴西", "zh", "manual")],
}


def _upsert(session: Session, model: Any, values: dict[str, Any], keys: list[str]) -> None:
    stmt = insert(model).values(**values)
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=[getattr(model, k) for k in keys],
            set_={getattr(model, k): v for k, v in values.items() if k not in keys},
        )
    )


def _register_immutable_reference(session: Session, values: dict[str, Any]) -> None:
    row = session.get(ReferenceDataset, values["dataset_key"])
    if row is None:
        session.add(ReferenceDataset(**values))
        return
    if row.version != values["version"] or row.content_sha256 != values["content_sha256"]:
        raise ValueError("REFERENCE_DATASET_INTEGRITY_ERROR")


def seed(session: Session, root: Path) -> dict[str, Any]:
    countries = []
    translators = {
        lang: gettext.translation("iso3166-1", pycountry.LOCALES_DIR, languages=[lang], fallback=True)
        for lang in ("zh_CN", "zh_TW", "zh_HK")
    }
    for item in sorted(pycountry.countries, key=lambda x: x.alpha_2):
        row = {
            "alpha2": item.alpha_2,
            "alpha3": item.alpha_3,
            "numeric_code": item.numeric,
            "name": item.name,
            "official_name": getattr(item, "official_name", None),
        }
        countries.append(row)
    content = json.dumps(countries, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    digest = hashlib.sha256(content).hexdigest()
    key = "iso3166-pycountry-26.2.16"
    _upsert(
        session,
        ReferenceDataset,
        {
            "dataset_key": key,
            "version": "26.2.16",
            "source_name": "pycountry / Debian iso-codes snapshot",
            "source_url": "https://pypi.org/project/pycountry/26.2.16/",
            "license_expression": "LGPL-2.1-or-later",
            "content_sha256": digest,
            "metadata_json": {"semantics": "ISO 3166-1", "record_count": len(countries)},
        },
        ["dataset_key"],
    )
    aliases = 0
    for row in countries:
        _upsert(session, Country, {**row, "active": True, "reference_dataset_key": key}, ["alpha2"])
        values = [
            (row["alpha2"], "und", "alpha2"),
            (row["alpha3"], "und", "alpha3"),
            (row["numeric_code"], "und", "numeric"),
            (row["name"], "en", "name"),
        ]
        if row["official_name"]:
            values.append((row["official_name"], "en", "official_name"))
        for lang, tr in translators.items():
            values.append((tr.gettext(str(row["name"])), lang, "translation"))
        values.extend(MANUAL.get(str(row["alpha2"]), []))
        for alias, locale, kind in values:
            stmt = (
                insert(CountryAlias)
                .values(
                    country_alpha2=row["alpha2"],
                    alias=alias,
                    normalized_alias=normalize_alias(str(alias)),
                    locale=locale,
                    alias_type=kind,
                    reference_dataset_key=key,
                )
                .on_conflict_do_nothing(constraint="uq_country_alias_identity")
            )
            session.execute(stmt)
            aliases += 1
    disease_path = root / "reference/diseases.v1.json"
    metric_v1_path = root / "reference/metrics.v1.json"
    for path, dataset_key, source, license_exp in (
        (disease_path, "diseases-v1", "project disease catalog", "CC0-1.0"),
        (metric_v1_path, "metrics-v1", "project metric catalog", "CC0-1.0"),
    ):
        raw = path.read_bytes()
        _register_immutable_reference(
            session,
            {
                "dataset_key": dataset_key,
                "version": "1",
                "source_name": source,
                "source_url": "project://" + path.name,
                "license_expression": license_exp,
                "content_sha256": hashlib.sha256(raw).hexdigest(),
                "metadata_json": {},
            },
        )
    alembic_version = session.execute(text("SELECT version_num FROM epidemic.alembic_version")).scalar_one()
    metric_path = metric_v1_path
    active_metric_key = "metrics-v1"
    alembic_sequence = alembic_version.partition("_")[0]
    if alembic_sequence.isdigit() and int(alembic_sequence) >= 3:
        manifest_v1_path = root / "reference/active_catalogs.v1.json"
        manifest_path = root / "reference/active_catalogs.v2.json"
        metric_v2_path = root / "reference/metrics.v2.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        active = manifest.get("active_metrics", {})
        if (
            manifest.get("minimum_alembic") != "0003_p1_4_normalization_pipeline"
            or active.get("dataset_key") != "metrics-v2"
            or active.get("file_name") != metric_v2_path.name
            or active.get("sha256") != hashlib.sha256(metric_v2_path.read_bytes()).hexdigest()
        ):
            raise ValueError("ACTIVE_CONTRACT_CATALOG_MISMATCH")
        for path, dataset_key, version, source in (
            (metric_v2_path, "metrics-v2", "2", "project metric catalog"),
            (manifest_v1_path, "active-catalogs-v1", "p1-4r-v1", "project historical catalog manifest"),
            (manifest_path, "active-catalogs-v2", "p1-5-v1", "project active catalog manifest"),
        ):
            raw = path.read_bytes()
            _register_immutable_reference(
                session,
                {
                    "dataset_key": dataset_key,
                    "version": version,
                    "source_name": source,
                    "source_url": "project://" + path.name,
                    "license_expression": "CC0-1.0",
                    "content_sha256": hashlib.sha256(raw).hexdigest(),
                    "metadata_json": {"active_for_alembic": alembic_version},
                },
            )
        metric_path = metric_v2_path
        active_metric_key = "metrics-v2"
    diseases = json.loads(disease_path.read_text())
    for d in diseases:
        did = uuid.uuid5(NAMESPACE, d["slug"])
        _upsert(
            session,
            Disease,
            {
                "id": did,
                "slug": d["slug"],
                "canonical_name": d["canonical_name"],
                "description": d.get("description"),
                "ontology_system": None,
                "ontology_code": None,
                "ontology_version": None,
                "catalog_version": d["catalog_version"],
                "active": d["active"],
            },
            ["slug"],
        )
        for a in d["aliases"]:
            session.execute(
                insert(DiseaseAlias)
                .values(
                    disease_id=did,
                    alias=a["value"],
                    normalized_alias=normalize_alias(a["value"]),
                    language_tag=a["language_tag"],
                    alias_type=a["alias_type"],
                    catalog_version=d["catalog_version"],
                )
                .on_conflict_do_nothing(constraint="uq_disease_alias_identity")
            )
    metrics = json.loads(metric_path.read_text())
    for m in metrics:
        _upsert(session, MetricDefinition, m, ["metric_code"])
    return {
        "countries": len(countries),
        "country_alias_attempts": aliases,
        "diseases": len(diseases),
        "metrics": len(metrics),
        "country_content_sha256": digest,
        "disease_file_sha256": hashlib.sha256(disease_path.read_bytes()).hexdigest(),
        "metric_file_sha256": hashlib.sha256(metric_path.read_bytes()).hexdigest(),
        "active_metric_dataset_key": active_metric_key,
        "alembic_version": alembic_version,
    }
