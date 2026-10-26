from __future__ import annotations

import argparse
import json
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from cleaner_agent.agent import CleanerAgent
from cleaner_agent.features import FeatureBuilder
from common.db import get_session, init_db
from common.models import ModelFeature, RawDocument
from crawler_agent.connector_ingest import ingest_connector_payload, store_connector_documents, upsert_connector_source
from crawler_agent.generic_parsers import parse_by_strategy, parser_result_to_connector_documents
from crawler_agent.raw_document_store import RawDocumentStore

COUNTRY = "United States"
DISEASE = "influenza"
FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"


def run_influenza_extension_smoke(
    *,
    database_url: str | None = None,
    temp_db: bool = False,
) -> dict[str, Any]:
    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    if temp_db or not database_url:
        temp_dir = tempfile.TemporaryDirectory(prefix="influenza-extension-smoke-")
        database_url = f"sqlite:///{(Path(temp_dir.name) / 'influenza-extension-smoke.db').as_posix()}"

    warnings: list[str] = []
    init_db(database_url).dispose()

    with get_session(database_url) as session:
        stored_results = []
        for parser_strategy, fixture_name, base_url in (
            ("csv_dataset", "cdc_fluview_ili.csv", "https://local.invalid/fluview/csv-fixture"),
            ("html_table", "cdc_fluview_table.html", "https://local.invalid/fluview/table-fixture"),
            ("official_report_page", "cdc_fluview_report.html", "https://local.invalid/fluview/report-fixture"),
        ):
            payload = (FIXTURES / fixture_name).read_text(encoding="utf-8")
            results = ingest_connector_payload(
                session,
                "cdc_fluview",
                payload,
                base_url=base_url,
                source_status="pending_review",
            )
            stored_results.extend(results)
            if not results:
                warnings.append(f"{parser_strategy} produced no raw documents.")

        json_payload = (FIXTURES / "influenza_json_api_sample.json").read_text(encoding="utf-8")
        json_result = parse_by_strategy(
            "json_api",
            json_payload,
            source_name="CDC FluView JSON API Fixture",
            url="https://local.invalid/fluview/json-api-fixture",
            country=COUNTRY,
            disease=DISEASE,
        )
        warnings.extend(json_result.warnings)
        json_documents = parser_result_to_connector_documents(json_result)
        source = upsert_connector_source(
            session,
            "cdc_fluview",
            base_url="https://local.invalid/fluview/json-api-fixture",
            status="pending_review",
        )
        stored_results.extend(
            store_connector_documents(
                source=source,
                documents=json_documents,
                store=RawDocumentStore(session),
            )
        )

        clean_summary = CleanerAgent(session).process_pending(limit=100, country=COUNTRY, disease=DISEASE)
        feature_summary = FeatureBuilder(session).build(
            country=COUNTRY,
            disease=DISEASE,
            horizons_days=(7,),
            outbreak_threshold=1,
        )

        saved_by_strategy = Counter()
        for result in stored_results:
            if not result.created:
                continue
            strategy = (
                result.document.metadata_json.get("parser_strategy")
                or result.document.metadata_json.get("parse_mode")
                or "unknown"
            )
            saved_by_strategy[str(strategy)] += 1

        summary = {
            "status": "ok",
            "network_accessed": False,
            "parser_strategies_used": sorted(saved_by_strategy),
            "raw_documents_saved_by_strategy": dict(sorted(saved_by_strategy.items())),
            "raw_documents_total": session.query(RawDocument).count(),
            "epidemic_events_created": int(clean_summary.get("events_created", 0)),
            "model_features_created": session.query(ModelFeature).filter(ModelFeature.disease == DISEASE).count(),
            "feature_rows": int(feature_summary.get("feature_rows", 0)),
            "warnings": warnings[:20],
        }

    if temp_dir is not None:
        temp_dir.cleanup()
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run influenza multi-format extension smoke without network access.")
    parser.add_argument("--database-url")
    parser.add_argument("--temp-db", action="store_true")
    args = parser.parse_args(argv)
    summary = run_influenza_extension_smoke(database_url=args.database_url, temp_db=args.temp_db)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if summary.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
