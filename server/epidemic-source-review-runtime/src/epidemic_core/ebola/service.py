from __future__ import annotations

# ruff: noqa: E501
import datetime as dt
import hashlib
import json
import mimetypes
import urllib.parse
import uuid
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Engine, text

from epidemic_core.ebola import ADAPTER_VERSION, PARSER_VERSION
from epidemic_core.ebola.fetcher import FetchResult, OfficialSourceFetcher
from epidemic_core.ebola.parsers import ParsedReport, parse_uganda_dashboard, parse_who_afro_pdf, parse_who_don

OUTBREAK_ID = uuid.uuid5(uuid.NAMESPACE_URL, "epidemic-openclaw:p1-11:bvd-2026")
WHO_DONS = (603, 605, 606, 607, 608, 612, 613)
WHO_URLS = tuple(f"https://www.who.int/emergencies/disease-outbreak-news/item/2026-DON{number}" for number in WHO_DONS)
UGANDA_URL = "https://evd-daily.health.go.ug/"
AFRO_PAGES = (
    "https://www.afro.who.int/countries/uganda/publication/ebola-bundibugyo-virus-disease-outbreak-democratic-republic-congo-1",
    "https://www.afro.who.int/countries/uganda/publication/ebola-bundibugyo-virus-disease-outbreak-democratic-republic-congo-2",
    "https://www.afro.who.int/countries/uganda/publication/ebola-bundibugyo-virus-disease-outbreak-democratic-republic-congo-3",
    "https://www.afro.who.int/countries/democratic-republic-of-congo/publication/ebola-bundibugyo-virus-disease-outbreak-1",
    "https://www.afro.who.int/countries/democratic-republic-of-congo/publication/ebola-bundibugyo-virus-disease-outbreak-2",
)
ALLOWED_HOSTS = {"www.who.int", "evd-daily.health.go.ug", "www.afro.who.int", "iris.who.int"}
SOURCE_DEFINITIONS = (
    {
        "key": "who-don-bvd-2026",
        "name": "WHO Disease Outbreak News: Bundibugyo virus disease 2026",
        "publisher": "World Health Organization",
        "domain": "www.who.int",
        "family": "WHO_DON",
        "url": "https://www.who.int/emergencies/emergency-events/item/2026-e000253",
        "terms_url": "https://www.who.int/about/policies/publishing/copyright",
        "active": True,
    },
    {
        "key": "uganda-moh-ebola-daily",
        "name": "Uganda Ministry of Health Ebola Daily Updates",
        "publisher": "Uganda Ministry of Health",
        "domain": "evd-daily.health.go.ug",
        "family": "UGANDA_MOH_DAILY",
        "url": UGANDA_URL,
        "terms_url": None,
        "active": True,
    },
    {
        "key": "who-afro-ebola-sitrep-fallback",
        "name": "WHO AFRO Bundibugyo Virus Disease Weekly External Situation Reports",
        "publisher": "WHO Regional Office for Africa",
        "domain": "www.afro.who.int",
        "family": "WHO_AFRO_SITREP",
        "url": AFRO_PAGES[-1],
        "terms_url": "https://www.afro.who.int/copyright",
        "active": True,
    },
    {
        "key": "drc-insp-ebola-sitrep",
        "name": "DRC INSP Ebola SitRep listing",
        "publisher": "Institut National de Santé Publique, DRC",
        "domain": "insp.cd",
        "family": "DRC_INSP_SITREP",
        "url": "https://insp.cd/category/sitrep/",
        "terms_url": None,
        "active": False,
    },
)


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def digest(value: object) -> str:
    payload = value if isinstance(value, bytes) else canonical_json(value).encode()
    return hashlib.sha256(payload).hexdigest()


def stable_uuid(kind: str, value: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"epidemic-openclaw:p1-11:{kind}:{value}")


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            for name, value in attrs:
                if name.lower() == "href" and value:
                    self.links.append(value)


def iris_pdf_url(page: FetchResult) -> str:
    parser = _LinkParser()
    parser.feed(page.payload.decode("utf-8", errors="replace"))
    for link in parser.links:
        url = urllib.parse.urljoin(page.final_url, link)
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme == "https" and parsed.hostname == "iris.who.int" and "/bitstreams/" in parsed.path:
            return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))
    raise ValueError("WHO_AFRO_PDF_LINK_MISSING")


class EbolaMonitoringService:
    def __init__(self, engine: Engine, project_root: Path) -> None:
        self.engine = engine
        self.project_root = project_root
        self.raw_root = project_root / "storage" / "ebola" / "raw" / "sha256"
        self.quarantine_root = project_root / "storage" / "quarantine" / "sha256"
        self.fetcher = OfficialSourceFetcher(ALLOWED_HOSTS)

    def seed(self) -> None:
        security = {
            "tls_verification": True,
            "redirect_hop_validation": True,
            "dns_public_only": True,
            "robots_aware": True,
            "max_bytes": 25165824,
            "active_content_execution": False,
        }
        now = dt.datetime.now(dt.timezone.utc)
        zone = ZoneInfo("Asia/Shanghai")
        local = now.astimezone(zone)
        next_local = dt.datetime.combine(local.date(), dt.time(2, 30), zone)
        if next_local <= local:
            next_local += dt.timedelta(days=1)
        with self.engine.begin() as connection:
            for code, name, species, aliases in (
                ("BDBV", "Bundibugyo virus", "Orthoebolavirus bundibugyoense", ["Bundibugyo ebolavirus"]),
                ("SUDV", "Sudan virus", "Orthoebolavirus sudanense", ["Sudan ebolavirus"]),
                ("EBOV", "Ebola virus", "Orthoebolavirus zairense", ["Zaire ebolavirus"]),
            ):
                connection.execute(
                    text(
                        "INSERT INTO epidemic_ebola.pathogen_variants(code,name,virus_species,aliases) VALUES(:code,:name,:species,CAST(:aliases AS jsonb)) ON CONFLICT(code) DO NOTHING"
                    ),
                    {"code": code, "name": name, "species": species, "aliases": canonical_json(aliases)},
                )
            connection.execute(
                text(
                    "INSERT INTO epidemic_ebola.outbreaks(id,outbreak_key,title,virus_name,pathogen_variant_code,started_on,status,metadata) VALUES(:id,'bvd-drc-uganda-2026','Bundibugyo virus disease outbreak, DRC and Uganda, 2026','Bundibugyo virus','BDBV','2026-05-11','ACTIVE',CAST(:meta AS jsonb)) ON CONFLICT(outbreak_key) DO NOTHING"
                ),
                {"id": OUTBREAK_ID, "meta": canonical_json({"cross_border": True, "no_cross_source_sum": True})},
            )
            for country, role, local_transmission in (
                ("COD", "PRIMARY", True),
                ("UGA", "CROSS_BORDER", True),
                ("FRA", "IMPORTED", False),
                ("DEU", "IMPORTED", False),
            ):
                connection.execute(
                    text(
                        "INSERT INTO epidemic_ebola.outbreak_countries(outbreak_id,country_iso3,role,local_transmission) VALUES(:outbreak,:country,:role,:local) ON CONFLICT(outbreak_id,country_iso3) DO NOTHING"
                    ),
                    {"outbreak": OUTBREAK_ID, "country": country, "role": role, "local": local_transmission},
                )
            for source in SOURCE_DEFINITIONS:
                source_id = stable_uuid("source", str(source["key"]))
                connection.execute(
                    text(
                        "INSERT INTO epidemic_ebola.source_contracts(id,outbreak_id,source_key,source_name,publisher,official_domain,source_family,canonical_url,adapter_version,parser_version,terms_url,attribution_template,security_policy,active) VALUES(:id,:outbreak,:key,:name,:publisher,:domain,:family,:url,:adapter,:parser,:terms_url,:attribution,CAST(:security AS jsonb),:active) ON CONFLICT(source_key) DO NOTHING"
                    ),
                    {
                        "id": source_id,
                        "outbreak": OUTBREAK_ID,
                        **source,
                        "adapter": ADAPTER_VERSION,
                        "parser": PARSER_VERSION,
                        "attribution": f"Source: {source['publisher']} ({source['domain']})",
                        "security": canonical_json(security),
                    },
                )
                if source["active"]:
                    schedule_id = stable_uuid("schedule", str(source["key"]))
                    connection.execute(
                        text(
                            "INSERT INTO epidemic_ebola.monitor_schedules(id,source_id,next_due_at) VALUES(:id,:source,:due) ON CONFLICT(source_id) DO NOTHING"
                        ),
                        {"id": schedule_id, "source": source_id, "due": next_local.astimezone(dt.timezone.utc)},
                    )

    def _store_raw(self, result: FetchResult) -> str:
        suffix = mimetypes.guess_extension(result.mime_type) or ".bin"
        target = self.raw_root / result.sha256[:2] / f"{result.sha256}{suffix}"
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(result.payload)
        return str(target.relative_to(self.project_root))

    def _persist_report(
        self, source_key: str, result: FetchResult, report: ParsedReport
    ) -> tuple[uuid.UUID, bool, int]:
        source_id = stable_uuid("source", source_key)
        storage_key = self._store_raw(result)
        report_id = stable_uuid("report", f"{source_key}:{report.publication_key}:{result.sha256}")
        created = False
        with self.engine.begin() as connection:
            existing = connection.execute(
                text(
                    "SELECT id FROM epidemic_ebola.source_report_versions WHERE source_id=:source AND publication_key=:key AND content_sha256=:sha"
                ),
                {"source": source_id, "key": report.publication_key, "sha": result.sha256},
            ).scalar_one_or_none()
            if existing is not None:
                return uuid.UUID(str(existing)), False, 0
            prior = connection.execute(
                text(
                    "SELECT id FROM epidemic_ebola.source_report_versions WHERE source_id=:source AND publication_key=:key AND is_current ORDER BY created_at DESC LIMIT 1"
                ),
                {"source": source_id, "key": report.publication_key},
            ).scalar_one_or_none()
            if prior is not None:
                connection.execute(
                    text("UPDATE epidemic_ebola.source_report_versions SET is_current=false WHERE id=:id"),
                    {"id": prior},
                )
            connection.execute(
                text(
                    "INSERT INTO epidemic_ebola.source_report_versions(id,source_id,publication_key,publication_date,source_as_of,canonical_url_redacted,mime_type,artifact_sha256,artifact_storage_key,byte_size,adapter_version,parser_version,content_sha256,etag,last_modified,content_length,redirect_chain_sha256,extraction_status,quality_flags,supersedes_id) VALUES(:id,:source,:key,:published,:as_of,:url,:mime,:artifact,:storage,:bytes,:adapter,:parser,:content,:etag,:last_modified,:content_length,:redirects,'PARSED_WITH_WARNINGS',CAST(:flags AS jsonb),:prior)"
                ),
                {
                    "id": report_id,
                    "source": source_id,
                    "key": report.publication_key,
                    "published": report.publication_date,
                    "as_of": report.source_as_of,
                    "url": result.final_url,
                    "mime": result.mime_type,
                    "artifact": result.sha256,
                    "storage": storage_key,
                    "bytes": len(result.payload),
                    "adapter": ADAPTER_VERSION,
                    "parser": PARSER_VERSION,
                    "content": result.sha256,
                    "etag": result.etag,
                    "last_modified": result.last_modified,
                    "content_length": len(result.payload),
                    "redirects": digest(result.redirect_hashes),
                    "flags": canonical_json(report.quality_flags),
                    "prior": prior,
                },
            )
            for observation in report.observations:
                lineage = digest(
                    {
                        "report": str(report_id),
                        "country": observation.country_iso3,
                        "date": observation.source_as_of,
                        "metric": observation.metric_code,
                        "class": observation.case_classification,
                        "import": observation.import_classification,
                        "value": observation.value,
                    }
                )
                observation_id = stable_uuid("observation", lineage)
                source_row_set_sha = digest(
                    {
                        "publication_key": report.publication_key,
                        "country": observation.country_iso3,
                        "metric": observation.metric_code,
                        "classification": observation.case_classification,
                        "import": observation.import_classification,
                        "value": observation.value,
                    }
                )
                connection.execute(
                    text(
                        "INSERT INTO epidemic_ebola.source_report_observations(id,report_version_id,outbreak_event_id,pathogen_variant_code,country_iso3,reporting_country,exposure_country,diagnosis_country,treatment_country,source_as_of,metric_code,value_numeric,observation_semantics,case_classification,import_classification,local_transmission,geography_scope,quality_status,quality_flags,source_row_set_sha256,lineage_sha256) VALUES(:id,:report,:outbreak,'BDBV',:country,:reporting,:exposure,:diagnosis,:treatment,:as_of,:metric,:value,'source_as_of_cumulative',:classification,:import_classification,:local,:scope,'review_required',CAST(:flags AS jsonb),:row_sha,:lineage)"
                    ),
                    {
                        "id": observation_id,
                        "report": report_id,
                        "outbreak": OUTBREAK_ID,
                        "country": observation.country_iso3,
                        "reporting": observation.reporting_country or observation.country_iso3,
                        "exposure": observation.exposure_country,
                        "diagnosis": observation.diagnosis_country,
                        "treatment": observation.treatment_country,
                        "as_of": observation.source_as_of,
                        "metric": observation.metric_code,
                        "value": observation.value,
                        "classification": observation.case_classification,
                        "import_classification": observation.import_classification,
                        "local": observation.local_transmission,
                        "scope": observation.geography_scope,
                        "flags": canonical_json(observation.quality_flags + report.quality_flags),
                        "row_sha": source_row_set_sha,
                        "lineage": lineage,
                    },
                )
                if prior is not None:
                    previous = (
                        connection.execute(
                            text(
                                "SELECT o.id,o.value_numeric FROM epidemic_ebola.source_report_observations o WHERE o.report_version_id=:prior AND o.country_iso3=:country AND o.metric_code=:metric AND o.case_classification=:classification AND o.import_classification=:import_classification"
                            ),
                            {
                                "prior": prior,
                                "country": observation.country_iso3,
                                "metric": observation.metric_code,
                                "classification": observation.case_classification,
                                "import_classification": observation.import_classification,
                            },
                        )
                        .mappings()
                        .one_or_none()
                    )
                    if previous is not None and previous["value_numeric"] != observation.value:
                        revision = {
                            "source": source_key,
                            "publication_key": report.publication_key,
                            "metric": observation.metric_code,
                            "country": observation.country_iso3,
                            "previous": str(previous["value_numeric"]),
                            "revised": observation.value,
                            "artifact": result.sha256,
                        }
                        connection.execute(
                            text(
                                "INSERT INTO epidemic_ebola.source_report_revisions(id,source_id,logical_key,previous_observation_id,revised_observation_id,previous_value,revised_value,revision_kind,evidence_sha256) VALUES(:id,:source,:key,:previous,:revised,:previous_value,:revised_value,:kind,:sha)"
                            ),
                            {
                                "id": stable_uuid("revision", digest(revision)),
                                "source": source_id,
                                "key": f"{report.publication_key}:{observation.country_iso3}:{observation.metric_code}:{observation.case_classification}:{observation.import_classification}",
                                "previous": previous["id"],
                                "revised": observation_id,
                                "previous_value": previous["value_numeric"],
                                "revised_value": observation.value,
                                "kind": (
                                    "CUMULATIVE_REVISION"
                                    if observation.value is not None
                                    and previous["value_numeric"] is not None
                                    and observation.value < previous["value_numeric"]
                                    else "RETROSPECTIVE_REVISION"
                                ),
                                "sha": digest(revision),
                            },
                        )
            created = True
        return report_id, created, len(report.observations)

    def _ingest_who(self) -> tuple[int, int, bool]:
        reports = observations = 0
        changed = False
        for number, url in zip(WHO_DONS, WHO_URLS, strict=True):
            fetched = self.fetcher.fetch(url)
            report = parse_who_don(fetched.payload, f"2026-DON{number}")
            _id, created, count = self._persist_report("who-don-bvd-2026", fetched, report)
            reports += int(created)
            observations += count
            changed |= created
        return reports, observations, changed

    def _ingest_uganda(self) -> tuple[int, int, bool]:
        fetched = self.fetcher.fetch(UGANDA_URL)
        report = parse_uganda_dashboard(fetched.payload)
        _id, created, count = self._persist_report("uganda-moh-ebola-daily", fetched, report)
        return int(created), count, created

    def _ingest_afro(self) -> tuple[int, int, bool]:
        reports = observations = 0
        changed = False
        for index, url in enumerate(AFRO_PAGES, 5):
            landing = self.fetcher.fetch(url)
            pdf = self.fetcher.fetch(iris_pdf_url(landing))
            report = parse_who_afro_pdf(pdf.payload, f"WHO-AFRO-SITREP-{index:02d}")
            _id, created, count = self._persist_report("who-afro-ebola-sitrep-fallback", pdf, report)
            reports += int(created)
            observations += count
            changed |= created
        return reports, observations, changed

    def ingest_source(self, source_key: str) -> tuple[int, int, bool]:
        if source_key == "who-don-bvd-2026":
            return self._ingest_who()
        if source_key == "uganda-moh-ebola-daily":
            return self._ingest_uganda()
        if source_key == "who-afro-ebola-sitrep-fallback":
            return self._ingest_afro()
        raise ValueError("EBOLA_SOURCE_NOT_ACTIVE")

    def _series(self, source_key: str) -> list[dict[str, Any]]:
        with self.engine.connect() as connection:
            rows = (
                connection.execute(
                    text(
                        "SELECT o.country_iso3,o.reporting_country,o.exposure_country,o.diagnosis_country,o.treatment_country,o.source_as_of,o.metric_code,o.value_numeric,o.unit,o.case_classification,o.import_classification,o.local_transmission,o.geography_scope,o.quality_status,o.quality_flags,o.source_row_set_sha256,o.lineage_sha256,o.revision_number,o.provisional,v.artifact_sha256,v.publication_key,v.publication_date,v.fetched_at,v.canonical_url_redacted FROM epidemic_ebola.source_report_observations o JOIN epidemic_ebola.source_report_versions v ON v.id=o.report_version_id JOIN epidemic_ebola.source_contracts c ON c.id=v.source_id WHERE c.source_key=:source AND v.is_current ORDER BY o.source_as_of,o.country_iso3,o.metric_code,o.import_classification"
                    ),
                    {"source": source_key},
                )
                .mappings()
                .all()
            )
        return [
            {
                key: (str(value) if isinstance(value, (dt.date, dt.datetime, uuid.UUID)) else value)
                for key, value in row.items()
            }
            for row in rows
        ]

    def materialize_candidate(self, source_key: str) -> uuid.UUID:
        series = self._series(source_key)
        if not series:
            raise ValueError("EBOLA_SERIES_EMPTY")
        source = next(item for item in SOURCE_DEFINITIONS if item["key"] == source_key)
        payload = canonical_json(
            {"schema_version": "p1-11-ebola-series-v1", "source_key": source_key, "rows": series}
        ).encode()
        artifact_sha = hashlib.sha256(payload).hexdigest()
        target = self.quarantine_root / artifact_sha[:2] / artifact_sha
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(payload)
        request = {
            "task": "P1-11B",
            "source_key": source_key,
            "disease": "ebola-disease",
            "human_review_required": True,
        }
        request_sha = digest(request)
        run_id = stable_uuid("discovery-run", source_key)
        candidate_id = stable_uuid("candidate", source_key)
        with self.engine.begin() as connection:
            existing = connection.execute(
                text(
                    "SELECT candidate_version_id FROM epidemic_ebola.candidate_bindings WHERE series_artifact_sha256=:sha"
                ),
                {"sha": artifact_sha},
            ).scalar_one_or_none()
            if existing is not None:
                return uuid.UUID(str(existing))
            connection.execute(
                text(
                    "INSERT INTO epidemic_discovery.discovery_runs(id,state,request_json,request_sha256,policy_version,budget_json,completed_at,progress_json) VALUES(:id,'REVIEW_READY',CAST(:request AS jsonb),:sha,'p1-11-official-source-policy-v1','{}'::jsonb,now(),CAST(:progress AS jsonb)) ON CONFLICT(request_sha256) DO NOTHING"
                ),
                {
                    "id": run_id,
                    "request": canonical_json(request),
                    "sha": request_sha,
                    "progress": canonical_json({"official_sources": 1, "qualified": 1}),
                },
            )
            search_id = stable_uuid("search-result", source_key)
            result_sha = digest({"source": source_key, "url": source["url"]})
            connection.execute(
                text(
                    "INSERT INTO epidemic_discovery.source_discovery_search_results(id,discovery_run_id,provider_id,query_id,query_sha256,rank,title,snippet,original_url,canonical_url,canonical_url_sha256,domain,publisher,stable_dataset_id,source_tier,media_type,landing_page_url,result_sha256,observed_at,metadata,disposition) VALUES(:id,:run,'OFFICIAL_REGISTERED_SOURCE','p1-11-official-chain',:query,1,:title,'Official source contract verified by deterministic fetch',:url,:url,:url_sha,:domain,:publisher,:stable,'NATIONAL_HEALTH','application/json',:url,:result,now(),CAST(:metadata AS jsonb),'FETCH_SELECTED') ON CONFLICT(discovery_run_id,provider_id,query_id,rank,canonical_url_sha256) DO NOTHING"
                ),
                {
                    "id": search_id,
                    "run": run_id,
                    "query": digest({"source": source_key}),
                    "title": source["name"],
                    "url": source["url"],
                    "url_sha": digest(str(source["url"])),
                    "domain": source["domain"],
                    "publisher": source["publisher"],
                    "stable": source_key,
                    "result": result_sha,
                    "metadata": canonical_json({"no_generic_search": True, "official": True}),
                },
            )
            connection.execute(
                text(
                    "INSERT INTO epidemic_discovery.source_candidates(id,discovery_run_id,canonical_endpoint_sha256,publisher,dataset_name,source_tier,status,risk_flags,score,score_breakdown) VALUES(:id,:run,:endpoint,:publisher,:name,'NATIONAL_HEALTH','REVIEW_READY','[]'::jsonb,0.9900,CAST(:score AS jsonb)) ON CONFLICT(discovery_run_id,canonical_endpoint_sha256) DO NOTHING"
                ),
                {
                    "id": candidate_id,
                    "run": run_id,
                    "endpoint": digest(str(source["url"])),
                    "publisher": source["publisher"],
                    "name": source["name"],
                    "score": canonical_json({"official_domain": 1, "aggregate_only": 1}),
                },
            )
            version_no = int(
                connection.execute(
                    text(
                        "SELECT COALESCE(max(version_no),0)+1 FROM epidemic_discovery.source_candidate_versions WHERE candidate_id=:id"
                    ),
                    {"id": candidate_id},
                ).scalar_one()
            )
            version_id = stable_uuid("candidate-version", f"{source_key}:{artifact_sha}")
            metadata = {
                "source_key": source_key,
                "country_iso3": "UGA" if source_key.startswith("uganda") else "COD",
                "disease_code": "ebola-disease",
                "geographic_scope": "national_reported",
                "data_tier": "CANDIDATE_REVIEW",
                "cross_source_sum_allowed": False,
                "source_as_of_min": series[0]["source_as_of"],
                "source_as_of_max": series[-1]["source_as_of"],
            }
            connection.execute(
                text(
                    "INSERT INTO epidemic_discovery.source_candidate_versions(id,candidate_id,version_no,canonical_url_redacted,canonical_endpoint_sha256,publisher_evidence_sha256,terms_sha256,license_status,format,metadata,content_sha256) VALUES(:id,:candidate,:version,:url,:endpoint,:publisher_sha,:terms,'REVIEW_REQUIRED','JSON',CAST(:metadata AS jsonb),:content)"
                ),
                {
                    "id": version_id,
                    "candidate": candidate_id,
                    "version": version_no,
                    "url": source["url"],
                    "endpoint": digest(str(source["url"])),
                    "publisher_sha": digest({"publisher": source["publisher"], "domain": source["domain"]}),
                    "terms": digest({"domain": source["domain"], "status": "REVIEW_REQUIRED"}),
                    "metadata": canonical_json(metadata),
                    "content": artifact_sha,
                },
            )
            fetch_id = stable_uuid("candidate-fetch", f"{source_key}:{artifact_sha}")
            connection.execute(
                text(
                    "INSERT INTO epidemic_discovery.candidate_fetches(id,discovery_run_id,search_result_id,candidate_id,completed_at,final_url,final_url_sha256,domain,status,http_status,content_type,byte_size,elapsed_ms,artifact_sha256,redirect_sha256) VALUES(:id,:run,:search,:candidate,now(),:url,:url_sha,:domain,'REVIEW_READY',200,'application/json',:bytes,0,:artifact,'[]'::jsonb)"
                ),
                {
                    "id": fetch_id,
                    "run": run_id,
                    "search": search_id,
                    "candidate": candidate_id,
                    "url": source["url"],
                    "url_sha": digest(str(source["url"])),
                    "domain": source["domain"],
                    "bytes": len(payload),
                    "artifact": artifact_sha,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO epidemic_discovery.quarantine_artifacts(id,candidate_version_id,sha256,byte_size,mime_type,storage_key,security_status,security_report_sha256,retrieved_at,search_result_id,fetch_id) VALUES(:id,:version,:sha,:bytes,'application/json',:storage,'PASS',:security,now(),:search,:fetch)"
                ),
                {
                    "id": stable_uuid("quarantine", artifact_sha),
                    "version": version_id,
                    "sha": artifact_sha,
                    "bytes": len(payload),
                    "storage": str(target.relative_to(self.project_root)),
                    "security": digest(
                        {"aggregate_only": True, "prompt_injection": "not_executed", "pii": "aggregate_only"}
                    ),
                    "search": search_id,
                    "fetch": fetch_id,
                },
            )
            fields = list(series[0])
            schema_sha = digest({"fields": fields, "rows": len(series)})
            profile_id = stable_uuid("schema", schema_sha)
            dates = [dt.date.fromisoformat(str(row["source_as_of"])) for row in series]
            connection.execute(
                text(
                    "INSERT INTO epidemic_discovery.source_schema_profiles(id,candidate_version_id,schema_sha256,row_count,column_count,date_min,date_max,profile_json) VALUES(:id,:version,:sha,:rows,:columns,:date_min,:date_max,CAST(:profile AS jsonb))"
                ),
                {
                    "id": profile_id,
                    "version": version_id,
                    "sha": schema_sha,
                    "rows": len(series),
                    "columns": len(fields),
                    "date_min": min(dates),
                    "date_max": max(dates),
                    "profile": canonical_json({"aggregate_only": True, "fields": fields}),
                },
            )
            for ordinal, field in enumerate(fields, 1):
                connection.execute(
                    text(
                        "INSERT INTO epidemic_discovery.source_schema_columns(id,schema_profile_id,ordinal,source_name,inferred_type,example_value,null_count,warning_flags) VALUES(:id,:profile,:ordinal,:name,:type,:example,:nulls,'[]'::jsonb)"
                    ),
                    {
                        "id": stable_uuid("column", f"{schema_sha}:{field}"),
                        "profile": profile_id,
                        "ordinal": ordinal,
                        "name": field,
                        "type": "number" if field == "value_numeric" else "string",
                        "example": str(series[0].get(field, ""))[:200],
                        "nulls": sum(row.get(field) is None for row in series),
                    },
                )
            mapping = {
                "source_as_of": {"canonical_field": "source_as_of", "transform": "iso_date"},
                "country_iso3": {"canonical_field": "country_iso3", "transform": "identity"},
                "metric_code": {"canonical_field": "metric_code", "transform": "identity"},
                "value_numeric": {"canonical_field": "value_numeric", "transform": "integer_or_null"},
                "case_classification": {"canonical_field": "case_classification", "transform": "controlled_vocabulary"},
                "import_classification": {
                    "canonical_field": "import_classification",
                    "transform": "controlled_vocabulary",
                },
            }
            mapping_sha = digest(mapping)
            proposal_id = stable_uuid("mapping", f"{version_id}:{mapping_sha}")
            connection.execute(
                text(
                    "INSERT INTO epidemic_discovery.source_mapping_proposals(id,candidate_version_id,revision,mapping_sha256,proposal_json,status) VALUES(:id,:version,1,:sha,CAST(:mapping AS jsonb),'PROPOSED')"
                ),
                {"id": proposal_id, "version": version_id, "sha": mapping_sha, "mapping": canonical_json(mapping)},
            )
            for field, spec in mapping.items():
                connection.execute(
                    text(
                        "INSERT INTO epidemic_discovery.source_mapping_fields(id,mapping_proposal_id,source_field,canonical_field,metric_code,transform,confidence,warning) VALUES(:id,:proposal,:source,:canonical,NULL,:transform,1.0,NULL)"
                    ),
                    {
                        "id": stable_uuid("mapping-field", f"{proposal_id}:{field}"),
                        "proposal": proposal_id,
                        "source": field,
                        "canonical": spec["canonical_field"],
                        "transform": spec["transform"],
                    },
                )
            for ordinal, row in enumerate(series[:20], 1):
                row_sha = digest(row)
                connection.execute(
                    text(
                        "INSERT INTO epidemic_discovery.source_preview_rows(id,candidate_version_id,sample_kind,row_ordinal,raw_json,sanitized_json,row_errors,row_sha256) VALUES(:id,:version,'HEAD',:ordinal,CAST(:row AS jsonb),CAST(:row AS jsonb),'[]'::jsonb,:sha)"
                    ),
                    {
                        "id": stable_uuid("preview-row", f"{version_id}:{ordinal}"),
                        "version": version_id,
                        "ordinal": ordinal,
                        "row": canonical_json(row),
                        "sha": row_sha,
                    },
                )
            quality = {
                "status": "REVIEW_READY",
                "aggregate_only": True,
                "license": "REVIEW_REQUIRED",
                "cross_source_sum_allowed": False,
                "rows": len(series),
            }
            connection.execute(
                text(
                    "INSERT INTO epidemic_discovery.source_quality_assessments(id,candidate_version_id,assessment_sha256,status,quality_json,security_json,pii_status,license_status) VALUES(:id,:version,:sha,'REVIEW_READY',CAST(:quality AS jsonb),CAST(:security AS jsonb),'AGGREGATE_ONLY','REVIEW_REQUIRED')"
                ),
                {
                    "id": stable_uuid("quality", f"{version_id}:{digest(quality)}"),
                    "version": version_id,
                    "sha": digest(quality),
                    "quality": canonical_json(quality),
                    "security": canonical_json(
                        {"prompt_injection": "data_only", "ssrf": "allowlisted", "person_level": False}
                    ),
                },
            )
            review_id = stable_uuid("review-case", str(version_id))
            etag = digest(
                {"version": str(version_id), "schema": schema_sha, "mapping": mapping_sha, "artifact": artifact_sha}
            )
            connection.execute(
                text(
                    "INSERT INTO epidemic_discovery.source_review_cases(id,candidate_version_id,state,current_mapping_revision,etag) VALUES(:id,:version,'REVIEW_READY',1,:etag)"
                ),
                {"id": review_id, "version": version_id, "etag": etag},
            )
            latest_report = connection.execute(
                text(
                    "SELECT v.id FROM epidemic_ebola.source_report_versions v JOIN epidemic_ebola.source_contracts c ON c.id=v.source_id WHERE c.source_key=:source AND v.is_current ORDER BY v.source_as_of DESC LIMIT 1"
                ),
                {"source": source_key},
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO epidemic_ebola.candidate_bindings(candidate_version_id,source_id,latest_report_version_id,series_artifact_sha256,mapping_contract_sha256,coverage_status,pii_status) VALUES(:version,:source,:report,:artifact,:mapping,'OFFICIAL_REPORTED_AGGREGATES_REVIEW_REQUIRED','AGGREGATE_ONLY')"
                ),
                {
                    "version": version_id,
                    "source": stable_uuid("source", source_key),
                    "report": latest_report,
                    "artifact": artifact_sha,
                    "mapping": mapping_sha,
                },
            )
        return version_id

    def initial_backfill(self) -> dict[str, Any]:
        self.seed()
        result: dict[str, Any] = {"sources": {}}
        for source_key in ("who-don-bvd-2026", "uganda-moh-ebola-daily", "who-afro-ebola-sitrep-fallback"):
            reports, observations, changed = self.ingest_source(source_key)
            version = self.materialize_candidate(source_key)
            result["sources"][source_key] = {
                "new_reports": reports,
                "new_observations": observations,
                "changed": changed,
                "candidate_version_id": str(version),
            }
        return result

    def run_due_once(self) -> bool:
        with self.engine.begin() as connection:
            row = (
                connection.execute(
                    text(
                        "SELECT s.id schedule_id,s.source_id,c.source_key FROM epidemic_ebola.monitor_schedules s JOIN epidemic_ebola.source_contracts c ON c.id=s.source_id WHERE s.active AND c.active AND s.next_due_at<=now() ORDER BY s.next_due_at FOR UPDATE OF s SKIP LOCKED LIMIT 1"
                    )
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                return False
            run_id = uuid.uuid4()
            connection.execute(
                text(
                    "INSERT INTO epidemic_ebola.monitor_runs(id,schedule_id,source_id,state) VALUES(:id,:schedule,:source,'FETCHING')"
                ),
                {"id": run_id, "schedule": row["schedule_id"], "source": row["source_id"]},
            )
        try:
            reports, observations, changed = self.ingest_source(str(row["source_key"]))
            if changed:
                self.materialize_candidate(str(row["source_key"]))
            state = "SUCCEEDED_CHANGED" if changed else "SUCCEEDED_NO_CHANGE"
            details: dict[str, object] = {
                "source_changed": changed,
                "new_reports": reports,
                "new_observations": observations,
            }
            with self.engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE epidemic_ebola.monitor_runs SET state=:state,finished_at=now(),source_changed=:changed,report_count=:reports,observation_count=:observations,evidence_sha256=:evidence,details=CAST(:details AS jsonb) WHERE id=:id"
                    ),
                    {
                        "state": state,
                        "changed": changed,
                        "reports": reports,
                        "observations": observations,
                        "evidence": digest(details),
                        "details": canonical_json(details),
                        "id": run_id,
                    },
                )
                connection.execute(
                    text(
                        "UPDATE epidemic_ebola.monitor_schedules SET last_checked_at=now(),last_success_at=now(),last_change_at=CASE WHEN :changed THEN now() ELSE last_change_at END,consecutive_failures=0,next_due_at=next_due_at+interval '1 day',state_version=state_version+1,updated_at=now() WHERE id=:id"
                    ),
                    {"changed": changed, "id": row["schedule_id"]},
                )
        except Exception as exc:
            code = type(exc).__name__.upper()
            details = {"error_code": code}
            with self.engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE epidemic_ebola.monitor_runs SET state='FAILED_RETRYABLE',finished_at=now(),failure_code=:code,evidence_sha256=:evidence,details=CAST(:details AS jsonb) WHERE id=:id"
                    ),
                    {"code": code, "evidence": digest(details), "details": canonical_json(details), "id": run_id},
                )
                connection.execute(
                    text(
                        "UPDATE epidemic_ebola.monitor_schedules SET last_checked_at=now(),consecutive_failures=consecutive_failures+1,next_due_at=now()+interval '1 hour',state_version=state_version+1,updated_at=now() WHERE id=:id"
                    ),
                    {"id": row["schedule_id"]},
                )
        return True
