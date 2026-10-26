from __future__ import annotations

import datetime as dt
import logging
import signal
import time
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from epidemic_core.db.models.tables import Country, DataSource, QueryRequest, RawArtifact, RetrievalRun, SourceFetch
from epidemic_core.normalization.contract import load_contract
from epidemic_core.normalization.dengue import load_dengue_contract
from epidemic_core.normalization.service import NormalizationService
from epidemic_core.normalization.worker import NormalizationWorker
from epidemic_core.retrieval.http import Attempt, DownloadError, DownloadResult, WhoDownloader
from epidemic_core.retrieval.dengue import DengueDownloader, DengueResult
from epidemic_core.retrieval.registry import load_registry, spec_for_source
from epidemic_core.retrieval.service import RetrievalService

log = logging.getLogger("retrieval_worker")


class RetrievalWorker:
    def __init__(self, factory: sessionmaker[Session], registry_path: Path, raw_root: Path):
        self.factory = factory
        self.registry_path = registry_path
        self.raw_root = raw_root
        self.stop_requested = False
        self.normalizer = NormalizationWorker(factory, registry_path.parent, raw_root)

    def recover(self) -> int:
        with self.factory() as session, session.begin():
            count = RetrievalService.reconcile_expired(session, dt.datetime.now(dt.timezone.utc))
        return count + self.normalizer.recover()

    def run_once(self) -> bool:
        now = dt.datetime.now(dt.timezone.utc)
        with self.factory() as session, session.begin():
            claimed = RetrievalService.claim(session, now)
            if claimed is None:
                return self.normalizer.run_once()
            run, token = claimed
            run_id = run.id
            source_id = run.source_id
        _, _, specs = load_registry(self.registry_path)
        with self.factory() as session:
            source_row = session.get(DataSource, source_id)
            run_row = session.get(RetrievalRun, run_id)
            query = session.get(QueryRequest, run_row.query_request_id) if run_row else None
            country = session.get(Country, query.country_alpha2) if query else None
            if source_row is None or query is None or country is None:
                raise RuntimeError("PROTOCOL_INCONSISTENCY")
            spec = spec_for_source(specs, source_row.source_key)
            dengue = spec.source_key == "who_dengue_global_validated"
        attempts: list[Attempt] = []

        def record(attempt: Attempt) -> None:
            attempts.append(attempt)

        def add_fetches(session: Session, artifact_id: uuid.UUID | None = None) -> SourceFetch | None:
            last: SourceFetch | None = None
            for index, attempt in enumerate(attempts):
                last = SourceFetch(
                    id=uuid.uuid4(),
                    retrieval_run_id=run_id,
                    source_id=source_id,
                    canonical_url=spec.download_url,
                    requested_at=dt.datetime.now(dt.timezone.utc),
                    fetched_at=dt.datetime.now(dt.timezone.utc) if attempt.http_status else None,
                    http_status=attempt.http_status,
                    etag=attempt.etag,
                    last_modified=attempt.last_modified,
                    artifact_id=artifact_id if index == len(attempts) - 1 else None,
                    attempt_number=attempt.number,
                    error_code=attempt.error_code,
                    error_message=None,
                    response_content_type=attempt.content_type,
                    response_byte_size=attempt.byte_size,
                )
                session.add(last)
            session.flush()
            return last

        stage = "conditional_lookup"
        try:
            with self.factory() as session:
                previous = (
                    session.execute(
                        select(SourceFetch)
                        .join(RetrievalRun, RetrievalRun.id == SourceFetch.retrieval_run_id)
                        .where(
                            SourceFetch.source_id == source_id,
                            RetrievalRun.status == "succeeded",
                            SourceFetch.artifact_id.is_not(None),
                        )
                        .order_by(SourceFetch.fetched_at.desc())
                    )
                    .scalars()
                    .first()
                )
                etag = previous.etag if previous and not dengue else None
                modified = previous.last_modified if previous and not dengue else None
                previous_artifact = previous.artifact_id if previous and not dengue else None
            stage = "download"
            if dengue:
                dengue_result = DengueDownloader(spec, self.raw_root).fetch(country.alpha3, query.start_date, query.end_date)
                result: DengueResult | DownloadResult = dengue_result
                record(Attempt(result.attempt_number,result.http_status,None,result.etag,result.last_modified,result.media_type,result.byte_size))
                source_metadata={"server_filter_applied":True,"odata_count":dengue_result.odata_count,"returned_row_count":dengue_result.returned_rows,"date_types":list(dengue_result.date_types),"request_fingerprint":dengue_result.request_fingerprint,"response_date":dengue_result.response_date,"cache_control":dengue_result.cache_control,"odata_context":dengue_result.odata_context}
            else:
                result = WhoDownloader(spec, self.raw_root).fetch(etag=etag, last_modified=modified, on_attempt=record)
                source_metadata={}
            if result.not_modified and previous_artifact is None:
                raise DownloadError("PROTOCOL_INCONSISTENCY")
            stage = "finalize"
            with self.factory() as session, session.begin():
                artifact_id = previous_artifact
                if not result.not_modified:
                    artifact = session.execute(
                        select(RawArtifact).where(RawArtifact.sha256 == result.sha256)
                    ).scalar_one_or_none()
                    if artifact is None:
                        artifact = RawArtifact(
                            id=uuid.uuid4(),
                            sha256=result.sha256,
                            byte_size=result.byte_size,
                            media_type=result.media_type,
                            encoding=None,
                            storage_mode="file",
                            storage_uri=str(result.path),
                            inline_text=None,
                            inline_json=None,
                        )
                        session.add(artifact)
                        session.flush()
                    artifact_id = artifact.id
                last_fetch = add_fetches(session, artifact_id)
                if last_fetch is None or artifact_id is None:
                    raise RuntimeError("PROTOCOL_INCONSISTENCY")
                if not RetrievalService.finish(
                    session, run_id, token, "succeeded", None, dt.datetime.now(dt.timezone.utc)
                ):
                    raise RuntimeError("STALE_CLAIM")
                session.add(
                    __import__("epidemic_core.db.models.tables", fromlist=["RetrievalRunEvent"]).RetrievalRunEvent(
                        retrieval_run_id=run_id,
                        from_status="succeeded",
                        to_status="succeeded",
                        event_code="SOURCE_NOT_MODIFIED" if result.not_modified else "RAW_ARTIFACT_STORED",
                        details={
                            "not_modified": result.not_modified,
                            "artifact_scope": spec.artifact_scope,
                            "query_filter_applied": dengue,
                            **source_metadata,
                        },
                    )
                )
                retrieval_row = session.get(RetrievalRun, run_id)
                artifact_row = session.get(RawArtifact, artifact_id)
                if retrieval_row is None or artifact_row is None:
                    raise RuntimeError("PROTOCOL_INCONSISTENCY")
                if dengue:
                    dengue_contract, _iso3, _label = load_dengue_contract(session, self.registry_path.parent, query.country_alpha2)
                    versions=dengue_contract.versions
                else:
                    covid_contract, _mapping = load_contract(session, self.registry_path.parent)
                    versions=covid_contract.versions
                NormalizationService.enqueue(session, retrieval_row, last_fetch, artifact_row.sha256, versions)
            log.info(
                "run_id=%s source_key=%s status=succeeded bytes=%s sha256=%s",
                run_id,
                spec.source_key,
                result.byte_size,
                result.sha256,
            )
        except DownloadError as exc:
            with self.factory() as session, session.begin():
                add_fetches(session)
                RetrievalService.finish(session, run_id, token, "failed", exc.code, dt.datetime.now(dt.timezone.utc))
            log.info(
                "run_id=%s source_key=%s status=failed error_code=%s http_status=%s",
                run_id,
                spec.source_key,
                exc.code,
                exc.status,
            )
        except SQLAlchemyError:
            with self.factory() as session, session.begin():
                RetrievalService.finish(
                    session, run_id, token, "failed", "DATABASE_WRITE_FAILED", dt.datetime.now(dt.timezone.utc)
                )
            log.info("run_id=%s source_key=%s status=failed error_code=DATABASE_WRITE_FAILED", run_id, spec.source_key)
        except RuntimeError as exc:
            code = (
                "STALE_CLAIM"
                if str(exc) == "STALE_CLAIM"
                else (
                    "DOWNLOAD_RUNTIME_ERROR"
                    if stage == "download"
                    else "FINALIZATION_RUNTIME_ERROR"
                    if stage == "finalize"
                    else "INTERNAL_RUNTIME_ERROR"
                )
            )
            with self.factory() as session, session.begin():
                add_fetches(session)
                RetrievalService.finish(session, run_id, token, "failed", code, dt.datetime.now(dt.timezone.utc))
            log.info("run_id=%s source_key=%s status=failed error_code=%s", run_id, spec.source_key, code)
        except AttributeError:
            with self.factory() as session, session.begin():
                RetrievalService.finish(
                    session, run_id, token, "failed", "INTERNAL_ATTRIBUTE_ERROR", dt.datetime.now(dt.timezone.utc)
                )
            log.info(
                "run_id=%s source_key=%s status=failed error_code=INTERNAL_ATTRIBUTE_ERROR", run_id, spec.source_key
            )
        except TypeError:
            with self.factory() as session, session.begin():
                RetrievalService.finish(
                    session, run_id, token, "failed", "INTERNAL_TYPE_ERROR", dt.datetime.now(dt.timezone.utc)
                )
            log.info("run_id=%s source_key=%s status=failed error_code=INTERNAL_TYPE_ERROR", run_id, spec.source_key)
        except AssertionError:
            with self.factory() as session, session.begin():
                RetrievalService.finish(
                    session, run_id, token, "failed", "INTERNAL_ASSERTION_ERROR", dt.datetime.now(dt.timezone.utc)
                )
            log.info(
                "run_id=%s source_key=%s status=failed error_code=INTERNAL_ASSERTION_ERROR", run_id, spec.source_key
            )
        except OSError:
            with self.factory() as session, session.begin():
                RetrievalService.finish(
                    session, run_id, token, "failed", "STORAGE_IO_ERROR", dt.datetime.now(dt.timezone.utc)
                )
            log.info("run_id=%s source_key=%s status=failed error_code=STORAGE_IO_ERROR", run_id, spec.source_key)
        except Exception:
            with self.factory() as session, session.begin():
                add_fetches(session)
                RetrievalService.finish(
                    session, run_id, token, "failed", "INTERNAL_ERROR", dt.datetime.now(dt.timezone.utc)
                )
            log.info("run_id=%s source_key=%s status=failed error_code=INTERNAL_ERROR", run_id, spec.source_key)
        return True

    def serve(self, poll_seconds: float = 2.0) -> None:
        log.info("worker_version=p1-4-normalization-v1")
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "stop_requested", True))
        self.recover()
        while not self.stop_requested:
            if not self.run_once():
                time.sleep(poll_seconds)
