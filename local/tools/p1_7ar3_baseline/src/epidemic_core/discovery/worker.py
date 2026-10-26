from __future__ import annotations

import datetime as dt
import os
import signal
import time
import uuid
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy import Engine, create_engine, text

from epidemic_core.discovery.schemas import DiscoveryInput

WORKER_VERSION = "p1-7ar3-queue-v1"
POLL_SECONDS = 2.0
LEASE_SECONDS = 30
GENERIC_STRATEGIES = (
    ("GOVERNMENT_DIRECTORY", "政府开放数据目录"),
    ("CKAN", "CKAN 开放数据接口"),
    ("SOCRATA", "Socrata 开放数据接口"),
    ("ODATA", "OData 开放数据接口"),
)


@dataclass(frozen=True)
class Claim:
    run_id: uuid.UUID
    request_payload: dict[str, Any]
    token: uuid.UUID
    attempt: int
    budget: dict[str, Any]


class Queue:
    def __init__(self, engine: Engine, worker_id: str, started_at: dt.datetime) -> None:
        self.engine = engine
        self.worker_id = worker_id
        self.started_at = started_at

    def touch(self, current: uuid.UUID | None = None, error: str | None = None) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                text("SELECT epidemic_discovery.touch_discovery_worker(:worker,:version,:started,:run,:error,:pid)"),
                {
                    "worker": self.worker_id,
                    "version": WORKER_VERSION,
                    "started": self.started_at,
                    "run": current,
                    "error": error,
                    "pid": os.getpid(),
                },
            )

    def claim(self) -> Claim | None:
        token = uuid.uuid4()
        with self.engine.begin() as connection:
            row = (
                connection.execute(
                    text("SELECT * FROM epidemic_discovery.claim_discovery_run(:worker,:token,:lease)"),
                    {"worker": self.worker_id, "token": token, "lease": LEASE_SECONDS},
                )
                .mappings()
                .first()
            )
        if row is None:
            return None
        return Claim(
            run_id=row["run_id"],
            request_payload=dict(row["request_payload"]),
            token=row["claim_token"],
            attempt=int(row["attempt_number"]),
            budget=dict(row["budget"]),
        )

    def heartbeat(self, claim: Claim) -> None:
        with self.engine.begin() as connection:
            current = connection.execute(
                text("SELECT epidemic_discovery.heartbeat_discovery_claim(:run,:token,:worker,:lease)"),
                {"run": claim.run_id, "token": claim.token, "worker": self.worker_id, "lease": LEASE_SECONDS},
            ).scalar_one()
        if not current:
            raise RuntimeError("STALE_CLAIM_TOKEN")

    def transition(
        self,
        claim: Claim,
        state: str,
        code: str,
        details: dict[str, Any],
        progress: dict[str, int],
    ) -> None:
        with self.engine.begin() as connection:
            current = connection.execute(
                text(
                    "SELECT epidemic_discovery.transition_discovery_claim("
                    ":run,:token,:state,:code,CAST(:details AS jsonb),CAST(:progress AS jsonb))"
                ),
                {
                    "run": claim.run_id,
                    "token": claim.token,
                    "state": state,
                    "code": code,
                    "details": _json(details),
                    "progress": _json(progress),
                },
            ).scalar_one()
        if not current:
            raise RuntimeError("STALE_CLAIM_TOKEN")

    def finish(
        self,
        claim: Claim,
        state: str,
        reason: str,
        details: dict[str, Any],
        progress: dict[str, int],
        retry_seconds: int = 0,
    ) -> None:
        with self.engine.begin() as connection:
            current = connection.execute(
                text(
                    "SELECT epidemic_discovery.finish_discovery_claim("
                    ":run,:token,:state,:reason,CAST(:details AS jsonb),CAST(:progress AS jsonb),:retry)"
                ),
                {
                    "run": claim.run_id,
                    "token": claim.token,
                    "state": state,
                    "reason": reason,
                    "details": _json(details),
                    "progress": _json(progress),
                    "retry": retry_seconds,
                },
            ).scalar_one()
        if not current:
            raise RuntimeError("STALE_CLAIM_TOKEN")


def _json(value: dict[str, Any]) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def process_claim(queue: Queue, claim: Claim) -> None:
    try:
        request = DiscoveryInput.model_validate(claim.request_payload)
    except ValidationError as exc:
        missing = sorted({str(item["loc"][0]) for item in exc.errors() if item.get("loc")})
        queue.finish(
            claim,
            "INVALID_REQUEST",
            "INVALID_REQUEST_FIELDS",
            {"missing_or_invalid_fields": missing, "message": "请求字段缺失或格式无效。"},
            {"blocked": 1},
        )
        return
    queue.transition(
        claim,
        "DISCOVERING",
        "DISCOVERY_PLAN_GENERATED",
        {
            "country_iso3": request.country_iso3,
            "disease_id": str(request.disease_id),
            "metrics": request.desired_metrics,
            "strategies": [code for code, _ in GENERIC_STRATEGIES],
        },
        {"plan_steps": len(GENERIC_STRATEGIES) + 3},
    )
    for index, (code, label) in enumerate(GENERIC_STRATEGIES, start=1):
        queue.heartbeat(claim)
        queue.transition(
            claim,
            "DISCOVERING",
            "SOURCE_DIRECTORY_CHECKED",
            {
                "strategy": code,
                "label_zh": label,
                "result": "UNCONFIGURED_FOR_COUNTRY",
                "country_iso3": request.country_iso3,
            },
            {"directories": index, "queries": index},
        )
    queue.finish(
        claim,
        "NO_CANDIDATE",
        "GENERIC_SEARCH_PROVIDER_UNCONFIGURED",
        {
            "message": (
                "通用搜索 Provider 未配置；已执行允许的政府目录、CKAN、Socrata 与 OData 策略检查，"
                "当前国家没有配置可安全查询的目录。"
            ),
            "checked_strategies": [code for code, _ in GENERIC_STRATEGIES],
            "next_step": "配置经批准的国家级官方目录后重新创建或重试任务。",
        },
        {
            "directories": len(GENERIC_STRATEGIES),
            "queries": len(GENERIC_STRATEGIES),
            "raw_results": 0,
            "deduplicated": 0,
            "qualified": 0,
            "blocked": len(GENERIC_STRATEGIES),
        },
    )


def main() -> None:
    engine = create_engine(os.environ["APP_DATABASE_URL"], pool_pre_ping=True, hide_parameters=True)
    started_at = dt.datetime.now(dt.timezone.utc)
    worker_id = f"discovery-worker-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    queue = Queue(engine, worker_id, started_at)
    stop = False

    def halt(*_: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, halt)
    signal.signal(signal.SIGINT, halt)
    last_error: str | None = None
    while not stop:
        try:
            queue.touch(error=last_error)
            claim = queue.claim()
            if claim is None:
                last_error = None
                time.sleep(POLL_SECONDS)
                continue
            queue.touch(current=claim.run_id)
            try:
                process_claim(queue, claim)
                last_error = None
            except Exception as exc:  # one task must never kill the worker loop
                last_error = type(exc).__name__
                try:
                    if claim.attempt < 3:
                        queue.finish(
                            claim,
                            "FAILED_RETRYABLE",
                            "WORKER_TASK_EXCEPTION",
                            {"message": type(exc).__name__},
                            {"blocked": 1},
                            retry_seconds=min(60, 2**claim.attempt),
                        )
                    else:
                        queue.finish(
                            claim,
                            "FAILED_TERMINAL",
                            "WORKER_TASK_RETRY_EXHAUSTED",
                            {"message": type(exc).__name__},
                            {"blocked": 1},
                        )
                except Exception:
                    engine.dispose()
            finally:
                queue.touch(error=last_error)
        except Exception as exc:  # database outages are retried by the polling loop
            last_error = type(exc).__name__
            engine.dispose()
            time.sleep(min(5.0, POLL_SECONDS))
    engine.dispose()


if __name__ == "__main__":
    main()
