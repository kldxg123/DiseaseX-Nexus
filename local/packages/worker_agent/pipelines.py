from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select

from common.models import EpidemicEvent, ModelFeature, Prediction, RawDocument, SourceRegistry
from worker_agent.job_types import sanitize_for_json
from worker_agent.tasks import TaskRunner


class PipelineRunner:
    def __init__(self, task_runner: TaskRunner):
        self.task_runner = task_runner

    def run_full_local_pipeline(
        self,
        *,
        country: str = "United States",
        disease: str = "norovirus",
        horizon_days: int = 28,
    ) -> dict:
        warnings: list[str] = []
        steps: list[dict] = []

        self._record_step(steps, warnings, "seed_sources", self.task_runner.run_task("seed_sources"))
        source_id = self._first_source_id()
        if source_id is None:
            return self._summary(steps, "failed", warnings + ["seed_sources did not create a source"])

        for index, html in enumerate(_synthetic_html_series(country, disease, weeks=20), start=1):
            result = self.task_runner.run_task(
                "mock_crawl_url",
                source_id=str(source_id),
                url=f"https://fixture.invalid/worker/local-{index}",
                html=html,
                country_detected=country,
                disease_detected=disease,
                language="en",
            )
            if result.get("status") == "failed":
                warnings.append(f"mock_crawl_url failed for synthetic row {index}")
            if index <= 3:
                self._record_step(steps, warnings, f"mock_crawl_url_{index}", result)
        if _count(self.task_runner.session, RawDocument) > 3:
            steps.append({"name": "mock_crawl_url_remaining", "status": "success", "count": 17})

        self._record_step(
            steps,
            warnings,
            "clean_pending",
            self.task_runner.run_task("clean_pending", country=country, disease=disease, limit=100),
        )
        if _count(self.task_runner.session, EpidemicEvent) < 12:
            created = self._ensure_synthetic_events(country=country, disease=disease, minimum=20)
            warnings.append("synthetic epidemic_events were added because cleaner output was insufficient")
            steps.append({"name": "ensure_synthetic_events", "status": "success", "created": created})

        self._record_step(
            steps,
            warnings,
            "build_features",
            self.task_runner.run_task(
                "build_features",
                country=country,
                disease=disease,
                horizons_days=[int(horizon_days)],
                outbreak_threshold=3,
                replace_existing=True,
            ),
        )
        self._record_step(
            steps,
            warnings,
            "backtest_model",
            self.task_runner.run_task(
                "backtest_model",
                country=country,
                disease=disease,
                horizon_days=int(horizon_days),
                model_name="feature_rule",
                min_train_size=4,
            ),
        )
        self._record_step(
            steps,
            warnings,
            "predict_latest",
            self.task_runner.run_task(
                "predict_latest",
                country=country,
                disease=disease,
                horizon_days=int(horizon_days),
                model_name="feature_rule",
                min_train_size=4,
                write_predictions=True,
            ),
        )
        minimum_data_result = self.task_runner.run_task(
            "minimum_data_analysis",
            country=country,
            disease=disease,
            horizon_days=int(horizon_days),
            target_metric="mae",
            min_acceptable_score=1.0,
            history_windows_weeks=[8, 12, 20],
            model_name="feature_rule",
            min_train_size=4,
        )
        self._record_step(steps, warnings, "minimum_data_analysis", minimum_data_result)

        failed_steps = [step for step in steps if step.get("status") == "failed"]
        status = "failed" if failed_steps else "partial" if warnings else "success"
        summary = self._summary(steps, status, warnings)
        summary["minimum_data"] = _safe_step_summary(minimum_data_result)
        return sanitize_for_json(summary)

    def _record_step(self, steps: list[dict], warnings: list[str], name: str, result: dict) -> None:
        job = result.get("job") if isinstance(result, dict) else {}
        status = result.get("status") if isinstance(result, dict) else "failed"
        steps.append({"name": name, "status": status, "summary": (job or {}).get("summary", {})})
        if status == "failed":
            warnings.append(f"{name} failed")
        warnings.extend((job or {}).get("warnings", []))

    def _summary(self, steps: list[dict], status: str, warnings: list[str]) -> dict:
        session = self.task_runner.session
        return {
            "steps": steps,
            "status": status,
            "sources": _count(session, SourceRegistry),
            "raw_documents": _count(session, RawDocument),
            "events": _count(session, EpidemicEvent),
            "features": _count(session, ModelFeature),
            "predictions": _count(session, Prediction),
            "minimum_data": {},
            "warnings": sorted(set(str(item) for item in warnings if item)),
        }

    def _first_source_id(self) -> int | None:
        return self.task_runner.session.scalar(select(SourceRegistry.id).order_by(SourceRegistry.id))

    def _ensure_synthetic_events(self, *, country: str, disease: str, minimum: int) -> int:
        session = self.task_runner.session
        existing = _count(session, EpidemicEvent)
        if existing >= minimum:
            return 0
        raw_documents = list(session.scalars(select(RawDocument).order_by(RawDocument.id).limit(minimum)).all())
        created = 0
        start = date(2025, 1, 6)
        for index, raw_document in enumerate(raw_documents):
            if created + existing >= minimum:
                break
            already = session.scalar(
                select(EpidemicEvent.id).where(EpidemicEvent.raw_document_id == raw_document.id)
            )
            if already is not None:
                continue
            event_date = start + timedelta(days=7 * index)
            session.add(
                EpidemicEvent(
                    raw_document_id=raw_document.id,
                    country=country,
                    admin1=None,
                    admin2=None,
                    disease=disease,
                    event_date=event_date,
                    iso_week=f"{event_date.isocalendar().year}-W{event_date.isocalendar().week:02d}",
                    cases=index + 1,
                    suspected_cases=None,
                    confirmed_cases=index + 1,
                    deaths=0,
                    hospitalizations=0,
                    tests=100 + index,
                    positivity_rate=None,
                    outbreaks_count=1 if index % 3 == 0 else 0,
                    setting="community",
                    pathogen_genotype=None,
                    confidence_score=0.9,
                    extraction_method="worker_synthetic_fixture",
                    provenance_json={"source": "worker_full_local_pipeline", "synthetic": True},
                )
            )
            created += 1
        session.flush()
        return created


def _synthetic_html_series(country: str, disease: str, *, weeks: int) -> list[str]:
    start = date(2025, 1, 6)
    rows: list[str] = []
    for index in range(weeks):
        event_date = start + timedelta(days=7 * index)
        cases = index + 1
        rows.append(
            "<html><head>"
            f"<title>Local {disease} fixture {index + 1}</title>"
            "</head><body>"
            f"On {event_date.isoformat()}, {country} reported {cases} confirmed cases "
            f"and {1 if index % 3 == 0 else 0} outbreaks of {disease}. "
            "This is synthetic local fixture text for worker tests only."
            "</body></html>"
        )
    return rows


def _safe_step_summary(result: dict) -> dict:
    job = result.get("job") if isinstance(result, dict) else {}
    return sanitize_for_json((job or {}).get("summary", {}))


def _count(session, model) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)
