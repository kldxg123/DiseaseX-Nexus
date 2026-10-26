from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import select

from cleaner_agent.features import FeatureBuilder
from common.db import get_session
from common.models import EpidemicEvent, ModelFeature
from live_source_search_orchestrator.event_quality_diagnostics import (
    assess_feature_generation_eligibility,
    diagnose_feature_generation_blockers,
    write_event_quality_diagnostic_report,
)


def try_generate_features_from_acquisition(
    database_url: str,
    disease: str,
    country: str,
    output_dir: str | Path,
    *,
    horizons_days: tuple[int, ...] = (7, 14, 28),
) -> dict[str, Any]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    with get_session(database_url) as session:
        events = list(
            session.scalars(
                select(EpidemicEvent)
                .where(EpidemicEvent.disease == disease)
                .where(EpidemicEvent.country == country)
                .order_by(EpidemicEvent.iso_week, EpidemicEvent.id)
            ).all()
        )
        eligibility = assess_feature_generation_eligibility(events)
        if not eligibility.eligible:
            blockers = diagnose_feature_generation_blockers(events)
            payload = {
                "status": "skipped",
                "feature_generation_attempted": False,
                "reason": "feature_eligibility_gate_blocked",
                "events_seen": len(events),
                "model_features_count": session.query(ModelFeature).count(),
                "blocker_types": [item.blocker_type for item in blockers],
                "feature_eligibility": eligibility.to_dict(),
                "limitations": "No model_features were fabricated. FeatureBuilder runs only after the eligibility gate passes.",
            }
            _write_json(root / "feature_generation_bridge_summary.json", payload)
            write_event_quality_diagnostic_report(root, database_url=database_url)
            return payload
        summary = FeatureBuilder(session).build(
            country=country,
            disease=disease,
            horizons_days=horizons_days,
            replace_existing=True,
        )
        payload = {
            "status": "ok",
            "feature_generation_attempted": True,
            "events_seen": int(summary.get("events_seen") or 0),
            "feature_rows": int(summary.get("feature_rows") or 0),
            "saved": int(summary.get("saved") or 0),
            "updated": int(summary.get("updated") or 0),
            "skipped": int(summary.get("skipped") or 0),
            "limitations": "Generated features still require analyst review before prediction.",
        }
        _write_json(root / "feature_generation_bridge_summary.json", payload)
        write_event_quality_diagnostic_report(root, database_url=database_url)
        return payload


def _write_json(path: str | Path, payload: dict[str, Any]) -> None:
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


__all__ = ["try_generate_features_from_acquisition"]
