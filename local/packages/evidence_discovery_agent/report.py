from __future__ import annotations

from collections import Counter
from typing import Any

from evidence_discovery_agent.disease_task import build_disease_task
from evidence_discovery_agent.evidence_scorer import score_evidence_records
from evidence_discovery_agent.evidence_store import EvidenceStore
from evidence_discovery_agent.feature_candidate_extractor import extract_feature_candidates
from evidence_discovery_agent.mock_provider import MockEvidenceProvider
from evidence_discovery_agent.model_candidate_extractor import extract_model_candidates
from evidence_discovery_agent.types import DiseaseTaskDefinition, EvidenceDiscoveryReport


def build_evidence_discovery_report(
    *,
    task: DiseaseTaskDefinition,
    provider: MockEvidenceProvider | None = None,
) -> EvidenceDiscoveryReport:
    provider = provider or MockEvidenceProvider()
    documents = provider.discover(task)
    feature_candidates = extract_feature_candidates(documents)
    model_candidates = extract_model_candidates(documents)
    evidence_records = score_evidence_records(task, documents)
    recommended = [
        candidate.name
        for candidate in model_candidates
        if candidate.suitable_for_m2_first_batch and candidate.score >= 0.62
    ][:6]
    high_risk = [
        candidate.name
        for candidate in model_candidates
        if not candidate.suitable_for_m2_first_batch or {"data_hungry", "hard_to_validate"} & set(candidate.risks)
    ]
    return EvidenceDiscoveryReport(
        task=task,
        evidence_documents=documents,
        feature_candidates=feature_candidates,
        model_candidates=model_candidates,
        evidence_records=evidence_records,
        recommended_first_batch_models=recommended,
        high_risk_models_not_recommended=high_risk,
        data_gaps=_data_gaps(feature_candidates),
        m1_data_capabilities=_m1_capabilities(),
        limitations=[
            "Fixture/mock evidence only; no live literature search was executed.",
            "Recommendations are readiness candidates and require rolling backtest validation.",
            "No model was trained, fine-tuned, promoted, or used for production prediction.",
        ],
        next_action_plan=[
            "Human-review evidence schema and candidate rankings.",
            "Define a controlled literature discovery policy before any live literature access.",
            "Prepare rolling backtest experiments for first-batch baseline and tree models.",
            "Keep transformer, LLM, graph and mobility-heavy approaches out of the first batch unless data gaps are closed.",
        ],
    )


def run_mock_evidence_discovery(
    *,
    disease: str,
    country: str,
    target: str,
    output_dir: str,
    horizon_days: int = 28,
) -> dict[str, Any]:
    task = build_disease_task(
        disease=disease,
        country=country,
        prediction_target=target,
        horizon_days=horizon_days,
    )
    report = build_evidence_discovery_report(task=task)
    return write_readiness_reports(report, output_dir=output_dir)


def write_readiness_reports(report: EvidenceDiscoveryReport, *, output_dir: str) -> dict[str, Any]:
    store = EvidenceStore(output_dir)
    payload = report.to_dict()
    paths = {
        "report_json": store.write_json("M2_M0_EVIDENCE_DISCOVERY_REPORT.json", payload),
        "report_markdown": store.write_markdown("M2_M0_EVIDENCE_DISCOVERY_REPORT.md", _main_report_markdown(report)),
        "feature_candidate_pool": store.write_markdown("M2_FEATURE_CANDIDATE_POOL.md", _feature_markdown(report)),
        "model_candidate_pool": store.write_markdown("M2_MODEL_CANDIDATE_POOL.md", _model_markdown(report)),
        "gap_analysis": store.write_markdown("M2_GAP_ANALYSIS.md", _gap_markdown(report)),
        "next_action_plan": store.write_markdown("M2_NEXT_ACTION_PLAN.md", _next_action_markdown(report)),
    }
    return {
        "status": "ok",
        "network_accessed": False,
        "documents_seen": len(report.evidence_documents),
        "feature_candidates": len(report.feature_candidates),
        "model_candidates": len(report.model_candidates),
        "recommended_first_batch_models": list(report.recommended_first_batch_models),
        "high_risk_models_not_recommended": list(report.high_risk_models_not_recommended),
        "paths": paths,
        "requires_human_review": True,
    }


def _m1_capabilities() -> dict[str, Any]:
    return {
        "supported_diseases": ["influenza", "norovirus"],
        "available_feature_groups": [
            "epidemiological_lag",
            "rolling_statistics",
            "seasonality",
            "testing",
            "positivity",
            "source_signal",
            "data_quality",
        ],
        "parser_strategies": [
            "csv_dataset",
            "html_table",
            "json_api",
            "official_report_page",
            "dashboard_data_source",
        ],
        "requires_human_review": True,
    }


def _data_gaps(feature_candidates) -> list[str]:
    gaps: list[str] = []
    for candidate in feature_candidates:
        if not candidate.m1_supported:
            gaps.extend(candidate.gap_notes)
    return sorted(set(gaps)) or ["No blocking M1 feature gap for first-batch baseline models."]


def _main_report_markdown(report: EvidenceDiscoveryReport) -> str:
    return "\n".join(
        [
            "# M2 M0 Evidence Discovery Report",
            "",
            "This readiness report is based on local fixtures and mock providers only.",
            "",
            f"- Disease: {report.task.disease}",
            f"- Country: {report.task.country}",
            f"- Target: {report.task.prediction_target}",
            f"- Horizon days: {report.task.horizon_days}",
            f"- Evidence documents: {len(report.evidence_documents)}",
            f"- Feature candidates: {len(report.feature_candidates)}",
            f"- Model candidates: {len(report.model_candidates)}",
            "",
            "## Recommended First-Batch Models",
            "",
            *[f"- {name}" for name in report.recommended_first_batch_models],
            "",
            "## High-Risk Models Not Recommended Immediately",
            "",
            *[f"- {name}" for name in report.high_risk_models_not_recommended],
            "",
            "## Limitations",
            "",
            *[f"- {item}" for item in report.limitations],
            "",
        ]
    )


def _feature_markdown(report: EvidenceDiscoveryReport) -> str:
    lines = ["# M2 Feature Candidate Pool", ""]
    for candidate in report.feature_candidates:
        lines.append(
            f"- {candidate.name} ({candidate.feature_group}): score={candidate.score}, "
            f"evidence_count={candidate.evidence_count}, m1_supported={candidate.m1_supported}"
        )
        for note in candidate.gap_notes:
            lines.append(f"  - {note}")
    return "\n".join(lines) + "\n"


def _model_markdown(report: EvidenceDiscoveryReport) -> str:
    lines = ["# M2 Model Candidate Pool", ""]
    for candidate in report.model_candidates:
        risks = ", ".join(candidate.risks) if candidate.risks else "none"
        lines.append(
            f"- {candidate.name} ({candidate.model_family}): score={candidate.score}, "
            f"first_batch={candidate.suitable_for_m2_first_batch}, risks={risks}"
        )
    return "\n".join(lines) + "\n"


def _gap_markdown(report: EvidenceDiscoveryReport) -> str:
    families = Counter(candidate.model_family for candidate in report.model_candidates)
    return "\n".join(
        [
            "# M2 Gap Analysis",
            "",
            "## Data Gaps",
            "",
            *[f"- {gap}" for gap in report.data_gaps],
            "",
            "## Model Family Coverage",
            "",
            *[f"- {family}: {count}" for family, count in sorted(families.items())],
            "",
            "Transformer, graph, deep sequence and LLM agent approaches are not recommended for immediate first-batch use without stronger data and validation controls.",
            "",
        ]
    )


def _next_action_markdown(report: EvidenceDiscoveryReport) -> str:
    return "\n".join(
        [
            "# M2 Next Action Plan",
            "",
            *[f"- {item}" for item in report.next_action_plan],
            "",
            "Any live literature or database discovery requires a later dedicated safety policy and explicit approval.",
            "",
        ]
    )
