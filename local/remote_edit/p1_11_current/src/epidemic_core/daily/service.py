from __future__ import annotations

import datetime as dt
import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from epidemic_core.db.models.tables import DailyUpdateSchedule

CONFIG_VERSION = "daily-update-config-v1"


class DailyUpdateError(RuntimeError):
    pass


@dataclass(frozen=True)
class SeedResult:
    schedule_ids: tuple[uuid.UUID, ...]
    created: int
    updated: int


@dataclass(frozen=True)
class ReconcileResult:
    run_id: uuid.UUID
    schedule_id: uuid.UUID
    created: bool
    missed_intervals: int


def load_config(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DailyUpdateError("DAILY_CONFIG_INVALID") from exc
    if not isinstance(value, dict) or value.get("schema_version") != CONFIG_VERSION:
        raise DailyUpdateError("DAILY_CONFIG_INVALID")
    required = {
        "job_name",
        "cron_expression",
        "timezone",
        "local_execution_time",
        "catch_up_policy",
        "revision_lookback",
    }
    if not required <= value.keys() or value["cron_expression"] != "30 2 * * *":
        raise DailyUpdateError("DAILY_CONFIG_INVALID")
    try:
        ZoneInfo(str(value["timezone"]))
        dt.time.fromisoformat(str(value["local_execution_time"]))
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise DailyUpdateError("DAILY_CONFIG_INVALID") from exc
    if value["catch_up_policy"] != "coalesce_latest" or not isinstance(value["revision_lookback"], dict):
        raise DailyUpdateError("DAILY_CONFIG_INVALID")
    return value


def next_due(now: dt.datetime, timezone: str, execution_time: dt.time) -> dt.datetime:
    if now.tzinfo is None:
        raise DailyUpdateError("CLOCK_MUST_BE_TIMEZONE_AWARE")
    zone = ZoneInfo(timezone)
    local = now.astimezone(zone)
    candidate = dt.datetime.combine(local.date(), execution_time, zone)
    if candidate <= local:
        candidate += dt.timedelta(days=1)
    return candidate.astimezone(dt.timezone.utc)


class DailyUpdateService:
    @staticmethod
    def seed_schedules(session: Session, config_path: Path, now: dt.datetime) -> SeedResult:
        config = load_config(config_path)
        rows = session.execute(
            text(
                """SELECT DISTINCT ON (ds.source_key,q.country_alpha2,q.disease_id)
                  ds.id data_source_id,ds.source_key,q.id query_request_id,q.country_alpha2,q.disease_id,
                  q.fingerprint,rr.adapter_version,
                  COALESCE(
                    snap.feature_spec->>'normalization_contract_version',
                    snap.feature_spec->>'contract_version'
                  ) contract_version
                FROM epidemic.dataset_snapshots snap JOIN epidemic.query_requests q ON q.id=snap.query_request_id
                JOIN epidemic.normalization_runs nr ON nr.dataset_snapshot_id=snap.id AND nr.status='succeeded'
                JOIN epidemic.retrieval_runs rr ON rr.id=nr.retrieval_run_id
                JOIN epidemic.data_sources ds ON ds.id=rr.source_id
                WHERE ds.enabled AND q.time_window_kind='latest_rolling_year'
                ORDER BY ds.source_key,q.country_alpha2,q.disease_id,snap.created_at DESC"""
            )
        ).mappings()
        timezone = str(config["timezone"])
        execution = dt.time.fromisoformat(str(config["local_execution_time"]))
        revision_map = config["revision_lookback"]
        assert isinstance(revision_map, dict)
        schedule_ids: list[uuid.UUID] = []
        created = 0
        updated = 0
        for row in rows:
            lookback = revision_map.get(row["source_key"])
            if not isinstance(lookback, dict) or not row["contract_version"]:
                continue
            name = f"daily-{row['source_key']}-{row['country_alpha2']}-{str(row['disease_id'])[:8]}"
            existing = session.execute(
                select(DailyUpdateSchedule).where(
                    DailyUpdateSchedule.data_source_id == row["data_source_id"],
                    DailyUpdateSchedule.query_request_id == row["query_request_id"],
                )
            ).scalar_one_or_none()
            values = {
                "schedule_name": name,
                "country_alpha2": row["country_alpha2"],
                "disease_id": row["disease_id"],
                "query_fingerprint": row["fingerprint"],
                "adapter_version": row["adapter_version"],
                "normalization_contract_version": row["contract_version"],
                "timezone": timezone,
                "cron_expression": str(config["cron_expression"]),
                "local_execution_time": execution,
                "enabled": True,
                "catch_up_policy": str(config["catch_up_policy"]),
                "revision_lookback": lookback,
                "updated_at": now,
            }
            if existing is None:
                schedule_id = uuid.uuid4()
                session.execute(
                    insert(DailyUpdateSchedule).values(
                        schedule_id=schedule_id,
                        data_source_id=row["data_source_id"],
                        query_request_id=row["query_request_id"],
                        next_due_at=next_due(now, timezone, execution),
                        **values,
                    )
                )
                created += 1
            else:
                schedule_id = existing.schedule_id
                for key, value in values.items():
                    setattr(existing, key, value)
                existing.state_version += 1
                updated += 1
            schedule_ids.append(schedule_id)
        if len(schedule_ids) != 2:
            raise DailyUpdateError("EXPECTED_TWO_APPROVED_DISPLAY_TARGETS")
        return SeedResult(tuple(schedule_ids), created, updated)

    @staticmethod
    def reconcile_due(session: Session, trigger: str, now: dt.datetime) -> tuple[ReconcileResult, ...]:
        if now.tzinfo is None:
            raise DailyUpdateError("CLOCK_MUST_BE_TIMEZONE_AWARE")
        rows = session.execute(
            text("SELECT * FROM epidemic.reconcile_daily_updates(:trigger,:now)"),
            {"trigger": trigger, "now": now},
        ).mappings()
        return tuple(
            ReconcileResult(row["run_id"], row["schedule_id"], row["created"], row["missed_intervals"]) for row in rows
        )

    @staticmethod
    def start_manual(session: Session, schedule_id: uuid.UUID, now: dt.datetime) -> uuid.UUID:
        if now.tzinfo is None:
            raise DailyUpdateError("CLOCK_MUST_BE_TIMEZONE_AWARE")
        value = session.execute(
            text("SELECT epidemic.submit_manual_daily_update(:schedule,:now)"),
            {"schedule": schedule_id, "now": now},
        ).scalar_one()
        return uuid.UUID(str(value))
