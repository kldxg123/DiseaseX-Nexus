from __future__ import annotations

import calendar
import datetime as dt
import decimal
import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from epidemic_core.db.models.tables import (
    Country,
    DatasetSnapshot,
    DatasetSnapshotObservation,
    DataSource,
    Disease,
    MetricDefinition,
    NormalizationRun,
    NormalizationRunEvent,
    NormalizedObservation,
    QualityAssessment,
    QueryRequest,
    RawArtifact,
    ReferenceDataset,
    RetrievalRun,
    RetrievalRunEvent,
    RetrievalWindowResolution,
    SourceFetch,
)
from epidemic_core.normalization.contract import ContractError, Versions, file_sha256
from epidemic_core.normalization.service import NormalizationError, sha


@dataclass(frozen=True)
class DengueContract:
    versions: Versions
    contract_version: str
    metric_mapping: dict[str, str]
    preserved: tuple[str, ...]
    terms_url: str
    attribution: str
    license_expression: str
    schema_sha256: str
    contract_sha256: str
    mapping_sha256: str
    quality_sha256: str


@dataclass(frozen=True)
class DengueObservation:
    metric_code: str
    value: decimal.Decimal
    raw_value: str
    period_start: dt.date
    period_end: dt.date
    granularity: str
    dimensions: dict[str, object]
    flags: tuple[str, ...]


@dataclass(frozen=True)
class DenguePlan:
    run_id: uuid.UUID
    retrieval_id: uuid.UUID
    fetch_id: uuid.UUID
    query_id: uuid.UUID
    country_alpha2: str
    disease_id: uuid.UUID
    source_key: str
    artifact_sha: str
    retrieved_at: dt.datetime
    contract: DengueContract
    observations: tuple[DengueObservation, ...]
    total: int
    selected: int
    missing: int
    all_null: int
    future_empty: int
    exact_duplicates: int
    date_types: tuple[str, ...]
    country_label: str
    request_meta: dict[str, object]


def _verified(session: Session, path: Path, key: str) -> object:
    row = session.get(ReferenceDataset, key)
    if row is None or row.content_sha256 != file_sha256(path):
        raise ContractError("REFERENCE_DATASET_INTEGRITY_ERROR")
    return json.loads(path.read_text(encoding="utf-8"))


def load_dengue_contract(session: Session, root: Path, country_alpha2: str) -> tuple[DengueContract, str, str]:
    manifest = _verified(session, root / "active_catalogs.v2.json", "active-catalogs-v2")
    contract = _verified(session, root / "who_dengue_xmart_contract.v1.json", "who_dengue_xmart_contract_v1")
    mapping = _verified(session, root / "who_dengue_iso3_country_map.v1.json", "who_dengue_iso3_country_map_v1")
    quality = _verified(session, root / "who_dengue_quality_rules.v1.json", "who_dengue_quality_rules_v1")
    if (
        not isinstance(manifest, dict)
        or manifest.get("active_metrics", {}).get("sha256")
        != "5f93cfe565021a9e60707b0d859cd37d45c8f2a822c084127471ff220c37036f"
        or manifest.get("active_covid_contract", {}).get("sha256")
        != "a8a14b5786eb0077aff4a29ff2beb6e1db8b8674a362031aa465c9fd83f4a5d1"
    ):
        raise ContractError("ACTIVE_CONTRACT_CATALOG_MISMATCH")
    if not isinstance(contract, dict) or not isinstance(mapping, dict) or not isinstance(quality, dict):
        raise ContractError("REFERENCE_DATASET_INTEGRITY_ERROR")
    country = session.get(Country, country_alpha2)
    rows = [x for x in mapping.get("mappings", []) if x.get("country_alpha3") == getattr(country, "alpha3", None)]
    if len(rows) != 1 or not re.fullmatch(r"[A-Z]{3}", rows[0].get("who_iso3", "")):
        raise ContractError("UNMAPPED_TARGET_COUNTRY")
    metric_mapping = contract.get("normalized_metric_mapping")
    if metric_mapping != {"CASES": "cases_new", "DEATHS": "deaths_new"}:
        raise ContractError("ACTIVE_CONTRACT_CATALOG_MISMATCH")
    metrics = {
        x.metric_code: x
        for x in session.scalars(
            select(MetricDefinition).where(MetricDefinition.metric_code.in_(["cases_new", "deaths_new"]))
        )
    }
    if len(metrics) != 2 or any(x.cumulative or not x.allow_negative or x.unit != "count" for x in metrics.values()):
        raise ContractError("ACTIVE_CONTRACT_CATALOG_MISMATCH")
    versions = Versions(
        "who-dengue-xmart-transform-v1", "who-dengue-iso3-country-map-v1", "who-dengue-reported-period-quality-v1", 1
    )
    return (
        DengueContract(
            versions,
            "who-dengue-xmart-contract-v1",
            metric_mapping,
            tuple(contract["preserved_raw_not_normalized"]),
            contract["terms_url"],
            contract["attribution"],
            contract["license_expression"],
            contract["schema_sha256"],
            file_sha256(root / "who_dengue_xmart_contract.v1.json"),
            file_sha256(root / "who_dengue_iso3_country_map.v1.json"),
            file_sha256(root / "who_dengue_quality_rules.v1.json"),
        ),
        rows[0]["who_iso3"],
        rows[0]["source_country_label"],
    )


def _period(row: dict[str, object]) -> tuple[dt.date, dt.date, str, bool, bool]:
    dtype = row.get("DATE_TYPE")
    year = row.get("YEAR")
    number = row.get("DATE_NUM")
    start_raw = row.get("START_DATE")
    derived = False
    mismatch = False
    if dtype not in {"month", "isoweek", "epiweek"}:
        raise NormalizationError("UNKNOWN_DATE_TYPE")
    if start_raw is not None:
        try:
            start = dt.date.fromisoformat(str(start_raw))
        except ValueError as exc:
            raise NormalizationError("UNRESOLVABLE_SOURCE_PERIOD") from exc
        if isinstance(year, int) and isinstance(number, int):
            expected = start.month if dtype == "month" else start.isocalendar().week if dtype == "isoweek" else None
            mismatch = expected is not None and (start.year != year or expected != number)
    elif dtype == "month" and isinstance(year, int) and isinstance(number, int) and 1 <= number <= 12:
        start = dt.date(year, number, 1)
        derived = True
    elif dtype == "isoweek" and isinstance(year, int) and isinstance(number, int):
        try:
            start = dt.date.fromisocalendar(year, number, 1)
        except ValueError as exc:
            raise NormalizationError("UNRESOLVABLE_SOURCE_PERIOD") from exc
        if start.isocalendar()[:2] != (year, number):
            raise NormalizationError("UNRESOLVABLE_SOURCE_PERIOD")
        derived = True
    elif dtype == "epiweek":
        raise NormalizationError("UNRESOLVABLE_SOURCE_PERIOD")
    else:
        raise NormalizationError("UNRESOLVABLE_SOURCE_PERIOD")
    if dtype == "month":
        end = dt.date(start.year, start.month, calendar.monthrange(start.year, start.month)[1])
        granularity = "month"
    else:
        end = start + dt.timedelta(days=6)
        granularity = "week"
    return start, end, granularity, derived, mismatch


def build_dengue_plan(session: Session, run_id: uuid.UUID, root: Path, raw_root: Path) -> DenguePlan:
    run = session.get(NormalizationRun, run_id)
    retrieval = session.get(RetrievalRun, run.retrieval_run_id) if run else None
    fetch = session.get(SourceFetch, run.source_fetch_id) if run else None
    if not run or not retrieval or not fetch or not fetch.artifact_id or not fetch.fetched_at:
        raise NormalizationError("ARTIFACT_INTEGRITY_ERROR")
    artifact = session.get(RawArtifact, fetch.artifact_id)
    query = session.get(QueryRequest, retrieval.query_request_id)
    source = session.get(DataSource, retrieval.source_id)
    disease = session.get(Disease, query.disease_id) if query else None
    if not artifact or not query or not source or not disease or source.source_key != "who_dengue_global_validated":
        raise NormalizationError("ARTIFACT_INTEGRITY_ERROR")
    contract, iso3, label = load_dengue_contract(session, root, query.country_alpha2)
    resolution = session.scalar(
        select(RetrievalWindowResolution).where(RetrievalWindowResolution.retrieval_run_id == retrieval.id)
    )
    query_start = resolution.resolved_start if resolution else query.start_date
    query_end = resolution.resolved_end if resolution else query.end_date
    if query_start is None or query_end is None:
        raise NormalizationError("WINDOW_RESOLUTION_MISSING")
    path = Path(artifact.storage_uri or "").resolve()
    base = raw_root.resolve()
    if (
        base not in path.parents
        or path.name != artifact.sha256 + ".json"
        or path.stat().st_size != artifact.byte_size
        or hashlib.sha256(path.read_bytes()).hexdigest() != artifact.sha256
    ):
        raise NormalizationError("ARTIFACT_INTEGRITY_ERROR")
    envelope = json.loads(path.read_text(encoding="utf-8"))
    rows = envelope["value"]
    seen: dict[tuple[object, ...], str] = {}
    prepared = []
    exact = 0
    missing = 0
    all_null = 0
    future_empty = 0
    types = set()
    today = fetch.fetched_at.date()
    for row in rows:
        start, end, granularity, derived, mismatch = _period(row)
        values = tuple(row.get(k) for k in ("CASES", "CONFIRMED_CASES", "SEVERE_CASES", "DEATHS"))
        key = (row.get("ISO3"), row.get("DATE_TYPE"), row.get("YEAR"), row.get("DATE_NUM"), start)
        canonical = json.dumps(row, sort_keys=True, separators=(",", ":"))
        if key in seen:
            if seen[key] != canonical:
                raise NormalizationError("CONFLICTING_SOURCE_ROWS")
            exact += 1
            continue
        seen[key] = canonical
        types.add(str(row["DATE_TYPE"]))
        allnull = all(x is None for x in values)
        if allnull:
            all_null += 1
            future_empty += int(start > today)
            continue
        if start > today:
            raise NormalizationError("FUTURE_DATED_NON_NULL_SOURCE_ROW")
        if not query_start < start <= query_end:
            continue
        missing += sum(x is None for x in values)
        prepared.append((row, start, end, granularity, derived, mismatch, values))
    mixed = len(types) > 1
    observations = []
    for row, start, end, granularity, derived, mismatch, values in prepared:
        common = set()
        if derived:
            common.add("SOURCE_PERIOD_DERIVED")
        if mismatch:
            common.add("SOURCE_DATE_METADATA_MISMATCH")
        if row.get("COUNTRY") != label:
            common.add("COUNTRY_LABEL_MISMATCH")
        if mixed:
            common.add("MIXED_TEMPORAL_GRANULARITY")
        if exact:
            common.add("EXACT_DUPLICATE_SOURCE_ROW")
        cases, confirmed, severe, deaths = values
        case_flags = set(common)
        death_flags = set(common)
        if cases is not None and cases < 0:
            case_flags.add("NEGATIVE_REPORTED_REVISION")
        if deaths is not None and deaths < 0:
            death_flags.add("NEGATIVE_REPORTED_REVISION")
        if cases is not None and confirmed is not None and confirmed > cases:
            case_flags.add("CONFIRMED_EXCEEDS_TOTAL_CASES")
        if cases is not None and severe is not None and severe > cases:
            case_flags.add("SEVERE_EXCEEDS_TOTAL_CASES")
        if cases is not None and deaths is not None and deaths > cases:
            death_flags.add("DEATHS_EXCEED_TOTAL_CASES")
        base_dims = {
            "reporting_basis": "reported_period",
            "source_date_type": row["DATE_TYPE"],
            "event_occurrence_time_known": False,
            "source_frequency": "variable",
        }
        if row["DATE_TYPE"] in ("epiweek", "isoweek"):
            base_dims["week_system"] = row["DATE_TYPE"]
        if cases is not None:
            observations.append(
                DengueObservation(
                    "cases_new",
                    decimal.Decimal(cases),
                    str(cases),
                    start,
                    end,
                    granularity,
                    {**base_dims, "metric_semantics": "total_reported_cases_including_suspected_and_confirmed"},
                    tuple(sorted(case_flags)),
                )
            )
        if deaths is not None:
            observations.append(
                DengueObservation(
                    "deaths_new",
                    decimal.Decimal(deaths),
                    str(deaths),
                    start,
                    end,
                    granularity,
                    {**base_dims, "metric_semantics": "reported_deaths_among_suspected_or_confirmed_cases"},
                    tuple(sorted(death_flags)),
                )
            )
    event = session.scalar(
        select(RetrievalRunEvent)
        .where(
            RetrievalRunEvent.retrieval_run_id == retrieval.id, RetrievalRunEvent.event_code == "RAW_ARTIFACT_STORED"
        )
        .order_by(RetrievalRunEvent.id.desc())
    )
    return DenguePlan(
        run.id,
        retrieval.id,
        fetch.id,
        query.id,
        query.country_alpha2,
        disease.id,
        source.source_key,
        artifact.sha256,
        fetch.fetched_at,
        contract,
        tuple(observations),
        len(rows),
        len(prepared),
        missing,
        all_null,
        future_empty,
        exact,
        tuple(sorted(types)),
        label,
        event.details if event else {},
    )


def finalize_dengue_plan(
    session: Session, run_id: uuid.UUID, token: uuid.UUID, plan: DenguePlan, now: dt.datetime
) -> uuid.UUID | None:
    run = session.scalar(select(NormalizationRun).where(NormalizationRun.id == run_id).with_for_update())
    if not run or run.status != "running" or run.claim_token != token:
        raise NormalizationError("STALE_CLAIM")
    rows = []
    warnings = 0
    for item in plan.observations:
        dims = item.dimensions
        series = {
            "source_key": plan.source_key,
            "country": plan.country_alpha2,
            "disease": "dengue",
            "metric": item.metric_code,
            "period_start": item.period_start.isoformat(),
            "period_end": item.period_end.isoformat(),
            "granularity": item.granularity,
            "dimensions": dims,
        }
        series_sha = sha(series)
        record_sha = sha({"series": series, "value": str(item.value)})
        lock = int(series_sha[:16], 16)
        lock = lock - (1 << 64) if lock >= 1 << 63 else lock
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock})
        obs = session.scalar(
            select(NormalizedObservation).where(NormalizedObservation.record_fingerprint == record_sha)
        )
        flags = set(item.flags)
        if obs is None:
            previous = session.scalar(
                select(NormalizedObservation)
                .where(NormalizedObservation.series_fingerprint == series_sha)
                .order_by(NormalizedObservation.revision.desc())
                .limit(1)
            )
            obs = NormalizedObservation(
                id=uuid.uuid4(),
                record_fingerprint=record_sha,
                series_fingerprint=series_sha,
                revision=1 if previous is None else previous.revision + 1,
                supersedes_observation_id=previous.id if previous else None,
                source_fetch_id=plan.fetch_id,
                country_alpha2=plan.country_alpha2,
                disease_id=plan.disease_id,
                metric_code=item.metric_code,
                value_numeric=item.value,
                raw_value=item.raw_value,
                period_start=item.period_start,
                period_end=item.period_end,
                granularity=item.granularity,
                reporting_date=item.period_start,
                as_of=plan.retrieved_at,
                dimensions=dims,
                dimensions_sha256=sha(dims),
                normalization_version=plan.contract.versions.transform,
            )
            session.add(obs)
            session.flush()
        quality = session.scalar(
            select(QualityAssessment).where(
                QualityAssessment.observation_id == obs.id,
                QualityAssessment.ruleset_version == plan.contract.versions.quality_ruleset,
            )
        )
        if quality is None:
            quality = QualityAssessment(
                id=uuid.uuid4(),
                observation_id=obs.id,
                ruleset_version=plan.contract.versions.quality_ruleset,
                status="accepted" if not flags else "quarantined",
                score=decimal.Decimal("1.0") if not flags else decimal.Decimal("0.5"),
                flags=sorted(flags),
                assessed_at=now,
            )
            session.add(quality)
        warnings += len(flags)
        rows.append(obs)
    snapshot_id = None
    if rows:
        window = session.scalar(
            select(RetrievalWindowResolution).where(RetrievalWindowResolution.retrieval_run_id == plan.retrieval_id)
        )
        ordered = sorted(
            rows,
            key=lambda x: (
                x.period_start,
                x.granularity,
                x.dimensions.get("source_date_type", ""),
                x.metric_code,
                x.series_fingerprint,
                x.revision,
                x.record_fingerprint,
            ),
        )
        feature = {
            "source_key": plan.source_key,
            "artifact_sha256": plan.artifact_sha,
            "accessed_at": plan.retrieved_at.isoformat(),
            "response_date": plan.request_meta.get("response_date"),
            "cache_control": plan.request_meta.get("cache_control"),
            "country": plan.country_alpha2,
            "data_provider_country": plan.country_label,
            "transform_version": plan.contract.versions.transform,
            "quality_version": plan.contract.versions.quality_ruleset,
            "mapping_version": plan.contract.versions.country_mapping,
            "contract_version": plan.contract.contract_version,
            "schema_sha256": plan.contract.schema_sha256,
            "metric_mapping": plan.contract.metric_mapping,
            "preserved_raw_not_normalized": list(plan.contract.preserved),
            "date_types": list(plan.date_types),
            "mixed_temporal_granularity": len(plan.date_types) > 1,
            "source_row_count": plan.total,
            "selected_source_rows": plan.selected,
            "missing_value_count": plan.missing,
            "all_null_source_row_count": plan.all_null,
            "future_empty_placeholder_count": plan.future_empty,
            "license_expression": plan.contract.license_expression,
            "terms_url": plan.contract.terms_url,
            "attribution": plan.contract.attribution,
            "no_who_endorsement": True,
            "no_public_redistribution": True,
            "caveats": [
                "surveillance sensitivity and case definitions vary",
                "underreporting and misdiagnosis may occur",
            ],
            "forecast_readiness_assessed": False,
            "prediction_performed": False,
            "time_window_kind": window.time_window_kind if window else "explicit_range",
            "resolved_start": window.resolved_start.isoformat() if window else None,
            "resolved_end": window.resolved_end.isoformat() if window else None,
            "source_data_through": window.source_data_through.isoformat() if window else None,
            "latest_window_contract_version": window.contract_version if window else None,
        }
        hash_rows = [
            {
                "metric": x.metric_code,
                "value": str(x.value_numeric),
                "period_start": x.period_start.isoformat(),
                "period_end": x.period_end.isoformat(),
                "granularity": x.granularity,
                "dimensions": x.dimensions,
                "revision": x.revision,
                "series": x.series_fingerprint,
                "record": x.record_fingerprint,
            }
            for x in ordered
        ]
        deterministic_feature = {
            k: v for k, v in feature.items() if k not in {"accessed_at", "response_date", "cache_control"}
        }
        content = sha({"feature_spec": deterministic_feature, "rows": hash_rows})
        snap = session.scalar(
            select(DatasetSnapshot).where(
                DatasetSnapshot.query_request_id == plan.query_id,
                DatasetSnapshot.snapshot_schema_version == 1,
                DatasetSnapshot.content_sha256 == content,
            )
        )
        if snap is None:
            snap = DatasetSnapshot(
                id=uuid.uuid4(),
                query_request_id=plan.query_id,
                snapshot_schema_version=1,
                cutoff_at=plan.retrieved_at,
                row_count=len(ordered),
                content_sha256=content,
                feature_spec=feature,
            )
            session.add(snap)
            session.flush()
            session.add_all(
                [
                    DatasetSnapshotObservation(snapshot_id=snap.id, observation_id=x.id, row_number=i)
                    for i, x in enumerate(ordered, 1)
                ]
            )
        snapshot_id = snap.id
    code = "NO_OBSERVATIONS" if not rows else "NORMALIZED"
    result = session.execute(
        update(NormalizationRun)
        .where(
            NormalizationRun.id == run_id, NormalizationRun.status == "running", NormalizationRun.claim_token == token
        )
        .values(
            status="succeeded",
            result_code=code,
            finished_at=now,
            claim_token=None,
            lease_expires_at=None,
            error_code=None,
            dataset_snapshot_id=snapshot_id,
            total_source_rows=plan.total,
            selected_source_rows=plan.selected,
            normalized_observation_count=len(rows),
            missing_value_count=plan.missing,
            warning_count=warnings,
            unmapped_source_code_count=0,
            exact_duplicate_count=plan.exact_duplicates,
        )
    )
    if getattr(result, "rowcount", 0) != 1:
        raise NormalizationError("STALE_CLAIM")
    session.add(
        NormalizationRunEvent(
            normalization_run_id=run_id,
            from_status="running",
            to_status="succeeded",
            event_code=code,
            details={
                "all_null_source_row_count": plan.all_null,
                "future_empty_placeholder_count": plan.future_empty,
                "date_types": list(plan.date_types),
                "mixed_temporal_granularity": len(plan.date_types) > 1,
            },
        )
    )
    return snapshot_id
