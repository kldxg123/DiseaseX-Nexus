from __future__ import annotations

import datetime as dt
import logging
import uuid
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from epidemic_core.normalization.contract import ContractError
from epidemic_core.normalization.dengue import build_dengue_plan, finalize_dengue_plan
from epidemic_core.normalization.parser import ParseError
from epidemic_core.normalization.service import (
    NormalizationError,
    NormalizationService,
    build_plan,
    fail_run,
    finalize_plan,
)

log = logging.getLogger("retrieval_worker")


class NormalizationWorker:
    def __init__(self, factory: sessionmaker[Session], reference_root: Path, raw_root: Path):
        self.factory = factory
        self.reference_root = reference_root
        self.raw_root = raw_root

    def recover(self) -> int:
        with self.factory() as session, session.begin():
            return NormalizationService.recover(session, dt.datetime.now(dt.timezone.utc))

    def run_once(self) -> bool:
        now = dt.datetime.now(dt.timezone.utc)
        with self.factory() as session, session.begin():
            claimed = NormalizationService.claim(session, now)
        if claimed is None:
            return False
        run_id, token = claimed
        try:
            with self.factory() as session:
                run = session.get(__import__("epidemic_core.db.models.tables", fromlist=["NormalizationRun"]).NormalizationRun, run_id)
                retrieval = session.get(__import__("epidemic_core.db.models.tables", fromlist=["RetrievalRun"]).RetrievalRun, run.retrieval_run_id) if run else None
                source = session.get(__import__("epidemic_core.db.models.tables", fromlist=["DataSource"]).DataSource, retrieval.source_id) if retrieval else None
                dengue = bool(source and source.source_key == "who_dengue_global_validated")
                if dengue:
                    dengue_plan = build_dengue_plan(session, run_id, self.reference_root, self.raw_root)
                else:
                    covid_plan = build_plan(session, run_id, self.reference_root, self.raw_root)
            with self.factory() as session, session.begin():
                if dengue:
                    snapshot_id = finalize_dengue_plan(session, run_id, token, dengue_plan, dt.datetime.now(dt.timezone.utc))
                else:
                    snapshot_id = finalize_plan(session, run_id, token, covid_plan, dt.datetime.now(dt.timezone.utc))
            log.info("normalization_run_id=%s status=succeeded snapshot_created=%s", run_id, snapshot_id is not None)
        except (ContractError, ParseError, NormalizationError) as exc:
            self._fail(run_id, token, str(exc))
        except SQLAlchemyError:
            self._fail(run_id, token, "DATABASE_WRITE_FAILED")
        except (OSError, UnicodeError):
            self._fail(run_id, token, "ARTIFACT_INTEGRITY_ERROR")
        except Exception:
            self._fail(run_id, token, "INTERNAL_ERROR")
        return True

    def _fail(self, run_id: uuid.UUID, token: uuid.UUID, code: str) -> None:
        safe = (
            code
            if code
            in {
                "REFERENCE_DATASET_INTEGRITY_ERROR",
                "AMBIGUOUS_TARGET_COUNTRY",
                "UNMAPPED_TARGET_COUNTRY",
                "ARTIFACT_INTEGRITY_ERROR",
                "SOURCE_SCHEMA_MISMATCH",
                "SOURCE_ROW_TOO_LONG",
                "INVALID_SOURCE_DATE",
                "INVALID_SOURCE_INTEGER",
                "CONFLICTING_SOURCE_ROWS",
                "METRIC_CONTRACT_ERROR",
                "STALE_CLAIM",
                "DATABASE_WRITE_FAILED",
                "ACTIVE_CONTRACT_CATALOG_MISMATCH",
                "UNKNOWN_DATE_TYPE",
                "UNRESOLVABLE_SOURCE_PERIOD",
                "FUTURE_DATED_NON_NULL_SOURCE_ROW",
                "INTERNAL_ERROR",
            }
            else "INTERNAL_ERROR"
        )
        try:
            with self.factory() as session, session.begin():
                fail_run(session, run_id, token, safe, dt.datetime.now(dt.timezone.utc))
        except SQLAlchemyError:
            log.info("normalization_run_id=%s status=failed error_code=DATABASE_WRITE_FAILED", run_id)
            return
        log.info("normalization_run_id=%s status=failed error_code=%s", run_id, safe)
