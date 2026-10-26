from __future__ import annotations

# ruff: noqa: E501
import datetime as dt
import uuid
from typing import Any

from sqlalchemy import Engine, text

from epidemic_core.ebola.service import digest, stable_uuid


def claim_one(engine: Engine, owner: str) -> tuple[uuid.UUID, uuid.UUID] | None:
    token = uuid.uuid4()
    with engine.begin() as connection:
        row = connection.execute(
            text(
                "SELECT id FROM epidemic_ebola.preview_jobs WHERE state IN ('PREVIEW_QUEUED','FAILED_RETRYABLE') AND (next_attempt_at IS NULL OR next_attempt_at<=now()) AND (claim_token IS NULL OR lease_expires_at<now()) ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1"
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        connection.execute(
            text(
                "UPDATE epidemic_ebola.preview_jobs SET state='PREVIEW_CLAIMED',attempt_count=attempt_count+1,claim_owner=:owner,claim_token=:token,lease_expires_at=now()+interval '2 minutes',updated_at=now() WHERE id=:id"
            ),
            {"owner": owner, "token": token, "id": row},
        )
    return uuid.UUID(str(row)), token


def _publish(engine: Engine, job_id: uuid.UUID, token: uuid.UUID) -> None:
    with engine.begin() as connection:
        candidate = (
            connection.execute(
                text(
                    "SELECT j.state,d.id decision_id,d.candidate_version_id,d.artifact_sha256,d.mapping_sha256,b.source_id,b.series_artifact_sha256,c.source_key,c.source_name,c.publisher,c.official_domain FROM epidemic_ebola.preview_jobs j JOIN epidemic_ebola.preview_decisions d ON d.id=j.decision_id JOIN epidemic_ebola.candidate_bindings b ON b.candidate_version_id=d.candidate_version_id JOIN epidemic_ebola.source_contracts c ON c.id=b.source_id WHERE j.id=:job AND j.claim_token=:token FOR UPDATE OF j"
                ),
                {"job": job_id, "token": token},
            )
            .mappings()
            .one_or_none()
        )
        if candidate is None:
            raise RuntimeError("STALE_EBOLA_PREVIEW_CLAIM")
        if candidate["artifact_sha256"] != candidate["series_artifact_sha256"]:
            raise ValueError("EBOLA_PREVIEW_ARTIFACT_BINDING_MISMATCH")
        connection.execute(
            text("UPDATE epidemic_ebola.preview_jobs SET state='PREVIEW_VALIDATING',updated_at=now() WHERE id=:job"),
            {"job": job_id},
        )
        rows = (
            connection.execute(
                text(
                    "SELECT o.id source_observation_id,o.country_iso3,o.source_as_of,o.metric_code,o.value_numeric,o.unit,o.quality_status,o.lineage_sha256 FROM epidemic_ebola.source_report_observations o JOIN epidemic_ebola.source_report_versions v ON v.id=o.report_version_id WHERE v.source_id=:source AND v.is_current ORDER BY o.source_as_of,o.country_iso3,o.metric_code,o.import_classification"
                ),
                {"source": candidate["source_id"]},
            )
            .mappings()
            .all()
        )
        if not rows:
            raise ValueError("EBOLA_PREVIEW_SERIES_EMPTY")
        connection.execute(
            text("UPDATE epidemic_ebola.preview_jobs SET state='PREVIEW_PUBLISHING',updated_at=now() WHERE id=:job"),
            {"job": job_id},
        )
        members: list[dict[str, Any]] = []
        for row in rows:
            lineage = digest(
                {"job": str(job_id), "source_lineage": row["lineage_sha256"], "tier": "EBOLA_REVIEW_PREVIEW"}
            )
            observation_id = stable_uuid("preview-observation", lineage)
            connection.execute(
                text(
                    "INSERT INTO epidemic_ebola.preview_observations(id,job_id,source_observation_id,source_key,country_iso3,source_as_of,metric_code,value_numeric,unit,quality_status,lineage_sha256) VALUES(:id,:job,:source_observation,:source_key,:country,:as_of,:metric,:value,:unit,:quality,:lineage) ON CONFLICT(job_id,source_observation_id) DO NOTHING"
                ),
                {
                    "id": observation_id,
                    "job": job_id,
                    "source_observation": row["source_observation_id"],
                    "source_key": candidate["source_key"],
                    "country": row["country_iso3"],
                    "as_of": row["source_as_of"],
                    "metric": row["metric_code"],
                    "value": row["value_numeric"],
                    "unit": row["unit"],
                    "quality": row["quality_status"],
                    "lineage": lineage,
                },
            )
            members.append(
                {
                    "id": str(observation_id),
                    "date": str(row["source_as_of"]),
                    "metric": row["metric_code"],
                    "value": str(row["value_numeric"]),
                    "lineage": lineage,
                }
            )
        snapshot_sha = digest(members)
        snapshot_id = stable_uuid("ebola-preview-snapshot", snapshot_sha)
        dates = [row["source_as_of"] for row in rows]
        connection.execute(
            text(
                "INSERT INTO epidemic_ebola.preview_snapshots(id,job_id,candidate_version_id,source_key,observation_count,date_min,date_max,snapshot_sha256) VALUES(:id,:job,:version,:source_key,:count,:date_min,:date_max,:sha) ON CONFLICT(job_id) DO NOTHING"
            ),
            {
                "id": snapshot_id,
                "job": job_id,
                "version": candidate["candidate_version_id"],
                "source_key": candidate["source_key"],
                "count": len(rows),
                "date_min": min(dates),
                "date_max": max(dates),
                "sha": snapshot_sha,
            },
        )
        publication_sha = digest({"job": str(job_id), "snapshot": snapshot_sha, "tier": "EBOLA_REVIEW_PREVIEW"})
        publication_id = stable_uuid("ebola-preview-publication", publication_sha)
        connection.execute(
            text(
                "INSERT INTO epidemic_ebola.preview_publications(id,job_id,snapshot_id,source_key,source_name,publisher,official_domain,publication_sha256) VALUES(:id,:job,:snapshot,:source_key,:source_name,:publisher,:domain,:sha) ON CONFLICT(job_id) DO NOTHING"
            ),
            {
                "id": publication_id,
                "job": job_id,
                "snapshot": snapshot_id,
                "source_key": candidate["source_key"],
                "source_name": candidate["source_name"],
                "publisher": candidate["publisher"],
                "domain": candidate["official_domain"],
                "sha": publication_sha,
            },
        )
        connection.execute(
            text(
                "UPDATE epidemic_ebola.preview_jobs SET state='PREVIEW_PUBLISHED',observation_count=:count,snapshot_id=:snapshot,publication_id=:publication,claim_owner=NULL,claim_token=NULL,lease_expires_at=NULL,updated_at=now() WHERE id=:job AND claim_token=:token"
            ),
            {"count": len(rows), "snapshot": snapshot_id, "publication": publication_id, "job": job_id, "token": token},
        )


def _fail(engine: Engine, job_id: uuid.UUID, token: uuid.UUID, exc: Exception) -> None:
    code = str(exc)[:100] if isinstance(exc, ValueError) else type(exc).__name__.upper()
    with engine.begin() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT attempt_count,max_attempts FROM epidemic_ebola.preview_jobs WHERE id=:job AND claim_token=:token FOR UPDATE"
                ),
                {"job": job_id, "token": token},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return
        retryable = int(row["attempt_count"]) < int(row["max_attempts"])
        connection.execute(
            text(
                "UPDATE epidemic_ebola.preview_jobs SET state=:state,failure_code=:code,claim_owner=NULL,claim_token=NULL,lease_expires_at=NULL,next_attempt_at=CASE WHEN :retry THEN now()+interval '5 seconds' ELSE NULL END,updated_at=now() WHERE id=:job"
            ),
            {
                "state": "FAILED_RETRYABLE" if retryable else "FAILED_FINAL",
                "code": code,
                "retry": retryable,
                "job": job_id,
            },
        )


def process_one(engine: Engine, owner: str) -> bool:
    claim = claim_one(engine, owner)
    if claim is None:
        return False
    try:
        _publish(engine, *claim)
    except Exception as exc:
        _fail(engine, *claim, exc)
    return True


def readiness(engine: Engine) -> dict[str, object]:
    with engine.connect() as connection:
        present = connection.execute(text("SELECT to_regclass('epidemic_ebola.preview_jobs') IS NOT NULL")).scalar_one()
        role = connection.execute(text("SELECT current_user")).scalar_one()
        queued = (
            connection.execute(
                text("SELECT count(*) FROM epidemic_ebola.preview_jobs WHERE state='PREVIEW_QUEUED'")
            ).scalar_one()
            if present
            else None
        )
    return {
        "ready": bool(present),
        "role": role,
        "queued": queued,
        "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "secrets_exposed": False,
    }
