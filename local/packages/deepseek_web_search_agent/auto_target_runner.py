from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from deepseek_web_search_agent.orchestrator import run_deepseek_web_search
from deepseek_web_search_agent.types import DeepSeekWebSearchRequest
from deepseek_web_search_agent.table_extraction_runner import run_structured_table_review


DEFAULT_TARGETS = [
    ("dengue", "Global"),
    ("measles", "United States"),
    ("cholera", "Global"),
    ("mpox", "Global"),
    ("Ebola", "African Region"),
]


@dataclass(frozen=True)
class GoalTargetAttempt:
    target: str
    disease: str
    country_or_region: str
    status: str
    run_dir: str | None = None
    live_run_dir: str | None = None
    review_table: str | None = None
    review_csv: str | None = None
    tavily_query_count: int = 0
    pages_read_count: int = 0
    document_preview_count: int = 0
    deepseek_call_count: int = 0
    date_anchor_count: int = 0
    metric_anchor_count: int = 0
    context_linked_candidate_count: int = 0
    deterministic_rows_generated: int = 0
    deepseek_rows_generated: int = 0
    review_rows_generated: int = 0
    review_rows_from_live_material: int = 0
    network_accessed: bool = False
    used_date_source_types: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GoalModeSummary:
    status: str
    output_dir: str
    successful_target: str | None
    attempts: list[GoalTargetAttempt]
    network_accessed: bool
    tavily_query_count_total: int
    pages_read_count_total: int
    document_preview_count_total: int
    deepseek_call_count_total: int
    review_rows_from_live_material: int
    goal_mode_summary_json: str
    goal_mode_summary_md: str
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["attempts"] = [attempt.to_dict() for attempt in self.attempts]
        return payload


def parse_targets(value: str | None) -> list[tuple[str, str]]:
    if not value:
        return list(DEFAULT_TARGETS)
    targets: list[tuple[str, str]] = []
    for raw in str(value).split(","):
        text = raw.strip()
        if not text:
            continue
        if ":" in text:
            disease, country = text.split(":", 1)
        else:
            disease, country = text, "Global"
        targets.append((" ".join(disease.split()), " ".join(country.split()) or "Global"))
    return targets or list(DEFAULT_TARGETS)


def run_goal_mode(
    *,
    targets: str | None,
    date_start: str,
    date_end: str,
    output_dir: str = "reports/deepseek_table_review_goal",
    database_url: str | None = None,
    mock_deepseek: bool = False,
    mock_search: bool = False,
    allow_live_source_search: bool = False,
    allow_deepseek: bool = False,
    terms_reviewed: bool = False,
    manual_reviewed: bool = False,
    allow_document_preview: bool = False,
    max_search_queries: int = 6,
    max_pages_to_read: int = 8,
    stop_when_rows_found: bool = True,
) -> GoalModeSummary:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    attempts: list[GoalTargetAttempt] = []
    warnings: list[str] = []

    for disease, country in parse_targets(targets):
        attempt_dir = root / _target_slug(disease, country)
        attempt_dir.mkdir(parents=True, exist_ok=True)
        presearch_summary: dict[str, Any] = {"status": "not_run"}
        source_pack: str | None = None
        search_backend = "mock" if mock_search else "tavily"
        if allow_live_source_search or mock_search:
            try:
                presearch = run_deepseek_web_search(
                    DeepSeekWebSearchRequest(
                        disease=disease,
                        country=country,
                        query_intent="structured_numeric_evidence",
                        search_backend=search_backend,
                        allow_deepseek_api=False,
                        allow_general_web_search=bool(allow_live_source_search and not mock_search),
                        allow_page_read=bool(allow_live_source_search and not mock_search),
                        terms_reviewed=terms_reviewed or mock_search,
                        manual_reviewed=manual_reviewed or mock_search,
                        max_search_queries=max_search_queries,
                        max_search_results_per_query=8,
                        max_pages_to_read=max_pages_to_read,
                        max_total_http_requests=16,
                        max_readable_pages_success=max_pages_to_read,
                        prefer_official_and_structured_sources=True,
                        prefer_situation_reports=True,
                        prefer_dated_numeric_sources=True,
                        include_document_candidates=bool(allow_document_preview),
                        do_not_download_documents=True,
                        output_dir=str(attempt_dir / "_web_search_source_pack"),
                        database_url=database_url,
                    )
                )
                paths = presearch.paths if isinstance(presearch.paths, dict) else {}
                source_pack = _pick_source_pack_path(paths)
                presearch_summary = {
                    "status": presearch.status,
                    "run_id": presearch.run_id,
                    "search_backend": presearch.search_backend,
                    "search_request_count": presearch.search_request_count,
                    "page_read_request_count": presearch.page_read_request_count,
                    "readable_pages_success": presearch.readable_pages_success,
                    "blocked_pages_count": presearch.blocked_pages_count,
                    "search_results_seen": presearch.search_results_seen,
                    "source_pack": source_pack,
                    "network_accessed": bool(allow_live_source_search and not mock_search),
                    "warnings": presearch.warnings,
                }
            except Exception as exc:
                presearch_summary = {
                    "status": "failed",
                    "failure_type": type(exc).__name__,
                    "network_accessed": bool(allow_live_source_search and not mock_search),
                    "warnings": ["presearch_failed_safely"],
                }
        result = run_structured_table_review(
            disease=disease,
            country_or_region=country,
            date_start=date_start,
            date_end=date_end,
            output_dir=str(attempt_dir),
            source_pack_path=source_pack,
            mock_deepseek=bool(mock_deepseek or not allow_deepseek),
            allow_deepseek=bool(allow_deepseek and not mock_deepseek),
            allow_live_source_search=False,
            allow_network=False,
            terms_reviewed=terms_reviewed,
            manual_reviewed=manual_reviewed,
            max_sources=5,
        )
        attempt = _attempt_from_result(
            disease=disease,
            country=country,
            result=result,
            presearch_summary=presearch_summary,
            document_preview_count=0,
            live_material=not mock_deepseek,
        )
        attempts.append(attempt)
        if stop_when_rows_found and (
            attempt.review_rows_from_live_material >= 1 or (mock_deepseek and attempt.review_rows_generated >= 1)
        ):
            break

    summary = _summary(root, attempts, warnings)
    _write_goal_reports(summary)
    return summary


def _attempt_from_result(
    *,
    disease: str,
    country: str,
    result: dict[str, Any],
    presearch_summary: dict[str, Any],
    document_preview_count: int,
    live_material: bool,
) -> GoalTargetAttempt:
    validation = result.get("validation") if isinstance(result.get("validation"), dict) else {}
    readiness = result.get("source_pack_readiness") if isinstance(result.get("source_pack_readiness"), dict) else {}
    paths = result.get("paths") if isinstance(result.get("paths"), dict) else {}
    rows_total = int(validation.get("ready_for_approval") or 0)
    live_rows = rows_total if live_material else 0
    network_accessed = bool(presearch_summary.get("network_accessed") or result.get("deepseek_called"))
    tavily_query_count = (
        int(presearch_summary.get("search_request_count") or 0)
        if presearch_summary.get("search_backend") == "tavily" and bool(presearch_summary.get("network_accessed"))
        else 0
    )
    pages_read_count = int(presearch_summary.get("page_read_request_count") or 0) if network_accessed else 0
    used_date_sources = _read_date_sources(paths.get("review_json"))
    blockers = sorted(
        set(
            list(readiness.get("source_pack_level_blockers") or [])
            + [
                issue
                for issue in (
                    "no_structured_rows_extracted",
                    "no_official_structured_source_found" if rows_total <= 0 else "",
                )
                if issue
            ]
        )
    )
    return GoalTargetAttempt(
        target=f"{disease}:{country}",
        disease=disease,
        country_or_region=country,
        status="success" if live_rows >= 1 else "mock_success" if rows_total >= 1 and not live_material else "no_live_rows",
        run_dir=result.get("run_dir"),
        live_run_dir=result.get("run_dir"),
        review_table=paths.get("review_json"),
        review_csv=paths.get("review_csv"),
        tavily_query_count=tavily_query_count,
        pages_read_count=pages_read_count,
        document_preview_count=document_preview_count,
        deepseek_call_count=1 if result.get("deepseek_called") else 0,
        date_anchor_count=int(result.get("date_anchor_count") or 0),
        metric_anchor_count=int(result.get("metric_anchor_count") or 0),
        context_linked_candidate_count=int(result.get("context_linked_candidate_count") or 0),
        deterministic_rows_generated=int(result.get("deterministic_rows_generated") or 0),
        deepseek_rows_generated=int(result.get("deepseek_rows_generated") or 0),
        review_rows_generated=rows_total,
        review_rows_from_live_material=live_rows,
        network_accessed=network_accessed,
        used_date_source_types=used_date_sources,
        blockers=blockers,
        warnings=list(result.get("warnings") or []) + list(presearch_summary.get("warnings") or []),
    )


def _summary(root: Path, attempts: list[GoalTargetAttempt], warnings: list[str]) -> GoalModeSummary:
    successful = next((attempt for attempt in attempts if attempt.review_rows_from_live_material >= 1), None)
    mock_success = next((attempt for attempt in attempts if attempt.review_rows_generated >= 1), None)
    json_path = root / "goal_mode_summary.json"
    md_path = root / "goal_mode_summary.md"
    return GoalModeSummary(
        status="ok" if successful or mock_success else "no_live_rows",
        output_dir=str(root),
        successful_target=successful.target if successful else None,
        attempts=attempts,
        network_accessed=any(attempt.network_accessed for attempt in attempts),
        tavily_query_count_total=sum(attempt.tavily_query_count for attempt in attempts),
        pages_read_count_total=sum(attempt.pages_read_count for attempt in attempts),
        document_preview_count_total=sum(attempt.document_preview_count for attempt in attempts),
        deepseek_call_count_total=sum(attempt.deepseek_call_count for attempt in attempts),
        review_rows_from_live_material=sum(attempt.review_rows_from_live_material for attempt in attempts),
        goal_mode_summary_json=str(json_path),
        goal_mode_summary_md=str(md_path),
        warnings=warnings,
    )


def _write_goal_reports(summary: GoalModeSummary) -> None:
    payload = summary.to_dict()
    Path(summary.goal_mode_summary_json).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# DeepSeek Table Review Goal Mode", ""]
    lines.append(f"- status: `{summary.status}`")
    lines.append(f"- successful_target: `{summary.successful_target}`")
    lines.append(f"- review_rows_from_live_material: `{summary.review_rows_from_live_material}`")
    lines.append("")
    lines.append("| target | status | rows | blockers |")
    lines.append("|---|---|---:|---|")
    for attempt in summary.attempts:
        lines.append(
            f"| {attempt.target} | {attempt.status} | {attempt.review_rows_from_live_material} | {', '.join(attempt.blockers[:5])} |"
        )
    Path(summary.goal_mode_summary_md).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _pick_source_pack_path(paths: dict[str, str]) -> str | None:
    for key in ("structured_source_pack", "page_read_source_pack", "source_pack", "page_read_summary", "search_results_summary"):
        value = paths.get(key)
        if value:
            return str(value)
    return None


def _read_date_sources(path: str | None) -> list[str]:
    if not path:
        return []
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = payload.get("rows") if isinstance(payload, dict) else []
    return sorted({str(row.get("date_source")) for row in rows if isinstance(row, dict) and row.get("date_source")})


def _target_slug(disease: str, country: str) -> str:
    return "_".join(f"{disease}_{country}".lower().replace("/", " ").split())


__all__ = ["DEFAULT_TARGETS", "GoalModeSummary", "GoalTargetAttempt", "parse_targets", "run_goal_mode"]
