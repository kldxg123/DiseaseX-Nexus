from __future__ import annotations

import datetime as dt
import hashlib
import os
import signal
import time
import urllib.parse
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy import Engine, create_engine, text

from epidemic_core.discovery.candidate_pipeline import materialize_verified_candidate, persist_search_results
from epidemic_core.discovery.providers import (
    BraveSearchProvider,
    BrazilOpenDataSUSDengueProvider,
    DataCiteProvider,
    DataGouvFranceProvider,
    DirectoryRegistry,
    DiscoveryProvider,
    OpenClawDuckDuckGoProvider,
    OpenClawParallelFreeProvider,
    ProviderOutcome,
    SearchRequest,
    SearchResult,
    default_providers,
)
from epidemic_core.discovery.safe_fetcher import SafeFetcher
from epidemic_core.discovery.schemas import DiscoveryInput

WORKER_VERSION = "p1-8b-verified-discovery-v1"
POLL_SECONDS = 2.0
LEASE_SECONDS = 30


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

    def search_request(self, claim: Claim, request: DiscoveryInput) -> SearchRequest:
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        "SELECT c.alpha2,c.alpha3,c.name country_en,"
                        "COALESCE((SELECT ca.alias FROM epidemic.country_aliases ca "
                        "WHERE ca.country_alpha2=c.alpha2 AND ca.locale=ANY(:languages) "
                        "ORDER BY ca.alias_type,ca.id LIMIT 1),c.name) country_local,"
                        "d.canonical_name disease_en,"
                        "COALESCE((SELECT da.alias FROM epidemic.disease_aliases da "
                        "WHERE da.disease_id=d.id AND da.language_tag=ANY(:languages) "
                        "ORDER BY da.alias_type,da.id LIMIT 1),d.canonical_name) disease_local "
                        "FROM epidemic.countries c CROSS JOIN epidemic.diseases d "
                        "WHERE c.alpha3=:country AND d.id=:disease"
                    ),
                    {
                        "country": request.country_iso3,
                        "disease": request.disease_id,
                        "languages": request.language_preferences,
                    },
                )
                .mappings()
                .one()
            )
        return SearchRequest(
            str(row["alpha3"]),
            str(row["alpha2"]),
            str(row["country_en"]),
            str(row["country_local"]),
            str(row["disease_en"]),
            str(row["disease_local"]),
            tuple(request.desired_metrics),
            request.time_window_kind,
            tuple(request.preferred_source_tiers),
            tuple(request.language_preferences),
            min(int(claim.budget.get("results_per_query", 20)), request.max_candidates),
        )

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


def _direct_machine_readable(result: SearchResult) -> bool:
    path = result.canonical_url.partition("?")[0].casefold()
    return result.media_type in {
        "text/csv",
        "application/csv",
        "text/tab-separated-values",
        "application/json",
        "application/x-ndjson",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/zip",
        "text/html",
    } or path.endswith((".csv", ".tsv", ".json", ".ndjson", ".jsonl", ".xlsx", ".zip", ".html"))


def _progress() -> dict[str, int]:
    return {
        "planned_queries": 0,
        "executed_queries": 0,
        "network_requests": 0,
        "directories_attempted": 0,
        "directories_succeeded": 0,
        "successful_responses": 0,
        "raw_results": 0,
        "deduplicated_results": 0,
        "policy_blocked_results": 0,
        "qualified_candidates": 0,
        "provider_successes": 0,
        "provider_degraded_or_failed": 0,
        "duplicate_results": 0,
        "fetch_attempts": 0,
        "fetch_successes": 0,
        "artifacts": 0,
        "parsed_tables": 0,
        "preview_rows": 0,
        "reused_candidates": 0,
        "blockers": 0,
        "plan_steps": 0,
        "directories": 0,
        "queries": 0,
        "deduplicated": 0,
        "qualified": 0,
        "blocked": 0,
    }


def _merge(progress: dict[str, int], **values: int) -> dict[str, int]:
    progress.update(values)
    progress["directories"] = progress["directories_attempted"]
    progress["queries"] = progress["executed_queries"]
    progress["deduplicated"] = progress["deduplicated_results"]
    progress["qualified"] = progress["qualified_candidates"]
    progress["blocked"] = progress["policy_blocked_results"]
    return dict(progress)


def _provider_selected(provider: DiscoveryProvider, request: SearchRequest) -> bool:
    if isinstance(provider, BraveSearchProvider):
        return True
    if isinstance(provider, DataCiteProvider):
        return provider.directory.supports(request.country_iso3, request.preferred_source_tiers)
    if isinstance(provider, DataGouvFranceProvider):
        return provider.directory.supports(request.country_iso3, request.preferred_source_tiers)
    if isinstance(provider, BrazilOpenDataSUSDengueProvider):
        return provider.directory.supports(request.country_iso3, request.preferred_source_tiers) and (
            "dengue" in request.disease_name_en.casefold() or "dengue" in request.disease_name_local.casefold()
        )
    if isinstance(provider, (OpenClawDuckDuckGoProvider, OpenClawParallelFreeProvider)):
        return provider.enabled
    return True


def _event_code(outcome: ProviderOutcome) -> str:
    if outcome.evidence.reason_code == "PROVIDER_DEGRADED":
        return "PROVIDER_DEGRADED"
    if not outcome.capability.configured:
        return "PROVIDER_CONFIGURATION_MISSING"
    if not outcome.evidence.network_request:
        return "PROVIDER_REQUEST_NOT_EXECUTED"
    return "DIRECTORY_REQUEST_AUDIT" if outcome.evidence.directory_id else "PROVIDER_REQUEST_AUDIT"


def process_claim(
    queue: Queue,
    claim: Claim,
    *,
    providers: tuple[DiscoveryProvider, ...] | None = None,
    registry: DirectoryRegistry | None = None,
    candidate_fetcher: SafeFetcher | None = None,
    quarantine_root: Path | None = None,
    candidate_profile_override: tuple[list[str], list[dict[str, object]], dict[str, int]] | None = None,
) -> None:
    try:
        request = DiscoveryInput.model_validate(claim.request_payload)
    except ValidationError as exc:
        missing = sorted({str(item["loc"][0]) for item in exc.errors() if item.get("loc")})
        queue.finish(
            claim,
            "INVALID_REQUEST",
            "INVALID_REQUEST_FIELDS",
            {"missing_or_invalid_fields": missing, "message": "请求字段缺失或格式无效。"},
            {"policy_blocked_results": 1, "blocked": 1},
        )
        return
    selected_registry = registry or DirectoryRegistry.load()
    selected_providers = providers or default_providers(selected_registry)
    search_request = queue.search_request(claim, request)
    active_providers = tuple(item for item in selected_providers if _provider_selected(item, search_request))
    progress = _progress()
    planned = sum(
        3 if isinstance(item, (OpenClawDuckDuckGoProvider, DataGouvFranceProvider)) else 1 for item in active_providers
    )
    matching_directories = selected_registry.matching(
        search_request.country_iso3, search_request.preferred_source_tiers
    )
    _merge(progress, planned_queries=planned, plan_steps=planned + len(matching_directories) + 3)
    queue.transition(
        claim,
        "DISCOVERING",
        "DISCOVERY_PLAN_GENERATED",
        {
            "country_iso3": search_request.country_iso3,
            "country_names": [search_request.country_name_en, search_request.country_name_local],
            "disease_names": [search_request.disease_name_en, search_request.disease_name_local],
            "metrics": list(search_request.desired_metrics),
            "time_window_kind": search_request.time_window_kind,
            "query_summary": search_request.summary(),
            "query_sha256": hashlib.sha256(search_request.query_text().encode()).hexdigest(),
            "bilingual_query_plan_sha256": [
                hashlib.sha256(item.encode()).hexdigest() for item in search_request.query_plan()
            ],
            "bilingual_query_plan_count": len(search_request.query_plan()),
            "provider_ids": [
                item.capability(
                    isinstance(item, (BraveSearchProvider, DataCiteProvider, DataGouvFranceProvider))
                ).provider_id
                for item in active_providers
            ],
            "directory_registry_version": selected_registry.version,
            "selected_source_tiers": list(search_request.preferred_source_tiers),
            "international_public_health_selected": "INTERNATIONAL_PUBLIC_HEALTH"
            in search_request.preferred_source_tiers,
        },
        dict(progress),
    )
    for directory in matching_directories:
        queue.heartbeat(claim)
        queue.transition(
            claim,
            "DISCOVERING",
            "DIRECTORY_CAPABILITY_EVALUATED",
            directory.evidence(required_channel=directory.enabled),
            dict(progress),
        )
    raw_results: list[SearchResult] = []
    outcomes: list[ProviderOutcome] = []
    query_budget = int(claim.budget.get("queries", 5))
    for provider in active_providers[:query_budget]:
        queue.heartbeat(claim)
        required = isinstance(provider, (DataCiteProvider, DataGouvFranceProvider))
        outcome = provider.search(search_request, required)
        outcomes.append(outcome)
        if outcome.evidence.network_request:
            attempts = (
                outcome.evidence.query_count
                if isinstance(provider, (OpenClawDuckDuckGoProvider, DataGouvFranceProvider))
                else 1
            )
            progress["executed_queries"] += attempts
            progress["network_requests"] += attempts
            if outcome.evidence.directory_id:
                progress["directories_attempted"] += 1
            if outcome.evidence.http_status == 200:
                progress["successful_responses"] += 1
                if outcome.evidence.directory_id:
                    progress["directories_succeeded"] += 1
        if outcome.evidence.result in {"SUCCESS", "NO_RESULTS"}:
            progress["provider_successes"] += 1
        else:
            progress["provider_degraded_or_failed"] += 1
        progress["raw_results"] += len(outcome.results)
        progress["policy_blocked_results"] += outcome.blocked_result_count
        raw_results.extend(outcome.results)
        queue.transition(
            claim,
            "DISCOVERING",
            _event_code(outcome),
            {
                **outcome.capability.evidence(),
                **outcome.evidence.event_details(),
            },
            _merge(progress),
        )
    seen_urls: set[str] = set()
    ledger_input: list[tuple[SearchResult, str]] = []
    for result in raw_results[:100]:
        duplicate = result.canonical_url in seen_urls
        if not duplicate:
            seen_urls.add(result.canonical_url)
        eligible_tier = result.source_tier in search_request.preferred_source_tiers
        disposition = (
            "DUPLICATE_URL"
            if duplicate
            else "FETCH_SELECTED"
            if eligible_tier and _direct_machine_readable(result)
            else "POLICY_BLOCKED"
        )
        ledger_input.append((result, disposition))
    persisted = persist_search_results(queue.engine, claim.run_id, ledger_input)
    progress["deduplicated_results"] = len(seen_urls)
    progress["duplicate_results"] = sum(disposition == "DUPLICATE_URL" for _, disposition in ledger_input)
    direct = [(result_id, result) for result_id, result, disposition in persisted if disposition == "FETCH_SELECTED"]
    progress["policy_blocked_results"] += sum(disposition == "POLICY_BLOCKED" for _, disposition in ledger_input)
    progress["blockers"] = progress["policy_blocked_results"]
    queue.transition(
        claim,
        "DISCOVERING",
        "DISCOVERY_RESULTS_DEDUPLICATED",
        {
            "raw_results": len(raw_results),
            "deduplicated_results": len(seen_urls),
            "persisted_search_results": len(persisted),
            "direct_machine_readable_results": len(direct),
            "duplicate_results": progress["duplicate_results"],
            "policy_blocked_results": progress["policy_blocked_results"],
        },
        _merge(progress),
    )
    candidate_ids: list[str] = []
    reused_candidate_ids: list[str] = []
    if direct:
        queue.transition(
            claim,
            "CANDIDATES_FOUND",
            "DIRECT_CANDIDATES_FOUND",
            {"count": len(direct)},
            _merge(progress),
        )
        queue.transition(
            claim,
            "PROFILING",
            "CANDIDATE_PROFILING_STARTED",
            {"count": len(direct)},
            _merge(progress),
        )
    fetch_budget = min(20, int(claim.budget.get("artifact_fetches", 20)))
    for search_result_id, result in direct[:fetch_budget]:
        queue.heartbeat(claim)
        progress["fetch_attempts"] += 1
        materialized = materialize_verified_candidate(
            queue.engine,
            claim.run_id,
            search_request,
            result,
            search_result_id,
            fetcher=candidate_fetcher,
            quarantine_root=quarantine_root,
            profile_override=candidate_profile_override,
        )
        if materialized.network_request:
            progress["network_requests"] += 1
            if materialized.http_status == 200:
                progress["successful_responses"] += 1
                progress["fetch_successes"] += 1
        if materialized.candidate_id is None:
            progress["policy_blocked_results"] += 1
            progress["blockers"] += 1
        else:
            progress["qualified_candidates"] += 1
            progress["artifacts"] += 1
            progress["parsed_tables"] += 1
            progress["preview_rows"] += materialized.preview_rows
            candidate_ids.append(str(materialized.candidate_id))
            if materialized.reason_code == "REUSED_EXISTING_CANDIDATE":
                progress["reused_candidates"] += 1
                reused_candidate_ids.append(str(materialized.candidate_id))
        queue.transition(
            claim,
            "PROFILING",
            "CANDIDATE_FETCH_AUDIT",
            {
                "provider_result_url_sha256": hashlib.sha256(result.canonical_url.encode()).hexdigest(),
                "search_result_id": str(materialized.search_result_id),
                "fetch_id": str(materialized.fetch_id),
                "domain": urllib.parse.urlsplit(result.canonical_url).hostname,
                "network_request": materialized.network_request,
                "http_status": materialized.http_status,
                "elapsed_ms": materialized.elapsed_ms,
                "response_sha256": materialized.response_sha256,
                "candidate_id": str(materialized.candidate_id) if materialized.candidate_id else None,
                "reason_code": materialized.reason_code,
                "artifact_id": str(materialized.artifact_id) if materialized.artifact_id else None,
                "parsed_rows": materialized.parsed_rows,
                "parsed_columns": materialized.parsed_columns,
                "preview_rows": materialized.preview_rows,
                "schema_sha256": materialized.schema_sha256,
                "date_min": materialized.date_min,
                "date_max": materialized.date_max,
                "nonzero_epidemic_values": materialized.nonzero_epidemic_values,
            },
            _merge(progress),
        )
        if len(candidate_ids) >= request.max_candidates:
            break
    if candidate_ids:
        queue.finish(
            claim,
            "REVIEW_READY",
            "CANDIDATES_AWAITING_HUMAN_REVIEW",
            {
                "candidate_ids": candidate_ids,
                "reused_candidate_ids": reused_candidate_ids,
                "message": (
                    f"找到 {len(candidate_ids)} 个候选，其中 {len(reused_candidate_ids)} 个因 Artifact 内容完全相同而"
                    "复用既有 Candidate Version，已进入隔离人工复核区。"
                    if reused_candidate_ids
                    else f"找到 {len(candidate_ids)} 个候选，已进入隔离人工复核区。"
                ),
                "next_step": "由授权复核人员检查来源、许可、范围、schema 与映射；不会自动进入正式库。",
            },
            _merge(progress),
        )
        return
    failed_required = any(
        outcome.capability.required_channel
        and (not outcome.evidence.network_request or outcome.evidence.result not in {"SUCCESS", "NO_RESULTS"})
        for outcome in outcomes
    )
    free_outcomes = [
        outcome
        for provider, outcome in zip(active_providers, outcomes, strict=True)
        if isinstance(provider, OpenClawDuckDuckGoProvider)
    ]
    free_completed = not free_outcomes or all(
        outcome.evidence.successful_query_count == outcome.evidence.query_count for outcome in free_outcomes
    )
    official_completed = any(
        outcome.capability.required_channel
        and outcome.evidence.network_request
        and outcome.evidence.result in {"SUCCESS", "NO_RESULTS"}
        for outcome in outcomes
    )
    channel_completed = any(
        outcome.evidence.network_request and outcome.evidence.result in {"SUCCESS", "NO_RESULTS"}
        for outcome in outcomes
    )
    coverage = {
        "executed_provider_ids": [outcome.capability.provider_id for outcome in outcomes],
        "degraded_provider_ids": [
            outcome.capability.provider_id
            for outcome in outcomes
            if outcome.evidence.result not in {"SUCCESS", "NO_RESULTS"}
        ],
        "registered_directories": [item.directory_id for item in matching_directories],
        "disabled_directory_ids": [item.directory_id for item in matching_directories if not item.enabled],
        "covers_entire_internet": False,
        "zero_candidates_does_not_prove_absence": True,
    }
    if not channel_completed:
        terminal_state = "FAILED_RETRYABLE" if claim.attempt < 3 else "FAILED_TERMINAL"
        queue.finish(
            claim,
            terminal_state,
            "ALL_SELECTED_DISCOVERY_CHANNELS_UNAVAILABLE",
            {
                "coverage": coverage,
                "message": "通用检索 API 尚未配置；本次覆盖不完整，0 个候选不代表不存在数据源。",
                "next_step": "需要管理员在服务器本地配置受限秘密文件，然后创建关联重试任务。",
            },
            _merge(progress),
            retry_seconds=min(60, 2**claim.attempt) if terminal_state == "FAILED_RETRYABLE" else 0,
        )
    elif official_completed and free_outcomes and not free_completed:
        queue.finish(
            claim,
            "DISCOVERY_PARTIAL_FREE_PROVIDER_UNAVAILABLE",
            "FREE_PROVIDER_UNAVAILABLE_OFFICIAL_CHANNELS_COMPLETE",
            {
                "coverage": coverage,
                "message": "至少一个必要检索渠道未成功完成，不能断言不存在数据源。",
                "next_step": "管理员检查网络、限流或目录健康状态后创建关联重试任务。",
            },
            _merge(progress),
        )
    elif failed_required:
        queue.finish(
            claim,
            "PROVIDER_DEGRADED",
            "OFFICIAL_PROVIDER_DEGRADED_FREE_SEARCH_COMPLETE",
            {"coverage": coverage, "message": "An official channel degraded while free search completed."},
            _merge(progress),
        )
    else:
        queue.finish(
            claim,
            "DISCOVERY_COMPLETE_NO_CANDIDATE",
            "DISCOVERY_COMPLETE_NO_CANDIDATE",
            {
                "coverage": coverage,
                "message": "本次选择范围内的所有必要检索渠道均已真实执行，但未产生合格候选。",
                "next_step": "可调整用户选择的来源层级或查询范围后创建新的受控发现任务。",
            },
            _merge(progress),
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
