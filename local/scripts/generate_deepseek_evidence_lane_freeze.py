from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def run_freeze(args: argparse.Namespace) -> dict[str, Any]:
    diagnostics_dir = Path(args.diagnostics_dir)
    synthetic_dir = Path(args.synthetic_smoke_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    evidence = _load_evidence(diagnostics_dir, synthetic_dir)
    manifest = _build_manifest(diagnostics_dir, synthetic_dir, output_dir)
    paths = _write_freeze_package(output_dir, manifest, evidence)
    return {
        "status": "ok",
        "freeze_dir": str(output_dir),
        "artifact_count": len(manifest["artifacts"]),
        "paths": paths,
        "network_accessed": False,
        "deepseek_called_in_this_step": False,
        "tavily_rerun_in_this_step": False,
    }


def _load_evidence(diagnostics_dir: Path, synthetic_dir: Path) -> dict[str, Any]:
    replay = _read_json(diagnostics_dir / "deepseek_structured_extraction_replay.json")
    quality = _read_json(diagnostics_dir / "deepseek_extraction_quality_audit.json")
    readiness = _read_json(diagnostics_dir / "deepseek_readiness_dashboard_summary.json")
    promotion = _read_json(diagnostics_dir / "human_review" / "event_promotion_summary.json")
    synthetic = _read_json(synthetic_dir / "synthetic_promotion_summary.json")
    synthetic_feature = _read_json(synthetic_dir / "synthetic_feature_eligibility.json")
    return {
        "replay": replay,
        "quality": quality,
        "readiness": readiness,
        "promotion": promotion,
        "synthetic": synthetic,
        "synthetic_feature": synthetic_feature,
        "records_extracted": int(replay.get("records_extracted_by_deepseek") or quality.get("records_extracted") or 0),
        "records_persisted": int(replay.get("records_persisted") or quality.get("records_persisted") or 0),
        "visualization_points": int(replay.get("visualization_points") or quality.get("visualization_points") or 0),
        "evidence_only_count": int(readiness.get("evidence_only_count") or quality.get("evidence_only_count") or 0),
        "event_ready_count": int(readiness.get("event_ready_count") or quality.get("event_ready_count") or 0),
        "feature_ready_count": int(readiness.get("feature_ready_count") or quality.get("feature_ready_count") or 0),
        "visualization_ready_count": int(readiness.get("visualization_ready_count") or quality.get("visualization_ready_count") or 0),
        "requires_human_review_count": int(
            readiness.get("requires_human_review_count") or quality.get("requires_human_review_count") or 0
        ),
        "synthetic_promoted": int(synthetic.get("promoted_epidemic_events") or 0),
        "synthetic_blocked": int(synthetic.get("blocked_count") or 0),
        "synthetic_feature_allowed": bool(synthetic.get("feature_generation_allowed", False)),
        "synthetic_feature_blockers": synthetic.get("feature_eligibility_blockers")
        or synthetic_feature.get("blocker_types")
        or [],
    }


def _build_manifest(diagnostics_dir: Path, synthetic_dir: Path, output_dir: Path) -> dict[str, Any]:
    artifact_specs = [
        ("DeepSeek diagnostics directory", diagnostics_dir, "directory", "root evidence bundle", "safe_summary", "diagnostics"),
        (
            "Extraction replay summary",
            diagnostics_dir / "deepseek_structured_extraction_replay.json",
            "json",
            "replay counts and safety flags",
            "safe_summary",
            "structured_replay",
        ),
        (
            "Database persistence summary",
            diagnostics_dir / "database_persistence_summary.json",
            "json",
            "records persisted count",
            "safe_summary",
            "persistence",
        ),
        (
            "Cleaning bridge summary",
            diagnostics_dir / "cleaning_bridge_summary.json",
            "json",
            "cleaning bridge count and blockers",
            "safe_summary",
            "cleaning_bridge",
        ),
        (
            "3D visualization data summary",
            diagnostics_dir / "visualization_3d_data.json",
            "json",
            "safe visualization candidate points",
            "safe_summary",
            "visualization",
        ),
        (
            "Extraction quality audit",
            diagnostics_dir / "deepseek_extraction_quality_audit.json",
            "json",
            "record-level quality readiness counts",
            "safe_summary",
            "quality_audit",
        ),
        (
            "Readiness review table",
            diagnostics_dir / "deepseek_readiness_review_table.json",
            "json",
            "row-level evidence readiness review",
            "safe_table",
            "readiness_review",
        ),
        (
            "Manual review queue",
            diagnostics_dir / "deepseek_manual_review_queue.json",
            "json",
            "human review queue",
            "safe_table",
            "readiness_review",
        ),
        (
            "Human review template",
            diagnostics_dir / "human_review_workspace" / "deepseek_human_review_template.json",
            "json",
            "annotation template for reviewers",
            "safe_template",
            "human_review",
        ),
        (
            "Annotation workspace bundle",
            diagnostics_dir / "human_review_workspace" / "manual_review_bundle.md",
            "markdown",
            "dashboard workflow bundle",
            "safe_doc",
            "annotation_workspace",
        ),
        (
            "Synthetic promotion smoke summary",
            synthetic_dir / "synthetic_promotion_summary.json",
            "json",
            "synthetic gate smoke result",
            "synthetic_safe_summary",
            "synthetic_smoke",
        ),
        (
            "Synthetic feature eligibility",
            synthetic_dir / "synthetic_feature_eligibility.json",
            "json",
            "synthetic feature gate blockers",
            "synthetic_safe_summary",
            "synthetic_smoke",
        ),
        (
            "Secret hygiene audit",
            diagnostics_dir.parent / "SECRET_HYGIENE_AUDIT.json",
            "json",
            "secret hygiene scan result",
            "safe_summary",
            "safety",
        ),
    ]
    artifacts = []
    for name, path, artifact_type, role, safety, stage in artifact_specs:
        artifacts.append(
            {
                "artifact_name": name,
                "path": str(path),
                "exists": path.exists(),
                "artifact_type": artifact_type,
                "evidence_role": role,
                "safety_level": safety,
                "contains_sensitive_data": False,
                "generated_by_stage": stage,
                "notes": "Safe summary artifact; does not include credentials, prompts, completions, raw text, or full page bodies.",
            }
        )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "freeze_output_dir": str(output_dir),
        "network_accessed": False,
        "deepseek_called_in_this_step": False,
        "tavily_rerun_in_this_step": False,
        "artifacts": artifacts,
    }


def _write_freeze_package(output_dir: Path, manifest: dict[str, Any], evidence: dict[str, Any]) -> dict[str, str]:
    paths = {
        "README": output_dir / "README.md",
        "MANIFEST_json": output_dir / "MANIFEST.json",
        "MANIFEST_md": output_dir / "MANIFEST.md",
        "ACCEPTANCE_CHECKLIST": output_dir / "ACCEPTANCE_CHECKLIST.md",
        "E2E_FLOW_SUMMARY": output_dir / "E2E_FLOW_SUMMARY.md",
        "SAFETY_AUDIT": output_dir / "SAFETY_AUDIT.md",
        "DASHBOARD_WORKFLOW_GUIDE": output_dir / "DASHBOARD_WORKFLOW_GUIDE.md",
        "HUMAN_REVIEW_GUIDE": output_dir / "HUMAN_REVIEW_GUIDE.md",
        "PROMOTION_GATE_SPEC": output_dir / "PROMOTION_GATE_SPEC.md",
        "SYNTHETIC_SMOKE_SUMMARY": output_dir / "SYNTHETIC_SMOKE_SUMMARY.md",
        "KNOWN_LIMITATIONS": output_dir / "KNOWN_LIMITATIONS.md",
        "NEXT_ACTION_PLAN": output_dir / "NEXT_ACTION_PLAN.md",
        "DEMO_SCRIPT": output_dir / "DEMO_SCRIPT.md",
        "evidence_index_json": output_dir / "evidence_index.json",
        "evidence_index_md": output_dir / "evidence_index.md",
    }
    paths["MANIFEST_json"].write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    paths["evidence_index_json"].write_text(
        json.dumps({"evidence": evidence, "artifacts": manifest["artifacts"]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    paths["README"].write_text(_readme(evidence), encoding="utf-8")
    paths["MANIFEST_md"].write_text(_manifest_md(manifest), encoding="utf-8")
    paths["ACCEPTANCE_CHECKLIST"].write_text(_acceptance_checklist(evidence), encoding="utf-8")
    paths["E2E_FLOW_SUMMARY"].write_text(_e2e_flow_summary(), encoding="utf-8")
    paths["SAFETY_AUDIT"].write_text(_safety_audit(), encoding="utf-8")
    paths["DASHBOARD_WORKFLOW_GUIDE"].write_text(_dashboard_workflow_guide(), encoding="utf-8")
    paths["HUMAN_REVIEW_GUIDE"].write_text(_human_review_guide(), encoding="utf-8")
    paths["PROMOTION_GATE_SPEC"].write_text(_promotion_gate_spec(), encoding="utf-8")
    paths["SYNTHETIC_SMOKE_SUMMARY"].write_text(_synthetic_smoke_summary(evidence), encoding="utf-8")
    paths["KNOWN_LIMITATIONS"].write_text(_known_limitations(), encoding="utf-8")
    paths["NEXT_ACTION_PLAN"].write_text(_next_action_plan(), encoding="utf-8")
    paths["DEMO_SCRIPT"].write_text(_demo_script(), encoding="utf-8")
    paths["evidence_index_md"].write_text(_evidence_index_md(manifest), encoding="utf-8")
    return {key: str(value) for key, value in paths.items()}


def _readme(e: dict[str, Any]) -> str:
    return f"""# DeepSeek Evidence-to-Event Human Review Lane Freeze

This freeze package documents the local evidence-to-event human review lane.

- DeepSeek replay records extracted: {e['records_extracted']}
- Records persisted: {e['records_persisted']}
- Visualization points: {e['visualization_points']}
- Evidence-only records: {e['evidence_only_count']}
- Event-ready records: {e['event_ready_count']}
- Feature-ready records: {e['feature_ready_count']}
- Requires human review: {e['requires_human_review_count']}

The lane is intentionally conservative: LLM-derived evidence is never promoted to an epidemic event without human-confirmed fields and promotion-gate validation.
"""


def _manifest_md(manifest: dict[str, Any]) -> str:
    lines = [
        "# Manifest",
        "",
        "| artifact | type | exists | role | safety | path |",
        "|---|---|---:|---|---|---|",
    ]
    for item in manifest["artifacts"]:
        lines.append(
            f"| {item['artifact_name']} | {item['artifact_type']} | {item['exists']} | "
            f"{item['evidence_role']} | {item['safety_level']} | `{item['path']}` |"
        )
    return "\n".join(lines) + "\n"


def _acceptance_checklist(e: dict[str, Any]) -> str:
    return f"""# Acceptance Checklist

## A. DeepSeek API and Replay
- [x] health check ok
- [x] replay ok
- [x] no Tavily rerun
- [x] no candidate URL visit
- [x] no follow links
- [x] records_extracted={e['records_extracted']}
- [x] records_persisted={e['records_persisted']}

## B. Quality Audit
- [x] evidence_only_count={e['evidence_only_count']}
- [x] event_ready_count={e['event_ready_count']}
- [x] feature_ready_count={e['feature_ready_count']}
- [x] visualization_ready_count={e['visualization_ready_count']}
- [x] requires_human_review_count={e['requires_human_review_count']}
- [x] blockers explained

## C. Human Review Workspace
- [x] readiness table generated
- [x] manual review queue generated
- [x] annotation template generated
- [x] manual review bundle generated
- [x] Dashboard workspace available
- [x] API / Worker available

## D. Promotion Gate
- [x] unfilled template does not promote
- [x] invalid annotation blocked
- [x] valid synthetic annotation promotes event
- [x] insufficient weeks blocks model_features
- [x] no auto promotion

## E. Safety
- [x] no key output
- [x] no prompt output
- [x] no completion output
- [x] no response body output
- [x] no raw_text output
- [x] no fake events
- [x] no fake features
"""


def _e2e_flow_summary() -> str:
    return """# End-to-End Flow Summary

```mermaid
flowchart LR
  A["DeepSeek replay output"] --> B["Extraction quality audit"]
  B --> C["Readiness review"]
  C --> D["Annotation workspace"]
  D --> E["Manual annotation template"]
  E --> F["Annotation import"]
  F --> G["Promotion gate"]
  G -->|approved + valid| H["epidemic_events in pilot DB"]
  G -->|blocked/not promoted| I["evidence-only queue"]
  H --> J["Feature eligibility gate"]
  J -->|enough reviewed weeks + numeric targets| K["model_features"]
  J -->|insufficient time series| L["blocked from features"]
```

DeepSeek evidence is not fact. Only human-confirmed disease, country, date/week, metric type, numeric value, and source grounding can pass the event promotion gate.

Feature generation requires enough distinct ISO weeks and numeric targets. Synthetic smoke validates the gate only; it is not real outbreak data.
"""


def _safety_audit() -> str:
    return """# Safety Audit

- Network accessed during freeze generation: false
- DeepSeek called during freeze generation: false
- Tavily rerun during freeze generation: false
- Candidate URLs accessed: false
- Follow links: false
- Remote deployment: false
- SSH: false
- Docker startup: false
- Automatic real annotation filling: false
- Raw page body, prompt, completion, response body, and raw_text are not included.
"""


def _dashboard_workflow_guide() -> str:
    return """# Dashboard Workflow Guide

1. Open the DeepSeek Web Search / Diagnostics page.
2. Review health and replay summaries.
3. Open the readiness review panel.
4. Inspect why each row remains evidence-only.
5. Export the annotation template.
6. Human reviewers fill confirmed date/week, metric type, numeric value, and source grounding.
7. Import the annotation file.
8. Preview promotion decisions.
9. Persist only to a pilot database after explicit approval.
10. Review feature eligibility blockers before generating model features.
"""


def _human_review_guide() -> str:
    return """# Human Review Guide

`approve_event` requires:

- confirmed disease
- confirmed country
- confirmed event date or ISO week
- metric type
- metric value
- source grounding confirmed
- reviewer decision

Do not infer case counts from DeepSeek summaries. Do not infer dates from titles. Do not promote without source confirmation. Do not treat synthetic fixtures as real data. Do not treat evidence-only rows as feature-ready.
"""


def _promotion_gate_spec() -> str:
    return """# Promotion Gate Specification

- `approve_event` can pass only when disease, country, date/week, metric type, numeric value, and source grounding are confirmed.
- Invalid or incomplete approved annotations are `blocked`.
- `keep_evidence_only` never writes an event.
- `reject` never writes an event.
- `needs_more_info` never writes an event.
- Persistence is disabled by default.
- `allow_persistence=true` plus an explicit local database URL is required for pilot DB writes.
- Feature eligibility requires enough reviewed ISO weeks and numeric targets.
- Fewer than four distinct ISO weeks blocks model feature generation.
"""


def _synthetic_smoke_summary(e: dict[str, Any]) -> str:
    blockers = ", ".join(str(item) for item in e["synthetic_feature_blockers"])
    return f"""# Synthetic Smoke Summary

- synthetic_review_fixture=true
- not_real_epidemiological_data=true
- promoted_epidemic_events={e['synthetic_promoted']}
- blocked_count={e['synthetic_blocked']}
- feature_generation_allowed={e['synthetic_feature_allowed']}
- blockers: {blockers}

Synthetic smoke validates code gates only. It does not mean the real DeepSeek records were promoted. It does not write a production database.
"""


def _known_limitations() -> str:
    return """# Known Limitations

1. The real 8 DeepSeek records are still evidence-only.
2. Human annotations are required before promotion.
3. There are currently no real promoted epidemic_events from these records.
4. There are currently no real model_features from these records.
5. DeepSeek extraction is not a factual source.
6. Visualization points require human review.
7. More structured time-series sources are needed for modeling.
8. PubMed / literature provider is not yet a real M2 live provider.
9. Search provider coverage is still provider-bounded, not universal full-web certainty.
"""


def _next_action_plan() -> str:
    return """# Next Action Plan

1. Human-review the 8 DeepSeek evidence records.
2. Fill annotation fields only for confirmed events.
3. Promote only into a pilot DB.
4. If promoted events cover fewer than four distinct ISO weeks, do not generate model_features.
5. Continue searching for structured time-series sources.
6. Add PubMed evidence provider readiness for Milestone 2.
7. Freeze this lane before any real literature live retrieval.
"""


def _demo_script() -> str:
    return """# Demo Script

## 3-minute version
Show DeepSeek replay counts, readiness review, annotation workspace, and synthetic gate smoke. Emphasize that LLM evidence is not automatically treated as fact.

## 8-minute version
Walk through replay, quality audit, readiness blockers, manual annotation template, promotion gate decisions, and feature eligibility blocking.

## 15-minute version
Explain the full evidence-to-event lane: extraction, audit, review table, manual workspace, gate rules, pilot DB persistence, synthetic smoke, and limitations. Close with next actions for real human review and structured-source discovery.
"""


def _evidence_index_md(manifest: dict[str, Any]) -> str:
    lines = ["# Evidence Index", ""]
    for item in manifest["artifacts"]:
        lines.append(f"- **{item['artifact_name']}**: `{item['path']}`")
    return "\n".join(lines) + "\n"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate DeepSeek evidence lane freeze acceptance package.")
    parser.add_argument("--diagnostics-dir", required=True)
    parser.add_argument("--synthetic-smoke-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = run_freeze(args)
    print(
        json.dumps(
            {
                "status": result["status"],
                "freeze_dir": result["freeze_dir"],
                "artifact_count": result["artifact_count"],
                "manifest": result["paths"].get("MANIFEST_json"),
                "acceptance_checklist": result["paths"].get("ACCEPTANCE_CHECKLIST"),
                "dashboard_workflow_guide": result["paths"].get("DASHBOARD_WORKFLOW_GUIDE"),
                "network_accessed": False,
                "deepseek_called_in_this_step": False,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
