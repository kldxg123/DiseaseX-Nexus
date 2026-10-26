from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path

from common.db import get_session, init_db
from common.models import EpidemicEvent, ModelFeature, RawDocument, SourceRegistry
from scripts.reprocess_live_pilot import reprocess_live_pilot


def _seed_synthetic_pilot(tmp_path: Path, *, weeks: int = 3) -> tuple[Path, Path]:
    pilot_dir = tmp_path / "source-pilot"
    pilot_dir.mkdir()
    source_db = pilot_dir / "cdc_norostat_live_pilot.sqlite"
    database_url = f"sqlite:///{source_db.as_posix()}"
    init_db(database_url).dispose()
    with get_session(database_url) as session:
        source = SourceRegistry(
            name="CDC NoroSTAT",
            base_url="https://fixture.invalid/cdc-norostat",
            country_scope=["United States"],
            disease_scope=["norovirus"],
            source_type="official_dataset",
            parser_type="cdc_norostat",
            status="pending_review",
            reliability_score=0.9,
            metadata_json={"connector": "cdc_norostat"},
        )
        session.add(source)
        session.flush()
        for index in range(1, weeks + 1):
            week = f"2025-W0{index}"
            body = f"Local fixture row for illness onset week {week}."
            document = RawDocument(
                source_registry_id=source.id,
                original_url=f"https://fixture.invalid/cdc-norostat?row={index}",
                normalized_url=f"https://fixture.invalid/cdc-norostat?row={index}",
                title=f"CDC NoroSTAT local row {index}",
                body_text=body,
                content_hash=hashlib.sha256(f"{index}:{body}".encode("utf-8")).hexdigest(),
                status_code=200,
                content_type="text/html",
                fetched_at=datetime(2026, 7, 2, tzinfo=UTC),
                extraction_status="cleaned",
                metadata_json={
                    "source_key": "cdc_norostat",
                    "row_number": index,
                    "row_fields": {
                        "state": "National",
                        "illness onset week": week,
                        "total outbreaks": str(index + 1),
                    },
                },
            )
            session.add(document)
            session.flush()
            session.add(
                EpidemicEvent(
                    raw_document_id=document.id,
                    country="United States",
                    admin1="National",
                    disease="norovirus",
                    event_date=date(2026, 7, 2),
                    iso_week="2026-W27",
                    extraction_method="old_rule_based",
                    confidence_score=0.5,
                    provenance_json={},
                )
            )
    (pilot_dir / "live_fetch_summary.json").write_text(
        json.dumps(
            {
                "status": "ok",
                "raw_documents_saved": weeks,
                "cleaner": {"events_created": weeks},
                "features": {"feature_rows": 0},
            }
        ),
        encoding="utf-8",
    )
    (pilot_dir / "data_quality_report.json").write_text(json.dumps({"warnings": []}), encoding="utf-8")
    (pilot_dir / "crawler_optimizer_report.json").write_text(
        json.dumps({"failure_case_count": weeks * 2, "top_failure_types": [{"failure_type": "low_relevance", "count": weeks * 2}]}),
        encoding="utf-8",
    )
    return pilot_dir, source_db


def _counts(database_path: Path) -> dict[str, int]:
    with get_session(f"sqlite:///{database_path.as_posix()}") as session:
        return {
            "events": session.query(EpidemicEvent).count(),
            "features": session.query(ModelFeature).count(),
            "raw_documents": session.query(RawDocument).count(),
        }


def test_reprocess_live_pilot_copies_db_and_rebuilds_local_derivatives(tmp_path) -> None:
    pilot_dir, source_db = _seed_synthetic_pilot(tmp_path, weeks=3)
    before_source_counts = _counts(source_db)

    result = reprocess_live_pilot(
        pilot_dir=pilot_dir,
        output_dir=tmp_path / "outputs",
        run_timestamp="20260702T120000Z",
    )

    after_source_counts = _counts(source_db)
    reprocess_db = Path(result["reprocess_db"])
    reprocess_counts = _counts(reprocess_db)
    comparison = json.loads((Path(result["reprocess_dir"]) / "comparison_with_original.json").read_text(encoding="utf-8"))

    assert before_source_counts == after_source_counts
    assert reprocess_db.exists()
    assert reprocess_counts["raw_documents"] == 3
    assert reprocess_counts["events"] == 3
    assert result["feature_rows_h7"] == 2
    assert result["feature_rows_h28"] == 0
    assert "horizon=28" in " ".join(result["warnings"])
    assert comparison["improvements"]["features_generated"] is True
    assert comparison["improvements"]["iso_weeks_recovered"] is True
    assert (Path(result["reprocess_dir"]) / "rollback_commands.md").exists()


def test_reprocess_live_pilot_supports_source_db_argument(tmp_path) -> None:
    _pilot_dir, source_db = _seed_synthetic_pilot(tmp_path, weeks=4)

    result = reprocess_live_pilot(
        source_db=source_db,
        output_dir=tmp_path / "outputs",
        run_timestamp="20260702T120100Z",
    )

    assert Path(result["reprocess_db"]).exists()
    assert result["source_db_unchanged"] is True
    assert result["feature_rows_h7"] == 3
    assert Path(result["output_files"]["reprocess_summary_json"]).exists()
